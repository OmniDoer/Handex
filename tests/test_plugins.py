import json
import os
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

from handex import plugins
from handex.tools.runner import ToolError, registry


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.original_plugin_settings = plugins.settings

    def tearDown(self):
        plugins.settings = self.original_plugin_settings

    def test_lists_command_plugin_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_dir = root / "echo"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": "echo",
                        "name": "Echo",
                        "description": "Echo input.",
                        "command": [sys.executable, "-c", "print('ok')"],
                        "safe": True,
                        "timeout": 5,
                    }
                ),
                encoding="utf-8",
            )
            plugins.settings = types.SimpleNamespace(plugin_roots=[root])

            found = plugins.list_plugins()

            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].plugin_id, "echo")
            self.assertTrue(found[0].safe)

    def test_plugin_run_passes_json_payload_on_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugins"
            workspace = Path(tmp) / "workspace"
            plugin_dir = root / "echo"
            plugin_dir.mkdir(parents=True)
            workspace.mkdir()
            script = plugin_dir / "echo.py"
            script.write_text(
                textwrap.dedent(
                    """\
                    import json
                    import os
                    import sys

                    data = json.loads(sys.stdin.read())
                    env = json.loads(os.environ["HANDEX_PLUGIN_ARGS"])
                    print(data["message"] + ":" + env["message"])
                    """
                ),
                encoding="utf-8",
            )
            (plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": "echo",
                        "name": "Echo",
                        "command": [sys.executable, str(script)],
                        "safe": True,
                        "timeout": 5,
                    }
                ),
                encoding="utf-8",
            )
            plugins.settings = types.SimpleNamespace(plugin_roots=[root])

            result = registry.run(
                {"tool": "plugin_run", "args": {"plugin_id": "echo", "input": {"message": "hello"}}},
                str(workspace),
                "safe",
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.strip(), "hello:hello")

    def test_plugin_run_does_not_inherit_service_secret_environment(self):
        old_value = os.environ.get("HANDEX_VAULT_KEY")
        os.environ["HANDEX_VAULT_KEY"] = "must-not-reach-plugin"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "plugins"
                workspace = Path(tmp) / "workspace"
                plugin_dir = root / "envcheck"
                plugin_dir.mkdir(parents=True)
                workspace.mkdir()
                script = plugin_dir / "envcheck.py"
                script.write_text(
                    "import os\nprint('HANDEX_VAULT_KEY' in os.environ)\n",
                    encoding="utf-8",
                )
                (plugin_dir / "plugin.json").write_text(
                    json.dumps(
                        {
                            "id": "envcheck",
                            "command": [sys.executable, str(script)],
                            "safe": True,
                        }
                    ),
                    encoding="utf-8",
                )
                plugins.settings = types.SimpleNamespace(plugin_roots=[root])

                result = registry.run({"tool": "plugin_run", "args": {"plugin_id": "envcheck"}}, str(workspace), "safe")

                self.assertEqual(result.stdout.strip(), "False")
        finally:
            if old_value is None:
                os.environ.pop("HANDEX_VAULT_KEY", None)
            else:
                os.environ["HANDEX_VAULT_KEY"] = old_value

    def test_safe_mode_blocks_yolo_only_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugins"
            workspace = Path(tmp) / "workspace"
            plugin_dir = root / "danger"
            plugin_dir.mkdir(parents=True)
            workspace.mkdir()
            (plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": "danger",
                        "command": [sys.executable, "-c", "print('danger')"],
                        "safe": False,
                    }
                ),
                encoding="utf-8",
            )
            plugins.settings = types.SimpleNamespace(plugin_roots=[root])

            with self.assertRaises(ToolError):
                registry.run({"tool": "plugin_run", "args": {"plugin_id": "danger"}}, str(workspace), "safe")


if __name__ == "__main__":
    unittest.main()
