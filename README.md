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
- Current plan
- Current summary
- Prompt template
- Tool protocol
- Workspace path
- Settings
- Logs
- Summary history
- Continuation transcript for resuming with another web LLM
- Redacted JSON snapshot export/import
- Workspace file and image uploads under `.handex_uploads/`

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
- `background_shell`
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
- `omnidoer_git`
- `omnidoer_github_api`
- `git_bootstrap`
- `apply_patch`
- `list_skills`
- `read_skill`
- `read_skill_file`
- `skill_pack`
- `list_vault_credentials`
- `vault_list`
- `vault_run`
- `omnidoer_credential_request`
- `omnidoer_credential_list`
- `omnidoer_vault_unlock`
- `omnidoer_credential_save_request`
- `omnidoer_request_status`
- `omnidoer_request_wait`
- `omnidoer_request_deny`
- `omnidoer_task_submit`
- `omnidoer_task_list`
- `omnidoer_task_complete`
- `omnidoer_task_cancel`
- `omnidoer_chat_messages`
- `omnidoer_chat_next`
- `omnidoer_chat_send`
- `omnidoer_chat_reply`
- `omnidoer_chat_log_user`
- `omnidoer_chat_start`
- `omnidoer_chat_delta`
- `omnidoer_chat_complete`
- `omnidoer_chat_record`
- `omnidoer_doctor`
- `omnidoer_control_status`
- `omnidoer_control_devices`
- `omnidoer_control_sessions`
- `omnidoer_control_tunnel_info`
- `omnidoer_control_security_status`
- `omnidoer_control_sync_status`
- `omnidoer_control_revoke_device`
- `omnidoer_control_revoke_session`
- `omnidoer_control_enable_sync`
- `omnidoer_request_challenge`
- `omnidoer_request_takeover`
- `omnidoer_request_release`
- `omnidoer_audit_tail`
- `omnidoer_audit_verify`
- `omnidoer_policy_test`
- `omnidoer_telegram_status`
- `omnidoer_console_dry_run`
- `omnidoer_upgrade_dry_run`
- `omnidoer_mcp_self_test`
- `omnidoer_browser_open`
- `capability_report`
- `capability_search`
- `context_pack`
- `list_uploads`
- `download_file`
- `view_image`
- `recent_results`
- `tool_batch`
- `update_plan`
- `plan_status`
- `job_status`
- `job_stop`
- `plugin_list`
- `plugin_run`

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

`apply_patch` accepts both unified diffs and Codex-style
`*** Begin Patch` blocks. Unified diffs run through `git apply --check` before
applying; Codex-style blocks are parsed and checked before any file write so a
failed later hunk does not leave a partial edit. In Safe Mode, absolute paths
and `..` paths are rejected.

Before execution, Handex shows a unified Diff Preview for file-changing tools:
`write_file`, `append_file`, `replace_file`, `delete_file`, and `apply_patch`.
The preview is generated without writing files, so the human can review the
same kind of patch surface they would normally inspect in a coding agent before
clicking Execute.

`context_pack` returns a Codex-style workspace orientation snapshot: Git status,
recent commits, inherited and workspace `AGENTS.md` instructions, top-level
manifests, and a bounded file tree. Secret-looking files are omitted from the
tree, and secret-looking lines in instruction files are redacted before the pack
is shown or copied to a web LLM.

`capability_search` is a lightweight Codex `tool_search` equivalent for the
manual loop. It searches built-in tools, configured skills, command plugins,
vault credential metadata, and configured help command labels, then returns the
matching next tool to use. It is read-only and safe to run before deciding
whether the task needs a skill, plugin, vault credential, or ordinary tool.

`list_uploads` returns metadata and redacted text previews for files uploaded
through the project page. Uploaded files live under `.handex_uploads/` inside
the workspace, so normal file tools can read, search, patch, or delete them
after review.

`download_file` returns metadata and an authenticated Handex download URL for a
workspace file. It is intended for generated artifacts such as PDFs, archives,
CSVs, model outputs, or logs that are too large or binary for `read_file`.
Secret-looking filenames such as `.env`, private keys, and certificate/key files
are blocked by default.

`view_image` verifies a workspace raster image, returns type/size/dimensions,
and provides an authenticated Handex preview URL. It is intended for uploaded
screenshots, generated figures, and local image artifacts. If the web LLM needs
visual reasoning, the human should open the preview and upload or show the image
to that model through its normal image interface.

