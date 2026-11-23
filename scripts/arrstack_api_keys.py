"""Read *ARR API keys from config volumes; write arrstack.auto.env."""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import string
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

AUTO_ENV_FILENAME = "arrstack.auto.env"
PLACEHOLDER = frozenset({"", "CHANGE_ME", "change_me"})

KEY_SOURCES: dict[str, tuple[str, str]] = {
    "SONARR_API_KEY": ("sonarr/config.xml", "arr_xml"),
    "RADARR_API_KEY": ("radarr/config.xml", "arr_xml"),
    "LIDARR_API_KEY": ("lidarr/config.xml", "arr_xml"),
    "PROWLARR_API_KEY": ("prowlarr/config.xml", "arr_xml"),
    "BAZARR_API_KEY": ("bazarr/config/config.yaml", "bazarr_yaml"),
    "SABNZBD_API_KEY": ("sabnzbd/sabnzbd.ini", "sab_ini"),
    "BINDERY_API_KEY": ("bindery/bindery.db", "bindery_db"),
    "QBITTORRENT_API_KEY": ("qbittorrent/qBittorrent/qBittorrent.conf", "qbit_conf"),
}

SEERR_SETTINGS_REL = "jellyseerr/settings.json"

HOMEPAGE_KEY_ALIASES = {
    "SONARR_API_KEY": "HOMEPAGE_VAR_SONARR_API_KEY",
    "RADARR_API_KEY": "HOMEPAGE_VAR_RADARR_API_KEY",
    "LIDARR_API_KEY": "HOMEPAGE_VAR_LIDARR_API_KEY",
    "PROWLARR_API_KEY": "HOMEPAGE_VAR_PROWLARR_API_KEY",
    "BAZARR_API_KEY": "HOMEPAGE_VAR_BAZARR_API_KEY",
    "SABNZBD_API_KEY": "HOMEPAGE_VAR_SABNZBD_API_KEY",
    "SEERR_API_KEY": "HOMEPAGE_VAR_SEERR_API_KEY",
    "AUDIOBOOKSHELF_API_KEY": "HOMEPAGE_VAR_AUDIOBOOKSHELF_API_KEY",
    "QBITTORRENT_API_KEY": "HOMEPAGE_VAR_QBITTORRENT_API_KEY",
    "JELLYFIN_API_KEY": "HOMEPAGE_VAR_JELLYFIN_API_KEY",
    "PORTAINER_API_KEY": "HOMEPAGE_VAR_PORTAINER_API_KEY",
    "MEALIE_OG_SYNC_TOKEN": "HOMEPAGE_VAR_MEALIE_OG_SYNC_TOKEN",
    "TRUENAS_API_KEY": "HOMEPAGE_VAR_TRUENAS_API_KEY",
}

HOMEPAGE_GRAFANA_USERNAME = "HOMEPAGE_VAR_GRAFANA_USERNAME"
HOMEPAGE_GRAFANA_PASSWORD = "HOMEPAGE_VAR_GRAFANA_PASSWORD"


def auto_env_path(data_dir: Path) -> Path:
    return data_dir / AUTO_ENV_FILENAME


def bare_value(value: str) -> str:
    return value.strip().strip('"').strip("'")


def read_arr_xml(path: Path) -> str:
    return (ET.parse(path).findtext("ApiKey") or "").strip()


def read_bazarr_yaml(path: Path) -> str:
    if yaml is not None:
        data = yaml.safe_load(path.read_text()) or {}
        auth = data.get("auth") or {}
        return str(auth.get("apikey") or auth.get("apiKey") or "").strip()

    text = path.read_text()
    in_auth = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^auth:\s*$", stripped, re.I):
            in_auth = True
            continue
        if in_auth:
            if stripped and not line.startswith((" ", "\t")):
                in_auth = False
                continue
            m = re.match(r"^apikey:\s*['\"]?([^'\"#\s]+)", stripped, re.I)
            if m:
                return m.group(1).strip()
    for line in text.splitlines():
        m = re.match(r"^\s*apikey:\s*['\"]?([^'\"#\s]+)", line, re.I)
        if m:
            return m.group(1).strip()
    return ""


def read_sab_ini(path: Path) -> str:
    """Read misc.api_key. SABnzbd writes __version__/__encoding__ before the first section."""
    in_misc = False
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            in_misc = stripped == "[misc]"
            continue
        if not in_misc:
            continue
        match = re.match(r"^api_key\s*=\s*(.*?)\s*$", stripped)
        if match:
            return bare_value(match.group(1))
    return ""


