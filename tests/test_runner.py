import tempfile
import textwrap
import types
import unittest
from pathlib import Path

from handex import capabilities
from handex.tools.runner import ToolError, registry


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.original_capability_settings = capabilities.settings

    def tearDown(self):
        capabilities.settings = self.original_capability_settings

    def test_safe_write_and_read_stay_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = {"tool": "write_file", "args": {"path": "note.txt", "content": "hello"}}
            result = registry.run(command, tmp, "safe")
            self.assertEqual(result.exit_code, 0)
            read = registry.run({"tool": "read_file", "args": {"path": "note.txt"}}, tmp, "safe")
            self.assertEqual(read.stdout, "hello")

    def test_safe_mode_blocks_outside_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ToolError):
                registry.run({"tool": "read_file", "args": {"path": "/etc/passwd"}}, tmp, "safe")

    def test_shell_preview_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.run({"tool": "shell", "args": {"command": "printf ok"}}, tmp, "safe")
            self.assertEqual(result.stdout, "ok")

    def test_read_skill_tool_reads_configured_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "example"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                textwrap.dedent(
                    """\
                    ---
                    name: example
                    description: Example skill.
                    ---

                    # Example

                    Use this skill for tests.
                    """
                ),
                encoding="utf-8",
            )
            capabilities.settings = types.SimpleNamespace(skill_roots=[Path(tmp)], vault_metadata_command="", help_commands=[])

            result = registry.run({"tool": "read_skill", "args": {"skill_id": "example"}}, tmp, "safe")
            self.assertEqual(result.exit_code, 0)
            self.assertIn("Use this skill for tests.", result.stdout)


if __name__ == "__main__":
    unittest.main()
