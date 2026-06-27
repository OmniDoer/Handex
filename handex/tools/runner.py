from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..bootstrap import BootstrapError, bootstrap_workspace_from_git, redacted_repo_url
from ..capabilities import configured_capability_report, list_skills, list_vault_metadata, read_skill, read_skill_file, search_capabilities, skill_pack_prompt
from ..config import settings
from ..context import build_context_pack, redact_text as redact_context_text
from ..db import get_project_plan, save_project_plan
from ..files import FileAccessError, file_info_payload, resolve_workspace_file
from ..history import project_logs_for_workspace
from ..images import ImageError, image_info_payload, resolve_workspace_image
from ..jobs import get_job_display, list_project_job_displays, project_id_for_workspace, start_background_shell, stop_job
from ..plans import normalize_plan_payload, plan_form_json
from ..plugins import find_plugin, list_plugins, plugin_argv
from ..prompts import TOOL_SCHEMA, redact_command_string, sanitize_command_for_prompt
from ..uploads import list_workspace_uploads
from ..vault import decrypt_item_secret, metadata_for_tools


SAFE_SHELL_BLOCKLIST = [
    r"\brm\s+-[^;\n]*[rf][^;\n]*(/|\*)",
    r"\bmkfs\b",
    r"\bdd\s+.*\bof=/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bsystemctl\b",
    r"\bservice\b",
    r"\bapt(-get)?\b",
    r"\byum\b",
    r"\bdnf\b",
    r"\bpacman\b",
    r"\bapk\b",
    r"\bchmod\s+-R\s+.*\s/",
    r"\bchown\s+-R\s+.*\s/",
    r"(curl|wget)[^|;]*\|\s*(sh|bash)",
]
SAFE_BATCH_TOOLS = {
    "read_file",
    "list_files",
    "search_files",
    "grep",
    "list_skills",
    "read_skill",
    "read_skill_file",
    "skill_pack",
    "list_vault_credentials",
    "vault_list",
    "omnidoer_credential_list",
    "capability_report",
    "capability_search",
    "context_pack",
    "list_uploads",
    "download_file",
    "view_image",
    "recent_results",
    "plan_status",
    "job_status",
    "omnidoer_request_status",
    "omnidoer_task_list",
    "omnidoer_chat_messages",
    "omnidoer_chat_next",
    "omnidoer_doctor",
    "omnidoer_control_status",
    "omnidoer_control_devices",
    "omnidoer_control_sessions",
    "omnidoer_control_tunnel_info",
    "omnidoer_control_security_status",
    "omnidoer_control_sync_status",
    "omnidoer_audit_tail",
    "omnidoer_audit_verify",
    "omnidoer_policy_test",
    "omnidoer_telegram_status",
    "omnidoer_console_dry_run",
    "omnidoer_upgrade_dry_run",
    "omnidoer_mcp_self_test",
    "plugin_list",
}
SAFE_BATCH_GIT_COMMANDS = {"status", "log", "show", "diff", "rev-parse", "ls-files", "grep", "describe", "blame"}
SAFE_OMNIDOER_GIT_COMMANDS = {"ls-remote"}
SAFE_GITHUB_API_METHODS = {"GET"}
SENSITIVE_OUTPUT_KEY_RE = re.compile(r"(?i)(password|passwd|passphrase|secret|token|api[_ -]?key|private[_ -]?key|ciphertext|totp)")
PUBLIC_OUTPUT_KEY_ALLOWLIST = {"secret_exposed_to_model", "secret_fields_allowed"}
MAX_BATCH_COMMANDS = 12


@dataclass
class ToolResult:
    tool: str
    command: dict[str, Any]
    mode: str
    cwd: str
    final_command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class ToolPreview:
    tool: str
    mode: str
    cwd: str
    final_command: str
    warnings: list[str]
    diff_preview: str = ""


class ToolError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[[dict[str, Any], Path, str], ToolResult]] = {}

    def register(self, name: str, runner: Callable[[dict[str, Any], Path, str], ToolResult]) -> None:
        self._tools[name] = runner

    def names(self) -> list[str]:
        return sorted(self._tools)

    def run(self, command: dict[str, Any], workspace: str, mode: str) -> ToolResult:
        normalized_mode = normalize_mode(mode or command.get("mode") or "safe")
        tool = str(command.get("tool") or "")
        if tool not in self._tools:
            raise ToolError(f"Unsupported tool: {tool}")
        workspace_path = Path(workspace).expanduser().resolve()
        return self._tools[tool](command, workspace_path, normalized_mode)

    def preview(self, command: dict[str, Any], workspace: str, mode: str) -> ToolPreview:
        normalized_mode = normalize_mode(mode or command.get("mode") or "safe")
        tool = str(command.get("tool") or "")
        workspace_path = Path(workspace).expanduser().resolve()
        cwd = resolve_cwd(command, workspace_path, normalized_mode)
        final_command = preview_command(command)
        warnings = validate_preview(command, workspace_path, cwd, normalized_mode)
        diff_preview = preview_diff(command, workspace_path, cwd, normalized_mode)
        return ToolPreview(tool=tool, mode=normalized_mode, cwd=str(cwd), final_command=final_command, warnings=warnings, diff_preview=diff_preview)


def normalize_mode(mode: str) -> str:
    return "yolo" if str(mode).lower() == "yolo" else "safe"


def command_args(command: dict[str, Any]) -> dict[str, Any]:
    args = command.get("args")
    return args if isinstance(args, dict) else {}


def resolve_cwd(command: dict[str, Any], workspace: Path, mode: str) -> Path:
    args = command_args(command)
    raw = command.get("cwd") or args.get("cwd") or "."
    cwd = Path(str(raw)).expanduser()
    if not cwd.is_absolute():
        cwd = workspace / cwd
    cwd = cwd.resolve()
    if mode == "safe" and not is_relative_to(cwd, workspace):
        raise ToolError(f"Safe Mode cwd must stay inside workspace: {cwd}")
    if not cwd.exists():
        raise ToolError(f"Working directory does not exist: {cwd}")
    if not cwd.is_dir():
        raise ToolError(f"Working directory is not a directory: {cwd}")
    return cwd


def resolve_path(path_value: Any, workspace: Path, mode: str) -> Path:
    if not path_value:
        raise ToolError("path is required")
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if mode == "safe" and not is_relative_to(path, workspace):
        raise ToolError(f"Safe Mode path must stay inside workspace: {path}")
    return path


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def clamp_output(value: str) -> str:
    if len(value) <= settings.max_output_chars:
        return value
    return value[: settings.max_output_chars] + "\n...[output truncated by Handex]..."


