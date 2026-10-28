"""
Model Vault API — rutas FastAPI sin PySide6.
Expone: modelos del DB, thumbnails, scan con SSE, edición de notas/tags.
"""
import os
import sys
import uuid
import json
import queue
import asyncio
import threading
import time
from urllib.parse import quote
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, Response
from pydantic import BaseModel

# ── Paths ────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)           # /ai-hub
_HUB  = os.path.join(_ROOT, "hub")
_APPS = os.path.join(_ROOT, "apps")

for _p in [_ROOT, _HUB, _APPS]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DB_PATH  = os.path.join(_HUB, ".cache", "model_vault.db")
_CFG_PATH = os.path.join(_HUB, "hub_config.json")
os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)

vault_router = APIRouter(prefix="/api/vault", tags=["vault"])

_scan_sessions: dict[str, queue.Queue] = {}
_scan_lock = threading.Lock()
_civitai_sessions: dict[str, queue.Queue] = {}
_civitai_lock = threading.Lock()
_operation_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2)


class _SessionQueue(queue.Queue):
    """Keep recent progress and the terminal event if the browser disconnects."""
    def __init__(self):
        super().__init__(maxsize=2)
        self.finished_at = None

    def put(self, item, block=True, timeout=None):
        while True:
            try:
                return super().put(item, block=False)
            except queue.Full:
                try:
                    super().get(block=False)
                except queue.Empty:
                    pass


