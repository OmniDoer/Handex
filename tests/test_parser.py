import unittest

from handex.parser import parse_llm_reply


class ParserTests(unittest.TestCase):
    def test_extracts_markdown_json_tool_command(self):
        reply = """
        I will inspect the workspace.

        ```json
        {"tool":"shell","args":{"command":"pwd"},"cwd":".","mode":"safe"}
        ```
        """
        result = parse_llm_reply(reply)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].command["tool"], "shell")

    def test_extracts_multiple_commands_from_wrapped_json(self):
        reply = """
        {"tool_commands":[
          {"tool":"read_file","args":{"path":"README.md"}},
          {"tool":"git","args":{"args":["status","--short"]}}
        ]}
        """
        result = parse_llm_reply(reply)
        self.assertEqual([item.command["tool"] for item in result.candidates], ["read_file", "git"])

    def test_reports_no_json(self):
        result = parse_llm_reply("No command needed.")
        self.assertFalse(result.candidates)
        self.assertIn("No JSON", result.errors[-1])


if __name__ == "__main__":
    unittest.main()
