#!/usr/bin/env python3
"""Replace empty / CHANGE_ME values in an env file so `docker compose config` can run in CI."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PATH.env", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    out: list[str] = []
    for line in path.read_text().splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            stripped = val.strip().strip('"').strip("'")
            needs = (not stripped) or ("CHANGE_ME" in stripped) or ("<" in stripped)
            if needs:
                upper = key.strip().upper()
                if upper.endswith(("_IP", "_SSH")) or upper.endswith("_HOST") or "DOCKER_IP" in upper:
                    repl = "192.0.2.10"
                elif "CIDR" in upper or "SUBNET" in upper:
                    repl = "192.0.2.0/24"
                elif "DOMAIN" in upper or stripped.startswith("http"):
                    # rewrite angle-bracket site tokens inside URLs/domains
                    repl = (
                        stripped.replace("<lan-domain>", "home.example.com")
                        .replace("<domain>", "example.com")
                        .replace("<arrstack-host>", "192.0.2.10")
                        .replace("<lan-cidr>", "192.0.2.0/24")
                    )
                    if (not repl) or ("CHANGE_ME" in repl) or ("<" in repl):
                        repl = "example.com" if "DOMAIN" in upper else "https://example.com"
                elif "HOST" in upper:
                    repl = stripped.replace("<arrstack-host>", "192.0.2.10")
                    if "<" in repl or not repl or "CHANGE_ME" in repl:
                        repl = "192.0.2.10,localhost,127.0.0.1"
                else:
                    repl = "ci-placeholder"
                line = f"{key.strip()}={repl}"
        out.append(line)
    path.write_text("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
