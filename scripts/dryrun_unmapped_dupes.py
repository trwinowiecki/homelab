#!/usr/bin/env python3
"""Dry-run (or delete) torrent-named leftovers under Sonarr's TV root."""
import argparse
import json
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}


def load_key():
    for p in ("/mnt/config/buildarr.yml", "/mnt/config/buildarr/buildarr.yml"):
        if os.path.exists(p):
            for line in open(p):
                if line.strip().startswith("api_key:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
    raise SystemExit("no api key")


def api(key, path):
    req = urllib.request.Request(
        "http://127.0.0.1:8989/api/v3/" + path, headers={"X-Api-Key": key}
    )
    return json.load(urllib.request.urlopen(req, timeout=120))


def norm(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def videos(path):
    p = Path(path)
    out = []
    if not p.exists():
        return out
    if p.is_file():
        if p.suffix.lower() in VIDEO:
            out.append(p)
        return out
    for f in p.rglob("*"):
        if f.is_file() and f.suffix.lower() in VIDEO:
            out.append(f)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    key = load_key()
    for _ in range(20):
        try:
            api(key, "system/status")
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("sonarr not up")

    root = api(key, "rootfolder")[0]
    unmapped = root.get("unmappedFolders") or []
    series = api(key, "series")
    series_titles = [(norm(s["title"]), s) for s in series]
    # Sonarr sees /data; this script runs on the LXC where the share is /mnt/media
    host_root = Path("/mnt/media/media/tv")

    def host_path(container_path: str) -> Path:
        p = Path(container_path)
        if str(p).startswith("/data/"):
            return Path("/mnt/media") / str(p)[len("/data/") :]
        return p

    print(f"unmapped_count {len(unmapped)}")
    print(f"series_count {len(series)}")

    safe, skip = [], []
    for u in unmapped:
        path = str(host_path(u["path"]))
        name = u["name"]
        n = norm(name)
        n2 = re.sub(
            r"^(www \S+ org|www \S+ com|www uindex org|www scenetime com|"
            r"www torrenting com|www torrenting org)\s+",
            "",
            n,
        )
        match = None
        for title, s in series_titles:
            if title and (title in n2 or n2.startswith(title)):
                match = s
                break
            if title.replace(" ", "") and title.replace(" ", "") in n2.replace(" ", ""):
                match = s
                break
        vids = videos(path)
        vsize = 0
        for f in vids:
            try:
                vsize += f.stat().st_size
            except OSError:
                pass
        if match is None:
            skip.append((name, "no_series_match", vsize, len(vids), path))
            continue
        eps = api(key, f"episodefile?seriesId={match['id']}")
        imported_sizes = {e.get("size") for e in eps}
        overlap = 0
        for f in vids:
            try:
                if f.stat().st_size in imported_sizes:
                    overlap += 1
            except OSError:
                continue
        if len(vids) == 0:
            skip.append((name, f"matched_{match['title']}_no_videos", vsize, 0, path))
            continue
        if overlap < len(vids):
            skip.append(
                (
                    name,
                    f"matched_{match['title']}_partial_{overlap}/{len(vids)}",
                    vsize,
                    len(vids),
                    path,
                )
            )
            continue
        safe.append((name, match["title"], vsize, len(vids), overlap, path))

    print("SAFE_DELETE", len(safe))
    for row in safe:
        print(
            f"SAFE\t{row[0][:90]}\t{row[1]}\t{row[2]}\tvids={row[3]}\toverlap={row[4]}"
        )
    print("SKIP", len(skip))
    for row in skip:
        print(f"SKIP\t{row[0][:90]}\t{row[1]}\t{row[2]}\tvids={row[3]}")
    print("SAFE_BYTES", sum(r[2] for r in safe))

    imported_all = {}
    for title, s in series_titles:
        for e in api(key, f"episodefile?seriesId={s['id']}"):
            imported_all.setdefault(e.get("size"), []).append(s["title"])
    print("ROOT_FILES")
    for f in host_root.iterdir():
        if not f.is_file() or f.suffix.lower() not in VIDEO:
            continue
        sz = f.stat().st_size
        if sz in imported_all:
            print(f"ROOT_SAFE\t{f.name[:90]}\t{imported_all[sz][0]}\t{sz}")
            if args.delete:
                f.unlink()
                print("DELETED_ROOT", f.name[:90])
        else:
            print(f"ROOT_SKIP\t{f.name[:90]}\t{sz}")

    if args.delete:
        deleted = 0
        for row in safe:
            p = Path(row[5])
            if p.is_dir():
                shutil.rmtree(p)
            elif p.is_file():
                p.unlink()
            else:
                print("MISSING", row[5])
                continue
            deleted += 1
            print("DELETED", row[0][:90])
        print("DELETED_COUNT", deleted)


if __name__ == "__main__":
    main()
