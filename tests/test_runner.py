import json
import os
import sys
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
        self.original_runner_settings = runner.settings

    def tearDown(self):
        capabilities.settings = self.original_capability_settings
        runner.settings = self.original_runner_settings

    def fake_omnidoer_settings(self, script: Path) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            max_output_chars=20000,
            omnidoer_bin=str(script),
            omnidoer_vault_path="/tmp/handex-test-vault.json",
            omnidoer_vault_passphrase_file="/tmp/handex-test-passphrase",
            omnidoer_git_origin="https://github.com",
            omnidoer_github_api_origin="https://api.github.com",
        )

    def write_fake_omnidoer(self, root: Path) -> Path:
        script = root / "fake_omnidoer.py"
        script.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys

                argv = sys.argv[1:]
                if argv[:2] == ["cred", "request"]:
                    origin = argv[argv.index("--origin") + 1] if "--origin" in argv else ""
                    print("credential_request=req_test")
                    print(f"origin={origin}")
                    print("expires_at=123.5")
                    print("secret_exposed_to_model=false")
                elif argv[:2] == ["cred", "save-request"]:
                    print(json.dumps({
                        "status": "stored",
                        "credential_id": "cred_saved",
                        "request_id": argv[2],
                        "has_vault_arg": "--vault" in argv,
                        "has_passphrase_file_arg": "--passphrase-file" in argv,
                        "secret_exposed_to_model": False,
                    }))
                elif argv[:2] == ["control", "requests"]:
                    print(json.dumps([
                        {
                            "request_id": "req_test",
                            "request_type": "credential",
                            "origin": "https://example.com",
                            "status": "pending",
                            "secret_exposed_to_model": False,
                        }
                    ]))
                elif argv[:2] == ["control", "wait-request"]:
                    print("request_id=req_test")
                    print("request_completed=true")
                    print("status=fulfilled")
                    print("completed_by_user=true")
                    print("has_ciphertext=true")
                    print("secret_exposed_to_model=false")
                elif argv[:2] == ["control", "deny"]:
                    print(f"denied {argv[-1]}")
                else:
                    print(json.dumps({
                        "argv": argv,
                        "has_handex_vault_key": "HANDEX_VAULT_KEY" in os.environ,
                    }))
                """
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script

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

    def test_omnidoer_git_uses_configured_vault_bridge_without_secret_env(self):
        old_value = os.environ.get("HANDEX_VAULT_KEY")
        os.environ["HANDEX_VAULT_KEY"] = "must-not-reach-child"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                script = self.write_fake_omnidoer(Path(tmp))
                runner.settings = self.fake_omnidoer_settings(script)

                result = registry.run(
                    {
                        "tool": "omnidoer_git",
                        "args": {
                            "args": ["ls-remote", "https://github.com/example/private.git"],
                            "credential_id": "cred_1",
                        },
                    },
                    tmp,
                    "safe",
                )

            payload = json.loads(result.stdout)
            self.assertFalse(payload["has_handex_vault_key"])
            self.assertEqual(payload["argv"][:4], ["git", "run", "--origin", "https://github.com"])
            self.assertIn("--vault", payload["argv"])
            self.assertIn("--passphrase-file", payload["argv"])
            self.assertIn("--credential-id", payload["argv"])
            self.assertEqual(payload["argv"][-2:], ["ls-remote", "https://github.com/example/private.git"])
        finally:
            if old_value is None:
                os.environ.pop("HANDEX_VAULT_KEY", None)
            else:
                os.environ["HANDEX_VAULT_KEY"] = old_value

    def test_omnidoer_credential_request_returns_public_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self.write_fake_omnidoer(Path(tmp))
            runner.settings = self.fake_omnidoer_settings(script)

            result = registry.run(
                {
                    "tool": "omnidoer_credential_request",
                    "args": {
                        "origin": "https://example.com",
                        "summary": "Need a test credential.",
                        "ttl": "5m",
                        "no_totp_field": True,
                    },
                },
                tmp,
                "safe",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(payload["credential_request"], "req_test")
        self.assertEqual(payload["origin"], "https://example.com")
        self.assertFalse(payload["secret_exposed_to_model"])
        self.assertIn("omnidoer_request_status", payload["next_tools"])
        self.assertIn("omnidoer_credential_save_request", payload["next_tools"])

    def test_omnidoer_credential_request_safe_mode_requires_https(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self.write_fake_omnidoer(Path(tmp))
            runner.settings = self.fake_omnidoer_settings(script)

            with self.assertRaises(ToolError):
                registry.run({"tool": "omnidoer_credential_request", "args": {"origin": "http://example.com"}}, tmp, "safe")

    def test_omnidoer_request_status_wait_save_and_deny_are_public(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self.write_fake_omnidoer(Path(tmp))
            runner.settings = self.fake_omnidoer_settings(script)

            status = registry.run({"tool": "omnidoer_request_status", "args": {"request_id": "req_test"}}, tmp, "safe")
            waited = registry.run({"tool": "omnidoer_request_wait", "args": {"request_id": "req_test", "wait_timeout": "1s"}}, tmp, "safe")
            saved = registry.run({"tool": "omnidoer_credential_save_request", "args": {"request_id": "req_test", "wait": True, "wait_timeout": "1s"}}, tmp, "safe")
            denied = registry.run({"tool": "omnidoer_request_deny", "args": {"request_id": "req_test"}}, tmp, "safe")

        status_payload = json.loads(status.stdout)
        wait_payload = json.loads(waited.stdout)
        save_payload = json.loads(saved.stdout)
        deny_payload = json.loads(denied.stdout)
        self.assertEqual(status_payload["request_id"], "req_test")
        self.assertFalse(status_payload["secret_exposed_to_model"])
        self.assertEqual(wait_payload["status"], "fulfilled")
        self.assertFalse(wait_payload["secret_exposed_to_model"])
        self.assertEqual(save_payload["status"], "stored")
        self.assertTrue(save_payload["has_vault_arg"])
        self.assertTrue(save_payload["has_passphrase_file_arg"])
        self.assertFalse(save_payload["secret_exposed_to_model"])
        self.assertEqual(deny_payload["status"], "denied")
        self.assertFalse(deny_payload["secret_exposed_to_model"])

    def test_omnidoer_git_safe_mode_blocks_mutating_subcommands(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ToolError):
                registry.run({"tool": "omnidoer_git", "args": {"args": ["push", "origin", "main"]}}, tmp, "safe")

    def test_omnidoer_github_api_builds_get_and_blocks_safe_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self.write_fake_omnidoer(Path(tmp))
            runner.settings = self.fake_omnidoer_settings(script)

            result = registry.run(
                {"tool": "omnidoer_github_api", "args": {"method": "GET", "path": "/user", "credential_id": "cred_1"}},
                tmp,
                "safe",
            )
            with self.assertRaises(ToolError):
                registry.run({"tool": "omnidoer_github_api", "args": {"method": "POST", "path": "/user/repos", "body": {"name": "demo"}}}, tmp, "safe")

        payload = json.loads(result.stdout)
        self.assertEqual(payload["argv"][:4], ["github", "api", "--origin", "https://github.com"])
        self.assertIn("--api-origin", payload["argv"])
        self.assertEqual(payload["argv"][-2:], ["GET", "/user"])

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
