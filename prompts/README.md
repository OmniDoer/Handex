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
