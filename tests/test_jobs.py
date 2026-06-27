import json
import tempfile
import time
import types
import unittest
from pathlib import Path

from handex import db, jobs
from handex.tools.runner import registry


class JobTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = db.DB_PATH
        self.original_job_settings = jobs.settings

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        jobs.settings = self.original_job_settings

    def configure_temp_db(self, root: Path) -> None:
        db.DB_PATH = root / "handex.db"
        jobs.settings = types.SimpleNamespace(logs_dir=root / "logs")
        db.init_db()

    def test_background_shell_can_be_polled_to_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_temp_db(root)
            workspace = root / "workspace"
            workspace.mkdir()
            db.create_project({"name": "Jobs", "workspace_path": str(workspace), "mode": "safe"})

            result = registry.run(
                {"tool": "background_shell", "args": {"command": "printf start; sleep 0.2; printf done"}},
                str(workspace),
                "safe",
            )
            payload = json.loads(result.stdout)
            job_id = payload["id"]

            status = {}
            for _ in range(20):
                status_result = registry.run({"tool": "job_status", "args": {"job_id": job_id}}, str(workspace), "safe")
                status = json.loads(status_result.stdout)
                if status["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.1)

            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["exit_code"], 0)
            self.assertIn("start", status["stdout"])
            self.assertIn("done", status["stdout"])

    def test_background_shell_can_be_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.configure_temp_db(root)
            workspace = root / "workspace"
            workspace.mkdir()
            db.create_project({"name": "Jobs", "workspace_path": str(workspace), "mode": "safe"})

            result = registry.run(
                {"tool": "background_shell", "args": {"command": "printf before; sleep 10; printf after"}},
                str(workspace),
                "safe",
            )
            job_id = json.loads(result.stdout)["id"]
            stopped = registry.run({"tool": "job_stop", "args": {"job_id": job_id}}, str(workspace), "safe")

            status = json.loads(stopped.stdout)
            self.assertEqual(status["status"], "stopped")
            self.assertIn("before", status["stdout"])


if __name__ == "__main__":
    unittest.main()
