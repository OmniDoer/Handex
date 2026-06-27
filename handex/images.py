from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ImageError(Exception):
    pass


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    relative_path: str
    media_type: str
    size: int
    width: int | None = None
    height: int | None = None


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def sniff_image_media_type(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
    except OSError as exc:
        raise ImageError(f"{type(exc).__name__}: {exc}") from exc
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    raise ImageError("Unsupported or unrecognized image file")


def png_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) >= 24 and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
    return None, None


def gif_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as handle:
        header = handle.read(10)
    if len(header) >= 10 and header.startswith((b"GIF87a", b"GIF89a")):
        return int.from_bytes(header[6:8], "little"), int.from_bytes(header[8:10], "little")
    return None, None


def bmp_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as handle:
        header = handle.read(26)
    if len(header) >= 26 and header.startswith(b"BM"):
        return int.from_bytes(header[18:22], "little", signed=True), abs(int.from_bytes(header[22:26], "little", signed=True))
    return None, None


def jpeg_dimensions(path: Path) -> tuple[int | None, int | None]:
    data = path.read_bytes()
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    return None, None


def image_dimensions(path: Path, media_type: str) -> tuple[int | None, int | None]:
    try:
        if media_type == "image/png":
            return png_dimensions(path)
        if media_type == "image/gif":
            return gif_dimensions(path)
        if media_type == "image/bmp":
            return bmp_dimensions(path)
        if media_type == "image/jpeg":
            return jpeg_dimensions(path)
    except OSError:
        return None, None
    return None, None


def resolve_workspace_image(workspace: str | Path, path_value: Any) -> ImageInfo:
    if not path_value:
        raise ImageError("Image path is required")
    root = Path(workspace).expanduser().resolve()
    path = Path(str(path_value)).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not is_relative_to(path, root):
        raise ImageError(f"Image path must stay inside workspace: {path}")
    if not path.exists() or not path.is_file():
        raise ImageError(f"Image not found: {path_value}")
    media_type = sniff_image_media_type(path)
    width, height = image_dimensions(path, media_type)
    return ImageInfo(
        path=path,
        relative_path=str(path.relative_to(root)),
        media_type=media_type,
        size=path.stat().st_size,
        width=width,
        height=height,
    )


def image_info_payload(info: ImageInfo, *, url: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": info.relative_path,
        "media_type": info.media_type,
        "size": info.size,
        "width": info.width,
        "height": info.height,
    }
    if url:
        payload["url"] = url
    return payload
