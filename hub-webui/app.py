"""
AI Hub WebUI — Implementación completa
Backend: FastAPI + WebSocket
Frontend: browser del sistema
"""
import os
import sys
import json
import signal
import atexit
import threading
import asyncio
import socket
from typing import Optional

# En Windows la consola usa cp1252 por defecto y revienta (UnicodeEncodeError)
# al imprimir caracteres no-ASCII (→, —, emojis), que abundan en los logs del
# hub. Forzar UTF-8 en stdout/stderr evita que un simple print tumbe un thread.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Paths ──────────────────────────────────────────────────────────────────
POC_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.join(os.path.dirname(POC_DIR), "hub")
PROJECT_ROOT = os.path.dirname(POC_DIR)
for path in [HUB_DIR, PROJECT_ROOT]:
    if path not in sys.path:
        sys.path.insert(0, path)

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
import uvicorn

from hub_bridge import bridge
from vault_routes import vault_router
from painter_routes import painter_router
from fusion_routes import fusion_router
from ideogram_routes import ideogram_router
from faceswap_routes import faceswap_router

# ── WebSocket clients ───────────────────────────────────────────────────────
_ws_clients: list[WebSocket] = []
_ws_lock = threading.Lock()
_event_loop: asyncio.AbstractEventLoop | None = None


async def _broadcast(data: dict):
    msg = json.dumps(data)
    dead = []
    with _ws_lock:
        clients = list(_ws_clients)
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    if dead:
        with _ws_lock:
            for ws in dead:
                if ws in _ws_clients:
                    _ws_clients.remove(ws)


def _schedule(coro):
    loop = _event_loop
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)


# ── Handlers del bridge ─────────────────────────────────────────────────────

def _on_state_changed(app_id: str):
    _schedule(_broadcast({
        "type": "state_changed",
        "app_id": app_id,
        "apps": bridge.get_apps_payload(),
    }))


def _on_log(app_id: str, line: str):
    _schedule(_broadcast({"type": "log", "app_id": app_id, "line": line}))


bridge.on_state_changed(_on_state_changed)
bridge.on_log(_on_log)


# ── FastAPI ─────────────────────────────────────────────────────────────────
api = FastAPI(title="AI Hub WebUI")


_NO_CACHE_PATHS = {"/", "/vault", "/painter", "/fusion", "/ideogram", "/faceswap"}

# ── Stop-all con periodo de gracia ───────────────────────────────────────────
_STOP_GRACE_SECS = 8.0
_pending_stop: Optional[threading.Timer] = None
_pending_stop_lock = threading.Lock()


def _cancel_pending_stop():
    global _pending_stop
    with _pending_stop_lock:
        if _pending_stop is not None:
            _pending_stop.cancel()
            _pending_stop = None


class _CancelPendingStop(BaseHTTPMiddleware):
    """Cualquier página o petición API tras el beacon de stop-all significa
    que el hub sigue en uso (recarga u otra pestaña) → cancelar el stop."""
    async def dispatch(self, request: Request, call_next):
        p = request.url.path
        if p in _NO_CACHE_PATHS or (p.startswith("/api/") and p != "/api/stop-all"):
            _cancel_pending_stop()
        return await call_next(request)

