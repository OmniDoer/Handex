from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .context import is_secret_like, redact_text


MAX_SKILL_CHARS = 24000


BUILTIN_TOOL_CATALOG: list[dict[str, str]] = [
    {"id": "shell", "description": "Run a reviewed shell command in the workspace."},
    {"id": "background_shell", "description": "Start a long-running shell command as a background job."},
    {"id": "python", "description": "Run reviewed Python code in the workspace."},
    {"id": "read_file", "description": "Read a workspace text file."},
    {"id": "write_file", "description": "Write a workspace file with reviewed content."},
    {"id": "append_file", "description": "Append reviewed content to a workspace file."},
    {"id": "replace_file", "description": "Replace exact text in a workspace file."},
    {"id": "delete_file", "description": "Delete a reviewed workspace file."},
    {"id": "list_files", "description": "List files under a workspace path."},
    {"id": "search_files", "description": "Find workspace files by name pattern."},
    {"id": "grep", "description": "Search workspace text with a regular expression."},
    {"id": "git", "description": "Run reviewed git commands, read-only in Safe Mode unless approved through another tool."},
    {"id": "git_bootstrap", "description": "Clone a repository into an empty workspace."},
    {"id": "apply_patch", "description": "Apply a unified diff or Codex-style patch block."},
    {"id": "list_skills", "description": "List configured Handex skills."},
    {"id": "read_skill", "description": "Read one configured SKILL.md instruction file."},
    {"id": "read_skill_file", "description": "Read a referenced text file inside a configured skill directory."},
    {"id": "skill_pack", "description": "Return a compact prompt catalog of configured skills."},
    {"id": "list_vault_credentials", "description": "List external vault credential metadata without secrets."},
    {"id": "vault_list", "description": "List local Handex vault item metadata without secrets."},
    {"id": "vault_run", "description": "Run a reviewed command with one local Handex vault secret injected into an environment variable."},
    {"id": "omnidoer_credential_request", "description": "Create an OmniDoer Control Client credential request without exposing the secret to Handex."},
    {"id": "omnidoer_credential_save_request", "description": "Store a fulfilled OmniDoer credential request into the configured OmniDoer vault."},
    {"id": "omnidoer_request_status", "description": "Read public metadata for OmniDoer Control Client requests."},
    {"id": "omnidoer_request_wait", "description": "Wait briefly for an OmniDoer Control Client request to complete."},
    {"id": "omnidoer_request_deny", "description": "Deny or cancel an OmniDoer Control Client request."},
    {"id": "omnidoer_task_submit", "description": "Submit a task to the OmniDoer Control Client queue for phone-side or paired-client handling."},
    {"id": "omnidoer_task_list", "description": "List or filter public OmniDoer Control Client task queue metadata."},
    {"id": "omnidoer_task_complete", "description": "Mark a reviewed OmniDoer Control Client task as completed."},
    {"id": "omnidoer_task_cancel", "description": "Cancel a stale or no-longer-needed OmniDoer Control Client task."},
    {"id": "omnidoer_chat_messages", "description": "List or filter public OmniDoer Control Client chat messages."},
    {"id": "omnidoer_chat_next", "description": "Inspect the next OmniDoer chat message without claiming it in Safe Mode."},
    {"id": "omnidoer_chat_send", "description": "Send reviewed chat text through the OmniDoer Control Client bridge."},
    {"id": "omnidoer_chat_reply", "description": "Reply to an OmniDoer Control Client chat message."},
    {"id": "omnidoer_chat_log_user", "description": "Record a reviewed user chat message in OmniDoer chat history."},
    {"id": "omnidoer_chat_start", "description": "Start a streaming OmniDoer assistant chat message."},
    {"id": "omnidoer_chat_delta", "description": "Append a reviewed delta to a streaming OmniDoer chat message."},
    {"id": "omnidoer_chat_complete", "description": "Complete a streaming OmniDoer chat message."},
    {"id": "omnidoer_chat_record", "description": "Record a typed OmniDoer chat event for audit or transcript continuity."},
    {"id": "omnidoer_git", "description": "Run Git through OmniDoer's vault-backed HTTPS credential bridge."},
    {"id": "omnidoer_github_api", "description": "Call the GitHub API through OmniDoer's vault-backed credential bridge."},
    {"id": "capability_report", "description": "Show configured skill roots, plugin roots, vault metadata provider, and help command output."},
    {"id": "capability_search", "description": "Search tools, skills, plugins, vault metadata, and help command labels by keyword."},
    {"id": "context_pack", "description": "Build a Codex-style workspace context snapshot."},
    {"id": "list_uploads", "description": "List uploaded workspace files with metadata and redacted previews."},
    {"id": "download_file", "description": "Return an authenticated download URL for a workspace artifact."},
    {"id": "view_image", "description": "Return metadata and an authenticated preview URL for a workspace image."},
    {"id": "recent_results", "description": "Recover recent Tool Result records from the project history."},
    {"id": "tool_batch", "description": "Run multiple independent read-only tool inspections in one reviewed step."},
    {"id": "update_plan", "description": "Update the visible project plan."},
    {"id": "plan_status", "description": "Read the visible project plan."},
    {"id": "job_status", "description": "Poll one or more background jobs."},
    {"id": "job_stop", "description": "Stop a background job that belongs to the current project."},
    {"id": "plugin_list", "description": "List configured command plugins."},
    {"id": "plugin_run", "description": "Run one configured command plugin with JSON input."},
]


