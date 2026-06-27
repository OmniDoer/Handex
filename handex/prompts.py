from __future__ import annotations

import json
import re
import textwrap
from typing import Any

from . import __version__
from .bootstrap import redacted_repo_url
from .capabilities import skill_pack_prompt
from .context import build_context_pack, redact_text
from .plugins import plugin_catalog_prompt
from .uploads import upload_inventory_prompt


TOOL_NAMES = [
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
    "git_bootstrap",
    "apply_patch",
    "list_skills",
    "read_skill",
    "skill_pack",
    "list_vault_credentials",
    "vault_list",
    "vault_run",
    "capability_report",
    "context_pack",
    "list_uploads",
    "plugin_list",
    "plugin_run",
]


TOOL_SCHEMA = {
    "type": "object",
    "required": ["tool", "args"],
    "properties": {
        "tool": {
            "type": "string",
            "enum": TOOL_NAMES,
        },
        "args": {"type": "object"},
        "cwd": {"type": "string", "description": "Optional working directory. Relative paths resolve inside the workspace."},
        "mode": {"type": "string", "enum": ["safe", "yolo"], "description": "Optional requested execution mode."},
        "reason": {"type": "string"},
    },
}


SENSITIVE_COMMAND_KEY_RE = re.compile(r"(?i)(password|passwd|passphrase|secret|token|api[_ -]?key|private[_ -]?key)")
URL_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")


def redact_command_string(value: str) -> str:
    return URL_USERINFO_RE.sub(r"\1[REDACTED]@", redact_text(value))


