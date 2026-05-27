"""
Model Vault API — rutas FastAPI sin PySide6.
Expone: modelos del DB, thumbnails, scan con SSE, edición de notas/tags.
"""
import os
import sys
import uuid
import json
import queue
import shutil
import asyncio
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
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
_pool = ThreadPoolExecutor(max_workers=2)


# ── Helpers ───────────────────────────────────────────────────────────────────

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

def _find_thumbnail(file_path: str) -> Optional[str]:
    if not file_path:
        return None
    base = os.path.splitext(file_path)[0]
    for ext in (".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp"):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    return None

def _get_models_root() -> str:
    return _load_config().get("paths", {}).get("models", "")

def _enrich(m: dict, models_root: str = "") -> dict:
    """Añade has_thumbnail y subfolder al dict de un modelo."""
    m = dict(m)
    m["has_thumbnail"] = bool(_find_thumbnail(m.get("file_path", "")))

    subfolder = ""
    file_path = m.get("file_path", "")
    if file_path and models_root:
        try:
            rel = os.path.relpath(file_path, models_root)
            parts = rel.replace("\\", "/").split("/")
            # parts[0]=carpeta_categoria, parts[-1]=archivo
            # path completo del subfolder (soporta anidamiento arbitrario)
            if len(parts) > 2:
                subfolder = "/".join(parts[1:-1])
        except Exception:
            pass
    m["subfolder"] = subfolder
    return m


# ── Pydantic models ───────────────────────────────────────────────────────────

class UpdateModelPayload(BaseModel):
    user_notes:  Optional[str] = None
    custom_tags: Optional[str] = None

class MoveEntry(BaseModel):
    hash:             str
    target_subfolder: str   # "" = raíz de la categoría

class ApplyMovesPayload(BaseModel):
    moves: list[MoveEntry]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@vault_router.get("/models")
async def get_models(q: str = "", cat: str = "", sort: str = "recent"):
    """Lista modelos del DB con filtrado por categoría, búsqueda y orden."""
    loop = asyncio.get_event_loop()

    def _load():
        svc    = _get_service()
        models = svc.db.get_all_models()

        # Filtrar categoría
        if cat and cat != "all":
            models = [m for m in models
                      if (m.get("model_type") or "").lower() == cat.lower()]

        # Filtrar búsqueda
        if q:
            ql = q.lower()
            models = [m for m in models if
                      ql in (m.get("name") or "").lower()
                      or ql in (m.get("display_name") or "").lower()
                      or ql in (m.get("creator_name") or "").lower()
                      or ql in (m.get("custom_tags") or "").lower()
                      or ql in (m.get("civitai_tags") or "").lower()
                      or ql in (m.get("base_model") or "").lower()
                      or ql in (m.get("triggers") or "").lower()]

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
    thumb = _find_thumbnail(path)
    if not thumb:
        return Response(status_code=404)
    return FileResponse(thumb)


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
        if payload.user_notes is not None:
            svc.db.update_user_notes(model_hash, payload.user_notes)
        if payload.custom_tags is not None:
            svc.db.update_custom_tags(model_hash, payload.custom_tags)
        return {"ok": True}

    return await loop.run_in_executor(_pool, _update)


