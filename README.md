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
- Dynamic skill roots, vault metadata providers, and capability help commands
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
- `apply_patch`
- `list_skills`
- `read_skill`
- `skill_pack`
- `list_vault_credentials`
- `vault_list`
- `vault_run`
- `capability_report`

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

`apply_patch` accepts a unified diff and runs `git apply --check` before
applying it. In Safe Mode, absolute paths and `..` paths are rejected.

## Single-Step Agent Mode

Handex can be used as a manual replacement when an automated coding agent is
unavailable or quota-limited. For Codex or OmniDoer users, the intended mental
model is the same agent loop with one extra copy/paste boundary:

```text
Codex/OmniDoer: model proposes tool -> tool runs -> model sees result
Handex:        copy prompt -> model proposes tool -> paste reply -> approve tool -> copy result back
```

The project page includes a Codex-style Single-Step Prompt that tells any web
LLM how to behave like a coding agent inside the Hand Loop:

- inspect local context through Tool Commands
- produce at most one next Tool Command per turn
- request exact file reads and edits
- apply focused unified diffs through `apply_patch`
- use skills by asking Handex to list/read configured `SKILL.md` files
- view vault credential metadata without exposing secrets
- run reviewed commands with local vault secrets injected through environment variables
- keep summaries durable between web LLM sessions

This mode is not Codex-specific and does not vendor Codex, OmniDoer, or any
private runtime. Handex is a peer framework: it reads compatible capability
sources from configuration at runtime.

### No-Learning-Cost Migration

The migration target is muscle-memory compatibility:

- use the Codex-style prompt as the first message in ChatGPT, Claude, Gemini,
  DeepSeek, Kimi, Doubao, Tongyi, or another web LLM
- paste the full web LLM reply back into Handex; do not manually extract JSON
- review the same surface Codex would have reviewed internally: JSON, command,
  cwd, mode, stdout, stderr, and result prompt
- use familiar tools: `shell`, `python`, `git`, `apply_patch`, file tools,
  skills, and vault-backed command execution
- keep working one step at a time until the Summary is updated

The only new habit is moving text between the web LLM and Handex.

## Skills

Handex skills are dynamic instruction files. A skill is any directory containing
`SKILL.md`; optional front matter can provide `name` and `description`.

Configure roots with:

```sh
HANDEX_SKILL_ROOTS=/opt/handex/skills:/some/other/skills
```

The built-in skill tools are:

- `list_skills`: return skill ids, names, descriptions, and source roots
- `read_skill`: read one configured `SKILL.md` by skill id or unique name
- `skill_pack`: return a compact skill catalog prompt

Handex only reads skills from configured roots. It does not hard-code or commit
the server's current Codex/OmniDoer skills.

## Vault Metadata

Handex has two vault layers.

The first layer is Handex's own encrypted local vault. It stores encrypted
secrets in SQLite and keeps the Fernet key in `HANDEX_VAULT_KEY`, which should
live in `/etc/handex/handex.env` or another deployment secret store, not in git.
The project page can add and delete local vault items. Tool access is explicit:

- `vault_list`: metadata only for local Handex vault items
- `vault_run`: decrypt one selected item and inject it into an environment
  variable for the approved command

`vault_run` redacts direct appearances of the secret value from stdout and
stderr before writing logs or Tool Result prompts. It cannot prevent a malicious
command from transforming a secret before printing it, so the human must still
review the full command before execution.

The second layer is an optional external metadata provider. Handex does not
decrypt or print external credentials by default. Instead, it can call a
configured provider that returns a JSON list of credential records. The provider
output is sanitized down to:

- credential id
- masked username
- allowed origins
- kind
- name
- source
- host

Configure a provider with:

```sh
HANDEX_VAULT_METADATA_COMMAND='your-vault-cli list-metadata --json'
```

The `list_vault_credentials` tool returns only this metadata. Credentialed
operations should still happen through local commands reviewed by the human,
for example a Vault-backed git wrapper.

## Capability Report

`HANDEX_HELP_COMMANDS` can expose local capability help text without coupling
Handex to a specific agent runtime:

```sh
HANDEX_HELP_COMMANDS='codex=codex --help;;omnidoer=omnidoer --help'
```

The `capability_report` tool reports configured skill roots, whether a vault
metadata provider exists, and the help output from those commands.

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

Important runtime configuration:

```sh
HANDEX_SKILL_ROOTS=/opt/handex/skills
HANDEX_VAULT_KEY=<generated-fernet-key>
HANDEX_VAULT_METADATA_COMMAND=
HANDEX_HELP_COMMANDS=
```

These can be changed in `/etc/handex/handex.env`; restart `handex.service`
after edits.

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
  skills/       default dynamic skill root
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
