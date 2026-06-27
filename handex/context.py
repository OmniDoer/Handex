from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .config import settings


SKIP_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "data",
    "dist",
    "logs",
    "node_modules",
    "projects",
    "target",
}

SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "vault.json",
}

SECRET_SUFFIXES = {".crt", ".der", ".key", ".kdbx", ".p12", ".pem", ".pfx"}
MANIFEST_NAMES = [
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "Dockerfile",
    "docker-compose.yml",
]

SENSITIVE_LINE_RE = re.compile(
    r"(?i)(password|passwd|passphrase|secret|token|api[_ -]?key|private[_ -]?key|github[_ -]?pat)\s*[:=]\s*\S+"
)
TOKEN_RE = re.compile(
    "("
    + "|".join(
        [
            "ghp" + "_[A-Za-z0-9_]+",
            "github" + "_pat" + "_[A-Za-z0-9_]+",
            "xox[baprs]-[A-Za-z0-9-]+",
        ]
    )
    + ")"
)
PRIVATE_KEY_WORD = "PRIVATE" + " KEY"
PRIVATE_KEY_RE = re.compile(
    "-----BEGIN " + "[A-Z ]*" + PRIVATE_KEY_WORD + "-----.*?-----END " + "[A-Z ]*" + PRIVATE_KEY_WORD + "-----",
    re.DOTALL,
)


def is_secret_like(path: Path) -> bool:
    name = path.name.lower()
    return name in SECRET_FILE_NAMES or path.suffix.lower() in SECRET_SUFFIXES


def redact_text(value: str) -> str:
    redacted = PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", value)
    redacted = TOKEN_RE.sub("[REDACTED TOKEN]", redacted)
    lines = []
    for line in redacted.splitlines():
        if SENSITIVE_LINE_RE.search(line):
            key = line.split(":", 1)[0] if ":" in line else line.split("=", 1)[0]
            lines.append(f"{key.rstrip()}: [REDACTED]")
        else:
            lines.append(line)
    return "\n".join(lines)


def compact(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated by Handex context pack]..."


def safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def run_git(root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=8,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def git_context(root: Path) -> str:
    if run_git(root, ["rev-parse", "--is-inside-work-tree"]) != "true":
        return "Not a Git worktree."
    status = run_git(root, ["status", "--short", "--branch"]) or "(status unavailable)"
    commits = run_git(root, ["log", "--oneline", "--decorate", "--max-count=5"]) or "(no recent commits)"
    return "\n".join(
        [
            "Status:",
            status,
            "",
            "Recent commits:",
            commits,
        ]
    )


def should_skip_dir(dirname: str) -> bool:
    return dirname in SKIP_DIRS


def file_tree(root: Path, max_entries: int = 180) -> tuple[list[str], int]:
    lines: list[str] = []
    redacted = 0
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirname for dirname in dirnames if not should_skip_dir(dirname))
        current = Path(current_root)
        if current != root:
            rel_dir = safe_relative(current, root) + "/"
            lines.append(rel_dir)
            if len(lines) >= max_entries:
                return lines, redacted
        for filename in sorted(filenames):
            path = current / filename
            if is_secret_like(path):
                redacted += 1
                continue
            lines.append(safe_relative(path, root))
            if len(lines) >= max_entries:
                return lines, redacted
    return lines, redacted


def detect_manifests(root: Path) -> list[str]:
    found = []
    for name in MANIFEST_NAMES:
        path = root / name
        if path.exists() and not is_secret_like(path):
            found.append(name)
    return found


def agent_instruction_files(root: Path, max_files: int = 12) -> list[Path]:
    matches = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirname for dirname in dirnames if not should_skip_dir(dirname))
        if "AGENTS.md" not in filenames:
            continue
        matches.append(Path(current_root) / "AGENTS.md")
        if len(matches) >= max_files:
            break
    return matches


def agent_instructions_context(root: Path) -> str:
    files = agent_instruction_files(root)
    if not files:
        return "No AGENTS.md files found inside the workspace."
    sections = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = f"{type(exc).__name__}: {exc}"
        rel = safe_relative(path, root)
        sections.append(f"## {rel}\n{compact(redact_text(text), 6000)}")
    return "\n\n".join(sections)


def build_context_pack(workspace: str | Path, *, max_chars: int | None = None) -> str:
    root = Path(workspace).expanduser().resolve()
    limit = max(3000, min(max_chars or 16000, settings.max_output_chars))
    if not root.exists():
        return f"# Handex Workspace Context Pack\n\nWorkspace does not exist: {root}"
    if not root.is_dir():
        return f"# Handex Workspace Context Pack\n\nWorkspace is not a directory: {root}"

    tree_lines, redacted_count = file_tree(root)
    manifests = detect_manifests(root)
    sections = [
        "# Handex Workspace Context Pack",
        "",
        f"Workspace: {root}",
        "",
        "## Git",
        git_context(root),
        "",
        "## Agent Instructions",
        agent_instructions_context(root),
        "",
        "## Detected Manifests",
        "\n".join(f"- {item}" for item in manifests) if manifests else "- none",
        "",
        "## File Tree",
        "\n".join(tree_lines) if tree_lines else "(empty)",
    ]
    if redacted_count:
        sections.extend(["", f"Secret-looking file names omitted from tree: {redacted_count}"])
    return compact("\n".join(sections), limit)
