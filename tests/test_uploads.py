import io
import tempfile
import types
import unittest
from pathlib import Path

from handex import uploads
from handex.tools.runner import registry


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.original_upload_settings = uploads.settings

    def tearDown(self):
        uploads.settings = self.original_upload_settings

    def test_save_upload_sanitizes_path_and_lists_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads.settings = types.SimpleNamespace(max_upload_bytes=1024 * 1024)

            info = uploads.save_workspace_upload(
                tmp,
                "Original Name.txt",
                io.BytesIO(b"hello uploaded file\n"),
                target_path="notes/My Upload.txt",
            )

            self.assertEqual(info.path, ".handex_uploads/notes/My-Upload.txt")
            self.assertTrue((Path(tmp) / ".handex_uploads" / "notes" / "My-Upload.txt").exists())
            listed = uploads.list_workspace_uploads(tmp)
            self.assertEqual(len(listed), 1)
            self.assertIn("hello uploaded file", listed[0].preview)

    def test_upload_rejects_parent_paths_and_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads.settings = types.SimpleNamespace(max_upload_bytes=4)

            with self.assertRaises(uploads.UploadError):
                uploads.save_workspace_upload(tmp, "x.txt", io.BytesIO(b"ok"), target_path="../x.txt")
            with self.assertRaises(uploads.UploadError):
                uploads.save_workspace_upload(tmp, "x.txt", io.BytesIO(b"too large"))

    def test_delete_upload_rejects_parent_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads.settings = types.SimpleNamespace(max_upload_bytes=1024)
            info = uploads.save_workspace_upload(tmp, "x.txt", io.BytesIO(b"ok"))

            with self.assertRaises(uploads.UploadError):
                uploads.delete_workspace_upload(tmp, "../x.txt")
            deleted = uploads.delete_workspace_upload(tmp, info.upload_path)

            self.assertEqual(deleted.path, ".handex_uploads/x.txt")
            self.assertFalse((Path(tmp) / ".handex_uploads" / "x.txt").exists())

    def test_list_uploads_tool_returns_metadata_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads.settings = types.SimpleNamespace(max_upload_bytes=1024)
            uploads.save_workspace_upload(tmp, "note.txt", io.BytesIO(b"tool-visible\n"))

            result = registry.run({"tool": "list_uploads", "args": {}}, tmp, "safe")

            self.assertEqual(result.exit_code, 0)
            self.assertIn(".handex_uploads/note.txt", result.stdout)
            self.assertIn("tool-visible", result.stdout)


if __name__ == "__main__":
    unittest.main()
