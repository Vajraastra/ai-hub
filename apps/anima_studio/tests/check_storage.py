"""End-to-end physical deletion across workshop library and ComfyUI files."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "apps"))
from anima_studio.core.library import Library
from anima_studio.core.storage import StorageLifecycle


class StorageChecks(unittest.TestCase):
    def setUp(self):
        self.parent = REPO / ".cache/storage-lifecycle-tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.root = Path(self.temp.name).resolve()
        self.library = Library(self.root / "library")
        self.project = self.library.save("projects", {"name": "Disposable test game"})
        self.comfy = self.root / "comfy"
        (self.comfy / "input").mkdir(parents=True)
        (self.comfy / "output").mkdir()
        self.storage = StorageLifecycle(self.library, self.comfy)

    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(self.parent.resolve()))
        self.temp.cleanup()

    def asset(self, content=b"test image", **metadata):
        return self.library.save_generation({"project_id": self.project["id"],
            "category": "characters", "seed": "42", **metadata}, content)

    def write(self, relative, content):
        path = self.comfy / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def index(self, asset):
        return self.library.root / "history" / f"{asset['id']}.json"

    def test_legacy_backend_original_is_removed_before_deleted_json_is_finalized(self):
        image = self.asset()
        original = self.write("output/anima_studio_00001_.png", b"test image")
        foreign = self.write("output/another_tool_00001_.png", b"test image")
        self.library.delete_generation(image["id"])
        self.assertTrue(self.index(image).exists())
        finish = self.library.finish_generation_deletion
        def finalize(ids):
            self.assertFalse(original.exists())
            self.assertTrue(self.index(image).exists())
            return finish(ids)
        with patch.object(self.library, "finish_generation_deletion", side_effect=finalize):
            result = self.storage.maintain()
        self.assertFalse(result["cleanup_pending"])
        self.assertEqual(result["deleted_images"], [image["id"]])
        self.assertFalse(self.index(image).exists())
        self.assertTrue(foreign.exists())
        self.assertFalse(self.storage.has_pending())

    def test_interrupted_scoped_run_and_locked_file_retry_keep_provenance(self):
        run_id = "a" * 32
        self.storage.artifacts.start_run(run_id)
        master = self.write(f"input/anima_studio/{run_id}/master.png", b"master")
        rendered = self.write(f"output/anima_studio/{run_id}/render_00001_.png", b"render")
        image = self.asset(backend_run_id=run_id)
        self.library.delete_generation(image["id"])
        original_unlink = Path.unlink
        def deny(target, *args, **kwargs):
            if target == rendered:
                raise PermissionError("Simulated open render file")
            return original_unlink(target, *args, **kwargs)
        with patch.object(Path, "unlink", deny):
            failed = self.storage.maintain()
        self.assertTrue(failed["cleanup_pending"])
        self.assertTrue(failed["errors"])
        self.assertTrue(rendered.exists())
        self.assertTrue(self.index(image).exists())
        self.assertEqual(len(self.storage.artifacts.pending_runs()), 1)
        retried = self.storage.maintain()
        self.assertFalse(retried["cleanup_pending"])
        self.assertFalse(rendered.exists())
        self.assertFalse(master.exists())
        self.assertFalse(self.index(image).exists())
        self.assertEqual(self.storage.artifacts.pending_runs(), [])

    def test_cached_original_survives_until_its_last_live_library_asset_is_deleted(self):
        one, two = self.asset(), self.asset()
        cached = self.write("output/anima_studio_00001_.png", b"test image")
        self.library.delete_generation(one["id"])
        first = self.storage.maintain()
        self.assertFalse(first["cleanup_pending"])
        self.assertEqual(first["preserved_shared"], 1)
        self.assertFalse(self.index(one).exists())
        self.assertTrue(self.index(two).exists())
        self.assertTrue(cached.exists())
        self.library.delete_generation(two["id"])
        last = self.storage.maintain()
        self.assertFalse(last["cleanup_pending"])
        self.assertFalse(self.index(two).exists())
        self.assertFalse(cached.exists())

    def test_terminal_run_cleanup_keeps_the_durable_library_image(self):
        run_id = "b" * 32
        self.storage.artifacts.start_run(run_id)
        original = self.write(f"output/anima_studio/{run_id}/render_00001_.png", b"test image")
        image = self.asset(backend_run_id=run_id)
        copied = self.library.image_path(image["id"])
        result = self.storage.complete_run(run_id)
        self.assertFalse(result["pending"])
        self.assertFalse(original.exists())
        self.assertEqual(copied.read_bytes(), b"test image")
        self.assertTrue(self.index(image).exists())
        self.assertFalse(self.storage.has_pending())

    def test_old_style_trash_is_physically_removed_after_legacy_image_migration(self):
        image = self.asset()
        original = self.write("output/anima_studio_00001_.png", b"test image")
        self.library.delete_generation(image["id"])
        style = self.library.save("styles", {"name": "Old deleted style", "config": {"seed": "42"}})
        self.library.save("styles", {"id": style["id"], "prompt": "revision two"})
        current = self.library.root / "styles" / f"{style['id']}.json"
        revisions = self.library.root / "revisions/styles" / style["id"]
        archive = self.library.root / ".trash/styles" / f"{style['id']}-{'c' * 32}"
        archive.mkdir(parents=True)
        os.replace(current, archive / current.name)
        os.replace(revisions, archive / revisions.name)
        purge = self.library.purge_legacy_trash
        def purge_after_images():
            self.assertFalse(original.exists())
            self.assertFalse(self.index(image).exists())
            return purge()
        with patch.object(self.library, "purge_legacy_trash", side_effect=purge_after_images):
            result = self.storage.maintain()
        self.assertFalse(result["cleanup_pending"])
        self.assertEqual(result["deleted_resources"], [{"id": style["id"], "kind": "styles"}])
        self.assertFalse(archive.exists())
        self.assertFalse((self.library.root / ".trash").exists())

    def test_malformed_run_provenance_does_not_finalize_or_touch_foreign_paths(self):
        outside = self.root / "foreign.png"
        outside.write_bytes(b"keep")
        image = self.asset(backend_run_id="../../not-a-run")
        self.library.delete_generation(image["id"])
        result = self.storage.maintain()
        self.assertTrue(result["cleanup_pending"])
        self.assertTrue(result["errors"])
        self.assertTrue(self.index(image).exists())
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_failed_legacy_proof_retains_full_deleted_json_for_retry(self):
        image = self.asset()
        original = self.write("output/anima_studio_00001_.png", b"test image")
        self.library.delete_generation(image["id"])
        with patch.object(self.storage.artifacts, "apply_legacy_plan", return_value={
                "removed": [], "bytes_freed": 0, "errors": ["Changed owner"], "pending": True}):
            failed = self.storage.maintain()
        self.assertTrue(failed["cleanup_pending"])
        self.assertTrue(original.exists())
        self.assertTrue(self.index(image).exists())
        retried = self.storage.maintain()
        self.assertFalse(retried["cleanup_pending"])
        self.assertFalse(original.exists())
        self.assertFalse(self.index(image).exists())

    def test_unidentified_legacy_original_keeps_unknown_provenance_pending(self):
        image = self.asset()
        original = self.write("output/anima_studio_00001_.png", b"unidentified original")
        self.library.delete_generation(image["id"])
        record = self.library.get_generation(image["id"])
        record.pop("image_sha256", None)
        self.library._write(self.index(image), record)
        result = self.storage.maintain()
        self.assertTrue(result["cleanup_pending"])
        self.assertGreater(result["unproven_files"], 0)
        self.assertTrue(self.index(image).exists())
        self.assertTrue(original.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
