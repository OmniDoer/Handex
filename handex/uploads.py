from __future__ import annotations

import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .config import settings
from .context import is_secret_like, redact_text


UPLOAD_DIRNAME = ".handex_uploads"
CHUNK_SIZE = 1024 * 1024


class UploadError(Exception):
    pass


@dataclass(frozen=True)
class UploadedFileInfo:
    path: str
    upload_path: str
    name: str
    size: int
    media_type: str
    modified_at: str
    preview: str = ""
    preview_omitted: str = ""


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def upload_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / UPLOAD_DIRNAME


def sanitize_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(" ._-")
    return cleaned or "upload"


def upload_relative_path(original_name: str, target_path: str = "") -> Path:
    original = Path(original_name or "upload").name or "upload"
    raw = (target_path or original).replace("\\", "/").strip()
    if target_path and raw.endswith("/"):
        raw = raw + original
    pure = PurePosixPath(raw or original)
    if pure.is_absolute() or ".." in pure.parts:
        raise UploadError("Upload path must be relative and must not contain '..'")
    parts = []
    for part in pure.parts:
        if part in {"", "."}:
            continue
        parts.append(sanitize_part(part))
    if not parts:
        parts = [sanitize_part(original)]
    return Path(*parts)


def unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem or "upload"
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise UploadError(f"Could not choose a unique upload path for {path.name}")


def save_workspace_upload(workspace: str | Path, original_name: str, source: BinaryIO, target_path: str = "") -> UploadedFileInfo:
    root = upload_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    rel = upload_relative_path(original_name, target_path)
    target = unique_target((root / rel).resolve())
    if not is_relative_to(target, root):
        raise UploadError("Upload path must stay inside the workspace upload directory")
    target.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise UploadError(f"Upload exceeds HANDEX_MAX_UPLOAD_BYTES ({settings.max_upload_bytes} bytes)")
                handle.write(chunk)
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise

    return file_info(root, target)


def is_binary_sample(sample: bytes) -> bool:
    return b"\x00" in sample


def text_preview(path: Path, max_chars: int = 1600) -> tuple[str, str]:
    if is_secret_like(path):
        return "", "preview omitted for secret-looking filename"
    try:
        with path.open("rb") as handle:
            sample = handle.read(4096)
            if is_binary_sample(sample):
                return "", "binary file"
            rest = handle.read(max_chars)
    except OSError as exc:
        return "", f"{type(exc).__name__}: {exc}"
    text = (sample + rest).decode("utf-8", errors="replace")
    redacted = redact_text(text)
    if len(redacted) > max_chars:
        return redacted[:max_chars] + "\n...[upload preview truncated by Handex]...", ""
    return redacted, ""


def file_info(root: Path, path: Path) -> UploadedFileInfo:
    stat = path.stat()
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload_path = str(path.relative_to(root))
    preview, preview_omitted = text_preview(path)
    return UploadedFileInfo(
        path=str(Path(UPLOAD_DIRNAME) / upload_path),
        upload_path=upload_path,
        name=path.name,
        size=stat.st_size,
        media_type=media_type,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        preview=preview,
        preview_omitted=preview_omitted,
    )


def list_workspace_uploads(workspace: str | Path, max_files: int = 200) -> list[UploadedFileInfo]:
    root = upload_root(workspace)
    if not root.exists():
        return []
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda item: str(item.relative_to(root)))
    return [file_info(root, path) for path in files[:max_files]]


def resolve_upload(workspace: str | Path, upload_path: str) -> tuple[Path, Path]:
    root = upload_root(workspace)
    raw = upload_path.replace("\\", "/").strip()
    prefix = f"{UPLOAD_DIRNAME}/"
    if raw == UPLOAD_DIRNAME:
        raw = ""
    elif raw.startswith(prefix):
        raw = raw[len(prefix) :]
    if not raw:
        raise UploadError("Upload path is required")
    rel = upload_relative_path(raw, target_path=raw)
    path = (root / rel).resolve()
    if not is_relative_to(path, root):
        raise UploadError("Upload path must stay inside the workspace upload directory")
    return root, path


def delete_workspace_upload(workspace: str | Path, upload_path: str) -> UploadedFileInfo:
    root, path = resolve_upload(workspace, upload_path)
    if not path.exists() or not path.is_file():
        raise UploadError(f"Upload not found: {upload_path}")
    info = file_info(root, path)
    path.unlink()
    current = path.parent
    while current != root and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
    return info


def upload_inventory_prompt(workspace: str | Path, *, max_files: int = 40, max_chars: int = 6000) -> str:
    uploads = list_workspace_uploads(workspace, max_files=max_files)
    if not uploads:
        return "No uploaded workspace files."
    lines = ["Uploaded workspace files live under .handex_uploads/ and can be inspected with list_uploads or read_file.", ""]
    for item in uploads:
        lines.append(f"- {item.path} ({item.size} bytes, {item.media_type})")
        if item.preview:
            lines.append("  Preview:")
            for line in item.preview.splitlines()[:12]:
                lines.append(f"    {line}")
        elif item.preview_omitted:
            lines.append(f"  Preview omitted: {item.preview_omitted}.")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[uploaded file inventory truncated by Handex]..."
    return text