class _NoCacheStatic(BaseHTTPMiddleware):
    """Fuerza no-cache en todas las rutas HTML y estáticos para evitar cache persistente de WebKit2GTK."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/") or path in _NO_CACHE_PATHS:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


api.add_middleware(_NoCacheStatic)
api.add_middleware(_CancelPendingStop)
api.mount("/static", StaticFiles(directory=os.path.join(POC_DIR, "static")), name="static")
api.include_router(vault_router)
api.include_router(painter_router)
api.include_router(fusion_router)
api.include_router(ideogram_router)
api.include_router(faceswap_router)


# ── Models Pydantic ──────────────────────────────────────────────────────────
class HubSettingsPayload(BaseModel):
    models_path: str = ""
    outputs_path: str = ""
    civitai_key: str = ""


class AppConfigPayload(BaseModel):
    port_override: Optional[int] = None
    active_flags: list[dict] = []
    free_args: str = ""


class ValidatePathPayload(BaseModel):
    path: str = ""


class ApplyPathPayload(BaseModel):
    path: str = ""
    create_if_missing: bool = False


class PurgeCachePayload(BaseModel):
    package: str = ""


# ── Rutas estáticas ──────────────────────────────────────────────────────────
@api.get("/")
async def index():
    return FileResponse(os.path.join(POC_DIR, "static", "index.html"))

@api.get("/vault")
async def vault_page():
    return FileResponse(os.path.join(POC_DIR, "static", "vault.html"))

@api.get("/painter")
async def painter_page():
    return FileResponse(os.path.join(POC_DIR, "static", "painter.html"))

@api.get("/fusion")
async def fusion_page():
    return FileResponse(os.path.join(POC_DIR, "static", "fusion.html"))

@api.get("/ideogram")
async def ideogram_page():
    return FileResponse(os.path.join(POC_DIR, "static", "ideogram.html"))

@api.get("/faceswap")
async def faceswap_page():
    return FileResponse(os.path.join(POC_DIR, "static", "faceswap.html"))


# ── Apps ─────────────────────────────────────────────────────────────────────
@api.get("/api/apps")
async def get_apps():
    return {"apps": bridge.get_apps_payload()}


@api.get("/api/sysinfo")
async def get_sysinfo():
    return bridge.get_sysinfo()


@api.post("/api/apps/{app_id}/launch")
async def launch_app(app_id: str):
    ok = bridge.launch(app_id)
    return {"ok": ok}


@api.post("/api/apps/{app_id}/stop")
async def stop_app(app_id: str):
    ok = bridge.stop(app_id)
    return {"ok": ok}


@api.post("/api/apps/{app_id}/launch-backend")
async def launch_backend(app_id: str, owner: str = "hub"):
    """Arranca una app como backend headless (sin abrir navegador), a nombre
    del módulo 'owner'. Reutilizable por cualquier página del hub."""
    return {"ok": bridge.launch_backend(app_id, owner)}


@api.post("/api/apps/{app_id}/stop-backend")
async def stop_backend(app_id: str, owner: str = "hub"):
    return {"ok": bridge.stop_backend(app_id, owner)}


@api.post("/api/apps/{app_id}/release-backend")
async def release_backend(app_id: str, owner: str = "hub"):
    """La página propietaria se cerró/recargó: cierra el backend con gracia
    si lo arrancó ella. Pensado para sendBeacon en beforeunload."""
    return {"ok": bridge.release_backend(app_id, owner)}


@api.post("/api/apps/{app_id}/install")
async def install_app(app_id: str):
    ok = bridge.install(app_id)
    return {"ok": ok}


@api.post("/api/apps/{app_id}/update")
async def update_app(app_id: str):
    ok = bridge.update(app_id)
    return {"ok": ok}


@api.post("/api/apps/{app_id}/uninstall")
async def uninstall_app(app_id: str):
    ok = bridge.uninstall(app_id)
    return {"ok": ok}


# ── Per-app config ────────────────────────────────────────────────────────────
@api.get("/api/apps/{app_id}/config")
async def get_app_config(app_id: str):
    return bridge.get_app_config(app_id)


@api.post("/api/apps/{app_id}/config")
async def save_app_config(app_id: str, payload: AppConfigPayload):
    return bridge.save_app_config(
        app_id,
        payload.port_override,
        payload.active_flags,
        payload.free_args,
    )


# ── Hub Settings ──────────────────────────────────────────────────────────────
@api.get("/api/settings")
async def get_settings():
    return bridge.get_hub_settings()


@api.post("/api/settings")
async def save_settings(payload: HubSettingsPayload):
    return bridge.save_hub_settings(
        payload.models_path,
        payload.outputs_path,
        payload.civitai_key,
    )


# ── Models Path Management ───────────────────────────────────────────────────
@api.post("/api/settings/validate-models-path")
async def validate_models_path(payload: ValidatePathPayload):
    return bridge.validate_models_path(payload.path)


@api.post("/api/settings/apply-models-path")
async def apply_models_path(payload: ApplyPathPayload):
    return bridge.apply_models_path(payload.path, payload.create_if_missing)


@api.post("/api/settings/reorganize-orphans")
async def reorganize_orphans(payload: ValidatePathPayload):
    return bridge.reorganize_orphans(payload.path)


@api.post("/api/settings/purge-uv-cache")
async def purge_uv_cache(payload: PurgeCachePayload):
    return bridge.purge_uv_cache(payload.package)


# ── Event Log ─────────────────────────────────────────────────────────────────
@api.get("/api/log")
async def get_log(lines: int = 200):
    return {"entries": bridge.get_event_log(lines)}


# ── Cleanup ───────────────────────────────────────────────────────────────────
@api.post("/api/cleanup")
async def cleanup():
    return bridge.cleanup_stale()


@api.post("/api/stop-all")
async def stop_all():
    """Programa el stop de todas las apps con periodo de gracia.

    El beacon de beforeunload también se dispara en un simple F5 o al navegar,
    así que no se puede matar las apps al instante: se programa el stop y
    cualquier petición posterior (la página recargada u otra pestaña del hub
    aún viva) lo cancela vía _CancelPendingStop.
    """
    global _pending_stop
    with _pending_stop_lock:
        if _pending_stop is not None:
            _pending_stop.cancel()

        def _fire():
            global _pending_stop
            with _pending_stop_lock:
                _pending_stop = None
            bridge.stop_all_running()

        _pending_stop = threading.Timer(_STOP_GRACE_SECS, _fire)
        _pending_stop.daemon = True
        _pending_stop.start()
    return {"ok": True, "grace_secs": _STOP_GRACE_SECS}


# ── Herramientas externas ─────────────────────────────────────────────────────
@api.post("/api/tools/{tool_id}/launch")
async def launch_tool(tool_id: str):
    return bridge.launch_tool(tool_id)


# ── WebSocket ─────────────────────────────────────────────────────────────────
@api.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    with _ws_lock:
        _ws_clients.append(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "apps": bridge.get_apps_payload(),
            "sysinfo": bridge.get_sysinfo(),
        }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            if ws in _ws_clients:
                _ws_clients.remove(ws)


# ── Servidor uvicorn ─────────────────────────────────────────────────────────
PORT = 9753


def _find_free_port(start: int = PORT) -> int:
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def _run_server(port: int):
    global _event_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _event_loop = loop
    config = uvicorn.Config(api, host="127.0.0.1", port=port,
                            loop="asyncio", log_level="warning")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())


# ── Entry point ───────────────────────────────────────────────────────────────

def _shutdown():
    """Limpieza al salir: detiene todas las apps gestionadas por el hub."""
    bridge.stop_all_running()


def main():
    port = _find_free_port()
    url  = f"http://127.0.0.1:{port}"

    # Registrar limpieza en señales del SO y en salida normal
    atexit.register(_shutdown)
    # SIGHUP no existe en Windows: solo registrar las señales disponibles
    _signals = [s for s in ("SIGTERM", "SIGHUP") if hasattr(signal, s)]
    for name in _signals:
        try:
            signal.signal(getattr(signal, name), lambda s, f: (_shutdown(), sys.exit(0)))
        except (OSError, ValueError):
            pass

    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    import time
    for _ in range(30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)

    print(f"  Servidor listo en {url}")

    import webbrowser
    webbrowser.open(url)
    print(f"  Servidor activo en {url}  (Ctrl+C para salir)")
    try:
        server_thread.join()
    except KeyboardInterrupt:
        print("\n  Saliendo...")
        _shutdown()


if __name__ == "__main__":
    main()
