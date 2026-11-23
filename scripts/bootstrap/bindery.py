"""Apply Bindery settings from arrstack/bindery/bootstrap.yml."""
from __future__ import annotations

from pathlib import Path

from arrstack_api_keys import PLACEHOLDER

from bootstrap.common import load_env_file, request_json, resolved_api_keys, service_url

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "arrstack" / "bindery" / "bootstrap.yml"


def base_url(env_file: Path) -> tuple[str, str]:
    keys = resolved_api_keys(env_file)
    key = keys.get("BINDERY_API_KEY", "")
    if not key or key in PLACEHOLDER:
        print("skip bindery: BINDERY_API_KEY not set")
        return "", ""
    url = service_url(env_file, "WEBUI_PORT_BINDERY", 8787)
    return url.rstrip("/"), key


def api_headers(key: str) -> dict[str, str]:
    return {"X-Api-Key": key, "Content-Type": "application/json"}


def resolve_path_remap(client: dict, env_file: Path) -> str:
    raw = client.get("path_remap", "")
    if raw in {"", None}:
        return ""
    if str(raw).lower() != "auto":
        return str(raw)
    downloads = load_env_file(env_file).get("FOLDER_FOR_DOWNLOADS", "").strip()
    if not downloads or downloads == "/downloads":
        return ""
    return f"{downloads.rstrip('/')}:/downloads"


def download_client_body(client: dict, env_file: Path) -> dict:
    body: dict = {
        "name": client["name"],
        "type": client.get("type", "qbittorrent"),
        "host": client["host"],
        "port": client.get("port", 8200),
        "useSsl": client.get("use_ssl", False),
        "urlBase": client.get("url_base", "/"),
        "category": client.get("category", ""),
        "enabled": client.get("enabled", True),
    }
    path_remap = resolve_path_remap(client, env_file)
    if path_remap:
        body["pathRemap"] = path_remap
    username = client.get("username", "")
    password = client.get("password", "")
    if username:
        body["username"] = username
    if password:
        body["password"] = password
    return body


def ensure_prowlarr(base: str, key: str, spec: dict, env_file: Path, *, dry_run: bool) -> None:
    prow_spec = spec.get("prowlarr")
    if not prow_spec:
        return
    servers = request_json(
        "GET",
        f"{base}/api/v1/prowlarr",
        api_key=key,
        headers=api_headers(key),
    ) or []
    prow_key = resolved_api_keys(env_file).get("PROWLARR_API_KEY", "")
    target_name = prow_spec.get("name", "Prowlarr")
    for server in servers:
        if server.get("name") != target_name:
            continue
        if not prow_key or prow_key in PLACEHOLDER:
            print("bindery: skip Prowlarr update (missing PROWLARR_API_KEY)")
            return
        server_id = server.get("id")
        if not server_id:
            print("bindery: skip Prowlarr update (missing id)")
            return
        body = {
            "name": target_name,
            "url": prow_spec["url"],
            "apiKey": prow_key,
            "syncOnStartup": server.get("syncOnStartup", True),
            "enabled": server.get("enabled", True),
        }
        if dry_run:
            print("bindery: would update Prowlarr")
            return
        updated = request_json(
            "PUT",
            f"{base}/api/v1/prowlarr/{server_id}",
            api_key=key,
            body=body,
            headers=api_headers(key),
        )
        if not updated:
            print("bindery: failed to update Prowlarr")
            return
        print("bindery: updated Prowlarr")
        if prow_spec.get("sync"):
            request_json(
                "POST",
                f"{base}/api/v1/prowlarr/{server_id}/sync",
                api_key=key,
                headers=api_headers(key),
            )
            print("bindery: synced Prowlarr indexers")
        return

    if not prow_key:
        print("bindery: skip Prowlarr (missing PROWLARR_API_KEY)")
        return
    body = {
        "name": target_name,
        "url": prow_spec["url"],
        "apiKey": prow_key,
    }
    if dry_run:
        print("bindery: would register Prowlarr")
        return
    created = request_json(
        "POST",
        f"{base}/api/v1/prowlarr",
        api_key=key,
        body=body,
        headers=api_headers(key),
    )
    if not created:
        print("bindery: failed to register Prowlarr")
        return
    if prow_spec.get("sync") and created.get("id"):
        request_json(
            "POST",
            f"{base}/api/v1/prowlarr/{created['id']}/sync",
            api_key=key,
            headers=api_headers(key),
        )
    print("bindery: registered Prowlarr")


def ensure_root_folders(base: str, key: str, folders: list[dict], *, dry_run: bool) -> None:
    existing = request_json(
        "GET",
        f"{base}/api/v1/rootfolder",
        api_key=key,
        headers=api_headers(key),
    ) or []
    existing_paths = {entry.get("path") for entry in existing}
    for folder in folders:
        path = folder["path"]
        if path in existing_paths:
            continue
        body = {"path": path, "name": folder.get("name", path.rsplit("/", 1)[-1])}
        if dry_run:
            print(f"bindery: would add root folder {path}")
            continue
        result = request_json(
            "POST",
            f"{base}/api/v1/rootfolder",
            api_key=key,
            body=body,
            headers=api_headers(key),
        )
        if result:
            print(f"bindery: added root folder {path}")
        else:
            print(f"bindery: failed to add root folder {path}")


def ensure_download_clients(
    base: str,
    key: str,
    clients: list[dict],
    env_file: Path,
    *,
    dry_run: bool,
) -> None:
    existing = request_json(
        "GET",
        f"{base}/api/v1/downloadclient",
        api_key=key,
        headers=api_headers(key),
    ) or []
    existing_by_name = {entry.get("name"): entry for entry in existing if entry.get("name")}
    for client in clients:
        name = client["name"]
        body = download_client_body(client, env_file)
        if name in existing_by_name:
            client_id = existing_by_name[name].get("id")
            if not client_id:
                print(f"bindery: skip update for {name} (missing id)")
                continue
            if dry_run:
                print(f"bindery: would update download client {name}")
                continue
            result = request_json(
                "PUT",
                f"{base}/api/v1/downloadclient/{client_id}",
                api_key=key,
                body=body,
                headers=api_headers(key),
            )
            if result:
                print(f"bindery: updated download client {name}")
            else:
                print(f"bindery: failed to update download client {name}")
            continue
        if dry_run:
            print(f"bindery: would add download client {name}")
            continue
        result = request_json(
            "POST",
            f"{base}/api/v1/downloadclient",
            api_key=key,
            body=body,
            headers=api_headers(key),
        )
        if result:
            print(f"bindery: added download client {name}")
        else:
            print(f"bindery: failed to add download client {name}")


def apply(spec: dict, env_file: Path, *, dry_run: bool = False) -> None:
    base, key = base_url(env_file)
    if not base or not key:
        return
    ensure_prowlarr(base, key, spec, env_file, dry_run=dry_run)
    if "root_folders" in spec:
        ensure_root_folders(base, key, spec["root_folders"], dry_run=dry_run)
    if "download_clients" in spec:
        ensure_download_clients(
            base, key, spec["download_clients"], env_file, dry_run=dry_run
        )