@vault_router.post("/scan")
async def start_scan(deep: bool = False):
    """Inicia un scan del directorio de modelos. Devuelve session_id."""
    session_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _scan_lock:
        _scan_sessions[session_id] = q

    def _run():
        try:
            cfg        = _load_config()
            models_dir = cfg.get("paths", {}).get("models")
            if not models_dir or not os.path.isdir(models_dir):
                q.put({"type": "error",
                       "error": f"Directorio de modelos no configurado: {models_dir}"})
                return

            svc = _get_service()

            def _cb(current, total, name):
                q.put({"type": "progress", "current": current,
                       "total": total, "name": name})

            svc.scan_and_index(models_dir, deep_scan=deep, progress_callback=_cb)
            count = len(svc.db.get_all_models())
            q.put({"type": "done", "total": count})
        except Exception as e:
            import traceback
            q.put({"type": "error",
                   "error": f"{e}\n{traceback.format_exc()}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"session_id": session_id}


@vault_router.get("/scan-progress/{session_id}")
async def scan_progress(session_id: str):
    """SSE stream del progreso de un scan en curso."""
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
    """Retorna cuántos modelos no tienen civitai_tags aún, sin iniciar sync."""
    loop = asyncio.get_event_loop()

    def _count():
        svc = _get_service()
        models = svc.db.get_all_models()
        return sum(1 for m in models if not (m.get("civitai_tags") or "").strip())

    pending = await loop.run_in_executor(_pool, _count)
    return {"pending": pending}


@vault_router.post("/civitai-sync")
async def start_civitai_sync(force: bool = False):
    """Inicia sync de tags Civitai para todos los modelos en DB. Devuelve session_id y pending count."""
    loop = asyncio.get_event_loop()

    def _prepare():
        svc = _get_service()
        models = svc.db.get_all_models()
        if force:
            return len(models)
        return sum(1 for m in models if not (m.get("civitai_tags") or "").strip())

    pending = await loop.run_in_executor(_pool, _prepare)

    session_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _civitai_lock:
        _civitai_sessions[session_id] = q

    def _run():
        try:
            svc = _get_service()

            def _cb(current, total, name):
                q.put({"type": "progress", "current": current, "total": total, "name": name})

            stats = svc.sync_all(progress_cb=_cb, force=force)
            q.put({"type": "done", **stats})
        except Exception as e:
            import traceback
            q.put({"type": "error", "error": f"{e}\n{traceback.format_exc()}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"session_id": session_id, "pending": pending}


@vault_router.get("/civitai-progress/{session_id}")
async def civitai_progress(session_id: str):
    """SSE stream del progreso del sync de Civitai."""
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
        svc         = _get_service()
        models_root = _get_models_root()
        moved  = 0
        errors = []

        for entry in payload.moves:
            try:
                m = svc.db.get_model_by_hash(entry.hash)
                if not m:
                    errors.append(f"{entry.hash[:8]}: no encontrado")
                    continue

                file_path  = m["file_path"]
                rel        = os.path.relpath(file_path, models_root)
                parts      = rel.replace("\\", "/").split("/")
                cat_folder = parts[0]
                filename   = parts[-1]

                if entry.target_subfolder:
                    new_path = os.path.join(models_root, cat_folder,
                                            entry.target_subfolder, filename)
                else:
                    new_path = os.path.join(models_root, cat_folder, filename)

                if os.path.normpath(new_path) == os.path.normpath(file_path):
                    continue

                new_dir   = os.path.dirname(new_path)
                os.makedirs(new_dir, exist_ok=True)

                # Mover archivos compañeros (preview, json, etc.) que comparten el nombre base
                src_dir   = os.path.dirname(file_path)
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                main_name = os.path.basename(file_path)
                for fname in os.listdir(src_dir):
                    if fname != main_name and fname.startswith(base_name + "."):
                        companion = os.path.join(src_dir, fname)
                        if os.path.isfile(companion):
                            shutil.move(companion, os.path.join(new_dir, fname))

                shutil.move(file_path, new_path)
                svc.db.update_file_path(entry.hash, new_path)
                moved += 1

            except Exception as e:
                errors.append(f"{entry.hash[:8]}: {e}")

        return {"moved": moved, "errors": len(errors), "error_detail": errors}

    return await loop.run_in_executor(_pool, _run)


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
            fp   = m.get("file_path", "")
            arch = m.get("base_model") or ""
            if fp and arch:
                # Clave: nombre de archivo sin extensión (case-insensitive en consulta)
                stem = os.path.splitext(os.path.basename(fp))[0]
                result[stem] = arch
        return result

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_pool, _run)
