from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .auth import login_response, logout_response, request_next_url, require_auth, verify_password
from .bootstrap import BootstrapError, bootstrap_workspace_from_git
from .capabilities import list_skills, list_vault_metadata
from .config import settings
from .context import build_context_pack
from .db import (
    add_log,
    create_project,
    delete_project,
    ensure_workspace,
    get_project,
    get_summary,
    import_summary,
    init_db,
    list_logs,
    list_projects,
    list_summaries,
    save_summary,
    update_project_goal,
    update_project,
)
from .history import sanitize_log_for_display
from .parser import parse_llm_reply
from .plugins import list_plugins
from .prompts import (
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_TOOL_PROTOCOL,
    build_agent_fallback_prompt,
    build_correction_prompt,
    build_start_prompt,
    build_summary_prompt,
    build_tool_result_prompt,
    redact_command_string,
    sanitize_command_for_prompt,
)
from .snapshot import build_project_snapshot, dumps_snapshot, imported_project_data, parse_snapshot
from .tools.runner import ToolError, ToolResult, preview_command, registry
from .transcript import build_project_transcript
from .uploads import UploadError, delete_workspace_upload, list_workspace_uploads, save_workspace_upload
from .vault import VaultError, create_item as vault_create_item, delete_item as vault_delete_item, list_items as vault_list_items, vault_enabled


STATIC_DIR = settings.base_dir / "static"
TEMPLATE_DIR = settings.base_dir / "templates"

app = FastAPI(title="Handex", version=__version__)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["app_version"] = __version__


@app.on_event("startup")
def startup() -> None:
    init_db()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def unique_workspace_path(name: str) -> str:
    base = settings.projects_dir / slugify(name)
    if not base.exists():
        return str(base)
    for index in range(2, 1000):
        candidate = settings.projects_dir / f"{base.name}-{index}"
        if not candidate.exists():
            return str(candidate)
    return str(settings.projects_dir / f"{base.name}-imported")


def record_git_bootstrap(project_id: int, workspace: str, repo_url: str, branch: str = "", depth: str = "") -> None:
    try:
        result = bootstrap_workspace_from_git(workspace, repo_url, branch=branch, depth=depth)
    except BootstrapError as exc:
        add_log(project_id, "workspace.git_bootstrap.error", stderr=str(exc))
        return
    add_log(
        project_id,
        "workspace.git_bootstrap",
        final_command=result.command,
        cwd=str(Path(result.workspace).parent),
        exit_code=result.exit_code,
        stdout=result.stdout or (f"Cloned {result.redacted_repo_url} into {result.workspace}\n" if result.exit_code == 0 else ""),
        stderr=result.stderr,
    )


def project_or_404(project_id: int) -> dict[str, Any]:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def project_mode(project: dict[str, Any]) -> str:
    mode = (project.get("settings") or {}).get("mode") or "safe"
    return "yolo" if str(mode).lower() == "yolo" else "safe"


def project_page_context(project: dict[str, Any], **extra: Any) -> dict[str, Any]:
    vault_error = ""
    try:
        vault_credentials = list_vault_metadata()
    except Exception as exc:
        vault_credentials = []
        vault_error = f"{type(exc).__name__}: {exc}"
    context_pack = build_context_pack(project.get("workspace_path") or ".", max_chars=12000)
    summaries = list_summaries(int(project["id"]))
    logs = [sanitize_log_for_display(log) for log in list_logs(int(project["id"]))]
    context = {
        "project": project,
        "start_prompt": build_start_prompt(project),
        "agent_prompt": build_agent_fallback_prompt(project),
        "context_pack": context_pack,
        "transcript_prompt": build_project_transcript(project, summaries, logs, context_pack, max_chars=24000),
        "summary_prompt": build_summary_prompt(project),
        "uploads": list_workspace_uploads(project.get("workspace_path") or "."),
        "max_upload_bytes": settings.max_upload_bytes,
        "summaries": summaries,
        "logs": logs,
        "skills": list_skills(),
        "plugins": list_plugins(),
        "vault_credentials": vault_credentials,
        "vault_error": vault_error,
        "handex_vault_enabled": vault_enabled(),
        "handex_vault_items": vault_list_items(),
        "default_prompt_template": DEFAULT_PROMPT_TEMPLATE,
        "default_tool_protocol": DEFAULT_TOOL_PROTOCOL,
        "tool_names": registry.names(),
        "project_mode": project_mode(project),
    }
    context.update(extra)
    return context


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok\n"


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next_url": request_next_url(request), "error": request.query_params.get("error")},
    )


