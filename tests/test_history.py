import unittest

from handex.history import recent_results_payload, sanitize_log_for_display


class HistoryTests(unittest.TestCase):
    def test_sanitize_log_for_display_redacts_command_and_outputs(self):
        log = {
            "id": 3,
            "project_id": 1,
            "event_type": "tool.execute",
            "mode": "safe",
            "command_json": '{"tool":"git_bootstrap","args":{"repo_url":"https://user:secret@example.com/repo.git","api_token":"must-not-leak"}}',
            "final_command": "git clone https://user:secret@example.com/repo.git",
            "cwd": "/tmp/demo",
            "exit_code": 1,
            "stdout": "TOKEN=must-not-leak\nok",
            "stderr": "",
            "result_prompt": "Command used https://user:secret@example.com/repo.git",
            "created_at": "now",
        }

        sanitized = sanitize_log_for_display(log)
        dumped = str(sanitized)

        self.assertNotIn("user:secret", dumped)
        self.assertNotIn("must-not-leak", dumped)
        self.assertIn("[REDACTED]", dumped)
        self.assertIn("https://example.com/repo.git", dumped)

    def test_recent_results_payload_can_omit_result_prompt(self):
        logs = [{"id": 1, "event_type": "tool.execute", "result_prompt": "large prompt", "created_at": "now"}]

        payload = recent_results_payload(logs, include_result_prompt=False)

        self.assertEqual(payload[0]["result_prompt"], "")


if __name__ == "__main__":
    unittest.main()
