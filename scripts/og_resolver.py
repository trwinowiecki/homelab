"""Resolve OurGroceries item names to store lists using shopping history."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

ActionType = Literal["add_item", "uncross_item", "already_active", "choose_list"]


@dataclass(frozen=True)
class ListItemRef:
    list_id: str
    list_name: str
    item_id: str
    status: Literal["active", "crossed_off"]
    crossed_off_at: int | None = None


@dataclass(frozen=True)
class RouteDecision:
    action: ActionType
    list_id: str | None = None
    list_name: str | None = None
    item_id: str | None = None
    value: str | None = None


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFD", value)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower().strip()
    return re.sub(r"\s+", " ", text)


def format_item_note(quantity: float | int | str | None, unit: str | None) -> str:
    parts: list[str] = []
    if quantity not in (None, ""):
        qty = quantity
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        parts.append(str(qty))
    if unit:
        parts.append(str(unit).strip())
    return " ".join(parts).strip()


def mealie_item_name(item: dict[str, Any]) -> str:
    food = item.get("food")
    if isinstance(food, dict):
        name = food.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    for key in ("display", "label", "note"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "item"


def _parse_crossed_off_at(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def build_item_index(
    lists: list[dict[str, Any]],
    items_by_list: dict[str, list[dict[str, Any]]],
) -> dict[str, list[ListItemRef]]:
    """Map normalized item name -> occurrences across shopping lists."""
    index: dict[str, list[ListItemRef]] = {}
    names_by_id = {
        str(lst.get("id", "")): str(lst.get("name", ""))
        for lst in lists
        if lst.get("listType") == "SHOPPING" and lst.get("id")
    }

    for list_id, items in items_by_list.items():
        list_name = names_by_id.get(list_id, "")
        for item in items:
            value = item.get("value")
            item_id = item.get("id")
            if not isinstance(value, str) or not value.strip() or not item_id:
                continue
            crossed = bool(item.get("crossedOff", False))
            status: Literal["active", "crossed_off"] = (
                "crossed_off" if crossed else "active"
            )
            ref = ListItemRef(
                list_id=list_id,
                list_name=list_name,
                item_id=str(item_id),
                status=status,
                crossed_off_at=_parse_crossed_off_at(item.get("crossedOffAt")),
            )
            key = normalize_name(value)
            index.setdefault(key, []).append(ref)
    return index


def _best_list_for_occurrences(occurrences: list[ListItemRef]) -> ListItemRef:
    active = [occ for occ in occurrences if occ.status == "active"]
    if active:
        return active[0]

    crossed = [occ for occ in occurrences if occ.status == "crossed_off"]
    crossed.sort(key=lambda occ: occ.crossed_off_at or 0, reverse=True)
    if crossed:
        return crossed[0]

    counts: dict[str, int] = {}
    for occ in occurrences:
        counts[occ.list_id] = counts.get(occ.list_id, 0) + 1
    best_list_id = max(counts, key=counts.get)
    return next(occ for occ in occurrences if occ.list_id == best_list_id)


def _lookup_override(
    query: str,
    overrides: dict[str, str],
    list_name_to_id: dict[str, str],
) -> tuple[str, str] | None:
    target_name = overrides.get(normalize_name(query))
    if not target_name:
        return None
    list_id = list_name_to_id.get(normalize_name(target_name))
    if not list_id:
        raise ValueError(f"override list not found in OurGroceries: {target_name}")
    return list_id, target_name


def _route_for_occurrences(
    query: str,
    occurrences: list[ListItemRef],
) -> RouteDecision | None:
    if not occurrences:
        return None

    active = [occ for occ in occurrences if occ.status == "active"]
    if active:
        pick = _best_list_for_occurrences(active)
        return RouteDecision(
            action="already_active",
            list_id=pick.list_id,
            list_name=pick.list_name,
            item_id=pick.item_id,
            value=query,
        )

    pick = _best_list_for_occurrences(occurrences)
    if pick.status == "crossed_off":
        return RouteDecision(
            action="uncross_item",
            list_id=pick.list_id,
            list_name=pick.list_name,
            item_id=pick.item_id,
            value=query,
        )

    return RouteDecision(
        action="add_item",
        list_id=pick.list_id,
        list_name=pick.list_name,
        value=query,
    )


def resolve_item_route(
    query: str,
    index: dict[str, list[ListItemRef]],
    list_name_to_id: dict[str, str],
    *,
    default_list: str,
    overrides: dict[str, str] | None = None,
) -> RouteDecision:
    """Pick target list and action for one item name."""
    overrides = overrides or {}
    override = _lookup_override(query, overrides, list_name_to_id)
    normalized = normalize_name(query)
    occurrences = index.get(normalized)
    if not occurrences:
        for key, refs in index.items():
            if key.startswith(normalized) or normalized.startswith(key):
                occurrences = refs
                break

    if override:
        list_id, list_name = override
        scoped = [occ for occ in (occurrences or []) if occ.list_id == list_id]
        routed = _route_for_occurrences(query, scoped)
        if routed:
            return routed
        return RouteDecision(
            action="add_item",
            list_id=list_id,
            list_name=list_name,
            value=query,
        )

    if not occurrences:
        list_id = list_name_to_id.get(normalize_name(default_list))
        if not list_id:
            raise ValueError(f"default_list not found in OurGroceries: {default_list}")
        return RouteDecision(
            action="add_item",
            list_id=list_id,
            list_name=default_list,
            value=query,
        )

    routed = _route_for_occurrences(query, occurrences)
    if routed:
        return routed

    list_id = list_name_to_id.get(normalize_name(default_list))
    if not list_id:
        raise ValueError(f"default_list not found in OurGroceries: {default_list}")
    return RouteDecision(
        action="add_item",
        list_id=list_id,
        list_name=default_list,
        value=query,
    )