def timeout_for(command: dict[str, Any], mode: str) -> int:
    args = command_args(command)
    try:
        timeout = int(args.get("timeout") or command.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60
    upper = 900 if mode == "yolo" else 180
    return max(1, min(timeout, upper))


def validate_safe_shell(command_text: str) -> list[str]:
    warnings: list[str] = []
    for pattern in SAFE_SHELL_BLOCKLIST:
        if re.search(pattern, command_text, flags=re.IGNORECASE):
            warnings.append(f"Safe Mode blocks shell pattern: {pattern}")
    return warnings


def validate_preview(command: dict[str, Any], workspace: Path, cwd: Path, mode: str) -> list[str]:
    warnings: list[str] = []
    tool = str(command.get("tool") or "")
    if tool not in TOOL_SCHEMA["properties"]["tool"]["enum"]:
        warnings.append(f"Unsupported built-in tool: {tool}")
    if mode == "safe" and not is_relative_to(cwd, workspace):
        warnings.append("Safe Mode requires cwd inside workspace.")
    if tool in {"shell", "background_shell"} and mode == "safe":
        warnings.extend(validate_safe_shell(str(command_args(command).get("command") or command.get("command") or "")))
    if tool == "tool_batch":
        try:
            validate_batch_children(command, mode)
        except ToolError as exc:
            warnings.append(str(exc))
    return warnings


def display_path(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def read_preview_text(path: Path, encoding: str) -> str:
    if not path.exists():
        return ""
    if path.is_dir():
        return f"[Handex preview: directory {path}]\n"
    return path.read_text(encoding=encoding, errors="replace")


def unified_text_diff(old: str, new: str, fromfile: str, tofile: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
        lineterm="\n",
    )
    text = "".join(diff)
    return clamp_output(text)


def preview_file_diff(command: dict[str, Any], workspace: Path, mode: str) -> str:
    args = command_args(command)
    tool = str(command.get("tool") or "")
    path = resolve_path(args.get("path"), workspace, mode)
    encoding = str(args.get("encoding") or "utf-8")
    rel = display_path(path, workspace)
    if tool == "delete_file" and path.is_dir():
        return f"Directory deletion preview: {rel}/\n"
    try:
        old_content = read_preview_text(path, encoding)
    except OSError as exc:
        return f"Diff preview unavailable: {type(exc).__name__}: {exc}\n"
    if tool == "write_file":
        new_content = str(args.get("content") or "")
    elif tool == "append_file":
        new_content = old_content + str(args.get("content") or "")
    elif tool == "replace_file":
        old = str(args.get("old") if args.get("old") is not None else args.get("search") or "")
        new = str(args.get("new") if args.get("new") is not None else args.get("replace") or "")
        if old == "":
            return "Diff preview unavailable: replace_file args.old is required.\n"
        if old not in old_content:
            return "Diff preview unavailable: search text was not found.\n"
        count_arg = args.get("count")
        count = -1 if count_arg in (None, "") else int(count_arg)
        new_content = old_content.replace(old, new, count)
    elif tool == "delete_file":
        if not path.exists():
            return "Diff preview unavailable: path does not exist.\n"
        new_content = ""
    else:
        return ""
    return unified_text_diff(old_content, new_content, f"a/{rel}", f"b/{rel}")


def preview_diff(command: dict[str, Any], workspace: Path, cwd: Path, mode: str) -> str:
    tool = str(command.get("tool") or "")
    if tool in {"write_file", "append_file", "replace_file", "delete_file"}:
        return preview_file_diff(command, workspace, mode)
    if tool == "apply_patch":
        args = command_args(command)
        patch = str(args.get("patch") or args.get("diff") or "")
        if not patch.strip():
            return ""
        validate_patch_paths(patch, mode)
        return clamp_output(patch)
    return ""


def preview_command(command: dict[str, Any]) -> str:
    args = command_args(command)
    tool = str(command.get("tool") or "")
    if tool == "shell":
        return str(args.get("command") or command.get("command") or "")
    if tool == "background_shell":
        return str(args.get("command") or command.get("command") or "")
    if tool == "python":
        code = str(args.get("code") or command.get("code") or "")
        return f"{sys.executable} -c {shlex.quote(code[:500])}"
    if tool == "git":
        git_args = git_command_args(command)
        return "git " + " ".join(shlex.quote(item) for item in git_args)
    if tool == "omnidoer_git":
        git_args = git_command_args(command)
        return "omnidoer git run " + " ".join(shlex.quote(item) for item in git_args)
    if tool == "omnidoer_github_api":
        method = str(args.get("method") or "GET").upper()
        return f"omnidoer github api {method} {args.get('path') or ''}"
    if tool == "omnidoer_credential_request":
        return f"omnidoer cred request {args.get('origin') or ''}"
    if tool == "omnidoer_credential_list":
        return "omnidoer cred list"
    if tool == "omnidoer_credential_save_request":
        return f"omnidoer cred save-request {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_vault_unlock":
        return "omnidoer vault unlock"
    if tool == "omnidoer_request_status":
        return f"omnidoer control requests {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_request_wait":
        return f"omnidoer control wait-request {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_request_deny":
        return f"omnidoer control deny {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_task_submit":
        task = str(args.get("task") or args.get("text") or "")
        return f"omnidoer control submit-task {shlex.quote(redact_context_text(task)[:80])}"
    if tool == "omnidoer_task_list":
        return f"omnidoer control tasks {args.get('task_id') or args.get('id') or args.get('status') or ''}"
    if tool == "omnidoer_task_complete":
        return f"omnidoer control complete-task {args.get('task_id') or args.get('id') or ''}"
    if tool == "omnidoer_task_cancel":
        return f"omnidoer control cancel-task {args.get('task_id') or args.get('id') or ''}"
    if tool == "omnidoer_chat_messages":
        return f"omnidoer control chat-messages {args.get('message_id') or args.get('id') or args.get('role') or ''}"
    if tool == "omnidoer_chat_next":
        return "omnidoer control chat-next --no-claim"
    if tool == "omnidoer_chat_send":
        message = str(args.get("message") or args.get("text") or "")
        return f"omnidoer control chat-send {shlex.quote(redact_context_text(message)[:80])}"
    if tool == "omnidoer_chat_reply":
        message = str(args.get("message") or args.get("text") or "")
        return f"omnidoer control chat-reply {args.get('reply_to') or args.get('reply_to_message_id') or ''} {shlex.quote(redact_context_text(message)[:80])}"
    if tool == "omnidoer_chat_log_user":
        message = str(args.get("message") or args.get("text") or "")
        return f"omnidoer control chat-log-user {shlex.quote(redact_context_text(message)[:80])}"
    if tool == "omnidoer_chat_start":
        return f"omnidoer control chat-start {args.get('reply_to') or args.get('reply_to_message_id') or args.get('source') or ''}"
    if tool == "omnidoer_chat_delta":
        delta = str(args.get("delta") or args.get("text") or "")
        return f"omnidoer control chat-delta {args.get('message_id') or args.get('id') or ''} {shlex.quote(redact_context_text(delta)[:80])}"
    if tool == "omnidoer_chat_complete":
        return f"omnidoer control chat-complete {args.get('message_id') or args.get('id') or ''}"
    if tool == "omnidoer_chat_record":
        text = str(args.get("text") or args.get("message") or "")
        return f"omnidoer control chat-record {args.get('record_type') or args.get('type') or ''} {shlex.quote(redact_context_text(text)[:80])}"
    if tool == "omnidoer_doctor":
        return "omnidoer doctor"
    if tool == "omnidoer_control_status":
        return "omnidoer control status"
    if tool == "omnidoer_control_devices":
        return "omnidoer control devices"
    if tool == "omnidoer_control_sessions":
        return "omnidoer control sessions"
    if tool == "omnidoer_control_tunnel_info":
        return "omnidoer control tunnel-info"
    if tool == "omnidoer_control_security_status":
        return "omnidoer control security-status"
    if tool == "omnidoer_control_sync_status":
        return f"omnidoer control sync-status {args.get('thread_id') or ''}"
    if tool == "omnidoer_control_revoke_device":
        return f"omnidoer control revoke-device {args.get('device_id') or args.get('id') or ''}"
    if tool == "omnidoer_control_revoke_session":
        return f"omnidoer control revoke-session {args.get('session_id') or args.get('id') or ''}"
    if tool == "omnidoer_control_enable_sync":
        return f"omnidoer control enable-sync {args.get('thread_id') or ''}"
    if tool == "omnidoer_request_challenge":
        return f"omnidoer control challenge {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_request_takeover":
        return f"omnidoer control takeover {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_request_release":
        return f"omnidoer control release {args.get('request_id') or args.get('id') or ''}"
    if tool == "omnidoer_audit_tail":
        return "omnidoer audit tail"
    if tool == "omnidoer_audit_verify":
        return "omnidoer audit verify"
    if tool == "omnidoer_policy_test":
        return "omnidoer policy test"
    if tool == "omnidoer_telegram_status":
        return "omnidoer telegram status"
    if tool == "omnidoer_console_dry_run":
        raw_codex_args = args.get("codex_args")
        if isinstance(raw_codex_args, list):
            codex_args = [str(item) for item in raw_codex_args]
        elif isinstance(raw_codex_args, str):
            try:
                codex_args = shlex.split(raw_codex_args)
            except ValueError:
                codex_args = [raw_codex_args]
        else:
            codex_args = []
        return " ".join(["omnidoer", "console", "--dry-run", *(shlex.quote(item) for item in codex_args)])
    if tool == "omnidoer_upgrade_dry_run":
        upgrade_args = ["omnidoer", "upgrade", "--dry-run"]
        if args.get("branch"):
            upgrade_args.extend(["--branch", str(args.get("branch"))])
        if args.get("install_dir"):
            upgrade_args.extend(["--install-dir", str(args.get("install_dir"))])
        return " ".join(shlex.quote(item) for item in upgrade_args)
    if tool == "omnidoer_mcp_self_test":
        return "omnidoer mcp serve --self-test"
    if tool == "omnidoer_browser_open":
        return f"omnidoer browser open {args.get('url') or ''}"
    if tool == "git_bootstrap":
        return f"git clone {redacted_repo_url(str(args.get('repo_url') or args.get('url') or ''))}"
    if tool == "apply_patch":
        patch = str(args.get("patch") or args.get("diff") or "")
        if is_codex_patch(patch):
            return "codex apply_patch --check && codex apply_patch"
        return "git apply --check && git apply"
    if tool in {"read_file", "write_file", "append_file", "replace_file", "delete_file", "list_files", "search_files", "grep"}:
        return f"{tool} {args.get('path') or args.get('root') or '.'}"
    if tool == "tool_batch":
        try:
            count = len(batch_child_commands(command))
        except ToolError:
            count = 0
        return f"tool_batch {count} command(s)"
    if tool == "read_skill":
        return f"read_skill {args.get('skill_id') or args.get('name') or ''}"
    if tool == "read_skill_file":
        return f"read_skill_file {args.get('skill_id') or args.get('name') or ''} {args.get('path') or ''}"
    if tool in {"list_skills", "skill_pack", "list_vault_credentials", "vault_list", "capability_report", "context_pack", "list_uploads", "recent_results", "job_status", "plugin_list", "plan_status"}:
        return tool
    if tool == "capability_search":
        return f"capability_search {args.get('query') or ''}"
    if tool == "download_file":
        return f"download_file {args.get('path') or ''}"
    if tool == "view_image":
        return f"view_image {args.get('path') or ''}"
    if tool == "update_plan":
        args = command_args(command)
        plan = args.get("plan") or args.get("items") or []
        count = len(plan) if isinstance(plan, list) else 0
        return f"update_plan {count} item(s)"
    if tool == "job_stop":
        return f"job_stop {args.get('job_id') or args.get('id') or ''}"
    if tool == "plugin_run":
        return f"plugin_run {args.get('plugin_id') or args.get('id') or args.get('name') or ''}"
    if tool == "vault_run":
        return str(args.get("command") or "")
    return tool


def subprocess_result(
    *,
    command: dict[str, Any],
    tool: str,
    mode: str,
    cwd: Path,
    final_command: str,
    argv: list[str] | None = None,
    shell: bool = False,
    extra_env: dict[str, str] | None = None,
    redact_values: list[str] | None = None,
    input_text: str | None = None,
    inherit_env: bool = True,
    clamp_result: bool = True,
) -> ToolResult:
    env = None
    if extra_env is not None:
        if inherit_env:
            env = os.environ.copy()
        else:
            env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}}
        env.update(extra_env)
    try:
        completed = subprocess.run(
            final_command if shell else argv,
            shell=shell,
            cwd=str(cwd),
            text=True,
            input=input_text,
            capture_output=True,
            timeout=timeout_for(command, mode),
            executable="/bin/bash" if shell else None,
            env=env,
        )
        stdout = redact_text(completed.stdout or "", redact_values or [])
        stderr = redact_text(completed.stderr or "", redact_values or [])
        if clamp_result:
            stdout = clamp_output(stdout)
            stderr = clamp_output(stderr)
        return ToolResult(
            tool=tool,
            command=command,
            mode=mode,
            cwd=str(cwd),
            final_command=final_command,
            exit_code=int(completed.returncode),
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        stdout = redact_text(stdout, redact_values or [])
        stderr = redact_text((stderr or "") + "\nHandex timeout expired.", redact_values or [])
        if clamp_result:
            stdout = clamp_output(stdout)
            stderr = clamp_output(stderr)
        return ToolResult(
            tool=tool,
            command=command,
            mode=mode,
            cwd=str(cwd),
            final_command=final_command,
            exit_code=124,
            stdout=stdout,
            stderr=stderr,
        )


def redact_text(text: str, values: list[str]) -> str:
    redacted = text
    for value in values:
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def run_shell(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    command_text = str(command_args(command).get("command") or command.get("command") or "")
    if not command_text.strip():
        raise ToolError("shell args.command is required")
    if mode == "safe":
        warnings = validate_safe_shell(command_text)
        if warnings:
            raise ToolError("; ".join(warnings))
    return subprocess_result(command=command, tool="shell", mode=mode, cwd=cwd, final_command=command_text, shell=True)


def run_background_shell(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    command_text = str(command_args(command).get("command") or command.get("command") or "")
    if not command_text.strip():
        raise ToolError("background_shell args.command is required")
    if mode == "safe":
        warnings = validate_safe_shell(command_text)
        if warnings:
            raise ToolError("; ".join(warnings))
    job = start_background_shell(
        workspace=workspace,
        cwd=cwd,
        mode=mode,
        command=command,
        command_text=command_text,
        final_command=command_text,
    )
    output = json.dumps(job, ensure_ascii=False, indent=2)
    return ToolResult("background_shell", command, mode, str(cwd), command_text, 0, output + "\n", "")


def run_python(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    code = str(command_args(command).get("code") or command.get("code") or "")
    if not code.strip():
        raise ToolError("python args.code is required")
    argv = [sys.executable, "-c", code]
    return subprocess_result(command=command, tool="python", mode=mode, cwd=cwd, final_command=preview_command(command), argv=argv)


def git_command_args(command: dict[str, Any]) -> list[str]:
    args = command_args(command)
    raw_args = args.get("args")
    raw_command = args.get("command") or command.get("command")
    if isinstance(raw_args, list):
        return [str(item) for item in raw_args]
    if isinstance(raw_command, str):
        return shlex.split(raw_command)
    raise ToolError("git args.args list or args.command string is required")


def run_git(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = git_command_args(command)
    if mode == "safe" and args and args[0] in {"clean", "reset"}:
        raise ToolError("Safe Mode blocks destructive git clean/reset. Use YOLO Mode after review.")
    argv = ["git", *args]
    final_command = "git " + " ".join(shlex.quote(item) for item in args)
    return subprocess_result(command=command, tool="git", mode=mode, cwd=cwd, final_command=final_command, argv=argv)


def omnidoer_vault_args() -> list[str]:
    vault_path = str(getattr(settings, "omnidoer_vault_path", "") or "")
    passphrase_file = str(getattr(settings, "omnidoer_vault_passphrase_file", "") or "")
    if not vault_path or not passphrase_file:
        raise ToolError("OmniDoer vault bridge is not configured. Set HANDEX_OMNIDOER_VAULT_PATH and HANDEX_OMNIDOER_VAULT_PASSPHRASE_FILE.")
    return ["--vault", vault_path, "--passphrase-file", passphrase_file]


def omnidoer_vault_path_arg() -> list[str]:
    vault_path = str(getattr(settings, "omnidoer_vault_path", "") or "")
    if not vault_path:
        raise ToolError("OmniDoer vault path is not configured. Set HANDEX_OMNIDOER_VAULT_PATH.")
    return ["--vault", vault_path]


def optional_credential_args(args: dict[str, Any]) -> list[str]:
    credential_id = str(args.get("credential_id") or args.get("id") or "")
    return ["--credential-id", credential_id] if credential_id else []


def omnidoer_base_argv() -> list[str]:
    return [str(getattr(settings, "omnidoer_bin", "") or "omnidoer")]


def public_kv_lines(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    notes: list[str] = []
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                payload[key] = value.strip()
                continue
        if line.strip():
            notes.append(line.strip())
    if notes:
        payload["notes"] = notes
    return payload


def run_omnidoer_subprocess(
    command: dict[str, Any],
    *,
    tool: str,
    mode: str,
    cwd: Path,
    argv: list[str],
    clamp_result: bool = True,
) -> ToolResult:
    final_command = " ".join(shlex.quote(item) for item in argv)
    return subprocess_result(command=command, tool=tool, mode=mode, cwd=cwd, final_command=final_command, argv=argv, extra_env={}, inherit_env=False, clamp_result=clamp_result)


def parse_public_json_or_kv(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return public_kv_lines(stripped)


def request_id_arg(args: dict[str, Any], tool: str) -> str:
    request_id = str(args.get("request_id") or args.get("id") or "")
    if not request_id:
        raise ToolError(f"{tool} args.request_id is required")
    if not re.fullmatch(r"req_[A-Za-z0-9_:-]+", request_id):
        raise ToolError(f"{tool} args.request_id must look like req_<id>")
    return request_id


def task_id_arg(args: dict[str, Any], tool: str) -> str:
    task_id = str(args.get("task_id") or args.get("id") or "")
    if not task_id:
        raise ToolError(f"{tool} args.task_id is required")
    if not re.fullmatch(r"task_[A-Za-z0-9_:-]+", task_id):
        raise ToolError(f"{tool} args.task_id must look like task_<id>")
    return task_id


def find_task_id(value: Any) -> str:
    if isinstance(value, dict):
        direct = str(value.get("task_id") or value.get("id") or "")
        if re.fullmatch(r"task_[A-Za-z0-9_:-]+", direct):
            return direct
        for item in value.values():
            found = find_task_id(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_task_id(item)
            if found:
                return found
    elif isinstance(value, str):
        match = re.search(r"\btask_[A-Za-z0-9_:-]+\b", value)
        if match:
            return match.group(0)
    return ""


def message_id_arg(args: dict[str, Any], tool: str) -> str:
    message_id = str(args.get("message_id") or args.get("id") or "")
    if not message_id:
        raise ToolError(f"{tool} args.message_id is required")
    if not re.fullmatch(r"msg_[A-Za-z0-9_:-]+", message_id):
        raise ToolError(f"{tool} args.message_id must look like msg_<id>")
    return message_id


def optional_message_id(args: dict[str, Any], *names: str) -> str:
    for name in names:
        value = str(args.get(name) or "")
        if value:
            if not re.fullmatch(r"msg_[A-Za-z0-9_:-]+", value):
                raise ToolError(f"{name} must look like msg_<id>")
            return value
    return ""


def find_message_id(value: Any) -> str:
    if isinstance(value, dict):
        direct = str(value.get("message_id") or value.get("id") or "")
        if re.fullmatch(r"msg_[A-Za-z0-9_:-]+", direct):
            return direct
        for item in value.values():
            found = find_message_id(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_message_id(item)
            if found:
                return found
    elif isinstance(value, str):
        match = re.search(r"\bmsg_[A-Za-z0-9_:-]+\b", value)
        if match:
            return match.group(0)
    return ""


def plain_token_arg(args: dict[str, Any], key: str, tool: str, *, required: bool = False) -> str:
    value = str(args.get(key) or "")
    if required and not value:
        raise ToolError(f"{tool} args.{key} is required")
    if value and not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ToolError(f"{tool} args.{key} must contain only letters, numbers, dot, underscore, colon, or dash")
    return value


def safe_cli_value_arg(args: dict[str, Any], key: str, tool: str, *, required: bool = False, max_len: int = 256) -> str:
    value = str(args.get(key) or "").strip()
    if required and not value:
        raise ToolError(f"{tool} args.{key} is required")
    if not value:
        return ""
    if len(value) > max_len or any(ord(char) < 32 for char in value) or re.search(r"\s", value):
        raise ToolError(f"{tool} args.{key} contains invalid whitespace/control characters or is too long")
    if value.startswith("-"):
        raise ToolError(f"{tool} args.{key} must not start with '-'")
    return value


def string_list_arg(args: dict[str, Any], key: str, tool: str) -> list[str]:
    value = args.get(key)
    if value in (None, ""):
        return []
    if isinstance(value, str):
        items = shlex.split(value)
    elif isinstance(value, list):
        items = [str(item) for item in value]
    else:
        raise ToolError(f"{tool} args.{key} must be a string or list of strings")
    for item in items:
        if "\x00" in item or any(ord(char) < 32 for char in item):
            raise ToolError(f"{tool} args.{key} contains an invalid argument")
    return items


def require_yolo(mode: str, tool: str, reason: str) -> None:
    if mode == "safe":
        raise ToolError(f"Safe Mode blocks {tool}: {reason}. Use YOLO Mode after review.")


def public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        payload: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text not in PUBLIC_OUTPUT_KEY_ALLOWLIST and SENSITIVE_OUTPUT_KEY_RE.search(key_text):
                payload[key_text] = "[REDACTED]"
            else:
                payload[key_text] = public_payload(item)
        return payload
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    if isinstance(value, str):
        return redact_context_text(value)
    return value


def public_json(value: Any) -> str:
    return json.dumps(public_payload(value), ensure_ascii=False, indent=2)


def validate_control_origin(origin: str, mode: str) -> None:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("origin must include an http(s) scheme and host")
    if mode == "safe" and parsed.scheme != "https":
        raise ToolError("Safe Mode credential requests require an HTTPS origin. Use YOLO Mode after review for local HTTP origins.")


def validate_browser_url(url: str, mode: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("omnidoer_browser_open args.url must include an http(s) scheme and host")
    if mode == "safe" and parsed.scheme != "https":
        raise ToolError("Safe Mode omnidoer_browser_open requires an HTTPS URL. Use YOLO Mode after review for local HTTP URLs.")


def run_omnidoer_public_command(command: dict[str, Any], workspace: Path, mode: str, tool: str, argv: list[str]) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    result = run_omnidoer_subprocess(command, tool=tool, mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    output = public_json(payload)
    return ToolResult(tool, command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_credential_request(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    origin = str(args.get("origin") or "")
    if not origin:
        raise ToolError("omnidoer_credential_request args.origin is required")
    validate_control_origin(origin, mode)
    argv = [*omnidoer_base_argv(), "cred", "request", "--origin", origin]
    for arg_key, cli_name in (
        ("top_level_url", "--top-level-url"),
        ("summary", "--summary"),
        ("risk_level", "--risk-level"),
        ("ttl", "--ttl"),
        ("username_label", "--username-label"),
        ("password_label", "--password-label"),
        ("totp_label", "--totp-label"),
    ):
        value = args.get(arg_key)
        if value not in (None, ""):
            argv.extend([cli_name, str(value)])
    if bool(args.get("no_save_to_vault")) or args.get("save_to_vault") is False:
        argv.append("--no-save-to-vault")
    if bool(args.get("no_totp_field")):
        argv.append("--no-totp-field")
    result = run_omnidoer_subprocess(command, tool="omnidoer_credential_request", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload["secret_exposed_to_model"] = False
    if result.exit_code == 0:
        payload["next_tools"] = ["omnidoer_request_status", "omnidoer_request_wait", "omnidoer_credential_save_request", "omnidoer_request_deny"]
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("omnidoer_credential_request", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_credential_list(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    result = run_omnidoer_subprocess(command, tool="omnidoer_credential_list", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "cred", "list", *omnidoer_vault_path_arg()])
    payload = parse_public_json_or_kv(result.stdout)
    output = public_json(payload)
    return ToolResult("omnidoer_credential_list", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_vault_unlock(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    vault_args = omnidoer_vault_args()
    argv = [*omnidoer_base_argv(), "vault", "unlock", "--path", vault_args[1], "--passphrase-file", vault_args[3]]
    result = run_omnidoer_subprocess(command, tool="omnidoer_vault_unlock", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload["secret_exposed_to_model"] = False
    output = public_json(payload)
    return ToolResult("omnidoer_vault_unlock", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_credential_save_request(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    request_id = request_id_arg(args, "omnidoer_credential_save_request")
    argv = [*omnidoer_base_argv(), "cred", "save-request", request_id, *omnidoer_vault_args()]
    if bool(args.get("wait")):
        argv.append("--wait")
        argv.extend(["--wait-timeout", str(args.get("wait_timeout") or args.get("timeout") or "30s")])
    if bool(args.get("create_vault")):
        argv.append("--create-vault")
    result = run_omnidoer_subprocess(command, tool="omnidoer_credential_save_request", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload["secret_exposed_to_model"] = False
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("omnidoer_credential_save_request", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_request_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    request_id = str(args.get("request_id") or args.get("id") or "")
    result = run_omnidoer_subprocess(command, tool="omnidoer_request_status", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "requests"])
    payload = parse_public_json_or_kv(result.stdout)
    if request_id:
        if not re.fullmatch(r"req_[A-Za-z0-9_:-]+", request_id):
            raise ToolError("omnidoer_request_status args.request_id must look like req_<id>")
        if isinstance(payload, list):
            matches = [item for item in payload if isinstance(item, dict) and item.get("request_id") == request_id]
            payload = matches[0] if matches else {"status": "not_found", "request_id": request_id, "secret_exposed_to_model": False}
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("omnidoer_request_status", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_request_wait(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    request_id = request_id_arg(args, "omnidoer_request_wait")
    wait_timeout = str(args.get("wait_timeout") or args.get("timeout") or "30s")
    argv = [*omnidoer_base_argv(), "control", "wait-request", "--timeout", wait_timeout, request_id]
    result = run_omnidoer_subprocess(command, tool="omnidoer_request_wait", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload["secret_exposed_to_model"] = False
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("omnidoer_request_wait", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_request_deny(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    request_id = request_id_arg(args, "omnidoer_request_deny")
    result = run_omnidoer_subprocess(command, tool="omnidoer_request_deny", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "deny", request_id])
    status = "denied" if result.exit_code == 0 else "error"
    output = json.dumps({"request_id": request_id, "status": status, "secret_exposed_to_model": False}, ensure_ascii=False, indent=2)
    return ToolResult("omnidoer_request_deny", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_task_submit(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    task = str(args.get("task") or args.get("text") or "")
    if not task.strip():
        raise ToolError("omnidoer_task_submit args.task is required")
    result = run_omnidoer_subprocess(command, tool="omnidoer_task_submit", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "submit-task", task])
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    task_id = find_task_id(payload)
    if task_id:
        payload.setdefault("task_id", task_id)
    if result.exit_code == 0:
        payload.setdefault("next_tools", ["omnidoer_task_list", "omnidoer_task_cancel", "omnidoer_task_complete"])
    output = public_json(payload)
    return ToolResult("omnidoer_task_submit", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_task_list(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    task_id = str(args.get("task_id") or args.get("id") or "")
    status_filter = str(args.get("status") or "").strip().lower()
    result = run_omnidoer_subprocess(command, tool="omnidoer_task_list", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "tasks"])
    payload = parse_public_json_or_kv(result.stdout)
    if task_id:
        if not re.fullmatch(r"task_[A-Za-z0-9_:-]+", task_id):
            raise ToolError("omnidoer_task_list args.task_id must look like task_<id>")
        if isinstance(payload, list):
            matches = [item for item in payload if isinstance(item, dict) and item.get("task_id") == task_id]
            payload = matches[0] if matches else {"status": "not_found", "task_id": task_id}
    if status_filter and isinstance(payload, list):
        payload = [item for item in payload if isinstance(item, dict) and str(item.get("status") or "").lower() == status_filter]
    if isinstance(payload, list):
        try:
            limit = int(args.get("limit") or len(payload))
        except (TypeError, ValueError):
            limit = len(payload)
        payload = payload[: max(1, min(limit, 100))]
    output = public_json(payload)
    return ToolResult("omnidoer_task_list", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_task_complete(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    task_id = task_id_arg(args, "omnidoer_task_complete")
    result = run_omnidoer_subprocess(command, tool="omnidoer_task_complete", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "complete-task", task_id])
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload.setdefault("task_id", task_id)
    if result.exit_code == 0:
        payload.setdefault("status", "completed")
    output = public_json(payload)
    return ToolResult("omnidoer_task_complete", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_task_cancel(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    task_id = task_id_arg(args, "omnidoer_task_cancel")
    result = run_omnidoer_subprocess(command, tool="omnidoer_task_cancel", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "cancel-task", task_id])
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload.setdefault("task_id", task_id)
    if result.exit_code == 0:
        payload.setdefault("status", "cancelled")
    output = public_json(payload)
    return ToolResult("omnidoer_task_cancel", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_messages(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    message_id = str(args.get("message_id") or args.get("id") or "")
    role_filter = str(args.get("role") or "").strip().lower()
    source_filter = str(args.get("source") or "").strip()
    status_filter = str(args.get("status") or "").strip().lower()
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_messages", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "chat-messages"], clamp_result=False)
    payload = parse_public_json_or_kv(result.stdout)
    if message_id:
        if not re.fullmatch(r"msg_[A-Za-z0-9_:-]+", message_id):
            raise ToolError("omnidoer_chat_messages args.message_id must look like msg_<id>")
        if isinstance(payload, list):
            matches = [item for item in payload if isinstance(item, dict) and item.get("message_id") == message_id]
            payload = matches[0] if matches else {"status": "not_found", "message_id": message_id}
    if isinstance(payload, list):
        if role_filter:
            payload = [item for item in payload if isinstance(item, dict) and str(item.get("role") or "").lower() == role_filter]
        if source_filter:
            payload = [item for item in payload if isinstance(item, dict) and str(item.get("source") or "") == source_filter]
        if status_filter:
            payload = [item for item in payload if isinstance(item, dict) and str(item.get("status") or "").lower() == status_filter]
        try:
            limit = int(args.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        payload = payload[-limit:]
    output = public_json(payload)
    return ToolResult("omnidoer_chat_messages", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_next(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    argv = [*omnidoer_base_argv(), "control", "chat-next"]
    claim = bool(args.get("claim"))
    if mode == "safe" and claim:
        raise ToolError("Safe Mode omnidoer_chat_next does not claim messages. Use YOLO Mode after review to claim.")
    if not claim or bool(args.get("no_claim")) or mode == "safe":
        argv.append("--no-claim")
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_next", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    output = public_json(payload)
    return ToolResult("omnidoer_chat_next", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_send(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    message = str(args.get("message") or args.get("text") or "")
    if not message.strip():
        raise ToolError("omnidoer_chat_send args.message is required")
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_send", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "chat-send", message])
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    message_id = find_message_id(payload)
    if message_id:
        payload.setdefault("message_id", message_id)
    if result.exit_code == 0:
        payload.setdefault("next_tools", ["omnidoer_chat_messages", "omnidoer_chat_reply"])
    output = public_json(payload)
    return ToolResult("omnidoer_chat_send", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_reply(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    message = str(args.get("message") or args.get("text") or "")
    if not message.strip():
        raise ToolError("omnidoer_chat_reply args.message is required")
    argv = [*omnidoer_base_argv(), "control", "chat-reply"]
    reply_to = optional_message_id(args, "reply_to", "reply_to_message_id")
    if reply_to:
        argv.extend(["--reply-to", reply_to])
    argv.append(message)
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_reply", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    message_id = find_message_id(payload)
    if message_id:
        payload.setdefault("message_id", message_id)
    output = public_json(payload)
    return ToolResult("omnidoer_chat_reply", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_log_user(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    message = str(args.get("message") or args.get("text") or "")
    if not message.strip():
        raise ToolError("omnidoer_chat_log_user args.message is required")
    argv = [*omnidoer_base_argv(), "control", "chat-log-user"]
    source = plain_token_arg(args, "source", "omnidoer_chat_log_user")
    if source:
        argv.extend(["--source", source])
    argv.append(message)
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_log_user", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    message_id = find_message_id(payload)
    if message_id:
        payload.setdefault("message_id", message_id)
    output = public_json(payload)
    return ToolResult("omnidoer_chat_log_user", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_start(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    argv = [*omnidoer_base_argv(), "control", "chat-start"]
    reply_to = optional_message_id(args, "reply_to", "reply_to_message_id")
    if reply_to:
        argv.extend(["--reply-to", reply_to])
    source = plain_token_arg(args, "source", "omnidoer_chat_start")
    if source:
        argv.extend(["--source", source])
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_start", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    message_id = find_message_id(payload)
    if message_id:
        payload.setdefault("message_id", message_id)
    if result.exit_code == 0:
        payload.setdefault("next_tools", ["omnidoer_chat_delta", "omnidoer_chat_complete"])
    output = public_json(payload)
    return ToolResult("omnidoer_chat_start", command, mode, result.cwd, result.final_command, result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_delta(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    message_id = message_id_arg(args, "omnidoer_chat_delta")
    delta = str(args.get("delta") or args.get("text") or "")
    if not delta:
        raise ToolError("omnidoer_chat_delta args.delta is required")
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_delta", mode=mode, cwd=cwd, argv=[*omnidoer_base_argv(), "control", "chat-delta", message_id, delta])
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload.setdefault("message_id", message_id)
    output = public_json(payload)
    return ToolResult("omnidoer_chat_delta", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_complete(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    message_id = message_id_arg(args, "omnidoer_chat_complete")
    argv = [*omnidoer_base_argv(), "control", "chat-complete"]
    text = str(args.get("text") or "")
    if text:
        argv.extend(["--text", text])
    argv.append(message_id)
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_complete", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload.setdefault("message_id", message_id)
    if result.exit_code == 0:
        payload.setdefault("status", "completed")
    output = public_json(payload)
    return ToolResult("omnidoer_chat_complete", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_chat_record(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    record_type = plain_token_arg(args, "record_type", "omnidoer_chat_record")
    if not record_type:
        record_type = plain_token_arg(args, "type", "omnidoer_chat_record", required=True)
    text = str(args.get("text") or args.get("message") or "")
    if not text.strip():
        raise ToolError("omnidoer_chat_record args.text is required")
    argv = [*omnidoer_base_argv(), "control", "chat-record"]
    role = plain_token_arg(args, "role", "omnidoer_chat_record")
    if role:
        argv.extend(["--role", role])
    message_id = optional_message_id(args, "message_id", "id")
    if message_id:
        argv.extend(["--message-id", message_id])
    argv.extend([record_type, text])
    result = run_omnidoer_subprocess(command, tool="omnidoer_chat_record", mode=mode, cwd=cwd, argv=argv)
    payload = parse_public_json_or_kv(result.stdout)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    message_id = find_message_id(payload) or message_id
    if message_id:
        payload.setdefault("message_id", message_id)
    output = public_json(payload)
    return ToolResult("omnidoer_chat_record", command, mode, result.cwd, redact_command_string(result.final_command), result.exit_code, output + "\n", result.stderr)


def run_omnidoer_doctor(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_doctor", [*omnidoer_base_argv(), "doctor"])


def run_omnidoer_control_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_status", [*omnidoer_base_argv(), "control", "status"])


def run_omnidoer_control_devices(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_devices", [*omnidoer_base_argv(), "control", "devices"])


def run_omnidoer_control_sessions(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_sessions", [*omnidoer_base_argv(), "control", "sessions"])


def run_omnidoer_control_tunnel_info(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_tunnel_info", [*omnidoer_base_argv(), "control", "tunnel-info"])


def run_omnidoer_control_security_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_security_status", [*omnidoer_base_argv(), "control", "security-status"])


def run_omnidoer_control_sync_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    argv = [*omnidoer_base_argv(), "control", "sync-status"]
    thread_id = plain_token_arg(args, "thread_id", "omnidoer_control_sync_status")
    if thread_id:
        argv.extend(["--thread-id", thread_id])
    codex_bin = str(args.get("codex_bin") or "")
    if codex_bin:
        if mode == "safe":
            raise ToolError("Safe Mode omnidoer_control_sync_status uses the default Codex binary. Use YOLO Mode after review to override codex_bin.")
        argv.extend(["--codex-bin", codex_bin])
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_sync_status", argv)


def run_omnidoer_control_revoke_device(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    require_yolo(mode, "omnidoer_control_revoke_device", "revoking a paired device changes Control Client access")
    args = command_args(command)
    device_id = plain_token_arg({"device_id": args.get("device_id") or args.get("id")}, "device_id", "omnidoer_control_revoke_device", required=True)
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_revoke_device", [*omnidoer_base_argv(), "control", "revoke-device", device_id])


def run_omnidoer_control_revoke_session(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    require_yolo(mode, "omnidoer_control_revoke_session", "revoking a session changes Control Client access")
    args = command_args(command)
    session_id = plain_token_arg({"session_id": args.get("session_id") or args.get("id")}, "session_id", "omnidoer_control_revoke_session", required=True)
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_revoke_session", [*omnidoer_base_argv(), "control", "revoke-session", session_id])


def run_omnidoer_control_enable_sync(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    require_yolo(mode, "omnidoer_control_enable_sync", "enabling sync may attach or alter a Codex/OmniDoer session bridge")
    args = command_args(command)
    argv = [*omnidoer_base_argv(), "control", "enable-sync"]
    thread_id = plain_token_arg(args, "thread_id", "omnidoer_control_enable_sync")
    if thread_id:
        argv.extend(["--thread-id", thread_id])
    codex_bin = str(args.get("codex_bin") or "")
    if codex_bin:
        argv.extend(["--codex-bin", codex_bin])
    if bool(args.get("yes")):
        argv.append("--yes")
    if bool(args.get("wait")):
        argv.append("--wait")
    timeout = str(args.get("timeout") or "")
    if timeout:
        argv.extend(["--timeout", timeout])
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_control_enable_sync", argv)


def run_omnidoer_request_challenge(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    require_yolo(mode, "omnidoer_request_challenge", "challenging a request changes Control Client request ownership/state")
    args = command_args(command)
    request_id = request_id_arg(args, "omnidoer_request_challenge")
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_request_challenge", [*omnidoer_base_argv(), "control", "challenge", request_id])


def run_omnidoer_request_takeover(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    require_yolo(mode, "omnidoer_request_takeover", "taking over a request changes Control Client request ownership/state")
    args = command_args(command)
    request_id = request_id_arg(args, "omnidoer_request_takeover")
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_request_takeover", [*omnidoer_base_argv(), "control", "takeover", request_id])


def run_omnidoer_request_release(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    require_yolo(mode, "omnidoer_request_release", "releasing a request changes Control Client request ownership/state")
    args = command_args(command)
    request_id = request_id_arg(args, "omnidoer_request_release")
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_request_release", [*omnidoer_base_argv(), "control", "release", request_id])


def run_omnidoer_audit_tail(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_audit_tail", [*omnidoer_base_argv(), "audit", "tail"])


def run_omnidoer_audit_verify(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_audit_verify", [*omnidoer_base_argv(), "audit", "verify"])


def run_omnidoer_policy_test(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_policy_test", [*omnidoer_base_argv(), "policy", "test"])


def run_omnidoer_telegram_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_telegram_status", [*omnidoer_base_argv(), "telegram", "status"])


def run_omnidoer_console_dry_run(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    codex_args = string_list_arg(args, "codex_args", "omnidoer_console_dry_run")
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_console_dry_run", [*omnidoer_base_argv(), "console", "--dry-run", *codex_args])


def run_omnidoer_upgrade_dry_run(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    argv = [*omnidoer_base_argv(), "upgrade", "--dry-run"]
    branch = safe_cli_value_arg(args, "branch", "omnidoer_upgrade_dry_run")
    if branch:
        argv.extend(["--branch", branch])
    install_dir = str(args.get("install_dir") or "").strip()
    if install_dir:
        if mode == "safe":
            raise ToolError("Safe Mode omnidoer_upgrade_dry_run uses the default install directory. Use YOLO Mode after review to override install_dir.")
        argv.extend(["--install-dir", install_dir])
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_upgrade_dry_run", argv)


def run_omnidoer_mcp_self_test(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_mcp_self_test", [*omnidoer_base_argv(), "mcp", "serve", "--self-test"])


def run_omnidoer_browser_open(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    url = str(args.get("url") or "")
    if not url:
        raise ToolError("omnidoer_browser_open args.url is required")
    validate_browser_url(url, mode)
    return run_omnidoer_public_command(command, workspace, mode, "omnidoer_browser_open", [*omnidoer_base_argv(), "browser", "open", url])


def run_omnidoer_git(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    git_args = git_command_args(command)
    if not git_args:
        raise ToolError("omnidoer_git args.args must include a git subcommand")
    if mode == "safe" and git_args[0] not in SAFE_OMNIDOER_GIT_COMMANDS:
        raise ToolError("Safe Mode omnidoer_git only permits read-only git ls-remote. Use YOLO Mode after review for credentialed writes or fetches.")
    origin = str(args.get("origin") or getattr(settings, "omnidoer_git_origin", "") or "https://github.com")
    argv = [
        *omnidoer_base_argv(),
        "git",
        "run",
        "--origin",
        origin,
        *omnidoer_vault_args(),
        *optional_credential_args(args),
        *git_args,
    ]
    return run_omnidoer_subprocess(command, tool="omnidoer_git", mode=mode, cwd=cwd, argv=argv)


def github_body_json(args: dict[str, Any]) -> str | None:
    if "body_json" in args and args.get("body_json") not in (None, ""):
        return str(args.get("body_json"))
    if "body" in args and args.get("body") is not None:
        return json.dumps(args.get("body"), ensure_ascii=False)
    return None


def run_omnidoer_github_api(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    method = str(args.get("method") or "GET").upper()
    path = str(args.get("path") or "")
    if not path:
        raise ToolError("omnidoer_github_api args.path is required")
    if mode == "safe" and method not in SAFE_GITHUB_API_METHODS:
        raise ToolError("Safe Mode omnidoer_github_api only permits GET. Use YOLO Mode after review for mutating GitHub API calls.")
    origin = str(args.get("origin") or getattr(settings, "omnidoer_git_origin", "") or "https://github.com")
    api_origin = str(args.get("api_origin") or getattr(settings, "omnidoer_github_api_origin", "") or "https://api.github.com")
    argv = [
        *omnidoer_base_argv(),
        "github",
        "api",
        "--origin",
        origin,
        "--api-origin",
        api_origin,
        *omnidoer_vault_args(),
        *optional_credential_args(args),
    ]
    body = github_body_json(args)
    if body is not None:
        argv.extend(["--body-json", body])
    argv.extend([method, path])
    return run_omnidoer_subprocess(command, tool="omnidoer_github_api", mode=mode, cwd=cwd, argv=argv)


def run_git_bootstrap(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    repo_url = str(args.get("repo_url") or args.get("url") or "")
    branch = str(args.get("branch") or args.get("ref") or "")
    depth = args.get("depth", 1)
    try:
        result = bootstrap_workspace_from_git(workspace, repo_url, branch=branch, depth=depth, timeout=timeout_for(command, mode))
    except BootstrapError as exc:
        raise ToolError(str(exc)) from exc
    stdout = result.stdout or (f"Cloned {result.redacted_repo_url} into {result.workspace}\n" if result.exit_code == 0 else "")
    return ToolResult(
        "git_bootstrap",
        command,
        mode,
        str(Path(result.workspace).parent),
        result.command,
        result.exit_code,
        clamp_output(stdout),
        clamp_output(result.stderr),
    )


def validate_patch_paths(patch: str, mode: str) -> None:
    if mode != "safe":
        return
    if is_codex_patch(patch):
        validate_codex_patch_paths(patch)
        return
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            paths.append(raw)
        elif line.startswith("diff --git "):
            parts = line.split()
            paths.extend(part[2:] if part.startswith(("a/", "b/")) else part for part in parts[2:4])
    for raw in paths:
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise ToolError(f"Safe Mode blocks patch path outside workspace: {raw}")


def is_codex_patch(patch: str) -> bool:
    return patch.lstrip().startswith("*** Begin Patch")


def validate_codex_patch_path(path_value: str) -> None:
    path = Path(path_value)
    if not path_value.strip() or path.is_absolute() or ".." in path.parts:
        raise ToolError(f"Safe Mode blocks patch path outside workspace: {path_value}")


def validate_codex_patch_paths(patch: str) -> None:
    for operation in parse_codex_patch(patch):
        validate_codex_patch_path(str(operation["path"]))
        if operation.get("move_to"):
            validate_codex_patch_path(str(operation["move_to"]))


def parse_codex_patch(patch: str) -> list[dict[str, Any]]:
    lines = patch.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[0] != "*** Begin Patch":
        raise ToolError("Codex patch must start with *** Begin Patch")
    if lines[-1] != "*** End Patch":
        raise ToolError("Codex patch must end with *** End Patch")
    operations: list[dict[str, Any]] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith("*** Add File: "):
            path = line.split(": ", 1)[1].strip()
            index += 1
            content = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                if not lines[index].startswith("+"):
                    raise ToolError(f"Add File lines must start with +: {path}")
                content.append(lines[index][1:])
                index += 1
            operations.append({"action": "add", "path": path, "content": content})
            continue
        if line.startswith("*** Delete File: "):
            path = line.split(": ", 1)[1].strip()
            operations.append({"action": "delete", "path": path})
            index += 1
            continue
        if line.startswith("*** Update File: "):
            path = line.split(": ", 1)[1].strip()
            index += 1
            move_to = ""
            hunks: list[list[tuple[str, str]]] = []
            current_hunk: list[tuple[str, str]] | None = None
            while index < len(lines) - 1:
                body_line = lines[index]
                if body_line.startswith(("*** Add File: ", "*** Delete File: ", "*** Update File: ")):
                    break
                if body_line.startswith("*** Move to: "):
                    move_to = body_line.split(": ", 1)[1].strip()
                    index += 1
                    continue
                if body_line == "*** End of File":
                    index += 1
                    continue
                if body_line.startswith("@@"):
                    current_hunk = []
                    hunks.append(current_hunk)
                    index += 1
                    continue
                if current_hunk is None:
                    if not body_line.strip():
                        index += 1
                        continue
                    raise ToolError(f"Update File hunk must start with @@: {path}")
                if not body_line or body_line[0] not in {" ", "-", "+"}:
                    raise ToolError(f"Update File hunk line must start with space, -, or +: {path}")
                current_hunk.append((body_line[0], body_line[1:]))
                index += 1
            if not hunks and not move_to:
                raise ToolError(f"Update File requires a hunk or move target: {path}")
            operations.append({"action": "update", "path": path, "move_to": move_to, "hunks": hunks})
            continue
        raise ToolError(f"Unsupported Codex patch header: {line}")
    if not operations:
        raise ToolError("Codex patch contains no file operations")
    return operations


def read_codex_patch_file(path: Path) -> list[str]:
    if not path.exists():
        raise ToolError(f"Patch target does not exist: {path}")
    if path.is_dir():
        raise ToolError(f"Patch target is a directory: {path}")
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def find_subsequence(lines: list[str], block: list[str], start: int) -> int:
    if not block:
        return max(0, min(start, len(lines)))
    last_start = len(lines) - len(block)
    for position in range(max(0, start), last_start + 1):
        if lines[position : position + len(block)] == block:
            return position
    raise ToolError("Codex patch hunk did not match the target file")


def apply_codex_hunks(lines: list[str], hunks: list[list[tuple[str, str]]]) -> list[str]:
    updated = list(lines)
    cursor = 0
    for hunk in hunks:
        old_block = [text for op, text in hunk if op in {" ", "-"}]
        new_block = [text for op, text in hunk if op in {" ", "+"}]
        position = find_subsequence(updated, old_block, cursor)
        updated[position : position + len(old_block)] = new_block
        cursor = position + len(new_block)
    return updated


def codex_patch_target(cwd: Path, raw_path: str) -> Path:
    return (cwd / raw_path).resolve()


def codex_file_text(lines: list[str]) -> str:
    return "\n".join(lines) + ("\n" if lines else "")


def run_codex_apply_patch(command: dict[str, Any], workspace: Path, mode: str, cwd: Path, patch: str, check_only: bool) -> ToolResult:
    if mode == "safe":
        validate_codex_patch_paths(patch)
    operations = parse_codex_patch(patch)
    virtual_files: dict[Path, list[str] | None] = {}
    written_paths: set[Path] = set()

    def current_lines(path: Path) -> list[str]:
        if path in virtual_files:
            value = virtual_files[path]
            if value is None:
                raise ToolError(f"Patch target does not exist: {path}")
            return list(value)
        return read_codex_patch_file(path)

    for operation in operations:
        target = codex_patch_target(cwd, str(operation["path"]))
        if mode == "safe" and not is_relative_to(target, workspace):
            raise ToolError(f"Safe Mode patch target must stay inside workspace: {target}")
        action = operation["action"]
        if action == "add":
            if target in virtual_files:
                if virtual_files[target] is not None:
                    raise ToolError(f"Patch add target already exists: {target}")
            elif target.exists():
                raise ToolError(f"Patch add target already exists: {target}")
            virtual_files[target] = list(operation["content"])
            written_paths.add(target)
        elif action == "delete":
            current_lines(target)
            virtual_files[target] = None
            written_paths.add(target)
        elif action == "update":
            updated = apply_codex_hunks(current_lines(target), operation.get("hunks", []))
            move_to = str(operation.get("move_to") or "")
            if move_to:
                destination = codex_patch_target(cwd, move_to)
                if mode == "safe" and not is_relative_to(destination, workspace):
                    raise ToolError(f"Safe Mode patch target must stay inside workspace: {destination}")
                if destination in virtual_files:
                    if virtual_files[destination] is not None:
                        raise ToolError(f"Patch move target already exists: {destination}")
                elif destination.exists():
                    raise ToolError(f"Patch move target already exists: {destination}")
                virtual_files[target] = None
                virtual_files[destination] = updated
                written_paths.update({target, destination})
            else:
                virtual_files[target] = updated
                written_paths.add(target)
        else:
            raise ToolError(f"Unsupported Codex patch action: {action}")

    if check_only:
        return ToolResult(
            "apply_patch",
            command,
            mode,
            str(cwd),
            "codex apply_patch --check",
            0,
            f"Codex patch check passed for {len(operations)} operation(s).\n",
            "",
        )

    for path, lines in virtual_files.items():
        if lines is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(codex_file_text(lines), encoding="utf-8")
    return ToolResult(
        "apply_patch",
        command,
        mode,
        str(cwd),
        "codex apply_patch",
        0,
        f"Applied Codex patch to {len(written_paths)} file(s).\n",
        "",
    )


def run_apply_patch(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    patch = str(args.get("patch") or args.get("diff") or "")
    check_only = bool(args.get("check_only") or False)
    if not patch.strip():
        raise ToolError("apply_patch args.patch is required")
    if is_codex_patch(patch):
        return run_codex_apply_patch(command, workspace, mode, cwd, patch, check_only)
    validate_patch_paths(patch, mode)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(patch)
        patch_path = handle.name
    try:
        check = subprocess.run(["git", "apply", "--check", patch_path], cwd=str(cwd), text=True, capture_output=True, timeout=timeout_for(command, mode))
        if check.returncode != 0 or check_only:
            return ToolResult(
                "apply_patch",
                command,
                mode,
                str(cwd),
                "git apply --check",
                int(check.returncode),
                clamp_output(check.stdout or ""),
                clamp_output(check.stderr or ""),
            )
        apply = subprocess.run(["git", "apply", patch_path], cwd=str(cwd), text=True, capture_output=True, timeout=timeout_for(command, mode))
        return ToolResult(
            "apply_patch",
            command,
            mode,
            str(cwd),
            "git apply",
            int(apply.returncode),
            clamp_output((check.stdout or "") + (apply.stdout or "")),
            clamp_output((check.stderr or "") + (apply.stderr or "")),
        )
    finally:
        try:
            Path(patch_path).unlink()
        except OSError:
            pass


def run_read_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    path = resolve_path(args.get("path"), workspace, mode)
    try:
        limit = int(args.get("limit") or settings.max_output_chars)
    except (TypeError, ValueError):
        limit = settings.max_output_chars
    content = path.read_text(encoding=args.get("encoding") or "utf-8", errors="replace")
    if len(content) > limit:
        content = content[:limit] + "\n...[file truncated by Handex]..."
    return ToolResult("read_file", command, mode, str(path.parent), f"read_file {path}", 0, content, "")


def run_write_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    path = resolve_path(args.get("path"), workspace, mode)
    content = str(args.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=args.get("encoding") or "utf-8")
    return ToolResult("write_file", command, mode, str(path.parent), f"write_file {path}", 0, f"Wrote {len(content)} characters to {path}\n", "")


def run_append_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    path = resolve_path(args.get("path"), workspace, mode)
    content = str(args.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding=args.get("encoding") or "utf-8") as handle:
        handle.write(content)
    return ToolResult("append_file", command, mode, str(path.parent), f"append_file {path}", 0, f"Appended {len(content)} characters to {path}\n", "")


def run_replace_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    path = resolve_path(args.get("path"), workspace, mode)
    old = str(args.get("old") if args.get("old") is not None else args.get("search") or "")
    new = str(args.get("new") if args.get("new") is not None else args.get("replace") or "")
    if old == "":
        raise ToolError("replace_file args.old is required")
    content = path.read_text(encoding=args.get("encoding") or "utf-8", errors="replace")
    if old not in content:
        return ToolResult("replace_file", command, mode, str(path.parent), f"replace_file {path}", 1, "", "Search text was not found.\n")
    count_arg = args.get("count")
    count = -1 if count_arg in (None, "") else int(count_arg)
    updated = content.replace(old, new, count)
    path.write_text(updated, encoding=args.get("encoding") or "utf-8")
    replacements = content.count(old) if count < 0 else min(content.count(old), count)
    return ToolResult("replace_file", command, mode, str(path.parent), f"replace_file {path}", 0, f"Replaced {replacements} occurrence(s) in {path}\n", "")


def run_delete_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    path = resolve_path(args.get("path"), workspace, mode)
    if not path.exists():
        return ToolResult("delete_file", command, mode, str(path.parent), f"delete_file {path}", 1, "", "Path does not exist.\n")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return ToolResult("delete_file", command, mode, str(path.parent), f"delete_file {path}", 0, f"Deleted {path}\n", "")


def iter_files(root: Path, max_entries: int) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [item for item in dirnames if item not in {".git", ".venv", "__pycache__", "node_modules"}]
        for dirname in dirnames:
            files.append(Path(current_root) / dirname)
            if len(files) >= max_entries:
                return files
        for filename in filenames:
            files.append(Path(current_root) / filename)
            if len(files) >= max_entries:
                return files
    return files


def run_list_files(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    root = resolve_path(args.get("path") or args.get("root") or ".", workspace, mode)
    max_entries = int(args.get("max_entries") or 200)
    pattern = str(args.get("pattern") or "*")
    files = [path for path in iter_files(root, max_entries * 3) if fnmatch.fnmatch(path.name, pattern)]
    lines = []
    for path in files[:max_entries]:
        marker = "/" if path.is_dir() else ""
        lines.append(str(path.relative_to(root)) + marker)
    return ToolResult("list_files", command, mode, str(root), f"list_files {root}", 0, "\n".join(lines) + ("\n" if lines else ""), "")


def run_search_files(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    root = resolve_path(args.get("path") or args.get("root") or ".", workspace, mode)
    query = str(args.get("query") or args.get("pattern") or "")
    if not query:
        raise ToolError("search_files args.query or args.pattern is required")
    max_entries = int(args.get("max_entries") or 200)
    matches = []
    for path in iter_files(root, max_entries * 10):
        rel = str(path.relative_to(root))
        if query.lower() in rel.lower() or fnmatch.fnmatch(path.name, query):
            matches.append(rel + ("/" if path.is_dir() else ""))
            if len(matches) >= max_entries:
                break
    return ToolResult("search_files", command, mode, str(root), f"search_files {root}", 0, "\n".join(matches) + ("\n" if matches else ""), "")


def run_grep(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    root = resolve_path(args.get("path") or args.get("root") or ".", workspace, mode)
    pattern = str(args.get("pattern") or args.get("query") or "")
    glob_pattern = str(args.get("glob") or "*")
    if not pattern:
        raise ToolError("grep args.pattern is required")
    max_matches = int(args.get("max_matches") or 200)
    regex = re.compile(pattern)
    lines: list[str] = []
    candidates = [root] if root.is_file() else [path for path in iter_files(root, max_matches * 30) if path.is_file()]
    for path in candidates:
        if not fnmatch.fnmatch(path.name, glob_pattern):
            continue
        try:
            text = path.read_text(encoding=args.get("encoding") or "utf-8", errors="replace")
        except OSError:
            continue
        base = root.parent if root.is_file() else root
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                lines.append(f"{path.relative_to(base)}:{line_no}:{line}")
                if len(lines) >= max_matches:
                    break
        if len(lines) >= max_matches:
            break
    return ToolResult("grep", command, mode, str(root), f"grep {pattern} {root}", 0, "\n".join(lines) + ("\n" if lines else ""), "")


def run_list_skills(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    skills = [
        {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "root": skill.root,
        }
        for skill in list_skills()
    ]
    output = json.dumps(skills, ensure_ascii=False, indent=2)
    return ToolResult("list_skills", command, mode, str(workspace), "list_skills", 0, output + "\n", "")


def run_read_skill(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    identifier = str(args.get("skill_id") or args.get("name") or "")
    if not identifier:
        raise ToolError("read_skill args.skill_id is required")
    skill, content = read_skill(identifier)
    header = {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "root": skill.root,
    }
    output = json.dumps(header, ensure_ascii=False, indent=2) + "\n\n" + content
    return ToolResult("read_skill", command, mode, str(workspace), f"read_skill {identifier}", 0, clamp_output(output), "")


def run_read_skill_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    identifier = str(args.get("skill_id") or args.get("name") or "")
    relative_path = str(args.get("path") or args.get("file") or "")
    if not identifier:
        raise ToolError("read_skill_file args.skill_id is required")
    try:
        max_chars = int(args.get("max_chars") or settings.max_output_chars)
    except (TypeError, ValueError):
        max_chars = settings.max_output_chars
    try:
        skill, path, content = read_skill_file(identifier, relative_path, limit=max_chars)
    except (KeyError, ValueError, PermissionError, FileNotFoundError) as exc:
        raise ToolError(str(exc)) from exc
    header = {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "root": skill.root,
        "path": path,
    }
    output = json.dumps(header, ensure_ascii=False, indent=2) + "\n\n" + content
    return ToolResult("read_skill_file", command, mode, str(workspace), f"read_skill_file {identifier} {path}", 0, clamp_output(output), "")


def run_skill_pack(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    return ToolResult("skill_pack", command, mode, str(workspace), "skill_pack", 0, clamp_output(skill_pack_prompt()) + "\n", "")


def run_list_vault_credentials(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    output = json.dumps(list_vault_metadata(), ensure_ascii=False, indent=2)
    return ToolResult("list_vault_credentials", command, mode, str(workspace), "list_vault_credentials", 0, output + "\n", "")


def run_vault_list(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    output = json.dumps(metadata_for_tools(), ensure_ascii=False, indent=2)
    return ToolResult("vault_list", command, mode, str(workspace), "vault_list", 0, output + "\n", "")


def parse_handex_vault_id(value: Any) -> int:
    raw = str(value or "")
    if raw.startswith("handex:"):
        raw = raw.split(":", 1)[1]
    try:
        return int(raw)
    except ValueError as exc:
        raise ToolError("vault_run args.credential_id must look like handex:<id>") from exc


def run_vault_run(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    item_id = parse_handex_vault_id(args.get("credential_id") or args.get("id"))
    env_name = str(args.get("env") or "HANDEX_SECRET")
    username_env = str(args.get("username_env") or "")
    command_text = str(args.get("command") or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
        raise ToolError("vault_run args.env must be a valid environment variable name")
    if username_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", username_env):
        raise ToolError("vault_run args.username_env must be a valid environment variable name")
    if not command_text.strip():
        raise ToolError("vault_run args.command is required")
    if mode == "safe":
        warnings = validate_safe_shell(command_text)
        if warnings:
            raise ToolError("; ".join(warnings))
    item, secret = decrypt_item_secret(item_id)
    extra_env = {env_name: secret}
    if username_env:
        extra_env[username_env] = str(item.get("username") or "")
    cwd = resolve_cwd(command, workspace, mode)
    return subprocess_result(
        command=command,
        tool="vault_run",
        mode=mode,
        cwd=cwd,
        final_command=command_text,
        shell=True,
        extra_env=extra_env,
        redact_values=[secret],
    )


def run_capability_report(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    tool_name = str(command.get("tool") or "capability_report")
    return ToolResult(tool_name, command, mode, str(workspace), tool_name, 0, clamp_output(configured_capability_report()) + "\n", "")


def run_capability_search(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    try:
        limit = int(args.get("limit") or 12)
    except (TypeError, ValueError):
        limit = 12
    payload = search_capabilities(str(args.get("query") or ""), limit=limit)
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("capability_search", command, mode, str(workspace), "capability_search", 0, output + "\n", "")


def run_context_pack(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    try:
        max_chars = int(args.get("max_chars") or 16000)
    except (TypeError, ValueError):
        max_chars = 16000
    output = build_context_pack(cwd, max_chars=max_chars)
    return ToolResult("context_pack", command, mode, str(cwd), "context_pack", 0, clamp_output(output) + "\n", "")


def run_list_uploads(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    try:
        max_files = int(args.get("max_files") or 200)
    except (TypeError, ValueError):
        max_files = 200
    uploads = [
        {
            "path": item.path,
            "upload_path": item.upload_path,
            "name": item.name,
            "size": item.size,
            "media_type": item.media_type,
            "is_image": item.is_image,
            "modified_at": item.modified_at,
            "preview": item.preview,
            "preview_omitted": item.preview_omitted,
        }
        for item in list_workspace_uploads(workspace, max_files=max(1, min(max_files, 500)))
    ]
    output = json.dumps(uploads, ensure_ascii=False, indent=2)
    return ToolResult("list_uploads", command, mode, str(workspace), "list_uploads", 0, output + "\n", "")


def image_url_for_workspace(workspace: Path, relative_path: str) -> str:
    try:
        project_id = project_id_for_workspace(workspace)
    except Exception:
        return ""
    return f"/projects/{project_id}/image?path={quote(relative_path, safe='')}"


def file_url_for_workspace(workspace: Path, relative_path: str, *, inline: bool = False) -> str:
    try:
        project_id = project_id_for_workspace(workspace)
    except Exception:
        return ""
    suffix = "&inline=1" if inline else ""
    return f"/projects/{project_id}/file?path={quote(relative_path, safe='')}{suffix}"


def run_download_file(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    inline = bool(args.get("inline") or False)
    try:
        info = resolve_workspace_file(workspace, args.get("path") or args.get("file") or "")
    except FileAccessError as exc:
        raise ToolError(str(exc)) from exc
    payload = file_info_payload(info, url=file_url_for_workspace(workspace, info.relative_path, inline=inline))
    payload["inline"] = inline
    payload["note"] = "Open this authenticated Handex URL to download or inspect the file. Secret-looking filenames are blocked by default."
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("download_file", command, mode, str(info.path.parent), f"download_file {info.relative_path}", 0, output + "\n", "")


def run_view_image(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    try:
        info = resolve_workspace_image(workspace, args.get("path") or args.get("file") or "")
    except ImageError as exc:
        raise ToolError(str(exc)) from exc
    payload = image_info_payload(info, url=image_url_for_workspace(workspace, info.relative_path))
    payload["note"] = "Open the URL in Handex to inspect the image, or upload the image separately to the web LLM when visual reasoning is required."
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("view_image", command, mode, str(info.path.parent), f"view_image {info.relative_path}", 0, output + "\n", "")


def run_recent_results(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    include_result_prompt = bool(args.get("include_result_prompt") or args.get("include_prompts") or False)
    logs = project_logs_for_workspace(
        workspace,
        limit=max(1, min(limit, 30)),
        include_result_prompt=include_result_prompt,
    )
    output = json.dumps(logs, ensure_ascii=False, indent=2)
    return ToolResult("recent_results", command, mode, str(workspace), "recent_results", 0, output + "\n", "")


def batch_child_commands(command: dict[str, Any]) -> list[dict[str, Any]]:
    args = command_args(command)
    raw = args.get("commands")
    if raw is None:
        raw = args.get("tool_commands")
    if raw is None:
        raw = args.get("items")
    if not isinstance(raw, list) or not raw:
        raise ToolError("tool_batch args.commands must be a non-empty list")
    if len(raw) > MAX_BATCH_COMMANDS:
        raise ToolError(f"tool_batch can run at most {MAX_BATCH_COMMANDS} commands")
    children: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ToolError(f"tool_batch command #{index} must be a JSON object")
        children.append(item)
    return children


def validate_batch_child(command: dict[str, Any], parent_mode: str) -> str:
    tool = str(command.get("tool") or "")
    if tool not in TOOL_SCHEMA["properties"]["tool"]["enum"]:
        raise ToolError(f"Unsupported tool in batch: {tool}")
    if tool == "tool_batch":
        raise ToolError("tool_batch cannot contain nested tool_batch commands")
    mode = normalize_mode(command.get("mode") or parent_mode)
    if parent_mode == "safe" and mode != "safe":
        raise ToolError("Safe Mode tool_batch cannot contain YOLO child commands")
    if parent_mode == "safe":
        if tool == "git":
            git_args = git_command_args(command)
            if not git_args or git_args[0] not in SAFE_BATCH_GIT_COMMANDS:
                raise ToolError("Safe Mode tool_batch only permits read-only git subcommands")
        elif tool not in SAFE_BATCH_TOOLS:
            raise ToolError(f"Safe Mode tool_batch only permits read-only tools; blocked {tool}")
    return mode


def validate_batch_children(command: dict[str, Any], parent_mode: str) -> list[tuple[dict[str, Any], str]]:
    return [(child, validate_batch_child(child, parent_mode)) for child in batch_child_commands(command)]


def compact_batch_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[batch result truncated by Handex]..."


def run_tool_batch(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    try:
        max_chars_per_result = int(args.get("max_chars_per_result") or 5000)
    except (TypeError, ValueError):
        max_chars_per_result = 5000
    max_chars_per_result = max(500, min(max_chars_per_result, settings.max_output_chars))
    stop_on_error = bool(args.get("stop_on_error", True))
    children = validate_batch_children(command, mode)
    results = []
    stopped_on_error = False
    for index, (child, child_mode) in enumerate(children, start=1):
        child_command = dict(child)
        child_command.setdefault("mode", child_mode)
        try:
            result = registry.run(child_command, str(workspace), child_mode)
            entry = {
                "index": index,
                "tool": result.tool,
                "mode": result.mode,
                "cwd": result.cwd,
                "command": sanitize_command_for_prompt(result.command),
                "final_command": redact_command_string(result.final_command),
                "exit_code": result.exit_code,
                "stdout": compact_batch_text(result.stdout or "", max_chars_per_result),
                "stderr": compact_batch_text(result.stderr or "", max_chars_per_result),
            }
        except Exception as exc:
            entry = {
                "index": index,
                "tool": str(child.get("tool") or ""),
                "mode": child_mode,
                "cwd": str(workspace),
                "command": sanitize_command_for_prompt(child),
                "final_command": preview_command(child),
                "exit_code": 1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}\n",
            }
        results.append(entry)
        if int(entry["exit_code"]) != 0 and stop_on_error:
            stopped_on_error = index < len(children)
            break
    exit_code = 0 if all(int(item["exit_code"]) == 0 for item in results) else 1
    payload = {
        "results": results,
        "stopped_on_error": stopped_on_error,
        "requested": len(children),
        "completed": len(results),
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("tool_batch", command, mode, str(workspace), f"tool_batch {len(children)} command(s)", exit_code, output + "\n", "")


def run_update_plan(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    payload: Any = args if ("plan" in args or "items" in args or "explanation" in args) else command
    explanation, items = normalize_plan_payload(payload)
    project_id = project_id_for_workspace(workspace)
    save_project_plan(project_id, explanation, items)
    output = plan_form_json(get_project_plan(project_id))
    return ToolResult("update_plan", command, mode, str(workspace), "update_plan", 0, output + "\n", "")


def run_plan_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    project_id = project_id_for_workspace(workspace)
    output = plan_form_json(get_project_plan(project_id))
    return ToolResult("plan_status", command, mode, str(workspace), "plan_status", 0, output + "\n", "")


def ensure_job_belongs_to_workspace(job: dict[str, Any], workspace: Path) -> None:
    project_id = project_id_for_workspace(workspace)
    if int(job.get("project_id") or 0) != project_id:
        raise ToolError(f"Background job does not belong to this workspace: {job.get('id')}")


def run_job_status(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    try:
        max_chars = int(args.get("max_chars") or settings.max_output_chars)
    except (TypeError, ValueError):
        max_chars = settings.max_output_chars
    max_chars = max(1000, min(max_chars, settings.max_output_chars))
    identifier = args.get("job_id") or args.get("id")
    if identifier:
        try:
            job = get_job_display(int(identifier), max_chars=max_chars)
        except (TypeError, ValueError) as exc:
            raise ToolError("job_status args.job_id must be an integer") from exc
        ensure_job_belongs_to_workspace(job, workspace)
        payload: Any = job
    else:
        try:
            limit = int(args.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        project_id = project_id_for_workspace(workspace)
        payload = list_project_job_displays(project_id, limit=max(1, min(limit, 50)), max_chars=max_chars)
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    return ToolResult("job_status", command, mode, str(workspace), "job_status", 0, output + "\n", "")


def run_job_stop(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    identifier = args.get("job_id") or args.get("id")
    if not identifier:
        raise ToolError("job_stop args.job_id is required")
    try:
        job_id = int(identifier)
    except (TypeError, ValueError) as exc:
        raise ToolError("job_stop args.job_id must be an integer") from exc
    job = get_job_display(job_id, max_chars=2000)
    ensure_job_belongs_to_workspace(job, workspace)
    stopped = stop_job(job_id)
    output = json.dumps(get_job_display(int(stopped["id"])), ensure_ascii=False, indent=2)
    return ToolResult("job_stop", command, mode, str(workspace), f"job_stop {job_id}", 0, output + "\n", "")


def run_plugin_list(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    output = json.dumps(
        [
            {
                "plugin_id": plugin.plugin_id,
                "name": plugin.name,
                "description": plugin.description,
                "safe": plugin.safe,
                "timeout": plugin.timeout,
                "root": plugin.root,
            }
            for plugin in list_plugins()
        ],
        ensure_ascii=False,
        indent=2,
    )
    return ToolResult("plugin_list", command, mode, str(workspace), "plugin_list", 0, output + "\n", "")


def run_plugin_run(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    args = command_args(command)
    identifier = str(args.get("plugin_id") or args.get("id") or args.get("name") or "")
    if not identifier:
        raise ToolError("plugin_run args.plugin_id is required")
    try:
        plugin = find_plugin(identifier)
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    if mode == "safe" and not plugin.safe:
        raise ToolError("Safe Mode only runs plugins whose manifest sets safe=true. Use YOLO Mode after review.")
    cwd = resolve_cwd(command, workspace, mode)
    payload = args.get("input")
    if payload is None:
        payload = {key: value for key, value in args.items() if key not in {"plugin_id", "id", "name", "timeout"}}
    payload_text = json.dumps(payload, ensure_ascii=False)
    try:
        requested_timeout = int(args.get("timeout") or command.get("timeout") or plugin.timeout)
    except (TypeError, ValueError):
        requested_timeout = plugin.timeout
    timeout = max(1, min(requested_timeout, 900 if mode == "yolo" else 180))
    argv = plugin_argv(plugin)
    final_command = " ".join(shlex.quote(item) for item in argv)
    plugin_command = {**command, "timeout": timeout}
    return subprocess_result(
        command=plugin_command,
        tool="plugin_run",
        mode=mode,
        cwd=cwd,
        final_command=final_command,
        argv=argv,
        extra_env={
            "HANDEX_PLUGIN_ID": plugin.plugin_id,
            "HANDEX_PLUGIN_ARGS": payload_text,
            "HANDEX_WORKSPACE": str(workspace),
            "HANDEX_MODE": mode,
        },
        input_text=payload_text,
        inherit_env=False,
    )


registry = ToolRegistry()
registry.register("shell", run_shell)
registry.register("background_shell", run_background_shell)
registry.register("python", run_python)
registry.register("git", run_git)
registry.register("omnidoer_credential_request", run_omnidoer_credential_request)
registry.register("omnidoer_credential_list", run_omnidoer_credential_list)
registry.register("omnidoer_vault_unlock", run_omnidoer_vault_unlock)
registry.register("omnidoer_credential_save_request", run_omnidoer_credential_save_request)
registry.register("omnidoer_request_status", run_omnidoer_request_status)
registry.register("omnidoer_request_wait", run_omnidoer_request_wait)
registry.register("omnidoer_request_deny", run_omnidoer_request_deny)
registry.register("omnidoer_task_submit", run_omnidoer_task_submit)
registry.register("omnidoer_task_list", run_omnidoer_task_list)
registry.register("omnidoer_task_complete", run_omnidoer_task_complete)
registry.register("omnidoer_task_cancel", run_omnidoer_task_cancel)
registry.register("omnidoer_chat_messages", run_omnidoer_chat_messages)
registry.register("omnidoer_chat_next", run_omnidoer_chat_next)
registry.register("omnidoer_chat_send", run_omnidoer_chat_send)
registry.register("omnidoer_chat_reply", run_omnidoer_chat_reply)
registry.register("omnidoer_chat_log_user", run_omnidoer_chat_log_user)
registry.register("omnidoer_chat_start", run_omnidoer_chat_start)
registry.register("omnidoer_chat_delta", run_omnidoer_chat_delta)
registry.register("omnidoer_chat_complete", run_omnidoer_chat_complete)
registry.register("omnidoer_chat_record", run_omnidoer_chat_record)
registry.register("omnidoer_doctor", run_omnidoer_doctor)
registry.register("omnidoer_control_status", run_omnidoer_control_status)
registry.register("omnidoer_control_devices", run_omnidoer_control_devices)
registry.register("omnidoer_control_sessions", run_omnidoer_control_sessions)
registry.register("omnidoer_control_tunnel_info", run_omnidoer_control_tunnel_info)
registry.register("omnidoer_control_security_status", run_omnidoer_control_security_status)
registry.register("omnidoer_control_sync_status", run_omnidoer_control_sync_status)
registry.register("omnidoer_control_revoke_device", run_omnidoer_control_revoke_device)
registry.register("omnidoer_control_revoke_session", run_omnidoer_control_revoke_session)
registry.register("omnidoer_control_enable_sync", run_omnidoer_control_enable_sync)
registry.register("omnidoer_request_challenge", run_omnidoer_request_challenge)
registry.register("omnidoer_request_takeover", run_omnidoer_request_takeover)
registry.register("omnidoer_request_release", run_omnidoer_request_release)
registry.register("omnidoer_audit_tail", run_omnidoer_audit_tail)
registry.register("omnidoer_audit_verify", run_omnidoer_audit_verify)
registry.register("omnidoer_policy_test", run_omnidoer_policy_test)
registry.register("omnidoer_telegram_status", run_omnidoer_telegram_status)
registry.register("omnidoer_console_dry_run", run_omnidoer_console_dry_run)
registry.register("omnidoer_upgrade_dry_run", run_omnidoer_upgrade_dry_run)
registry.register("omnidoer_mcp_self_test", run_omnidoer_mcp_self_test)
registry.register("omnidoer_browser_open", run_omnidoer_browser_open)
registry.register("omnidoer_git", run_omnidoer_git)
registry.register("omnidoer_github_api", run_omnidoer_github_api)
registry.register("git_bootstrap", run_git_bootstrap)
registry.register("apply_patch", run_apply_patch)
registry.register("read_file", run_read_file)
registry.register("write_file", run_write_file)
registry.register("append_file", run_append_file)
registry.register("replace_file", run_replace_file)
registry.register("delete_file", run_delete_file)
registry.register("list_files", run_list_files)
registry.register("search_files", run_search_files)
registry.register("grep", run_grep)
registry.register("list_skills", run_list_skills)
registry.register("read_skill", run_read_skill)
registry.register("read_skill_file", run_read_skill_file)
registry.register("skill_pack", run_skill_pack)
registry.register("list_vault_credentials", run_list_vault_credentials)
registry.register("vault_list", run_vault_list)
registry.register("vault_run", run_vault_run)
registry.register("capability_report", run_capability_report)
registry.register("capability_search", run_capability_search)
registry.register("context_pack", run_context_pack)
registry.register("list_uploads", run_list_uploads)
registry.register("download_file", run_download_file)
registry.register("view_image", run_view_image)
registry.register("recent_results", run_recent_results)
registry.register("tool_batch", run_tool_batch)
registry.register("update_plan", run_update_plan)
registry.register("plan_status", run_plan_status)
registry.register("job_status", run_job_status)
registry.register("job_stop", run_job_stop)
registry.register("plugin_list", run_plugin_list)
registry.register("plugin_run", run_plugin_run)
