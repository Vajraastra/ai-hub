"""Local inventory regression checks; no GPU, network, or real model library."""
import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps"))
from model_vault.core import vault_service
from model_vault.core.scanner import scan_models
from model_vault.core.header_classifier import classify


class LocalInventoryTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[2] / ".cache" / "test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="vault-index-", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "models"
        self.root.mkdir()
        self.service = vault_service.VaultService(str(Path(self.temp.name) / "vault.db"))
        self.addCleanup(self.service.executor.shutdown)
        self.network = patch.object(self.service.client, "_request", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.sidecar_write = patch.object(self.service, "_update_local_sidecars", side_effect=AssertionError("Writes forbidden"))
        self.sidecar_write.start()
        self.addCleanup(self.sidecar_write.stop)

    def model(self, name="checkpoints/local.safetensors", content=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            header = json.dumps({"model.diffusion_model.input_blocks.0.0.weight":
                                 {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
            content = struct.pack("<Q", len(header)) + header + b"\0" * 4
        path.write_bytes(content)
        return path

    def record(self, path):
        return self.service.db.get_model_by_hash(hashlib.sha256(path.read_bytes()).hexdigest())

    def test_unknown_to_civitai_is_indexed_without_network_or_sidecar_writes(self):
        path = self.model()
        before = path.read_bytes()
        progress = []
        stats = self.service.scan_and_index(str(self.root), progress_callback=lambda *args: progress.append(args))
        self.assertEqual({k: stats[k] for k in ("indexed", "added", "errors")},
                         {"indexed": 1, "added": 1, "errors": 0})
        row = self.record(path)
        self.assertEqual((row["model_type"], row["base_model"]), ("checkpoint", "SD 1.5"))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])
        self.assertEqual(progress, [(1, 1, path.name)])

    def test_repeated_scan_reuses_hash_with_normalized_windows_path(self):
        path = self.model()
        self.service.scan_and_index(str(self.root))
        row = self.record(path)
        self.service.db.update_file_path(row["hash"], str(path).replace("\\", "/"))
        with patch.object(vault_service, "calculate_sha256", side_effect=AssertionError("Unexpected rehash")):
            stats = self.service.scan_and_index(str(self.root))
        self.assertEqual((stats["indexed"], stats["added"], stats["errors"]), (1, 0, 0))
        self.assertEqual(len(self.service.db.get_all_models()), 1)

    def test_deep_scan_rehashes_known_files(self):
        self.model()
        self.service.scan_and_index(str(self.root))
        with patch.object(vault_service, "calculate_sha256", wraps=vault_service.calculate_sha256) as hasher:
            stats = self.service.scan_and_index(str(self.root), deep_scan=True)
        self.assertEqual(hasher.call_count, 1)
        self.assertEqual((stats["indexed"], stats["added"], stats["errors"]), (1, 0, 0))

    def test_rescan_preserves_remote_metadata_and_manual_edits(self):
        path = self.model()
        self.service.scan_and_index(str(self.root))
        row = self.record(path)
        self.service.db.update_civitai_data(row["hash"], {
            "display_name": "Remote title", "base_model": "Anima", "model_type": "Checkpoint",
            "creator_name": "Author", "version_id": 42, "model_id": 21,
            "version_name": "v2", "triggers": "remote trigger", "description": "Remote description",
            "civitai_tags": "remote tags",
        })
        self.service.db.update_custom_tags(row["hash"], "my tags")
        self.service.db.update_user_notes(row["hash"], "my notes")
        before = self.record(path)
        path.with_suffix(".cm-info.json").write_text(json.dumps({
            "ModelName": "Sidecar title", "BaseModel": "SDXL", "ModelType": "LORA",
        }))
        self.service.scan_and_index(str(self.root))
        after = self.record(path)
        for key in ("display_name", "base_model", "model_type", "creator_name", "version_id", "model_id",
                    "version_name", "triggers", "description", "civitai_tags", "custom_tags", "user_notes", "date_added"):
            self.assertEqual(after[key], before[key], key)

    def test_same_hash_at_new_path_retains_notes_and_metadata(self):
        path = self.model()
        self.service.scan_and_index(str(self.root))
        original = self.record(path)
        self.service.db.update_custom_tags(original["hash"], "keep tags")
        self.service.db.update_user_notes(original["hash"], "keep notes")
        new_path = path.with_name("renamed.safetensors")
        path.rename(new_path)
        stats = self.service.scan_and_index(str(self.root))
        after = self.record(new_path)
        self.assertEqual((stats["indexed"], stats["added"]), (1, 0))
        self.assertEqual((after["file_path"], after["name"]), (str(new_path), new_path.name))
        self.assertEqual((after["custom_tags"], after["user_notes"]), ("keep tags", "keep notes"))
        self.assertEqual(len(self.service.db.get_all_models()), 1)

    def test_matching_sidecar_metadata_is_read_but_hash_is_calculated(self):
        path = self.model()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(".cm-info.json").write_text(json.dumps({
            "Hashes": {"SHA256": digest.upper()}, "BaseModel": "Anima", "ModelType": "Checkpoint",
            "ModelName": "WAI-ANIMA", "VersionId": 4, "ModelId": 2, "TrainedWords": ["hello"],
        }))
        with patch.object(vault_service, "calculate_sha256", wraps=vault_service.calculate_sha256) as hasher:
            stats = self.service.scan_and_index(str(self.root))
        self.assertEqual(hasher.call_count, 1)
        self.assertEqual(stats["errors"], 0)
        row = self.record(path)
        self.assertEqual((row["display_name"], row["base_model"], row["triggers"]), ("WAI-ANIMA", "Anima", "hello"))

    def test_stale_and_malformed_sidecars_do_not_hide_local_model(self):
        path = self.model()
        path.with_suffix(".metadata.json").write_text("{invalid")
        path.with_suffix(".cm-info.json").write_text(json.dumps({
            "Hashes": {"SHA256": "0" * 64}, "ModelName": "Wrong model", "BaseModel": "Anima",
        }))
        stats = self.service.scan_and_index(str(self.root))
        row = self.record(path)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual((row["display_name"], row["base_model"]), (path.name, "SD 1.5"))

    def test_saved_civitai_metadata_is_imported_offline(self):
        path = self.model()
        path.with_suffix(".metadata.json").write_text(json.dumps({
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "civitai": {"id": 42, "modelId": 21, "name": "v1", "baseModel": "Anima",
                        "trainedWords": ["word"], "description": "<p>Description</p>",
                        "model": {"name": "WAI-ANIMA", "type": "Checkpoint", "tags": ["tag"],
                                  "creator": {"username": "Author"}}},
        }))
        stats = self.service.scan_and_index(str(self.root))
        row = self.record(path)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual((row["display_name"], row["base_model"], row["creator_name"]),
                         ("WAI-ANIMA", "Anima", "Author"))
        self.assertEqual((row["version_id"], row["model_id"], row["civitai_tags"], row["description"]),
                         (42, 21, "tag", "Description"))

    def test_database_context_closes_connection(self):
        with self.service.db._get_conn() as conn:
            conn.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_hash_failure_is_reported_and_other_files_are_indexed(self):
        bad = self.model("checkpoints/a.safetensors")
        good = self.model("checkpoints/b.safetensors", b"valid fallback")
        hasher = vault_service.calculate_sha256
        with patch.object(vault_service, "calculate_sha256", side_effect=lambda path: None if path == str(bad) else hasher(path)):
            stats = self.service.scan_and_index(str(self.root))
        self.assertEqual((stats["indexed"], stats["added"], stats["errors"]), (1, 1, 1))
        self.assertEqual(stats["error_detail"][0]["path"], str(bad))
        self.assertIsNotNone(self.record(good))

    def test_supported_extensions_and_directory_links(self):
        paths = [self.model(f"diffusion_models/model{i}{ext}", str(i).encode())
                 for i, ext in enumerate((".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".gguf", ".bin"))]
        hidden = self.model("linked/outside.safetensors")
        with patch("model_vault.core.scanner._is_directory_link", side_effect=lambda path: Path(path).name == "linked"):
            found = {m["path"] for m in scan_models(str(self.root))}
        self.assertEqual(found, {str(path) for path in paths})
        self.assertNotIn(str(hidden), found)

    def test_replaced_path_rehashes_and_transfers_only_personal_annotations(self):
        path = self.model()
        self.service.scan_and_index(str(self.root))
        old = self.record(path)
        self.service.db.update_user_notes(old["hash"], "keep notes")
        self.service.db.update_custom_tags(old["hash"], "keep tags")
        self.service.db.update_civitai_data(old["hash"], {"model_id": 44, "version_id": 55, "base_model": "Anima"})
        path.write_bytes(b"different model")
        stats = self.service.scan_and_index(str(self.root))
        row = self.record(path)
        self.assertEqual(stats["errors"], 0)
        self.assertEqual((row["user_notes"], row["custom_tags"]), ("keep notes", "keep tags"))
        self.assertIsNone(row["model_id"])
        self.assertIsNone(self.service.db.get_model_by_hash(old["hash"]))
        self.assertEqual(len(self.service.db.get_all_models()), 1)

    def test_changed_mtime_same_size_invalidates_cached_hash(self):
        path = self.model(content=b"before")
        self.service.scan_and_index(str(self.root))
        old = self.record(path)
        stamp = path.stat().st_mtime_ns
        path.write_bytes(b"after!")
        os.utime(path, ns=(stamp + 1000000, stamp + 1000000))
        self.service.scan_and_index(str(self.root))
        self.assertNotEqual(self.record(path)["hash"], old["hash"])

    def test_duplicate_copies_keep_all_locations_and_need_no_repeat_hash(self):
        original = self.model()
        alias = self.model("checkpoints/krita/copy.safetensors", original.read_bytes())
        self.service.scan_and_index(str(self.root))
        row = self.record(original)
        self.assertEqual(row["file_path"], str(original))
        self.assertEqual(set(row["file_paths"]), {str(original), str(alias)})
        self.assertEqual(self.service.db.get_model_by_path(str(alias))["hash"], row["hash"])
        with patch.object(vault_service, "calculate_sha256", side_effect=AssertionError("Unexpected rehash")):
            stats = self.service.scan_and_index(str(self.root))
        self.assertEqual((stats["indexed"], stats["added"], stats["errors"]), (2, 0, 0))

    def test_atomic_replacement_with_same_size_and_timestamp_invalidates_hash(self):
        path = self.model(content=b"before")
        self.service.scan_and_index(str(self.root))
        old = self.record(path)
        stamp = path.stat().st_mtime_ns
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"after!")
        os.utime(replacement, ns=(stamp, stamp))
        os.replace(replacement, path)
        stats = self.service.scan_and_index(str(self.root))
        self.assertEqual(stats["errors"], 0)
        self.assertNotEqual(self.record(path)["hash"], old["hash"])

    def test_hardlinks_read_content_once_even_during_deep_scan(self):
        original = self.model()
        alias = original.parent / "krita" / original.name
        alias.parent.mkdir()
        try:
            os.link(original, alias)
        except PermissionError:
            self.skipTest("El entorno restringido impide crear hardlinks; repetir en Windows sin restricción")
        for deep in (False, True):
            with patch.object(vault_service, "calculate_sha256", wraps=vault_service.calculate_sha256) as hasher:
                stats = self.service.scan_and_index(str(self.root), deep_scan=deep)
            self.assertEqual(hasher.call_count, 1)
            self.assertEqual(stats["errors"], 0)
        self.assertEqual(self.record(original)["file_path"], str(original))

    def test_replacement_with_existing_annotated_hash_preserves_both_identities(self):
        first = self.model("checkpoints/first.safetensors", b"first model")
        second = self.model("checkpoints/second.safetensors", b"second model")
        self.service.scan_and_index(str(self.root))
        old = self.record(first)
        target = self.record(second)
        for row, note in ((old, "first notes"), (target, "second notes")):
            self.service.db.update_user_notes(row["hash"], note)
            self.service.db.update_custom_tags(row["hash"], note + " tags")
        first.write_bytes(second.read_bytes())
        self.service.scan_and_index(str(self.root))
        kept_old = self.service.db.get_model_by_hash(old["hash"])
        kept_target = self.service.db.get_model_by_hash(target["hash"])
        self.assertTrue(kept_old["missing"])
        self.assertEqual(kept_old["file_paths"], [])
        self.assertEqual((kept_old["user_notes"], kept_old["custom_tags"]), ("first notes", "first notes tags"))
        self.assertEqual((kept_target["user_notes"], kept_target["custom_tags"]), ("second notes", "second notes tags"))
        self.assertEqual(set(kept_target["file_paths"]), {str(first), str(second)})

    def test_absent_files_keep_notes_and_are_marked_missing(self):
        path = self.model()
        self.service.scan_and_index(str(self.root))
        row = self.record(path)
        self.service.db.update_user_notes(row["hash"], "keep me")
        path.unlink()
        self.service.scan_and_index(str(self.root))
        after = self.service.db.get_model_by_hash(row["hash"])
        self.assertTrue(after["missing"])
        self.assertEqual(after["user_notes"], "keep me")
        self.assertEqual(self.service.pending_models(force=True), [])

    def test_move_updates_cached_location_atomically(self):
        path = self.model()
        self.service.scan_and_index(str(self.root))
        row = self.record(path)
        target = path.with_name("moved.safetensors")
        path.rename(target)
        self.service.db.update_file_path(row["hash"], str(target), old_path=str(path))
        after = self.record(target)
        self.assertEqual(after["file_paths"], [str(target)])
        with patch.object(vault_service, "calculate_sha256", side_effect=AssertionError("Unexpected rehash")):
            stats = self.service.scan_and_index(str(self.root))
        self.assertEqual(stats["errors"], 0)

    def test_legacy_cached_hash_is_bootstrapped_once_without_rereading_weights(self):
        path = self.model()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.service.db.index_local_model({"hash": digest, "name": path.name, "file_path": str(path)})
        with patch.object(vault_service, "calculate_sha256", side_effect=AssertionError("Unexpected rehash")):
            stats = self.service.scan_and_index(str(self.root))
        self.assertEqual(stats["errors"], 0)
        cache = self.service.db.get_file_records()[0]
        self.assertEqual((cache["file_size"], cache["file_mtime_ns"]), (path.stat().st_size, path.stat().st_mtime_ns))

    def test_anima_header_and_sft_are_detected_offline(self):
        for prefix in ("net.", "model.diffusion_model."):
            keys = [prefix + suffix for suffix in ("llm_adapter.out_proj.weight",
                    "blocks.0.adaln_modulation_cross_attn.1.weight", "blocks.0.adaln_modulation_self_attn.1.weight")]
            raw = json.dumps({key: {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]} for key in keys}).encode()
            path = self.model("diffusion_models/anima.sft", struct.pack("<Q", len(raw)) + raw + b"\0" * 4)
            result = classify(str(path))
            self.assertEqual((result.model_type, result.arch), ("diffusion_model", "Anima"))
        pth = self.model("embeddings/small.pth", b"small")
        self.assertEqual(classify(str(pth)).model_type, "embedding")


if __name__ == "__main__":
    unittest.main()