@dataclass
class SkillInfo:
    skill_id: str
    name: str
    description: str
    root: str
    path: str


def _read_text(path: Path, limit: int | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if limit and len(text) > limit:
        return text[:limit] + "\n...[truncated by Handex]..."
    return text


def _front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def _heading_name(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _first_paragraph(text: str) -> str:
    cleaned = re.sub(r"^---.*?---", "", text, count=1, flags=re.DOTALL).strip()
    paragraphs = [item.strip().replace("\n", " ") for item in cleaned.split("\n\n") if item.strip()]
    for paragraph in paragraphs:
        if not paragraph.startswith("#"):
            return paragraph[:500]
    return ""


def _skill_id(root_index: int, root: Path, path: Path) -> str:
    relative = str(path.relative_to(root).with_suffix(""))
    if relative.endswith("/SKILL"):
        relative = relative[: -len("/SKILL")]
    return f"root{root_index}:{relative}"


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def list_skills() -> list[SkillInfo]:
    skills: list[SkillInfo] = []
    for index, root in enumerate(settings.skill_roots, start=1):
        if not root.exists():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            text = _read_text(path, 8000)
            metadata = _front_matter(text)
            fallback_name = path.parent.name
            skills.append(
                SkillInfo(
                    skill_id=_skill_id(index, root, path),
                    name=metadata.get("name") or _heading_name(text, fallback_name),
                    description=metadata.get("description") or _first_paragraph(text),
                    root=str(root),
                    path=str(path),
                )
            )
    return skills


def _find_skill(identifier: str) -> SkillInfo | None:
    wanted = identifier.strip().lower()
    all_skills = list_skills()
    for skill in all_skills:
        if wanted in {skill.skill_id.lower(), skill.name.lower()}:
            return skill
    matches = [skill for skill in all_skills if wanted and wanted in skill.skill_id.lower()]
    if len(matches) == 1:
        return matches[0]
    matches = [skill for skill in all_skills if wanted and wanted in skill.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def read_skill(identifier: str) -> tuple[SkillInfo, str]:
    skill = _find_skill(identifier)
    if not skill:
        raise KeyError(f"Skill not found or ambiguous: {identifier}")
    path = Path(skill.path).resolve()
    if path.name != "SKILL.md":
        raise PermissionError("Only SKILL.md files can be read through the skill bridge")
    if not any(_path_is_relative_to(path, root) for root in settings.skill_roots):
        raise PermissionError("Skill path is outside configured Handex skill roots")
    return skill, _read_text(path, MAX_SKILL_CHARS)


def read_skill_file(identifier: str, relative_path: str, limit: int = MAX_SKILL_CHARS) -> tuple[SkillInfo, str, str]:
    skill = _find_skill(identifier)
    if not skill:
        raise KeyError(f"Skill not found or ambiguous: {identifier}")
    skill_file = Path(skill.path).resolve()
    skill_dir = skill_file.parent
    requested = Path(str(relative_path or "").replace("\\", "/"))
    if not str(relative_path or "").strip():
        raise ValueError("Skill file path is required")
    if requested.is_absolute():
        raise PermissionError("Skill file path must be relative to the skill directory")
    path = (skill_dir / requested).resolve()
    if not _path_is_relative_to(path, skill_dir):
        raise PermissionError("Skill file path must stay inside the selected skill directory")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Skill file not found: {relative_path}")
    if is_secret_like(path):
        raise PermissionError(f"Secret-looking skill files cannot be read through read_skill_file: {path.name}")
    try:
        raw_limit = int(limit or MAX_SKILL_CHARS)
    except (TypeError, ValueError):
        raw_limit = MAX_SKILL_CHARS
    parsed_limit = max(1000, min(raw_limit, MAX_SKILL_CHARS))
    return skill, str(path.relative_to(skill_dir)), redact_text(_read_text(path, parsed_limit))


def skill_pack_prompt() -> str:
    lines = ["Available Handex skills from configured skill roots:", ""]
    skills = list_skills()
    if not skills:
        lines.append("(No skills found. Configure HANDEX_SKILL_ROOTS to one or more directories containing SKILL.md files.)")
    for skill in skills:
        lines.append(f"- {skill.skill_id} ({skill.name}): {skill.description or 'No description.'}")
    lines.extend(
        [
            "",
            "Use a skill only when it directly applies. If you need full instructions, ask Handex for:",
            '{"tool":"read_skill","args":{"skill_id":"root1:example-skill"},"reason":"load relevant skill instructions"}',
            "If SKILL.md references a relative file, ask Handex for:",
            '{"tool":"read_skill_file","args":{"skill_id":"root1:example-skill","path":"references/details.md"},"reason":"load referenced skill material"}',
        ]
    )
    return "\n".join(lines)


def list_vault_metadata() -> list[dict[str, Any]]:
    if not settings.vault_metadata_command:
        return []
    command = shlex.split(settings.vault_metadata_command)
    if not command:
        return []
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}}
    completed = subprocess.run(command, text=True, capture_output=True, timeout=20, env=env)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Vault metadata command failed").strip())
    data = json.loads(completed.stdout or "[]")
    if not isinstance(data, list):
        raise ValueError("Vault metadata command must return a JSON list")
    return [sanitize_vault_item(item) for item in data if isinstance(item, dict)]


def sanitize_vault_item(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    allowed_origins = item.get("allowed_origins") if isinstance(item.get("allowed_origins"), list) else []
    return {
        "credential_id": item.get("credential_id") or item.get("id") or "",
        "username": item.get("username") or "",
        "allowed_origins": allowed_origins,
        "kind": metadata.get("kind") or item.get("kind") or "",
        "name": metadata.get("name") or item.get("name") or "",
        "source": metadata.get("source") or item.get("source") or "",
        "host": metadata.get("host") or item.get("host") or "",
    }


def _search_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_.:-]+", query.lower()) if token]


def _entry_text(entry: dict[str, Any]) -> str:
    parts = []
    for key in ("type", "id", "name", "description", "kind", "source", "host", "root"):
        value = entry.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _score_entry(query: str, entry: dict[str, Any]) -> int:
    tokens = _search_tokens(query)
    if not tokens:
        return 1
    text = _entry_text(entry)
    name = str(entry.get("name") or "").lower()
    identifier = str(entry.get("id") or "").lower()
    score = 0
    normalized_query = query.strip().lower()
    if normalized_query and normalized_query in text:
        score += 5
    for token in tokens:
        if token == identifier or token == name:
            score += 8
        elif identifier.startswith(token) or name.startswith(token):
            score += 4
        elif token in text:
            score += 2
    return score


def _result_entry(entry_type: str, identifier: str, name: str, description: str, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": entry_type,
        "id": identifier,
        "name": name,
        "description": description,
    }
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            entry[key] = value
    return entry


def capability_entries() -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for tool in BUILTIN_TOOL_CATALOG:
        identifier = tool["id"]
        entries.append(
            _result_entry(
                "tool",
                identifier,
                identifier,
                tool["description"],
                next_tool=identifier,
            )
        )
    for skill in list_skills():
        entries.append(
            _result_entry(
                "skill",
                skill.skill_id,
                skill.name,
                skill.description or "Configured Handex skill.",
                root=skill.root,
                next_tool="read_skill",
            )
        )
    try:
        from .plugins import list_plugins

        for plugin in list_plugins():
            entries.append(
                _result_entry(
                    "plugin",
                    plugin.plugin_id,
                    plugin.name,
                    plugin.description or "Configured Handex command plugin.",
                    safe=plugin.safe,
                    timeout=plugin.timeout,
                    root=plugin.root,
                    next_tool="plugin_run",
                )
            )
    except Exception as exc:
        errors.append(f"plugins: {type(exc).__name__}: {exc}")
    try:
        for item in list_vault_metadata():
            identifier = str(item.get("credential_id") or "")
            name = str(item.get("name") or identifier or "vault credential")
            description_parts = [
                str(item.get("kind") or ""),
                str(item.get("source") or ""),
                str(item.get("host") or ""),
                str(item.get("username") or ""),
            ]
            entries.append(
                _result_entry(
                    "vault_credential",
                    identifier,
                    name,
                    " ".join(part for part in description_parts if part) or "External vault credential metadata.",
                    username=item.get("username") or "",
                    kind=item.get("kind") or "",
                    source=item.get("source") or "",
                    host=item.get("host") or "",
                    next_tool="list_vault_credentials",
                )
            )
    except Exception as exc:
        errors.append(f"vault_metadata: {type(exc).__name__}: {exc}")
    for label, _command in settings.help_commands:
        entries.append(
            _result_entry(
                "help_command",
                label,
                label,
                "Configured local capability help command. Use capability_report to inspect its output.",
                next_tool="capability_report",
            )
        )
    return entries, errors


def search_capabilities(query: str, limit: int = 12) -> dict[str, Any]:
    try:
        parsed_limit = int(limit or 12)
    except (TypeError, ValueError):
        parsed_limit = 12
    limit = max(1, min(parsed_limit, 50))
    entries, errors = capability_entries()
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        score = _score_entry(query, entry)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("type") or ""), str(item[1].get("id") or "")))
    results = []
    for score, entry in scored[:limit]:
        item = dict(entry)
        item["score"] = score
        results.append(item)
    return {
        "query": query,
        "limit": limit,
        "total_matches": len(scored),
        "results": results,
        "errors": errors,
    }


