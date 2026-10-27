"""CPU-only backend page-session lifecycle regressions (no servers or GPU)."""
import asyncio
import io
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend_sessions as module
import comfy_jobs
from backend_sessions import BackendSessions
from fastapi import HTTPException, WebSocketDisconnect


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Bridge:
    def __init__(self, port=8188):
        self.port = port
        self.launch_ok = True
        self.launches = []
        self.releases = []
        self.guards = {}
        self._state = SimpleNamespace(running_apps={})

    def _effective_port(self, app_id):
        return self.port

    def set_backend_idle_guard(self, app_id, guard):
        self.guards[app_id] = guard

    def launch_backend(self, app_id, owner):
        self.launches.append((app_id, owner))
        return self.launch_ok

    def release_backend(self, app_id, owner, grace=8):
        self.releases.append((app_id, owner, grace))
        return True


class FakeWebSocket:
    def __init__(self):
        self.accepted = asyncio.Event()
        self.sent = asyncio.Event()
        self.receiving = asyncio.Event()
        self.messages = []
        self.incoming = asyncio.Queue()
        self.disconnected = False
        self.close_codes = []

    async def accept(self):
        self.accepted.set()

    async def send_json(self, value):
        if self.disconnected:
            raise WebSocketDisconnect(code=1001)
        self.messages.append(value)
        self.sent.set()

    async def receive_text(self):
        self.receiving.set()
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value

    def disconnect(self):
        self.disconnected = True
        self.incoming.put_nowait(WebSocketDisconnect(code=1001))

    async def close(self, code=1000):
        self.close_codes.append(code)
        self.disconnected = True


class SessionChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.bridge = Bridge()
        self.client = SimpleNamespace(health_check=AsyncMock(return_value=True))
        self.manager = BackendSessions(self.client, lambda: self.bridge, clock=self.clock)
        self.id = str(uuid.uuid4())
        self.tasks = []
        self.old_active, comfy_jobs._active = comfy_jobs._active, None

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.manager.shutdown()
        comfy_jobs._active = self.old_active

    async def test_open_idempotent_configures_guard_and_renews_seen(self):
        first = await self.manager.open(self.id)
        self.clock.advance(11)
        second = await self.manager.open(self.id)
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(second["status"], "ready")
        self.assertEqual(len(self.bridge.launches), 1)
        self.assertEqual(self.manager.sessions[self.id].seen, self.clock())
        self.assertEqual(self.bridge.guards["comfyui"], self.manager.backend_idle)

    async def test_two_pages_have_distinct_owners_and_release_independently(self):
        other = str(uuid.uuid4())
        await self.manager.open(self.id)
        await self.manager.open(other)
        owners = [owner for _, owner in self.bridge.launches]
        self.assertEqual(len(set(owners)), 2)
        await self.manager.close(self.id)
        self.assertIn(other, self.manager.sessions)
        self.assertEqual([entry[1] for entry in self.bridge.releases], owners[:1])
        await self.manager.close(other)
        self.assertEqual([entry[1] for entry in self.bridge.releases], owners)

    async def test_close_overtakes_open_without_launch_or_revival(self):
        await self.manager.close(self.id)
        result = await self.manager.open(self.id)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(self.bridge.launches, [])
        self.assertEqual(self.manager.sessions, {})

    async def test_heartbeat_ttl_is_holgado_and_closed_session_cannot_renew(self):
        await self.manager.open(self.id)
        self.assertEqual(self.manager.ttl, 180)
        self.clock.advance(170)
        await self.manager.heartbeat(self.id)
        self.clock.advance(170)
        await self.manager.sweep()
        self.assertIn(self.id, self.manager.sessions)
        self.assertEqual(self.bridge.releases, [])
        self.clock.advance(11)
        await self.manager.sweep()
        self.assertNotIn(self.id, self.manager.sessions)
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertEqual((await self.manager.heartbeat(self.id))["status"], "closed")

    async def test_request_and_worker_keep_lease_after_page_close(self):
        await self.manager.open(self.id)
        entered, preflight, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def worker():
            await finish.wait()

        @self.manager.job
        async def launch(payload):
            entered.set()
            await preflight.wait()
            task = asyncio.create_task(worker())
            self.tasks.append(task)
            self.manager.track_worker(task)
            return {"job_id": "fixture"}

        request = asyncio.create_task(launch({"session_id": self.id}))
        self.tasks.append(request)
        await entered.wait()
        await self.manager.close(self.id)
        self.assertEqual(self.bridge.releases, [])
        preflight.set()
        self.assertEqual(await request, {"job_id": "fixture"})
        self.assertEqual(self.manager.sessions[self.id].jobs, 1)
        self.assertEqual(self.bridge.releases, [])
        finish.set()
        await asyncio.gather(*self.tasks)
        await asyncio.sleep(0)
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertNotIn(self.id, self.manager.sessions)

    async def test_worker_cancelled_before_first_step_releases_pin(self):
        await self.manager.open(self.id)
        ran = False

        async def worker():
            nonlocal ran
            ran = True

        @self.manager.job
        async def launch(payload):
            task = asyncio.create_task(worker())
            self.tasks.append(task)
            self.manager.track_worker(task)
            task.cancel()

        await launch({"session_id": self.id})
        await self.manager.close(self.id)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertFalse(ran)
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertNotIn(self.id, self.manager.sessions)

    async def test_failed_worker_releases_closed_page(self):
        await self.manager.open(self.id)
        finish = asyncio.Event()

        async def worker():
            await finish.wait()
            raise ValueError("Fixture failure")

        @self.manager.job
        async def launch(payload):
            task = asyncio.create_task(worker())
            self.tasks.append(task)
            self.manager.track_worker(task)

        await launch({"session_id": self.id})
        await self.manager.close(self.id)
        self.assertEqual(self.bridge.releases, [])
        finish.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertEqual(len(self.bridge.releases), 1)

    async def test_request_failure_or_cancellation_releases_preflight_pin(self):
        await self.manager.open(self.id)
        entered, gate = asyncio.Event(), asyncio.Event()

        @self.manager.job
        async def launch(payload):
            entered.set()
            await gate.wait()

        request = asyncio.create_task(launch({"session_id": self.id}))
        self.tasks.append(request)
        await entered.wait()
        await self.manager.close(self.id)
        self.assertEqual(self.bridge.releases, [])
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        self.assertEqual(len(self.bridge.releases), 1)

    async def test_ready_requires_http_health_and_uses_effective_port(self):
        self.bridge.port = 8299
        self.client.health_check.return_value = False
        self.assertEqual((await self.manager.open(self.id))["status"], "starting")
        self.assertEqual(self.client.base_url, "http://127.0.0.1:8299")
        self.assertEqual(self.client.ws_url, "ws://127.0.0.1:8299/ws")
        self.client.health_check.return_value = True
        self.assertEqual((await self.manager.heartbeat(self.id))["status"], "ready")

    async def test_startup_failure_and_timeout_are_visible_and_closeable(self):
        self.bridge.launch_ok = False
        result = await self.manager.open(self.id)
        self.assertEqual(result["status"], "error")
        self.assertIn("iniciar", result["error"])
        await self.manager.close(self.id)
        self.assertEqual(len(self.bridge.releases), 1)

        other = str(uuid.uuid4())
        self.bridge.launch_ok = True
        self.client.health_check.return_value = False
        self.assertEqual((await self.manager.open(other))["status"], "starting")
        self.clock.advance(self.manager.startup_timeout + 1)
        result = await self.manager.heartbeat(other)
        self.assertEqual(result["status"], "error")
        self.assertIn("tiempo", result["error"])

    async def test_closed_or_invalid_session_cannot_start_job(self):
        @self.manager.job
        async def launch(payload):
            self.fail("A closed/invalid session must not reach the worker")

        for session_id, status in ((self.id, 409), ("not-a-uuid", 400)):
            with self.assertRaises(HTTPException) as raised:
                await launch({"session_id": session_id})
            self.assertEqual(raised.exception.status_code, status)

    async def test_backend_idle_requires_empty_queue_and_no_hub_job(self):
        await self.manager.open(self.id)
        def queue(value):
            return patch.object(module, "urlopen", return_value=io.BytesIO(json.dumps(value).encode()))
        with queue({"queue_running": [], "queue_pending": []}):
            comfy_jobs._active = SimpleNamespace(owner="Painter")
            self.assertFalse(self.manager.backend_idle())
            comfy_jobs._active = None
        for value in ({"queue_running": [[1]], "queue_pending": []},
                      {"queue_running": [], "queue_pending": [[2]]}, {}, []):
            with queue(value):
                self.assertFalse(self.manager.backend_idle())
        with queue({"queue_running": [], "queue_pending": []}):
            self.assertTrue(self.manager.backend_idle())
            self.assertIsNotNone(comfy_jobs._active)

    async def test_job_entering_during_queue_check_prevents_shutdown_reservation(self):
        await self.manager.open(self.id)
        release = None

        def queue_with_racing_admission(*args, **kwargs):
            nonlocal release
            release = comfy_jobs.reserve_backend_shutdown("Fixture competing job")
            self.assertIsNotNone(release)
            return io.BytesIO(b'{"queue_running": [], "queue_pending": []}')

        try:
            with patch.object(module, "urlopen", side_effect=queue_with_racing_admission):
                self.assertFalse(self.manager.backend_idle())
                self.assertIsNone(self.manager._closing_release)
        finally:
            if release:
                release()

    async def test_queue_error_preserves_live_process_and_allows_exited_cleanup(self):
        await self.manager.open(self.id)
        process = SimpleNamespace(poll=lambda: None)
        self.bridge._state.running_apps["comfyui"] = process
        with patch.object(module, "urlopen", side_effect=OSError("Disconnected")):
            self.assertFalse(self.manager.backend_idle())
            process.poll = lambda: 1
            self.assertTrue(self.manager.backend_idle())

    async def test_shutdown_releases_idle_sessions_and_stops_sweeper(self):
        await self.manager.open(self.id)
        await self.manager.shutdown()
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertEqual(self.manager.sessions, {})
        self.assertIsNone(self.manager._sweeper)

    async def test_socket_disconnect_closes_page_without_waiting_for_ttl(self):
        socket = FakeWebSocket()
        task = asyncio.create_task(self.manager.socket(socket, self.id))
        self.tasks.append(task)
        await asyncio.wait_for(socket.receiving.wait(), 2)
        self.assertTrue(socket.accepted.is_set())
        self.assertEqual(socket.messages[0]["status"], "ready")
        self.assertEqual((await self.manager.open(self.id))["status"], "ready")
        self.assertEqual(len(self.bridge.launches), 1)
        socket.disconnect()
        await asyncio.wait_for(task, 2)
        self.assertNotIn(self.id, self.manager.sessions)
        self.assertIn(self.id, self.manager.closed)
        self.assertEqual(len(self.bridge.releases), 1)

    async def test_socket_invalid_id_closes_without_launching(self):
        socket = FakeWebSocket()
        await self.manager.socket(socket, "invalid-session")
        self.assertEqual(socket.close_codes, [1008])
        self.assertFalse(socket.accepted.is_set())
        self.assertEqual(self.bridge.launches, [])

    async def test_socket_for_closed_page_does_not_restart_backend(self):
        await self.manager.close(self.id)
        socket = FakeWebSocket()
        await self.manager.socket(socket, self.id)
        self.assertEqual(socket.messages[0]["status"], "closed")
        self.assertEqual(socket.close_codes, [1000])
        self.assertEqual(self.bridge.launches, [])

    async def test_socket_disconnect_while_backend_is_starting_releases_page(self):
        self.client.health_check.return_value = False
        socket = FakeWebSocket()
        task = asyncio.create_task(self.manager.socket(socket, self.id))
        self.tasks.append(task)
        await asyncio.wait_for(socket.receiving.wait(), 2)
        self.assertEqual(socket.messages[0]["status"], "starting")
        socket.disconnect()
        await asyncio.wait_for(task, 2)
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertNotIn(self.id, self.manager.sessions)

    async def test_socket_close_during_initial_health_does_not_leave_owner(self):
        checking, ready = asyncio.Event(), asyncio.Event()

        async def health():
            checking.set()
            await ready.wait()
            return False

        self.client.health_check = health
        socket = FakeWebSocket()
        task = asyncio.create_task(self.manager.socket(socket, self.id))
        self.tasks.append(task)
        await asyncio.wait_for(checking.wait(), 2)
        socket.disconnect()
        ready.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertNotIn(self.id, self.manager.sessions)
        self.assertEqual(socket.messages, [])

    async def test_socket_disconnect_keeps_generation_pin_until_worker_finishes(self):
        socket = FakeWebSocket()
        connection = asyncio.create_task(self.manager.socket(socket, self.id))
        self.tasks.append(connection)
        await asyncio.wait_for(socket.receiving.wait(), 2)
        finish = asyncio.Event()

        @self.manager.job
        async def launch(payload):
            worker = asyncio.create_task(finish.wait())
            self.tasks.append(worker)
            self.manager.track_worker(worker)

        await launch({"session_id": self.id})
        socket.disconnect()
        await asyncio.wait_for(connection, 2)
        self.assertTrue(self.manager.sessions[self.id].closing)
        self.assertEqual(self.manager.sessions[self.id].jobs, 1)
        self.assertEqual(self.bridge.releases, [])
        finish.set()
        await asyncio.gather(*self.tasks)
        await asyncio.sleep(0)
        self.assertEqual(len(self.bridge.releases), 1)
        self.assertNotIn(self.id, self.manager.sessions)

    async def test_socket_timeout_sends_liveness_and_keeps_lease(self):
        socket = FakeWebSocket()
        original_wait_for = asyncio.wait_for
        timed_out = False

        async def wait_for(awaitable, timeout):
            nonlocal timed_out
            if timeout == 15 and not timed_out:
                timed_out = True
                self.clock.advance(170)
                awaitable.close()
                raise TimeoutError()
            return await original_wait_for(awaitable, timeout)

        with patch.object(module.asyncio, "wait_for", wait_for):
            task = asyncio.create_task(self.manager.socket(socket, self.id))
            self.tasks.append(task)
            await original_wait_for(socket.receiving.wait(), 2)
            self.assertTrue(timed_out)
            self.assertGreaterEqual(len(socket.messages), 2)
            self.assertIn(self.id, self.manager.sessions)
            self.assertEqual(self.manager.sessions[self.id].seen, self.clock())
            self.assertEqual(self.bridge.releases, [])
            socket.disconnect()
            await original_wait_for(task, 2)
        self.assertEqual(len(self.bridge.releases), 1)


class ShutdownAdmissionChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNone(comfy_jobs._active)
        self.tasks = []
        self.releases = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for release in self.releases:
            release()
        await asyncio.sleep(0)
        self.assertIsNone(comfy_jobs._active)

    async def test_shutdown_cannot_reserve_while_request_or_worker_is_active(self):
        entered, gate = asyncio.Event(), asyncio.Event()

        @comfy_jobs.exclusive("Painter")
        async def launch():
            entered.set()
            await gate.wait()
            worker = comfy_jobs.spawn(asyncio.Event().wait())
            self.tasks.append(worker)

        request = asyncio.create_task(launch())
        self.tasks.append(request)
        await entered.wait()
        self.assertIsNone(comfy_jobs.reserve_backend_shutdown())
        gate.set()
        await request
        self.assertIsNone(comfy_jobs.reserve_backend_shutdown())

    async def test_shutdown_blocks_generators_until_released_and_double_release_is_safe(self):
        release = comfy_jobs.reserve_backend_shutdown()
        self.assertIsNotNone(release)
        self.releases.append(release)
        self.assertIsNone(comfy_jobs.reserve_backend_shutdown())

        @comfy_jobs.exclusive("Painter")
        async def generate():
            return "ready"

        with self.assertRaises(HTTPException) as raised:
            await generate()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("cerrando", raised.exception.detail)
        release()
        self.assertEqual(await generate(), "ready")
        # An old callback cannot clear a subsequently acquired reservation.
        second = comfy_jobs.reserve_backend_shutdown("Second shutdown")
        self.releases.append(second)
        release()
        self.assertIsNotNone(comfy_jobs._active)
        self.assertEqual(comfy_jobs._active.owner, "Second shutdown")
        second()

    async def test_timer_thread_can_reserve_and_release_without_context(self):
        loop_thread = threading.get_ident()
        acquired = await asyncio.to_thread(lambda: (
            threading.get_ident(), comfy_jobs.reserve_backend_shutdown("Thread shutdown")))
        self.assertNotEqual(acquired[0], loop_thread)
        release = acquired[1]
        self.assertIsNotNone(release)
        self.releases.append(release)

        @comfy_jobs.exclusive("MiniMax H3")
        async def generate():
            return True

        with self.assertRaises(HTTPException):
            await generate()
        await asyncio.to_thread(release)
        self.assertTrue(await generate())

    async def test_competing_timer_threads_only_one_gets_admission(self):
        count = 8
        barrier = threading.Barrier(count)

        def compete():
            barrier.wait(timeout=5)
            return comfy_jobs.reserve_backend_shutdown()

        releases = await asyncio.gather(*(asyncio.to_thread(compete) for _ in range(count)))
        winners = [release for release in releases if release is not None]
        self.releases.extend(winners)
        self.assertEqual(len(winners), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
