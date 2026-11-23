#!/usr/bin/env python3
"""Remove SSD torrent data after share limits when Sonarr/Radarr already imported to NAS."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SONARR_CONFIG = Path(os.environ.get("SONARR_CONFIG", "/arrstack/config/sonarr/config.xml"))
RADARR_CONFIG = Path(os.environ.get("RADARR_CONFIG", "/arrstack/config/radarr/config.xml"))
AUTO_ENV = Path(os.environ.get("ARRSTACK_AUTO_ENV", "/arrstack/config/arrstack.auto.env"))
QBIT_URL = os.environ.get("QBIT_URL", "http://127.0.0.1:8200").rstrip("/")
SAB_URL = os.environ.get("SAB_URL", "http://127.0.0.1:8100").rstrip("/")
DOWNLOADS_ROOT = Path(os.environ.get("DOWNLOADS_ROOT", "/arrstack/downloads"))
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/mnt/media"))
LOG_PATH = Path(os.environ.get("LOG_PATH", "/var/log/qbit-ssd-cleanup.log"))

MAX_RATIO = float(os.environ.get("MAX_RATIO", "1.0"))
MAX_SEEDING_SECS = int(os.environ.get("MAX_SEEDING_SECS", str(4320 * 60)))
# qBittorrent defaults; 0 disables resume/load (BT_backup fails on restart).
TORRENT_FILE_SIZE_LIMIT = int(os.environ.get("TORRENT_FILE_SIZE_LIMIT", "104857600"))
BDECODE_DEPTH_LIMIT = int(os.environ.get("BDECODE_DEPTH_LIMIT", "100"))
BDECODE_TOKEN_LIMIT = int(os.environ.get("BDECODE_TOKEN_LIMIT", "10000000"))
# Extensions Sonarr/Radarr fail themselves (Executables: exe/bat/cmd;
# Potentially Dangerous: scr/vbs/lnk, plus arj/lzh/ps1/zipx) stay out of this
# list so the download finishes and the *ARR app can blocklist and search again.
# Client-side skips leave those grabs sitting in the queue.
EXCLUDED_FILE_NAMES = "\n".join(
    [
        "*.com",
        "*.pif",
        "*.msi",
        "*.js",
        "*.wsf",
    ]
)
FAIL_DOWNLOADS = [0, 1]
VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".wmv", ".flac", ".mp3", ".m4a", ".m4b"}


def log(msg: str) -> None:
    line = f"{msg}\n"
    sys.stdout.write(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def api_key_from_config(path: Path) -> str | None:
    if not path.is_file():
        return None
    match = re.search(r"<ApiKey>([^<]+)</ApiKey>", path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def key_from_env_file(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    prefix = f"{name}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def parse_arr_keys() -> tuple[str, str]:
    """Live config.xml keys, then arrstack.auto.env. buildarr.yml is gone."""
    sonarr = api_key_from_config(SONARR_CONFIG) or key_from_env_file(AUTO_ENV, "SONARR_API_KEY")
    radarr = api_key_from_config(RADARR_CONFIG) or key_from_env_file(AUTO_ENV, "RADARR_API_KEY")
    if not sonarr or not radarr:
        raise RuntimeError(
            "Sonarr/Radarr API keys not found in config.xml or arrstack.auto.env"
        )
    return sonarr, radarr


def http_json(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
) -> object:
    if method is None:
        method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def qbit_request(path: str, form: dict[str, str] | None = None) -> bytes:
    cmd = ["docker", "exec", "qbittorrent", "wget", "-qO-", f"http://127.0.0.1:8200{path}"]
    if form is not None:
        cmd = [
            "docker",
            "exec",
            "qbittorrent",
            "wget",
            "-qO-",
            "--post-data",
            urllib.parse.urlencode(form),
            f"http://127.0.0.1:8200{path}",
        ]
    try:
        return subprocess.check_output(cmd, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"qBittorrent API failed for {path}") from exc


def qbit_get(path: str) -> object:
    raw = qbit_request(path)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def qbit_post(path: str, form: dict[str, str]) -> None:
    qbit_request(path, form)


def arr_history_has_import(base: str, api_key: str, info_hash: str) -> bool:
    q = urllib.parse.urlencode({"downloadId": info_hash, "pageSize": 50})
    url = f"{base}/api/v3/history?{q}"
    try:
        rows = http_json(url, headers={"X-Api-Key": api_key})
    except urllib.error.HTTPError:
        return False
    if not isinstance(rows, list):
        return False
    for row in rows:
        if row.get("eventType") in {
            "downloadFolderImported",
            "downloadImported",
            "episodeFileImported",
            "movieFileImported",
        }:
            data = row.get("data") or {}
            if (data.get("downloadId") or "").lower() == info_hash.lower():
                return True
    return False


def category_arr(category: str) -> tuple[str, str] | None:
    cat = (category or "").lower()
    if cat in {"tv-sonarr", "sonarr", "tv"}:
        return ("http://127.0.0.1:8989", "sonarr")
    if cat in {"radarr", "movies"}:
        return ("http://127.0.0.1:7878", "radarr")
    return None


def share_limit_reached(torrent: dict) -> bool:
    ratio = float(torrent.get("ratio") or 0)
    seed_secs = int(torrent.get("seeding_time") or 0)
    return ratio >= MAX_RATIO or seed_secs >= MAX_SEEDING_SECS


def on_ssd_path(path: str) -> bool:
    p = path or ""
    return p.startswith("/downloads/") or p.startswith("/arrstack/downloads/")


def host_path(container_path: str) -> Path:
    if container_path.startswith("/downloads/"):
        return DOWNLOADS_ROOT / container_path.removeprefix("/downloads/").lstrip("/")
    if container_path.startswith("/arrstack/downloads/"):
        return Path(container_path)
    return Path(container_path)


def tracked_download_dirs(torrents: list[dict]) -> set[Path]:
    """Directory roots still owned by qBittorrent (for orphan detection)."""
    dirs: set[Path] = set()
    for torrent in torrents:
        save_path = torrent.get("save_path") or ""
        content_path = torrent.get("content_path") or save_path
        if not on_ssd_path(save_path) and not on_ssd_path(content_path):
            continue
        for raw in {save_path, content_path}:
            if not raw or not on_ssd_path(raw):
                continue
            hp = host_path(raw)
            dirs.add(hp if hp.is_dir() else hp.parent)
    return {d.resolve() for d in dirs if d != Path(".")}


def dir_is_tracked(entry: Path, tracked_dirs: set[Path]) -> bool:
    resolved = entry.resolve()
    for tracked in tracked_dirs:
        if resolved == tracked:
            return True
        try:
            if resolved.is_relative_to(tracked) or tracked.is_relative_to(resolved):
                return True
        except ValueError:
            continue
    return False


def largest_video(path: Path) -> Path | None:
    best: Path | None = None
    best_size = -1
    if not path.exists():
        return None
    if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
        return path
    for root, _, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            if fp.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if size > best_size:
                best_size = size
                best = fp
    return best


def nas_has_same_video(video: Path) -> bool:
    """True when the NAS has a file with the same name and byte size.

    Size alone is not enough: an unrelated file of the same length would
    make the cleanup delete the whole orphan directory.
    """
    try:
        size = video.stat().st_size
    except OSError:
        return False
    if size <= 0 or not video.name:
        return False
    try:
        out = subprocess.check_output(
            ["find", str(MEDIA_ROOT), "-type", "f", "-name", video.name, "-size", f"{size}c"],
            stderr=subprocess.DEVNULL,
            timeout=120,
            text=True,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return bool(out.strip())


def delete_torrent(info_hash: str, content_path: str) -> None:
    qbit_post(
        "/api/v2/torrents/delete",
        {"hashes": info_hash, "deleteFiles": "true"},
    )
    log(f"deleted torrent {info_hash} with files at {content_path}")


def delete_orphan(path: Path) -> None:
    shutil.rmtree(path)
    log(f"deleted orphan dir {path}")


def complete_scan_bases() -> list[Path]:
    """Completed download folders only. incomplete/ stays untouched."""
    return [
        DOWNLOADS_ROOT / "torrents/complete/tv-sonarr",
        DOWNLOADS_ROOT / "torrents/complete/radarr",
        DOWNLOADS_ROOT / "torrents/complete/lidarr",
        DOWNLOADS_ROOT / "usenet/complete/tv",
        DOWNLOADS_ROOT / "usenet/complete/movies",
        DOWNLOADS_ROOT / "usenet/complete/music",
        DOWNLOADS_ROOT / "usenet/complete/audio",
    ]


def sab_queue_dirs(payload: object) -> set[str]:
    """Container paths still owned by an active SABnzbd job."""
    slots: list = []
    if isinstance(payload, dict):
        queue = payload.get("queue") or {}
        if isinstance(queue, dict):
            slots = queue.get("slots") or []
    paths: set[str] = set()
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        for key in ("storage", "path", "completedir", "downloaddir"):
            raw = slot.get(key) or ""
            if isinstance(raw, str) and on_ssd_path(raw):
                paths.add(raw)
    return paths


def sab_active_dirs() -> set[Path] | None:
    """None when SABnzbd cannot be queried, so usenet orphans are left alone."""
    api_key = key_from_env_file(AUTO_ENV, "SABNZBD_API_KEY")
    if not api_key:
        log("SABnzbd API key missing; skipping usenet orphan cleanup")
        return None
    query = urllib.parse.urlencode({"mode": "queue", "output": "json", "apikey": api_key})
    try:
        payload = http_json(f"{SAB_URL}/api?{query}")
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        log(f"SABnzbd queue unavailable; skipping usenet orphan cleanup: {exc}")
        return None
    return {host_path(path) for path in sab_queue_dirs(payload)}


def scan_orphans(tracked_dirs: set[Path], *, include_usenet: bool) -> list[Path]:
    """Only scan completed download dirs — never touch incomplete/ (active downloads)."""
    orphans: list[Path] = []
    scan_bases = complete_scan_bases()
    if not include_usenet:
        scan_bases = [path for path in scan_bases if "usenet" not in path.parts]
    for base in scan_bases:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            if dir_is_tracked(entry, tracked_dirs):
                continue
            orphans.append(entry)
    return orphans


def ensure_fail_downloads(base: str, api_key: str) -> None:
    """Turn on Executables and Potentially Dangerous, and reject blocklisted hashes.

    Prowlarr full-sync can clear these. Without the hash check, a failed .exe is
    grabbed again from the next indexer under the same infohash.
    """
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    try:
        indexers = http_json(f"{base}/api/v3/indexer", headers=headers)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        log(f"indexer list unavailable for {base}: {exc}")
        return
    if not isinstance(indexers, list):
        log(f"indexer list unavailable for {base}")
        return
    for indexer in indexers:
        fields = indexer.get("fields") or []
        field = next((item for item in fields if item.get("name") == "failDownloads"), None)
        if field is None:
            continue
        current = field.get("value") or []
        hash_field = next(
            (item for item in fields if item.get("name") == "rejectBlocklistedTorrentHashesWhileGrabbing"),
            None,
        )
        fail_ok = isinstance(current, list) and set(current) == set(FAIL_DOWNLOADS)
        hash_ok = hash_field is None or hash_field.get("value") is True
        if fail_ok and hash_ok:
            continue
        field["value"] = list(FAIL_DOWNLOADS)
        if hash_field is not None:
            hash_field["value"] = True
        indexer_id = indexer.get("id")
        name = indexer.get("name") or indexer_id
        body = json.dumps(indexer).encode("utf-8")
        updated = False
        for attempt in range(4):
            try:
                http_json(
                    f"{base}/api/v3/indexer/{indexer_id}?forceSave=true",
                    headers=headers,
                    data=body,
                    method="PUT",
                )
                updated = True
                break
            except urllib.error.HTTPError as exc:
                # Sonarr tests the indexer on save. Prowlarr answers 429 under a burst,
                # which Sonarr surfaces as 400. Do not log the body; it echoes the URL.
                if exc.code not in {400, 429} or attempt == 3:
                    log(f"failed to set failDownloads on {name}: HTTP {exc.code}")
                    break
                time.sleep(5 * (attempt + 1))
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                log(f"failed to set failDownloads on {name}: {exc}")
                break
        if updated:
            log(f"set failDownloads on {name}")
            time.sleep(2)


def ensure_qbit_prefs() -> None:
    prefs = qbit_get("/api/v2/app/preferences")
    if not isinstance(prefs, dict):
        return
    desired = {
        "max_ratio_act": 0,
        "max_active_downloads": -1,
        "max_ratio_enabled": True,
        "max_ratio": MAX_RATIO,
        "max_seeding_time_enabled": True,
        "max_seeding_time": MAX_SEEDING_SECS // 60,
        "torrent_file_size_limit": TORRENT_FILE_SIZE_LIMIT,
        "bdecode_depth_limit": BDECODE_DEPTH_LIMIT,
        "bdecode_token_limit": BDECODE_TOKEN_LIMIT,
        "excluded_file_names_enabled": True,
        "excluded_file_names": EXCLUDED_FILE_NAMES,
    }
    if all(prefs.get(key) == value for key, value in desired.items()):
        pass
    else:
        qbit_post("/api/v2/app/setPreferences", {"json": json.dumps(desired)})
        log(
            "updated qBittorrent preferences: share limits, excluded file names, "
            f"torrent_file_size_limit={TORRENT_FILE_SIZE_LIMIT}"
        )


# SABnzbd uses this when a job is aborted locally (restart, paused unpack),
# not when the NZB is missing articles. Sonarr/Radarr blocklist that release
# and the re-search then finds nothing else.
LOCAL_UNPACK_ABORT = "sabnzbd.org/not-complete"


def is_local_unpack_abort(message: str) -> bool:
    return LOCAL_UNPACK_ABORT in (message or "")


def blocklist_ids_to_release(records: object, failed_counts: dict[str, int]) -> list[int]:
    """Clear a local-abort blocklist entry once.

    A second failure of the same release stays blocklisted so a genuinely
    incomplete NZB is not grabbed forever.
    """
    if not isinstance(records, list):
        return []
    ids: list[int] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if not is_local_unpack_abort(str(rec.get("message") or "")):
            continue
        title = str(rec.get("sourceTitle") or "")
        if failed_counts.get(title, 0) >= 2:
            continue
        rec_id = rec.get("id")
        if isinstance(rec_id, int):
            ids.append(rec_id)
    return ids


def search_ids_for_blocklist(records: object, release_ids: set[int]) -> tuple[list[int], list[int]]:
    """Episode ids (Sonarr) and movie ids (Radarr) to search after a release."""
    episodes: list[int] = []
    movies: list[int] = []
    if not isinstance(records, list):
        return episodes, movies
    for rec in records:
        if not isinstance(rec, dict) or rec.get("id") not in release_ids:
            continue
        for episode_id in rec.get("episodeIds") or []:
            if isinstance(episode_id, int):
                episodes.append(episode_id)
        movie_id = rec.get("movieId")
        if isinstance(movie_id, int):
            movies.append(movie_id)
    return episodes, movies


def _failed_abort_counts(history: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    records = history.get("records") if isinstance(history, dict) else None
    if not isinstance(records, list):
        return counts
    for rec in records:
        if not isinstance(rec, dict) or rec.get("eventType") != "downloadFailed":
            continue
        data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        if not is_local_unpack_abort(str(data.get("message") or "")):
            continue
        title = str(rec.get("sourceTitle") or "")
        counts[title] = counts.get(title, 0) + 1
    return counts


def release_aborted_imports(base: str, api_key: str) -> None:
    """Drop one-time local SAB aborts from the blocklist and search again."""
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    try:
        block = http_json(
            f"{base}/api/v3/blocklist?pageSize=100&sortKey=date&sortDirection=descending",
            headers=headers,
        )
        history = http_json(
            f"{base}/api/v3/history?pageSize=200&sortKey=date&sortDirection=descending",
            headers=headers,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        log(f"blocklist unavailable for {base}: {exc}")
        return
    records = block.get("records") if isinstance(block, dict) else []
    ids = blocklist_ids_to_release(records, _failed_abort_counts(history))
    if not ids:
        return
    for rec_id in ids:
        try:
            http_json(f"{base}/api/v3/blocklist/{rec_id}", headers=headers, method="DELETE")
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
            log(f"failed to clear blocklist {rec_id} on {base}: {exc}")
            return
    episodes, movies = search_ids_for_blocklist(records, set(ids))
    try:
        if episodes:
            http_json(
                f"{base}/api/v3/command",
                headers=headers,
                data=json.dumps({"name": "EpisodeSearch", "episodeIds": episodes}).encode(),
            )
        if movies:
            http_json(
                f"{base}/api/v3/command",
                headers=headers,
                data=json.dumps({"name": "MoviesSearch", "movieIds": movies}).encode(),
            )
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        log(f"search after blocklist clear failed for {base}: {exc}")
        return
    log(f"cleared {len(ids)} local unpack abort(s) on {base} and searched again")


def main() -> int:
    sonarr_key, radarr_key = parse_arr_keys()
    keys = {"sonarr": sonarr_key, "radarr": radarr_key}

    ensure_fail_downloads("http://127.0.0.1:8989", sonarr_key)
    ensure_fail_downloads("http://127.0.0.1:7878", radarr_key)
    release_aborted_imports("http://127.0.0.1:8989", sonarr_key)
    release_aborted_imports("http://127.0.0.1:7878", radarr_key)
    ensure_qbit_prefs()

    torrents = qbit_get("/api/v2/torrents/info")
    if not isinstance(torrents, list):
        log("qBittorrent torrent list unavailable")
        return 1

    tracked_dirs = tracked_download_dirs(torrents)
    sab_dirs = sab_active_dirs()
    if sab_dirs is not None:
        tracked_dirs |= sab_dirs
    deleted = 0

    for torrent in torrents:
        save_path = torrent.get("save_path") or ""
        content_path = torrent.get("content_path") or save_path
        if not on_ssd_path(save_path) and not on_ssd_path(content_path):
            continue

        if not share_limit_reached(torrent):
            continue

        arr = category_arr(torrent.get("category") or "")
        if not arr:
            continue
        base, name = arr
        info_hash = torrent.get("hash") or ""
        if not info_hash:
            continue
        if not arr_history_has_import(base, keys[name], info_hash):
            log(f"skip {info_hash}: share limit reached but no {name} import for {content_path}")
            continue
        delete_torrent(info_hash, content_path)
        deleted += 1

    orphan_candidates = scan_orphans(tracked_dirs, include_usenet=sab_dirs is not None)

    for orphan in orphan_candidates:
        if dir_is_tracked(orphan, tracked_dirs):
            continue
        video = largest_video(orphan)
        if video and nas_has_same_video(video):
            delete_orphan(orphan)
            deleted += 1
        else:
            log(f"skip orphan {orphan}: no matching NAS file size")

    log(f"cleanup complete, removed={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
