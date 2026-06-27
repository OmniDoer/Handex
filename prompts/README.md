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
