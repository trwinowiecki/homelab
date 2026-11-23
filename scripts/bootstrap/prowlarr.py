"""Apply Prowlarr settings from arrstack/prowlarr/bootstrap.yml."""
from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from arrstack_api_keys import PLACEHOLDER

from bootstrap.common import env, request_json, resolved_api_keys, service_url

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "arrstack" / "prowlarr" / "bootstrap.yml"

CARDIGANN_TYPES = frozenset(
    {"nyaasi", "1337x", "thepiratebay", "yts", "torrentleech", "eztv"}
)


def base_url(env_file: Path) -> tuple[str, str]:
    keys = resolved_api_keys(env_file)
    key = keys.get("PROWLARR_API_KEY", "")
    if not key or key in PLACEHOLDER:
        print("skip prowlarr: PROWLARR_API_KEY not set")
        return "", ""
    url = env("PROWLARR_URL") or service_url(env_file, "WEBUI_PORT_PROWLARR", 9696)
    return url.rstrip("/"), key


def ensure_tag(base: str, key: str, label: str) -> int:
    tags = request_json("GET", f"{base}/api/v1/tag", key) or []
    for tag in tags:
        if tag.get("label") == label:
            return tag["id"]
    created = request_json("POST", f"{base}/api/v1/tag", key, {"label": label})
    if not created or "id" not in created:
        raise RuntimeError(f"failed to create tag {label!r}")
    return created["id"]


def ensure_proxy(base: str, key: str, spec: dict, tag_id: int, *, dry_run: bool) -> None:
    proxies = request_json("GET", f"{base}/api/v1/indexerProxy", key) or []
    for proxy in proxies:
        if proxy.get("name") == "FlareSolverr":
            return
    if dry_run:
        print("prowlarr: would add FlareSolverr proxy")
        return
    body = {
        "name": "FlareSolverr",
        "implementation": "FlareSolverr",
        "configContract": "FlareSolverrSettings",
        "fields": [
            {"name": "host", "value": spec["flaresolverr"]["host_url"]},
            {"name": "requestTimeout", "value": 60},
        ],
        "tags": [tag_id],
    }
    request_json("POST", f"{base}/api/v1/indexerProxy", key, body)
    print("prowlarr: added FlareSolverr proxy")


def app_body(
    display: str,
    contract: str,
    implementation: str,
    spec: dict,
    app_key: str,
    *,
    sync_level: str | None = None,
) -> dict:
    return {
        "name": display,
        "syncLevel": sync_level or spec.get("sync_level", "fullSync"),
        "implementation": implementation,
        "configContract": contract,
        "fields": [
            {"name": "prowlarrUrl", "value": spec["prowlarr_url"]},
            {"name": "baseUrl", "value": spec["base_url"]},
            {"name": "apiKey", "value": app_key},
        ],
    }


def ensure_apps(base: str, key: str, apps: dict, env_file: Path, *, dry_run: bool) -> None:
    existing = request_json("GET", f"{base}/api/v1/applications", key) or []
    existing_by_name = {a.get("name"): a for a in existing if a.get("name")}
    api_keys = resolved_api_keys(env_file)
    impl = {
        "sonarr": ("Sonarr", "SonarrSettings", "Sonarr"),
        "radarr": ("Radarr", "RadarrSettings", "Radarr"),
        "lidarr": ("Lidarr", "LidarrSettings", "Lidarr"),
    }
    for name, spec in apps.items():
        app_key = api_keys.get(f"{name.upper()}_API_KEY", "")
        if not app_key or app_key in PLACEHOLDER:
            print(f"prowlarr: skip app {name} (missing API key)")
            continue
        display, contract, implementation = impl[name]
        existing_app = existing_by_name.get(display)
        if existing_app:
            app_id = existing_app.get("id")
            if not app_id:
                print(f"prowlarr: skip update for {display} (missing id)")
                continue
            body = app_body(
                display,
                contract,
                implementation,
                spec,
                app_key,
                sync_level=existing_app.get("syncLevel"),
            )
            if dry_run:
                print(f"prowlarr: would update app {display}")
                continue
            result = request_json(
                "PUT",
                f"{base}/api/v1/applications/{app_id}",
                key,
                body,
            )
            if result:
                print(f"prowlarr: updated app {display}")
            else:
                print(f"prowlarr: failed to update app {display}")
            continue

        body = app_body(display, contract, implementation, spec, app_key)
        if dry_run:
            print(f"prowlarr: would add app {display}")
            continue
        result = request_json("POST", f"{base}/api/v1/applications", key, body)
        if result:
            print(f"prowlarr: added app {display}")
        else:
            print(f"prowlarr: failed to add app {display}")


def resolve_app_profile_id(base: str, key: str, name: str) -> int:
    profiles = request_json("GET", f"{base}/api/v1/appprofile", key) or []
    for profile in profiles:
        if profile.get("name") == name:
            return profile["id"]
    return 1


