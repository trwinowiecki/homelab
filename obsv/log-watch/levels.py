"""Map a log line to error/warn/info/unknown. Vector VRL in vector.yaml mirrors this."""
from __future__ import annotations

import json
import re

ERROR_TOKENS = frozenset(
    {
        "error",
        "err",
        "fatal",
        "crit",
        "critical",
        "panic",
        "emerg",
        "emergency",
        "alert",
        "ftl",
        "erro",
    }
)
WARN_TOKENS = frozenset({"warn", "warning", "wrn"})
INFO_TOKENS = frozenset(
    {"info", "information", "inf", "debug", "dbg", "trace", "trc", "notice"}
)

LOGFMT_RE = re.compile(
    r'(?i)(?:^|\s)(?:level|lvl|severity)=["\']?([A-Za-z]+)',
)
BRACKET_RE = re.compile(
    r"\[(error|err|fatal|warn|warning|info|debug|trace)\]",
    re.I,
)
GO_RE = re.compile(r"(?:^|\s)(ERR|WRN|INF|DBG|FTL|TRC)(?:\s|:)")
WORD_RE = re.compile(
    r"(?i)(?:^|[\s])(error|fatal|warning|warn|info)(?:$|[\s:\[\]])",
)
TRACEBACK_RE = re.compile(
    r"traceback \(most recent call last\)|unhandled exception",
    re.I,
)


def map_level(raw: str) -> str:
    token = (raw or "").strip().lower()
    if token in ERROR_TOKENS:
        return "error"
    if token in WARN_TOKENS:
        return "warn"
    if token in INFO_TOKENS:
        return "info"
    return "unknown"


def extract_level(message: str, stream: str = "stdout") -> str:
    msg = message or ""
    stripped = msg.strip()

    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("level", "lvl", "severity"):
                if key in parsed and parsed[key] is not None:
                    mapped = map_level(str(parsed[key]))
                    if mapped != "unknown":
                        return mapped

    match = LOGFMT_RE.search(msg)
    if match:
        mapped = map_level(match.group(1))
        if mapped != "unknown":
            return mapped

    match = BRACKET_RE.search(msg)
    if match:
        mapped = map_level(match.group(1))
        if mapped != "unknown":
            return mapped

    match = GO_RE.search(msg)
    if match:
        mapped = map_level(match.group(1))
        if mapped != "unknown":
            return mapped

    match = WORD_RE.search(msg)
    if match:
        mapped = map_level(match.group(1))
        if mapped != "unknown":
            return mapped

    if stream == "stderr" and TRACEBACK_RE.search(msg):
        return "error"
    return "unknown"
