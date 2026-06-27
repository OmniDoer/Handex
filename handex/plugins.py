from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings


PLUGIN_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class CommandPlugin:
    plugin_id: str
    name: str
    description: str
    command: list[str]
    safe: bool
    timeout: int
    path: str
    root: str


def normalize_command(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value.strip():
        return shlex.split(value)
    return []


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def plugin_from_manifest(path: Path, root: Path) -> CommandPlugin | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    plugin_id = str(data.get("id") or data.get("plugin_id") or path.parent.name).strip()
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        return None
    command = normalize_command(data.get("command") or data.get("argv"))
    if not command:
        return None
    try:
        timeout = int(data.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60
    return CommandPlugin(
        plugin_id=plugin_id,
        name=str(data.get("name") or plugin_id),
        description=str(data.get("description") or ""),
        command=command,
        safe=truthy(data.get("safe")),
        timeout=max(1, min(timeout, 900)),
        path=str(path.resolve()),
        root=str(root.resolve()),
    )


def list_plugins() -> list[CommandPlugin]:
    plugins: list[CommandPlugin] = []
    seen: set[str] = set()
    for root in settings.plugin_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("plugin.json")):
            plugin = plugin_from_manifest(path, root)
            if not plugin or plugin.plugin_id in seen:
                continue
            seen.add(plugin.plugin_id)
            plugins.append(plugin)
    return plugins


def find_plugin(identifier: str) -> CommandPlugin:
    wanted = identifier.strip().lower()
    for plugin in list_plugins():
        if wanted in {plugin.plugin_id.lower(), plugin.name.lower()}:
            return plugin
    matches = [plugin for plugin in list_plugins() if wanted and wanted in plugin.plugin_id.lower()]
    if len(matches) == 1:
        return matches[0]
    matches = [plugin for plugin in list_plugins() if wanted and wanted in plugin.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"Plugin not found or ambiguous: {identifier}")


def plugin_argv(plugin: CommandPlugin) -> list[str]:
    manifest_dir = Path(plugin.path).parent
    argv = list(plugin.command)
    first = Path(argv[0]).expanduser()
    if not first.is_absolute() and ("/" in argv[0] or argv[0].startswith(".")):
        argv[0] = str((manifest_dir / first).resolve())
    return argv


def plugin_catalog_prompt() -> str:
    lines = ["Available Handex command plugins:", ""]
    plugins = list_plugins()
    if not plugins:
        lines.append("(No plugins found. Configure HANDEX_PLUGIN_ROOTS to directories containing plugin.json files.)")
    for plugin in plugins:
        safe = "safe" if plugin.safe else "yolo-only"
        lines.append(f"- {plugin.plugin_id} ({plugin.name}, {safe}): {plugin.description or 'No description.'}")
    lines.extend(
        [
            "",
            "To run a plugin, ask Handex for:",
            '{"tool":"plugin_run","args":{"plugin_id":"example","input":{}},"mode":"safe","reason":"run a configured command plugin"}',
        ]
    )
    return "\n".join(lines)