def sanitize_command_for_prompt(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in {"repo_url", "url"} and isinstance(item, str):
                sanitized[key_text] = redacted_repo_url(item)
            elif SENSITIVE_COMMAND_KEY_RE.search(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = sanitize_command_for_prompt(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_command_for_prompt(item) for item in value]
    if isinstance(value, str):
        return redact_command_string(value)
    return value


DEFAULT_TOOL_PROTOCOL = """When you need Linux tools, output exactly one Tool Command JSON object.

Schema:
{
  "tool": "shell | python | read_file | write_file | append_file | replace_file | delete_file | list_files | search_files | grep | git | git_bootstrap | apply_patch | list_skills | read_skill | skill_pack | list_vault_credentials | vault_list | vault_run | capability_report | context_pack | list_uploads | plugin_list | plugin_run",
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
{"tool":"git_bootstrap","args":{"repo_url":"https://github.com/org/repo.git","branch":"main","depth":1},"mode":"safe","reason":"clone the target repository into an empty workspace"}
{"tool":"apply_patch","args":{"patch":"diff --git a/file.txt b/file.txt\\n--- a/file.txt\\n+++ b/file.txt\\n@@ -1 +1 @@\\n-old\\n+new\\n"},"cwd":".","mode":"safe","reason":"apply a reviewed unified diff"}
{"tool":"list_skills","args":{},"mode":"safe","reason":"inspect available Handex skills"}
{"tool":"read_skill","args":{"skill_id":"root1:example-skill"},"mode":"safe","reason":"load relevant skill instructions"}
{"tool":"list_vault_credentials","args":{},"mode":"safe","reason":"inspect available credential metadata without secrets"}
{"tool":"vault_list","args":{},"mode":"safe","reason":"inspect Handex local vault metadata"}
{"tool":"vault_run","args":{"credential_id":"handex:1","env":"HANDEX_SECRET","command":"printf ready"},"cwd":".","mode":"safe","reason":"run a command with a reviewed secret environment variable"}
{"tool":"capability_report","args":{},"mode":"safe","reason":"inspect configured Handex skill roots and providers"}
{"tool":"context_pack","args":{},"cwd":".","mode":"safe","reason":"inspect Git status, AGENTS.md, manifests, and file tree"}
{"tool":"list_uploads","args":{},"mode":"safe","reason":"inspect user-uploaded workspace files"}
{"tool":"plugin_list","args":{},"mode":"safe","reason":"inspect configured Handex command plugins"}
{"tool":"plugin_run","args":{"plugin_id":"example","input":{}},"cwd":".","mode":"safe","reason":"run a configured command plugin"}

Vault rules:
- list_vault_credentials returns metadata only: credential id, masked username, origin, kind, name, source, host.
- vault_list returns metadata only for Handex's built-in encrypted vault.
- vault_run injects one selected Handex vault secret into an environment variable for the approved command; never print or echo that variable.
- Never ask Handex to print passwords, tokens, private keys, or decrypted secrets.
- For credentialed git or GitHub work, request a shell command that uses the locally configured Vault-backed CLI and let the human review it.

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


AGENT_FALLBACK_TEMPLATE = """You are acting as a Codex/OmniDoer-style Single-Step coding agent through Handex, a manual Human-in-the-Loop workspace for web LLMs and local tools.

There is no required LLM API, model vendor, autonomous browser, MCP dependency, Codex dependency, or OmniDoer dependency in this loop. The human copies your entire reply into Handex. Handex parses Tool Command JSON, shows the exact command and execution mode to the human, executes only after human approval, then returns a Tool Result Prompt.

Your job is to feel like an interactive coding agent, but with one manual boundary: every tool use is one reviewed copy/paste step. Preserve the Codex/OmniDoer working style: inspect first, make small patches, verify, summarize durable state, and keep secrets out of transcript text.

Each reply should normally contain either:
- concise reasoning and exactly one next Tool Command JSON object, or
- a durable Summary when the user asks to update state, or
- a direct answer when no local action is needed.

Project:
- Name: {project_name}
- Workspace: {workspace_path}
- Goal: {current_goal}
- Summary: {current_summary}
- State: {project_state}

Operating rules:
- Read the codebase before making implementation claims.
- Use small, reviewable Tool Commands.
- Produce at most one Tool Command JSON object per turn unless the human explicitly asks for alternatives.
- Prefer Safe Mode. Request YOLO Mode only when it is necessary and explain why.
- Never say a command ran until Handex returns Tool Result.
- Keep secrets out of chat. Vault access is metadata-only unless the human explicitly runs a local Vault-backed command after review.
- Use Handex skills by listing configured skill roots first, then reading only the relevant SKILL.md instructions.
- Use git_bootstrap to clone a repository only when the workspace is empty and the URL has no embedded credentials.
- Use context_pack for Codex-style workspace orientation when Git status, AGENTS.md, manifests, or the file tree may matter.
- Use list_uploads and read_file for user-uploaded files under .handex_uploads/.
- Use plugin_list before plugin_run; only run configured plugins that directly apply to the task.
- Use apply_patch for focused code edits when a unified diff is clearer than write_file/replace_file.
- After durable progress, update the Summary.
- Do not explain Handex basics back to the user unless asked; behave like a familiar terminal coding agent whose tool calls are manually ferried.

Agent-compatible tools available through Handex:
{tool_protocol}

Configured skill catalog snapshot:
{skill_pack}

Configured plugin catalog snapshot:
{plugin_pack}

Uploaded workspace files:
{upload_pack}

Initial workspace context snapshot:
{workspace_context}

Start by identifying the next concrete step. If you need local context, output one Tool Command JSON object."""


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


def build_agent_fallback_prompt(project: dict[str, Any]) -> str:
    try:
        workspace_context = build_context_pack(project.get("workspace_path") or ".", max_chars=10000)
    except Exception as exc:
        workspace_context = f"(Workspace context unavailable: {type(exc).__name__}: {exc})"
    return AGENT_FALLBACK_TEMPLATE.format(
        project_name=project.get("name") or "Untitled",
        workspace_path=project.get("workspace_path") or ".",
        current_goal=compact(project.get("goal") or "No current goal set."),
        current_summary=compact(project.get("current_summary") or "No summary yet."),
        project_state=compact(project.get("project_state") or "No project state recorded."),
        tool_protocol=compact(project.get("tool_protocol") or DEFAULT_TOOL_PROTOCOL, 8000),
        skill_pack=compact(skill_pack_prompt(), 10000),
        plugin_pack=compact(plugin_catalog_prompt(), 8000),
        upload_pack=compact(upload_inventory_prompt(project.get("workspace_path") or "."), 6000),
        workspace_context=compact(workspace_context, 10000),
    ).strip()



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
    final_command = compact(redact_command_string(result.final_command or ""), 4000)
    command_json = json.dumps(sanitize_command_for_prompt(result.command), ensure_ascii=False, indent=2)
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
        {final_command}

        STDOUT:
        {stdout or "(empty)"}

        STDERR:
        {stderr or "(empty)"}

        Continue from this result. If more local work is needed, produce the next Tool Command JSON. If the task state changed durably, update your working summary.
        """
    ).strip()