@app.post("/login")
def login_submit(
    request: Request,
    password: Annotated[str, Form()] = "",
    next_url: Annotated[str, Form()] = "/",
) -> RedirectResponse:
    if verify_password(password):
        return login_response(next_url)
    return redirect(f"/login?error=1&next={next_url}")


@app.post("/logout")
def logout(_: None = Depends(require_auth)) -> RedirectResponse:
    return logout_response()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: None = Depends(require_auth)) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request, "projects": list_projects()})


@app.post("/projects")
def create_project_route(
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    goal: Annotated[str, Form()] = "",
    workspace_path: Annotated[str, Form()] = "",
    git_repo_url: Annotated[str, Form()] = "",
    git_branch: Annotated[str, Form()] = "",
    git_depth: Annotated[str, Form()] = "1",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")
    workspace = workspace_path.strip() or str(settings.projects_dir / slugify(name))
    ensure_workspace(workspace)
    project_id = create_project(
        {
            "name": name,
            "description": description,
            "goal": goal,
            "workspace_path": workspace,
            "mode": "safe",
        }
    )
    if git_repo_url.strip():
        record_git_bootstrap(project_id, workspace, git_repo_url, git_branch, git_depth)
    return redirect(f"/projects/{project_id}")


@app.post("/projects/import")
def import_project_route(
    snapshot_json: Annotated[str, Form()],
    workspace_path: Annotated[str, Form()] = "",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    try:
        snapshot = parse_snapshot(snapshot_json)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source_project = snapshot["project"]
    name = str(source_project.get("name") or "Imported Project")
    workspace = workspace_path.strip() or unique_workspace_path(f"{name}-imported")
    ensure_workspace(workspace)
    project_id = create_project(imported_project_data(snapshot, workspace))
    for summary in reversed([item for item in snapshot.get("summaries", []) if isinstance(item, dict)]):
        import_summary(
            project_id,
            str(summary.get("content") or ""),
            str(summary.get("note") or ""),
            str(summary.get("created_at") or ""),
        )
    for log in reversed([item for item in snapshot.get("logs", []) if isinstance(item, dict)]):
        add_log(
            project_id,
            str(log.get("event_type") or "snapshot.imported_event"),
            mode=str(log.get("mode") or ""),
            command_json=str(log.get("command_json") or ""),
            final_command=str(log.get("final_command") or ""),
            cwd=str(log.get("cwd") or ""),
            exit_code=log.get("exit_code") if isinstance(log.get("exit_code"), int) else None,
            stdout=str(log.get("stdout") or ""),
            stderr=str(log.get("stderr") or ""),
            result_prompt=str(log.get("result_prompt") or ""),
            created_at=str(log.get("created_at") or ""),
        )
    add_log(project_id, "snapshot.import", stdout="Imported Handex project snapshot.")
    return redirect(f"/projects/{project_id}")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int, _: None = Depends(require_auth)) -> HTMLResponse:
    project = project_or_404(project_id)
    ensure_workspace(project["workspace_path"])
    return templates.TemplateResponse("project.html", {"request": request, **project_page_context(project)})


@app.get("/projects/{project_id}/export")
def export_project_route(project_id: int, _: None = Depends(require_auth)) -> Response:
    project = project_or_404(project_id)
    context_pack = build_context_pack(project.get("workspace_path") or ".", max_chars=12000)
    snapshot = build_project_snapshot(project, list_summaries(project_id), list_logs(project_id, limit=200), context_pack)
    filename = f"handex-{slugify(str(project.get('name') or 'project'))}-snapshot.json"
    return Response(
        dumps_snapshot(snapshot),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/projects/{project_id}/settings")
def save_project_settings(
    project_id: int,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    goal: Annotated[str, Form()] = "",
    project_state: Annotated[str, Form()] = "",
    current_summary: Annotated[str, Form()] = "",
    prompt_template: Annotated[str, Form()] = "",
    tool_protocol: Annotated[str, Form()] = "",
    workspace_path: Annotated[str, Form()] = "",
    mode: Annotated[str, Form()] = "safe",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project_or_404(project_id)
    if not name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    workspace = workspace_path.strip() or str(settings.projects_dir / slugify(name))
    ensure_workspace(workspace)
    update_project(
        project_id,
        {
            "name": name.strip(),
            "description": description,
            "goal": goal,
            "project_state": project_state,
            "current_summary": current_summary,
            "prompt_template": prompt_template,
            "tool_protocol": tool_protocol,
            "workspace_path": workspace,
            "mode": mode,
        },
    )
    return redirect(f"/projects/{project_id}#settings")


@app.post("/projects/{project_id}/goal")
def save_project_goal(
    project_id: int,
    goal: Annotated[str, Form()] = "",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project_or_404(project_id)
    update_project_goal(project_id, goal)
    add_log(project_id, "project.goal.update", stdout=goal)
    return redirect(f"/projects/{project_id}#goal")


@app.post("/projects/{project_id}/delete")
def delete_project_route(project_id: int, _: None = Depends(require_auth)) -> RedirectResponse:
    project_or_404(project_id)
    delete_project(project_id)
    return redirect("/")


@app.post("/projects/{project_id}/bootstrap/git")
def bootstrap_project_git_route(
    project_id: int,
    git_repo_url: Annotated[str, Form()],
    git_branch: Annotated[str, Form()] = "",
    git_depth: Annotated[str, Form()] = "1",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project = project_or_404(project_id)
    workspace = str(ensure_workspace(project["workspace_path"]))
    record_git_bootstrap(project_id, workspace, git_repo_url, git_branch, git_depth)
    return redirect(f"/projects/{project_id}#git-bootstrap")


@app.post("/projects/{project_id}/uploads")
async def upload_project_file_route(
    project_id: int,
    file: Annotated[UploadFile, File()],
    target_path: Annotated[str, Form()] = "",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project = project_or_404(project_id)
    workspace = ensure_workspace(project["workspace_path"])
    try:
        info = save_workspace_upload(workspace, file.filename or "upload", file.file, target_path=target_path)
    except UploadError as exc:
        add_log(project_id, "workspace.upload.error", stderr=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()
    add_log(project_id, "workspace.upload", stdout=f"Uploaded {info.path} ({info.size} bytes)")
    return redirect(f"/projects/{project_id}#uploads")


@app.post("/projects/{project_id}/uploads/delete")
def delete_project_upload_route(
    project_id: int,
    upload_path: Annotated[str, Form()],
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project = project_or_404(project_id)
    workspace = ensure_workspace(project["workspace_path"])
    try:
        info = delete_workspace_upload(workspace, upload_path)
    except UploadError as exc:
        add_log(project_id, "workspace.upload.delete.error", stderr=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_log(project_id, "workspace.upload.delete", stdout=f"Deleted {info.path}")
    return redirect(f"/projects/{project_id}#uploads")


@app.post("/projects/{project_id}/vault")
def create_vault_item_route(
    project_id: int,
    label: Annotated[str, Form()],
    kind: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    host: Annotated[str, Form()] = "",
    allowed_origins: Annotated[str, Form()] = "",
    secret: Annotated[str, Form()] = "",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project_or_404(project_id)
    metadata = {
        "host": host.strip(),
        "allowed_origins": [item.strip() for item in allowed_origins.splitlines() if item.strip()],
    }
    try:
        item_id = vault_create_item(label, kind, username, secret, metadata)
        add_log(project_id, "vault.create", stdout=f"Created Handex vault item handex:{item_id} ({label})")
    except VaultError as exc:
        add_log(project_id, "vault.create.error", stderr=str(exc))
    return redirect(f"/projects/{project_id}#capabilities")


@app.post("/projects/{project_id}/vault/{item_id}/delete")
def delete_vault_item_route(project_id: int, item_id: int, _: None = Depends(require_auth)) -> RedirectResponse:
    project_or_404(project_id)
    vault_delete_item(item_id)
    add_log(project_id, "vault.delete", stdout=f"Deleted Handex vault item handex:{item_id}")
    return redirect(f"/projects/{project_id}#capabilities")


@app.get("/projects/{project_id}/prompt/start", response_class=PlainTextResponse)
def start_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    return build_start_prompt(project_or_404(project_id))


@app.get("/projects/{project_id}/prompt/summary", response_class=PlainTextResponse)
def summary_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    return build_summary_prompt(project_or_404(project_id))


@app.get("/projects/{project_id}/prompt/agent", response_class=PlainTextResponse)
def agent_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    return build_agent_fallback_prompt(project_or_404(project_id))


@app.get("/projects/{project_id}/prompt/context", response_class=PlainTextResponse)
def context_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    project = project_or_404(project_id)
    return build_context_pack(project.get("workspace_path") or ".", max_chars=16000)


@app.get("/projects/{project_id}/prompt/transcript", response_class=PlainTextResponse)
def transcript_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    project = project_or_404(project_id)
    context_pack = build_context_pack(project.get("workspace_path") or ".", max_chars=12000)
    return build_project_transcript(project, list_summaries(project_id), list_logs(project_id, limit=80), context_pack, max_chars=32000)


@app.post("/projects/{project_id}/parse", response_class=HTMLResponse)
def parse_reply(
    request: Request,
    project_id: int,
    llm_reply: Annotated[str, Form()] = "",
    _: None = Depends(require_auth),
) -> HTMLResponse:
    project = project_or_404(project_id)
    parse_result = parse_llm_reply(llm_reply)
    choices = []
    for candidate in parse_result.candidates:
        effective_mode = candidate.command.get("mode") or project_mode(project)
        try:
            preview = registry.preview(candidate.command, project["workspace_path"], effective_mode)
            choices.append({"candidate": candidate, "preview": preview, "blocked": False})
        except Exception as exc:
            choices.append(
                {
                    "candidate": candidate,
                    "preview": {
                        "tool": candidate.command.get("tool", ""),
                        "mode": effective_mode,
                        "cwd": "",
                        "final_command": preview_command(candidate.command),
                        "warnings": [f"{type(exc).__name__}: {exc}"],
                        "diff_preview": "",
                    },
                    "blocked": True,
                }
            )
    correction_prompt = ""
    if not choices:
        correction_prompt = build_correction_prompt(project, llm_reply, parse_result.errors)
    context = project_page_context(
        project,
        parse_result=parse_result,
        command_choices=choices,
        correction_prompt=correction_prompt,
        pasted_reply=llm_reply,
    )
    return templates.TemplateResponse("project.html", {"request": request, **context})


@app.post("/projects/{project_id}/execute", response_class=HTMLResponse)
def execute_command(
    request: Request,
    project_id: int,
    command_json: Annotated[str, Form()],
    execution_mode: Annotated[str, Form()] = "safe",
    _: None = Depends(require_auth),
) -> HTMLResponse:
    project = project_or_404(project_id)
    try:
        command = json.loads(command_json)
        if not isinstance(command, dict):
            raise ValueError("Command JSON must be an object")
        result = registry.run(command, project["workspace_path"], execution_mode)
    except Exception as exc:
        try:
            command = json.loads(command_json)
            if not isinstance(command, dict):
                command = {"raw": command}
        except Exception:
            command = {"raw": command_json}
        result = ToolResult(
            tool=str(command.get("tool") or "unknown"),
            command=command,
            mode=execution_mode,
            cwd=str(project["workspace_path"]),
            final_command=preview_command(command) if isinstance(command, dict) else "",
            exit_code=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}\n",
        )

    result_prompt = build_tool_result_prompt(project, result)
    add_log(
        int(project["id"]),
        "tool.execute",
        mode=result.mode,
        command_json=json.dumps(sanitize_command_for_prompt(result.command), ensure_ascii=False, indent=2),
        final_command=redact_command_string(result.final_command),
        cwd=result.cwd,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        result_prompt=result_prompt,
    )
    refreshed = project_or_404(project_id)
    context = project_page_context(refreshed, execution_result=result, result_prompt=result_prompt)
    return templates.TemplateResponse("project.html", {"request": request, **context})


@app.post("/projects/{project_id}/summary")
def save_summary_route(
    project_id: int,
    summary_content: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
    _: None = Depends(require_auth),
) -> RedirectResponse:
    project_or_404(project_id)
    save_summary(project_id, summary_content, note)
    add_log(project_id, "summary.update", stdout=summary_content)
    return redirect(f"/projects/{project_id}#summary")


@app.post("/projects/{project_id}/summary/{summary_id}/rollback")
def rollback_summary_route(project_id: int, summary_id: int, _: None = Depends(require_auth)) -> RedirectResponse:
    project_or_404(project_id)
    summary = get_summary(summary_id)
    if not summary or int(summary["project_id"]) != project_id:
        raise HTTPException(status_code=404, detail="Summary not found")
    save_summary(project_id, summary["content"], note=f"Rollback to summary #{summary_id}")
    add_log(project_id, "summary.rollback", stdout=summary["content"])
    return redirect(f"/projects/{project_id}#history")
