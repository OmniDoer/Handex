import json
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

from handex import capabilities


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = capabilities.settings

    def tearDown(self):
        capabilities.settings = self.original_settings

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


if __name__ == "__main__":
    unittest.main()
