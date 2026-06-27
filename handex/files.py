from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import is_secret_like


class FileAccessError(Exception):
    pass


@dataclass(frozen=True)
class WorkspaceFileInfo:
    path: Path
    relative_path: str
    name: str
    size: int
    media_type: str


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def resolve_workspace_file(workspace: str | Path, path_value: Any, *, allow_secret: bool = False) -> WorkspaceFileInfo:
    if not path_value:
        raise FileAccessError("File path is required")
    root = Path(workspace).expanduser().resolve()
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not is_relative_to(path, root):
        raise FileAccessError(f"File path must stay inside workspace: {path}")
    if not path.exists() or not path.is_file():
        raise FileAccessError(f"File not found: {path_value}")
    if is_secret_like(path) and not allow_secret:
        raise FileAccessError(f"Secret-looking files cannot be exposed through download URLs: {path.name}")
    return WorkspaceFileInfo(
        path=path,
        relative_path=str(path.relative_to(root)),
        name=path.name,
        size=path.stat().st_size,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


def file_info_payload(info: WorkspaceFileInfo, *, url: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": info.relative_path,
        "name": info.name,
        "media_type": info.media_type,
        "size": info.size,
    }
    if url:
        payload["url"] = url
    return payload
