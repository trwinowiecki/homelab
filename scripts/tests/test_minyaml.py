#!/usr/bin/env python3
"""Self-check minyaml against arrstack bootstrap files."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from minyaml import MinYamlError, loads  # noqa: E402


NEGATIVE_CASES: tuple[tuple[str, str], ...] = (
    (
        "flow collection as mapping value",
        """indexers:
  - name: NZBGeek
    categories: [2000, 5000, 3000, 7000]
""",
    ),
    (
        "tab indentation",
        "key:\n\tvalue: bad\n",
    ),
)


def run_negative_cases() -> int:
    failed = 0
    for label, doc in NEGATIVE_CASES:
        try:
            loads(doc)
        except MinYamlError:
            print(f"OK negative: {label}")
            continue
        print(f"FAIL negative: {label} should raise MinYamlError")
        failed += 1
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate minyaml bootstrap YAML files.")
    parser.add_argument(
        "--no-parity",
        action="store_true",
        help="Skip PyYAML parity comparison (not recommended)",
    )
    args = parser.parse_args()

    boot_files = sorted((ROOT / "arrstack").rglob("bootstrap.yml"))
    if not boot_files:
        print("no bootstrap.yml files found")
        return 1

    if args.no_parity:
        yaml = None
    else:
        try:
            import yaml  # type: ignore
        except ImportError:
            print(
                "ERROR: PyYAML is required for parity checks. "
                "Install with: pip install -r requirements-dev.txt",
                file=sys.stderr,
            )
            return 1

    for path in boot_files:
        text = path.read_text()
        parsed = loads(text)
        if parsed is None:
            print(f"FAIL: {path} parsed to None")
            return 1
        if yaml is not None:
            expected = yaml.safe_load(text)
            if parsed != expected:
                print(f"FAIL: {path} differs from PyYAML")
                print("minyaml:", parsed)
                print("pyyaml:", expected)
                return 1
        print(f"OK: {path}")

    negative_failures = run_negative_cases()
    if negative_failures:
        return 1

    print(f"validated {len(boot_files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
