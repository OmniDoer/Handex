from __future__ import annotations

import json
import textwrap
from typing import Any

from . import __version__


TOOL_SCHEMA = {
    "type": "object",
    "required": ["tool", "args"],
    "properties": {
        "tool": {
            "type": "string",
            "enum": [
                "shell",
                "python",
                "read_file",
                "write_file",
                "append_file",
                "replace_file",
                "delete_file",
                "list_files",
                "search_files",
                "grep",
                "git",
            ],
        },
        "args": {"type": "object"},
        "cwd": {"type": "string", "description": "Optional working directory. Relative paths resolve inside the workspace."},
        "mode": {"type": "string", "enum": ["safe", "yolo"], "description": "Optional requested execution mode."},
        "reason": {"type": "string"},
    },
}


DEFAULT_TOOL_PROTOCOL = """When you need Linux tools, output exactly one Tool Command JSON object.

Schema:
{
  "tool": "shell | python | read_file | write_file | append_file | replace_file | delete_file | list_files | search_files | grep | git",
  "args": {},
  "cwd": ".",
  "mode": "safe",
  "reason": "why this command is needed"
}

Examples:
{"tool":"shell","args":{"command":"pwd && ls -la"},"cwd":".","mode":"safe","reason":"inspect workspace"}
{"tool":"read_file","args":{"path":"README.md"},"mode":"safe","reason":"read project docs"}
{"tool":"write_file","args":{"path":"notes.txt","content":"hello\\n"},"mode":"safe","reason":"create a note"}
{"tool":"git","args":{"args":["status","--short"]},"cwd":".","mode":"safe","reason":"inspect git status"}

If no tool is needed, write normal analysis or a Summary. Do not invent API access. The user will copy your full reply back into Handex, and Handex will extract the JSON."""


DEFAULT_PROMPT_TEMPLATE = """You are the working LLM for a Handex project.

Handex is not an autonomous agent. The human copies messages between this web LLM and Handex. Handex can parse Tool Command JSON, ask the human to approve it, execute local Linux tools, and return Tool Result text back to you.

Project Background:
{project_background}

Current Goal:
{current_goal}

Current Summary:
{current_summary}

Project State:
{project_state}

Workspace:
{workspace_path}

Tool Protocol:
{tool_protocol}

Rules:
- Keep context compact and actionable.
- Ask for a Tool Command only when local execution is useful.
- Prefer safe, reversible actions unless YOLO is explicitly needed.
- Never claim a command was executed until Handex returns a Tool Result.
- When asked for Summary, return the latest project summary only.

Hand Loop:
1. You respond with analysis, a Tool Command JSON object, or a Summary.
2. The human copies your whole reply into Handex.
3. Handex extracts Tool Command JSON and shows it to the human.
4. The human approves, rejects, or edits before execution.
5. Handex returns Tool Result text.
6. Continue from that result."""


def compact(value: str, limit: int = 6000) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]..."


def build_start_prompt(project: dict[str, Any]) -> str:
    template = project.get("prompt_template") or DEFAULT_PROMPT_TEMPLATE
    tool_protocol = project.get("tool_protocol") or DEFAULT_TOOL_PROTOCOL
    values = {
        "project_background": compact(project.get("description") or "No description yet."),
        "current_goal": compact(project.get("goal") or "No current goal set."),
        "current_summary": compact(project.get("current_summary") or "No summary yet."),
        "project_state": compact(project.get("project_state") or "No project state recorded."),
        "workspace_path": project.get("workspace_path") or ".",
        "tool_protocol": compact(tool_protocol, 8000),
    }
    try:
        return template.format(**values).strip()
    except Exception:
        return DEFAULT_PROMPT_TEMPLATE.format(**values).strip()


def build_summary_prompt(project: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""
        Update the Handex project summary for this project.

        Project: {project.get("name")}
        Goal: {project.get("goal") or "No current goal set."}
        Existing Summary:
        {project.get("current_summary") or "No summary yet."}

        Return only the latest durable summary. Include active goal, important decisions, files or commands touched, current blockers, and next useful step. Do not include Markdown fences or commentary.
        """
    ).strip()


def build_correction_prompt(project: dict[str, Any], llm_reply: str, parse_errors: list[str]) -> str:
    error_text = "\n".join(f"- {item}" for item in parse_errors[:8]) or "- JSON could not be parsed."
    return textwrap.dedent(
        f"""
        Your previous reply could not be parsed by Handex as a Tool Command.

        Fix it by outputting only one valid JSON object. Do not output explanations. Do not use Markdown. Do not wrap it in a code block.

        Required JSON Schema:
        {json.dumps(TOOL_SCHEMA, ensure_ascii=False, indent=2)}

        Parse errors:
        {error_text}

        Project workspace: {project.get("workspace_path")}

        Previous reply:
        {compact(llm_reply, 5000)}
        """
    ).strip()


def build_tool_result_prompt(project: dict[str, Any], result: Any) -> str:
    stdout = compact(result.stdout or "", 12000)
    stderr = compact(result.stderr or "", 8000)
    command_json = json.dumps(result.command, ensure_ascii=False, indent=2)
    return textwrap.dedent(
        f"""
        Handex Tool Result

        Project: {project.get("name")}
        Tool: {result.tool}
        Mode: {result.mode}
        CWD: {result.cwd}
        Exit Code: {result.exit_code}

        Command JSON:
        {command_json}

        Final Command:
        {result.final_command}

        STDOUT:
        {stdout or "(empty)"}

        STDERR:
        {stderr or "(empty)"}

        Continue from this result. If more local work is needed, produce the next Tool Command JSON. If the task state changed durably, update your working summary.
        """
    ).strip()
