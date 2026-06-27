from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


DB_PATH = settings.data_dir / "handex.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                project_state TEXT NOT NULL DEFAULT '',
                current_summary TEXT NOT NULL DEFAULT '',
                prompt_template TEXT NOT NULL DEFAULT '',
                tool_protocol TEXT NOT NULL DEFAULT '',
                workspace_path TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS summary_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT '',
                command_json TEXT NOT NULL DEFAULT '',
                final_command TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                exit_code INTEGER,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                result_prompt TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS vault_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                secret_encrypted TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if "settings_json" in data:
        try:
            data["settings"] = json.loads(data.get("settings_json") or "{}")
        except json.JSONDecodeError:
            data["settings"] = {}
    return data


def list_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC").fetchall()
    return [row_dict(row) for row in rows if row is not None]


def get_project(project_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_dict(row)


def create_project(data: dict[str, Any]) -> int:
    timestamp = now_iso()
    project_settings = {"mode": data.get("mode") or "safe"}
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO projects (
                name, description, goal, project_state, current_summary,
                prompt_template, tool_protocol, workspace_path, settings_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data.get("description", ""),
                data.get("goal", ""),
                data.get("project_state", ""),
                data.get("current_summary", ""),
                data.get("prompt_template", ""),
                data.get("tool_protocol", ""),
                data["workspace_path"],
                json.dumps(project_settings, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def update_project(project_id: int, data: dict[str, Any]) -> None:
    timestamp = now_iso()
    settings_json = json.dumps({"mode": data.get("mode") or "safe"}, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """
            UPDATE projects
            SET name = ?, description = ?, goal = ?, project_state = ?,
                current_summary = ?, prompt_template = ?, tool_protocol = ?,
                workspace_path = ?, settings_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                data["name"],
                data.get("description", ""),
                data.get("goal", ""),
                data.get("project_state", ""),
                data.get("current_summary", ""),
                data.get("prompt_template", ""),
                data.get("tool_protocol", ""),
                data["workspace_path"],
                settings_json,
                timestamp,
                project_id,
            ),
        )


def update_project_goal(project_id: int, goal: str) -> None:
    timestamp = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET goal = ?, updated_at = ? WHERE id = ?",
            (goal, timestamp, project_id),
        )


def delete_project(project_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def save_summary(project_id: int, content: str, note: str = "") -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO summary_history (project_id, content, note, created_at) VALUES (?, ?, ?, ?)",
            (project_id, content, note, timestamp),
        )
        conn.execute(
            "UPDATE projects SET current_summary = ?, updated_at = ? WHERE id = ?",
            (content, timestamp, project_id),
        )
        return int(cursor.lastrowid)


def import_summary(project_id: int, content: str, note: str = "", created_at: str = "") -> int:
    timestamp = created_at or now_iso()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO summary_history (project_id, content, note, created_at) VALUES (?, ?, ?, ?)",
            (project_id, content, note, timestamp),
        )
        return int(cursor.lastrowid)


def list_summaries(project_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM summary_history WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_summary(summary_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM summary_history WHERE id = ?", (summary_id,)).fetchone()
    return dict(row) if row else None


def add_log(
    project_id: int,
    event_type: str,
    *,
    mode: str = "",
    command_json: str = "",
    final_command: str = "",
    cwd: str = "",
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    result_prompt: str = "",
    created_at: str = "",
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO logs (
                project_id, event_type, mode, command_json, final_command, cwd,
                exit_code, stdout, stderr, result_prompt, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                event_type,
                mode,
                command_json,
                final_command,
                cwd,
                exit_code,
                stdout,
                stderr,
                result_prompt,
                created_at or now_iso(),
            ),
        )
        return int(cursor.lastrowid)


def list_logs(project_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE project_id = ? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def create_vault_item(data: dict[str, Any]) -> int:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO vault_items (
                label, kind, username, secret_encrypted, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["label"],
                data.get("kind", ""),
                data.get("username", ""),
                data["secret_encrypted"],
                json.dumps(data.get("metadata") or {}, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def list_vault_items() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, label, kind, username, metadata_json, created_at, updated_at
            FROM vault_items
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        result.append(item)
    return result


def get_vault_item(item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM vault_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        item["metadata"] = {}
    return item


def delete_vault_item(item_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM vault_items WHERE id = ?", (item_id,))


def ensure_workspace(path: str) -> Path:
    workspace = Path(path).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
