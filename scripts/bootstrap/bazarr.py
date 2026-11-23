"""Apply Bazarr settings from arrstack/bazarr/bootstrap.yml."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from arrstack_api_keys import PLACEHOLDER

from bootstrap.common import request_form, resolved_api_keys, service_url

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "arrstack" / "bazarr" / "bootstrap.yml"


def base_url(env_file: Path) -> tuple[str, str]:
    keys = resolved_api_keys(env_file)
    key = keys.get("BAZARR_API_KEY", "")
    if not key or key in PLACEHOLDER:
        print("skip bazarr: BAZARR_API_KEY not set")
        return "", ""
    url = service_url(env_file, "WEBUI_PORT_BAZARR", 6767)
    return url.rstrip("/"), key


def py_bool(value: bool) -> str:
    return "True" if value else "False"


def form_bool(value: bool) -> str:
    return "true" if value else "false"


def parse_arr_url(url: str) -> tuple[str, int, str, bool]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    base_path = parsed.path if parsed.path not in ("", "/") else ""
    return host, port, base_path, parsed.scheme == "https"


def arr_connection_fields(
    arr_name: str,
    url: str,
    api_key: str,
) -> list[tuple[str, str]]:
    host, port, base_path, use_ssl = parse_arr_url(url)
    prefix = f"settings-{arr_name}"
    return [
        (f"settings-general-use_{arr_name}", "true"),
        (f"{prefix}-ip", host),
        (f"{prefix}-port", str(port)),
        (f"{prefix}-apikey", api_key),
        (f"{prefix}-base_url", base_path),
        (f"{prefix}-ssl", form_bool(use_ssl)),
    ]


def build_settings_form(spec: dict, env_file: Path) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("settings-general-serie_default_enabled", "true"),
        ("settings-general-movie_default_enabled", "true"),
    ]

    keys = resolved_api_keys(env_file)
    for arr_name in ("sonarr", "radarr"):
        arr_spec = spec.get(arr_name)
        if not arr_spec:
            continue
        arr_key = keys.get(f"{arr_name.upper()}_API_KEY", "")
        if not arr_key or arr_key in PLACEHOLDER:
            print(f"bazarr: skip {arr_name} link (missing {arr_name.upper()}_API_KEY)")
            continue
        fields.extend(arr_connection_fields(arr_name, arr_spec["url"], arr_key))

    settings = spec.get("settings", {})
    mapping = {
        "series_subtitles": "settings-general-serie_default_language",
        "movie_subtitles": "settings-general-movie_default_language",
        "minimum_score": "settings-general-minimum_score",
        "hearing_impaired": "settings-general-hearing_impaired",
        "forced": "settings-general-forced",
    }
    for src, dst in mapping.items():
        if src in settings:
            value = settings[src]
            if isinstance(value, bool):
                value = py_bool(value)
            fields.append((dst, str(value)))

    for provider in spec.get("providers", []):
        if provider.get("enabled", True):
            fields.append(
                ("settings-general-enabled_providers", provider["name"])
            )

    languages = spec.get("languages", [])
    profile_items = []
    for idx, lang in enumerate(languages, start=1):
        code = lang.get("code", lang.get("language", "en"))
        fields.append(("languages-enabled", code))
        profile_items.append(
            {
                "profileId": idx,
                "name": lang.get("name", code.title()),
                "cutoff": None,
                "items": [
                    {
                        "id": 1,
                        "language": code,
                        "audio_exclude": "False",
                        "audio_only_include": "False",
                        "forced": py_bool(settings.get("forced", False)),
                        "hi": py_bool(settings.get("hearing_impaired", False)),
                    }
                ],
                "mustContain": [],
                "mustNotContain": [],
                "originalFormat": None,
                "tag": None,
            }
        )

    if profile_items:
        default_profile = profile_items[0]["profileId"]
        fields.append(("settings-general-serie_default_profile", str(default_profile)))
        fields.append(("settings-general-movie_default_profile", str(default_profile)))
        fields.append(("languages-profiles", json.dumps(profile_items)))

    return fields


def apply_settings(base: str, key: str, spec: dict, env_file: Path, *, dry_run: bool) -> None:
    fields = build_settings_form(spec, env_file)
    if not fields:
        return
    if dry_run:
        print(f"bazarr: would POST {len(fields)} settings field(s)")
        return
    status = request_form(f"{base}/api/system/settings", key, fields)
    if status in (200, 204):
        print("bazarr: applied settings")
    else:
        print(f"bazarr: settings apply failed (HTTP {status})")


def apply(spec: dict, env_file: Path, *, dry_run: bool = False) -> None:
    base, key = base_url(env_file)
    if not base or not key:
        return
    apply_settings(base, key, spec, env_file, dry_run=dry_run)
