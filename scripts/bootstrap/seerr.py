"""Apply Seerr settings from arrstack/seerr/bootstrap.yml."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from arrstack_api_keys import PLACEHOLDER, load_auto_env_assignments, load_env_folder_for_data

from bootstrap.common import load_env_file, request_json, resolved_api_keys, service_url

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "arrstack" / "seerr" / "bootstrap.yml"


def seerr_headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


def parse_service_url(url: str) -> tuple[str, int, str, bool]:
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


def settings_path(env_file: Path) -> Path | None:
    data_dir = load_env_folder_for_data(env_file)
    if data_dir is None:
        return None
    return data_dir / "jellyseerr" / "settings.json"


def seerr_initialized(path: Path) -> bool:
    if not path.is_file():
        return False
    data = json.loads(path.read_text())
    public = data.get("public") or {}
    return bool(public.get("initialized"))


def load_seerr_api_key_from_settings(path: Path) -> str:
    if not path.is_file():
        return ""
    data = json.loads(path.read_text())
    return str((data.get("main") or {}).get("apiKey") or "").strip()


def resolve_seerr_api_key(env_file: Path) -> str:
    file_env = load_env_file(env_file)
    key = file_env.get("SEERR_API_KEY", "").strip()
    if key and key not in PLACEHOLDER:
        return key

    data_dir = load_env_folder_for_data(env_file)
    if data_dir:
        auto = load_auto_env_assignments(data_dir)
        key = auto.get("SEERR_API_KEY", "").strip()
        if key and key not in PLACEHOLDER:
            return key

    cfg = settings_path(env_file)
    if cfg:
        return load_seerr_api_key_from_settings(cfg)
    return ""


def base_url(env_file: Path, *, require_initialized: bool = True) -> tuple[str, str]:
    cfg = settings_path(env_file)
    if cfg is None:
        print("skip seerr: FOLDER_FOR_DATA not found")
        return "", ""
    if require_initialized and not seerr_initialized(cfg):
        print("skip seerr: not initialized (complete Jellyfin first-admin in UI)")
        return "", ""
    seerr_key = resolve_seerr_api_key(env_file)
    if not seerr_key:
        print("skip seerr: SEERR_API_KEY missing (.env, arrstack.auto.env, or settings.json)")
        return "", ""
    url = service_url(env_file, "WEBUI_PORT_SEERR", 5055)
    return url.rstrip("/"), seerr_key


def test_arr(
    base: str,
    seerr_key: str,
    kind: str,
    arr_api_key: str,
    url_spec: dict,
) -> dict | None:
    host, port, base_path, use_ssl = parse_service_url(url_spec["url"])
    test_body: dict = {
        "hostname": host,
        "port": port,
        "apiKey": arr_api_key,
        "useSsl": use_ssl,
    }
    if base_path:
        test_body["baseUrl"] = base_path
    result = request_json(
        "POST",
        f"{base}/api/v1/settings/{kind}/test",
        api_key=seerr_key,
        body=test_body,
        headers=seerr_headers(seerr_key),
    )
    return result if isinstance(result, dict) else None


def pick_profile(profiles: list, name: str) -> dict | None:
    for profile in profiles:
        if profile.get("name") == name:
            return profile
    return None


def pick_root_folder(folders: list, path: str) -> dict | None:
    for folder in folders:
        folder_path = folder.get("path") if isinstance(folder, dict) else str(folder)
        if folder_path == path:
            return folder if isinstance(folder, dict) else {"path": folder_path}
    return None


def build_instance_body(
    kind: str,
    instance_spec: dict,
    url_spec: dict,
    arr_api_key: str,
    test_result: dict,
) -> dict | None:
    profiles = test_result.get("profiles") or []
    root_folders = test_result.get("rootFolders") or []
    profile_name = instance_spec.get("quality_profile") or ""
    root_path = instance_spec.get("root_folder") or ""

    profile = pick_profile(profiles, profile_name) if profile_name else None
    if profile is None and profiles:
        profile = profiles[0]
    folder = pick_root_folder(root_folders, root_path) if root_path else None
    if folder is None and root_folders:
        entry = root_folders[0]
        folder = entry if isinstance(entry, dict) else {"path": str(entry)}
    if not profile:
        print(f"seerr: {kind} profile {profile_name!r} not found on server")
        return None
    if not folder:
        print(f"seerr: {kind} root folder {root_path!r} not found on server")
        return None

    host, port, base_path, use_ssl = parse_service_url(url_spec["url"])
    folder_path = folder["path"] if isinstance(folder, dict) else str(folder)
    body: dict = {
        "name": instance_spec.get("name", kind.title()),
        "hostname": host,
        "port": port,
        "apiKey": arr_api_key,
        "useSsl": use_ssl,
        "baseUrl": base_path or "",
        "activeProfileId": profile["id"],
        "activeProfileName": profile["name"],
        "activeDirectory": folder_path,
        "is4k": bool(instance_spec.get("is_4k", False)),
        "enableSeasonFolders": False,
        "isDefault": bool(instance_spec.get("is_default", False)),
    }
    if kind == "sonarr":
        lang_profiles = test_result.get("languageProfiles")
        if lang_profiles:
            body["activeLanguageProfileId"] = lang_profiles[0]["id"]
        body["seriesType"] = "standard"
        body["animeSeriesType"] = "anime"
    if kind == "radarr":
        body["minimumAvailability"] = "released"
    return body


def upsert_instance(
    base: str,
    seerr_key: str,
    kind: str,
    instance_spec: dict,
    url_spec: dict,
    env_file: Path,
    existing: dict | None,
    *,
    dry_run: bool,
) -> None:
    env_name = f"{kind.upper()}_API_KEY"
    arr_api_key = resolved_api_keys(env_file).get(env_name, "")
    if not arr_api_key or arr_api_key in PLACEHOLDER:
        print(f"seerr: skip {kind} (missing {env_name})")
        return

    label = instance_spec.get("name", kind)
    if dry_run:
        action = "update" if existing else "create"
        print(f"seerr: would {action} {kind} instance {label!r}")
        return

    test_result = test_arr(base, seerr_key, kind, arr_api_key, url_spec)
    if not test_result:
        print(f"seerr: {kind} connection test failed for {label!r}")
        return

    body = build_instance_body(kind, instance_spec, url_spec, arr_api_key, test_result)
    if not body:
        return

    if existing and existing.get("id") is not None:
        inst_id = existing["id"]
        body.pop("id", None)
        result = request_json(
            "PUT",
            f"{base}/api/v1/settings/{kind}/{inst_id}",
            api_key=seerr_key,
            body=body,
            headers=seerr_headers(seerr_key),
        )
        if result:
            print(f"seerr: updated {kind} id={inst_id} ({label})")
        else:
            print(f"seerr: failed to update {kind} id={inst_id} ({label})")
        return

    result = request_json(
        "POST",
        f"{base}/api/v1/settings/{kind}",
        api_key=seerr_key,
        body=body,
        headers=seerr_headers(seerr_key),
    )
    if result:
        print(f"seerr: created {kind} instance {label!r}")
    else:
        print(f"seerr: failed to create {kind} instance {label!r}")


def refresh_connection_only(
    base: str,
    seerr_key: str,
    kind: str,
    instance: dict,
    arr_api_key: str,
    url_spec: dict,
    *,
    dry_run: bool,
) -> None:
    host, port, base_path, use_ssl = parse_service_url(url_spec["url"])
    updated = dict(instance)
    updated["apiKey"] = arr_api_key
    updated["hostname"] = host
    updated["port"] = port
    updated["useSsl"] = use_ssl
    if base_path:
        updated["baseUrl"] = base_path
    inst_id = instance.get("id")
    if inst_id is None:
        print(f"seerr: skip {kind} update (missing id)")
        return
    if dry_run:
        print(f"seerr: would update {kind} id={inst_id}")
        return
    updated.pop("id", None)
    result = request_json(
        "PUT",
        f"{base}/api/v1/settings/{kind}/{inst_id}",
        api_key=seerr_key,
        body=updated,
        headers=seerr_headers(seerr_key),
    )
    if result:
        print(f"seerr: updated {kind} id={inst_id}")
    else:
        print(f"seerr: failed to update {kind} id={inst_id}")


def ensure_instances(
    base: str,
    seerr_key: str,
    kind: str,
    url_spec: dict,
    env_file: Path,
    instance_specs: list[dict],
    *,
    dry_run: bool,
) -> None:
    instances = request_json(
        "GET",
        f"{base}/api/v1/settings/{kind}",
        api_key=seerr_key,
        headers=seerr_headers(seerr_key),
    )
    if instances is None:
        print(f"seerr: failed to list {kind} instances")
        return
    if not isinstance(instances, list):
        instances = []

    for spec in instance_specs:
        is_4k = bool(spec.get("is_4k", False))
        existing = next(
            (inst for inst in instances if bool(inst.get("is4k")) == is_4k),
            None,
        )
        upsert_instance(
            base,
            seerr_key,
            kind,
            spec,
            url_spec,
            env_file,
            existing,
            dry_run=dry_run,
        )


def ensure_dvr_legacy(
    base: str,
    seerr_key: str,
    kind: str,
    url_spec: dict,
    env_file: Path,
    *,
    dry_run: bool,
) -> None:
    env_name = f"{kind.upper()}_API_KEY"
    arr_api_key = resolved_api_keys(env_file).get(env_name, "")
    if not arr_api_key or arr_api_key in PLACEHOLDER:
        print(f"seerr: skip {kind} (missing {env_name})")
        return

    instances = request_json(
        "GET",
        f"{base}/api/v1/settings/{kind}",
        api_key=seerr_key,
        headers=seerr_headers(seerr_key),
    )
    if instances is None:
        print(f"seerr: failed to list {kind} instances")
        return
    if not isinstance(instances, list):
        instances = []

    if instances:
        for instance in instances:
            refresh_connection_only(
                base,
                seerr_key,
                kind,
                instance,
                arr_api_key,
                url_spec,
                dry_run=dry_run,
            )
        return

    default_spec = {
        "name": kind.title(),
        "quality_profile": "",
        "root_folder": "",
        "is_4k": False,
        "is_default": True,
    }
    upsert_instance(
        base,
        seerr_key,
        kind,
        default_spec,
        url_spec,
        env_file,
        None,
        dry_run=dry_run,
    )


def apply_kind(
    base: str,
    seerr_key: str,
    kind: str,
    url_spec: dict,
    env_file: Path,
    *,
    sync_profiles: bool,
    dry_run: bool,
) -> None:
    url = url_spec.get("url", "")
    if not url:
        print(f"seerr: skip {kind} (missing url in bootstrap)")
        return

    instances = url_spec.get("instances")
    if sync_profiles and instances:
        ensure_instances(
            base,
            seerr_key,
            kind,
            url_spec,
            env_file,
            instances,
            dry_run=dry_run,
        )
        return

    ensure_dvr_legacy(base, seerr_key, kind, url_spec, env_file, dry_run=dry_run)


def apply_main_settings(
    base: str,
    seerr_key: str,
    main_spec: dict,
    *,
    dry_run: bool,
) -> None:
    """Patch /api/v1/settings/main (applicationUrl, localLogin, …)."""
    if not main_spec:
        return
    allowed = ("applicationUrl", "localLogin", "mediaServerLogin", "applicationTitle")
    body = {k: main_spec[k] for k in allowed if k in main_spec}
    if not body:
        return
    if dry_run:
        print(f"seerr: would update main settings {sorted(body)}")
        return
    result = request_json(
        "POST",
        f"{base}/api/v1/settings/main",
        api_key=seerr_key,
        body=body,
        headers=seerr_headers(seerr_key),
    )
    if result:
        print(f"seerr: updated main settings ({', '.join(sorted(body))})")
    else:
        print("seerr: failed to update main settings")


def apply(
    spec: dict,
    env_file: Path,
    *,
    dry_run: bool = False,
    sync_profiles: bool = False,
) -> None:
    base, seerr_key = base_url(env_file, require_initialized=sync_profiles or True)
    if not base or not seerr_key:
        return

    if "main" in spec and isinstance(spec["main"], dict):
        apply_main_settings(base, seerr_key, spec["main"], dry_run=dry_run)

    if "sonarr" in spec:
        apply_kind(
            base,
            seerr_key,
            "sonarr",
            spec["sonarr"],
            env_file,
            sync_profiles=sync_profiles,
            dry_run=dry_run,
        )
    if "radarr" in spec:
        apply_kind(
            base,
            seerr_key,
            "radarr",
            spec["radarr"],
            env_file,
            sync_profiles=sync_profiles,
            dry_run=dry_run,
        )
