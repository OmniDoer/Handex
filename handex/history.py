from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import redact_text
from .db import list_logs, list_projects
from .prompts import redact_command_string, sanitize_command_for_prompt


def compact(value: str, limit: int) -> str:
    value = value or ""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[history field truncated by Handex]..."


def sanitize_command_json(value: str, *, max_chars: int = 12000) -> str:
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return compact(redact_command_string(value), max_chars)
    return compact(json.dumps(sanitize_command_for_prompt(parsed), ensure_ascii=False, indent=2), max_chars)


def sanitize_history_text(value: str, *, max_chars: int = 12000) -> str:
    return compact(redact_command_string(redact_text(value or "")), max_chars)


def sanitize_log_for_display(log: dict[str, Any], *, include_result_prompt: bool = True) -> dict[str, Any]:
    return {
        "id": log.get("id"),
        "project_id": log.get("project_id"),
        "event_type": sanitize_history_text(str(log.get("event_type") or ""), max_chars=600),
        "mode": sanitize_history_text(str(log.get("mode") or ""), max_chars=100),
        "command_json": sanitize_command_json(str(log.get("command_json") or "")),
        "final_command": sanitize_history_text(str(log.get("final_command") or ""), max_chars=2000),
        "cwd": sanitize_history_text(str(log.get("cwd") or ""), max_chars=1200),
        "exit_code": log.get("exit_code"),
        "stdout": sanitize_history_text(str(log.get("stdout") or ""), max_chars=12000),
        "stderr": sanitize_history_text(str(log.get("stderr") or ""), max_chars=12000),
        "result_prompt": sanitize_history_text(str(log.get("result_prompt") or ""), max_chars=16000) if include_result_prompt else "",
        "created_at": str(log.get("created_at") or ""),
    }


def recent_results_payload(logs: list[dict[str, Any]], *, include_result_prompt: bool = False) -> list[dict[str, Any]]:
    return [sanitize_log_for_display(log, include_result_prompt=include_result_prompt) for log in logs]


def project_logs_for_workspace(workspace: str | Path, *, limit: int = 10, include_result_prompt: bool = False) -> list[dict[str, Any]]:
    target = Path(workspace).expanduser().resolve()
    for project in list_projects():
        try:
            project_workspace = Path(str(project.get("workspace_path") or "")).expanduser().resolve()
        except OSError:
            continue
        if project_workspace == target:
            return recent_results_payload(list_logs(int(project["id"]), limit=limit), include_result_prompt=include_result_prompt)
    return []
