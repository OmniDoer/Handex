# Handex Plugins

Handex command plugins are manifest-defined local tools. A plugin is any
directory under `HANDEX_PLUGIN_ROOTS` that contains a `plugin.json` file.

Example:

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

The web LLM can inspect configured plugins with:

```json
{"tool":"plugin_list","args":{},"mode":"safe","reason":"inspect configured command plugins"}
```

It can request a plugin run with:

```json
{"tool":"plugin_run","args":{"plugin_id":"echo","input":{"message":"hello"}},"cwd":".","mode":"safe","reason":"run the echo plugin"}
```

`plugin_run` executes argv directly; it does not interpolate shell strings.
JSON input is passed on stdin. Plugins run with a minimal process environment
plus `HANDEX_PLUGIN_ARGS`, `HANDEX_PLUGIN_ID`, `HANDEX_WORKSPACE`, and
`HANDEX_MODE`.

Safe Mode only runs plugins whose manifest sets `"safe": true`. Plugins that
can write outside the workspace, access secrets, call privileged services, or
perform irreversible actions should leave `safe` false and require YOLO Mode.

Secret-bearing plugins should follow the same rule as `vault_run`: never put
raw secrets in `command_json`, `final_command`, logs, stdout, stderr, or Tool
Result prompts. Inject secrets through process environment or another reviewed
local mechanism, then redact direct secret values from all captured output.