def generate_qbit_api_key() -> str:
    """qBittorrent ≥5.2 API keys: qbt_ + 28 alphanumeric chars."""
    alphabet = string.ascii_letters + string.digits
    return "qbt_" + "".join(secrets.choice(alphabet) for _ in range(28))


def read_qbit_conf(path: Path) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text().splitlines():
        m = re.match(r"^WebUI\\APIKey=(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return ""


def write_qbit_conf(path: Path, api_key: str) -> bool:
    """Upsert WebUI\\APIKey under [Preferences] in qBittorrent.conf."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"WebUI\\APIKey={api_key}"
    if path.is_file():
        text = path.read_text()
        if re.search(rf"^WebUI\\APIKey={re.escape(api_key)}$", text, re.M):
            return False
        if re.search(r"^WebUI\\APIKey=", text, re.M):
            text = re.sub(
                r"^WebUI\\APIKey=.*$",
                lambda _: line,
                text,
                count=1,
                flags=re.M,
            )
        elif re.search(r"^\[Preferences\]", text, re.M):
            text = re.sub(
                r"(\[Preferences\]\s*\n)",
                lambda m: f"{m.group(1)}{line}\n",
                text,
                count=1,
            )
        else:
            text = f"[Preferences]\n{line}\n{text}"
        path.write_text(text)
        return True
    path.write_text(f"[Preferences]\n{line}\n")
    return True


def read_seerr_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def read_seerr_api_key(data_dir: Path) -> str:
    data = read_seerr_settings(data_dir / SEERR_SETTINGS_REL)
    return str((data.get("main") or {}).get("apiKey") or "").strip()


def read_jellyfin_api_key_from_seerr(data_dir: Path) -> str:
    data = read_seerr_settings(data_dir / SEERR_SETTINGS_REL)
    return str((data.get("jellyfin") or {}).get("apiKey") or "").strip()


def load_obsv_grafana_password(env_file: Path | None) -> str:
    if env_file is None or not env_file.is_file():
        return ""
    obsv_env = env_file.parent / "obsv" / ".env"
    if not obsv_env.is_file():
        return ""
    password = parse_env_assignments(obsv_env.read_text()).get("GRAFANA_ADMIN_PASSWORD", "")
    return bare_value(password) if password and password not in PLACEHOLDER else ""


def apply_homepage_aliases(
    out: dict[str, str],
    changed: list[str],
    *,
    keys: dict[str, str] | None = None,
) -> None:
    source = keys if keys is not None else out
    for key, alias in HOMEPAGE_KEY_ALIASES.items():
        value = source.get(key, "")
        if value and value not in PLACEHOLDER:
            if out.get(alias) != value:
                changed.append(alias)
            out[alias] = value


def read_bindery_db(path: Path) -> str:
    """Read the live API key from Bindery's SQLite settings (auth.api_key)."""
    if not path.is_file():
        return ""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("auth.api_key",),
            ).fetchone()
        finally:
            con.close()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def read_key(path: Path, kind: str) -> str:
    if not path.is_file():
        return ""
    readers = {
        "arr_xml": read_arr_xml,
        "bazarr_yaml": read_bazarr_yaml,
        "sab_ini": read_sab_ini,
        "bindery_db": read_bindery_db,
        "qbit_conf": read_qbit_conf,
    }
    try:
        return readers[kind](path)
    except Exception:
        return ""


