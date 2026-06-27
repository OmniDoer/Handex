from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..capabilities import configured_capability_report, list_skills, list_vault_metadata, read_skill, skill_pack_prompt
from ..config import settings
from ..prompts import TOOL_SCHEMA
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
        return ToolPreview(tool=tool, mode=normalized_mode, cwd=str(cwd), final_command=final_command, warnings=warnings)


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
    if tool == "shell" and mode == "safe":
        warnings.extend(validate_safe_shell(str(command_args(command).get("command") or command.get("command") or "")))
    return warnings


def preview_command(command: dict[str, Any]) -> str:
    args = command_args(command)
    tool = str(command.get("tool") or "")
    if tool == "shell":
        return str(args.get("command") or command.get("command") or "")
    if tool == "python":
        code = str(args.get("code") or command.get("code") or "")
        return f"{sys.executable} -c {shlex.quote(code[:500])}"
    if tool == "git":
        git_args = git_command_args(command)
        return "git " + " ".join(shlex.quote(item) for item in git_args)
    if tool == "apply_patch":
        return "git apply --check && git apply"
    if tool in {"read_file", "write_file", "append_file", "replace_file", "delete_file", "list_files", "search_files", "grep"}:
        return f"{tool} {args.get('path') or args.get('root') or '.'}"
    if tool == "read_skill":
        return f"read_skill {args.get('skill_id') or args.get('name') or ''}"
    if tool in {"list_skills", "skill_pack", "list_vault_credentials", "vault_list", "capability_report"}:
        return tool
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
) -> ToolResult:
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    try:
        completed = subprocess.run(
            final_command if shell else argv,
            shell=shell,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_for(command, mode),
            executable="/bin/bash" if shell else None,
            env=env,
        )
        return ToolResult(
            tool=tool,
            command=command,
            mode=mode,
            cwd=str(cwd),
            final_command=final_command,
            exit_code=int(completed.returncode),
            stdout=clamp_output(redact_text(completed.stdout or "", redact_values or [])),
            stderr=clamp_output(redact_text(completed.stderr or "", redact_values or [])),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        return ToolResult(
            tool=tool,
            command=command,
            mode=mode,
            cwd=str(cwd),
            final_command=final_command,
            exit_code=124,
            stdout=clamp_output(redact_text(stdout, redact_values or [])),
            stderr=clamp_output(redact_text((stderr or "") + "\nHandex timeout expired.", redact_values or [])),
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


def validate_patch_paths(patch: str, mode: str) -> None:
    if mode != "safe":
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


def run_apply_patch(command: dict[str, Any], workspace: Path, mode: str) -> ToolResult:
    cwd = resolve_cwd(command, workspace, mode)
    args = command_args(command)
    patch = str(args.get("patch") or args.get("diff") or "")
    check_only = bool(args.get("check_only") or False)
    if not patch.strip():
        raise ToolError("apply_patch args.patch is required")
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


registry = ToolRegistry()
registry.register("shell", run_shell)
registry.register("python", run_python)
registry.register("git", run_git)
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
registry.register("skill_pack", run_skill_pack)
registry.register("list_vault_credentials", run_list_vault_credentials)
registry.register("vault_list", run_vault_list)
registry.register("vault_run", run_vault_run)
registry.register("capability_report", run_capability_report)
