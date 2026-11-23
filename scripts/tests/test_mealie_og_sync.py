#!/usr/bin/env python3
"""Tests for Mealie → OurGroceries sync helpers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from og_resolver import (  # noqa: E402
    build_item_index,
    format_item_note,
    mealie_item_name,
    normalize_name,
    resolve_item_route,
)


class FormattingTests(unittest.TestCase):
    def test_format_item_note(self) -> None:
        self.assertEqual(format_item_note(2, "cups"), "2 cups")
        self.assertEqual(format_item_note(1.0, "lb"), "1 lb")
        self.assertEqual(format_item_note(None, "bunch"), "bunch")
        self.assertEqual(format_item_note("", ""), "")

    def test_mealie_item_name_prefers_food(self) -> None:
        item = {"food": {"name": "chicken"}, "display": "Chicken breast"}
        self.assertEqual(mealie_item_name(item), "chicken")


class ResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lists = [
            {"id": "meijer", "name": "Meijer", "listType": "SHOPPING"},
            {"id": "tj", "name": "Trader Joes", "listType": "SHOPPING"},
        ]
        self.items_by_list = {
            "meijer": [
                {"id": "1", "value": "chicken", "crossedOff": True, "crossedOffAt": 200},
                {"id": "2", "value": "milk", "crossedOff": False},
            ],
            "tj": [
                {"id": "3", "value": "yogurt", "crossedOff": False},
            ],
        }
        self.index = build_item_index(self.lists, self.items_by_list)
        self.list_name_to_id = {
            normalize_name("Meijer"): "meijer",
            normalize_name("Trader Joes"): "tj",
        }

    def test_resolve_known_item_to_history_list(self) -> None:
        route = resolve_item_route(
            "chicken",
            self.index,
            self.list_name_to_id,
            default_list="Meijer",
        )
        self.assertEqual(route.action, "uncross_item")
        self.assertEqual(route.list_id, "meijer")

    def test_resolve_active_item_skips(self) -> None:
        route = resolve_item_route(
            "milk",
            self.index,
            self.list_name_to_id,
            default_list="Meijer",
        )
        self.assertEqual(route.action, "already_active")

    def test_resolve_override_wins(self) -> None:
        route = resolve_item_route(
            "yogurt",
            self.index,
            self.list_name_to_id,
            default_list="Meijer",
            overrides={"yogurt": "Trader Joes"},
        )
        self.assertEqual(route.action, "already_active")
        self.assertEqual(route.list_id, "tj")

    def test_resolve_new_item_uses_default(self) -> None:
        route = resolve_item_route(
            "rice",
            self.index,
            self.list_name_to_id,
            default_list="Meijer",
        )
        self.assertEqual(route.action, "add_item")
        self.assertEqual(route.list_id, "meijer")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
