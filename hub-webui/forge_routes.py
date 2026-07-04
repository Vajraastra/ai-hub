"""
Forge Lab API — rutas FastAPI del módulo (esqueleto Fase 0).

Por ahora solo diagnóstico: estado de ComfyUI, presencia de los ficheros de
modelo del adaptador y sondeo de los nodos de comfyUI-Realtime-Lora vía API.
Las rutas de generación/merge llegan con las Fases 1+.
"""
import sys
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_CORE = _ROOT / "apps" / "forge_lab" / "core"

for _p in [str(_ROOT), str(_CORE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comfy_client import ComfyClient
from architectures import get_adapter, SUPPORTED_ARCHITECTURES

forge_router = APIRouter(prefix="/api/forge", tags=["forge"])

_comfy = ComfyClient(port=8188)

# Nodos de comfyUI-Realtime-Lora que la Fase 4 necesita disparar por API
_REQUIRED_NODES = [
    "ZImageSelectiveLoRALoader",
    "LoRALoaderWithAnalysis",
    "ScheduledLoRALoader",
]


def _models_root() -> Path:
    cfg = json.loads((_ROOT / "hub" / "hub_config.json").read_text(encoding="utf-8"))
    return Path(cfg["paths"]["models"])


@forge_router.get("/architectures")
async def architectures():
    out = []
    for name in SUPPORTED_ARCHITECTURES:
        a = get_adapter(name)
        out.append({
            "name": a.name,
            "label": a.label,
            "blocks": a.list_blocks(),
            "groups": [vars(g) for g in a.block_groups()],
            "forbidden_zones": a.forbidden_zones(),
            "sampling_defaults": vars(a.sampling_defaults()),
        })
    return {"architectures": out}


@forge_router.get("/status")
async def status(arch: str = "zimage"):
    """Diagnóstico Fase 0: ComfyUI vivo + modelos presentes + nodos requeridos."""
    try:
        adapter = get_adapter(arch)
    except ValueError as e:
        raise HTTPException(400, str(e))

    root = _models_root()
    files = vars(adapter.model_files())
    models = {k: {"path": v, "present": (root / v).exists()} for k, v in files.items()}

    comfy_up = await _comfy.health_check()
    nodes = {}
    if comfy_up:
        for n in _REQUIRED_NODES:
            nodes[n] = await _comfy.probe_node(n)

    return {
        "arch": adapter.name,
        "comfyui": comfy_up,
        "models": models,
        "nodes": nodes,
        "ready": comfy_up and all(m["present"] for m in models.values())
                 and all(nodes.values() or [False]),
    }
