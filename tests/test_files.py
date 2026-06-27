import json
import tempfile
import unittest
from pathlib import Path

from handex import db
from handex.files import FileAccessError, resolve_workspace_file
from handex.tools.runner import ToolError, registry


class FileTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = db.DB_PATH

    def tearDown(self):
        db.DB_PATH = self.original_db_path

    def test_resolve_workspace_file_blocks_parent_and_secret_like_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.pdf").write_bytes(b"%PDF-1.4\n")
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

            info = resolve_workspace_file(root, "report.pdf")

            self.assertEqual(info.media_type, "application/pdf")
            with self.assertRaises(FileAccessError):
                resolve_workspace_file(root, "../report.pdf")
            with self.assertRaises(FileAccessError):
                resolve_workspace_file(root, ".env")

    def test_download_file_tool_returns_project_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db.DB_PATH = root / "handex.db"
            db.init_db()
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            project_id = db.create_project({"name": "Files", "workspace_path": str(workspace), "mode": "safe"})

            result = registry.run({"tool": "download_file", "args": {"path": "report.csv"}}, str(workspace), "safe")
            payload = json.loads(result.stdout)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(payload["media_type"], "text/csv")
            self.assertEqual(payload["name"], "report.csv")
            self.assertIn(f"/projects/{project_id}/file", payload["url"])

    def test_download_file_tool_blocks_secret_like_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "id_rsa").write_text("secret", encoding="utf-8")

            with self.assertRaises(ToolError):
                registry.run({"tool": "download_file", "args": {"path": "id_rsa"}}, tmp, "safe")


if __name__ == "__main__":
    unittest.main()
