import json
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

from handex import capabilities
from handex.tools import runner
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

    def test_read_skill_file_tool_reads_referenced_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "example"
            reference_dir = skill_dir / "references"
            reference_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Example\n", encoding="utf-8")
            (reference_dir / "details.md").write_text("Referenced details.\n", encoding="utf-8")
            capabilities.settings = types.SimpleNamespace(skill_roots=[Path(tmp)], vault_metadata_command="", help_commands=[])

            result = registry.run(
                {"tool": "read_skill_file", "args": {"skill_id": "example", "path": "references/details.md"}},
                tmp,
                "safe",
            )

            self.assertEqual(result.exit_code, 0)
            self.assertIn('"path": "references/details.md"', result.stdout)
            self.assertIn("Referenced details.", result.stdout)

    def test_capability_search_tool_returns_matching_builtin(self):
        capabilities.settings = types.SimpleNamespace(skill_roots=[], vault_metadata_command="", help_commands=[])
        with tempfile.TemporaryDirectory() as tmp:
            result = registry.run({"tool": "capability_search", "args": {"query": "patch", "limit": 5}}, tmp, "safe")

        payload = json.loads(result.stdout)
        self.assertEqual(result.exit_code, 0)
        self.assertIn(("tool", "apply_patch"), {(item["type"], item["id"]) for item in payload["results"]})

    def test_apply_patch_updates_workspace_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("old\n", encoding="utf-8")
            patch = (
                "diff --git a/note.txt b/note.txt\n"
                "--- a/note.txt\n"
                "+++ b/note.txt\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )

            result = registry.run({"tool": "apply_patch", "args": {"patch": patch}, "cwd": "."}, tmp, "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")

    def test_apply_patch_safe_mode_blocks_parent_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch = (
                "diff --git a/../note.txt b/../note.txt\n"
                "--- a/../note.txt\n"
                "+++ b/../note.txt\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )

            with self.assertRaises(ToolError):
                registry.run({"tool": "apply_patch", "args": {"patch": patch}, "cwd": "."}, tmp, "safe")

    def test_codex_apply_patch_updates_workspace_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            patch = (
                "*** Begin Patch\n"
                "*** Update File: note.txt\n"
                "@@\n"
                " alpha\n"
                "-beta\n"
                "+delta\n"
                " gamma\n"
                "*** End Patch\n"
            )

            result = registry.run({"tool": "apply_patch", "args": {"patch": patch}, "cwd": "."}, tmp, "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "alpha\ndelta\ngamma\n")

    def test_codex_apply_patch_adds_and_deletes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_path = Path(tmp) / "old.txt"
            new_path = Path(tmp) / "new.txt"
            old_path.write_text("remove me\n", encoding="utf-8")
            patch = (
                "*** Begin Patch\n"
                "*** Delete File: old.txt\n"
                "*** Add File: new.txt\n"
                "+created\n"
                "+file\n"
                "*** End Patch\n"
            )

            result = registry.run({"tool": "apply_patch", "args": {"patch": patch}, "cwd": "."}, tmp, "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertFalse(old_path.exists())
            self.assertEqual(new_path.read_text(encoding="utf-8"), "created\nfile\n")

    def test_codex_apply_patch_check_only_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("old\n", encoding="utf-8")
            patch = (
                "*** Begin Patch\n"
                "*** Update File: note.txt\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )

            result = registry.run({"tool": "apply_patch", "args": {"patch": patch, "check_only": True}, "cwd": "."}, tmp, "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertIn("check passed", result.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")

    def test_codex_apply_patch_safe_mode_blocks_parent_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch = (
                "*** Begin Patch\n"
                "*** Update File: ../note.txt\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )

            with self.assertRaises(ToolError):
                registry.run({"tool": "apply_patch", "args": {"patch": patch}, "cwd": "."}, tmp, "safe")

    def test_preview_write_file_shows_unified_diff_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("old\n", encoding="utf-8")

            preview = registry.preview({"tool": "write_file", "args": {"path": "note.txt", "content": "new\n"}}, tmp, "safe")

            self.assertTrue(preview.diff_preview.startswith("--- a/note.txt\n+++ b/note.txt\n"))
            self.assertIn("--- a/note.txt", preview.diff_preview)
            self.assertIn("+++ b/note.txt", preview.diff_preview)
            self.assertIn("-old", preview.diff_preview)
            self.assertIn("+new", preview.diff_preview)
            self.assertEqual(path.read_text(encoding="utf-8"), "old\n")

    def test_preview_replace_and_delete_file_show_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("alpha\nbeta\n", encoding="utf-8")

            replace = registry.preview(
                {"tool": "replace_file", "args": {"path": "note.txt", "old": "beta", "new": "gamma"}},
                tmp,
                "safe",
            )
            delete = registry.preview({"tool": "delete_file", "args": {"path": "note.txt"}}, tmp, "safe")

            self.assertIn("-beta", replace.diff_preview)
            self.assertIn("+gamma", replace.diff_preview)
            self.assertIn("-alpha", delete.diff_preview)
            self.assertIn("-beta", delete.diff_preview)
            self.assertTrue(path.exists())

    def test_preview_apply_patch_returns_reviewed_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch = (
                "diff --git a/note.txt b/note.txt\n"
                "--- a/note.txt\n"
                "+++ b/note.txt\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )

            preview = registry.preview({"tool": "apply_patch", "args": {"patch": patch}, "cwd": "."}, tmp, "safe")

            self.assertIn("diff --git a/note.txt b/note.txt", preview.diff_preview)
            self.assertIn("+new", preview.diff_preview)

    def test_context_pack_includes_agents_and_redacts_secret_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text(
                "Follow local instructions.\nDefault password: should-not-leak\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=should-not-leak\n", encoding="utf-8")

            result = registry.run({"tool": "context_pack", "args": {}}, tmp, "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertIn("Follow local instructions.", result.stdout)
            self.assertIn("README.md", result.stdout)
            self.assertNotIn("should-not-leak", result.stdout)
            self.assertIn("Secret-looking file names omitted", result.stdout)

    def test_context_pack_includes_inherited_and_nested_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent"
            workspace = parent / "workspace"
            nested = workspace / "package"
            nested.mkdir(parents=True)
            (parent / "AGENTS.md").write_text(
                "Inherited instruction.\napi_token: should-not-leak\n",
                encoding="utf-8",
            )
            (workspace / "AGENTS.md").write_text("Workspace instruction.\n", encoding="utf-8")
            (nested / "AGENTS.md").write_text("Nested instruction.\n", encoding="utf-8")

            result = registry.run({"tool": "context_pack", "args": {}}, str(workspace), "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertIn("Inherited instruction.", result.stdout)
            self.assertIn("Workspace instruction.", result.stdout)
            self.assertIn("Nested instruction.", result.stdout)
            self.assertNotIn("should-not-leak", result.stdout)
            self.assertEqual(result.stdout.count("Workspace instruction."), 1)

    def test_context_pack_safe_mode_blocks_outside_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ToolError):
                registry.run({"tool": "context_pack", "args": {}, "cwd": ".."}, tmp, "safe")

    def test_recent_results_tool_uses_workspace_history(self):
        original_recent_results = runner.project_logs_for_workspace
        try:
            runner.project_logs_for_workspace = lambda workspace, limit, include_result_prompt: [
                {
                    "id": 4,
                    "event_type": "tool.execute",
                    "mode": "safe",
                    "command_json": "{}",
                    "final_command": "printf ok",
                    "cwd": str(workspace),
                    "exit_code": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "result_prompt": "prompt" if include_result_prompt else "",
                    "created_at": "now",
                }
            ]
            with tempfile.TemporaryDirectory() as tmp:
                result = registry.run({"tool": "recent_results", "args": {"include_result_prompt": True}}, tmp, "safe")

            self.assertIn("printf ok", result.stdout)
            self.assertIn("prompt", result.stdout)
        finally:
            runner.project_logs_for_workspace = original_recent_results

    def test_tool_batch_runs_safe_read_only_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("hello\nTODO item\n", encoding="utf-8")

            result = registry.run(
                {
                    "tool": "tool_batch",
                    "args": {
                        "commands": [
                            {"tool": "read_file", "args": {"path": "README.md"}},
                            {"tool": "grep", "args": {"pattern": "TODO", "path": "."}},
                        ],
                        "stop_on_error": False,
                    },
                },
                tmp,
                "safe",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(payload["completed"], 2)
            self.assertIn("hello", payload["results"][0]["stdout"])
            self.assertIn("TODO item", payload["results"][1]["stdout"])

    def test_tool_batch_can_continue_after_read_only_child_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ok.txt").write_text("ok\n", encoding="utf-8")

            result = registry.run(
                {
                    "tool": "tool_batch",
                    "args": {
                        "commands": [
                            {"tool": "read_file", "args": {"path": "missing.txt"}},
                            {"tool": "read_file", "args": {"path": "ok.txt"}},
                        ],
                        "stop_on_error": False,
                    },
                },
                tmp,
                "safe",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(payload["completed"], 2)
            self.assertIn("FileNotFoundError", payload["results"][0]["stderr"])
            self.assertIn("ok", payload["results"][1]["stdout"])

    def test_tool_batch_safe_mode_blocks_write_children_before_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "created.txt"

            with self.assertRaises(ToolError):
                registry.run(
                    {
                        "tool": "tool_batch",
                        "args": {
                            "commands": [
                                {"tool": "write_file", "args": {"path": "created.txt", "content": "no"}},
                                {"tool": "read_file", "args": {"path": "created.txt"}},
                            ]
                        },
                    },
                    tmp,
                    "safe",
                )

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
