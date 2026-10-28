"""Local gallery metadata and preview delivery, without GPU or network services."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "apps"))
sys.path.insert(0, str(REPO / "hub-webui"))
from fastapi import FastAPI
from PIL import Image
from anima_studio.core.catalog_assets import CatalogAssets
import anima_studio_routes as routes


def preview(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), "teal").save(path, format="PNG")


class AssetsChecks(unittest.TestCase):
    def setUp(self):
        parent = REPO / ".cache" / "anima-asset-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name) / "models"
        self.root.mkdir()
        self.path = self.root / "checkpoints" / "cryptic.safetensors"
        self.path.parent.mkdir()
        self.path.write_bytes(b"fixture")
        self.catalogue = {"models": [{"file": "checkpoints::cryptic.safetensors", "name": "cryptic",
                                     "relative_path": "checkpoints/cryptic.safetensors"}], "loras": []}

    def tearDown(self):
        self.temp.cleanup()

    def test_human_name_version_search_terms_and_real_image_type(self):
        self.path.with_suffix(".cm-info.json").write_text(json.dumps({
            "ModelName": "WAI-ANIMA", "VersionName": "v1.0", "TrainedWords": ["painted", "fantasy"]}))
        image = self.path.with_suffix(".preview.jpeg")
        preview(image)
        before = hashlib.sha256(image.read_bytes()).hexdigest()
        assets = CatalogAssets(self.root)
        record = assets.enrich(self.catalogue)["models"][0]
        self.assertEqual(record["display_name"], "WAI-ANIMA · v1.0")
        self.assertEqual(record["tags"], ["painted", "fantasy"])
        identifier = record["thumbnail_url"].split("/")[-1].split("?")[0]
        self.assertNotIn(str(self.root), record["thumbnail_url"])
        self.assertEqual(assets.thumbnail(identifier), (image, "image/png"))
        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), before)

    def test_metadata_sidecar_and_broken_optional_metadata(self):
        self.path.with_suffix(".cm-info.json").write_text("{broken")
        self.path.with_suffix(".metadata.json").write_text(json.dumps({"civitai": {
            "modelName": "Watercolor", "versionName": "Ink", "tags": ["style"]}}))
        record = CatalogAssets(self.root).enrich(self.catalogue)["models"][0]
        self.assertEqual(record["display_name"], "Watercolor · Ink")
        self.assertEqual(record["tags"], ["style"])
        self.assertIsNone(record["thumbnail_url"])

    def test_no_vault_creation_and_no_required_preview(self):
        missing = Path(self.temp.name) / "missing.db"
        record = CatalogAssets(self.root, missing).enrich(self.catalogue)["models"][0]
        self.assertEqual(record["display_name"], "cryptic")
        self.assertIsNone(record["thumbnail_url"])
        self.assertFalse(missing.exists())

    def test_vault_alias_preview_and_custom_name_are_read_only(self):
        alias = self.root / "diffusion_models" / "alias.safetensors"
        alias.parent.mkdir()
        alias.write_bytes(b"fixture")
        image = alias.with_suffix(".preview.png")
        preview(image)
        database = Path(self.temp.name) / "vault.db"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE models (hash TEXT, file_path TEXT, display_name TEXT, custom_tags TEXT)")
            db.execute("CREATE TABLE model_files (hash TEXT, file_path TEXT)")
            db.execute("INSERT INTO models VALUES (?,?,?,?)", ("one", str(alias), "My painted style", "favorite, character"))
            db.execute("INSERT INTO model_files VALUES (?,?)", ("one", str(self.path)))
        db.close()
        before = database.read_bytes()
        assets = CatalogAssets(self.root, database)
        record = assets.enrich(self.catalogue)["models"][0]
        self.assertEqual(record["display_name"], "My painted style")
        self.assertEqual(record["tags"], ["favorite", "character"])
        identifier = record["thumbnail_url"].split("/")[-1].split("?")[0]
        self.assertEqual(assets.thumbnail(identifier)[0], image)
        self.assertEqual(database.read_bytes(), before)

    def test_unknown_or_non_image_assets_are_not_served(self):
        assets = CatalogAssets(self.root)
        with self.assertRaises(ValueError):
            assets.thumbnail("../cryptic.safetensors")
        self.path.with_suffix(".preview.png").write_bytes(b"not an image")
        record = assets.enrich(self.catalogue)["models"][0]
        identifier = record["thumbnail_url"].split("/")[-1].split("?")[0]
        with self.assertRaises(ValueError):
            assets.thumbnail(identifier)

    def test_outside_library_is_not_exposed(self):
        outside = Path(self.temp.name) / "outside.safetensors"
        outside.write_bytes(b"fixture")
        preview(outside.with_suffix(".preview.png"))
        self.catalogue["models"][0]["relative_path"] = "../outside.safetensors"
        record = CatalogAssets(self.root).enrich(self.catalogue)["models"][0]
        self.assertIsNone(record["thumbnail_url"])


class GalleryApiChecks(unittest.IsolatedAsyncioTestCase):
    async def test_catalogue_and_thumbnail_roundtrip_without_gpu(self):
        parent = REPO / ".cache" / "anima-asset-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            path = root / "diffusion_models" / "plain.safetensors"
            path.parent.mkdir()
            header = json.dumps({"net.llm_adapter.out_proj.weight": {"shape": [1024, 1024]},
                                 "net.blocks.0.self_attn.q_proj.weight": {"shape": [2048, 2048]}}).encode()
            path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 8)
            preview(path.with_suffix(".preview.jpeg"))
            api = FastAPI()
            api.include_router(routes.anima_studio_router)

            async def request(endpoint):
                messages = []
                async def receive():
                    await asyncio.Event().wait()
                async def send(message):
                    messages.append(message)
                await api({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                           "http_version": "1.1", "method": "GET", "scheme": "http", "path": endpoint,
                           "raw_path": endpoint.encode(), "query_string": b"", "headers": [],
                           "server": ("test", 80), "client": ("test", 1)}, receive, send)
                start = next(message for message in messages if message["type"] == "http.response.start")
                body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
                return start["status"], dict(start["headers"]), body

            with patch.object(routes, "_models_root", return_value=root), \
                    patch.object(routes, "_catalog", None), patch.object(routes, "_catalog_path", None), \
                    patch.object(routes, "_catalog_result", None), patch.object(routes, "_catalog_assets", None):
                status, _, body = await request("/api/anima-studio/catalog")
                self.assertEqual(status, 200)
                catalogue = json.loads(body)
                url = catalogue["models"][0]["thumbnail_url"].split("?")[0]
                status, headers, body = await request(url)
                self.assertEqual(status, 200)
                self.assertEqual(headers[b"content-type"], b"image/png")
                self.assertEqual(Image.open(io.BytesIO(body)).size, (32, 48))
                self.assertEqual((await request("/api/anima-studio/catalog/thumbnail/unknown"))[0], 404)


if __name__ == "__main__":
    unittest.main()
