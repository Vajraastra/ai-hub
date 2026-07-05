"""
Sesión de exploración por bloques (Fase 4) — el laboratorio de switches.

Flujo (visión del usuario, sesión 2026-07-04):
  1. Se fija checkpoint + LoRA + prompt + seed + sampling → sesión.
  2. Primera generación = REFERENCIA (con la config de bloques que sea).
  3. Se cambian switches/dosis por bloque → generar → comparar contra la
     referencia. Iterar hasta dar con la config deseada.
  4. Config buena → confirmar regenerando el set de validación en runtime.
  5. Convencido → merge a fichero con esa misma config (misma matemática).

Las imágenes de exploración son TEMPORALES a propósito (no acumular): viven
en data/explore_tmp/ y se borran al crear otra sesión o cerrarla. Lo único
que merece persistir de una exploración es la config ganadora, y esa acaba
en el linaje del checkpoint mergeado y/o en el label del run de confirmación.

Dosis lineal vs nodo: ZImageSelectiveLoRALoader multiplica lora_A Y lora_B
por layer_N_str, así que su efecto sobre el delta (B@A) es CUADRÁTICO. Aquí
la dosis del usuario es lineal (0.5 = 50% del delta) y al nodo se le pasa
sqrt(dosis); el merge usa la dosis tal cual (strength × dosis). Así preview
runtime ≡ checkpoint final. Verificado empíricamente (test de equivalencia).
"""
import json
import math
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .architectures import get_adapter
from .comfy_client import load_workflow

EXPLORE_DIR = Path(__file__).parent.parent / "data" / "explore_tmp"
_SESSION_FILE = EXPLORE_DIR / "session.json"

N_LAYERS = 30