`recent_results` returns sanitized recent execution history for the current
workspace, including command JSON, final command, stdout, stderr, and optionally
the full Tool Result Prompt. This is useful when a browser refresh, missed copy,
or model switch interrupts the manual loop.

`tool_batch` runs multiple reviewed child Tool Commands in one Tool Result.
Safe Mode batches are limited to read-only inspection tools and read-only git
subcommands such as `status`, `log`, `show`, and `diff`; they cannot write
files, run shell commands, or start background jobs. This mirrors the common
Codex pattern of parallel file reads while preserving a single human approval
step.

`update_plan` replaces the current visible project plan with reviewed steps and
statuses (`pending`, `in_progress`, or `completed`). `plan_status` returns the
same plan as JSON. This mirrors the Codex planning surface for multi-step work
while keeping plan changes durable inside the Handex project.

`background_shell` starts a reviewed shell command in the background and returns
a job id immediately. `job_status` polls the job status plus redacted stdout and
stderr tails; `job_stop` terminates a running job. Use this for tests, builds,
downloads, or other commands that may outlive a normal web request.

`git_bootstrap` clones a Git repository into an empty project workspace without
shell interpolation. Repository URLs with embedded credentials are rejected;
private clone flows should use reviewed Vault-backed commands instead of
pasting tokens into URLs.

`omnidoer_git` and `omnidoer_github_api` call OmniDoer's vault-backed Git and
GitHub API bridges through argv, not shell interpolation. They use the
server-configured vault path and passphrase file, pass only credential metadata
such as `credential_id`, and run with a minimal environment that does not
inherit Handex secret variables. Safe Mode permits only `git ls-remote` and
GitHub `GET`; mutating Git or GitHub operations require YOLO Mode after human
review.

`omnidoer_credential_request` creates a pending OmniDoer Control Client
credential request when a needed credential does not exist yet. It returns only
public request metadata such as `request_id`, origin, expiry, and status; the
user enters the secret in the paired Control Client, encrypted to OmniDoer, not
through Handex or the web LLM. Use `omnidoer_request_status` or
`omnidoer_request_wait` to observe public completion state, and
`omnidoer_credential_save_request` to store a fulfilled request into the
configured OmniDoer vault. `omnidoer_request_deny` cancels a stale request.

`omnidoer_credential_list` lists configured OmniDoer vault credential metadata,
and `omnidoer_vault_unlock` verifies the configured vault/passphrase file
without returning the passphrase.

`omnidoer_task_submit`, `omnidoer_task_list`, `omnidoer_task_complete`, and
`omnidoer_task_cancel` bridge OmniDoer's Control Client task queue. They are
for reviewed coordination with a paired client, for example handing off a
manual check or inspecting phone-submitted task state. Task text is not a secret
transport; credentials should use the credential request tools instead.

`omnidoer_chat_messages`, `omnidoer_chat_next`, `omnidoer_chat_send`,
`omnidoer_chat_reply`, `omnidoer_chat_log_user`, `omnidoer_chat_start`,
`omnidoer_chat_delta`, `omnidoer_chat_complete`, and `omnidoer_chat_record`
bridge OmniDoer's Control Client chat/transcript commands. Safe Mode peeks at
the next message with `--no-claim`; claiming a message requires YOLO Mode after
review. Chat text is treated as public coordination text, not a secret channel.

`omnidoer_doctor`, `omnidoer_control_status`,
`omnidoer_control_devices`, `omnidoer_control_sessions`,
`omnidoer_control_tunnel_info`, `omnidoer_control_security_status`,
`omnidoer_control_sync_status`, `omnidoer_audit_tail`,
`omnidoer_audit_verify`, `omnidoer_policy_test`, and
`omnidoer_telegram_status` expose OmniDoer readiness, pairing, sync, audit,
policy, and notification diagnostics through argv calls. `omnidoer_browser_open`
opens a reviewed URL through OmniDoer's browser bridge; Safe Mode requires
HTTPS.

`omnidoer_console_dry_run`, `omnidoer_upgrade_dry_run`, and
`omnidoer_mcp_self_test` cover runtime probes that are safe to run from Handex:
they preview the Codex console wrapper, preview an OmniDoer upgrade, and run the
MCP server self-test without launching Codex, installing files, or starting a
persistent MCP process. `omnidoer_upgrade_dry_run` allows a branch in Safe Mode;
overriding `install_dir` requires YOLO Mode after review.

