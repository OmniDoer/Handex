import json
import tempfile
import unittest
from pathlib import Path

from handex import db
from handex.jobs import JobError
from handex.plans import PlanError, normalize_plan_payload
from handex.tools.runner import registry


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = db.DB_PATH

    def tearDown(self):
        db.DB_PATH = self.original_db_path

    def configure_temp_project(self, root: Path) -> Path:
        db.DB_PATH = root / "handex.db"
        db.init_db()
        workspace = root / "workspace"
        workspace.mkdir()
        db.create_project({"name": "Plans", "workspace_path": str(workspace), "mode": "safe"})
        return workspace

    def test_update_plan_tool_persists_current_project_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.configure_temp_project(Path(tmp))
            command = {
                "tool": "update_plan",
                "args": {
                    "explanation": "Working through it.",
                    "plan": [
                        {"step": "Inspect", "status": "completed"},
                        {"step": "Patch", "status": "in_progress"},
                        {"step": "Verify", "status": "pending"},
                    ],
                },
            }

            result = registry.run(command, str(workspace), "safe")
            status = registry.run({"tool": "plan_status", "args": {}}, str(workspace), "safe")
            payload = json.loads(status.stdout)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(payload["explanation"], "Working through it.")
            self.assertEqual([item["status"] for item in payload["plan"]], ["completed", "in_progress", "pending"])

    def test_update_plan_requires_project_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            db.DB_PATH = Path(tmp) / "handex.db"
            db.init_db()
            with self.assertRaises(JobError):
                registry.run({"tool": "plan_status", "args": {}}, tmp, "safe")

    def test_normalize_plan_rejects_multiple_in_progress_items(self):
        with self.assertRaises(PlanError):
            normalize_plan_payload(
                {
                    "plan": [
                        {"step": "A", "status": "in_progress"},
                        {"step": "B", "status": "in_progress"},
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