def read_keys_from_data_dir(data_dir: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for env_name, (rel_path, kind) in KEY_SOURCES.items():
        key = read_key(data_dir / rel_path, kind)
        if key and key not in PLACEHOLDER:
            found[env_name] = key
    return found


def parse_env_value(raw: str) -> str:
    """Parse a single .env value: strip unquoted trailing comments and outer quotes."""
    value = raw.strip()
    in_single = in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            value = value[:i]
            break
    return bare_value(value)


def parse_env_assignments(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", stripped)
        if m:
            out[m.group(1)] = parse_env_value(m.group(2))
    return out


def load_env_folder_for_data(env_path: Path) -> Path | None:
    for line in env_path.read_text().splitlines():
        m = re.match(r"^FOLDER_FOR_DATA=(.*)$", line)
        if m:
            return Path(bare_value(m.group(1)))
    return None


def load_auto_env_assignments(data_dir: Path) -> dict[str, str]:
    path = auto_env_path(data_dir)
    if not path.is_file():
        return {}
    return parse_env_assignments(path.read_text())


def build_auto_env_assignments(
    data_dir: Path,
    existing: dict[str, str],
    *,
    env_file: Path | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Merge disk API keys + homepage aliases + Mixarr secret into auto env dict."""
    out = dict(existing)
    changed: list[str] = []

    disk_keys = read_keys_from_data_dir(data_dir)
    for key, value in disk_keys.items():
        if out.get(key) != value:
            changed.append(key)
        out[key] = value

    seerr_key = read_seerr_api_key(data_dir)
    env_seerr = bare_value(existing.get("SEERR_API_KEY", ""))
    if env_seerr and env_seerr not in PLACEHOLDER:
        seerr_key = env_seerr
    if seerr_key and seerr_key not in PLACEHOLDER:
        if out.get("SEERR_API_KEY") != seerr_key:
            changed.append("SEERR_API_KEY")
        out["SEERR_API_KEY"] = seerr_key

    jellyfin_key = read_jellyfin_api_key_from_seerr(data_dir)
    env_jellyfin = bare_value(existing.get("JELLYFIN_API_KEY", ""))
    if env_jellyfin and env_jellyfin not in PLACEHOLDER:
        jellyfin_key = env_jellyfin
    if jellyfin_key and jellyfin_key not in PLACEHOLDER:
        if out.get("JELLYFIN_API_KEY") != jellyfin_key:
            changed.append("JELLYFIN_API_KEY")
        out["JELLYFIN_API_KEY"] = jellyfin_key

    grafana_password = load_obsv_grafana_password(env_file)
    if grafana_password:
        if out.get(HOMEPAGE_GRAFANA_USERNAME) != "admin":
            changed.append(HOMEPAGE_GRAFANA_USERNAME)
        out[HOMEPAGE_GRAFANA_USERNAME] = "admin"
        if out.get(HOMEPAGE_GRAFANA_PASSWORD) != grafana_password:
            changed.append(HOMEPAGE_GRAFANA_PASSWORD)
        out[HOMEPAGE_GRAFANA_PASSWORD] = grafana_password

    if env_file and env_file.is_file():
        env_vals = parse_env_assignments(env_file.read_text())
        for key in ("MEALIE_OG_SYNC_TOKEN", "MIXARR_SESSION_SECRET", "TRUENAS_API_KEY"):
            value = bare_value(env_vals.get(key, ""))
            if value and value not in PLACEHOLDER:
                if out.get(key) != value:
                    changed.append(key)
                out[key] = value

    apply_homepage_aliases(out, changed)

    session = bare_value(out.get("SESSION_SECRET", ""))
    if not session or session in PLACEHOLDER:
        session = secrets.token_hex(32)
        out["SESSION_SECRET"] = session
        changed.append("SESSION_SECRET")

    for extra in (
        "BINDERY_API_KEY",
        "AUDIOBOOKSHELF_API_KEY",
        "PORTAINER_API_KEY",
        "QBITTORRENT_API_KEY",
        "SEERR_API_KEY",
    ):
        if extra in disk_keys:
            continue
        if extra in existing and existing[extra] not in PLACEHOLDER:
            if out.get(extra) != existing[extra]:
                out[extra] = existing[extra]
                changed.append(extra)

    apply_homepage_aliases(out, changed)

    # De-dupe changed while preserving order
    seen: set[str] = set()
    unique_changed: list[str] = []
    for name in changed:
        if name not in seen:
            seen.add(name)
            unique_changed.append(name)
    return out, unique_changed


def format_auto_env(assignments: dict[str, str]) -> str:
    lines = [
        "# Generated by sync_arrstack_api_keys.py — do not edit manually",
        "# Loaded via docker-compose env_file (required: false).",
        "",
    ]
    for key in sorted(assignments):
        lines.append(f"{key}={assignments[key]}")
    lines.append("")
    return "\n".join(lines)


def write_auto_env_file(
    path: Path,
    assignments: dict[str, str],
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Atomic write (temp + replace) for CIFS/SMB-safe updates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = format_auto_env(assignments)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{AUTO_ENV_FILENAME}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp, 0o640)
        if uid is not None and gid is not None:
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
