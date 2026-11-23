#!/usr/bin/env python3
"""Tests for TrueNAS snapshot-task reconcile and media purge filters."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "backup"))
sys.path.insert(0, str(ROOT / "scripts"))

from truenas_snapshots import (  # noqa: E402
    load_config,
    media_auto_snapshots,
    reconcile,
)


CONFIG_PATH = ROOT / "scripts" / "backup" / "truenas-snapshots.yaml"


def _task(**overrides: object) -> dict:
    base = {
        "id": 1,
        "dataset": "metrion/zinthos",
        "recursive": True,
        "lifetime_value": 2,
        "lifetime_unit": "DAY",
        "naming_schema": "auto-6h-%Y-%m-%d_%H-%M",
        "schedule": {
            "minute": "0",
            "hour": "0,6,12,18",
            "dom": "*",
            "month": "*",
            "dow": "*",
        },
        "enabled": True,
        "allow_empty": True,
        "exclude": [],
    }
    base.update(overrides)
    return base


class ConfigTests(unittest.TestCase):
    def test_import_from_tmp_path_does_not_need_repo_parents(self) -> None:
        src = ROOT / "scripts" / "backup" / "truenas_snapshots.py"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "homelab-truenas_snapshots.py"
            dest.write_text(src.read_text())
            spec = importlib.util.spec_from_file_location("truenas_snapshots_tmp", dest)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(hasattr(mod, "reconcile"))
            self.assertTrue(hasattr(mod, "load_config"))

    def test_repo_yaml_excludes_media(self) -> None:
        cfg = load_config(CONFIG_PATH)
        self.assertEqual(cfg["dataset"], "metrion/zinthos")
        self.assertTrue(cfg["recursive"])
        self.assertIn("metrion/zinthos/media", cfg["exclude"])
        schemas = {t["naming_schema"] for t in cfg["tasks"]}
        self.assertEqual(
            schemas,
            {
                "auto-6h-%Y-%m-%d_%H-%M",
                "auto-daily-%Y-%m-%d_%H-%M",
                "auto-weekly-%Y-%m-%d_%H-%M",
            },
        )


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(CONFIG_PATH)

    def test_creates_missing_tasks_with_exclude(self) -> None:
        ops = reconcile(self.cfg, [])
        self.assertEqual([op["action"] for op in ops], ["create", "create", "create"])
        for op in ops:
            self.assertEqual(op["payload"]["exclude"], ["metrion/zinthos/media"])
            self.assertTrue(op["payload"]["recursive"])
            self.assertEqual(op["payload"]["dataset"], "metrion/zinthos")

    def test_updates_existing_task_missing_exclude(self) -> None:
        existing = [
            _task(id=11, naming_schema="auto-6h-%Y-%m-%d_%H-%M", exclude=[]),
            _task(
                id=12,
                naming_schema="auto-daily-%Y-%m-%d_%H-%M",
                lifetime_value=14,
                schedule={
                    "minute": "0",
                    "hour": "4",
                    "dom": "*",
                    "month": "*",
                    "dow": "*",
                },
                exclude=["metrion/zinthos/media"],
            ),
            _task(
                id=13,
                naming_schema="auto-weekly-%Y-%m-%d_%H-%M",
                lifetime_value=8,
                lifetime_unit="WEEK",
                schedule={
                    "minute": "0",
                    "hour": "5",
                    "dom": "*",
                    "month": "*",
                    "dow": "sun",
                },
                exclude=["metrion/zinthos/media"],
            ),
        ]
        ops = reconcile(self.cfg, existing)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["action"], "update")
        self.assertEqual(ops[0]["id"], 11)
        self.assertEqual(ops[0]["payload"]["exclude"], ["metrion/zinthos/media"])

    def test_noop_when_already_matched(self) -> None:
        existing = []
        for idx, task in enumerate(self.cfg["tasks"], start=1):
            existing.append(
                _task(
                    id=idx,
                    naming_schema=task["naming_schema"],
                    lifetime_value=task["lifetime_value"],
                    lifetime_unit=task["lifetime_unit"],
                    schedule=task["schedule"],
                    exclude=["metrion/zinthos/media"],
                )
            )
        self.assertEqual(reconcile(self.cfg, existing), [])

    def test_deletes_undeclared_auto_task_on_same_dataset(self) -> None:
        existing = [
            _task(id=21, naming_schema="auto-hourly-%Y-%m-%d_%H-%M"),
            _task(id=22, naming_schema="auto-6h-%Y-%m-%d_%H-%M"),
            _task(
                id=23,
                naming_schema="auto-daily-%Y-%m-%d_%H-%M",
                lifetime_value=14,
            ),
            _task(
                id=24,
                naming_schema="auto-weekly-%Y-%m-%d_%H-%M",
                lifetime_value=8,
                lifetime_unit="WEEK",
                schedule={
                    "minute": "0",
                    "hour": "5",
                    "dom": "*",
                    "month": "*",
                    "dow": "sun",
                },
            ),
        ]
        ops = reconcile(self.cfg, existing)
        deletes = [op for op in ops if op["action"] == "delete"]
        self.assertEqual(deletes, [{"action": "delete", "id": 21}])

    def test_leaves_foreign_dataset_and_manual_schema(self) -> None:
        existing = [
            _task(id=31, dataset="boot-pool", naming_schema="auto-6h-%Y-%m-%d_%H-%M"),
            _task(id=32, naming_schema="manual-%Y-%m-%d"),
        ]
        ops = reconcile(self.cfg, existing)
        actions = [(op["action"], op.get("id")) for op in ops]
        self.assertNotIn(("delete", 31), actions)
        self.assertNotIn(("delete", 32), actions)
        self.assertEqual(sum(1 for op in ops if op["action"] == "create"), 3)


class PurgeTests(unittest.TestCase):
    def test_all_snapshots_on_media_dataset(self) -> None:
        names = [
            "metrion/zinthos/media@auto-daily-2026-10-01_04-00",
            "metrion/zinthos/media@manual-2026-08-17",
            "metrion/zinthos/config@auto-daily-2026-10-01_04-00",
            "metrion/zinthos@auto-weekly-2026-09-01_05-00",
            "metrion/zinthos/media/extra@auto-daily-2026-10-01_04-00",
        ]
        self.assertEqual(
            media_auto_snapshots(names, dataset="metrion/zinthos/media"),
            [
                "metrion/zinthos/media@auto-daily-2026-10-01_04-00",
                "metrion/zinthos/media@manual-2026-08-17",
                "metrion/zinthos/media/extra@auto-daily-2026-10-01_04-00",
            ],
        )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
