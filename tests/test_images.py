import base64
import json
import tempfile
import unittest
from pathlib import Path

from handex import db
from handex.images import ImageError, resolve_workspace_image
from handex.tools.runner import ToolError, registry


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = db.DB_PATH

    def tearDown(self):
        db.DB_PATH = self.original_db_path

    def test_resolve_workspace_image_reads_png_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.png"
            path.write_bytes(PNG_1X1)

            info = resolve_workspace_image(tmp, "image.png")

            self.assertEqual(info.media_type, "image/png")
            self.assertEqual(info.width, 1)
            self.assertEqual(info.height, 1)

    def test_resolve_workspace_image_rejects_parent_and_fake_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ImageError):
                resolve_workspace_image(tmp, "../image.png")
            fake = Path(tmp) / "fake.png"
            fake.write_text("<html>not an image</html>", encoding="utf-8")
            with self.assertRaises(ImageError):
                resolve_workspace_image(tmp, "fake.png")

    def test_view_image_tool_returns_metadata_and_project_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db.DB_PATH = root / "handex.db"
            db.init_db()
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "image.png").write_bytes(PNG_1X1)
            project_id = db.create_project({"name": "Images", "workspace_path": str(workspace), "mode": "safe"})

            result = registry.run({"tool": "view_image", "args": {"path": "image.png"}}, str(workspace), "safe")
            payload = json.loads(result.stdout)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(payload["media_type"], "image/png")
            self.assertEqual(payload["width"], 1)
            self.assertIn(f"/projects/{project_id}/image", payload["url"])

    def test_view_image_tool_blocks_non_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "note.txt").write_text("text", encoding="utf-8")

            with self.assertRaises(ToolError):
                registry.run({"tool": "view_image", "args": {"path": "note.txt"}}, tmp, "safe")


if __name__ == "__main__":
    unittest.main()