Mutating Control Client management commands are exposed separately and are
YOLO-only: `omnidoer_control_revoke_device`,
`omnidoer_control_revoke_session`, `omnidoer_control_enable_sync`,
`omnidoer_request_challenge`, `omnidoer_request_takeover`, and
`omnidoer_request_release`. Safe Mode rejects them before invoking OmniDoer.
OmniDoer commands that generate pairing credentials, initialize installs, start
persistent services, launch real Codex sessions, or run autonomous agent tasks
are intentionally left to reviewed shell/background commands instead of normal
Safe tools.

`plugin_list` and `plugin_run` expose configured command plugins from
`HANDEX_PLUGIN_ROOTS`. A plugin is a directory containing `plugin.json`; it
declares a command argv, description, timeout, and whether it is allowed in
Safe Mode. `plugin_run` passes JSON input to the plugin through stdin and the
`HANDEX_PLUGIN_ARGS` environment variable, so plugins do not need shell string
interpolation.

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
- refresh repo orientation through `context_pack`
- bootstrap an empty workspace from Git through `git_bootstrap`
- produce at most one next Tool Command per turn
- request exact file reads and edits
- apply focused Codex-style patch blocks or unified diffs through `apply_patch`
- inspect user-provided files and artifacts through `list_uploads`,
  `download_file`, `view_image`, and `read_file`
- recover missed Tool Result text through `recent_results` or the project
  Execution History section
- batch independent read-only inspections through `tool_batch`
- keep a visible multi-step plan through `update_plan` and `plan_status`
- run long commands through `background_shell`, then poll or stop them with
  `job_status` and `job_stop`
- search available tools, skills, plugins, vault metadata, and help entries
  through `capability_search`
- use skills by asking Handex to list/read configured `SKILL.md` files
- view vault credential metadata without exposing secrets
- list configured OmniDoer vault credential metadata and verify vault unlock
  readiness without exposing passphrases
- run reviewed commands with local vault secrets injected through environment variables
- request missing credentials through the paired OmniDoer Control Client without
  pasting secrets into chat
- submit, list, complete, or cancel paired OmniDoer Control Client tasks for
  reviewed human/device coordination
- inspect and reply through the paired OmniDoer Control Client chat stream,
  including streaming response records
- inspect OmniDoer doctor/status/devices/sessions/tunnel/security/sync,
  audit, policy, and Telegram notification status without dropping to shell
- preview OmniDoer console/upgrade behavior and run the MCP self-test without
  launching persistent runtime processes
- perform reviewed YOLO-only Control Client management actions such as
  revoking devices/sessions, enabling sync, or changing request ownership
- open reviewed HTTPS URLs through OmniDoer's browser bridge
- run reviewed Git/GitHub operations with existing OmniDoer vault credentials
  through `omnidoer_git` and `omnidoer_github_api`
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
  cwd, mode, diff preview, stdout, stderr, and result prompt
- use familiar tools: `shell`, `python`, `git`, `apply_patch`, file tools,
  `context_pack`, `capability_search`, `tool_batch`, `update_plan`, skills,
  plugins, and vault-backed command execution
- keep working one step at a time until the Summary is updated

The only new habit is moving text between the web LLM and Handex.

## Workspace Context Pack

Codex normally sees the current worktree, Git state, and repository
instructions before it acts. Handex mirrors that pattern with a generated
Workspace Context Pack on each project page and through the `context_pack`
tool.

The pack includes:

- project workspace path
- `git status --short --branch`
- recent commits
- `AGENTS.md` files inherited from workspace ancestors
- `AGENTS.md` files found inside the workspace
- common manifests such as `README.md`, `requirements.txt`, `package.json`,
  `pyproject.toml`, and similar project entrypoints
- a bounded file tree that skips bulky runtime folders

Safe Mode keeps the active `context_pack` working directory inside the project
workspace while still allowing inherited `AGENTS.md` files from ancestor
directories to be summarized. The pack is an orientation aid, not proof that the
LLM has read every relevant file; the LLM should still request focused
`read_file`, `grep`, or `git` commands before making implementation claims.

## Workspace Uploads

Project pages can upload files or images directly into the active workspace.
Uploads are stored under `.handex_uploads/` and are not hidden from the tool
runner. This mirrors Codex-style task attachments while keeping the manual
review loop intact:

