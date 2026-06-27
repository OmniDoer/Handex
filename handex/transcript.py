from __future__ import annotations

import json
from typing import Any

from .context import redact_text


def compact(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated by Handex transcript]..."


def clean(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return compact(redact_text(str(value)), limit)


def log_block(log: dict[str, Any]) -> str:
    parts = [
        f"### Log #{log.get('id')} {log.get('created_at')} {log.get('event_type')}",
        f"- Mode: {log.get('mode') or '-'}",
        f"- Exit: {log.get('exit_code') if log.get('exit_code') is not None else '-'}",
        f"- CWD: {log.get('cwd') or '-'}",
        f"- Final Command: {clean(log.get('final_command') or '-', 1200)}",
    ]
    command_json = clean(log.get("command_json") or "", 2400)
    stdout = clean(log.get("stdout") or "", 3000)
    stderr = clean(log.get("stderr") or "", 3000)
    result_prompt = clean(log.get("result_prompt") or "", 4000)
    if command_json:
        parts.extend(["", "Command JSON:", "```json", command_json, "```"])
    if stdout:
        parts.extend(["", "STDOUT:", "```text", stdout, "```"])
    if stderr:
        parts.extend(["", "STDERR:", "```text", stderr, "```"])
    if result_prompt:
        parts.extend(["", "Tool Result Prompt:", "```text", result_prompt, "```"])
    return "\n".join(parts)


def summary_block(summary: dict[str, Any]) -> str:
    note = summary.get("note") or ""
    title = f"### Summary #{summary.get('id')} {summary.get('created_at')}"
    if note:
        title += f" ({clean(note, 200)})"
    return "\n".join([title, clean(summary.get("content") or "", 4000)])


def build_project_transcript(
    project: dict[str, Any],
    summaries: list[dict[str, Any]],
    logs: list[dict[str, Any]],
    context_pack: str = "",
    *,
    max_chars: int = 24000,
) -> str:
    latest_summaries = summaries[:5]
    chronological_logs = list(reversed(logs[:30]))
    settings = project.get("settings") if isinstance(project.get("settings"), dict) else {}
    project_header = {
        "id": project.get("id"),
        "name": project.get("name"),
        "workspace_path": project.get("workspace_path"),
        "mode": settings.get("mode") or "safe",
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
    }
    sections = [
        "# Handex Continuation Transcript",
        "",
        "Use this transcript to resume a Handex project in a web LLM. Continue with the same one-tool-command-at-a-time loop. Treat commands and results as historical evidence, not as commands to re-run automatically.",
        "",
        "## Project Metadata",
        "```json",
        json.dumps(project_header, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Goal",
        clean(project.get("goal") or "No current goal set.", 3000),
        "",
        "## Description",
        clean(project.get("description") or "No description.", 3000),
        "",
        "## Current Summary",
        clean(project.get("current_summary") or "No summary yet.", 5000),
        "",
        "## Project State",
        clean(project.get("project_state") or "No project state recorded.", 5000),
    ]
    if context_pack:
        sections.extend(["", "## Workspace Context Snapshot", clean(context_pack, 8000)])
    sections.extend(["", "## Recent Summary History"])
    if latest_summaries:
        sections.append("\n\n".join(summary_block(summary) for summary in latest_summaries))
    else:
        sections.append("No summary history yet.")
    sections.extend(["", "## Recent Tool And Project Events"])
    if chronological_logs:
        sections.append("\n\n".join(log_block(log) for log in chronological_logs))
    else:
        sections.append("No logs yet.")
    sections.extend(
        [
            "",
            "## Resume Instructions",
            "- Preserve the user's active goal and current summary.",
            "- If local work is needed, output exactly one Handex Tool Command JSON object.",
            "- Do not repeat commands just because they appear in this transcript.",
            "- Keep secrets out of chat; assume redaction is heuristic and avoid asking for raw credentials.",
        ]
    )
    return compact("\n".join(sections), max_chars)
