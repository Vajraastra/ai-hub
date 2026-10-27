"""Page sessions pin a shared headless backend through requests and workers."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import signature
import json
import time
import threading
from urllib.request import urlopen
import uuid

from fastapi import HTTPException, WebSocketDisconnect
import comfy_jobs


@dataclass
class Session:
    id: str
    owner: str
    seen: float
    opened: float
    jobs: int = 0
    closing: bool = False
    error: str = ""


class BackendSessions:
    def __init__(self, client, bridge_factory, *, app_id="comfyui", prefix="anima-studio",
                 ttl=180.0, grace=8.0, startup_timeout=180.0, clock=time.monotonic):
        self.client = client
        self.bridge_factory = bridge_factory
        self.app_id, self.prefix = app_id, prefix
        self.ttl, self.grace, self.startup_timeout = ttl, grace, startup_timeout
        self.clock = clock
        self.sessions = {}
        self.closed = {}
        self._lock = asyncio.Lock()
        self._sweeper = None
        self._context = ContextVar(f"{prefix}_session", default=None)
        self._bridge = None
        self._closing_lock = threading.Lock()
        self._closing_release = None
        self._watch_stop = threading.Event()

    def _bridge_instance(self):
        if self._bridge is None:
            self._bridge = self.bridge_factory()
            self._bridge.set_backend_idle_guard(self.app_id, self.backend_idle)
        port = self._bridge._effective_port(self.app_id)
        if not port:
            raise ValueError("ComfyUI no está configurado en el Hub.")
        self.client.base_url = f"http://127.0.0.1:{int(port)}"
        self.client.ws_url = f"ws://127.0.0.1:{int(port)}/ws"
        return self._bridge

    @staticmethod
    def identifier(value):
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Identificador de sesión inválido.")

    def backend_idle(self):
        """Timer-thread guard: a lost WS does not prove that the GPU stopped."""
        with self._closing_lock:
            if self._closing_release is not None:
                return True
        if comfy_jobs._active is not None:
            return False
        try:
            with urlopen(self.client.base_url + "/queue", timeout=3) as response:
                queue = json.load(response)
            idle = (isinstance(queue, dict) and "queue_running" in queue and "queue_pending" in queue
                    and not queue["queue_running"] and not queue["queue_pending"])
            if not idle:
                return False
            with self._closing_lock:
                if self._closing_release is not None:
                    return True
                release = comfy_jobs.reserve_backend_shutdown()
                if release is None:
                    return False
                self._closing_release = release
            threading.Thread(target=self._watch_backend_stop, args=(release,), daemon=True).start()
            return True
        except Exception:
            # If our managed process exited, there is nothing left to preserve.
            if self._bridge is not None:
                process = self._bridge._state.running_apps.get(self.app_id)
                return process is None or process.poll() is not None
            return False

    def _watch_backend_stop(self, release):
        started = time.monotonic()
        try:
            while not self._watch_stop.wait(0.05):
                bridge = self._bridge
                if bridge is None:
                    break
                process = bridge._state.running_apps.get(self.app_id)
                if process is None or process.poll() is not None:
                    break
                stopping = getattr(bridge._state, "busy_apps", {}).get(self.app_id) == "stopping"
                if not stopping and getattr(bridge, "_backend_owners", {}).get(self.app_id):
                    break  # A newly opened page cancelled the pending stop.
                elapsed = time.monotonic() - started
                if elapsed > 0.5 and not stopping and self.app_id not in getattr(bridge, "_backend_autostarted", set()):
                    break  # Manual adoption or a cancelled/failed stop.
                if elapsed > 20:
                    break  # Never strand admission if Windows cannot end a process.
        finally:
            release()
            with self._closing_lock:
                if self._closing_release is release:
                    self._closing_release = None

    async def _health(self):
        try:
            return await asyncio.wait_for(self.client.health_check(), 3)
        except (TimeoutError, OSError):
            return False

    def _maintain(self):
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._watch())

    async def open(self, value):
        session_id = self.identifier(value)
        async with self._lock:
            # A close beacon may overtake the request that was opening a page.
            if session_id in self.closed:
                return {"session_id": session_id, "status": "closed", "error": "La página ya se cerró."}
            session = self.sessions.get(session_id)
            if session is None:
                now = self.clock()
                session = Session(session_id, f"{self.prefix}:{session_id}", now, now)
                self.sessions[session_id] = session
                try:
                    bridge = self._bridge_instance()
                    ok = bridge.launch_backend(self.app_id, session.owner)
                    if not ok:
                        raise ValueError("No se pudo iniciar ComfyUI. Revisa su instalación en el Hub.")
                except Exception as exc:
                    session.error = str(exc)
            else:
                session.seen = self.clock()
            self._maintain()
        return await self._status(session)

    async def heartbeat(self, value):
        session_id = self.identifier(value)
        session = self.sessions.get(session_id)
        if session is None or session.closing:
            return {"session_id": session_id, "status": "closed", "error": "La sesión terminó; vuelve a abrir el taller."}
        session.seen = self.clock()
        return await self._status(session)

    async def socket(self, websocket, value):
        try:
            session_id = self.identifier(value)
        except HTTPException:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            state = await self.open(session_id)
            await websocket.send_json(state)
            if state["status"] == "closed":
                await websocket.close(code=1000)
                return
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), 15)
                except TimeoutError:
                    # Server-side keepalive also works when browser timers throttle.
                    session = self.sessions.get(session_id)
                    if session is None or session.closing:
                        break
                    await websocket.send_json({"type": "alive", **await self._status(session)})
                session = self.sessions.get(session_id)
                if session is None or session.closing:
                    break
                session.seen = self.clock()
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass
        finally:
            await self.close(session_id)

    async def _status(self, session):
        if session.closing:
            return {"session_id": session.id, "status": "closed"}
        if session.error:
            return {"session_id": session.id, "status": "error", "error": session.error}
        if await self._health():
            return {"session_id": session.id, "status": "ready"}
        if self._bridge is not None and self.clock() - session.opened > 3:
            state = self._bridge._state
            process = state.running_apps.get(self.app_id)
            busy = self.app_id in getattr(state, "busy_apps", {})
            port_up = getattr(self._bridge, "is_backend_up", lambda _: True)(self.app_id)
            if not busy and (process is None or process.poll() is not None) and not port_up:
                session.error = "ComfyUI se detuvo al iniciar. Revisa el log del Hub y reintenta."
                return {"session_id": session.id, "status": "error", "error": session.error}
        if self.clock() - session.opened > self.startup_timeout:
            session.error = "ComfyUI no respondió a tiempo. Revisa el log del Hub y reintenta."
            return {"session_id": session.id, "status": "error", "error": session.error}
        return {"session_id": session.id, "status": "starting"}

    async def close(self, value):
        session_id = self.identifier(value)
        self.closed[session_id] = self.clock()
        session = self.sessions.get(session_id)
        if session is not None:
            session.closing = True
            self._release_if_finished(session)
        return {"session_id": session_id, "status": "closed"}

    def _release_if_finished(self, session):
        if not session.closing or session.jobs:
            return
        if self._bridge is not None:
            self._bridge.release_backend(self.app_id, session.owner, grace=self.grace)
        self.sessions.pop(session.id, None)

    def job(self, func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            payload = kwargs.get("payload") or (args[0] if args else {})
            session_id = payload.get("session_id")
            # Existing API clients may use a backend they started themselves.
            session = None
            if session_id:
                session_id = self.identifier(session_id)
                session = self.sessions.get(session_id)
                if session is None or session.closing or session.error:
                    raise HTTPException(409, "La sesión del taller terminó. Reabre o reintenta el backend.")
                session.jobs += 1
                session.seen = self.clock()
            token = self._context.set(session)
            try:
                return await func(*args, **kwargs)
            finally:
                self._context.reset(token)
                if session:
                    session.jobs -= 1
                    self._release_if_finished(session)
        wrapped.__signature__ = signature(func, eval_str=True)
        return wrapped

    def track_worker(self, task):
        session = self._context.get()
        if session:
            session.jobs += 1
            def done(_):
                session.jobs -= 1
                self._release_if_finished(session)
            task.add_done_callback(done)

    async def sweep(self):
        now = self.clock()
        for session in list(self.sessions.values()):
            if now - session.seen > self.ttl:
                session.closing = True
                self.closed[session.id] = now
                self._release_if_finished(session)
        for key, closed_at in list(self.closed.items()):
            if now - closed_at > self.ttl * 2:
                self.closed.pop(key, None)

    async def _watch(self):
        while self.sessions or self.closed:
            await asyncio.sleep(5)
            await self.sweep()

    async def shutdown(self):
        self._watch_stop.set()
        with self._closing_lock:
            if self._closing_release:
                self._closing_release()
                self._closing_release = None
        for session in list(self.sessions.values()):
            session.closing = True
            self._release_if_finished(session)
        if self._sweeper:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper, return_exceptions=True)
            self._sweeper = None
