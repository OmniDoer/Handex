import unittest
from types import SimpleNamespace

from handex.prompts import build_tool_result_prompt, sanitize_command_for_prompt


class PromptTests(unittest.TestCase):
    def test_command_sanitizer_redacts_secret_keys_and_git_url_userinfo(self):
        command = {
            "tool": "git_bootstrap",
            "args": {
                "repo_url": "https://user:secret@example.com/repo.git",
                "api_token": "must-not-leak",
            },
        }

        sanitized = sanitize_command_for_prompt(command)
        dumped = str(sanitized)

        self.assertNotIn("secret", dumped)
        self.assertNotIn("must-not-leak", dumped)
        self.assertIn("https://example.com/repo.git", dumped)
        self.assertIn("[REDACTED]", dumped)

    def test_tool_result_prompt_redacts_command_json(self):
        result = SimpleNamespace(
            tool="git_bootstrap",
            mode="safe",
            cwd="/tmp/workspace",
            exit_code=1,
            command={"raw": '{"repo_url":"https://user:secret@example.com/repo.git"}'},
            final_command="git clone https://user:secret@example.com/repo.git",
            stdout="",
            stderr="Git repository URL must not contain embedded credentials.",
        )

        prompt = build_tool_result_prompt({"name": "Demo"}, result)

        self.assertNotIn("user:secret", prompt)
        self.assertIn("[REDACTED]", prompt)


if __name__ == "__main__":
    unittest.main()