- the human uploads a file from the browser
- the Single-Step Prompt includes a compact uploaded-file inventory
- the LLM can request `list_uploads` for metadata and redacted text previews
- the LLM can request `view_image` for an authenticated Handex image preview URL
- the LLM can request `download_file` for a generated artifact download URL
- normal tools can read `.handex_uploads/name`, grep uploaded text, or process
  binary/image files with shell commands after review

Upload filenames and optional paths are sanitized, parent traversal is rejected,
text previews redact common secret-looking lines, and the default upload limit
is controlled by `HANDEX_MAX_UPLOAD_BYTES`.

## Git Workspace Bootstrap

Handex can create a project around a Git repository instead of an already
populated directory. The Create Project form accepts an optional repository URL,
branch/ref, and clone depth. Existing projects also have a Git Workspace
Bootstrap form for empty workspaces.

The same operation is exposed to web LLMs as a reviewed Tool Command:

```json
{"tool":"git_bootstrap","args":{"repo_url":"https://github.com/org/repo.git","branch":"main","depth":1},"mode":"safe","reason":"clone the target repository into an empty workspace"}
```

The target workspace must be empty. The command is executed as argv, not a shell
string, and rejects repository URLs containing embedded username/password
credentials. Use `depth: 0` for a full clone.

After a successful bootstrap, the normal `context_pack`, `git`, `read_file`,
`grep`, and patch tools operate on the cloned worktree.

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
- `read_skill_file`: read a specific relative text file referenced by that
  skill, such as `references/details.md` or `scripts/helper.py`
- `skill_pack`: return a compact skill catalog prompt

`read_skill_file` is intentionally narrow: paths are relative to the selected
skill directory, parent traversal is rejected, and secret-looking filenames are
blocked. This supports Codex-style progressive disclosure where `SKILL.md`
points to a small number of extra files instead of loading every asset up
front.

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

When OmniDoer is installed locally, Handex can also call its vault-backed Git
and GitHub API bridges directly:

```sh
HANDEX_OMNIDOER_BIN=omnidoer
HANDEX_OMNIDOER_VAULT_PATH=/root/.omnidoer/vault.json
HANDEX_OMNIDOER_VAULT_PASSPHRASE_FILE=/root/.omnidoer/vault-passphrase
HANDEX_OMNIDOER_GIT_ORIGIN=https://github.com
HANDEX_OMNIDOER_GITHUB_API_ORIGIN=https://api.github.com
```

The related tools are:

- `omnidoer_credential_request`: ask the paired Control Client for a missing
  credential without exposing plaintext to Handex
- `omnidoer_credential_list`: list configured OmniDoer vault credential
  metadata without plaintext secrets
- `omnidoer_vault_unlock`: verify the configured OmniDoer vault/passphrase file
  can be unlocked without returning the passphrase
- `omnidoer_credential_save_request`: store a fulfilled request in the
  configured OmniDoer vault without returning plaintext secrets
- `omnidoer_request_status`: inspect public metadata for pending or completed
  Control Client requests
- `omnidoer_request_wait`: wait briefly for a Control Client request to finish
- `omnidoer_request_deny`: deny or cancel a no-longer-needed request
- `omnidoer_task_submit`: submit reviewed task text to the paired OmniDoer
  Control Client queue
- `omnidoer_task_list`: inspect public task queue metadata, optionally filtered
  by `task_id`, `status`, or `limit`
- `omnidoer_task_complete`: mark a reviewed task as completed
- `omnidoer_task_cancel`: cancel a no-longer-needed task
- `omnidoer_chat_messages`: list or filter public chat transcript metadata
- `omnidoer_chat_next`: inspect the next chat message; Safe Mode always uses
  `--no-claim`
- `omnidoer_chat_send`: send reviewed chat text through OmniDoer
- `omnidoer_chat_reply`: reply to a specific message when `reply_to` is set
- `omnidoer_chat_log_user`: record a reviewed user message in chat history
- `omnidoer_chat_start`: create a streaming assistant message
- `omnidoer_chat_delta`: append text to a streaming assistant message
- `omnidoer_chat_complete`: complete a streaming assistant message
- `omnidoer_chat_record`: record a typed chat event for audit or transcript
  continuity
- `omnidoer_doctor`: run OmniDoer runtime readiness diagnostics
- `omnidoer_control_status`: inspect Control Client service status
- `omnidoer_control_devices`: list paired devices
- `omnidoer_control_sessions`: list active sessions
- `omnidoer_control_tunnel_info`: show tunnel metadata
- `omnidoer_control_security_status`: show security status
- `omnidoer_control_sync_status`: inspect Codex thread/session sync status;
  Safe Mode uses the default Codex binary
