"""Civitai error accounting, credentials and offline sync eligibility checks."""
import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps"))
from model_vault.core.civitai_client import CivitaiClient, CivitaiError
from model_vault.core.vault_service import VaultService


def response(status=200, data=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data or {}).encode()
    return result


def image_bytes(format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (40, 90, 160)).save(stream, format=format)
    return stream.getvalue()


class CivitaiClientTests(unittest.TestCase):
    def preview_directory(self):
        temp_root = Path(__file__).resolve().parents[2] / ".cache" / "test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="vault-preview-", dir=temp_root)
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_preview_download_replaces_destination_only_after_complete_stream(self):
        directory = self.preview_directory()
        destination = directory / "model.preview.jpeg"
        destination.write_bytes(b"previous image")
        content = image_bytes()
        remote = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
        remote.iter_content.return_value = iter([content[:12], b"", content[12:]])
        client = CivitaiClient()
        with patch.object(client, "_request", return_value=remote):
            self.assertTrue(client.download_image("https://image.civitai.com/image.jpeg", str(destination)))
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(list(directory.iterdir()), [destination])
        remote.close.assert_called_once()

    def test_interrupted_preview_leaves_previous_image_or_absent_destination_intact(self):
        for existed in (False, True):
            with self.subTest(existed=existed):
                directory = self.preview_directory()
                destination = directory / "model.preview.jpeg"
                if existed:
                    destination.write_bytes(b"previous image")
                def interrupted_stream(_):
                    yield b"partial image"
                    raise requests.ConnectionError("stream interrupted")
                remote = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
                remote.iter_content.side_effect = interrupted_stream
                client = CivitaiClient()
                with patch.object(client, "_request", return_value=remote):
                    self.assertFalse(client.download_image("https://image.civitai.com/image.jpeg", str(destination)))
                self.assertEqual(list(directory.iterdir()), [destination] if existed else [])
                if existed:
                    self.assertEqual(destination.read_bytes(), b"previous image")
                remote.close.assert_called_once()

    def test_only_404_means_model_not_found(self):
        client = CivitaiClient()
        with patch.object(client, "_request", return_value=response(404)):
            self.assertIsNone(client.get_model_version_by_hash("hash"))
        for status in (401, 403, 500):
            with self.subTest(status=status), patch.object(client, "_request", return_value=response(status)):
                with self.assertRaises(CivitaiError) as caught:
                    client.get_model_version_by_hash("hash")
                self.assertEqual(caught.exception.status_code, status)

    def test_connection_failures_are_errors_after_bounded_retries(self):
        client = CivitaiClient()
        with patch("model_vault.core.civitai_client.requests.request", side_effect=requests.ConnectionError), \
             patch("model_vault.core.civitai_client.time.sleep") as sleep:
            with self.assertRaises(CivitaiError):
                client.get_model_version_by_hash("hash")
        self.assertEqual(sleep.call_count, 2)

    def test_authorization_is_never_sent_to_preview_hosts(self):
        client = CivitaiClient("secret")
        with patch("model_vault.core.civitai_client.requests.request", side_effect=lambda *a, **k: response()) as request:
            for url in ("https://civitai.com/api/v1/models/1", "https://image.civitai.com/image.png",
                        "https://external.example/image.png", "https://civitai.com.example/api/v1/models/1"):
                client._request("GET", url)
        headers = [call.kwargs["headers"] for call in request.call_args_list]
        self.assertEqual(headers[0]["Authorization"], "Bearer secret")
        self.assertTrue(all("Authorization" not in item for item in headers[1:]))

    def test_catalog_query_uses_encoded_request_parameters(self):
        client = CivitaiClient()
        with patch.object(client, "_request", return_value=response(data={"items": []})) as request:
            client.get_models(query="a&b", cursor="abc=next&x")
        self.assertEqual(request.call_args.kwargs["params"]["query"], "a&b")
        self.assertEqual(request.call_args.args[1], "https://civitai.com/api/v1/models")


class CivitaiSyncTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[2] / ".cache" / "test-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="vault-sync-", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "models"
        self.root.mkdir()
        self.file = self.root / "model.safetensors"
        self.file.write_bytes(b"local model")
        self.service = VaultService(str(Path(self.temp.name) / "vault.db"))
        self.addCleanup(self.service.executor.shutdown)
        self.service.scan_and_index(str(self.root))
        self.row = self.service.db.get_all_models()[0]
        self.service.db.update_user_notes(self.row["hash"], "my notes")
        self.service.db.update_custom_tags(self.row["hash"], "my tags")
        self.version = {"id": 42, "modelId": 21, "name": "v1", "baseModel": "Anima", "trainedWords": [],
                        "model": {"name": "Remote title", "type": "Checkpoint", "creator": {"username": "Author"}}}
        self.offline = patch.object(self.service.client, "_request", side_effect=AssertionError("Network forbidden"))
        self.offline.start()
        self.addCleanup(self.offline.stop)

    def test_success_without_tags_is_not_repeated_and_preserves_notes(self):
        with patch.object(self.service.client, "get_model_version_by_hash", return_value=self.version), \
             patch.object(self.service.client, "get_model_info", return_value={"tags": []}), \
             patch("model_vault.core.vault_service.time.sleep"):
            stats = self.service.sync_all()
        self.assertEqual((stats["synced"], stats["errors"]), (1, 0))
        row = self.service.db.get_model_by_hash(self.row["hash"])
        self.assertTrue(row["civitai_synced_at"])
        self.assertEqual((row["custom_tags"], row["user_notes"]), ("my tags", "my notes"))
        self.assertEqual(self.service.pending_models(), [])
        with patch.object(self.service.client, "get_model_version_by_hash", side_effect=AssertionError("Should be skipped")):
            self.assertEqual(self.service.sync_all()["skipped"], 1)

    def test_tags_alone_do_not_mark_incomplete_metadata_as_complete(self):
        self.service.db.update_civitai_data(self.row["hash"], {"civitai_tags": "some tag"})
        self.assertEqual(len(self.service.pending_models()), 1)

    def test_not_found_and_remote_error_have_distinct_counts(self):
        with patch.object(self.service.client, "get_model_version_by_hash", return_value=None), \
             patch("model_vault.core.vault_service.time.sleep"):
            stats = self.service.sync_all()
        self.assertEqual((stats["not_found"], stats["errors"]), (1, 0))
        self.assertEqual(self.service.pending_models(), [])
        with patch.object(self.service.client, "get_model_version_by_hash", side_effect=CivitaiError("offline")):
            stats = self.service.sync_all(force=True)
        self.assertEqual((stats["not_found"], stats["errors"]), (0, 1))
        self.assertEqual(stats["error_detail"][0]["path"], str(self.file))

    def test_invalid_credentials_stop_instead_of_calling_every_model(self):
        with patch.object(self.service.client, "get_model_version_by_hash", side_effect=CivitaiError("bad key", 401)):
            with self.assertRaises(CivitaiError):
                self.service.sync_all(force=True)

    def test_direct_sync_keeps_personal_tags(self):
        with patch.object(self.service.client, "get_model_info", return_value={}):
            self.service.sync_model(str(self.file), self.row["hash"], self.version)
        after = self.service.db.get_model_by_hash(self.row["hash"])
        self.assertEqual((after["custom_tags"], after["user_notes"]), ("my tags", "my notes"))


if __name__ == "__main__":
    unittest.main()
