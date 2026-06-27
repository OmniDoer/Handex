# Handex Plugins

Handex tool execution is registry based. Built-in tools are registered in
`handex/tools/runner.py` and share this command shape:

```json
{
  "tool": "shell",
  "args": {},
  "cwd": ".",
  "mode": "safe",
  "reason": "why this command is needed"
}
```

Future plugins should expose a Python callable that receives:

- `command`: the parsed Tool Command object
- `workspace`: the project workspace as a resolved `Path`
- `mode`: `safe` or `yolo`

The callable returns a `ToolResult`. Safe Mode plugins should keep filesystem
effects inside the project workspace unless the user explicitly switches to
YOLO Mode.

Secret-bearing plugins should follow the same rule as `vault_run`: never put
raw secrets in `command_json`, `final_command`, logs, stdout, stderr, or Tool
Result prompts. Inject secrets through process environment or another reviewed
local mechanism, then redact direct secret values from all captured output.
