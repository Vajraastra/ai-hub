import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import storage_manager


class ModelDirectoryMigrationTests(unittest.TestCase):
    def test_migrates_files_and_replaces_source_with_link(self):
        with tempfile.TemporaryDirectory() as root:
            app_path = os.path.join(root, "app", "models", "style_models")
            central_path = os.path.join(root, "models", "style_models")
            os.makedirs(app_path)
            os.makedirs(central_path)
            source_file = os.path.join(app_path, "model.bin")
            with open(source_file, "wb") as handle:
                handle.write(b"model-data")

            result = storage_manager.migrate_model_directory(app_path, central_path)

            self.assertEqual(result["status"], "migrated")
            self.assertEqual(result["files"], 1)
            self.assertTrue(storage_manager._is_dir_link(app_path))
            self.assertTrue(storage_manager._link_points_to(app_path, central_path))
            with open(os.path.join(central_path, "model.bin"), "rb") as handle:
                self.assertEqual(handle.read(), b"model-data")

    def test_conflict_aborts_without_changing_source(self):
        with tempfile.TemporaryDirectory() as root:
            app_path = os.path.join(root, "app-models")
            central_path = os.path.join(root, "central-models")
            os.makedirs(app_path)
            os.makedirs(central_path)
            with open(os.path.join(app_path, "model.bin"), "wb") as handle:
                handle.write(b"local")
            with open(os.path.join(central_path, "model.bin"), "wb") as handle:
                handle.write(b"central")

            with self.assertRaisesRegex(OSError, "Conflicto de contenido"):
                storage_manager.migrate_model_directory(app_path, central_path)

            self.assertFalse(storage_manager._is_dir_link(app_path))
            with open(os.path.join(app_path, "model.bin"), "rb") as handle:
                self.assertEqual(handle.read(), b"local")

    def test_link_failure_restores_source_directory(self):
        with tempfile.TemporaryDirectory() as root:
            app_path = os.path.join(root, "app-models")
            central_path = os.path.join(root, "central-models")
            os.makedirs(app_path)
            with open(os.path.join(app_path, "model.bin"), "wb") as handle:
                handle.write(b"model-data")

            with mock.patch.object(storage_manager, "_create_dir_link", side_effect=OSError("boom")):
                with self.assertRaisesRegex(OSError, "boom"):
                    storage_manager.migrate_model_directory(app_path, central_path)

            self.assertTrue(os.path.isdir(app_path))
            self.assertFalse(storage_manager._is_dir_link(app_path))
            with open(os.path.join(app_path, "model.bin"), "rb") as handle:
                self.assertEqual(handle.read(), b"model-data")


if __name__ == "__main__":
    unittest.main()
