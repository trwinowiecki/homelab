"""Minimal YAML parser for arrstack bootstrap files (stdlib only).

Supports nested mappings, block sequences, quoted/unquoted scalars, and # comments.
Does not support anchors, merge keys, flow collections, or multi-line scalars.
"""
from __future__ import annotations

import re
from typing import Any


class MinYamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text in ("null", "Null", "NULL", "~"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    if text.startswith("[") or text.startswith("{"):
        raise MinYamlError(f"flow collections not supported: {text!r}")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_sequence_item(line: str) -> bool:
    stripped = _strip_comment(line).strip()
    return stripped.startswith("- ")


def _sequence_value(line: str) -> str:
    return _strip_comment(line).strip()[2:].strip()


def _split_key_value(line: str) -> tuple[str, str | None]:
    stripped = _strip_comment(line).strip()
    if not stripped or stripped.startswith("#"):
        return "", None
    if stripped.startswith("- "):
        return "", None
    match = re.match(r"^([^:]+):\s*(.*)$", stripped)
    if not match:
        raise MinYamlError(f"invalid line: {line!r}")
    key = match.group(1).strip()
    value = match.group(2)
    return key, value


def _parse_block(lines: list[str], start: int, base_indent: int) -> tuple[Any, int]:
    if start >= len(lines):
        return {}, start

    first = lines[start]
    if _is_sequence_item(first):
        return _parse_sequence(lines, start, base_indent)

    result: dict[str, Any] = {}
    i = start
    while i < len(lines):
        line = lines[i]
        if not _strip_comment(line).strip():
            i += 1
            continue
        indent = _indent(line)
        if indent < base_indent:
            break
        if indent > base_indent:
            raise MinYamlError(f"unexpected indent at line {i + 1}: {line!r}")

        key, value = _split_key_value(line)
        if not key:
            i += 1
            continue

        if value is None or value == "":
            child, i = _parse_block(lines, i + 1, base_indent + 2)
            result[key] = child
        elif value == "|" or value == ">":
            raise MinYamlError(f"multi-line scalars not supported at line {i + 1}")
        else:
            result[key] = _parse_scalar(value)
            i += 1
    return result, i


def _parse_mapping_item(lines: list[str], start: int, item_indent: int) -> tuple[dict[str, Any], int]:
    """Parse a list item that is a mapping (possibly multi-line)."""
    line = lines[start]
    rest = _sequence_value(line)
    result: dict[str, Any] = {}

    if rest and ":" in rest and not rest.startswith('"') and not rest.startswith("'"):
        key, value = _split_key_value(f"{rest}")
        if key:
            if value is None or value == "":
                child, next_i = _parse_block(lines, start + 1, item_indent + 2)
                result[key] = child
                return result, next_i
            result[key] = _parse_scalar(value)
            i = start + 1
            while i < len(lines):
                next_line = lines[i]
                if not _strip_comment(next_line).strip():
                    i += 1
                    continue
                indent = _indent(next_line)
                if indent <= item_indent:
                    break
                if _is_sequence_item(next_line):
                    break
                nkey, nvalue = _split_key_value(next_line)
                if not nkey:
                    break
                if nvalue is None or nvalue == "":
                    child, i = _parse_block(lines, i + 1, indent + 2)
                    result[nkey] = child
                else:
                    result[nkey] = _parse_scalar(nvalue)
                    i += 1
            return result, i

    if rest == "":
        child, i = _parse_block(lines, start + 1, item_indent + 2)
        return child if isinstance(child, dict) else {"": child}, i

    return {_parse_scalar(rest): None}, start + 1


def _parse_sequence(lines: list[str], start: int, base_indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not _strip_comment(line).strip():
            i += 1
            continue
        indent = _indent(line)
        if indent < base_indent:
            break
        if indent > base_indent or not _is_sequence_item(line):
            break

        rest = _sequence_value(line)
        if rest == "" or (rest and ":" in rest and not rest.startswith('"') and not rest.startswith("'")):
            item, i = _parse_mapping_item(lines, i, indent)
            items.append(item)
            continue

        if rest.startswith("{") or rest.startswith("["):
            raise MinYamlError(f"flow collections not supported at line {i + 1}")
        items.append(_parse_scalar(rest))
        i += 1
    return items, i


def _reject_tab_indentation(lines: list[str]) -> None:
    for lineno, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        leading = line[: len(line) - len(line.lstrip())]
        if "\t" in leading:
            raise MinYamlError(f"tab indentation not supported at line {lineno}")


def loads(text: str) -> Any:
    lines = text.splitlines()
    _reject_tab_indentation(lines)
    i = 0
    while i < len(lines) and not _strip_comment(lines[i]).strip():
        i += 1
    if i >= len(lines):
        return {}

    first = lines[i]
    if _is_sequence_item(first):
        result, _ = _parse_sequence(lines, i, _indent(first))
        return result
    result, _ = _parse_block(lines, i, _indent(first))
    return result


def load(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return loads(handle.read())