- `omnidoer_control_revoke_device`: revoke a paired device; YOLO Mode only
- `omnidoer_control_revoke_session`: revoke a session; YOLO Mode only
- `omnidoer_control_enable_sync`: enable sync for a reviewed session; YOLO Mode
  only
- `omnidoer_request_challenge`: challenge a request; YOLO Mode only
- `omnidoer_request_takeover`: take over a request; YOLO Mode only
- `omnidoer_request_release`: release a request; YOLO Mode only
- `omnidoer_audit_tail`: read recent audit entries
- `omnidoer_audit_verify`: verify audit log integrity
- `omnidoer_policy_test`: run OmniDoer policy self-tests
- `omnidoer_telegram_status`: inspect Telegram notification bridge status
- `omnidoer_console_dry_run`: preview the Codex console wrapper command without
  launching Codex
- `omnidoer_upgrade_dry_run`: preview upgrade actions without installing files;
  Safe Mode permits `branch`, while `install_dir` override is YOLO-only
- `omnidoer_mcp_self_test`: run `omnidoer mcp serve --self-test` without
  starting a persistent MCP server
- `omnidoer_browser_open`: open a reviewed URL; Safe Mode requires HTTPS
- `omnidoer_git`: run `omnidoer git run` with the configured vault bridge
- `omnidoer_github_api`: run `omnidoer github api` with the configured vault
  bridge

Safe Mode only permits read-only `git ls-remote` and GitHub `GET` requests.
Use YOLO Mode after review for `push`, release creation, issue edits, workflow
dispatches, or other mutating operations. Command output is still treated as
heuristically redacted; do not ask tools to print raw tokens or passwords.
Pairing, `init`, `control serve`, `demo start`, `agent run`, non-dry-run
`console`, and non-dry-run `upgrade` are not exposed as normal Safe tools
because they can create credentials, start services, or mutate runtime state.

Credential requests are intentionally public-metadata-only. Handex can create,
poll, wait for, or deny the request, but it never receives the plaintext
password, TOTP seed, or encrypted response body.

Control Client tasks are also treated as public coordination text. Handex
redacts secret-looking task fields in Tool Results, but users and LLMs should
not put passwords, tokens, private keys, or TOTP seeds into task text.

Control Client chat messages follow the same boundary. Handex redacts
secret-looking fields and lines in Tool Results and command previews, but chat
history can be read by paired clients and should not contain credentials.

## Capability Report

`HANDEX_HELP_COMMANDS` can expose local capability help text without coupling
Handex to a specific agent runtime:

```sh
HANDEX_HELP_COMMANDS='codex=codex --help;;omnidoer=omnidoer --help'
```

The `capability_report` tool reports configured skill roots, plugin roots,
whether a vault metadata provider exists, and the help output from those
commands. The `capability_search` tool searches those same capability sources
plus built-in tool descriptions, skills, plugins, and vault credential metadata:

```json
{"tool":"capability_search","args":{"query":"github release","limit":8},"mode":"safe","reason":"find the relevant Handex capability"}
```

## Command Plugins

Handex can load command plugins from configured plugin roots:

```sh
HANDEX_PLUGIN_ROOTS=/opt/handex/plugins:/some/other/plugins
```

Each plugin lives in a directory with `plugin.json`:

```json
{
  "id": "echo",
  "name": "Echo",
  "description": "Echo JSON input for diagnostics.",
  "command": ["python3", "/opt/handex/plugins/echo/echo.py"],
  "safe": true,
  "timeout": 30
}
```

The built-in plugin tools are:

- `plugin_list`: return plugin ids, names, descriptions, safe/yolo mode, root,
  and timeout
- `plugin_run`: run one configured plugin with JSON input

Safe Mode only runs plugins whose manifest sets `"safe": true`; other plugins
require YOLO Mode after review. Plugins receive JSON input on stdin and run
with a minimal process environment plus `HANDEX_PLUGIN_ARGS`,
`HANDEX_PLUGIN_ID`, `HANDEX_WORKSPACE`, and `HANDEX_MODE`. Plugin output is
captured into the normal Tool Result Prompt.

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

## Project Plan

