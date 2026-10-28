"""Real preview/sidecar readiness and non-destructive synchronization regressions."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps"))
from model_vault.core.model_assets import assets_status, find_preview, image_media_type, sidecar_status, update_sidecars
from model_vault.core.vault_service import VaultService
from model_vault.core.civitai_client import CivitaiError


def image_bytes(format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), (30, 120, 210)).save(stream, format=format)
    return stream.getvalue()


def remote_image(content=None):
    response = Mock(status_code=200, headers={"Content-Type": "image/png"})
    response.iter_content.return_value = iter([content if content is not None else image_bytes()])
    return response


class RealAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[2] / ".cache" / "test-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="vault-assets-", dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.models = self.root / "models"
        self.models.mkdir()
        self.file = self.models / "local.safetensors"
        self.file.write_bytes(b"model contents")
        self.service = VaultService(str(self.root / "vault.db"))
        self.addCleanup(self.service.executor.shutdown)
        self.service.scan_and_index(str(self.models))
        self.model = self.service.db.get_all_models()[0]
        self.hash = self.model["hash"]
        self.version = {"id": 42, "modelId": 21, "name": "v1", "baseModel": "Anima", "trainedWords": ["trigger"],
                        "model": {"name": "Remote model", "type": "Checkpoint", "tags": ["style"],
                                  "creator": {"username": "Creator"}},
                        "images": [{"type": "image", "url": "https://image.civitai.com/model.png"}]}
        self.sleep = patch("model_vault.core.vault_service.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def row(self):
        return self.service.db.get_model_by_hash(self.hash)

    def preview(self, suffix=".preview.jpeg"):
        return self.file.with_suffix(suffix)

    def sidecars(self):
        return [self.file.with_suffix(suffix) for suffix in (".cm-info.json", ".metadata.json")]

    def sync(self, version=None, force=False, response=None):
        with patch.object(self.service.client, "get_model_version_by_hash", return_value=version or self.version), \
                patch.object(self.service.client, "get_model_info", return_value={}), \
                patch.object(self.service.client, "_request", return_value=response or remote_image()):
            return self.service.sync_all(force=force)

    def test_batch_sync_completes_actual_json_and_png_even_with_metadata_already_synced(self):
        self.service.db.update_civitai_data(self.hash, {"model_id": 21, "version_id": 42, "display_name": "Local title",
                                                      "base_model": "Anima", "model_type": "Checkpoint", "civitai_tags": "tag"})
        self.service.db.mark_civitai_synced(self.hash)
        self.assertEqual(len(self.service.pending_models()), 1)
        with patch.object(self.service, "_complete_real_assets", wraps=self.service._complete_real_assets) as complete:
            stats = self.sync()
        complete.assert_called_once()
        self.assertEqual((stats["synced"], stats["previews_downloaded"], stats["sidecars_updated"], stats["errors"]), (1, 1, 2, 0))
        self.assertEqual(image_media_type(self.preview()), "image/png")
        self.assertEqual(json.loads(self.sidecars()[0].read_text())["ModelId"], 21)
        self.assertEqual(json.loads(self.sidecars()[1].read_text())["sha256"], self.hash)
        self.assertTrue(assets_status(self.row())["assets_complete"])
        self.assertEqual(self.service.pending_models(), [])

    def test_normal_and_forced_sync_keep_existing_image_and_unchanged_json_bytes(self):
        self.sync()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in [self.preview(), *self.sidecars()]}
        with patch.object(self.service.client, "download_image", side_effect=AssertionError("Must preserve image")):
            self.assertEqual(self.sync()["skipped"], 1)
            stats = self.sync(force=True)
        self.assertEqual((stats["previews_downloaded"], stats["sidecars_updated"]), (0, 0))
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}, before)
        self.service.scan_and_index(str(self.models))
        self.assertEqual({path: path.read_bytes() for path in before}, {path: value[0] for path, value in before.items()})

    def test_missing_preview_remains_pending_after_image_network_error(self):
        stats = self.sync(version={**self.version, "images": []})
        self.assertEqual((stats["no_preview"], stats["errors"]), (1, 0))
        self.assertEqual(self.service.pending_models(), [])
        with patch.object(self.service.client, "download_image", side_effect=CivitaiError("network interrupted")):
            stats = self.sync(force=True)
        self.assertEqual(stats["errors"], 1)
        self.assertFalse(self.row()["civitai_assets_synced_at"])
        self.assertEqual(len(self.service.pending_models()), 1)
        self.assertFalse(self.preview().exists())

    def test_video_only_or_no_images_is_explicitly_unavailable_until_forced(self):
        version = {**self.version, "images": [{"type": "video", "url": "https://image.civitai.com/clip.mp4"}]}
        stats = self.sync(version=version)
        self.assertEqual((stats["no_preview"], stats["errors"]), (1, 0))
        self.assertEqual(assets_status(self.row())["preview_status"], "unavailable")
        self.assertEqual(self.service.pending_models(), [])
        self.assertEqual(self.sync(force=True)["previews_downloaded"], 1)

    def test_nonimage_response_or_invalid_image_never_creates_a_preview(self):
        for response in (remote_image(b"invalid"), remote_image(b""), Mock(status_code=200, headers={"Content-Type": "text/html"})):
            with self.subTest(response=response):
                stats = self.sync(force=True, response=response)
                self.assertEqual(stats["previews_downloaded"], 0)
                self.assertEqual(stats["errors"], 1)
                self.assertFalse(self.preview().exists())
                self.assertEqual(len(self.service.pending_models()), 1)

    def test_matching_json_merge_preserves_unknown_and_personal_fields(self):
        cm, metadata = self.sidecars()
        cm.write_text(json.dumps({"Hashes": {"SHA256": self.hash, "CUSTOM": "keep"}, "UserNotes": "personal", "Extra": {"mine": 1}}))
        metadata.write_text(json.dumps({"sha256": self.hash, "personal_tags": ["mine"], "civitai": {"my_annotation": "keep"}}))
        stats = self.sync()
        self.assertEqual(stats["errors"], 0)
        self.assertEqual(json.loads(cm.read_text())["UserNotes"], "personal")
        self.assertEqual(json.loads(cm.read_text())["Hashes"]["CUSTOM"], "keep")
        self.assertEqual(json.loads(metadata.read_text())["personal_tags"], ["mine"])
        self.assertEqual(json.loads(metadata.read_text())["civitai"]["my_annotation"], "keep")

    def test_corrupt_personal_or_wrong_hash_json_is_preserved_and_stays_pending(self):
        examples = (b"{invalid", b'{"my_notes":"untouched"}', b'{"sha256":"different","civitai":{"id":2,"modelId":3}}')
        for content in examples:
            with self.subTest(content=content):
                self.sidecars()[1].write_bytes(content)
                stats = self.sync(force=True)
                self.assertEqual(self.sidecars()[1].read_bytes(), content)
                self.assertEqual(stats["errors"], 1)
                status = assets_status(self.row())
                self.assertEqual(status["sidecar_status"], "protected")
                self.assertFalse(status["has_sidecars"])
                self.assertFalse(status["assets_complete"])
                self.assertEqual(len(self.service.pending_models()), 1)

    def test_hash_only_json_is_not_ready_but_is_completed_by_normal_sync(self):
        cm, metadata = self.sidecars()
        cm.write_text(json.dumps({"Hashes": {"SHA256": self.hash}}))
        metadata.write_text(json.dumps({"sha256": self.hash}))
        self.preview().write_bytes(image_bytes("JPEG"))
        self.service.db.mark_civitai_synced(self.hash)
        self.assertEqual(sidecar_status(str(self.file), file_hash=self.hash), "incomplete")
        self.assertFalse(assets_status(self.row())["assets_complete"])
        self.assertEqual(len(self.service.pending_models()), 1)
        stats = self.sync()
        self.assertEqual((stats["sidecars_updated"], stats["previews_downloaded"], stats["errors"]), (2, 0, 0))
        self.assertEqual(self.service.pending_models(), [])

    def test_preview_and_valid_json_in_alias_are_preferred_over_protected_primary(self):
        self.sync()
        alias = self.models / "alias.safetensors"
        alias.write_bytes(self.file.read_bytes())
        self.service.scan_and_index(str(self.models))
        for path, suffix in zip(self.sidecars(), (".cm-info.json", ".metadata.json")):
            path.rename(alias.with_suffix(suffix))
        for path in self.sidecars():
            path.write_text('{"personal":"primary only"}')
        self.preview().rename(alias.with_suffix(".preview.png"))
        current = self.row()
        self.assertEqual(find_preview(str(self.file), current["file_paths"]), str(alias.with_suffix(".preview.png")))
        self.assertEqual(sidecar_status(str(self.file), current["file_paths"], self.hash), "complete")
        before = {path: path.read_bytes() for path in self.sidecars()}
        stats = self.sync(force=True)
        self.assertEqual((stats["sidecars_protected"], stats["errors"], stats["previews_downloaded"]), (0, 0, 0))
        self.assertEqual({path: path.read_bytes() for path in self.sidecars()}, before)

    def test_empty_remote_values_do_not_erase_known_metadata(self):
        self.sync()
        before = self.row()
        empty = {"id": 42, "modelId": 21, "name": "", "baseModel": "Unknown", "model": {}, "images": []}
        self.sync(version=empty, force=True)
        after = self.row()
        for key in ("display_name", "base_model", "model_type", "creator_name", "triggers", "civitai_tags", "version_name"):
            self.assertEqual(after[key], before[key], key)

    def test_json_replace_failure_keeps_original_and_reports_pending(self):
        self.sync()
        cm = self.sidecars()[0]
        before = cm.read_bytes()
        version = {**self.version, "name": "v2"}
        with patch("model_vault.core.model_assets.os.replace", side_effect=OSError("locked")):
            stats = self.sync(version=version, force=True)
        self.assertEqual(cm.read_bytes(), before)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(list(self.models.glob(".vault-sidecar-*")), [])


if __name__ == "__main__":
    unittest.main()
