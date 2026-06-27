from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .auth import login_response, logout_response, request_next_url, require_auth, verify_password
from .config import settings
from .db import (
    add_log,
    create_project,
    delete_project,
    ensure_workspace,
    get_project,
    get_summary,
    init_db,
    list_logs,
    list_projects,
    list_summaries,
    save_summary,
    update_project,
)
from .parser import parse_llm_reply
from .prompts import (
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_TOOL_PROTOCOL,
    build_correction_prompt,
    build_start_prompt,
    build_summary_prompt,
    build_tool_result_prompt,
)
from .tools.runner import ToolError, ToolResult, preview_command, registry


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


def project_or_404(project_id: int) -> dict[str, Any]:
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def project_mode(project: dict[str, Any]) -> str:
    mode = (project.get("settings") or {}).get("mode") or "safe"
    return "yolo" if str(mode).lower() == "yolo" else "safe"


def project_page_context(project: dict[str, Any], **extra: Any) -> dict[str, Any]:
    context = {
        "project": project,
        "start_prompt": build_start_prompt(project),
        "summary_prompt": build_summary_prompt(project),
        "summaries": list_summaries(int(project["id"])),
        "logs": list_logs(int(project["id"])),
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
    return redirect(f"/projects/{project_id}")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int, _: None = Depends(require_auth)) -> HTMLResponse:
    project = project_or_404(project_id)
    ensure_workspace(project["workspace_path"])
    return templates.TemplateResponse("project.html", {"request": request, **project_page_context(project)})


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


@app.post("/projects/{project_id}/delete")
def delete_project_route(project_id: int, _: None = Depends(require_auth)) -> RedirectResponse:
    project_or_404(project_id)
    delete_project(project_id)
    return redirect("/")


@app.get("/projects/{project_id}/prompt/start", response_class=PlainTextResponse)
def start_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    return build_start_prompt(project_or_404(project_id))


@app.get("/projects/{project_id}/prompt/summary", response_class=PlainTextResponse)
def summary_prompt(project_id: int, _: None = Depends(require_auth)) -> str:
    return build_summary_prompt(project_or_404(project_id))


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
        command_json=json.dumps(result.command, ensure_ascii=False, indent=2),
        final_command=result.final_command,
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
