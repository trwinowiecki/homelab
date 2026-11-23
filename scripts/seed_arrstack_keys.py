"""Seed deterministic API keys from .env into config volumes before compose up."""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from arrstack_api_keys import (
    PLACEHOLDER,
    apply_homepage_aliases,
    auto_env_path,
    bare_value,
    generate_qbit_api_key,
    load_auto_env_assignments,
    load_env_folder_for_data,
    parse_env_assignments,
    read_key,
    write_auto_env_file,
    write_qbit_conf,
)

ROOT = Path(__file__).resolve().parents[1]

SERVARR_APPS = {
    "SONARR_API_KEY": ("sonarr/config.xml", "arr_xml"),
    "RADARR_API_KEY": ("radarr/config.xml", "arr_xml"),
    "LIDARR_API_KEY": ("lidarr/config.xml", "arr_xml"),
    "PROWLARR_API_KEY": ("prowlarr/config.xml", "arr_xml"),
}

BAZARR_KEY = ("bazarr/config/config.yaml", "bazarr_yaml")
QBIT_KEY = ("qbittorrent/qBittorrent/qBittorrent.conf", "qbit_conf")
SEERR_SETTINGS = "jellyseerr/settings.json"


def generate_key() -> str:
    return secrets.token_hex(16)


def resolve_key(env_name: str, env_value: str, data_dir: Path) -> str:
    if env_value and env_value not in PLACEHOLDER:
        return env_value
    rel, kind = SERVARR_APPS.get(env_name, BAZARR_KEY if env_name == "BAZARR_API_KEY" else ("", ""))
    if rel:
        disk = read_key(data_dir / rel, kind)
        if disk and disk not in PLACEHOLDER:
            return disk
    return generate_key()


def write_arr_xml(path: Path, api_key: str) -> bool:
    if path.is_file():
        tree = ET.parse(path)
        root = tree.getroot()
        node = root.find("ApiKey")
        if node is not None and (node.text or "").strip() == api_key:
            return False
        if node is None:
            node = ET.SubElement(root, "ApiKey")
        node.text = api_key
        tree.write(path, encoding="utf-8", xml_declaration=True)
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("Config")
    ET.SubElement(root, "ApiKey").text = api_key
    ET.SubElement(root, "Port").text = "8989"
    ET.SubElement(root, "AuthenticationMethod").text = "External"
    ET.SubElement(root, "AuthenticationRequired").text = "DisabledForLocalAddresses"
    tree = ET.ElementTree(root)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return True


def write_bazarr_config(path: Path, api_key: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        text = path.read_text()
        if re.search(r"^\s*apikey:\s*['\"]?%s" % re.escape(api_key), text, re.I | re.M):
            return False
        if re.search(r"^\s*apikey:", text, re.I | re.M):
            text = re.sub(
                r"(^\s*apikey:\s*)(['\"]?)[^'\"#\n]+",
                rf"\1\2{api_key}",
                text,
                count=1,
                flags=re.I | re.M,
            )
        else:
            if "auth:" not in text:
                text = f"auth:\n  apikey: {api_key}\n  type: null\n" + text
            else:
                text = re.sub(
                    r"(auth:\s*\n)",
                    rf"\1  apikey: {api_key}\n",
                    text,
                    count=1,
                )
        path.write_text(text)
        return True

    content = f"""auth:
  apikey: {api_key}
  type: null
general:
  use_sonarr: false
  use_radarr: false
"""
    path.write_text(content)
    return True


def write_seerr_settings(path: Path, api_key: str) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    main = data.setdefault("main", {})
    if main.get("apiKey") == api_key:
        return False
    main["apiKey"] = api_key
    path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def resolve_seerr_key(env_value: str, data_dir: Path) -> str:
    if env_value and env_value not in PLACEHOLDER:
        return env_value
    settings_path = data_dir / SEERR_SETTINGS
    if settings_path.is_file():
        try:
            data = json.loads(settings_path.read_text())
            disk = str((data.get("main") or {}).get("apiKey") or "").strip()
            if disk and disk not in PLACEHOLDER:
                return disk
        except (json.JSONDecodeError, OSError):
            pass
    return generate_key()


def resolve_qbit_key(env_value: str, data_dir: Path) -> str:
    if env_value and env_value not in PLACEHOLDER:
        return env_value
    disk = read_key(data_dir / QBIT_KEY[0], QBIT_KEY[1])
    if disk and disk not in PLACEHOLDER:
        return disk
    return generate_qbit_api_key()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic arrstack API keys.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env_path = args.env_file.resolve()
    if not env_path.is_file():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        return 1

    env_vars = parse_env_assignments(env_path.read_text())
    data_dir = load_env_folder_for_data(env_path)
    if data_dir is None:
        print("ERROR: FOLDER_FOR_DATA not found", file=sys.stderr)
        return 1

    assignments = dict(load_auto_env_assignments(data_dir))
    changed: list[str] = []

    for env_name, (rel_path, kind) in SERVARR_APPS.items():
        canonical = resolve_key(env_name, env_vars.get(env_name, ""), data_dir)
        path = data_dir / rel_path
        if args.dry_run:
            print(f"would seed {env_name} -> {path}")
        elif write_arr_xml(path, canonical):
            print(f"seeded {env_name} in {path}")
        assignments[env_name] = canonical
        changed.append(env_name)

    bazarr_key = resolve_key("BAZARR_API_KEY", env_vars.get("BAZARR_API_KEY", ""), data_dir)
    bazarr_path = data_dir / BAZARR_KEY[0]
    if args.dry_run:
        print(f"would seed BAZARR_API_KEY -> {bazarr_path}")
    elif write_bazarr_config(bazarr_path, bazarr_key):
        print(f"seeded BAZARR_API_KEY in {bazarr_path}")
    assignments["BAZARR_API_KEY"] = bazarr_key
    changed.append("BAZARR_API_KEY")

    bindery_key = env_vars.get("BINDERY_API_KEY", "")
    if not bindery_key or bindery_key in PLACEHOLDER:
        bindery_key = assignments.get("BINDERY_API_KEY") or generate_key()
    assignments["BINDERY_API_KEY"] = bindery_key
    changed.append("BINDERY_API_KEY")

    qbit_key = resolve_qbit_key(env_vars.get("QBITTORRENT_API_KEY", ""), data_dir)
    qbit_path = data_dir / QBIT_KEY[0]
    if args.dry_run:
        print(f"would seed QBITTORRENT_API_KEY -> {qbit_path}")
    elif write_qbit_conf(qbit_path, qbit_key):
        print(f"seeded QBITTORRENT_API_KEY in {qbit_path}")
    assignments["QBITTORRENT_API_KEY"] = qbit_key
    changed.append("QBITTORRENT_API_KEY")

    seerr_key = resolve_seerr_key(env_vars.get("SEERR_API_KEY", ""), data_dir)
    seerr_path = data_dir / SEERR_SETTINGS
    if args.dry_run:
        print(f"would seed SEERR_API_KEY -> {seerr_path}")
    elif write_seerr_settings(seerr_path, seerr_key):
        print(f"seeded SEERR_API_KEY in {seerr_path}")
    assignments["SEERR_API_KEY"] = seerr_key
    changed.append("SEERR_API_KEY")

    apply_homepage_aliases(assignments, changed)

    puid = int(bare_value(env_vars.get("PUID", "10000")) or "10000")
    pgid = int(bare_value(env_vars.get("PGID", "10000")) or "10000")

    if not args.dry_run:
        write_auto_env_file(auto_env_path(data_dir), assignments, uid=puid, gid=pgid)
        print(f"updated {auto_env_path(data_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
