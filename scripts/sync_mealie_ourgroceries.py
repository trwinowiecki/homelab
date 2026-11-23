#!/usr/bin/env python3
"""Sync unchecked Mealie staging-list items to OurGroceries store lists."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from minyaml import load as load_yaml  # noqa: E402
from og_resolver import (  # noqa: E402
    RouteDecision,
    build_item_index,
    format_item_note,
    mealie_item_name,
    normalize_name,
    resolve_item_route,
)

OURGROCERIES_BASE = "https://www.ourgroceries.com"
SIGN_IN_URL = f"{OURGROCERIES_BASE}/sign-in"
YOUR_LISTS_URL = f"{OURGROCERIES_BASE}/your-lists/"


@dataclass
class SyncStats:
    added: dict[str, int] = field(default_factory=dict)
    uncrossed: dict[str, int] = field(default_factory=dict)
    skipped_active: dict[str, int] = field(default_factory=dict)
    used_default: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def bump(self, bucket: dict[str, int], list_name: str) -> None:
        bucket[list_name] = bucket.get(list_name, 0) + 1


@dataclass
class SyncConfig:
    staging_list: str
    default_list: str
    overrides: dict[str, str]


@dataclass
class SyncSettings:
    mealie_base_url: str
    mealie_api_token: str
    mealie_staging_list: str
    og_email: str
    og_password: str
    config_path: Path
    keep_staging: bool = False


class MealieClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Mealie API {method} {path} failed: {exc.code} {detail}") from exc

    def find_staging_list_id(self, name: str) -> str:
        payload = self._request("GET", "/api/households/shopping/lists")
        items = payload.get("items", []) if isinstance(payload, dict) else payload
        for lst in items:
            if str(lst.get("name", "")).strip() == name:
                list_id = lst.get("id")
                if list_id:
                    return str(list_id)
        raise RuntimeError(f"Mealie staging list not found: {name!r}")

    def fetch_unchecked_items(self, list_id: str) -> list[dict[str, Any]]:
        query = {
            "page": "1",
            "perPage": "-1",
            "queryFilter": f"shopping_list_id={list_id}",
        }
        payload = self._request("GET", "/api/households/shopping/items", query=query)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [item for item in items if not item.get("checked")]

    def delete_items(self, item_ids: list[str]) -> None:
        for item_id in item_ids:
            self._request("DELETE", f"/api/households/shopping/items/{item_id}")


class OurGroceriesClient:
    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self.auth_cookie: str | None = None
        self.team_id: str | None = None
        self.master_list_id: str | None = None

    async def login(self) -> None:
        import aiohttp

        form = aiohttp.FormData()
        form.add_field("emailAddress", self.email)
        form.add_field("password", self.password)
        form.add_field("action", "sign-in")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
            async with session.post(SIGN_IN_URL, data=form) as resp:
                cookies = session.cookie_jar.filter_cookies(OURGROCERIES_BASE)
                cookie = cookies.get("ourgroceries-auth")
                if not cookie:
                    raise RuntimeError("OurGroceries login failed: missing auth cookie")
                self.auth_cookie = cookie.value

        async with aiohttp.ClientSession(
            cookies={"ourgroceries-auth": self.auth_cookie},
        ) as session:
            async with session.get(YOUR_LISTS_URL) as resp:
                html = await resp.text()
                team_match = re.search(r'g_teamId = "([^"]+)";', html)
                master_match = re.search(
                    r'g_masterListUrl = "/your-lists/list/(\S*)"',
                    html,
                )
                if not team_match:
                    raise RuntimeError("OurGroceries login failed: team id not found")
                self.team_id = team_match.group(1)
                if master_match:
                    self.master_list_id = master_match.group(1)

    async def _post(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        import aiohttp

        if not self.auth_cookie or not self.team_id:
            await self.login()
        body = {"command": command, "teamId": self.team_id, "shareId": None, "locale": "en-US"}
        if payload:
            body.update(payload)
        async with aiohttp.ClientSession(
            cookies={"ourgroceries-auth": self.auth_cookie},
        ) as session:
            async with session.post(YOUR_LISTS_URL, json=body) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(
                        f"OurGroceries API {command} failed: {resp.status} {text}",
                    )
                return await resp.json()

    async def get_overview(self) -> dict[str, Any]:
        data = await self._post("getOverview")
        if not isinstance(data, dict):
            raise RuntimeError("OurGroceries getOverview returned unexpected payload")
        return data

    async def get_list_items(self, list_id: str) -> list[dict[str, Any]]:
        data = await self._post("getList", {"listId": list_id})
        lst = data.get("list", {}) if isinstance(data, dict) else {}
        items = lst.get("items", [])
        for item in items:
            item.setdefault("crossedOff", False)
        return items

    async def add_item(self, list_id: str, value: str, note: str = "") -> None:
        payload: dict[str, Any] = {
            "listId": list_id,
            "value": value,
            "isFromRecipe": False,
        }
        if note:
            payload["note"] = note
        await self._post("insertItem", payload)

    async def uncross_item(self, list_id: str, item_id: str) -> None:
        await self._post(
            "setItemCrossedOff",
            {"listId": list_id, "itemId": item_id, "crossedOff": False},
        )


def load_sync_config(path: Path, staging_list_env: str) -> SyncConfig:
    if path.is_file():
        raw = load_yaml(path.read_text())
    else:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"sync config must be a mapping: {path}")

    staging_list = str(raw.get("staging_list") or staging_list_env).strip()
    default_list = str(raw.get("default_list") or "").strip()
    if not default_list:
        raise ValueError(f"default_list is required in {path}")

    overrides_raw = raw.get("overrides") or {}
    if not isinstance(overrides_raw, dict):
        raise ValueError("overrides must be a mapping")
    overrides = {
        normalize_name(str(key)): str(value).strip()
        for key, value in overrides_raw.items()
        if str(value).strip()
    }
    return SyncConfig(
        staging_list=staging_list,
        default_list=default_list,
        overrides=overrides,
    )


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def default_credentials_path(env: dict[str, str]) -> Path:
    explicit = env.get("MEALIE_OG_SYNC_CREDENTIALS", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = env.get("FOLDER_FOR_DATA", "").strip()
    if data_dir:
        return Path(data_dir) / "mealie-og-sync" / "credentials.env"
    return Path("/config/credentials.env")


def load_settings(args: argparse.Namespace) -> SyncSettings:
    env: dict[str, str] = {}
    if args.env_file:
        env = load_env_file(Path(args.env_file))

    def pick(name: str, default: str = "") -> str:
        return os.environ.get(name, env.get(name, default)).strip()

    credentials = load_env_file(default_credentials_path(env))
    config_path = Path(
        pick("MEALIE_OG_SYNC_CONFIG", str(ROOT / "arrstack" / "mealie-og-sync.yaml")),
    )
    return SyncSettings(
        mealie_base_url=pick("MEALIE_BASE_URL", "http://mealie:9000"),
        mealie_api_token=pick("MEALIE_API_TOKEN"),
        mealie_staging_list=pick("MEALIE_STAGING_LIST", "Weekly Meals (staging)"),
        og_email=pick("OURGROCERIES_EMAIL") or credentials.get("OURGROCERIES_EMAIL", ""),
        og_password=pick("OURGROCERIES_PASSWORD")
        or credentials.get("OURGROCERIES_PASSWORD", ""),
        config_path=config_path,
        keep_staging=args.keep_staging,
    )


async def _build_og_index(og: OurGroceriesClient) -> tuple[dict[str, str], dict[str, list]]:
    overview = await og.get_overview()
    lists = overview.get("lists", [])
    shopping_lists = [
        lst for lst in lists if lst.get("listType") == "SHOPPING" and lst.get("id")
    ]
    list_name_to_id = {
        normalize_name(str(lst.get("name", ""))): str(lst["id"])
        for lst in shopping_lists
    }
    items_by_list: dict[str, list[dict[str, Any]]] = {}
    for lst in shopping_lists:
        list_id = str(lst["id"])
        items_by_list[list_id] = await og.get_list_items(list_id)
    index = build_item_index(shopping_lists, items_by_list)
    return list_name_to_id, index


async def run_sync_async(settings: SyncSettings) -> tuple[SyncStats, list[dict[str, Any]]]:
    if not settings.mealie_api_token:
        raise RuntimeError("MEALIE_API_TOKEN is required")
    if not settings.og_email or not settings.og_password:
        raise RuntimeError(
            "OURGROCERIES_EMAIL and OURGROCERIES_PASSWORD are required "
            "(set in mealie-og-sync/credentials.env or environment)",
        )

    config = load_sync_config(settings.config_path, settings.mealie_staging_list)
    mealie = MealieClient(settings.mealie_base_url, settings.mealie_api_token)
    list_id = mealie.find_staging_list_id(config.staging_list)
    mealie_items = mealie.fetch_unchecked_items(list_id)
    if not mealie_items:
        return SyncStats(), []

    og = OurGroceriesClient(settings.og_email, settings.og_password)
    await og.login()
    list_name_to_id, index = await _build_og_index(og)

    stats = SyncStats()
    synced_ids: list[str] = []

    for item in mealie_items:
        name = mealie_item_name(item)
        note = format_item_note(item.get("quantity"), item.get("unit"))
        try:
            route = resolve_item_route(
                name,
                index,
                list_name_to_id,
                default_list=config.default_list,
                overrides=config.overrides,
            )
            await _apply_route(og, route, note, stats, config.default_list, index, name)
            synced_ids.append(str(item["id"]))
        except Exception as exc:  # noqa: BLE001 — collect per-item errors for summary
            stats.errors.append(f"{name}: {exc}")

    if synced_ids and not settings.keep_staging:
        mealie.delete_items(synced_ids)

    return stats, mealie_items


async def _apply_route(
    og: OurGroceriesClient,
    route: RouteDecision,
    note: str,
    stats: SyncStats,
    default_list: str,
    index: dict[str, list],
    name: str,
) -> None:
    list_name = route.list_name or ""
    if route.action == "already_active":
        stats.bump(stats.skipped_active, list_name or "unknown")
        return
    if route.action == "uncross_item":
        if not route.list_id or not route.item_id:
            raise RuntimeError("uncross_item missing list or item id")
        await og.uncross_item(route.list_id, route.item_id)
        stats.bump(stats.uncrossed, list_name)
        return
    if route.action == "add_item":
        if not route.list_id or not route.value:
            raise RuntimeError("add_item missing list id or value")
        await og.add_item(route.list_id, route.value, note)
        stats.bump(stats.added, list_name)
        if (
            list_name
            and normalize_name(list_name) == normalize_name(default_list)
            and normalize_name(name) not in index
        ):
            stats.bump(stats.used_default, list_name)
        return
    raise RuntimeError(f"unsupported route action: {route.action}")


def render_html_summary(stats: SyncStats, item_count: int) -> str:
    def section(title: str, bucket: dict[str, int]) -> str:
        if not bucket:
            return ""
        rows = "".join(
            f"<li>{name}: {count}</li>" for name, count in sorted(bucket.items())
        )
        return f"<h2>{title}</h2><ul>{rows}</ul>"

    errors = ""
    if stats.errors:
        rows = "".join(f"<li>{err}</li>" for err in stats.errors)
        errors = f"<h2>Errors</h2><ul>{rows}</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Grocery sync</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.5rem; line-height: 1.4; }}
    h1 {{ font-size: 1.4rem; }}
    h2 {{ font-size: 1.1rem; margin-top: 1.25rem; }}
    ul {{ padding-left: 1.25rem; }}
  </style>
</head>
<body>
  <h1>Mealie → OurGroceries sync</h1>
  <p>Processed {item_count} staging item(s).</p>
  {section("Added", stats.added)}
  {section("Uncrossed", stats.uncrossed)}
  {section("Already on list", stats.skipped_active)}
  {section("New items (default store)", stats.used_default)}
  {errors}
</body>
</html>"""


def run_sync(settings: SyncSettings) -> tuple[SyncStats, list[dict[str, Any]], str]:
    stats, items = asyncio.run(run_sync_async(settings))
    html = render_html_summary(stats, len(items))
    return stats, items, html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Path to .env file")
    parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="Do not remove synced items from Mealie staging list",
    )
    parser.add_argument("--json", action="store_true", help="Print stats as JSON")
    args = parser.parse_args()
    settings = load_settings(args)
    stats, items, _html = run_sync(settings)
    if args.json:
        print(
            json.dumps(
                {
                    "item_count": len(items),
                    "added": stats.added,
                    "uncrossed": stats.uncrossed,
                    "skipped_active": stats.skipped_active,
                    "used_default": stats.used_default,
                    "errors": stats.errors,
                },
                indent=2,
            ),
        )
    else:
        print(render_html_summary(stats, len(items)))
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