def build_indexer_body(
    spec: dict,
    schema: list[dict],
    profile_id: int,
    tag_ids: list[int],
    env_file: Path,
) -> dict | None:
    name = spec["name"]
    indexer_type = spec.get("type", "")
    tags = list(tag_ids)

    if indexer_type == "newznab" or spec.get("implementation") == "Newznab":
        api_key_env = spec.get("api_key_env", "")
        api_key = env(api_key_env) if api_key_env else ""
        if not api_key:
            file_env = {}
            if env_file.is_file():
                from arrstack_api_keys import parse_env_assignments

                file_env = parse_env_assignments(env_file.read_text())
            api_key = file_env.get(api_key_env, "")
        if not api_key:
            print(f"prowlarr: skip indexer {name} (missing {api_key_env})")
            return None
        fields = [
            {"name": "baseUrl", "value": spec["base_url"]},
            {"name": "apiPath", "value": "/api"},
            {"name": "apiKey", "value": api_key},
        ]
        categories = spec.get("categories") or []
        if categories:
            fields.append({"name": "categories", "value": categories})
        return {
            "name": name,
            "implementation": "Newznab",
            "configContract": "NewznabSettings",
            "fields": fields,
            "appProfileId": profile_id,
            "enable": True,
            "redirect": True,
            "priority": spec.get("priority", 25),
            "tags": tags,
        }

    definition = indexer_type
    for entry in schema:
        if entry.get("definitionName") == definition or entry.get("name") == name:
            body = {
                "name": name,
                "implementation": entry.get("implementation", "Cardigann"),
                "configContract": entry.get("configContract", "CardigannSettings"),
                "fields": [{"name": "definitionFile", "value": definition}],
                "appProfileId": profile_id,
                "enable": True,
                "priority": spec.get("priority", 25),
                "tags": tags,
            }
            for field in spec.get("fields", []):
                body["fields"].append(field)
            return body

    if definition in CARDIGANN_TYPES or not spec.get("implementation"):
        return {
            "name": name,
            "implementation": "Cardigann",
            "configContract": "CardigannSettings",
            "fields": [{"name": "definitionFile", "value": definition}],
            "appProfileId": profile_id,
            "enable": True,
            "priority": spec.get("priority", 25),
            "tags": tags,
        }
    print(f"prowlarr: skip indexer {name} (unknown type {indexer_type!r})")
    return None


def ensure_indexers(
    base: str,
    key: str,
    indexers: list[dict],
    env_file: Path,
    tag_cache: dict[str, int],
    *,
    dry_run: bool,
) -> None:
    existing = request_json("GET", f"{base}/api/v1/indexer", key) or []
    by_name = {entry.get("name"): entry for entry in existing}
    schema = request_json("GET", f"{base}/api/v1/indexer/schema", key) or []
    changed = False

    for spec in indexers:
        name = spec["name"]
        current = by_name.get(name)
        if current is not None:
            desired = int(spec.get("priority", 25))
            if current.get("priority") == desired:
                continue
            body = dict(current)
            body["priority"] = desired
            if dry_run:
                print(f"prowlarr: would set indexer {name} priority {desired}")
                continue
            indexer_id = current.get("id")
            result = request_json("PUT", f"{base}/api/v1/indexer/{indexer_id}", key, body)
            if result:
                print(f"prowlarr: set indexer {name} priority {desired}")
                changed = True
            else:
                print(f"prowlarr: failed to set indexer {name} priority {desired}")
            continue
        profile_name = spec.get("sync_profile", "Standard")
        profile_id = resolve_app_profile_id(base, key, profile_name)
        tag_ids = [tag_cache[label] for label in spec.get("tags", []) if label in tag_cache]
        body = build_indexer_body(spec, schema, profile_id, tag_ids, env_file)
        if body is None:
            continue
        if dry_run:
            print(f"prowlarr: would add indexer {name}")
            continue
        query = urllib.parse.urlencode({"forceSave": "true"})
        result = request_json("POST", f"{base}/api/v1/indexer?{query}", key, body)
        if result:
            print(f"prowlarr: added indexer {name}")
            changed = True
        else:
            print(f"prowlarr: failed to add indexer {name}")

    if changed and not dry_run:
        synced = request_json(
            "POST",
            f"{base}/api/v1/command",
            key,
            {"name": "ApplicationIndexerSync"},
        )
        if synced:
            print("prowlarr: synced indexer priority to apps")
        else:
            print("prowlarr: failed to sync indexer priority to apps")


def apply(spec: dict, env_file: Path, *, dry_run: bool = False) -> None:
    base, key = base_url(env_file)
    if not base or not key:
        return

    tag_cache: dict[str, int] = {}
    if "indexer_proxy" in spec:
        for label in spec["indexer_proxy"].get("flaresolverr", {}).get("tags", ["flaresolverr"]):
            if dry_run:
                tag_cache[label] = 0
            else:
                tag_cache[label] = ensure_tag(base, key, label)
        ensure_proxy(base, key, spec["indexer_proxy"], tag_cache.get("flaresolverr", 0), dry_run=dry_run)

    if "apps" in spec:
        ensure_apps(base, key, spec["apps"], env_file, dry_run=dry_run)

    if "indexers" in spec:
        for indexer in spec["indexers"]:
            for label in indexer.get("tags", []):
                if label not in tag_cache:
                    tag_cache[label] = 0 if dry_run else ensure_tag(base, key, label)
        ensure_indexers(base, key, spec["indexers"], env_file, tag_cache, dry_run=dry_run)
