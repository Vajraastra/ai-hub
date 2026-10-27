"""CPU regression checks for project isolation and asset lifetime."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from anima_studio.core.library import Library, LibraryError


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

    def test_old_style_revision_still_protects_its_sample(self):
        sample = self.asset()
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"],
                                           "sample_ids": [sample["id"]]})
        self.store.save("styles", {"id": style["id"], "sample_ids": []})
        self.store.review(sample["id"], "rejected")
        self.assertEqual(self.store.purge_rejected([sample["id"]])["protected"], [sample["id"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
