#!/usr/bin/env python3
"""Reconcile TrueNAS periodic snapshot tasks from truenas-snapshots.yaml."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

SCHEDULE_KEYS = ("minute", "hour", "dom", "month", "dow")
AUTO_PREFIX = "auto-"


def _minyaml_load(path: str | Path) -> Any:
    """Load YAML using repo minyaml. Not used on TrueNAS (--config-json)."""
    here = Path(__file__).resolve()
    candidates = []
    if here.parent.name == "backup":
        candidates.append(here.parent.parent)
    candidates.append(here.parent)
    for scripts in candidates:
        if (scripts / "minyaml.py").is_file():
            sys.path.insert(0, str(scripts))
            from minyaml import load as minyaml_load  # noqa: PLC0415

            return minyaml_load(str(path))
    raise ImportError(
        "minyaml.py not found; pass --config-json when running on TrueNAS"
    )


def load_config(path: str | Path) -> dict[str, Any]:
    data = _minyaml_load(path)
    if not isinstance(data, dict):
        raise ValueError(f"snapshot config must be a mapping: {path}")
    exclude = data.get("exclude") or []
    if isinstance(exclude, str):
        exclude = [exclude]
    tasks = data.get("tasks") or []
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    return {
        "dataset": str(data["dataset"]),
        "recursive": bool(data.get("recursive", True)),
        "exclude": [str(item) for item in exclude],
        "tasks": tasks,
    }


def _schedule(raw: Any) -> dict[str, str]:
    src = raw if isinstance(raw, dict) else {}
    return {key: str(src.get(key, "*")) for key in SCHEDULE_KEYS}


def desired_payload(cfg: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": cfg["dataset"],
        "recursive": bool(cfg["recursive"]),
        "exclude": list(cfg["exclude"]),
        "lifetime_value": int(task["lifetime_value"]),
        "lifetime_unit": str(task["lifetime_unit"]),
        "naming_schema": str(task["naming_schema"]),
        "schedule": _schedule(task.get("schedule")),
        "enabled": bool(task.get("enabled", True)),
        "allow_empty": bool(task.get("allow_empty", True)),
    }


def existing_payload(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": str(task.get("dataset") or ""),
        "recursive": bool(task.get("recursive")),
        "exclude": [str(item) for item in (task.get("exclude") or [])],
        "lifetime_value": int(task.get("lifetime_value") or 0),
        "lifetime_unit": str(task.get("lifetime_unit") or ""),
        "naming_schema": str(task.get("naming_schema") or ""),
        "schedule": _schedule(task.get("schedule")),
        "enabled": bool(task.get("enabled", True)),
        "allow_empty": bool(task.get("allow_empty", True)),
    }


def reconcile(cfg: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    desired = [desired_payload(cfg, task) for task in cfg["tasks"]]
    desired_keys = {(item["dataset"], item["naming_schema"]) for item in desired}
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for task in existing:
        key = (str(task.get("dataset") or ""), str(task.get("naming_schema") or ""))
        by_key.setdefault(key, task)

    ops: list[dict[str, Any]] = []
    for payload in desired:
        key = (payload["dataset"], payload["naming_schema"])
        current = by_key.get(key)
        if current is None:
            ops.append({"action": "create", "payload": payload})
            continue
        if existing_payload(current) != payload:
            ops.append(
                {
                    "action": "update",
                    "id": current["id"],
                    "payload": payload,
                }
            )

    for task in existing:
        dataset = str(task.get("dataset") or "")
        schema = str(task.get("naming_schema") or "")
        if dataset != cfg["dataset"]:
            continue
        if not schema.startswith(AUTO_PREFIX):
            continue
        if (dataset, schema) in desired_keys:
            continue
        ops.append({"action": "delete", "id": task["id"]})
    return ops


def media_auto_snapshots(
    names: list[str],
    *,
    dataset: str = "metrion/zinthos/media",
) -> list[str]:
    """All snapshots of the media dataset (auto-* and leftover manuals)."""
    matched: list[str] = []
    for name in names:
        if "@" not in name:
            continue
        ds, _snap = name.split("@", 1)
        if ds == dataset or ds.startswith(f"{dataset}/"):
            matched.append(name)
    return matched


def midclt(method: str, payload: Any = None) -> Any:
    cmd = ["midclt", "call", method]
    if isinstance(payload, list):
        for item in payload:
            cmd.append(json.dumps(item))
    elif payload is not None:
        cmd.append(json.dumps(payload))
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out) if out.strip() else None


def apply_ops(
    ops: list[dict[str, Any]],
    *,
    call: Callable[..., Any] = midclt,
) -> None:
    for op in ops:
        action = op["action"]
        if action == "create":
            call("pool.snapshottask.create", op["payload"])
            print(f"created: {op['payload']['naming_schema']}")
        elif action == "update":
            call("pool.snapshottask.update", [op["id"], op["payload"]])
            print(f"updated: {op['payload']['naming_schema']} id={op['id']}")
        elif action == "delete":
            call("pool.snapshottask.delete", op["id"])
            print(f"deleted: id={op['id']}")
        else:
            raise ValueError(f"unknown action {action}")


def list_snapshots(dataset: str) -> list[str]:
    out = subprocess.check_output(
        ["zfs", "list", "-H", "-t", "snapshot", "-o", "name", "-r", dataset],
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def destroy_snapshot(name: str, *, dry_run: bool) -> None:
    cmd = ["zfs", "destroy"]
    if dry_run:
        cmd.append("-nv")
    cmd.append(name)
    subprocess.check_call(cmd)


def run_apply(cfg: dict[str, Any]) -> None:
    existing = midclt("pool.snapshottask.query") or []
    if not isinstance(existing, list):
        raise SystemExit("pool.snapshottask.query did not return a list")
    ops = reconcile(cfg, existing)
    if not ops:
        print("snapshot tasks already match config")
    else:
        apply_ops(ops)
    remaining = midclt("pool.snapshottask.query") or []
    for task in remaining:
        print(
            task.get("id"),
            task.get("naming_schema"),
            task.get("lifetime_value"),
            task.get("lifetime_unit"),
            task.get("exclude"),
        )


def run_purge(dataset: str, *, dry_run: bool, yes: bool) -> int:
    names = media_auto_snapshots(list_snapshots(dataset), dataset=dataset)
    if not names:
        print(f"no {dataset}@auto-* snapshots")
        return 0
    print("snapshots to destroy:")
    for name in names:
        print(f"  {name}")
        destroy_snapshot(name, dry_run=True)
    if dry_run and not yes:
        print("dry-run only; pass --yes to destroy")
        return 0
    if not yes:
        print("refusing to destroy without --yes")
        return 1
    for name in names:
        destroy_snapshot(name, dry_run=False)
        print(f"destroyed: {name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile TrueNAS snapshot tasks.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("truenas-snapshots.yaml"),
    )
    parser.add_argument("--config-json", type=Path)
    parser.add_argument("--dump-json", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--purge-media-snapshots", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--media-dataset", default="metrion/zinthos/media")
    args = parser.parse_args()

    if args.config_json:
        cfg = json.loads(args.config_json.read_text())
    else:
        cfg = load_config(args.config)

    if args.dump_json:
        json.dump(cfg, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.apply:
        run_apply(cfg)
        return 0
    if args.purge_media_snapshots:
        return run_purge(
            args.media_dataset,
            dry_run=args.dry_run or not args.yes,
            yes=args.yes,
        )
    parser.error("specify --dump-json, --apply, or --purge-media-snapshots")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
