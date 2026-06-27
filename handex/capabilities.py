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


MAX_SKILL_CHARS = 24000


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
    allowed = False
    for root in settings.skill_roots:
        try:
            path.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise PermissionError("Skill path is outside configured Handex skill roots")
    return skill, _read_text(path, MAX_SKILL_CHARS)


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
