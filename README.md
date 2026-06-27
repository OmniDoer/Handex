# Handex

Handex is a Linux Server Human-in-the-Loop Workspace for connecting any web LLM
to local tools through copy and paste. It does not require an LLM API, OpenAI
API, browser automation, MCP, or a specific model vendor.

Supported web LLMs include ChatGPT, DeepSeek, Claude, Gemini, Doubao, Kimi,
Tongyi Qianwen, and any model that can exchange text by copy and paste.

## Architecture

- Python + FastAPI web app
- SQLite project state in `data/handex.db`
- Jinja2 templates with a small mobile-first frontend
- PWA manifest and service worker for installable browser use
- Registry-based tool runner in `handex/tools/runner.py`
- systemd service listening on port `17395`

Handex is not the agent. The web LLM is the agent. Handex maintains durable
project context, prompts, summaries, logs, workspace settings, and tool results.

## Hand Loop

1. User opens Handex and selects a project.
2. Handex generates a compact launch prompt.
3. User copies the prompt into any web LLM.
4. The LLM replies with analysis, a JSON Tool Command, or a Summary.
5. User pastes the entire LLM reply back into Handex.
6. Handex extracts Tool Command JSON from prose, Markdown, or code blocks.
7. User reviews full JSON, final command, cwd, and execution mode.
8. User clicks Execute, Reject, or edits JSON before execution.
9. Linux runs the selected tool.
10. Handex generates a Tool Result Prompt.
11. User copies the Tool Result Prompt back to the web LLM.
12. The loop continues.

## Project Management

Each project stores:

- Project name
- Description
- Goal
- Project state
- Current summary
- Prompt template
- Tool protocol
- Workspace path
- Settings
- Logs
- Summary history

Projects can be created, edited, entered, and deleted from the web UI.

## Prompt Management

The launch prompt combines:

- Project background
- Current goal
- Current summary
- Project state
- Workspace path
- Tool protocol
- Hand Loop rules

Projects may override the default prompt template and tool protocol. Template
variables are documented in `prompts/README.md`.

## Tool Runner

Built-in tools:

- `shell`
- `python`
- `read_file`
- `write_file`
- `append_file`
- `replace_file`
- `delete_file`
- `list_files`
- `search_files`
- `grep`
- `git`

Command schema:

```json
{
  "tool": "shell",
  "args": {"command": "pwd && ls -la"},
  "cwd": ".",
  "mode": "safe",
  "reason": "inspect workspace"
}
```

The runner is plugin-ready through `ToolRegistry`. Future tools can register a
callable that receives the parsed command, resolved workspace, and mode, then
returns a `ToolResult`.

## JSON Correction

Users can paste the full LLM reply, not just JSON. Handex searches Markdown JSON
blocks, normal JSON, JSON surrounded by explanations, multiple JSON blocks, and
wrapped command arrays.

If parsing fails, Handex creates a correction prompt instructing the LLM to
return only valid JSON that matches the Tool Command schema.

## Summary Workflow

The project page provides a Summary Prompt. The user copies it to the web LLM,
pastes the returned Summary into Handex, and saves it. Handex records every
saved Summary as history and supports rollback.

## Safe Mode and YOLO Mode

Safe Mode is the default. It keeps paths and working directories inside the
project workspace and blocks obvious destructive shell or git actions.

YOLO Mode is advanced. It intentionally allows arbitrary shell, arbitrary paths,
root-level actions, Docker, Git, Python, Node, package management, deletion, and
network requests if the server permits them. Handex still never auto-executes:
the user must review full JSON, final command, cwd, and mode, then click
Execute.

## PWA

Handex includes:

- `manifest.webmanifest`
- root-scoped service worker
- installable app icon
- static asset cache
- mobile-first layout for Android, iPhone, and desktop browsers

## Deployment

Install dependencies:

```sh
cd /opt/handex
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run locally without TLS:

```sh
.venv/bin/uvicorn handex.app:app --host 0.0.0.0 --port 17395
```

Install systemd service:

```sh
sudo scripts/install_systemd.sh
```

The installer creates `/etc/handex/handex.env` with a generated
`HANDEX_SECRET_KEY` and `HANDEX_ADMIN_PASSWORD`, installs
`/etc/systemd/system/handex.service`, enables it, and starts it.

If `/etc/letsencrypt/live/482692.xyz/fullchain.pem` and `privkey.pem` exist,
the installer enables direct HTTPS on port `17395`, making the PWA installable
at `https://482692.xyz:17395/` without changing nginx. If those variables are
removed, the same service runs plain HTTP on port `17395`.

Check:

```sh
systemctl status handex.service --no-pager
```

For plain HTTP deployments:

```sh
curl http://127.0.0.1:17395/healthz
```

For the default `482692.xyz` TLS deployment:

```sh
curl --resolve 482692.xyz:17395:127.0.0.1 https://482692.xyz:17395/healthz
```

The generated password is intentionally not committed. Read it directly from
`/etc/handex/handex.env` on the server when administering Handex.

## Repository Layout

```text
handex/
  handex/       FastAPI app, prompts, parser, runner
  templates/    Jinja2 pages
  static/       PWA assets
  projects/     local project workspaces, ignored by git
  runners/      reserved for future runner modules
  prompts/      prompt documentation
  plugins/      plugin documentation
  logs/         runtime logs, ignored by git
  data/         SQLite runtime state, ignored by git
  systemd/      service unit
  scripts/      deployment helpers
  tests/        parser and runner tests
```

## Future Roadmap

- Per-project auth roles
- Richer plugin loading from `plugins/`
- Diff preview for file-write tools
- Streaming command output
- Nginx optional TLS reverse proxy
- Import/export project snapshots
- Workspace Git repository bootstrap helpers
- Offline read-only project cache
