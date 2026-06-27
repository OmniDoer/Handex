import subprocess
import tempfile
import unittest
from pathlib import Path

from handex.bootstrap import BootstrapError, bootstrap_workspace_from_git, redacted_repo_url
from handex.tools.runner import registry


class BootstrapTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "source"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "handex@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Handex"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        return repo

    def test_bootstrap_clones_into_empty_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_repo(root)
            workspace = root / "workspace"

            result = bootstrap_workspace_from_git(workspace, str(source), depth=0)

            self.assertEqual(result.exit_code, 0)
            self.assertTrue((workspace / ".git").exists())
            self.assertEqual((workspace / "README.md").read_text(encoding="utf-8"), "# Demo\n")

    def test_bootstrap_rejects_non_empty_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_repo(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "existing.txt").write_text("occupied\n", encoding="utf-8")

            with self.assertRaises(BootstrapError):
                bootstrap_workspace_from_git(workspace, str(source), depth=0)

    def test_bootstrap_rejects_embedded_credentials(self):
        with self.assertRaises(BootstrapError):
            bootstrap_workspace_from_git("/tmp/handex-unused", "https://user:secret@example.com/repo.git")

    def test_redacted_repo_url_strips_credentials(self):
        self.assertEqual(
            redacted_repo_url("https://user:secret@example.com:8443/repo.git"),
            "https://example.com:8443/repo.git",
        )

    def test_git_bootstrap_tool_clones_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_repo(root)
            workspace = root / "tool-workspace"

            result = registry.run(
                {"tool": "git_bootstrap", "args": {"repo_url": str(source), "depth": 0}},
                str(workspace),
                "safe",
            )

            self.assertEqual(result.exit_code, 0)
            self.assertIn("git clone", result.final_command)
            self.assertTrue((workspace / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
