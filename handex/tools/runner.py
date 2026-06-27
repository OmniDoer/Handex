from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import settings
from ..prompts import TOOL_SCHEMA


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
    if tool in {"read_file", "write_file", "append_file", "replace_file", "delete_file", "list_files", "search_files", "grep"}:
        return f"{tool} {args.get('path') or args.get('root') or '.'}"
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
) -> ToolResult:
    try:
        completed = subprocess.run(
            final_command if shell else argv,
            shell=shell,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_for(command, mode),
            executable="/bin/bash" if shell else None,
        )
        return ToolResult(
            tool=tool,
            command=command,
            mode=mode,
            cwd=str(cwd),
            final_command=final_command,
            exit_code=int(completed.returncode),
            stdout=clamp_output(completed.stdout or ""),
            stderr=clamp_output(completed.stderr or ""),
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
            stdout=clamp_output(stdout),
            stderr=clamp_output((stderr or "") + "\nHandex timeout expired."),
        )


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


registry = ToolRegistry()
registry.register("shell", run_shell)
registry.register("python", run_python)
registry.register("git", run_git)
registry.register("read_file", run_read_file)
registry.register("write_file", run_write_file)
registry.register("append_file", run_append_file)
registry.register("replace_file", run_replace_file)
registry.register("delete_file", run_delete_file)
registry.register("list_files", run_list_files)
registry.register("search_files", run_search_files)
registry.register("grep", run_grep)
