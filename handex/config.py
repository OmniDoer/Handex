from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    max_output_chars: int = 20000


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("HANDEX_DATA_DIR", str(BASE_DIR / "data"))).resolve()
    projects_dir = Path(os.environ.get("HANDEX_PROJECTS_DIR", str(BASE_DIR / "projects"))).resolve()
    logs_dir = Path(os.environ.get("HANDEX_LOGS_DIR", str(BASE_DIR / "logs"))).resolve()
    return Settings(
        base_dir=BASE_DIR,
        data_dir=data_dir,
        projects_dir=projects_dir,
        logs_dir=logs_dir,
        host=os.environ.get("HANDEX_HOST", "0.0.0.0"),
        port=int(os.environ.get("HANDEX_PORT", "17395")),
        secret_key=os.environ.get("HANDEX_SECRET_KEY", "dev-only-change-me"),
        admin_password=os.environ.get("HANDEX_ADMIN_PASSWORD", ""),
    )


settings = load_settings()
