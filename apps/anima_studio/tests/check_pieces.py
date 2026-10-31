"""CPU checks for pinned piece inheritance and authoritative test capture."""
import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from anima_studio.core.library import Library, LibraryConflict, LibraryError


class PieceChecks(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[3] / ".cache" / "piece-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.store = Library(Path(self.temp.name))
        self.project = self.store.save("projects", {"name": "VN"})
        self.other = self.store.save("projects", {"name": "Other"})

    def tearDown(self):
        self.temp.cleanup()

    def piece(self, **kwargs):
        return self.store.save("pieces", {"name": "Hero", "kind": "character",
                                         "project_id": self.project["id"], "prompt": "blue hair",
                                         "negative": "hat", **kwargs})

    def sample(self, **kwargs):
        return self.store.save_generation({"project_id": self.project["id"], "category": "characters",
            "seed": "18446744073709551615", "prompt": "quality\nblue hair", "negative": "bad anatomy",
            "config": {"model": "anima.safetensors", "seed": "18446744073709551615", "steps": 30},
            "snapshot": {"style": {"id": "frozen-style", "revision": 2}, "pieces": []},
            "composition": {"blocks": [{"kind": "character", "prompt": "blue hair"}]}, **kwargs}, b"png")

    def test_legacy_piece_and_new_resource_kinds(self):
        for kind in ("character", "scene", "background", "item"):
            with self.subTest(kind=kind):
                piece = self.piece(kind=kind)
                self.assertEqual(piece["prompt"], "blue hair")
                self.assertNotIn("seed", piece)
                self.assertNotIn("master_id", piece)

    def test_existing_piece_type_cannot_change_or_write_a_revision(self):
        original = self.piece()
        revisions = self.store.root / "revisions" / "pieces" / original["id"]
        before = {path.name: path.read_bytes() for path in revisions.glob("*.json")}
        for field in ("kind", "piece_type", "type"):
            for value in ("outfit", None):
                with self.subTest(field=field, value=value), self.assertRaisesRegex(LibraryError, "crea otra pieza"):
                    self.store.save("pieces", {"id": original["id"], field: value, "prompt": "changed"})
                self.assertEqual(self.store.get("pieces", original["id"]), original)
                self.assertEqual({path.name: path.read_bytes() for path in revisions.glob("*.json")}, before)
        edited = self.store.save("pieces", {"id": original["id"], "prompt": "red hair"})
        self.assertEqual((edited["kind"], edited["revision"], edited["prompt"]), ("character", 2, "red hair"))
        for field in ("kind", "piece_type", "type"):
            edited = self.store.save("pieces", {"id": original["id"], field: "character"})
            self.assertEqual(edited["kind"], "character")

    def test_variant_inherits_pinned_revision_and_is_not_changed_by_master_edit(self):
        master = self.piece(seed=18446744073709551615, tags=["hero"],
                            loras=[{"file": "characters/hero.safetensors", "strength": .7}])
        variant = self.store.save("pieces", {"name": "Tall hero", "master_id": master["id"],
                                             "prompt": "blue hair, tall"})
        self.assertEqual(variant["master_revision"], 1)
        self.assertEqual(variant["kind"], "character")
        self.assertEqual(variant["project_id"], self.project["id"])
        self.assertEqual(variant["seed"], "18446744073709551615")
        self.assertEqual(variant["negative"], "hat")
        self.assertEqual(variant["loras"][0]["strength"], .7)
        master["loras"][0]["strength"] = 9
        self.piece(id=master["id"], prompt="red hair", seed="3", loras=[])
        loaded = self.store.get("pieces", variant["id"])
        self.assertEqual(loaded["prompt"], "blue hair, tall")
        self.assertEqual(loaded["seed"], "18446744073709551615")
        self.assertEqual(loaded["loras"][0]["strength"], .7)
        edited = self.store.save("pieces", {"id": variant["id"], "negative": ""})
        self.assertEqual(edited["prompt"], "blue hair, tall")
        self.assertEqual(edited["negative"], "")
        self.assertEqual(edited["master_revision"], 1)

    def test_variant_can_explicitly_pin_old_master_revision(self):
        master = self.piece(seed="42")
        self.piece(id=master["id"], prompt="red hair", seed="100")
        variant = self.store.save("pieces", {"name": "Original face", "master_id": master["id"],
                                             "master_revision": 1})
        self.assertEqual((variant["prompt"], variant["seed"]), ("blue hair", "42"))

    def test_parent_cannot_change_and_self_or_cycles_are_rejected(self):
        master, other = self.piece(), self.piece(name="Other hero")
        variant = self.piece(name="Variant", master_id=master["id"])
        for payload in ({"id": master["id"], "master_id": master["id"]},
                        {"id": master["id"], "master_id": variant["id"]},
                        {"id": variant["id"], "master_id": other["id"]},
                        {"id": variant["id"], "master_id": None},
                        {"id": variant["id"], "master_revision": 2}):
            with self.subTest(payload=payload), self.assertRaises(LibraryError):
                self.store.save("pieces", payload)

    def test_kind_project_and_revision_must_match_parent(self):
        master = self.piece()
        for changes in ({"kind": "outfit"}, {"project_id": self.other["id"]},
                        {"project_id": None}, {"master_revision": 0}, {"master_revision": True},
                        {"master_revision": "1"}):
            with self.subTest(changes=changes), self.assertRaises(LibraryError):
                self.piece(name="Bad variant", master_id=master["id"], **changes)
        shared = self.piece(project_id=None)
        private = self.piece(master_id=shared["id"])
        self.assertEqual(private["project_id"], self.project["id"])

    def test_invalid_seed_never_reaches_disk(self):
        for value in (True, -1, "-1", 2**64, "1e3", "1.0", .5, "١٢", {}, []):
            with self.subTest(value=value), self.assertRaises(LibraryError):
                self.piece(seed=value)
        self.assertEqual(len(self.store.list("pieces")), 0)
        seed = self.piece(seed="42")
        cleared = self.store.save("pieces", {"id": seed["id"], "seed": None})
        self.assertNotIn("seed", cleared)

    def test_lora_identity_scope_duplicates_and_values(self):
        valid = self.piece(loras=[{"file": "identity\\hero.safetensors", "strength": .6,
                                    "trigger": "my hero", "enabled": False}])
        self.assertEqual(valid["loras"][0]["file"], "identity/hero.safetensors")
        self.assertFalse(valid["loras"][0]["enabled"])
        invalid = ([{"file": "../escape.safetensors"}], [{"file": "C:/model.safetensors"}],
                   [{"file": "hero.safetensors", "strength": float("nan")}],
                   [{"file": "hero.safetensors", "strength": True}],
                   [{"file": "hero.safetensors", "enabled": "false"}],
                   [{"file": "hero.safetensors", "trigger": ["hero"]}],
                   [{"file": "Hero.safetensors"}, {"file": "hero.safetensors"}])
        for loras in invalid:
            with self.subTest(loras=loras), self.assertRaises(LibraryError):
                self.piece(loras=loras)
        for kind in ("outfit", "pose", "expression", "background", "scene", "item", "personality"):
            linked = self.piece(kind=kind, loras=[{"file": "specific.safetensors", "strength": 0}])
            self.assertEqual(linked["loras"], [{"file": "specific.safetensors", "strength": 0.0}])

    def test_capture_uses_real_seed_config_and_blocks_not_caller_snapshot(self):
        sample = self.sample()
        master = self.piece(sample_asset_id=sample["id"], seed="7",
            generation_snapshot={"seed": "7", "config": {"model": "fake"}})
        self.assertEqual(master["seed"], "18446744073709551615")
        snapshot = master["generation_snapshot"]
        self.assertEqual(snapshot["config"]["model"], "anima.safetensors")
        self.assertEqual(snapshot["composition"]["blocks"][0]["kind"], "character")
        self.assertEqual(snapshot["snapshot"]["style"]["revision"], 2)
        self.assertEqual(master["prompt"], "blue hair")
        snapshot["config"]["model"] = "changed"
        self.assertEqual(self.store.get_generation(sample["id"])["config"]["model"], "anima.safetensors")

    def test_new_variant_inherits_test_settings_without_claiming_parent_preview(self):
        sample = self.sample()
        master = self.piece(sample_asset_id=sample["id"])
        variant = self.store.save("pieces", {"name": "Variant", "master_id": master["id"],
                                             "prompt": "blue hair, tall"})
        self.assertEqual(variant["seed"], master["seed"])
        self.assertEqual(variant["generation_snapshot"], master["generation_snapshot"])
        self.assertNotIn("sample_asset_id", variant)

    def test_capture_rejects_other_project_missing_and_deleted_images(self):
        source = self.sample(project_id=self.other["id"])
        with self.assertRaises(LibraryError):
            self.piece(sample_asset_id=source["id"])
        sample = self.sample()
        self.store.delete_generation(sample["id"])
        for sample_id in (sample["id"], "missing", 123):
            with self.subTest(sample_id=sample_id), self.assertRaises(LibraryError):
                self.piece(sample_asset_id=sample_id)

    def test_parent_protected_and_frozen_snapshot_is_not_live_resource_dependency(self):
        master = self.piece()
        variant = self.piece(name="Variant", master_id=master["id"])
        with self.assertRaises(LibraryConflict):
            self.store.delete("pieces", master["id"])
        self.store.delete("pieces", variant["id"])
        self.store.delete("pieces", master["id"])
        style = self.store.save("styles", {"name": "Paint"})
        sample = self.sample(snapshot={"style": style})
        self.piece(sample_asset_id=sample["id"])
        self.assertTrue(self.store.delete("styles", style["id"])["deleted"])

    def test_sample_deletion_keeps_settings_but_hides_unavailable_preview(self):
        sample = self.sample()
        master = self.piece(sample_asset_id=sample["id"])
        self.assertTrue(self.store.delete_generation(sample["id"])["deleted"])
        current = self.store.get("pieces", master["id"])
        self.assertNotIn("sample_asset_id", current)
        self.assertEqual(current["seed"], master["seed"])
        self.assertEqual(current["generation_snapshot"], master["generation_snapshot"])
        self.assertNotIn("sample_asset_id", self.store.list("pieces")[0])
        edited = self.store.save("pieces", {"id": master["id"], "name": "Renamed"})
        self.assertNotIn("sample_asset_id", edited)

    def test_accepted_preview_is_protected_by_current_and_prior_piece_revisions(self):
        sample = self.sample()
        self.store.review(sample["id"], "accepted")
        master = self.piece(sample_asset_id=sample["id"])
        with self.assertRaises(LibraryConflict):
            self.store.delete_generation(sample["id"])
        self.store.save("pieces", {"id": master["id"], "sample_asset_id": None})
        with self.assertRaises(LibraryConflict):
            self.store.delete_generation(sample["id"])
        self.store.delete("pieces", master["id"])
        self.assertTrue(self.store.delete_generation(sample["id"])["deleted"])

    def test_client_snapshot_requires_capture_but_import_preserves_provenance(self):
        snapshot = {"asset_id": "external", "seed": "18446744073709551615", "config": {"model": "old"}}
        with self.assertRaises(LibraryError):
            self.piece(generation_snapshot=snapshot)
        imported = self.store.save("pieces", {"name": "Imported", "kind": "character",
            "generation_snapshot": snapshot, "seed": "18446744073709551615"}, imported=True)
        self.assertEqual(imported["generation_snapshot"], snapshot)

    def test_pose_control_is_validated_and_inherited_without_catalog_access(self):
        image = io.BytesIO()
        Image.new("RGB", (64, 96)).save(image, "PNG")
        pose = {"model_patch": "anima/pose.safetensors", "representation": "openpose",
                "image_base64": base64.b64encode(image.getvalue()).decode(), "strength": .8}
        master = self.piece(kind="pose", pose_control=pose)
        variant = self.store.save("pieces", {"name": "Same pose", "master_id": master["id"]})
        self.assertEqual(variant["pose_control"], master["pose_control"])
        self.assertEqual(variant["pose_control"]["strength"], .8)
        with self.assertRaises(LibraryError):
            self.piece(kind="pose", pose_control={"model_patch": "../escape"})
        with self.assertRaises(LibraryError):
            self.piece(pose_control=pose)


if __name__ == "__main__":
    unittest.main(verbosity=2)