def configured_capability_report() -> str:
    sections = ["# Handex Capability Report", ""]
    sections.append("## Skill Roots")
    if settings.skill_roots:
        sections.extend(f"- {root}" for root in settings.skill_roots)
    else:
        sections.append("- none")
    sections.append("")
    sections.append("## Plugin Roots")
    plugin_roots = getattr(settings, "plugin_roots", [])
    if plugin_roots:
        sections.extend(f"- {root}" for root in plugin_roots)
    else:
        sections.append("- none")
    sections.append("")
    sections.append("## Vault Metadata Provider")
    sections.append("- configured" if settings.vault_metadata_command else "- not configured")
    sections.append("")
    sections.append("## OmniDoer Vault Bridge")
    omnidoer_vault_path = getattr(settings, "omnidoer_vault_path", "")
    omnidoer_vault_passphrase_file = getattr(settings, "omnidoer_vault_passphrase_file", "")
    if omnidoer_vault_path and omnidoer_vault_passphrase_file:
        sections.append(f"- vault: {omnidoer_vault_path}")
        sections.append("- passphrase file: configured")
        sections.append(f"- git origin: {getattr(settings, 'omnidoer_git_origin', 'https://github.com')}")
        sections.append(f"- github api origin: {getattr(settings, 'omnidoer_github_api_origin', 'https://api.github.com')}")
    else:
        sections.append("- not configured")
    sections.append("")
    sections.append("## Extra Help Commands")
    if not settings.help_commands:
        sections.append("- none")
    for label, command in settings.help_commands:
        sections.append(f"### {label}")
        try:
            completed = subprocess.run(shlex.split(command), text=True, capture_output=True, timeout=10)
            output = completed.stdout if completed.returncode == 0 else completed.stderr
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
        sections.append(output.strip()[:6000])
    return "\n".join(sections)
