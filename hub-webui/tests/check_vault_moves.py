"""Windows-safe move regressions using disposable files inside the workspace."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))
from model_vault.core.file_operations import move_model_files, relative_folder
from model_vault.core.model_placer import InboxItem, classify_inbox_file, place_model


class MoveChecks(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / ".cache" / "test-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "Models"
        self.source = self.root / "checkpoints" / "model.safetensors"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"source model")
        self.target = self.root / "diffusion_models" / "anima"
        self.sidecar = self.source.with_suffix(".preview.webp")
        self.sidecar.write_bytes(b"source preview")

    def move(self, **kwargs):
        return move_model_files(str(self.source), str(self.root), "diffusion_models/anima", **kwargs)

    def test_complete_move_includes_preview_and_updates_database_last(self):
        def update(path):
            self.assertTrue(Path(path).exists())
            self.assertTrue((self.target / self.sidecar.name).exists())
        self.assertEqual(self.move(update_path=update), (str(self.target / self.source.name), True))
        self.assertFalse(self.source.exists())
        self.assertFalse(self.sidecar.exists())

    def test_weight_collision_does_not_overwrite_any_file(self):
        self.target.mkdir(parents=True)
        target = self.target / self.source.name
        target.write_bytes(b"existing model")
        with self.assertRaises(FileExistsError):
            self.move()
        self.assertEqual(target.read_bytes(), b"existing model")
        self.assertEqual(self.source.read_bytes(), b"source model")
        self.assertTrue(self.sidecar.exists())

    def test_sidecar_collision_prevents_weight_move(self):
        self.target.mkdir(parents=True)
        target = self.target / self.sidecar.name
        target.write_bytes(b"existing preview")
        with self.assertRaises(FileExistsError):
            self.move()
        self.assertTrue(self.source.exists())
        self.assertEqual(target.read_bytes(), b"existing preview")

    def test_other_model_with_same_stem_stays_in_place(self):
        other = self.source.with_suffix(".ckpt")
        other.write_bytes(b"other model")
        self.sidecar.unlink()
        self.move()
        self.assertEqual(other.read_bytes(), b"other model")
        self.assertFalse((self.target / other.name).exists())

    def test_shared_stem_sidecars_are_rejected_as_ambiguous(self):
        other = self.source.with_suffix(".ckpt")
        other.write_bytes(b"other model")
        with self.assertRaisesRegex(ValueError, "ambiguos"):
            self.move()
        self.assertTrue(self.source.exists())
        self.assertTrue(self.sidecar.exists())
        self.assertEqual(other.read_bytes(), b"other model")
        self.assertFalse(self.target.exists())

    def test_reject_absolute_drive_unc_and_traversal_targets(self):
        for folder in ("../outside", "checkpoints/../../outside", r"E:\outside",
                       r"\outside", r"\\server\share", "checkpoints/./anima"):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                move_model_files(str(self.source), str(self.root), folder)
        self.assertTrue(self.source.exists())

    def test_reject_source_outside_model_root(self):
        outside = Path(self.temporary.name) / "outside.safetensors"
        outside.write_bytes(b"outside")
        with self.assertRaises(ValueError):
            move_model_files(str(outside), str(self.root), "checkpoints/anima")
        self.assertTrue(outside.exists())

    def test_reject_external_junction_target(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        junction = self.root / "external"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            junction.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            move_model_files(str(self.source), str(self.root), "external/anima")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(self.source.exists())

    def test_rename_failure_rolls_back_completed_files(self):
        real_rename = os.rename
        def rename(source, target):
            if Path(source) == self.sidecar:
                raise OSError("simulated preview failure")
            return real_rename(source, target)
        with patch("model_vault.core.file_operations.os.rename", side_effect=rename):
            with self.assertRaisesRegex(OSError, "preview failure"):
                self.move()
        self.assertTrue(self.source.exists())
        self.assertTrue(self.sidecar.exists())
        self.assertEqual(list(self.target.iterdir()), [])

    def test_database_failure_rolls_back_weight_and_sidecars(self):
        with self.assertRaisesRegex(OSError, "database failure"):
            self.move(update_path=Mock(side_effect=OSError("database failure")))
        self.assertTrue(self.source.exists())
        self.assertTrue(self.sidecar.exists())
        self.assertEqual(list(self.target.iterdir()), [])

    def test_current_folder_is_a_noop(self):
        update = Mock()
        result = move_model_files(str(self.source), str(self.root), "checkpoints", update_path=update)
        self.assertEqual(result, (str(self.source), False))
        update.assert_not_called()

    def test_models_root_is_an_explicit_valid_destination_and_noop(self):
        result = move_model_files(str(self.source), str(self.root), "")
        target = self.root / self.source.name
        self.assertEqual(result, (str(target), True))
        self.assertTrue((self.root / self.sidecar.name).exists())
        self.assertEqual(move_model_files(str(target), str(self.root), ""), (str(target), False))

    def test_physical_folder_includes_storage_category(self):
        self.assertEqual(relative_folder(str(self.source), str(self.root)), ("checkpoints", "checkpoints"))
        self.move()
        self.assertEqual(relative_folder(str(self.target / self.source.name), str(self.root)),
                         ("diffusion_models/anima", "diffusion_models"))

    def test_inbox_placement_uses_suggestion_unless_root_override_is_explicit(self):
        item = InboxItem(str(self.source), self.source.name, 1, "checkpoint", "Anima", 1,
                         "header", "diffusion_models", "anima")
        result = place_model(item, str(self.root))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["dest"], str(self.target / self.source.name))

    def test_inbox_classifier_imports_its_own_package(self):
        model = self.source.with_suffix(".bin")
        model.write_bytes(b"dummy model")
        result = classify_inbox_file(str(model))
        self.assertEqual(result.filename, model.name)


if __name__ == "__main__":
    unittest.main()
