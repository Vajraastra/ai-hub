"""Real ASGI requests with simulated generation; no GPU or HTTP client dependency."""
import asyncio
import base64
import hashlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "hub-webui"))
sys.path.insert(0, str(REPO / "apps"))

from fastapi import FastAPI
from PIL import Image
import comfy_jobs
import anima_studio_routes as routes
from anima_studio.core.library import Library
from anima_studio.core.storage import StorageLifecycle


def png(color="blue", size=(128, 128)):
    stream = io.BytesIO()
    Image.new("RGBA", size, color).save(stream, format="PNG")
    return stream.getvalue()


class FakeCatalog:
    DATA = {
        "models": [{"file": "anima/base.safetensors", "name": "Anima Base"}],
        "loras": [{"file": "anima/paint.safetensors", "name": "Paint"}],
        "text_encoders": [{"file": "anima/qwen.safetensors", "name": "Qwen"}],
        "vaes": [{"file": "qwen_image_vae.safetensors", "name": "VAE"}],
    }

    def scan(self):
        return self.DATA

    def validate(self, params):
        result = {}
        for field, group in (("model", "models"), ("text_encoder", "text_encoders"), ("vae", "vaes")):
            value = getattr(params, field)
            found = next((entry for entry in self.DATA[group] if entry["file"] == value), None)
            if found is None:
                raise ValueError(f"{field} no está en el catálogo Anima")
            result[field] = {**found, "fingerprint": "fixture"}
        for item in params.loras:
            if item["file"] not in [entry["file"] for entry in self.DATA["loras"]]:
                raise ValueError("LoRA no está en el catálogo Anima")
        return result


class ApiChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_parent = REPO / ".cache" / "api-tests"
        self.temp_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.temp_parent)
        self.store = Library(Path(self.temp.name))
        self.old_store, routes._library = routes._library, self.store
        self.old_jobs, routes._jobs = routes._jobs, {}
        self.old_tasks, routes._tasks = routes._tasks, set()
        self.old_active, comfy_jobs._active = comfy_jobs._active, None
        self.one = self.store.save("projects", {"name": "Game One", "categories": ["characters", "backgrounds"],
                                               "formats": [{"id": "small", "name": "Small", "width": 128,
                                                            "height": 128, "alpha": False}]})
        self.two = self.store.save("projects", {"name": "Game Two", "categories": ["characters", "items"],
                                               "formats": [{"id": "wide", "name": "Wide", "width": 256,
                                                            "height": 128, "alpha": False}]})
        self.api = FastAPI()
        self.api.include_router(routes.anima_studio_router)
        self.catalog = FakeCatalog()
        self.models_path = Path(self.temp.name) / "models"
        self.comfy_root = Path(self.temp.name) / "comfy"
        self.storage = StorageLifecycle(self.store, self.comfy_root)

        async def backend_read(path):
            self.assertEqual(path, "/queue", "La prueba no debe consultar un ComfyUI real")
            return {"queue_running": [], "queue_pending": []}

        self.patches = [
            patch.object(routes, "_storage", return_value=self.storage),
            patch.object(routes, "_models_root", return_value=self.models_path),
            patch.object(routes, "_catalog", self.catalog),
            patch.object(routes, "_catalog_path", self.models_path),
            patch.object(routes, "_catalog_result", self.catalog.scan()),
            patch.object(routes, "_catalog_time", time.monotonic()),
            patch.object(routes._comfy, "health_check", AsyncMock(return_value=True)),
            patch.object(routes._comfy, "_get", side_effect=backend_read),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        pending = list(routes._tasks)
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for item in reversed(self.patches):
            item.stop()
        routes._library, routes._jobs, routes._tasks = self.old_store, self.old_jobs, self.old_tasks
        comfy_jobs._active = self.old_active
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(self.temp_parent.resolve()))
        self.temp.cleanup()

    async def request(self, method, endpoint, payload=None, **query):
        body = json.dumps(payload).encode() if payload is not None else b""
        messages = []
        received = False

        async def receive():
            nonlocal received
            if received:
                # A real ASGI server waits for input/disconnection after the body.
                # Repeating http.request without yielding starves FileResponse's
                # disconnect listener on newer Starlette versions.
                await asyncio.Event().wait()
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        path = f"/api/anima-studio{endpoint}"
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                 "query_string": urlencode(query).encode(), "root_path": "", "server": ("fixture", 80),
                 "client": ("fixture", 1), "headers": [(b"content-type", b"application/json")]}
        await asyncio.wait_for(self.api(scope, receive, send), 5)
        response = b"".join(message.get("body", b"") for message in messages)
        try:
            value = json.loads(response)
        except (json.JSONDecodeError, UnicodeError):
            value = response
        return messages[0]["status"], value

    def payload(self, **values):
        return {"project_id": self.one["id"], "prompt": "A friendly fantasy character", "category": "characters",
                "config": {"model": "anima/base.safetensors", "text_encoder": "anima/qwen.safetensors",
                           "vae": "qwen_image_vae.safetensors", "seed": "18446744073709551615",
                           "width": 128, "height": 128, "steps": 2}, **values}

    async def finish(self, job_id):
        for _ in range(100):
            await asyncio.sleep(0.005)
            status, record = await self.request("GET", f"/jobs/{job_id}")
            self.assertEqual(status, 200)
            if record["status"] != "running":
                self.assertEqual(record["status"], "done", record)
                return record["result"]
        self.fail("La generación simulada no terminó")

    @staticmethod
    async def render(client, params, on_progress=None, **kwargs):
        return {"image": png(), "metadata": {"seed": str(params.seed), "width": params.width,
                                               "height": params.height, "mode": params.mode}}

    async def test_compose_isolates_projects_and_preserves_style_revision(self):
        first = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"], "prompt": "painted",
                                           "config": {"cfg": 4}})
        self.store.save("styles", {"id": first["id"], "prompt": "new paint"})
        foreign = self.store.save("pieces", {"name": "Foreign hero", "kind": "character", "project_id": self.two["id"]})
        status, result = await self.request("POST", "/compose", {"project_id": self.one["id"],
                                                                  "style_id": first["id"], "style_revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(result["prompt"], "painted")
        status, _ = await self.request("POST", "/compose", {"project_id": self.one["id"], "piece_ids": [foreign["id"]]})
        self.assertEqual(status, 400)
        status, _ = await self.request("POST", "/compose", {"project_id": self.one["id"], "format_id": "wide"})
        self.assertEqual(status, 400)

    async def test_delete_saved_resources_reports_dependency_then_removes_them(self):
        style = self.store.save("styles", {"name": "Mistake", "project_id": self.one["id"]})
        piece = self.store.save("pieces", {"name": "Hero", "kind": "character", "project_id": self.one["id"]})
        recipe = self.store.save("recipes", {"name": "Greeting", "project_id": self.one["id"],
                                             "style_id": style["id"], "piece_ids": [piece["id"]]})
        status, blocked = await self.request("DELETE", f'/styles/{style["id"]}')
        self.assertEqual(status, 409)
        self.assertIn("Greeting", blocked["detail"])
        status, blocked = await self.request("DELETE", f'/projects/{self.one["id"]}')
        self.assertEqual(status, 409)
        for kind, record in (("recipes", recipe), ("styles", style), ("pieces", piece), ("projects", self.one)):
            status, result = await self.request("DELETE", f'/{kind}/{record["id"]}')
            self.assertEqual(status, 200, result)
            self.assertTrue(result["deleted"])
            self.assertEqual(result["id"], record["id"])
            self.assertFalse((self.store.root / kind / f'{record["id"]}.json').exists())
            self.assertFalse((self.store.root / "revisions" / kind / record["id"]).exists())
            self.assertFalse(list((self.store.root / ".trash").rglob("*.json")))

    async def test_direct_image_delete_protects_active_master_and_saved_sample(self):
        image = self.store.save_generation({"project_id": self.one["id"], "category": "characters"}, png())
        routes._active_masters.add(image["id"])
        try:
            status, blocked = await self.request("DELETE", f'/generations/{image["id"]}')
            self.assertEqual(status, 409)
            self.assertIn("en curso", blocked["detail"])
        finally:
            routes._active_masters.discard(image["id"])
        self.store.review(image["id"], "accepted")
        style = self.store.save("styles", {"name": "Mistake", "sample_ids": [image["id"]]})
        status, _ = await self.request("DELETE", f'/generations/{image["id"]}')
        self.assertEqual(status, 409)
        self.assertEqual(self.store.get_generation(image["id"])["status"], "accepted")
        await self.request("DELETE", f'/styles/{style["id"]}')
        status, result = await self.request("DELETE", f'/generations/{image["id"]}')
        self.assertEqual(status, 200)
        self.assertTrue(result["deleted"])
        self.assertFalse(result["cleanup_pending"])
        self.assertFalse((self.store.root / "history" / f'{image["id"]}.json').exists())
        status, _ = await self.request("GET", f'/images/{image["id"]}')
        self.assertEqual(status, 404)
        status, images = await self.request("GET", "/generations", project_id=self.one["id"])
        self.assertEqual((status, images), (200, []))

    async def test_pending_and_rejected_style_samples_delete_and_clear_style_previews(self):
        for status in ("pending", "rejected"):
            with self.subTest(status=status):
                image = self.store.save_generation({"project_id": self.one["id"], "category": "characters"}, png())
                self.store.review(image["id"], status)
                style = self.store.save("styles", {"name": "Keep", "project_id": self.one["id"],
                    "prompt": "painted", "config": {"cfg": 4}, "sample_ids": [image["id"]]})
                status_code, result = await self.request("DELETE", f'/generations/{image["id"]}')
                self.assertEqual(status_code, 200, result)
                status_code, result = await self.request("GET", "/bootstrap")
                self.assertEqual(status_code, 200)
                current = next(item for item in result["styles"] if item["id"] == style["id"])
                self.assertEqual(current["sample_ids"], [])
                self.assertEqual((current["config"], current["revision"]), ({"cfg": 4}, 1))
                status_code, result = await self.request("POST", "/styles", {
                    "id": style["id"], "name": "Corrected name", "config": {"cfg": 4}})
                self.assertEqual(status_code, 200, result)
                self.assertEqual(result["sample_ids"], [])
                self.assertEqual(self.store.get("styles", style["id"], 1)["sample_ids"], [image["id"]])

    async def test_bootstrap_does_not_recreate_deleted_last_project(self):
        for project in (self.one, self.two):
            status, result = await self.request("DELETE", f'/projects/{project["id"]}')
            self.assertEqual(status, 200, result)
        status, result = await self.request("GET", "/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(result["projects"], [])

    async def test_style_cannot_save_a_sample_deleted_from_another_tab(self):
        image = self.store.save_generation({"project_id": self.one["id"], "category": "characters"}, png())
        status, _ = await self.request("DELETE", f'/generations/{image["id"]}')
        self.assertEqual(status, 200)
        status, result = await self.request("POST", "/styles", {
            "name": "Stale selection", "project_id": self.one["id"], "sample_ids": [image["id"]]})
        self.assertEqual(status, 400)
        self.assertTrue("borrada" in result["detail"] or "No se encontró" in result["detail"], result)
        self.assertEqual(self.store.list("styles"), [])

    async def test_profile_import_keeps_sample_provenance_without_requiring_its_image(self):
        image = self.store.save_generation({"project_id": self.one["id"], "category": "characters"}, png())
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"],
                                            "sample_ids": [image["id"]]})
        status, bundle = await self.request("GET", f'/projects/{self.one["id"]}/export')
        self.assertEqual(status, 200)
        self.store.delete("styles", style["id"])
        self.store.delete_generation(image["id"])
        status, imported = await self.request("POST", "/projects/import", bundle)
        self.assertEqual(status, 200, imported)
        imported_style = self.store.list("styles", imported["id"])[0]
        self.assertFalse(imported_style.get("sample_ids"))
        self.assertEqual(imported_style["source_sample_ids"], [image["id"]])

    async def test_project_cannot_be_deleted_while_generation_has_no_result_yet(self):
        gate = asyncio.Event()

        async def blocked(*args, **kwargs):
            await gate.wait()
            return await self.render(*args, **kwargs)

        with patch.object(routes.pipeline, "generate", blocked):
            status, job = await self.request("POST", "/generate", self.payload())
            self.assertEqual(status, 200)
            self.assertEqual(self.store.list_generations(), [])
            status, result = await self.request("DELETE", f'/projects/{self.one["id"]}')
            self.assertEqual(status, 409)
            self.assertIn("en curso", result["detail"])
            gate.set()
            await self.finish(job["job_id"])
        await asyncio.sleep(0)
        self.assertNotIn(self.one["id"], routes._active_projects)

    async def test_saved_recipe_keeps_its_format_when_project_format_changes(self):
        project = self.store.save("projects", {
            "name": "Format snapshots", "categories": ["characters"],
            "formats": [{"id": "portrait", "name": "Portrait", "width": 128,
                         "height": 256, "alpha": True}],
        })
        style = self.store.save("styles", {"name": "Paint", "project_id": project["id"],
                                           "prompt": "painted", "config": {"cfg": 4}})
        status, recipe = await self.request("POST", "/recipes", {
            "name": "Original portrait", "project_id": project["id"], "style_id": style["id"],
            "format_id": "portrait", "config": {"width": 128, "height": 256, "seed": "42"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(recipe["format_snapshot"]["width"], 128)
        self.assertEqual(recipe["format_snapshot"]["height"], 256)
        self.store.save("projects", {"id": project["id"], "formats": [
            {"id": "portrait", "name": "Portrait", "width": 256, "height": 128, "alpha": True},
        ]})
        status, restored = await self.request("POST", "/compose", recipe)
        self.assertEqual(status, 200)
        self.assertEqual((restored["config"]["width"], restored["config"]["height"]), (128, 256))
        status, fresh = await self.request("POST", "/compose", {
            "project_id": project["id"], "style_id": style["id"], "format_id": "portrait",
        })
        self.assertEqual(status, 200)
        self.assertEqual((fresh["config"]["width"], fresh["config"]["height"]), (256, 128))

    async def test_generate_keeps_large_seed_and_snapshot(self):
        with patch.object(routes.pipeline, "generate", self.render):
            status, job = await self.request("POST", "/generate", self.payload())
            self.assertEqual(status, 200)
            record = await self.finish(job["job_id"])
        self.assertEqual(record["seed"], "18446744073709551615")
        self.assertEqual(record["config"]["model"], "anima/base.safetensors")
        self.assertEqual(record["snapshot"]["project"]["id"], self.one["id"])
        status, body = await self.request("GET", f"/images/{record['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(body, png())

    async def test_scoped_backend_files_are_reclaimed_after_durable_save_then_delete_removes_record(self):
        created = []
        image_data = png("green")

        async def staged(client, params, on_progress=None, *, artifact_run_id, on_artifact, **kwargs):
            namespace = f"anima_studio/{artifact_run_id}"
            artifacts = []
            for area, name in (("input", "master.png"), ("input", "mask.png"),
                               ("output", "render_00001_.png")):
                descriptor = {"type": area, "subfolder": namespace, "filename": name,
                              "sha256": hashlib.sha256(image_data).hexdigest()}
                on_artifact({**descriptor, "state": "planned"})
                path = self.comfy_root / area / namespace / name
                path.write_bytes(image_data)
                created.append(path)
                on_artifact({**descriptor, "state": "created"})
                artifacts.append(descriptor)
            return {"image": image_data, "metadata": {"backend_run_id": artifact_run_id,
                "backend_artifacts": artifacts, "mode": params.mode, "seed": params.seed}}

        original_complete = self.storage.complete_run
        durable_checks = []

        def complete(run_id, descriptors):
            records = self.store.list_generations()
            self.assertEqual(len(records), 1)
            self.assertEqual(self.store.image_path(records[0]["id"]).read_bytes(), image_data)
            self.assertTrue(all(path.is_file() for path in created))
            durable_checks.append(run_id)
            return original_complete(run_id, descriptors)

        with patch.object(routes.pipeline, "generate", staged), patch.object(self.storage, "complete_run", complete):
            status, job = await self.request("POST", "/generate", self.payload())
            self.assertEqual(status, 200, job)
            record = await self.finish(job["job_id"])
            await asyncio.gather(*list(routes._tasks))
            await asyncio.sleep(0)
        self.assertEqual(durable_checks, [record["backend_run_id"]])
        self.assertTrue(all(not path.exists() for path in created))
        self.assertEqual(self.storage.artifacts.pending_runs(), [])
        image_path = self.store.image_path(record["id"])
        metadata_path = self.store.root / "history" / f'{record["id"]}.json'
        status, deleted = await self.request("DELETE", f'/generations/{record["id"]}')
        self.assertEqual(status, 200, deleted)
        self.assertFalse(deleted["cleanup_pending"])
        self.assertFalse(image_path.exists())
        self.assertFalse(metadata_path.exists())
        status, _ = await self.request("GET", f'/generations/{record["id"]}')
        self.assertIn(status, (400, 404))

    async def test_cleanup_error_retains_retry_provenance_then_finishes_all_files(self):
        image_data = png("orange")
        image = self.store.save_generation({"project_id": self.one["id"], "prompt": "keep cleanup proof"}, image_data)
        output = self.comfy_root / "output" / "anima_studio_00001_.png"
        output.parent.mkdir(parents=True)
        output.write_bytes(image_data)
        original_unlink = Path.unlink

        def locked(path, *args, **kwargs):
            if path == output:
                raise PermissionError("locked output fixture")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", locked):
            status, deleted = await self.request("DELETE", f'/generations/{image["id"]}')
        self.assertEqual(status, 200, deleted)
        self.assertTrue(deleted["cleanup_pending"])
        self.assertIn("locked output fixture", " ".join(deleted["cleanup_errors"]))
        self.assertTrue(output.exists())
        tombstone = self.store.get_generation(image["id"])
        self.assertTrue(tombstone["purged_at"])
        self.assertEqual(tombstone["prompt"], "keep cleanup proof")
        self.assertEqual(tombstone["image_sha256"], hashlib.sha256(image_data).hexdigest())
        status, cleaned = await self.request("POST", "/storage/cleanup")
        self.assertEqual(status, 200, cleaned)
        self.assertFalse(cleaned["cleanup_pending"])
        self.assertIn(image["id"], cleaned["deleted_images"])
        self.assertFalse(output.exists())
        self.assertFalse((self.store.root / "history" / f'{image["id"]}.json').exists())

    async def test_shared_legacy_output_survives_until_its_last_live_image_is_deleted(self):
        data = png("purple")
        first = self.store.save_generation({"project_id": self.one["id"]}, data)
        second = self.store.save_generation({"project_id": self.one["id"]}, data)
        output = self.comfy_root / "output" / "anima_studio_00002_.png"
        output.parent.mkdir(parents=True)
        output.write_bytes(data)
        status, result = await self.request("DELETE", f'/generations/{first["id"]}')
        self.assertEqual(status, 200, result)
        self.assertFalse(result["cleanup_pending"])
        self.assertTrue(output.exists())
        self.assertTrue(self.store.image_path(second["id"]).is_file())
        status, result = await self.request("DELETE", f'/generations/{second["id"]}')
        self.assertEqual(status, 200, result)
        self.assertFalse(result["cleanup_pending"])
        self.assertFalse(output.exists())

    async def test_bootstrap_finishes_proven_legacy_output_and_inactive_style_archive(self):
        image_data = png("yellow")
        image = self.store.save_generation({"project_id": self.one["id"]}, image_data)
        self.store.delete_generation(image["id"])
        output = self.comfy_root / "output" / "anima_studio_00003_.png"
        output.parent.mkdir(parents=True)
        output.write_bytes(image_data)
        style = self.store.save("styles", {"name": "Already deleted", "prompt": "painted"})
        archive = self.store.root / ".trash" / "styles" / f'{style["id"]}-{"e" * 32}'
        archive.mkdir(parents=True)
        (self.store.root / "styles" / f'{style["id"]}.json').replace(archive / f'{style["id"]}.json')
        (self.store.root / "revisions" / "styles" / style["id"]).replace(archive / style["id"])
        self.assertEqual(len(list(archive.rglob("*.json"))), 2)
        status, result = await self.request("GET", "/bootstrap")
        self.assertEqual(status, 200, result)
        self.assertFalse(result["storage_cleanup"]["cleanup_pending"])
        self.assertFalse(output.exists())
        self.assertFalse(archive.exists())
        self.assertFalse((self.store.root / "history" / f'{image["id"]}.json').exists())

    async def test_external_backend_work_defers_physical_cleanup_and_preserves_retry_record(self):
        image = self.store.save_generation({"project_id": self.one["id"]}, png())
        busy = {"queue_running": [[1, "outside-client"]], "queue_pending": []}
        with patch.object(routes._comfy, "_get", AsyncMock(return_value=busy)):
            status, result = await self.request("DELETE", f'/generations/{image["id"]}')
        self.assertEqual(status, 200, result)
        self.assertTrue(result["cleanup_pending"])
        self.assertTrue(self.store.get_generation(image["id"])["purged_at"])
        status, cleaned = await self.request("POST", "/storage/cleanup")
        self.assertEqual(status, 200, cleaned)
        self.assertFalse(cleaned["cleanup_pending"])
        self.assertFalse((self.store.root / "history" / f'{image["id"]}.json').exists())

    async def test_malformed_backend_queue_never_authorizes_file_cleanup(self):
        image = self.store.save_generation({"project_id": self.one["id"], "category": "characters"}, png())
        self.store.delete_generation(image["id"])
        index = self.store.root / "history" / f'{image["id"]}.json'
        for value in ({}, {"queue_running": [], "queue_pending": None}, []):
            with self.subTest(queue=value), patch.object(routes._comfy, "_get", AsyncMock(return_value=value)):
                status, result = await self.request("POST", "/storage/cleanup", {})
                self.assertEqual(status, 200)
                self.assertTrue(result["cleanup_pending"])
                self.assertTrue(result["deferred"])
                self.assertTrue(index.exists())

    async def test_background_reservation_survives_http_response(self):
        gate = asyncio.Event()
        existing = self.store.save_generation({"project_id": self.one["id"]}, png("red"))
        existing_path = self.store.image_path(existing["id"])

        async def blocked(*args, **kwargs):
            await gate.wait()
            return await self.render(*args, **kwargs)

        with patch.object(routes.pipeline, "generate", blocked):
            status, first = await self.request("POST", "/generate", self.payload())
            self.assertEqual(status, 200)
            status, busy = await self.request("POST", "/generate", self.payload())
            self.assertEqual(status, 409)
            self.assertIn("Anima Studio", busy["detail"])
            for method, endpoint, body in (
                ("DELETE", f'/generations/{existing["id"]}', None),
                ("POST", "/rejected/purge", {"ids": [existing["id"]], "min_age_days": 0}),
            ):
                status, blocked_delete = await self.request(method, endpoint, body)
                self.assertEqual(status, 409, blocked_delete)
                self.assertTrue(existing_path.is_file())
                self.assertFalse(self.store.get_generation(existing["id"]).get("purged_at"))
            status, _ = await self.request("GET", "/generations", project_id=self.one["id"])
            self.assertEqual(status, 200)
            gate.set()
            await self.finish(first["job_id"])
            await asyncio.gather(*list(routes._tasks))
            await asyncio.sleep(0)  # reservation release is a task done callback
        self.assertIsNone(comfy_jobs._active)

    async def test_other_module_blocks_generation_and_invalid_models_never_queue(self):
        gate = asyncio.Event()

        @comfy_jobs.exclusive("Painter")
        async def hold():
            await gate.wait()

        holder = asyncio.create_task(hold())
        await asyncio.sleep(0)
        try:
            status, value = await self.request("POST", "/generate", self.payload())
            self.assertEqual(status, 409)
            self.assertIn("Painter", value["detail"])
        finally:
            gate.set()
            await holder
        payload = self.payload()
        payload["config"]["model"] = "sdxl/other.safetensors"
        with patch.object(routes.pipeline, "generate", AsyncMock()) as render:
            status, value = await self.request("POST", "/generate", payload)
            self.assertEqual(status, 400)
            self.assertIn("catálogo Anima", value["detail"])
            render.assert_not_called()
        payload["config"]["model"] = "anima/base.safetensors"
        payload["config"]["loras"] = [{"file": "sdxl/foreign.safetensors", "strength": 1}]
        status, _ = await self.request("POST", "/generate", payload)
        self.assertEqual(status, 400)

    async def test_review_purge_and_project_history_filter(self):
        image = self.store.save_generation({"project_id": self.one["id"], "seed": "42"}, png())
        self.store.save_generation({"project_id": self.two["id"], "seed": "43"}, png())
        status, records = await self.request("GET", "/generations", project_id=self.one["id"])
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in records], [image["id"]])
        status, accepted = await self.request("POST", f"/generations/{image['id']}/review", {"status": "accepted"})
        self.assertEqual(status, 200)
        self.assertTrue(accepted["file"].startswith("accepted/"))
        status, _ = await self.request("POST", f"/generations/{image['id']}/review", {"status": "rejected"})
        self.assertEqual(status, 200)
        status, summary = await self.request("POST", "/rejected/purge", {"ids": [image["id"]], "min_age_days": 0})
        self.assertEqual(status, 200)
        self.assertEqual(summary["purged"], [image["id"]])
        status, _ = await self.request("GET", f"/images/{image['id']}")
        self.assertEqual(status, 404)

    async def test_project_bundle_import_remaps_style_and_piece_ids(self):
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"], "config": {"seed": "18446744073709551615"}})
        piece = self.store.save("pieces", {"name": "Hero", "kind": "character", "project_id": self.one["id"]})
        recipe = self.store.save("recipes", {"name": "Hero painted", "project_id": self.one["id"],
                                              "style_id": style["id"], "style_revision": 1, "piece_ids": [piece["id"]]})
        status, bundle = await self.request("GET", f"/projects/{self.one['id']}/export")
        self.assertEqual(status, 200)
        status, imported = await self.request("POST", "/projects/import", bundle)
        self.assertEqual(status, 200)
        self.assertNotEqual(imported["id"], self.one["id"])
        self.assertEqual(imported["formats"], self.one["formats"])
        imported_recipes = self.store.list("recipes", imported["id"])
        self.assertEqual(len(imported_recipes), 1)
        self.assertNotEqual(imported_recipes[0]["id"], recipe["id"])
        self.assertNotEqual(imported_recipes[0]["style_id"], style["id"])
        self.assertNotEqual(imported_recipes[0]["piece_ids"], [piece["id"]])
        self.assertEqual(self.store.get("styles", imported_recipes[0]["style_id"])["config"]["seed"], "18446744073709551615")

    async def test_expression_master_cannot_come_from_another_project(self):
        master = self.store.save_generation({"project_id": self.two["id"], "seed": "42"}, png())
        encoded_mask = base64.b64encode(png("white")).decode()
        status, _ = await self.request("POST", "/generate", self.payload(mode="expression", master_asset_id=master["id"],
                                                                          mask_base64=encoded_mask))
        self.assertEqual(status, 400)

    async def test_import_keeps_pinned_style_and_piece_revisions(self):
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"],
                                           "prompt": "original painted style", "config": {"cfg": 4, "seed": "99"}})
        piece = self.store.save("pieces", {"name": "Hero", "kind": "character", "project_id": self.one["id"],
                                           "prompt": "original hero"})
        self.store.save("recipes", {"name": "Pinned recipe", "project_id": self.one["id"],
                                    "style_id": style["id"], "style_revision": 1,
                                    "piece_ids": [piece["id"]], "piece_revisions": {piece["id"]: 1}})
        self.store.save("styles", {"id": style["id"], "prompt": "new painted style", "config": {"cfg": 8}})
        self.store.save("pieces", {"id": piece["id"], "prompt": "new hero"})
        status, bundle = await self.request("GET", f"/projects/{self.one['id']}/export")
        self.assertEqual(status, 200)
        self.assertEqual(len(bundle["revisions"]["styles"][style["id"]]), 2)
        status, project = await self.request("POST", "/projects/import", bundle)
        self.assertEqual(status, 200)
        imported = self.store.list("recipes", project["id"])[0]
        old_style = self.store.get("styles", imported["style_id"], imported["style_revision"])
        self.assertEqual(old_style["prompt"], "original painted style")
        self.assertEqual(old_style["config"]["cfg"], 4)
        new_piece_id = imported["piece_ids"][0]
        old_piece = self.store.get("pieces", new_piece_id, imported["piece_revisions"][new_piece_id])
        self.assertEqual(old_piece["prompt"], "original hero")
        self.assertEqual(old_piece["project_id"], project["id"])
        self.assertEqual(self.store.get("pieces", new_piece_id)["prompt"], "new hero")
        status, assembled = await self.request("POST", "/compose", imported)
        self.assertEqual(status, 200)
        self.assertEqual(assembled["prompt"], "original painted style\noriginal hero")
        self.assertEqual(assembled["config"]["cfg"], 4)

    async def test_invalid_bundle_does_not_publish_a_partial_project(self):
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"]})
        self.store.save("recipes", {"name": "Recipe", "project_id": self.one["id"], "style_id": style["id"],
                                    "style_revision": 1})
        status, bundle = await self.request("GET", f"/projects/{self.one['id']}/export")
        self.assertEqual(status, 200)
        # Leave valid entities first so a non-transactional import would already
        # have created a project and style before finding this missing reference.
        bundle["recipes"][0]["style_id"] = "missing-style"
        bundle["revisions"]["recipes"][bundle["recipes"][0]["id"]][0]["style_id"] = "missing-style"
        before = {kind: len(self.store.list(kind)) for kind in ("projects", "styles", "pieces", "recipes")}
        status, error = await self.request("POST", "/projects/import", bundle)
        self.assertEqual(status, 400)
        self.assertIn("No se importó", error["detail"])
        self.assertEqual(before, {kind: len(self.store.list(kind)) for kind in before})

    async def test_v1_bundle_missing_a_pinned_revision_is_rejected(self):
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"], "prompt": "first"})
        current = self.store.save("styles", {"id": style["id"], "prompt": "second"})
        recipe = self.store.save("recipes", {"name": "Old recipe", "project_id": self.one["id"],
                                              "style_id": style["id"], "style_revision": 1})
        before = len(self.store.list("projects"))
        status, _ = await self.request("POST", "/projects/import", {"schema": "anima-studio-profile-v1",
                                                                    "project": self.one, "styles": [current],
                                                                    "recipes": [recipe]})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.store.list("projects")), before)

    async def test_active_expression_master_is_protected_then_child_keeps_it(self):
        master = self.store.save_generation({"project_id": self.one["id"], "seed": "42"}, png())
        gate, started = asyncio.Event(), asyncio.Event()

        async def blocked(*args, **kwargs):
            started.set()
            await gate.wait()
            return await self.render(*args, **kwargs)

        encoded_mask = base64.b64encode(png("white")).decode()
        with patch.object(routes.pipeline, "generate", blocked):
            status, job = await self.request("POST", "/generate", self.payload(
                mode="expression", master_asset_id=master["id"], mask_base64=encoded_mask))
            self.assertEqual(status, 200)
            await asyncio.wait_for(started.wait(), 5)
            status, _ = await self.request("POST", f"/generations/{master['id']}/review", {"status": "rejected"})
            self.assertEqual(status, 200)
            status, active = await self.request("POST", "/rejected/purge", {"ids": [master["id"]], "min_age_days": 0})
            self.assertEqual(status, 409)
            self.assertIn("Anima Studio", active["detail"])
            self.assertFalse(self.store.get_generation(master["id"]).get("purged_at"))
            gate.set()
            child = await self.finish(job["job_id"])
            await asyncio.gather(*list(routes._tasks))
            await asyncio.sleep(0)
        self.assertEqual(child["master_asset_id"], master["id"])
        status, saved_dependency = await self.request("POST", "/rejected/purge", {"ids": [master["id"]], "min_age_days": 0})
        self.assertEqual(status, 200)
        self.assertEqual(saved_dependency["protected"], [master["id"]])

    async def test_failed_expression_releases_its_master_for_cleanup(self):
        master = self.store.save_generation({"project_id": self.one["id"], "seed": "42"}, png())
        gate, started = asyncio.Event(), asyncio.Event()

        async def failure(*args, **kwargs):
            started.set()
            await gate.wait()
            raise RuntimeError("Simulated generation failure")

        encoded_mask = base64.b64encode(png("white")).decode()
        with patch.object(routes.pipeline, "generate", failure):
            status, job = await self.request("POST", "/generate", self.payload(
                mode="expression", master_asset_id=master["id"], mask_base64=encoded_mask))
            self.assertEqual(status, 200)
            await asyncio.wait_for(started.wait(), 5)
            await self.request("POST", f"/generations/{master['id']}/review", {"status": "rejected"})
            status, active = await self.request("POST", "/rejected/purge", {"ids": [master["id"]], "min_age_days": 0})
            self.assertEqual(status, 409)
            self.assertIn("Anima Studio", active["detail"])
            self.assertFalse(self.store.get_generation(master["id"]).get("purged_at"))
            gate.set()
            await asyncio.gather(*list(routes._tasks))
            await asyncio.sleep(0)
        status, failed = await self.request("GET", f"/jobs/{job['job_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(failed["status"], "error")
        status, cleaned = await self.request("POST", "/rejected/purge", {"ids": [master["id"]], "min_age_days": 0})
        self.assertEqual(status, 200)
        self.assertEqual(cleaned["purged"], [master["id"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
