"""Real Hub HTTP lifecycle against isolated CPU fixtures on ephemeral ports."""
import asyncio
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
import uuid

import check_backend_stop_windows as fixture
import uvicorn


@unittest.skipUnless(os.name == "nt", "Windows process-tree integration")
class WindowsHTTPChecks(unittest.IsolatedAsyncioTestCase):
    wait_until = fixture.WindowsStopChecks.wait_until
    ready = fixture.WindowsStopChecks.ready

    async def asyncSetUp(self):
        await fixture.WindowsStopChecks.asyncSetUp(self)
        self.serve_task = None
        self.server = None
        self.socket = None
        self.patches = ExitStack()
        self.proof = {}
        models = self.folder / "empty-models"
        models.mkdir()
        config_path = fixture.ROOT / "hub" / "hub_config.json"
        read_text = Path.read_text

        def fixture_config(path, *args, **kwargs):
            if path == config_path:
                return json.dumps({"paths": {"models": str(models)}, "cuda": {}})
            return read_text(path, *args, **kwargs)

        self.patches.enter_context(patch.object(Path, "read_text", fixture_config))
        self.app = importlib.import_module("app")
        self.patches.enter_context(patch.object(self.app, "bridge", self.bridge))
        self.manager = self.app._anima_sessions
        self.patches.enter_context(patch.object(self.manager, "bridge_factory", lambda: self.bridge))
        self.patches.enter_context(patch.object(self.manager, "grace", 0.1))
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.api_port = self.socket.getsockname()[1]
        config = uvicorn.Config(self.app.api, host="127.0.0.1", port=self.api_port,
                                lifespan="on", loop="asyncio", log_level="error")
        self.server = uvicorn.Server(config)
        self.serve_task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        await self.wait_until(lambda: self.server.started)
        self.proof.update(api_port=self.api_port, backend_port=self.port)

    async def asyncTearDown(self):
        try:
            if self.server is not None:
                self.server.should_exit = True
            if self.serve_task is not None:
                await asyncio.wait_for(self.serve_task, 15)
        finally:
            if self.socket is not None:
                self.socket.close()
            try:
                await fixture.WindowsStopChecks.asyncTearDown(self)
            finally:
                self.patches.close()
                result = fixture.ROOT / ".cache" / "backend-http-result.json"
                result.write_text(json.dumps({"proof": self.proof, "logs": self.logs},
                                             indent=2, ensure_ascii=False), encoding="utf-8")
                print(json.dumps(self.proof, ensure_ascii=False))

    async def request(self, path, payload=None):
        def send():
            body = None if payload is None else json.dumps(payload).encode()
            req = Request(f"http://127.0.0.1:{self.api_port}{path}", data=body,
                          headers={"Content-Type": "application/json"},
                          method="GET" if payload is None else "POST")
            with urlopen(req, timeout=20) as response:
                self.assertEqual(response.status, 200)
                return json.load(response)
        return await asyncio.to_thread(send)

    async def assert_api_alive(self):
        data = await self.request("/api/apps")
        self.assertEqual(len(data["apps"]), 1)
        self.assertFalse(self.serve_task.done())
        return data["apps"][0]

    async def wait_stopped(self, stopped_before):
        await self.wait_until(lambda: self.proc.poll() is not None
                              and not self.bridge.is_backend_up("comfyui"))
        await self.wait_until(lambda: self.logs.count("=== App detenida ===") > stopped_before)
        await self.wait_until(lambda: "comfyui" not in self.bridge._state.busy_apps)
        self.assertNotIn("comfyui", self.bridge._state.running_apps)
        if self.proc.stdout:
            self.proc.stdout.close()

    async def open_session(self):
        self.marker.unlink(missing_ok=True)
        session_id = str(uuid.uuid4())
        data = await self.request("/api/anima-studio/session/open", {"session_id": session_id})
        self.assertIn(data["status"], ("starting", "ready"))
        await self.ready()
        status = await self.request("/api/anima-studio/session/heartbeat", {"session_id": session_id})
        self.assertEqual(status["status"], "ready")
        return session_id

    async def test_dashboard_and_anima_lifecycle_keep_hub_http_alive(self):
        # A CPU fixture reproduces a backend whose Hub Popen reference was lost.
        self.assertTrue(self.bridge.launch("comfyui", open_browser=False))
        await self.ready()
        self.bridge._state.running_apps.pop("comfyui")
        card = await self.assert_api_alive()
        self.assertEqual(card["status"], "running")
        self.assertIsNone(card["pid"])
        self.assertTrue(card["any_running"])
        stopped_before = self.logs.count("=== App detenida ===")
        self.assertTrue((await self.request("/api/apps/comfyui/stop", {}))["ok"])
        await self.wait_stopped(stopped_before)
        self.assertEqual((await self.assert_api_alive())["status"], "installed")
        self.proof["lost_reference_stop_hub_alive"] = True

        # Closing the last Anima session must stop only its automatic backend.
        session_id = await self.open_session()
        stopped_before = self.logs.count("=== App detenida ===")
        closed = await self.request("/api/anima-studio/session/close", {"session_id": session_id})
        self.assertEqual(closed["status"], "closed")
        await self.wait_stopped(stopped_before)
        await self.wait_until(lambda: fixture.comfy_jobs._active is None)
        self.assertEqual((await self.assert_api_alive())["status"], "installed")
        self.proof["anima_close_stops_backend_hub_alive"] = True

        # The dashboard button must also work while an Anima session is open.
        session_id = await self.open_session()
        stopped_before = self.logs.count("=== App detenida ===")
        self.assertTrue((await self.request("/api/apps/comfyui/stop", {}))["ok"])
        await self.wait_stopped(stopped_before)
        self.assertEqual((await self.assert_api_alive())["status"], "installed")
        await self.request("/api/anima-studio/session/close", {"session_id": session_id})
        self.proof["dashboard_stop_with_anima_open_hub_alive"] = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
