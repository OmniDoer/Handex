import json
import unittest

from handex.snapshot import build_project_snapshot, dumps_snapshot, imported_project_data, parse_snapshot


class SnapshotTests(unittest.TestCase):
    def test_snapshot_redacts_and_excludes_vault_secrets(self):
        project = {
            "name": "Demo",
            "description": "password: must-not-leak",
            "goal": "Resume elsewhere",
            "project_state": "state",
            "current_summary": "summary",
            "prompt_template": "",
            "tool_protocol": "",
            "workspace_path": "/tmp/demo",
            "settings": {"mode": "safe"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:01:00+00:00",
        }
        logs = [
            {
                "event_type": "tool.execute",
                "mode": "safe",
                "command_json": '{"tool":"shell"}',
                "final_command": "printf ok",
                "cwd": "/tmp/demo",
                "exit_code": 0,
                "stdout": "TOKEN=must-not-leak\nok",
                "stderr": "",
                "result_prompt": "",
                "created_at": "now",
            }
        ]

        snapshot = build_project_snapshot(project, [], logs, "context")
        dumped = dumps_snapshot(snapshot)

        self.assertNotIn("must-not-leak", dumped)
        self.assertIn("[REDACTED]", dumped)
        self.assertFalse(snapshot["vault"]["included"])
        self.assertNotIn("secret_encrypted", dumped)

    def test_parse_snapshot_and_imported_project_data(self):
        raw = json.dumps(
            {
                "schema": "handex.project_snapshot",
                "version": 1,
                "project": {"name": "Demo", "mode": "yolo", "goal": "g"},
                "summaries": [],
                "logs": [],
            }
        )

        snapshot = parse_snapshot(raw)
        project = imported_project_data(snapshot, "/tmp/imported")

        self.assertEqual(project["name"], "Demo (imported)")
        self.assertEqual(project["mode"], "yolo")
        self.assertEqual(project["workspace_path"], "/tmp/imported")

    def test_parse_snapshot_rejects_wrong_schema_or_version(self):
        with self.assertRaises(ValueError):
            parse_snapshot(json.dumps({"schema": "wrong", "version": 1, "project": {}}))
        with self.assertRaises(ValueError):
            parse_snapshot(json.dumps({"schema": "handex.project_snapshot", "version": "bad", "project": {}}))


if __name__ == "__main__":
    unittest.main()
