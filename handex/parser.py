from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .prompts import TOOL_SCHEMA


CODE_FENCE_RE = re.compile(r"```(?:json|JSON|tool|Tool Command|tool-command)?\s*\n(.*?)```", re.DOTALL)
ALLOWED_TOOLS = set(TOOL_SCHEMA["properties"]["tool"]["enum"])


@dataclass
class ParseCandidate:
    command: dict[str, Any]
    source: str
    index: int
    raw_json: str
    warnings: list[str]


@dataclass
class ParseResult:
    candidates: list[ParseCandidate]
    errors: list[str]
    json_values_seen: int


def parse_llm_reply(text: str) -> ParseResult:
    sources: list[tuple[str, str]] = []
    for index, match in enumerate(CODE_FENCE_RE.finditer(text), start=1):
        sources.append((f"code block {index}", match.group(1).strip()))
    sources.append(("full reply", text))

    candidates: list[ParseCandidate] = []
    errors: list[str] = []
    seen: set[str] = set()
    json_values_seen = 0

    for source_name, source_text in sources:
        values, source_errors = extract_json_values(source_text)
        errors.extend(f"{source_name}: {error}" for error in source_errors)
        json_values_seen += len(values)
        for value, raw_json in values:
            for command in iter_tool_commands(value):
                normalized, warnings = normalize_command(command)
                fingerprint = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                candidates.append(
                    ParseCandidate(
                        command=normalized,
                        source=source_name,
                        index=len(candidates) + 1,
                        raw_json=json.dumps(normalized, ensure_ascii=False, indent=2),
                        warnings=warnings,
                    )
                )

    if not candidates and json_values_seen:
        errors.append("JSON was found, but no object matched the Tool Command schema.")
    if not candidates and not json_values_seen:
        errors.append("No JSON object or JSON code block was found.")
    return ParseResult(candidates=candidates, errors=dedupe(errors), json_values_seen=json_values_seen)


def extract_json_values(text: str) -> tuple[list[tuple[Any, str]], list[str]]:
    decoder = json.JSONDecoder()
    values: list[tuple[Any, str]] = []
    errors: list[str] = []
    positions = [idx for idx, char in enumerate(text) if char in "[{"]
    for pos in positions:
        try:
            value, end = decoder.raw_decode(text[pos:])
        except json.JSONDecodeError as exc:
            if len(errors) < 10:
                errors.append(f"JSON decode error near offset {pos}: {exc.msg}")
            continue
        raw = text[pos : pos + end].strip()
        values.append((value, raw))
    return dedupe_json_values(values), errors


def dedupe_json_values(values: list[tuple[Any, str]]) -> list[tuple[Any, str]]:
    seen: set[str] = set()
    result: list[tuple[Any, str]] = []
    for value, raw in values:
        try:
            key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except TypeError:
            key = raw
        if key in seen:
            continue
        seen.add(key)
        result.append((value, raw))
    return result


def iter_tool_commands(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from iter_tool_commands(item)
        return

    if not isinstance(value, dict):
        return

    if "tool" in value:
        yield value
        return

    for key in ("tool_command", "toolCommand", "command"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            yield from iter_tool_commands(nested)

    for key in ("tool_commands", "toolCommands", "commands"):
        nested_list = value.get(key)
        if isinstance(nested_list, list):
            yield from iter_tool_commands(nested_list)


def normalize_command(command: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    normalized = dict(command)
    tool = str(normalized.get("tool") or "").strip()
    normalized["tool"] = tool
    if tool not in ALLOWED_TOOLS:
        warnings.append(f"Unknown tool '{tool}'. Execution will reject it unless a plugin adds it.")

    args = normalized.get("args")
    if args is None:
        args = {}
        warnings.append("Missing args object; Handex created an empty args object.")
    elif not isinstance(args, dict):
        args = {"value": args}
        warnings.append("args was not an object; Handex wrapped it as args.value.")
    normalized["args"] = args

    for key in ("cwd", "mode", "reason"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = str(normalized[key])

    return normalized, warnings


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
