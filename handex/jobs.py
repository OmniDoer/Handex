from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import settings
from .context import redact_text
from .db import (
    create_background_job,
    find_project_by_workspace,
    get_background_job,
    list_background_jobs,
    now_iso,
    update_background_job,
)
from .prompts import redact_command_string, sanitize_command_for_prompt


class JobError(Exception):
    pass


TERMINAL_STATUSES = {"completed", "failed", "stopped", "lost"}
ACTIVE_PROCESSES: dict[int, subprocess.Popen[bytes]] = {}


def job_dir() -> Path:
    path = settings.logs_dir / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_status_to_exit_code(status: int) -> int:
    try:
        return os.waitstatus_to_exitcode(status)
    except AttributeError:
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return status


def compact(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def read_tail(path_value: str, max_chars: int = 12000) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return ""
    max_bytes = max(1024, min(max_chars * 4, 512 * 1024))
    with path.open("rb") as handle:
        try:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
        except OSError:
            handle.seek(0)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    return compact(redact_command_string(redact_text(text)), max_chars)


def output_paths(job_id: int) -> tuple[Path, Path]:
    root = job_dir()
    return root / f"job-{job_id}.stdout.log", root / f"job-{job_id}.stderr.log"


def project_id_for_workspace(workspace: Path) -> int:
    project = find_project_by_workspace(str(workspace))
    if not project:
        raise JobError(f"No Handex project found for workspace: {workspace}")
    return int(project["id"])


def start_background_shell(
    *,
    workspace: Path,
    cwd: Path,
    mode: str,
    command: dict[str, Any],
    command_text: str,
    final_command: str,
) -> dict[str, Any]:
    project_id = project_id_for_workspace(workspace)
    command_json = json.dumps(sanitize_command_for_prompt(command), ensure_ascii=False, indent=2)
    job_id = create_background_job(
        {
            "project_id": project_id,
            "tool": "background_shell",
            "mode": mode,
            "command_json": command_json,
            "final_command": redact_command_string(final_command),
            "cwd": str(cwd),
            "status": "starting",
        }
    )
    stdout_path, stderr_path = output_paths(job_id)
    stdout_handle = stdout_path.open("ab")
    stderr_handle = stderr_path.open("ab")
    try:
        process = subprocess.Popen(
            command_text,
            shell=True,
            cwd=str(cwd),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=False,
            executable="/bin/bash",
            start_new_session=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        update_background_job(
            job_id,
            {
                "status": "failed",
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "updated_at": now_iso(),
                "completed_at": now_iso(),
            },
        )
        raise
    finally:
        stdout_handle.close()
        stderr_handle.close()
    update_background_job(
        job_id,
        {
            "pid": process.pid,
            "status": "running",
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "updated_at": now_iso(),
        },
    )
    ACTIVE_PROCESSES[job_id] = process
    return refresh_job(job_id)


def refresh_job(job_id: int) -> dict[str, Any]:
    job = get_background_job(job_id)
    if not job:
        raise JobError(f"Background job not found: {job_id}")
    status = str(job.get("status") or "")
    pid = job.get("pid")
    if status in {"running", "starting"} and pid:
        exit_code = None
        new_status = status
        completed_at = ""
        process = ACTIVE_PROCESSES.get(job_id)
        if process is not None:
            exit_code = process.poll()
            if exit_code is not None:
                new_status = "completed" if exit_code == 0 else "failed"
                completed_at = now_iso()
                ACTIVE_PROCESSES.pop(job_id, None)
        else:
            try:
                waited_pid, wait_status = os.waitpid(int(pid), os.WNOHANG)
                if waited_pid:
                    exit_code = wait_status_to_exit_code(wait_status)
                    new_status = "completed" if exit_code == 0 else "failed"
                    completed_at = now_iso()
            except ChildProcessError:
                if not pid_exists(int(pid)):
                    new_status = "lost"
                    completed_at = now_iso()
        if new_status != status or completed_at:
            update_background_job(
                job_id,
                {
                    "status": new_status,
                    "exit_code": exit_code,
                    "updated_at": now_iso(),
                    "completed_at": completed_at,
                },
            )
            job = get_background_job(job_id) or job
    return job


def stop_job(job_id: int) -> dict[str, Any]:
    job = refresh_job(job_id)
    if str(job.get("status") or "") in TERMINAL_STATUSES:
        return job
    pid = job.get("pid")
    if not pid:
        update_background_job(job_id, {"status": "lost", "updated_at": now_iso(), "completed_at": now_iso()})
        return get_background_job(job_id) or job
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        update_background_job(job_id, {"status": "lost", "updated_at": now_iso(), "completed_at": now_iso()})
        return get_background_job(job_id) or job
    process = ACTIVE_PROCESSES.get(job_id)
    if process is not None:
        try:
            process.wait(timeout=2)
            ACTIVE_PROCESSES.pop(job_id, None)
            update_background_job(job_id, {"status": "stopped", "exit_code": -15, "updated_at": now_iso(), "completed_at": now_iso()})
            return get_background_job(job_id) or job
        except subprocess.TimeoutExpired:
            pass
    else:
        deadline = time.time() + 2
        while time.time() < deadline:
            if not pid_exists(int(pid)):
                update_background_job(job_id, {"status": "stopped", "exit_code": -15, "updated_at": now_iso(), "completed_at": now_iso()})
                return get_background_job(job_id) or job
            time.sleep(0.1)
    try:
        os.killpg(int(pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    ACTIVE_PROCESSES.pop(job_id, None)
    update_background_job(job_id, {"status": "stopped", "exit_code": -15, "updated_at": now_iso(), "completed_at": now_iso()})
    return get_background_job(job_id) or job


def display_job(job: dict[str, Any], *, max_chars: int = 12000) -> dict[str, Any]:
    refreshed = refresh_job(int(job["id"])) if str(job.get("status") or "") not in TERMINAL_STATUSES else job
    return {
        "id": refreshed.get("id"),
        "project_id": refreshed.get("project_id"),
        "tool": refreshed.get("tool"),
        "mode": refreshed.get("mode"),
        "command_json": refreshed.get("command_json") or "",
        "final_command": redact_command_string(str(refreshed.get("final_command") or "")),
        "cwd": refreshed.get("cwd") or "",
        "pid": refreshed.get("pid"),
        "status": refreshed.get("status") or "",
        "exit_code": refreshed.get("exit_code"),
        "stdout": read_tail(str(refreshed.get("stdout_path") or ""), max_chars=max_chars),
        "stderr": read_tail(str(refreshed.get("stderr_path") or ""), max_chars=max_chars),
        "started_at": refreshed.get("started_at") or "",
        "updated_at": refreshed.get("updated_at") or "",
        "completed_at": refreshed.get("completed_at") or "",
    }


def get_job_display(job_id: int, *, max_chars: int = 12000) -> dict[str, Any]:
    job = refresh_job(job_id)
    return display_job(job, max_chars=max_chars)


def list_project_job_displays(project_id: int, *, limit: int = 20, max_chars: int = 8000) -> list[dict[str, Any]]:
    return [display_job(job, max_chars=max_chars) for job in list_background_jobs(project_id, limit=limit)]