Handex projects have a current plan separate from summaries and logs. The plan
is visible near the top of the project page, included in the Codex-style prompt
and Continuation Transcript, and exported in redacted project snapshots.

The web LLM can update it through:

```json
{"tool":"update_plan","args":{"explanation":"Working through the implementation.","plan":[{"step":"Inspect current code","status":"completed"},{"step":"Patch focused files","status":"in_progress"},{"step":"Run tests","status":"pending"}]},"mode":"safe","reason":"publish the current working plan"}
```

Read it back with:

```json
{"tool":"plan_status","args":{},"mode":"safe","reason":"read the current project plan"}
```

Plan statuses are `pending`, `in_progress`, and `completed`; Handex accepts at
most one `in_progress` item.

## Tool Batches

For broad inspection turns, Handex supports one reviewed batch command:

```json
{"tool":"tool_batch","args":{"commands":[{"tool":"read_file","args":{"path":"README.md"}},{"tool":"grep","args":{"pattern":"TODO","path":"."}}],"stop_on_error":false},"mode":"safe","reason":"run independent read-only inspections in one reviewed step"}
```

Safe Mode batches are intentionally read-only. They support file reads/searches,
context and history lookups, capability discovery, skills/vault metadata, plan
status, job status, plugin lists, and read-only git subcommands. Use normal
single commands for file edits, shell commands, background jobs, vault-backed
commands, and plugin execution so the human reviews each side-effecting action
directly.

## Execution History

Each project page includes an Execution History section with the recent
reviewed commands and their sanitized command JSON, final command, stdout,
stderr, and Tool Result Prompt. Each field has a copy button so the human can
recover a missed result prompt after a browser refresh or continue a session in
another web LLM.

The same data is available to the LLM through:

```json
{"tool":"recent_results","args":{"limit":5,"include_result_prompt":true},"mode":"safe","reason":"recover recent execution results"}
```

History display redaction is heuristic. Avoid printing raw credentials in
normal command output.

## Background Jobs

Long-running commands can be started without blocking the browser request:

```json
{"tool":"background_shell","args":{"command":"pytest -q"},"cwd":".","mode":"safe","reason":"run tests in the background"}
```

Handex stores the job under the project and captures stdout/stderr to job log
files. Poll with:

```json
{"tool":"job_status","args":{"job_id":1,"max_chars":12000},"mode":"safe","reason":"poll test output"}
```

Stop a job with:

```json
{"tool":"job_stop","args":{"job_id":1},"mode":"safe","reason":"stop an obsolete background command"}
```

The project page also has a Background Jobs section with status, output tails,
stop controls, and SSE live updates while a job is running. Output shown
through the UI and tools is redacted heuristically, but commands should still
avoid printing raw secrets.

## Continuation Transcript

Handex projects also expose a Continuation Transcript. This is a compact,
copyable project record for switching web LLMs, resuming after a browser tab is
lost, or handing the same task to another human.

The transcript includes project metadata, active goal, current summary, project
state, the current Workspace Context Pack, recent summary history, and recent
tool/project events. It tells the next LLM to continue the same
one-tool-command-at-a-time Hand Loop and not to re-run historical commands just
because they appear in the record.

Transcript redaction is heuristic. Handex redacts common secret-like lines and
token patterns before rendering the transcript, but users should still avoid
printing raw credentials in ordinary shell commands.

## Project Snapshots

Each project can be exported as a redacted JSON snapshot and imported back as a
new project. Snapshots are intended for backups, moving work between Handex
instances, or handing a task to another operator without copying the whole
SQLite database.

Snapshots include project metadata, prompt settings, current summary, project
state, summary history, recent logs, and a context snapshot. They intentionally
exclude Handex Vault secrets and encrypted vault rows. Secret-like lines and
common token patterns are redacted before export, but snapshot redaction is
heuristic; avoid printing raw credentials into normal command output.

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
HANDEX_MAX_UPLOAD_BYTES=26214400
HANDEX_VAULT_METADATA_COMMAND=
HANDEX_HELP_COMMANDS=
HANDEX_OMNIDOER_BIN=omnidoer
HANDEX_OMNIDOER_VAULT_PATH=
HANDEX_OMNIDOER_VAULT_PASSPHRASE_FILE=
HANDEX_OMNIDOER_GIT_ORIGIN=https://github.com
HANDEX_OMNIDOER_GITHUB_API_ORIGIN=https://api.github.com
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
- Nginx optional TLS reverse proxy
- Offline read-only project cache
