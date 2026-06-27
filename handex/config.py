from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    projects_dir: Path
    logs_dir: Path
    host: str
    port: int
    secret_key: str
    admin_password: str
    skill_roots: List[Path]
    plugin_roots: List[Path]
    vault_metadata_command: str
    help_commands: List[Tuple[str, str]]
    vault_key: str
    max_output_chars: int = 20000
    max_upload_bytes: int = 25 * 1024 * 1024


def _path_list(value: str, default: list[Path]) -> list[Path]:
    if not value.strip():
        return default
    roots = []
    for item in value.split(":"):
        item = item.strip()
        if item:
            roots.append(Path(item).expanduser().resolve())
    return roots or default


def _help_commands(value: str) -> list[tuple[str, str]]:
    commands = []
    for item in value.split(";;"):
        if not item.strip():
            continue
        if "=" in item:
            label, command = item.split("=", 1)
        else:
            label, command = item, item
        commands.append((label.strip(), command.strip()))
    return commands


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("HANDEX_DATA_DIR", str(BASE_DIR / "data"))).resolve()
    projects_dir = Path(os.environ.get("HANDEX_PROJECTS_DIR", str(BASE_DIR / "projects"))).resolve()
    logs_dir = Path(os.environ.get("HANDEX_LOGS_DIR", str(BASE_DIR / "logs"))).resolve()
    default_skill_roots = [(BASE_DIR / "skills").resolve()]
    default_plugin_roots = [(BASE_DIR / "plugins").resolve()]
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        projects_dir=projects_dir,
        logs_dir=logs_dir,
        host=os.environ.get("HANDEX_HOST", "0.0.0.0"),
        port=int(os.environ.get("HANDEX_PORT", "17395")),
        secret_key=os.environ.get("HANDEX_SECRET_KEY", "dev-only-change-me"),
        admin_password=os.environ.get("HANDEX_ADMIN_PASSWORD", ""),
        skill_roots=_path_list(os.environ.get("HANDEX_SKILL_ROOTS", ""), default_skill_roots),
        plugin_roots=_path_list(os.environ.get("HANDEX_PLUGIN_ROOTS", ""), default_plugin_roots),
        vault_metadata_command=os.environ.get("HANDEX_VAULT_METADATA_COMMAND", ""),
        help_commands=_help_commands(os.environ.get("HANDEX_HELP_COMMANDS", "")),
        vault_key=os.environ.get("HANDEX_VAULT_KEY", ""),
        max_upload_bytes=int(os.environ.get("HANDEX_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))),
    )


settings = load_settings()
