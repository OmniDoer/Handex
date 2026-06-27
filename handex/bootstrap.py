from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class BootstrapError(Exception):
    pass


@dataclass(frozen=True)
class BootstrapResult:
    repo_url: str
    redacted_repo_url: str
    branch: str
    depth: int
    workspace: str
    command: str
    exit_code: int
    stdout: str
    stderr: str


CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def workspace_has_files(workspace: Path) -> bool:
    return workspace.exists() and any(workspace.iterdir())


def validate_repo_url(repo_url: str) -> str:
    value = repo_url.strip()
    if not value:
        raise BootstrapError("Git repository URL is required")
    if len(value) > 4096 or CONTROL_RE.search(value):
        raise BootstrapError("Git repository URL contains invalid characters")
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme not in {"file", "git", "http", "https", "ssh"}:
        raise BootstrapError(f"Unsupported Git repository URL scheme: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise BootstrapError("Git repository URL must not contain embedded credentials")
    if value.startswith("-"):
        raise BootstrapError("Git repository URL must not start with '-'")
    return value


def redacted_repo_url(repo_url: str) -> str:
    parsed = urlsplit(repo_url)
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return repo_url


def normalize_branch(branch: str) -> str:
    value = branch.strip()
    if not value:
        return ""
    if len(value) > 255 or CONTROL_RE.search(value) or any(char.isspace() for char in value):
        raise BootstrapError("Git branch/ref contains invalid characters")
    if value.startswith("-"):
        raise BootstrapError("Git branch/ref must not start with '-'")
    return value


def normalize_depth(depth: str | int | None) -> int:
    if depth in (None, ""):
        return 1
    try:
        value = int(depth)
    except (TypeError, ValueError) as exc:
        raise BootstrapError("Git clone depth must be a positive integer") from exc
    if value < 0:
        raise BootstrapError("Git clone depth must be a positive integer")
    return min(value, 10000)


def minimal_git_env() -> dict[str, str]:
    keys = {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TZ", "SSH_AUTH_SOCK"}
    env = {key: value for key, value in os.environ.items() if key in keys}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def clone_command(repo_url: str, workspace: Path, branch: str = "", depth: int = 1) -> list[str]:
    argv = ["git", "clone"]
    if depth > 0:
        argv.extend(["--depth", str(depth)])
    if branch:
        argv.extend(["--branch", branch])
    argv.extend(["--", repo_url, str(workspace)])
    return argv


def command_display(argv: list[str], redacted_url: str, workspace: Path) -> str:
    display = list(argv)
    for index, value in enumerate(display):
        if value == "--" and index + 1 < len(display):
            display[index + 1] = redacted_url
    if display:
        display[-1] = str(workspace)
    return " ".join(shlex.quote(item) for item in display)


def bootstrap_workspace_from_git(
    workspace: str | Path,
    repo_url: str,
    *,
    branch: str = "",
    depth: str | int | None = 1,
    timeout: int = 300,
) -> BootstrapResult:
    target = Path(workspace).expanduser().resolve()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not target.is_dir():
        raise BootstrapError(f"Workspace path is not a directory: {target}")
    if workspace_has_files(target):
        raise BootstrapError("Git bootstrap requires an empty workspace directory")

    normalized_url = validate_repo_url(repo_url)
    normalized_branch = normalize_branch(branch)
    normalized_depth = normalize_depth(depth)
    argv = clone_command(normalized_url, target, branch=normalized_branch, depth=normalized_depth)
    safe_url = redacted_repo_url(normalized_url)
    display = command_display(argv, safe_url, target)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(parent),
            text=True,
            capture_output=True,
            timeout=max(1, min(int(timeout), 900)),
            env=minimal_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        return BootstrapResult(
            normalized_url,
            safe_url,
            normalized_branch,
            normalized_depth,
            str(target),
            display,
            124,
            stdout,
            (stderr or "") + "\nHandex git bootstrap timeout expired.",
        )
    return BootstrapResult(
        normalized_url,
        safe_url,
        normalized_branch,
        normalized_depth,
        str(target),
        display,
        int(completed.returncode),
        completed.stdout or "",
        completed.stderr or "",
    )
