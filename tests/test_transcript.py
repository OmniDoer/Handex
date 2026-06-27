import unittest

from handex.transcript import build_project_transcript


class TranscriptTests(unittest.TestCase):
    def test_transcript_includes_project_state_and_redacts_secret_like_lines(self):
        project = {
            "id": 7,
            "name": "Demo",
            "description": "Build a fallback agent.",
            "goal": "Keep moving.",
            "project_state": "Patch pending.",
            "current_summary": "Current work summary.",
            "workspace_path": "/tmp/demo",
            "settings": {"mode": "safe"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:01:00+00:00",
        }
        summaries = [{"id": 1, "created_at": "now", "content": "Earlier summary.", "note": "checkpoint"}]
        logs = [
            {
                "id": 4,
                "created_at": "now",
                "event_type": "tool.execute",
                "mode": "safe",
                "exit_code": 0,
                "cwd": "/tmp/demo",
                "final_command": "printf ok",
                "command_json": '{"tool":"shell","args":{"command":"printf ok"}}',
                "stdout": "password: must-not-leak\nok\n",
                "stderr": "",
                "result_prompt": "TOKEN=must-not-leak\ncontinue",
            }
        ]

        transcript = build_project_transcript(project, summaries, logs, "Context here", max_chars=12000)

        self.assertIn("Handex Continuation Transcript", transcript)
        self.assertIn("Keep moving.", transcript)
        self.assertIn("Earlier summary.", transcript)
        self.assertIn("tool.execute", transcript)
        self.assertIn("Context here", transcript)
        self.assertNotIn("must-not-leak", transcript)
        self.assertIn("[REDACTED]", transcript)


if __name__ == "__main__":
    unittest.main()