def _prune_sessions(sessions, reserve=False):
    now = time.monotonic()
    completed = sorted((q.finished_at, key) for key, q in sessions.items()
                       if getattr(q, "finished_at", None) is not None)
    for finished_at, key in completed:
        if now - finished_at > 300 or len(sessions) > (15 if reserve else 16):
            sessions.pop(key, None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _start_operation(sessions, sessions_lock, worker):
    """Una sola revisión/descarga modifica el inventario a la vez, entre pestañas."""
    if not _operation_lock.acquire(blocking=False):
        raise HTTPException(409, "Model Vault ya está actualizando la biblioteca o los datos de Civitai.")
    session_id = str(uuid.uuid4())
    q = _SessionQueue()

    def _run():
        try:
            worker(q)
        except Exception as exc:
            q.put({"type": "error", "error": str(exc)})
        finally:
            q.finished_at = time.monotonic()
            _operation_lock.release()

    with sessions_lock:
        _prune_sessions(sessions, reserve=True)
        sessions[session_id] = q
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        with sessions_lock:
            sessions.pop(session_id, None)
        _operation_lock.release()
        raise
    return session_id

def _load_config() -> dict:
    try:
        with open(_CFG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _get_service():
    from model_vault.core.vault_service import VaultService
    api_key = None
    try:
        from state import state
        api_key = state.get_civitai_key()
    except Exception:
        cfg = _load_config()
        api_key = cfg.get("civitai_key") or None
    return VaultService(_DB_PATH, api_key=api_key)

def _find_thumbnail(file_path: str, file_paths=()) -> Optional[str]:
    from model_vault.core.model_assets import find_preview
    return find_preview(file_path, file_paths)

def _get_models_root() -> str:
    return _load_config().get("paths", {}).get("models", "")

def _enrich(m: dict, models_root: str = "") -> dict:
    """Añade has_thumbnail y subfolder al dict de un modelo."""
    m = dict(m)
    last_preview_status = m.get("preview_status")
    from model_vault.core.model_assets import assets_status
    assets = assets_status(m)
    m.update(assets)
    preview_status = assets["preview_status"]
    m["civitai_assets_status"] = (
        preview_status if not assets["has_thumbnail"] and preview_status in ("unavailable", "not_found") else
        "ready" if assets["assets_complete"] else
        "error" if last_preview_status == "error" else "pending"
    )
    m["thumbnail_url"] = (
        f"/api/vault/models/{quote(m['hash'], safe='')}/thumbnail"
        if m["has_thumbnail"] else None
    )

    subfolder = ""
    folder = ""
    storage_category = ""
    file_path = m.get("file_path", "")
    if file_path and models_root:
        try:
            from model_vault.core.file_operations import relative_folder
            folder, storage_category = relative_folder(file_path, models_root)
            parts = folder.split("/")
            subfolder = "/".join(parts[1:])
        except Exception:
            pass
    m["subfolder"] = subfolder
    m["folder"] = folder
    m["storage_category"] = storage_category
    return m


# ── Pydantic models ───────────────────────────────────────────────────────────

class UpdateModelPayload(BaseModel):
    user_notes:  Optional[str] = None
    custom_tags: Optional[str] = None

class MoveEntry(BaseModel):
    hash:             str
    target_subfolder: str = ""  # legado: relativo a la categoría física de origen
    target_folder: Optional[str] = None  # relativo a la raíz configurada de modelos

class ApplyMovesPayload(BaseModel):
    moves: list[MoveEntry]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@vault_router.get("/models")
async def get_models(q: str = "", cat: str = "", sort: str = "recent", arch: str = ""):
    """Lista modelos del DB con filtrado por categoría, búsqueda y orden."""
    loop = asyncio.get_event_loop()

    def _load():
        svc    = _get_service()
        models = svc.db.get_all_models()

        # Filtrar categoría
        if cat and cat != "all":
            models = [m for m in models
                      if (m.get("model_type") or "").lower() == cat.lower()]

        if arch:
            models = [m for m in models
                      if (m.get("base_model") or "Unknown").strip().casefold() == arch.strip().casefold()]

        # Filtrar búsqueda
        if q:
            ql = q.lower().replace("\\", "/")
            fields = ("name", "display_name", "creator_name", "custom_tags",
                      "civitai_tags", "base_model", "triggers", "file_path")
            models = [m for m in models if any(
                ql in (m.get(field) or "").lower().replace("\\", "/")
                for field in fields
            ) or any(ql in path.lower().replace("\\", "/")
                     for path in m.get("file_paths", []))]

        # Ordenar
        if sort == "name":
            models.sort(key=lambda m: (m.get("display_name") or m.get("name") or "").lower())
        elif sort == "arch":
            models.sort(key=lambda m: (m.get("base_model") or "").lower())
        # "recent" usa el orden por defecto del DB (date_added DESC)

        models_root = _get_models_root()
        return [_enrich(m, models_root) for m in models]

    models = await loop.run_in_executor(_pool, _load)
    return {"models": models, "total": len(models)}


@vault_router.get("/thumbnail")
async def get_thumbnail(path: str):
    """Sirve el thumbnail de un modelo dado su file_path."""
    from model_vault.core.model_assets import image_media_type
    thumb = _find_thumbnail(path)
    if not thumb:
        def _resolve():
            model = _get_service().db.get_model_by_path(path)
            return _find_thumbnail(path, model.get("file_paths", ())) if model else None
        thumb = await asyncio.get_running_loop().run_in_executor(_pool, _resolve)
    if not thumb:
        return Response(status_code=404)
    return FileResponse(thumb, media_type=image_media_type(thumb))


@vault_router.get("/models/{model_hash}/thumbnail")
async def get_model_thumbnail(model_hash: str):
    """Resuelve la imagen por identidad, incluyendo ubicaciones alternativas."""
    from model_vault.core.model_assets import image_media_type

    def _resolve():
        model = _get_service().db.get_model_by_hash(model_hash)
        return _find_thumbnail(model.get("file_path", ""), model.get("file_paths", ())) if model else None

    thumb = await asyncio.get_running_loop().run_in_executor(_pool, _resolve)
    if not thumb:
        return Response(status_code=404)
    return FileResponse(thumb, media_type=image_media_type(thumb))


@vault_router.get("/models/{model_hash}")
async def get_model_detail(model_hash: str):
    """Detalles completos de un modelo por su hash."""
    loop = asyncio.get_event_loop()

    def _load():
        svc = _get_service()
        m   = svc.db.get_model_by_hash(model_hash)
        models_root = _get_models_root()
        return _enrich(m, models_root) if m else None

    m = await loop.run_in_executor(_pool, _load)
    if not m:
        return Response(status_code=404)
    return m


@vault_router.patch("/models/{model_hash}")
async def update_model(model_hash: str, payload: UpdateModelPayload):
    """Actualiza notas de usuario o tags personalizados."""
    loop = asyncio.get_event_loop()

    def _update():
        svc = _get_service()
        if not svc.db.get_model_by_hash(model_hash):
            raise HTTPException(404, "Modelo no encontrado")
        if payload.user_notes is not None:
            svc.db.update_user_notes(model_hash, payload.user_notes)
        if payload.custom_tags is not None:
            svc.db.update_custom_tags(model_hash, payload.custom_tags)
        return {"ok": True}

    return await loop.run_in_executor(_pool, _update)


@vault_router.post("/scan")
async def start_scan(deep: bool = False, enrich: bool = True):
    """Descubre archivos y completa datos, JSON e imágenes con Civitai."""
    def _run(q):
        models_dir = _get_models_root()
        if not models_dir or not os.path.isdir(models_dir):
            raise ValueError(f"Directorio de modelos no configurado: {models_dir}")
        svc = _get_service()

        def _cb(current, total, name):
            q.put({"type": "progress", "current": current,
                   "total": total, "name": name, "phase": "local"})

        stats = svc.scan_and_index(models_dir, deep_scan=deep, progress_callback=_cb)
        result = dict(stats or {})
        if enrich:
            q.put({"type": "progress", "current": 0, "total": 0,
                   "name": "Civitai", "phase": "civitai"})

            def _civitai_cb(current, total, name):
                q.put({"type": "progress", "current": current,
                       "total": total, "name": name, "phase": "civitai"})

            try:
                result["civitai"] = svc.sync_all(progress_cb=_civitai_cb)
            except Exception as exc:
                # La biblioteca descubierta sigue disponible aunque falle el proveedor.
                result["civitai_error"] = str(exc)
        count = len(svc.db.get_all_models())
        q.put({"type": "done", "total": count, **result})

    session_id = _start_operation(_scan_sessions, _scan_lock, _run)
    return {"session_id": session_id}


@vault_router.get("/scan-progress/{session_id}")
async def scan_progress(session_id: str):
    """SSE stream del progreso de un scan en curso."""
    with _scan_lock:
        _prune_sessions(_scan_sessions)
        q = _scan_sessions.get(session_id)
    if not q:
        return Response(status_code=404)

    async def _gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    event = await loop.run_in_executor(
                        None, lambda: q.get(timeout=30)
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("done", "error"):
                        break
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            with _scan_lock:
                _scan_sessions.pop(session_id, None)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@vault_router.get("/civitai-pending")
async def get_civitai_pending():
    """Cuenta modelos pendientes de datos, JSON o miniatura, sin iniciar trabajo."""
    loop = asyncio.get_event_loop()

    def _count():
        svc = _get_service()
        return len(svc.pending_models())

    pending = await loop.run_in_executor(_pool, _count)
    return {"pending": pending}


@vault_router.post("/civitai-sync")
async def start_civitai_sync(force: bool = False):
    """Completa datos, JSON y miniaturas; devuelve session_id y cantidad pendiente."""
    loop = asyncio.get_event_loop()

    def _prepare():
        svc = _get_service()
        return len(svc.pending_models(force=force))

    pending = await loop.run_in_executor(_pool, _prepare)

    def _run(q):
        svc = _get_service()

        def _cb(current, total, name):
            q.put({"type": "progress", "current": current, "total": total, "name": name})

        stats = svc.sync_all(progress_cb=_cb, force=force)
        q.put({"type": "done", **stats})

    session_id = _start_operation(_civitai_sessions, _civitai_lock, _run)
    return {"session_id": session_id, "pending": pending}


@vault_router.get("/civitai-progress/{session_id}")
async def civitai_progress(session_id: str):
    """SSE stream del progreso del sync de Civitai."""
    with _civitai_lock:
        _prune_sessions(_civitai_sessions)
        q = _civitai_sessions.get(session_id)
    if not q:
        return Response(status_code=404)

    async def _gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    event = await loop.run_in_executor(
                        None, lambda: q.get(timeout=30)
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("done", "error"):
                        break
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            with _civitai_lock:
                _civitai_sessions.pop(session_id, None)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@vault_router.post("/apply-moves")
async def apply_moves(payload: ApplyMovesPayload):
    """Mueve archivos de modelos a subfolders destino y actualiza la DB."""
    loop = asyncio.get_event_loop()

    def _run():
        from model_vault.core.file_operations import move_model_files, relative_folder
        svc         = _get_service()
        models_root = _get_models_root()
        moved  = 0
        errors = []
        moved_hashes = []
        failed_hashes = []

        for entry in payload.moves:
            try:
                m = svc.db.get_model_by_hash(entry.hash)
                if not m:
                    raise ValueError("Modelo no encontrado")

                file_path  = m["file_path"]
                target_folder = entry.target_folder
                if target_folder is None:
                    _, cat_folder = relative_folder(file_path, models_root)
                    target_folder = "/".join(part for part in (cat_folder, entry.target_subfolder) if part)
                _, changed = move_model_files(
                    file_path, models_root, target_folder,
                    update_path=lambda path: svc.db.update_file_path(entry.hash, path, old_path=file_path),
                )
                moved += int(changed)
                moved_hashes.append(entry.hash)

            except Exception as e:
                errors.append(f"{entry.hash[:8]}: {e}")
                failed_hashes.append(entry.hash)

        return {"moved": moved, "errors": len(errors), "error_detail": errors,
                "moved_hashes": moved_hashes, "failed_hashes": failed_hashes}

    if not _operation_lock.acquire(blocking=False):
        raise HTTPException(409, "Espera a que termine la actualización de Model Vault antes de mover modelos.")
    def _guarded_run():
        try:
            return _run()
        finally:
            _operation_lock.release()

    try:
        future = loop.run_in_executor(_pool, _guarded_run)
    except Exception:
        _operation_lock.release()
        raise
    return await asyncio.shield(future)


@vault_router.get("/arch-map")
async def arch_map():
    """
    Devuelve {basename_sin_ext: base_model} para todos los modelos en la DB.
    El Painter lo usa para filtrar modelos por arquitectura sin depender
    de heurísticas de nombre.
    """
    def _run():
        svc    = _get_service()
        models = svc.db.get_all_models()
        result = {}
        for m in models:
            arch = m.get("base_model") or ""
            if arch:
                for fp in m.get("file_paths") or [m.get("file_path", "")]:
                    if fp:
                        # Cada nombre físico (incluidos hardlinks) necesita su arquitectura.
                        stem = os.path.splitext(os.path.basename(fp))[0]
                        result[stem] = arch
        return result

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_pool, _run)
