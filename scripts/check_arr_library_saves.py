#!/usr/bin/env python3
"""Report whether *ARR imports are coming from library paths."""
from pathlib import Path
import json
import re
import urllib.request
from xml.etree import ElementTree as ET

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}


def key(cfg):
    return ET.parse(cfg).findtext("ApiKey")


def get(port, apikey, path, ver="v3"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/{ver}/{path}",
        headers={"X-Api-Key": apikey},
    )
    return json.load(urllib.request.urlopen(req, timeout=60))


def history_from_library(port, apikey, ver, library_prefix):
    h = get(port, apikey, "history?page=1&pageSize=50&sortKey=date&sortDirection=descending", ver)
    recs = h.get("records") if isinstance(h, dict) else []
    bad = []
    for rec in recs:
        if rec.get("eventType") != "downloadFolderImported":
            continue
        dropped = (rec.get("data") or {}).get("droppedPath") or ""
        dest = (rec.get("data") or {}).get("importedPath") or (rec.get("data") or {}).get(
            "destinationPath"
        )
        if dropped.startswith(library_prefix):
            bad.append((dropped, dest, rec.get("sourceTitle")))
    return len(recs), bad


def scan_scene_inside(host_root: Path):
    hits = []
    if not host_root.exists():
        return hits
    for d in sorted(host_root.iterdir()):
        if not d.is_dir():
            continue
        vids = [
            p
            for p in d.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO
        ]
        scene = []
        for v in vids:
            n = v.name
            if re.search(r" \((19|20)\d{2}\) ", n) or re.search(
                r" \((19|20)\d{2}\)\.", n
            ):
                continue
            if re.search(r"\.(19|20)\d{2}\.", n) or "1080p" in n or "2160p" in n:
                scene.append(n)
        if scene:
            hits.append((d.name, len(vids), scene[:5]))
    return hits


def main():
    print("== history: imports whose source is already under the library ==")
    for name, port, cfg, ver, lib in [
        ("sonarr", 8989, "/arrstack/config/sonarr/config.xml", "v3", "/data/media/tv"),
        ("radarr", 7878, "/arrstack/config/radarr/config.xml", "v3", "/data/media/movies"),
        ("lidarr", 8686, "/arrstack/config/lidarr/config.xml", "v1", "/data/media/music"),
        ("readarr", 8787, "/arrstack/config/readarr/config.xml", "v1", "/data/media/books"),
        ("whisparr", 6969, "/arrstack/config/whisparr/config.xml", "v3", "/data/media"),
    ]:
        try:
            k = key(cfg)
            n, bad = history_from_library(port, k, ver, lib)
            print(f"{name}: scanned_imported_in_page={n} library_sourced={len(bad)}")
            for dropped, dest, title in bad[:8]:
                print(f"  FROM_LIB {title[:50]}")
                print(f"    {dropped[:120]}")
                print(f"    -> {str(dest)[:120]}")
        except Exception as e:
            print(f"{name}: FAIL {e}")

    print("== movies: scene-named extras inside mapped folders ==")
    hits = scan_scene_inside(Path("/mnt/media/media/movies"))
    print("folders_with_sceneish", len(hits))
    for name, nvids, scene in hits[:15]:
        print(f"  {name[:70]} videos={nvids}")
        for s in scene:
            print("   ", s[:100])

    print("== music top-level non-artist leftovers ==")
    music = Path("/mnt/media/media/music")
    if music.exists():
        for p in sorted(music.iterdir()):
            print(" ", p.name[:80], "dir" if p.is_dir() else "file")


if __name__ == "__main__":
    main()
