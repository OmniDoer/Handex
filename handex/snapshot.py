from __future__ import annotations

import json
from typing import Any

from . import __version__
from .context import redact_text


SNAPSHOT_VERSION = 1


def clean(value: Any) -> str:
    if value is None:
        return ""
    return redact_text(str(value))


def snapshot_project(project: dict[str, Any]) -> dict[str, Any]:
    settings = project.get("settings") if isinstance(project.get("settings"), dict) else {}
    return {
        "name": clean(project.get("name")),
        "description": clean(project.get("description")),
        "goal": clean(project.get("goal")),
        "project_state": clean(project.get("project_state")),
        "current_summary": clean(project.get("current_summary")),
        "prompt_template": clean(project.get("prompt_template")),
        "tool_protocol": clean(project.get("tool_protocol")),
        "workspace_path": clean(project.get("workspace_path")),
        "mode": "yolo" if str(settings.get("mode")).lower() == "yolo" else "safe",
        "created_at": project.get("created_at") or "",
        "updated_at": project.get("updated_at") or "",
    }


def snapshot_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": clean(summary.get("content")),
        "note": clean(summary.get("note")),
        "created_at": summary.get("created_at") or "",
    }


def snapshot_log(log: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": clean(log.get("event_type")),
        "mode": clean(log.get("mode")),
        "command_json": clean(log.get("command_json")),
        "final_command": clean(log.get("final_command")),
        "cwd": clean(log.get("cwd")),
        "exit_code": log.get("exit_code"),
        "stdout": clean(log.get("stdout")),
        "stderr": clean(log.get("stderr")),
        "result_prompt": clean(log.get("result_prompt")),
        "created_at": log.get("created_at") or "",
    }


def build_project_snapshot(
    project: dict[str, Any],
    summaries: list[dict[str, Any]],
    logs: list[dict[str, Any]],
    context_pack: str = "",
) -> dict[str, Any]:
    return {
        "schema": "handex.project_snapshot",
        "version": SNAPSHOT_VERSION,
        "handex_version": __version__,
        "project": snapshot_project(project),
        "summaries": [snapshot_summary(summary) for summary in summaries],
        "logs": [snapshot_log(log) for log in logs],
        "context_pack": clean(context_pack),
        "vault": {
            "included": False,
            "reason": "Handex snapshots never include vault secrets or encrypted vault items.",
        },
    }


def dumps_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def parse_snapshot(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Snapshot JSON is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Snapshot must be a JSON object")
    if data.get("schema") != "handex.project_snapshot":
        raise ValueError("Snapshot schema must be handex.project_snapshot")
    try:
        version = int(data.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported snapshot version: {data.get('version')}") from exc
    if version != SNAPSHOT_VERSION:
        raise ValueError(f"Unsupported snapshot version: {data.get('version')}")
    if not isinstance(data.get("project"), dict):
        raise ValueError("Snapshot project must be an object")
    if not isinstance(data.get("summaries", []), list):
        raise ValueError("Snapshot summaries must be a list")
    if not isinstance(data.get("logs", []), list):
        raise ValueError("Snapshot logs must be a list")
    return data


def imported_project_data(snapshot: dict[str, Any], workspace_path: str) -> dict[str, Any]:
    project = snapshot["project"]
    name = clean(project.get("name")) or "Imported Project"
    if not name.endswith(" (imported)"):
        name = f"{name} (imported)"
    return {
        "name": name,
        "description": clean(project.get("description")),
        "goal": clean(project.get("goal")),
        "project_state": clean(project.get("project_state")),
        "current_summary": clean(project.get("current_summary")),
        "prompt_template": clean(project.get("prompt_template")),
        "tool_protocol": clean(project.get("tool_protocol")),
        "workspace_path": workspace_path,
        "mode": "yolo" if str(project.get("mode")).lower() == "yolo" else "safe",
    }
