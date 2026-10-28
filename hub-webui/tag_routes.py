"""Shared prompt-tag API for Painter and standalone Anima Studio.

Importing this router loads no CSVs and does not import the Painter backend.
Both servers mount the same router under /api/painter and share tag_engine.
"""
import asyncio
import csv as _csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "apps" / "painter" / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

_PROFILES_FILE = _ROOT / "apps" / "painter" / "model_profiles.json"
tags_router = APIRouter(prefix="/tags", tags=["tags"])

try:
    import tag_engine as _te
except ImportError:
    _te = None  # type: ignore


def _load_profiles_data() -> dict:
    if not _PROFILES_FILE.exists():
        return {"profiles": [], "default": ""}
    try:
        return json.loads(_PROFILES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": [], "default": ""}


_tag_operations: dict[str, asyncio.Lock] = {}


def _tag_path(csv_name: str) -> Path:
    try:
        return _te.csv_path_for(csv_name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _tag_operation(csv_name: str) -> asyncio.Lock:
    # The Hub uses one event loop. Serialize writers and first loads per CSV,
    # while already loaded searches keep using the immutable previous index.
    return _tag_operations.setdefault(csv_name, asyncio.Lock())


async def _tag_cpu(function, *args):
    """Keep CPU off the event loop; finish file readers before cancellation cleanup."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except (_csv.Error, UnicodeError, OSError, ValueError) as error:
        raise HTTPException(503, f"No se pudo leer la biblioteca de tags: {error}") from error
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


async def _ensure_tags(csv_name: str) -> int:
    if _te.is_loaded(csv_name):
        return _te.tag_count(csv_name)
    async with _tag_operation(csv_name):
        return await _tag_cpu(_te.ensure_loaded, csv_name)


@tags_router.get("/status")
async def tags_status(csv: str = ""):
    """Estado de un CSV específico. csv = nombre sin extensión."""
    if not csv or not _te:
        return {"loaded": False, "count": 0, "csv_present": False}
    csv_p = _tag_path(csv)
    await _ensure_tags(csv)
    return {
        "loaded":      _te.is_loaded(csv),
        "count":       _te.tag_count(csv),
        "csv_present": csv_p.exists(),
    }


@tags_router.get("/search")
async def tags_search(q: str = "", csv: str = "", limit: int = 5):
    """Busca tags en el CSV del perfil activo."""
    limit = max(1, min(limit, 20))
    if not _te or not csv:
        return {"results": []}
    _tag_path(csv)
    if not q.strip():
        return {"results": []}
    await _ensure_tags(csv)
    return {"results": await _tag_cpu(_te.search, csv, q, limit)}


@tags_router.post("/reload")
async def tags_reload(csv: str = ""):
    if not _te or not csv:
        raise HTTPException(400, "Falta el parámetro csv")
    _tag_path(csv)
    async with _tag_operation(csv):
        n = await _tag_cpu(_te.load_csv, csv)
    return {"ok": True, "count": n}


@tags_router.get("/profiles")
async def tags_profiles():
    return _load_profiles_data()


@tags_router.get("/download")
async def tags_download(profile_id: str):
    """SSE — descarga el CSV configurado para el perfil indicado."""
    data = _load_profiles_data()
    profiles_map = {p["id"]: p for p in data.get("profiles", [])}
    profile  = profiles_map.get(profile_id)
    if not profile:
        raise HTTPException(404, "Perfil no encontrado")
    csv_name = profile.get("tag_csv")
    csv_url  = profile.get("tag_csv_url")
    if not csv_name or not csv_url:
        raise HTTPException(400, "Este perfil no tiene CSV configurado")
    dest = _tag_path(csv_name) if _te else None

    async def stream() -> AsyncGenerator[str, None]:
        if not _te:
            yield f'data: {json.dumps({"type":"error","msg":"Motor de tags no disponible"})}\n\n'
            return

        async with _tag_operation(csv_name):
            staged = None
            try:
                import aiohttp
                dest.parent.mkdir(parents=True, exist_ok=True)
                yield f'data: {json.dumps({"type":"start","msg":f"Descargando {csv_name}…"})}\n\n'
                descriptor, filename = tempfile.mkstemp(prefix=f".{csv_name}.", suffix=".tmp", dir=dest.parent)
                os.close(descriptor)
                staged = Path(filename)
                timeout = aiohttp.ClientTimeout(total=120, connect=30, sock_read=30)
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get(csv_url) as resp:
                        if resp.status != 200:
                            raise ValueError(f"HTTP {resp.status}")
                        total = int(resp.headers.get("Content-Length", 0))
                        downloaded = 0
                        with staged.open("wb") as output:
                            async for chunk in resp.content.iter_chunked(64 * 1024):
                                output.write(chunk)
                                downloaded += len(chunk)
                                pct = int(downloaded * 100 / total) if total else 0
                                yield f'data: {json.dumps({"type":"progress","downloaded":downloaded,"total":total,"pct":pct})}\n\n'
                        if total and downloaded != total:
                            raise ValueError("La descarga de tags está incompleta")

                yield f'data: {json.dumps({"type":"loading","msg":"Indexando tags…"})}\n\n'
                prepared = await _tag_cpu(_te.prepare_csv, csv_name, staged)
                # No await between validation and this short atomic commit:
                # cancellation before it leaves both the prior file and index intact.
                n = _te.install_csv(csv_name, staged, prepared)
                yield f'data: {json.dumps({"type":"done","count":n,"csv":csv_name})}\n\n'
            except Exception as error:
                message = error.detail if isinstance(error, HTTPException) else str(error)
                yield f'data: {json.dumps({"type":"error","msg":message})}\n\n'
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