class ExploreError(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ── Config de bloques ──────────────────────────────────────────────────────
# Formato: {"layers": {"0": dosis, ..., "29": dosis}, "other": dosis}
# dosis 0 (o ausente) = bloque apagado; 1.0 = efecto completo; lineal.

def normalize_config(config: dict) -> dict:
    layers = {}
    for i in range(N_LAYERS):
        d = float((config.get("layers") or {}).get(str(i), 0.0))
        if d < 0:
            raise ExploreError(f"dosis negativa en layers.{i} (no soportado: "
                               "la conversión sqrt al nodo la impide)")
        layers[str(i)] = round(d, 4)
    other = float(config.get("other", 0.0))
    if other < 0:
        raise ExploreError("dosis negativa en 'other' (no soportado)")
    return {"layers": layers, "other": round(other, 4)}


def full_config() -> dict:
    return {"layers": {str(i): 1.0 for i in range(N_LAYERS)}, "other": 1.0}


def node_inputs(config: dict) -> dict:
    """Inputs del ZImageSelectiveLoRALoader para una config (dosis lineal →
    sqrt para compensar el escalado cuadrático del nodo)."""
    out = {}
    for i in range(N_LAYERS):
        d = config["layers"][str(i)]
        out[f"layer_{i}"] = d > 0
        out[f"layer_{i}_str"] = round(math.sqrt(d), 6) if d > 0 else 1.0
    out["other_weights"] = config["other"] > 0
    out["other_weights_str"] = (round(math.sqrt(config["other"]), 6)
                                if config["other"] > 0 else 1.0)
    return out


def config_to_merge_blocks(config: dict) -> dict[str, float]:
    """Config de exploración → parámetro `blocks` del merge worker."""
    blocks = {f"layers.{i}": d
              for i, d in ((int(k), v) for k, v in config["layers"].items())
              if d > 0}
    if config["other"] > 0:
        blocks["noise_refiner"] = config["other"]
        blocks["context_refiner"] = config["other"]
    if not blocks:
        raise ExploreError("config vacía: todos los bloques apagados")
    return dict(sorted(blocks.items()))


def config_summary(config: dict) -> str:
    """Resumen corto y legible ("28 ON, 2 OFF (7,9), dosis: 20=0.5")."""
    off = [k for k, d in config["layers"].items() if d == 0]
    dosed = [f"{k}={d}" for k, d in config["layers"].items() if 0 < d != 1.0]
    parts = [f"{N_LAYERS - len(off)}/{N_LAYERS} ON"]
    if off:
        parts.append("OFF: " + ",".join(sorted(off, key=int)))
    if dosed:
        parts.append("dosis: " + ",".join(dosed))
    if config["other"] != 1.0:
        parts.append(f"other={config['other']}")
    return " · ".join(parts)


# ── Sesión (una sola, en disco para sobrevivir reloads de página) ──────────

def get_session() -> dict | None:
    if _SESSION_FILE.exists():
        return json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
    return None


def _save(session: dict):
    EXPLORE_DIR.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(
        json.dumps(session, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def clear_session():
    """Cierra la sesión y borra TODAS sus imágenes (regla: no acumular)."""
    if EXPLORE_DIR.exists():
        shutil.rmtree(EXPLORE_DIR)


def create_session(arch: str, checkpoint: str, model: str, lora: str,
                   strength: float, prompt: str, negative: str, seed: int,
                   sampling: dict | None = None) -> dict:
    """Arranca una sesión nueva (borra la anterior, imágenes incluidas).

    checkpoint: nombre en el registro (para el merge final).
    model: unet_name que entiende ComfyUI. lora: ruta relativa posix."""
    adapter = get_adapter(arch)
    if not str(prompt).strip():
        raise ExploreError("prompt vacío")
    if not 0.0 < float(strength) <= 2.0:
        raise ExploreError(f"strength {strength} fuera de rango (0–2]")
    clear_session()
    session = {
        "id": uuid.uuid4().hex[:8],
        "created_at": _now(),
        "arch": arch,
        "checkpoint": checkpoint,
        "model": model,
        "lora": lora,
        "strength": float(strength),
        "prompt": {"text": str(prompt).strip(),
                   "negative": str(negative or "").strip(),
                   "seed": int(seed)},
        "sampling": sampling or vars(adapter.sampling_defaults()),
        "reference": None,
        "generations": [],
    }
    _save(session)
    return session


def set_reference(gen_id: str) -> dict:
    session = get_session()
    if not session:
        raise ExploreError("no hay sesión de exploración activa")
    if not any(g["id"] == gen_id for g in session["generations"]):
        raise ExploreError(f"no existe la generación {gen_id!r}")
    session["reference"] = gen_id
    _save(session)
    return session


def image_path(gen_id: str) -> Path:
    base = EXPLORE_DIR.resolve()
    p = (base / f"{gen_id}.png").resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise ExploreError(f"no existe la imagen {gen_id!r}")
    return p


async def generate(comfy, config: dict,
                   on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Genera una variante con la config dada y la añade a la sesión.
    La primera generación queda como referencia automáticamente."""
    session = get_session()
    if not session:
        raise ExploreError("no hay sesión de exploración activa")
    config = normalize_config(config)

    template = load_workflow("txt2img_lora_selective.json", session["arch"])
    template["10"]["inputs"].update(node_inputs(config))
    from .model_config import model_files, loader_name
    files = model_files(session["arch"])
    params = {
        "model": session["model"],
        "lora": session["lora"].replace("/", os.sep),
        "lora_strength": session["strength"],
        "prompt": session["prompt"]["text"],
        "negative": session["prompt"]["negative"],
        "seed": session["prompt"]["seed"],
        "text_encoder": loader_name(files["text_encoder"]),
        "vae": loader_name(files["vae"]),
        **session["sampling"],
    }
    t0 = time.time()
    png = await comfy.run_workflow(template, params, on_progress=on_progress)

    gen = {"id": uuid.uuid4().hex[:8], "config": config,
           "summary": config_summary(config),
           "seconds": round(time.time() - t0, 1), "created_at": _now()}
    EXPLORE_DIR.mkdir(parents=True, exist_ok=True)
    (EXPLORE_DIR / f"{gen['id']}.png").write_bytes(png)

    # releer por si algo cambió durante la generación (p. ej. referencia)
    session = get_session()
    if not session:
        raise ExploreError("la sesión se cerró durante la generación")
    session["generations"].append(gen)
    if session["reference"] is None:
        session["reference"] = gen["id"]
    _save(session)
    return gen
