"""Windows CPU integration: real hidden venv process, HTTP queue and termination.

Run with the Hub venv Python. Fixtures use an isolated uv venv and a free local
port; no installed backend, model, GPU job or pre-existing process is touched.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "hub-webui"))
with patch("modules.model_organizer.sync_all_app_configs"), \
        patch("modules.model_organizer.sync_krita_priority"), \
        patch("modules.storage_manager.create_model_dirs"):
    import hub_bridge
from backend_sessions import BackendSessions
import comfy_jobs


SERVER = '''import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        value = {"queue_running": [], "queue_pending": []} if self.path == "/queue" else {"ok": True}
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
Path(sys.argv[2]).write_text(json.dumps({"pid": os.getpid(), "parent_pid": os.getppid()}))
server.serve_forever()
'''


class FixtureState:
    def __init__(self, folder, port, marker):
        self._lock = threading.Lock()
        self.running_apps, self.busy_apps = {}, {}
        self.registry_apps = {"comfyui": {
            "id": "comfyui", "name": "CPU stop fixture", "default_port": port,
            "launch_type": "python", "launch_script": "server.py",
            "launch_args": [str(port), str(marker)], "auto_open_browser": False,
        }}
        self.registry_utilities = {}
        self.installed_apps = {"comfyui": {"dir": str(folder)}}
        self.config_file = folder / "hub.json"
        self.config_file.write_text(json.dumps({"paths": {}, "cuda": {}}))
        self.events = []

    def get_port_override(self, app_id):
        return None

    def get_user_extra_args(self, app_id):
        return []

    def set_busy(self, app_id, status):
        with self._lock:
            self.busy_apps[app_id] = status

    def clear_busy(self, app_id):
        with self._lock:
            self.busy_apps.pop(app_id, None)

    def set_running(self, app_id, proc):
        with self._lock:
            self.running_apps[app_id] = proc

    def clear_running(self, app_id):
        with self._lock:
            self.running_apps.pop(app_id, None)

    def cleanup_stale(self):
        with self._lock:
            dead = [app_id for app_id, proc in self.running_apps.items() if proc.poll() is not None]
            for app_id in dead:
                self.running_apps.pop(app_id, None)
            return dead

    def get_app_status(self, app_id):
        with self._lock:
            if app_id in self.busy_apps:
                return self.busy_apps[app_id]
            return "running" if app_id in self.running_apps else "installed"

    def log_event(self, *args):
        self.events.append(args)


class HealthClient:
    base_url = ""
    ws_url = ""

    async def health_check(self):
        def request():
            try:
                with urlopen(self.base_url + "/system_stats", timeout=0.5) as response:
                    return response.status == 200
            except OSError:
                return False
        return await asyncio.to_thread(request)


@unittest.skipUnless(os.name == "nt", "The supported process lifecycle is Windows native")
class WindowsStopChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parent = ROOT / ".cache" / "windows-stop-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.folder = Path(self.temp.name)
        self.proc = None
        self.manager = None
        self.logs = []
        uv = os.environ.get("AI_HUB_UV_PATH") or shutil.which("uv")
        self.assertTrue(uv, "uv is required to create the isolated fixture venv")
        env = dict(os.environ, UV_PYTHON_PREFERENCE="only-managed",
                   UV_CACHE_DIR=str(self.folder / "uv-cache"))
        subprocess.run([uv, "venv", "--python", sys.executable, str(self.folder / "venv")],
                       check=True, capture_output=True, text=True, timeout=60, env=env)
        (self.folder / "server.py").write_text(SERVER, encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.marker = self.folder / "ready.json"
        with patch.object(hub_bridge.HubBridge, "_run_startup_sync"):
            self.bridge = hub_bridge.HubBridge()
        self.bridge._state = FixtureState(self.folder, self.port, self.marker)
        self.bridge.on_log(lambda app_id, line: self.logs.append(line))
        self.assertIsNone(comfy_jobs._active)

    async def asyncTearDown(self):
        if self.manager is not None:
            await self.manager.shutdown()
        if hasattr(self, "bridge"):
            await asyncio.to_thread(self.bridge.stop_all_running, 0.2)
        if self.proc is not None:
            if self.proc.poll() is None:
                await asyncio.to_thread(hub_bridge._kill_tree, self.proc)
            await asyncio.to_thread(self.proc.wait, 5)
            if self.proc.stdout:
                self.proc.stdout.close()
        self.assertFalse(hub_bridge._is_port_in_use(self.port))
        self.temp.cleanup()
        self.assertIsNone(comfy_jobs._active)

    async def wait_until(self, check, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            await asyncio.sleep(0.05)
        self.fail("Fixture timed out: " + " | ".join(self.logs[-8:]))

    async def ready(self):
        await self.wait_until(lambda: self.marker.is_file() and self.bridge.is_backend_up("comfyui"))
        self.proc = self.bridge._state.running_apps.get("comfyui")
        self.assertIsInstance(self.proc, subprocess.Popen)
        details = json.loads(self.marker.read_text())
        # uv's Windows venv trampoline is a separate process from Python itself.
        self.assertNotEqual(details["pid"], self.proc.pid)
        self.assertEqual(details["parent_pid"], self.proc.pid)
        self.assertIsNone(self.proc.poll())

    async def test_last_session_release_stops_real_headless_process_and_port(self):
        self.manager = BackendSessions(HealthClient(), lambda: self.bridge, grace=0.1)
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        await self.manager.open(first)
        await self.ready()
        await self.manager.open(second)
        await self.manager.close(first)
        await asyncio.sleep(0.25)
        self.assertTrue(self.bridge.is_backend_up("comfyui"))
        await self.manager.close(second)
        await self.wait_until(lambda: self.proc.poll() is not None and not self.bridge.is_backend_up("comfyui"))
        await self.wait_until(lambda: "comfyui" not in self.bridge._state.busy_apps)
        await self.wait_until(lambda: comfy_jobs._active is None)
        self.assertNotIn("comfyui", self.bridge._state.running_apps)

    async def test_dashboard_stop_terminates_real_process_tree(self):
        self.assertTrue(self.bridge.launch("comfyui", open_browser=False))
        await self.ready()
        self.assertTrue(self.bridge.stop("comfyui"))
        self.assertTrue(self.bridge.stop("comfyui"))
        await self.wait_until(lambda: self.proc.poll() is not None and not self.bridge.is_backend_up("comfyui"))
        await self.wait_until(lambda: "comfyui" not in self.bridge._state.busy_apps)
        self.assertNotIn("comfyui", self.bridge._state.running_apps)

    async def test_explicit_stop_recovers_listener_after_process_reference_is_lost(self):
        self.assertTrue(self.bridge.launch("comfyui", open_browser=False))
        await self.ready()
        # Simulate a Hub restart while retaining the fixture Popen only for cleanup.
        self.bridge._state.running_apps.pop("comfyui")
        self.assertTrue(await asyncio.to_thread(self.bridge.stop, "comfyui"))
        await self.wait_until(lambda: not self.bridge.is_backend_up("comfyui"))
        await self.wait_until(lambda: self.proc.poll() is not None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
