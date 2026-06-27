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
Workspace Context Pack with Git status, `AGENTS.md` instructions, manifests,
and a bounded file tree. Any web LLM can act as a manual coding agent through
Handex without relying on Codex, OmniDoer, or a model API. The prompt asks the
LLM to produce at most one next Tool Command per turn so Handex remains a
reviewed copy/paste agent loop.

The context snapshot is also exposed separately on the project page and through
the `context_pack` tool, so the LLM can refresh orientation without spending
several turns on `pwd`, `git status`, `ls`, and `AGENTS.md` reads.

The project page also exposes a Continuation Transcript. It packages project
metadata, goal, summary, workspace context, recent summaries, and recent
tool/project events into a copyable prompt for resuming the same Hand Loop in
another web LLM session.

When a parsed Tool Command would change files, Handex renders a read-only Diff
Preview before execution. This preview covers `write_file`, `append_file`,
`replace_file`, `delete_file`, and `apply_patch`, and gives the human a
Codex-like review surface before approving the command.

Configured command plugins are exposed through `plugin_list` and `plugin_run`.
The Single-Step Agent Prompt includes a plugin catalog snapshot and instructs
the LLM to list plugins before running one, keeping plugin execution in the
same reviewed one-command loop as built-in tools.

Workspace uploads are exposed through the project page and the `list_uploads`
tool. Uploaded files live under `.handex_uploads/` inside the workspace, and
the Single-Step Agent Prompt includes a compact inventory so the LLM can ask
for focused `read_file`, `grep`, or shell processing of user-provided files.
