# Handex Prompts

Default prompt text is currently embedded in `handex/prompts.py` so it can be
versioned, tested, and rendered without runtime file dependencies.

Project owners can override the launch prompt template and tool protocol from
the project settings page. Supported launch template variables are:

- `{project_background}`
- `{current_goal}`
- `{current_summary}`
- `{project_state}`
- `{workspace_path}`
- `{tool_protocol}`

The project page also exposes a Single-Step Agent Prompt. It is generated from
the same project state, the current dynamic skill catalog, and a compact
Workspace Context Pack with Git status, inherited and workspace `AGENTS.md`
instructions, manifests, and a bounded file tree. Any web LLM can act as a
manual coding agent through Handex without relying on Codex, OmniDoer, or a
model API. The prompt asks the LLM to produce at most one next Tool Command per
turn so Handex remains a reviewed copy/paste agent loop.

The Single-Step Agent Prompt also includes the current project plan. The LLM can
replace that plan through `update_plan` and inspect it through `plan_status`,
mirroring Codex's visible multi-step planning loop without requiring an API
session.

The context snapshot is also exposed separately on the project page and through
the `context_pack` tool, so the LLM can refresh orientation without spending
several turns on `pwd`, `git status`, `ls`, and inherited `AGENTS.md` reads.

The project page also exposes a Continuation Transcript. It packages project
metadata, goal, summary, workspace context, recent summaries, and recent
tool/project events into a copyable prompt for resuming the same Hand Loop in
another web LLM session.

When a parsed Tool Command would change files, Handex renders a read-only Diff
Preview before execution. This preview covers `write_file`, `append_file`,
`replace_file`, `delete_file`, and `apply_patch`; `apply_patch` accepts unified
diffs and Codex-style `*** Begin Patch` blocks. This gives the human a
Codex-like review surface before approving the command.

Configured command plugins are exposed through `plugin_list` and `plugin_run`.
The Single-Step Agent Prompt includes a plugin catalog snapshot and instructs
the LLM to list plugins before running one, keeping plugin execution in the
same reviewed one-command loop as built-in tools.

Workspace uploads are exposed through the project page and the `list_uploads`
tool. Uploaded files live under `.handex_uploads/` inside the workspace, and
the Single-Step Agent Prompt includes a compact inventory so the LLM can ask
for focused `read_file`, `grep`, or shell processing of user-provided files.

Git bootstrap is exposed through project forms and the `git_bootstrap` tool.
It lets a web LLM request a reviewed clone into an empty workspace without
constructing shell commands or embedding credentials in URLs.

Execution history recovery is exposed through the project page and
`recent_results`. This lets the LLM or human recover sanitized command JSON,
stdout, stderr, and Tool Result Prompts when a copy/paste loop is interrupted.

Independent read-only inspection commands can be grouped with `tool_batch`.
Safe Mode batches are limited to read-only tools and read-only git subcommands,
so they reduce copy/paste overhead for broad context gathering without turning a
single approval into unreviewed file edits or shell execution.

Long-running command support is exposed through `background_shell`,
`job_status`, and `job_stop`. The prompt tells the LLM to start long commands
as background jobs, poll output tails, and stop jobs that are no longer useful.
The project page also streams running job status/output over SSE for the human.
