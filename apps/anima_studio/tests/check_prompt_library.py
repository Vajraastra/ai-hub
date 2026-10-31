"""CPU checks for named prompt persistence, explicit edits and safe storage."""
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from anima_studio.core.prompt_library import (
    PromptLibrary, PromptLibraryError, PromptConflict, PromptNotFound,
)


class PromptLibraryChecks(unittest.TestCase):
    def setUp(self):
        self.temp_parent = Path(__file__).resolve().parents[3] / ".cache" / "prompt-library-tests"
        self.temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.temp_parent)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = PromptLibrary(self.data)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.temp_parent.resolve()))
        self.temp.cleanup()

    def payload(self, **values):
        return {"name": "Acabado favorito", "style_prompt": "painted lines",
                "style_negative": "grain", "test_prompt": "garden courtyard", **values}

    def revision(self, record, **values):
        return {**record, "expected_revision": record["revision"], **values}

    def test_named_prompts_survive_reopening_without_any_project_or_browser_storage(self):
        saved = self.store.save(self.payload(name="  Acuarela  "))
        self.assertEqual(saved["name"], "Acuarela")
        self.assertEqual(saved["revision"], 1)
        self.assertRegex(saved["id"], r"^[a-f0-9]{32}$")
        self.assertTrue(saved["created_at"])
        self.assertTrue(saved["updated_at"])
        self.assertEqual(PromptLibrary(self.data).get(saved["id"]), saved)
        self.assertEqual(PromptLibrary(self.data).list(), [saved])
        self.assertTrue((self.data / "prompts" / f"{saved['id']}.json").is_file())
        self.assertFalse((self.data / "projects").exists())

    def test_every_text_field_preserves_unicode_whitespace_weights_and_newlines_exactly(self):
        fields = {"style_prompt": "  líneas finas, (blue hair:1.25)\r\n雪 y ñ  ",
                  "style_negative": "\n grain, @artista\t",
                  "test_prompt": "  un viajero\r\nen un jardín\n  "}
        saved = self.store.save(self.payload(**fields))
        reopened = PromptLibrary(self.data).get(saved["id"])
        for field, expected in fields.items():
            self.assertEqual(saved[field], expected)
            self.assertEqual(reopened[field], expected)

    def test_saved_records_contain_only_prompt_fields_and_store_metadata(self):
        saved = self.store.save(self.payload(model="private-checkpoint", project_id="private-project",
                                             loras=[{"file": "identity", "strength": 0.8}],
                                             config={"seed": "18446744073709551615"}))
        self.assertEqual(set(saved), {"id", "name", "style_prompt", "style_negative", "test_prompt",
                                      "revision", "created_at", "updated_at"})

    def test_duplicate_normalized_names_never_overwrite_an_existing_prompt(self):
        first = self.store.save(self.payload(name="Acuarela", style_prompt="original"))
        with self.assertRaises(PromptConflict):
            self.store.save(self.payload(name="  ACUARELA  ", style_prompt="replacement"))
        self.assertEqual(self.store.list(), [first])

    def test_updates_are_explicit_keep_the_identifier_and_increment_the_revision(self):
        first = self.store.save(self.payload())
        changed = self.store.save(self.revision(first, name="Líneas de tinta", style_prompt="  ink lines  "))
        self.assertEqual(changed["id"], first["id"])
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["created_at"], first["created_at"])
        self.assertEqual(changed["style_prompt"], "  ink lines  ")
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(PromptLibrary(self.data).get(first["id"]), changed)

    def test_renaming_an_entry_cannot_take_another_saved_name(self):
        first = self.store.save(self.payload(name="Acuarela"))
        second = self.store.save(self.payload(name="Tinta"))
        with self.assertRaises(PromptConflict):
            self.store.save(self.revision(second, name="ACUARELA"))
        self.assertEqual(self.store.get(first["id"]), first)
        self.assertEqual(self.store.get(second["id"]), second)

    def test_an_outdated_editor_cannot_update_or_delete_a_newer_revision(self):
        first = self.store.save(self.payload())
        second = self.store.save(self.revision(first, style_prompt="new preferred finish"))
        with self.assertRaises(PromptConflict):
            self.store.save(self.revision(first, style_prompt="stale finish"))
        with self.assertRaises(PromptConflict):
            self.store.delete(first["id"], expected_revision=first["revision"])
        self.assertEqual(self.store.get(first["id"]), second)

    def test_a_second_store_instance_also_observes_revision_conflicts(self):
        first = self.store.save(self.payload())
        another = PromptLibrary(self.data)
        self.store.save(self.revision(first, style_prompt="new finish"))
        with self.assertRaises(PromptConflict):
            another.save(self.revision(first, style_prompt="stale finish"))

    def test_concurrent_creations_from_separate_instances_cannot_duplicate_a_name(self):
        another = PromptLibrary(self.data)
        start = threading.Barrier(2)

        def save(store):
            start.wait(timeout=3)
            try:
                return store.save(self.payload(name="Concurrent favorite"))
            except PromptConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(save, store) for store in (self.store, another)]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(len(self.store.list()), 1)

    def test_update_and_delete_require_a_real_positive_integer_revision(self):
        first = self.store.save(self.payload())
        for invalid in (None, True, False, 0, -1, "1", 1.5):
            with self.subTest(expected_revision=invalid):
                with self.assertRaises(PromptLibraryError):
                    self.store.save(self.revision(first, expected_revision=invalid))
                with self.assertRaises(PromptLibraryError):
                    self.store.delete(first["id"], expected_revision=invalid)
        self.assertEqual(self.store.get(first["id"]), first)

    def test_empty_or_invalid_names_and_empty_prompt_sets_are_rejected(self):
        for name in (None, 5, [], "", " \t\n ", "x" * 121):
            with self.subTest(name=name):
                with self.assertRaises(PromptLibraryError):
                    self.store.save(self.payload(name=name))
        with self.assertRaises(PromptLibraryError):
            self.store.save(self.payload(style_prompt=" \n ", style_negative="\t", test_prompt=""))
        self.assertEqual(self.store.list(), [])

    def test_nonstring_and_overlong_prompt_fields_are_rejected_without_saved_artifacts(self):
        for field in ("style_prompt", "style_negative", "test_prompt"):
            for invalid in (None, 42, True, [], {}, "x" * 65537):
                with self.subTest(field=field, value_type=type(invalid).__name__):
                    with self.assertRaises(PromptLibraryError):
                        self.store.save(self.payload(**{field: invalid}))
        self.assertEqual(self.store.list(), [])

    def test_a_negative_only_or_test_scene_only_entry_can_be_saved(self):
        negative = self.store.save(self.payload(name="Negativos", style_prompt="", test_prompt=""))
        scene = self.store.save(self.payload(name="Escena", style_prompt="", style_negative=""))
        self.assertEqual(negative["style_negative"], "grain")
        self.assertEqual(scene["test_prompt"], "garden courtyard")

    def test_mutating_returned_values_does_not_change_saved_or_later_read_records(self):
        saved = self.store.save(self.payload())
        original = dict(saved)
        saved["style_prompt"] = "mutated response"
        listed = self.store.list()
        listed[0]["name"] = "mutated listing"
        self.assertEqual(self.store.get(original["id"]), original)

    def test_missing_entries_report_not_found_instead_of_becoming_new_prompts(self):
        missing = "f" * 32
        with self.assertRaises(PromptNotFound):
            self.store.get(missing)
        with self.assertRaises(PromptNotFound):
            self.store.save(self.payload(id=missing, expected_revision=1))
        with self.assertRaises(PromptNotFound):
            self.store.delete(missing, expected_revision=1)
        self.assertEqual(self.store.list(), [])

    def test_identifiers_cannot_traverse_to_another_file_or_use_windows_path_syntax(self):
        outside = self.root / "outside.json"
        outside.write_text('{"keep":true}', encoding="utf-8")
        for invalid in ("../outside", "..\\outside", "C:\\outside", "a/b", "a\\b", "", ".", None):
            with self.subTest(identifier=invalid):
                with self.assertRaises(PromptLibraryError):
                    self.store.get(invalid)
                with self.assertRaises(PromptLibraryError):
                    self.store.save(self.payload(id=invalid, expected_revision=1))
                with self.assertRaises(PromptLibraryError):
                    self.store.delete(invalid, expected_revision=1)
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"keep":true}')

    def test_failed_atomic_replace_preserves_the_previous_record_and_leaves_no_temporary_file(self):
        first = self.store.save(self.payload())
        path = self.data / "prompts" / f"{first['id']}.json"
        original_bytes = path.read_bytes()
        with patch("anima_studio.core.prompt_library.os.replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.store.save(self.revision(first, style_prompt="should not survive"))
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(self.store.get(first["id"]), first)
        self.assertEqual(list((self.data / "prompts").glob("*.tmp")), [])

    def test_deletion_removes_only_the_selected_prompt_and_survives_reopening(self):
        first = self.store.save(self.payload(name="First"))
        second = self.store.save(self.payload(name="Second"))
        result = self.store.delete(first["id"], expected_revision=first["revision"])
        self.assertEqual(result, {"id": first["id"], "deleted": True})
        self.assertEqual(PromptLibrary(self.data).list(), [second])
        with self.assertRaises(PromptNotFound):
            self.store.get(first["id"])

    def _symlink_or_skip(self, path, target, is_directory=False):
        try:
            path.symlink_to(target, target_is_directory=is_directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"El entorno no permite crear symlinks de prueba: {error}")

    def test_symlinked_prompt_file_never_reads_rewrites_or_deletes_its_target(self):
        self.store.save(self.payload())
        outside = self.root / "outside.json"
        original = self.payload(id="a" * 32, revision=1, created_at="fixture", updated_at="fixture")
        outside.write_text(json.dumps(original), encoding="utf-8")
        link = self.data / "prompts" / f"{'a' * 32}.json"
        self._symlink_or_skip(link, outside)
        try:
            with self.assertRaises(PromptLibraryError):
                self.store.get("a" * 32)
            with self.assertRaises(PromptLibraryError):
                self.store.save(self.revision(original, style_prompt="attack"))
            with self.assertRaises(PromptLibraryError):
                self.store.delete("a" * 32, expected_revision=1)
            self.assertEqual(json.loads(outside.read_text(encoding="utf-8")), original)
        finally:
            link.unlink(missing_ok=True)

    def test_symlinked_prompt_directory_cannot_redirect_new_saves(self):
        folder = self.data / "prompts"
        if folder.exists():
            folder.rmdir()
        outside = self.root / "outside-prompts"
        outside.mkdir()
        self._symlink_or_skip(folder, outside, is_directory=True)
        try:
            with self.assertRaises(PromptLibraryError):
                self.store.save(self.payload())
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            folder.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
