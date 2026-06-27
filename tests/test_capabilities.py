import json
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

from handex import capabilities, plugins


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = capabilities.settings
        self.original_plugin_settings = plugins.settings

    def tearDown(self):
        capabilities.settings = self.original_settings
        plugins.settings = self.original_plugin_settings

    def test_skills_are_loaded_from_configured_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "release-manager"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                textwrap.dedent(
                    """\
                    ---
                    name: release-manager
                    description: Prepare releases.
                    ---

                    # Release Manager

                    Follow the release checklist.
                    """
                ),
                encoding="utf-8",
            )
            capabilities.settings = types.SimpleNamespace(
                skill_roots=[Path(tmp)],
                vault_metadata_command="",
                help_commands=[],
            )

            skills = capabilities.list_skills()
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].skill_id, "root1:release-manager")
            self.assertEqual(skills[0].name, "release-manager")

            skill, content = capabilities.read_skill("root1:release-manager")
            self.assertEqual(skill.name, "release-manager")
            self.assertIn("Follow the release checklist.", content)

    def test_read_skill_file_reads_referenced_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "release-manager"
            references = skill_dir / "references"
            references.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Release Manager\n", encoding="utf-8")
            (references / "details.md").write_text("Release details\napi_token=must-not-appear\n", encoding="utf-8")
            (skill_dir / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
            capabilities.settings = types.SimpleNamespace(
                skill_roots=[Path(tmp)],
                vault_metadata_command="",
                help_commands=[],
            )

            skill, relative_path, content = capabilities.read_skill_file("release-manager", "references/details.md")

            self.assertEqual(skill.skill_id, "root1:release-manager")
            self.assertEqual(relative_path, "references/details.md")
            self.assertIn("Release details", content)
            self.assertNotIn("must-not-appear", content)
            with self.assertRaises(PermissionError):
                capabilities.read_skill_file("release-manager", "../outside.md")
            with self.assertRaises(PermissionError):
                capabilities.read_skill_file("release-manager", ".env")

    def test_vault_metadata_provider_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            emitter = Path(tmp) / "emit.py"
            emitter.write_text(
                "import json\n"
                "print(json.dumps([{'credential_id':'cred_1','username':'u***r','allowed_origins':['https://example.com'],"
                "'metadata':{'kind':'token','name':'example','host':'example.com','source':'test'},"
                "'password':'must-not-appear'}]))\n",
                encoding="utf-8",
            )
            capabilities.settings = types.SimpleNamespace(
                skill_roots=[],
                vault_metadata_command=f"python3 {emitter}",
                help_commands=[],
            )

            metadata = capabilities.list_vault_metadata()
            self.assertEqual(metadata[0]["credential_id"], "cred_1")
            self.assertNotIn("password", metadata[0])
            self.assertNotIn("must-not-appear", json.dumps(metadata))

    def test_capability_search_finds_tools_skills_plugins_vault_and_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root = root / "skills"
            plugin_root = root / "plugins"
            skill_dir = skill_root / "release-manager"
            plugin_dir = plugin_root / "release-plugin"
            skill_dir.mkdir(parents=True)
            plugin_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                textwrap.dedent(
                    """\
                    ---
                    name: release-manager
                    description: Ship GitHub releases.
                    ---

                    # Release Manager
                    """
                ),
                encoding="utf-8",
            )
            (plugin_dir / "plugin.json").write_text(
                json.dumps(
                    {
                        "id": "release-plugin",
                        "name": "Release Plugin",
                        "description": "Publish release metadata.",
                        "command": [sys.executable, "-c", "print('ok')"],
                        "safe": True,
                    }
                ),
                encoding="utf-8",
            )
            emitter = root / "vault_emit.py"
            emitter.write_text(
                "import json\n"
                "print(json.dumps([{'credential_id':'cred_release','username':'u***r',"
                "'metadata':{'kind':'token','name':'GitHub release token','host':'github.com'},"
                "'password':'must-not-appear'}]))\n",
                encoding="utf-8",
            )
            capabilities.settings = types.SimpleNamespace(
                skill_roots=[skill_root],
                vault_metadata_command=f"{sys.executable} {emitter}",
                help_commands=[("release-help", "release --help")],
            )
            plugins.settings = types.SimpleNamespace(plugin_roots=[plugin_root])

            tool_payload = capabilities.search_capabilities("patch", limit=5)
            release_payload = capabilities.search_capabilities("release", limit=20)

            self.assertIn(("tool", "apply_patch"), {(item["type"], item["id"]) for item in tool_payload["results"]})
            release_matches = {(item["type"], item["id"]) for item in release_payload["results"]}
            self.assertIn(("skill", "root1:release-manager"), release_matches)
            self.assertIn(("plugin", "release-plugin"), release_matches)
            self.assertIn(("vault_credential", "cred_release"), release_matches)
            self.assertIn(("help_command", "release-help"), release_matches)
            self.assertEqual(release_payload["errors"], [])
            self.assertNotIn("must-not-appear", json.dumps(release_payload))


if __name__ == "__main__":
    unittest.main()
