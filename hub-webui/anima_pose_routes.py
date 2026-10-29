"""Pose-control catalogue and explicit, bounded installation of Anima weights."""
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from fastapi import APIRouter, HTTPException

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "apps"))
from anima_studio.core import pose_control

anima_pose_router = APIRouter(prefix="/api/anima-studio/pose", tags=["anima-studio"])
_installation_lock = asyncio.Lock()


def _models_root():
    config_path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        root = Path(value["paths"]["models"])
        if not root.is_dir():
            raise ValueError("No existe la biblioteca de modelos.")
        return root
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, f"Revisa la carpeta de modelos en los ajustes del Hub: {exc}") from exc


@anima_pose_router.get("/catalog")
async def catalog():
    return await asyncio.to_thread(pose_control.catalog, _models_root())


@anima_pose_router.post("/install/{model_id}")
async def install(model_id: str):
    async with _installation_lock:
        try:
            return await pose_control.install(_models_root(), model_id)
        except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise HTTPException(400, f"No se instaló el control de pose: {exc}") from exc
