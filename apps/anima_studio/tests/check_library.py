"""CPU regression checks for project isolation and asset lifetime."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from anima_studio.core.library import Library, LibraryError, LibraryConflict


class LibraryChecks(unittest.TestCase):
    def setUp(self):
        self.temp_parent = Path(__file__).resolve().parents[3] / ".cache" / "library-tests"
        self.temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.temp_parent)
        self.root = Path(self.temp.name)
        self.store = Library(self.root)
        self.one = self.store.save("projects", {"name": "First Game"})
        self.two = self.store.save("projects", {"name": "Second Game"})

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.temp_parent.resolve()))
        self.temp.cleanup()

    def asset(self, **kwargs):
        return self.store.save_generation({"project_id": self.one["id"], "category": "characters",
                                           "character_name": "Ofelia", "outfit_name": "Camarera",
                                           "seed": 18446744073709551615, **kwargs}, b"fake png bytes",
                                          {"patch": b"patch", "mask": b"mask"})

    def test_revisions_and_mutable_input_cannot_rewrite_history(self):
        config = {"model": "anima/base.safetensors", "loras": [{"file": "test.safetensors", "strength": 1}]}
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"], "config": config})
        first = self.asset(style_snapshot=style)
        config["loras"][0]["strength"] = 99
        changed = self.store.save("styles", {"id": style["id"], "config": {"model": "other"}})
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(self.store.get("styles", style["id"], 1)["config"]["loras"][0]["strength"], 1)
        self.assertEqual(self.store.get_generation(first["id"])["style_snapshot"]["revision"], 1)
        self.assertEqual(first["seed"], "18446744073709551615")

    def test_project_isolation_and_shared_pieces(self):
        shared = self.store.save("pieces", {"name": "Standing", "kind": "pose"})
        private = self.store.save("pieces", {"name": "Hero", "kind": "character", "project_id": self.two["id"]})
        self.assertEqual([x["id"] for x in self.store.list("pieces", self.one["id"])], [shared["id"]])
        with self.assertRaises(LibraryError):
            self.store.save("recipes", {"name": "Bad recipe", "project_id": self.one["id"], "piece_ids": [private["id"]]})
        with self.assertRaises(LibraryError):
            self.store.save("pieces", {"id": private["id"], "project_id": self.one["id"]})

    def test_review_moves_files_and_restores_without_losing_extras(self):
        original = self.asset(expression_name="Alegre")
        accepted = self.store.review(original["id"], "accepted")
        self.assertTrue(accepted["file"].startswith("accepted/first-game/characters/ofelia/camarera/"))
        self.assertIn("alegre", accepted["file"])
        self.assertEqual(self.store.image_path(original["id"]).read_bytes(), b"fake png bytes")
        self.assertTrue(self.store.image_path(original["id"], "patch").is_file())
        rejected = self.store.review(original["id"], "rejected", "Face drift")
        self.assertTrue(rejected["file"].startswith("rejected/"))
        restored = self.store.review(original["id"], "pending")
        self.assertEqual(restored["file"], original["file"])
        self.assertEqual(self.store.image_path(original["id"], "mask").read_bytes(), b"mask")

    def test_unique_files_even_if_same_inputs_and_names(self):
        first, second = self.asset(), self.asset()
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(self.store.review(first["id"], "accepted")["file"],
                            self.store.review(second["id"], "accepted")["file"])

    def test_purge_rejects_used_masters_and_keeps_seed_tombstone(self):
        master = self.asset()
        child = self.asset(master_id=master["id"])
        unused = self.asset()
        self.store.review(master["id"], "rejected")
        self.store.review(unused["id"], "rejected")
        summary = self.store.purge_rejected([master["id"], unused["id"], child["id"]])
        self.assertEqual(summary["protected"], [master["id"]])
        self.assertEqual(summary["purged"], [unused["id"]])
        self.assertEqual(len(summary["skipped"]), 1)
        self.assertTrue(self.store.image_path(master["id"]).is_file())
        self.assertEqual(self.store.get_generation(unused["id"])["seed"], "18446744073709551615")
        with self.assertRaises(LibraryError):
            self.store.review(unused["id"], "accepted")

    def test_retention_and_cross_project_master(self):
        asset = self.asset()
        self.store.review(asset["id"], "rejected")
        self.assertFalse(self.store.purge_rejected([asset["id"]], 30)["purged"])
        with self.assertRaises(LibraryError):
            self.store.save_generation({"project_id": self.two["id"], "master_id": asset["id"]}, b"png")

    def test_paths_are_checked_even_in_existing_records(self):
        with self.assertRaises(LibraryError):
            self.store.get("projects", "../../anything")
        with self.assertRaises(LibraryError):
            self.store.save("projects", {"id": "../escape", "name": "Bad"})
        with self.assertRaises(LibraryError):
            self.store.save("projects", {"id": "CON", "name": "Bad"})
        original = self.asset()
        path = self.root / "history" / f"{original['id']}.json"
        broken = json.loads(path.read_text())
        broken["file"] = "../../outside.png"
        path.write_text(json.dumps(broken))
        with self.assertRaises(LibraryError):
            self.store.image_path(original["id"])

    def test_bad_naming_template_does_not_move_an_image(self):
        self.store.save("projects", {"id": self.one["id"], "settings": {"naming": "{project.__class__}"}})
        asset = self.asset()
        with self.assertRaises(LibraryError):
            self.store.review(asset["id"], "accepted")
        self.assertEqual(self.store.get_generation(asset["id"])["status"], "pending")
        self.assertTrue(self.store.image_path(asset["id"]).is_file())

    def test_failed_review_write_rolls_images_back(self):
        asset = self.asset()
        real_write = self.store._write

        def fail_index(path, value):
            if path == self.root / "history" / f"{asset['id']}.json":
                raise OSError("Simulated disk failure")
            return real_write(path, value)

        with patch.object(self.store, "_write", side_effect=fail_index):
            with self.assertRaises(OSError):
                self.store.review(asset["id"], "accepted")
        self.assertEqual(self.store.get_generation(asset["id"])["status"], "pending")
        self.assertEqual(self.store.image_path(asset["id"]).read_bytes(), b"fake png bytes")
        self.assertEqual(self.store.image_path(asset["id"], "patch").read_bytes(), b"patch")
        self.assertFalse(list((self.root / "accepted").rglob("*.png")))

    def test_old_style_revision_still_protects_its_accepted_sample(self):
        sample = self.asset()
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"],
                                           "sample_ids": [sample["id"]]})
        self.store.save("styles", {"id": style["id"], "sample_ids": []})
        self.store.review(sample["id"], "accepted")
        with self.assertRaises(LibraryConflict):
            self.store.delete_generation(sample["id"])

    def test_pending_and_rejected_samples_delete_without_removing_style_or_history(self):
        for status in ("pending", "rejected"):
            with self.subTest(status=status):
                sample = self.asset()
                self.store.review(sample["id"], status)
                config = {"model": "anima/base.safetensors", "seed": "18446744073709551615", "cfg": 4}
                style = self.store.save("styles", {"name": "Keep my settings", "project_id": self.one["id"],
                    "config": config, "prompt": "painted", "sample_ids": [sample["id"]]})
                snapshot = self.asset(snapshot={"style": style})
                revision_path = self.root / "revisions" / "styles" / style["id"] / "00000001.json"
                original_revision = revision_path.read_bytes()
                self.assertTrue(self.store.delete_generation(sample["id"])["deleted"])
                self.assertEqual(revision_path.read_bytes(), original_revision)
                current = self.store.get("styles", style["id"])
                self.assertEqual((current["config"], current["prompt"], current["revision"]),
                                 (config, "painted", 1))
                self.assertEqual(current["sample_ids"], [])
                self.assertEqual(next(x for x in self.store.list("styles") if x["id"] == style["id"])["sample_ids"], [])
                self.assertEqual(self.store.get("styles", style["id"], 1)["sample_ids"], [sample["id"]])
                self.assertEqual(self.store.get_generation(snapshot["id"])["snapshot"]["style"]["sample_ids"], [sample["id"]])
                changed = self.store.save("styles", {"id": style["id"], "name": "Renamed"})
                self.assertEqual((changed["sample_ids"], changed["config"], changed["revision"]), ([], config, 2))

    def test_rejected_style_samples_can_be_purged_in_bulk(self):
        image = self.asset()
        style = self.store.save("styles", {"name": "Keep", "sample_ids": [image["id"]]})
        self.store.review(image["id"], "rejected")
        result = self.store.purge_rejected([image["id"]])
        self.assertEqual(result["purged"], [image["id"]])
        self.assertEqual(result["protected"], [])
        self.assertEqual(self.store.get("styles", style["id"])["sample_ids"], [])

    def test_sample_provenance_does_not_protect_images_but_actual_masters_do(self):
        image = self.asset()
        self.store.review(image["id"], "accepted")
        self.store.save("styles", {"name": "Imported", "source_sample_ids": [image["id"]],
                                    "imported_from": {"id": image["id"]}})
        self.asset(source_sample_ids=[image["id"]], imported_from={"id": image["id"]})
        self.assertTrue(self.store.delete_generation(image["id"])["deleted"])
        master = self.asset()
        self.asset(master_asset_id=master["id"])
        for status in ("pending", "rejected"):
            self.store.review(master["id"], status)
            with self.subTest(status=status), self.assertRaises(LibraryConflict):
                self.store.delete_generation(master["id"])

    def test_style_save_rejects_deleted_missing_and_malformed_samples(self):
        image = self.asset()
        self.store.delete_generation(image["id"])
        for samples in ([image["id"]], ["missing"], "not-a-list", [None]):
            with self.subTest(samples=samples), self.assertRaises(LibraryError):
                self.store.save("styles", {"name": "Stale", "project_id": self.one["id"],
                                            "sample_ids": samples})
        self.assertEqual(self.store.list("styles"), [])
        self.assertFalse(list((self.root / "revisions" / "styles").rglob("*.json")))

    def test_private_style_samples_require_same_project_but_shared_style_can_share_them(self):
        image = self.asset()
        with self.assertRaisesRegex(LibraryError, "otro proyecto"):
            self.store.save("styles", {"name": "Foreign", "project_id": self.two["id"],
                                        "sample_ids": [image["id"]]})
        private = self.store.save("styles", {"name": "Local", "project_id": self.one["id"],
                                              "sample_ids": [image["id"]]})
        shared = self.store.save("styles", {"name": "Shared", "sample_ids": [image["id"]]})
        self.assertEqual(private["sample_ids"], shared["sample_ids"])

    def test_direct_delete_all_review_states_and_variants_keeps_metadata(self):
        for status in ("pending", "accepted", "rejected"):
            with self.subTest(status=status):
                image = self.asset()
                self.store.review(image["id"], status)
                paths = [self.store.image_path(image["id"], variant) for variant in ("image", "patch", "mask")]
                deleted = self.store.delete_generation(image["id"])
                self.assertTrue(deleted["deleted"])
                self.assertGreater(deleted["bytes_freed"], 0)
                self.assertTrue(all(not path.exists() for path in paths))
                tombstone = self.store.get_generation(image["id"])
                self.assertEqual(tombstone["seed"], "18446744073709551615")
                self.assertTrue(tombstone["purged_at"])
                self.assertEqual(self.store.list_generations(), [])

    def test_delete_style_releases_sample_but_preserves_generation_snapshot(self):
        sample = self.asset()
        self.store.review(sample["id"], "accepted")
        style = self.store.save("styles", {"name": "Mistake", "project_id": self.one["id"],
                                            "sample_ids": [sample["id"]], "prompt": "painted"})
        generated = self.asset(snapshot={"style": style})
        with self.assertRaises(LibraryConflict):
            self.store.delete_generation(sample["id"])
        self.store.delete("styles", style["id"])
        self.assertEqual(self.store.list("styles"), [])
        with self.assertRaises(LibraryError):
            self.store.get("styles", style["id"], 1)
        # The generation's independent snapshot remains available even though
        # the original style is no longer in the library.
        self.assertEqual(self.store.get_generation(generated["id"])["snapshot"]["style"]["prompt"], "painted")
        # A generated snapshot containing the style also contains its samples:
        # that provenance still protects the sample until the result is removed.
        with self.assertRaises(LibraryConflict):
            self.store.delete_generation(sample["id"])
        self.store.delete_generation(generated["id"])
        self.assertTrue(self.store.delete_generation(sample["id"])["deleted"])

    def test_saved_recipe_revisions_block_dependency_delete_until_recipe_removed(self):
        style = self.store.save("styles", {"name": "Paint"})
        piece = self.store.save("pieces", {"name": "Hero", "kind": "character"})
        recipe = self.store.save("recipes", {"name": "Greeting", "project_id": self.one["id"],
                                             "style_id": style["id"], "style_revision": 1,
                                             "piece_ids": [piece["id"]]})
        self.store.save("recipes", {"id": recipe["id"], "style_id": None, "piece_ids": []})
        for kind, record in (("styles", style), ("pieces", piece)):
            with self.subTest(kind=kind), self.assertRaisesRegex(LibraryConflict, "Greeting"):
                self.store.delete(kind, record["id"])
            self.assertEqual(self.store.get(kind, record["id"])["name"], record["name"])
        self.store.delete("recipes", recipe["id"])
        self.store.delete("styles", style["id"])
        self.store.delete("pieces", piece["id"])

    def test_project_delete_requires_removing_contents_and_does_not_cascade(self):
        style = self.store.save("styles", {"name": "Private style", "project_id": self.one["id"]})
        image = self.asset()
        with self.assertRaises(LibraryConflict):
            self.store.delete("projects", self.one["id"])
        self.store.delete("styles", style["id"])
        with self.assertRaisesRegex(LibraryConflict, "imágenes"):
            self.store.delete("projects", self.one["id"])
        self.store.delete_generation(image["id"])
        self.store.delete("projects", self.one["id"])
        self.assertEqual([item["id"] for item in self.store.list("projects")], [self.two["id"]])
        self.assertEqual(self.store.get_generation(image["id"])["project_id"], self.one["id"])

    def test_metadata_delete_move_failure_restores_resource_and_revisions(self):
        style = self.store.save("styles", {"name": "Paint"})
        real_replace = __import__("os").replace

        def fail_revision(source, target):
            if Path(source).is_dir():
                raise OSError("Simulated move failure")
            return real_replace(source, target)

        with patch("anima_studio.core.library.os.replace", side_effect=fail_revision):
            with self.assertRaises(OSError):
                self.store.delete("styles", style["id"])
        self.assertEqual(self.store.get("styles", style["id"])["name"], "Paint")
        self.assertEqual(self.store.get("styles", style["id"], 1)["name"], "Paint")

    def test_resource_delete_physically_removes_json_and_all_revisions(self):
        style = self.store.save("styles", {"name": "Disposable", "prompt": "one"})
        self.store.save("styles", {"id": style["id"], "prompt": "two"})
        current = self.root / "styles" / f'{style["id"]}.json'
        revisions = self.root / "revisions" / "styles" / style["id"]
        expected_bytes = current.stat().st_size + sum(path.stat().st_size for path in revisions.glob("*.json"))
        result = self.store.delete("styles", style["id"])
        self.assertEqual(result["bytes_freed"], expected_bytes)
        self.assertFalse(current.exists())
        self.assertFalse(revisions.exists())
        self.assertFalse((self.root / ".trash").exists())
        self.assertFalse((self.root / ".deleting").exists())

    def test_partial_metadata_unlink_failure_restores_exact_resource_bytes(self):
        style = self.store.save("styles", {"name": "Keep if failure", "prompt": "one"})
        self.store.save("styles", {"id": style["id"], "prompt": "two"})
        paths = [self.root / "styles" / f'{style["id"]}.json',
                 *self.root.joinpath("revisions", "styles", style["id"]).glob("*.json")]
        before = {path: path.read_bytes() for path in paths}
        real_unlink = Path.unlink
        count = 0

        def fail_second_staged_file(path, *args, **kwargs):
            nonlocal count
            if ".deleting" in path.parts and path.is_file() and path.name != "journal.json" and not path.name.startswith("."):
                count += 1
                if count == 2:
                    raise OSError("Simulated partial unlink failure")
            return real_unlink(path, *args, **kwargs)

        with patch("pathlib.Path.unlink", new=fail_second_staged_file):
            with self.assertRaises(OSError):
                self.store.delete("styles", style["id"])
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertFalse((self.root / ".deleting").exists())

    def _stage_crashed_style_delete(self, *, partial_unlink=False):
        import os
        import uuid
        style = self.store.save("styles", {"name": "Interrupted", "prompt": "one"})
        self.store.save("styles", {"id": style["id"], "prompt": "two"})
        paths = [self.root / "styles" / f'{style["id"]}.json',
                 self.root / "revisions" / "styles" / style["id"]]
        before = self.store._metadata_snapshot(paths)
        stage = self.root / ".deleting" / uuid.uuid4().hex
        journal = self.store._metadata_journal(paths, before, stage.name)
        self.store._write(stage / "journal.json", journal)
        for index, path in enumerate(paths):
            os.replace(path, stage / str(index))
        if partial_unlink:
            next((stage / "1").glob("*.json")).unlink()
        return style, before, stage

    def test_crashed_metadata_delete_restores_exact_bytes_from_durable_journal(self):
        style, before, stage = self._stage_crashed_style_delete(partial_unlink=True)
        self.assertFalse((self.root / "styles" / f'{style["id"]}.json').exists())
        new_store = Library(self.root)
        result = new_store.recover_pending_metadata_deletions()
        self.assertEqual(result["recovered"], [stage.name])
        self.assertEqual(result["skipped"], [])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(new_store.get("styles", style["id"])["prompt"], "two")
        self.assertFalse((self.root / ".deleting").exists())

    def test_recovery_never_overwrites_metadata_changed_after_the_crash(self):
        style, _, stage = self._stage_crashed_style_delete()
        new_store = Library(self.root)
        new_store.save("styles", {"id": style["id"], "name": "New choice", "prompt": "new"})
        result = new_store.recover_pending_metadata_deletions()
        self.assertEqual(result["recovered"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(new_store.get("styles", style["id"])["prompt"], "new")
        self.assertTrue((stage / "journal.json").is_file())

    def test_recovery_rejects_foreign_paths_and_preserves_unregistered_staged_files(self):
        _, _, stage = self._stage_crashed_style_delete()
        manifest = stage / "journal.json"
        journal = json.loads(manifest.read_text(encoding="utf-8"))
        journal["entries"][0]["original"] = "../outside.json"
        manifest.write_text(json.dumps(journal), encoding="utf-8")
        foreign = stage / "untouched.txt"
        foreign.write_text("Keep this file", encoding="utf-8")
        result = Library(self.root).recover_pending_metadata_deletions()
        self.assertEqual(result["recovered"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(foreign.read_text(encoding="utf-8"), "Keep this file")
        self.assertTrue(manifest.exists())

    def test_successful_metadata_delete_removes_durable_journal_last(self):
        style = self.store.save("styles", {"name": "Disposable"})
        real_unlink = Path.unlink
        removed = []

        def record_unlink(path, *args, **kwargs):
            if ".deleting" in path.parts and path.is_file() and not path.name.startswith("."):
                removed.append(path.name)
            return real_unlink(path, *args, **kwargs)

        with patch("pathlib.Path.unlink", new=record_unlink):
            self.store.delete("styles", style["id"])
        self.assertEqual(removed[-1], "journal.json")
        self.assertFalse((self.root / ".deleting").exists())

    def test_recovery_removes_empty_committed_staging_directory(self):
        import uuid
        stage = self.root / ".deleting" / uuid.uuid4().hex
        stage.mkdir(parents=True)
        result = Library(self.root).recover_pending_metadata_deletions()
        self.assertEqual(result["discarded_empty"], [stage.name])
        self.assertFalse((self.root / ".deleting").exists())

    def test_recovery_discards_only_uncommitted_journal_temps_before_any_move(self):
        import uuid
        style = self.store.save("styles", {"name": "Never moved"})
        current = self.root / "styles" / f'{style["id"]}.json'
        before = current.read_bytes()
        stage = self.root / ".deleting" / uuid.uuid4().hex
        stage.mkdir(parents=True)
        temporary = stage / f'.journal.json.{uuid.uuid4().hex}.tmp'
        temporary.write_bytes(b'{"schema":')
        unknown = stage.parent / uuid.uuid4().hex
        unknown.mkdir()
        staged = unknown / "0"
        staged.write_bytes(b"Must not guess this file's origin")
        result = Library(self.root).recover_pending_metadata_deletions()
        self.assertEqual(result["discarded_empty"], [stage.name])
        self.assertFalse(stage.exists())
        self.assertEqual(current.read_bytes(), before)
        self.assertEqual(staged.read_bytes(), b"Must not guess this file's origin")
        self.assertEqual([item["transaction"] for item in result["skipped"]], [unknown.name])

    def test_generation_record_is_physically_removed_only_after_cleanup_finalization(self):
        image = self.asset()
        index = self.root / "history" / f'{image["id"]}.json'
        with self.assertRaises(LibraryConflict):
            self.store.finish_generation_deletion([image["id"]])
        self.store.delete_generation(image["id"])
        self.assertTrue(index.is_file())
        result = self.store.finish_generation_deletion([image["id"]])
        self.assertEqual(result["deleted"], [image["id"]])
        self.assertFalse(index.exists())
        self.assertFalse(list(self.root.rglob(f'*{image["id"]}*.png')))
        self.assertEqual(self.store.finish_generation_deletion([image["id"]])["deleted"], [])

    def test_generation_finalization_preflights_entire_batch_before_erasing_records(self):
        removed, active = self.asset(), self.asset()
        self.store.delete_generation(removed["id"])
        with self.assertRaises(LibraryConflict):
            self.store.finish_generation_deletion([removed["id"], active["id"]])
        self.assertTrue((self.root / "history" / f'{removed["id"]}.json').exists())
        self.assertTrue(self.store.image_path(active["id"]).is_file())

    def test_generation_finalization_retries_interrupted_png_unlinks_before_removing_index(self):
        image, untouched = self.asset(), self.asset()
        index = self.root / "history" / f'{image["id"]}.json'
        failed_path = self.store.image_path(image["id"], "patch")
        real_unlink = Path.unlink

        def fail_patch(path, *args, **kwargs):
            if path == failed_path:
                raise OSError("Simulated occupied PNG")
            return real_unlink(path, *args, **kwargs)

        with patch("pathlib.Path.unlink", new=fail_patch):
            with self.assertRaises(OSError):
                self.store.delete_generation(image["id"])
        self.assertTrue(index.exists())
        self.assertTrue(failed_path.exists())
        self.assertTrue(self.store.get_generation(image["id"])["purged_at"])
        self.store.finish_generation_deletion([image["id"]])
        self.assertFalse(index.exists())
        self.assertFalse(list(self.root.rglob(f'*{image["id"]}*.png')))
        self.assertTrue(self.store.image_path(untouched["id"]).exists())

    def test_generation_finalization_removes_sidecar_when_png_was_already_erased(self):
        image = self.asset()
        self.store.review(image["id"], "accepted")
        sidecar = self.store.image_path(image["id"]).with_suffix(".json")
        real_unlink = Path.unlink

        def fail_sidecar(path, *args, **kwargs):
            if path == sidecar:
                raise OSError("Simulated occupied sidecar")
            return real_unlink(path, *args, **kwargs)

        with patch("pathlib.Path.unlink", new=fail_sidecar):
            with self.assertRaises(OSError):
                self.store.delete_generation(image["id"])
        self.assertTrue(sidecar.exists())
        self.assertFalse(sidecar.with_suffix(".png").exists())
        self.store.finish_generation_deletion([image["id"]])
        self.assertFalse(sidecar.exists())
        self.assertFalse((self.root / "history" / f'{image["id"]}.json').exists())

    def test_old_resource_trash_is_erased_but_active_and_unknown_files_are_kept(self):
        import os
        import shutil
        import uuid
        removed = self.store.save("styles", {"name": "Previously removed"})
        active = self.store.save("styles", {"name": "Active"})
        trash = self.root / ".trash" / "styles"
        archived = trash / f'{removed["id"]}-{uuid.uuid4().hex}'
        archived.mkdir(parents=True)
        os.replace(self.root / "styles" / f'{removed["id"]}.json', archived / f'{removed["id"]}.json')
        os.replace(self.root / "revisions" / "styles" / removed["id"], archived / removed["id"])
        active_archive = trash / f'{active["id"]}-{uuid.uuid4().hex}'
        active_archive.mkdir()
        shutil.copyfile(self.root / "styles" / f'{active["id"]}.json', active_archive / f'{active["id"]}.json')
        unknown = trash / "unrelated.json"
        unknown.write_text('{"untouched":true}', encoding="utf-8")
        result = self.store.purge_legacy_trash()
        self.assertEqual(result["deleted"], [{"id": removed["id"], "kind": "styles"}])
        self.assertFalse(archived.exists())
        self.assertTrue(active_archive.exists())
        self.assertTrue(unknown.exists())
        self.assertEqual(self.store.get("styles", active["id"])["name"], "Active")
        self.assertFalse((self.root / ".deleting").exists())

    def test_initialized_marker_survives_deleting_final_project_without_trash(self):
        self.assertTrue(self.store.initialized)
        self.store.delete("projects", self.one["id"])
        self.store.delete("projects", self.two["id"])
        self.assertEqual(self.store.list("projects"), [])
        self.assertTrue(Library(self.root).initialized)
        self.assertFalse((self.root / ".trash").exists())

    def test_direct_delete_cannot_remove_authoritative_json_from_tampered_image(self):
        image = self.asset()
        path = self.root / "history" / f'{image["id"]}.json'
        record = json.loads(path.read_text())
        record["file"] = f'projects/{self.one["id"]}.json'
        path.write_text(json.dumps(record))
        with self.assertRaises(LibraryError):
            self.store.delete_generation(image["id"])
        self.assertTrue((self.root / record["file"]).is_file())
        self.assertFalse(self.store.get_generation(image["id"]).get("purged_at"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
