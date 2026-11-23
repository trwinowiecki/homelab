#!/usr/bin/env python3
"""Emit one jsonline per unhealthy container (Docker Health.Log snapshot)."""
from __future__ import annotations

import json
import socket
import urllib.parse

SOCKET_PATH = "/var/run/docker.sock"
API = "v1.43"
HOST = "ct100-arrstack"


def _unchunk(data: bytes) -> bytes:
    out = bytearray()
    rest = data
    while rest:
        line, _, rest = rest.partition(b"\r\n")
        try:
            size = int(line.split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out.extend(rest[:size])
        rest = rest[size:]
        if rest.startswith(b"\r\n"):
            rest = rest[2:]
    return bytes(out)


def docker_get(path: str) -> object:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    req = f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
    sock.sendall(req.encode())
    buf = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    sock.close()
    header, _, body = buf.partition(b"\r\n\r\n")
    status = int(header.split(b" ", 2)[1])
    if status >= 400:
        raise RuntimeError(f"docker API {status} for {path}: {body[:200]!r}")
    if b"transfer-encoding: chunked" in header.lower():
        body = _unchunk(body)
    return json.loads(body.decode())


def main() -> None:
    filt = urllib.parse.quote('{"health":["unhealthy"]}')
    listing = docker_get(f"/{API}/containers/json?filters={filt}")
    if not isinstance(listing, list):
        return
    for item in listing:
        cid = item.get("Id")
        if not cid:
            continue
        inspect = docker_get(f"/{API}/containers/{cid}/json")
        name = (inspect.get("Name") or "").lstrip("/")
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        health = (inspect.get("State") or {}).get("Health") or {}
        rec = {
            "host": HOST,
            "stream": "health",
            "container": name,
            "compose_service": labels.get("com.docker.compose.service") or name,
            "image": (inspect.get("Config") or {}).get("Image") or "",
            "network_mode": (inspect.get("HostConfig") or {}).get("NetworkMode") or "",
            "Status": health.get("Status") or "",
            "FailingStreak": health.get("FailingStreak") or 0,
            "message": json.dumps(health, separators=(",", ":")),
        }
        print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
