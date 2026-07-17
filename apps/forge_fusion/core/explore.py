"""
Sesión de exploración por bloques (Fase 4) — el laboratorio de switches.

Flujo (visión del usuario, sesión 2026-07-04; calibración añadida 2026-07-16):
  0. CALIBRACIÓN (pre-lock): se puede generar libremente para afinar prompt y
     KSampler ANTES de fijar la sesión. Esas imágenes no entran al historial:
     viven en draft.png y cada prueba sobrescribe la anterior.
  1. Se fija checkpoint + LoRA + prompt + seed + sampling → sesión (lock).
  2. Primera generación = REFERENCIA (con la config de bloques que sea).
  3. Se cambian switches/dosis por bloque → generar → comparar contra la
     referencia. Iterar hasta dar con la config deseada.
  4. Config buena → confirmar regenerando el set de validación en runtime.
  5. Convencido → merge a fichero con esa misma config (misma matemática).

Los trabajos (generaciones) PERSISTEN en disco (data/explore_tmp/: session.json
+ <gen_id>.png) y sobreviven al cierre del hub/navegador. El borrado es siempre
MANUAL: papelera por trabajo (delete_generation), vaciar el historial
conservando la sesión (clear_generations) o descartar la sesión entera para
empezar otra config (clear_session). Antes eran efímeros y se autoborraban;
ahora el usuario decide. La config ganadora además acaba en el linaje del
checkpoint mergeado y/o en el label del run de confirmación.

Config de bloques (genérica, ids del adaptador):
  {"blocks": {"layers.0": dosis, ...}, "other": dosis}
  dosis 0 (o ausente) = bloque apagado; 1.0 = efecto completo; LINEAL.

Dosis lineal vs nodo: los nodos selectivos de Realtime-Lora multiplican
TODOS los tensores del bloque por su strength, así que el efecto sobre el
delta es strength² (lora_up×lora_down) o strength³ si el LoRA trae alpha
(kohya/SDXL). El exponente se detecta del header del LoRA al crear la sesión
y al nodo se le pasa dosis**(1/exponente); el merge usa la dosis tal cual
(strength × dosis). Así preview runtime ≡ checkpoint final. Verificado
empíricamente en zimage (test de equivalencia, 2026-07-04).
"""
import json
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
DRAFT_FILE = EXPLORE_DIR / "draft.png"
BASE_FILE = EXPLORE_DIR / "base.png"


class ExploreError(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ── Config de bloques ──────────────────────────────────────────────────────

def _switch_ids(arch: str) -> tuple[list[str], bool]:
    """(ids de bloque reales, ¿hay pseudo-bloque "other"?)."""
    sw = [s["id"] for s in get_adapter(arch).explore_switches()]
    has_other = "other" in sw
    return [s for s in sw if s != "other"], has_other


def normalize_config(config: dict, arch: str) -> dict:
    block_ids, has_other = _switch_ids(arch)
    raw = config.get("blocks") or {}
    unknown = set(raw) - set(block_ids)
    if unknown:
        raise ExploreError(f"bloques desconocidos para {arch!r}: "
                           f"{sorted(unknown)[:5]}")
    blocks = {}
    for b in block_ids:
        d = float(raw.get(b, 0.0))
        if d < 0:
            raise ExploreError(f"dosis negativa en {b} (no soportado: la "
                               "conversión a strength del nodo la impide)")
        blocks[b] = round(d, 4)
    other = float(config.get("other", 0.0)) if has_other else 0.0
    if other < 0:
        raise ExploreError("dosis negativa en 'other' (no soportado)")
    return {"blocks": blocks, "other": round(other, 4)}


def full_config(arch: str) -> dict:
    block_ids, has_other = _switch_ids(arch)
    return {"blocks": {b: 1.0 for b in block_ids},
            "other": 1.0 if has_other else 0.0}


def node_inputs(config: dict, arch: str, exponent: int) -> dict:
    """Inputs del nodo selectivo (nodo "10" del workflow) para una config
    normalizada, con la conversión de dosis lineal → strength del nodo."""
    return get_adapter(arch).selective_node_inputs(config, exponent)


def config_to_merge_blocks(config: dict, arch: str) -> dict[str, float]:
    """Config de exploración → parámetro `blocks` del merge worker."""
    blocks = get_adapter(arch).config_to_merge_blocks(config)
    if not blocks:
        raise ExploreError("config vacía: todos los bloques apagados")
    return blocks


def config_summary(config: dict, arch: str) -> str:
    """Resumen corto y legible ("28/30 ON · OFF: 7,9 · dosis: 20=0.5")."""
    switches = get_adapter(arch).explore_switches()
    labels = {s["id"]: s["label"] for s in switches}
    blocks = config["blocks"]
    off = [labels[b].split(" ")[0] for b in blocks if blocks[b] == 0]
    dosed = [f"{labels[b].split(' ')[0]}={d}"
             for b, d in blocks.items() if 0 < d != 1.0]
    parts = [f"{len(blocks) - len(off)}/{len(blocks)} ON"]
    if off:
        parts.append("OFF: " + ",".join(off))
    if dosed:
        parts.append("dosis: " + ",".join(dosed))
    if "other" in labels and config.get("other", 0.0) != 1.0:
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


def _validate_lora_coverage(hdr: dict, adapter):
    """Rechaza al crear la sesión los LoRAs que el nodo selectivo aplicaría a
    medias o NO aplicaría (fallo MUDO: devuelve el modelo sin tocar y la
    exploración genera siempre la misma imagen — bug reportado 2026-07-14 con
    un LoRA solo-TE en sdxl). Mismo contrato que el merge_worker: claves TE
    se saltan por política; cualquier módulo sin mapeo en la arquitectura
    aborta (la garantía preview runtime ≡ fichero no se puede sostener)."""
    from .lora_format import LoraFormatError, collect_pairs
    try:
        pairs, skipped = collect_pairs(
            [k for k in hdr if k != "__metadata__"],
            adapter.ignored_lora_prefixes)
    except LoraFormatError as e:
        raise ExploreError(f"LoRA no soportado: {e}")
    if not pairs:
        raise ExploreError(
            f"este LoRA solo entrena text encoders ({len(skipped)} módulos "
            f"TE): Forge Lab nunca aplica los TE (política preview ≡ merge), "
            "así que no habría nada que explorar ni mergear — elige otro LoRA")
    key_map = adapter.lora_key_map()
    unmapped = sorted(m for m in pairs if m not in key_map)
    if unmapped:
        raise ExploreError(
            f"{len(unmapped)}/{len(pairs)} módulos del LoRA no mapean al "
            f"UNet de {adapter.name!r} (p. ej. {unmapped[0]!r}): naming no "
            "soportado (¿diffusers/LoCon?) — el preview los descartaría en "
            "silencio y el merge abortaría, así que la sesión se rechaza")


def _build_session(arch: str, checkpoint: str, model: str, lora: str,
                   strength: float, prompt: str, negative: str, seed: int,
                   sampling: dict | None,
                   models_root: Path | None) -> dict:
    """Valida las entradas y arma el dict de sesión SIN tocar disco.
    Compartido entre el lock (create_session) y la calibración pre-lock
    (generate_draft): mismas validaciones → misma garantía de que el LoRA
    aplica de verdad (coverage, exponente de dosis)."""
    adapter = get_adapter(arch)
    if not str(prompt).strip():
        raise ExploreError("prompt vacío")
    if not 0.0 < float(strength) <= 2.0:
        raise ExploreError(f"strength {strength} fuera de rango (0–2]")

    exponent = 2
    lora_source = None
    if models_root is not None:
        from .merge import _read_st_header
        lora_path = Path(models_root) / "loras" / lora
        if not lora_path.is_file():
            raise ExploreError(f"no existe el LoRA {lora!r}")
        hdr, _ = _read_st_header(lora_path)
        if adapter.name == "sdxl":
            # naming diffusers → conversión al vuelo a kohya canónico; el
            # resto de la sesión (nodo selectivo Y merge) consume la copia
            from .lora_alias import LoraAliasError, ensure_alias, needs_alias
            if needs_alias(hdr):
                try:
                    lora_source, lora = lora, ensure_alias(
                        Path(models_root), lora)
                except LoraAliasError as e:
                    raise ExploreError(
                        f"LoRA con naming diffusers no convertible: {e}")
                lora_path = Path(models_root) / "loras" / lora
                hdr, _ = _read_st_header(lora_path)
        exponent = adapter.dose_exponent(
            lora_has_alpha=any(k.endswith(".alpha") for k in hdr))
        _validate_lora_coverage(hdr, adapter)

    return {
        "id": uuid.uuid4().hex[:8],
        "created_at": _now(),
        "arch": arch,
        "checkpoint": checkpoint,
        "model": model,
        "lora": lora,
        "lora_source": lora_source,     # original si `lora` es copia alias
        "strength": float(strength),
        "dose_exponent": exponent,
        "prompt": {"text": str(prompt).strip(),
                   "negative": str(negative or "").strip(),
                   "seed": int(seed)},
        "sampling": sampling or vars(adapter.sampling_defaults()),
        "reference": None,
        "generations": [],
    }


def create_session(arch: str, checkpoint: str, model: str, lora: str,
                   strength: float, prompt: str, negative: str, seed: int,
                   sampling: dict | None = None,
                   models_root: Path | None = None) -> dict:
    """Arranca una sesión nueva (borra la anterior, imágenes y draft de
    calibración incluidos).

    checkpoint: nombre en el registro (para el merge final).
    model: unet/ckpt_name que entiende ComfyUI. lora: ruta relativa posix.
    models_root: almacén global; si se da, se lee el header del LoRA para
    fijar el exponente de dosis (3 con alpha, 2 sin él)."""
    session = _build_session(arch, checkpoint, model, lora, strength, prompt,
                             negative, seed, sampling, models_root)
    clear_session()
    _save(session)
    return session


def get_draft() -> dict | None:
    """Última imagen de calibración pre-lock, si existe."""
    if DRAFT_FILE.is_file():
        return {"ts": int(DRAFT_FILE.stat().st_mtime)}
    return None


async def generate_draft(comfy, *, arch: str, checkpoint: str, model: str,
                         lora: str, strength: float, prompt: str,
                         negative: str = "", seed: int = 0,
                         sampling: dict | None = None,
                         models_root: Path | None = None,
                         on_progress: Callable[[int, int], None] | None = None
                         ) -> dict:
    """Calibración pre-lock: genera con las mismas validaciones y workflow que
    una sesión (LoRA a dosis 1.0 en todos los bloques) pero SIN sesión y SIN
    persistir en el historial — la imagen sobrescribe draft.png en cada
    prueba. Sirve para afinar prompt y KSampler antes del lock."""
    session = _build_session(arch, checkpoint, model, lora, strength, prompt,
                             negative, seed, sampling, models_root)
    png, _, secs = await _render(session, full_config(arch),
                                 session["prompt"], comfy, on_progress)
    EXPLORE_DIR.mkdir(parents=True, exist_ok=True)
    DRAFT_FILE.write_bytes(png)
    return {"seconds": secs, "ts": int(DRAFT_FILE.stat().st_mtime)}


def delete_generation(gen_id: str) -> dict:
    """Borra un trabajo (entry + imagen). Si era la referencia, la reasigna al
    último que quede (o None)."""
    session = get_session()
    if not session:
        raise ExploreError("no hay sesión de exploración activa")
    gens = session["generations"]
    idx = next((i for i, g in enumerate(gens) if g["id"] == gen_id), None)
    if idx is None:
        raise ExploreError(f"no existe la generación {gen_id!r}")
    gens.pop(idx)
    img = EXPLORE_DIR / f"{gen_id}.png"
    if img.exists():
        img.unlink()
    if session["reference"] == gen_id:
        session["reference"] = gens[-1]["id"] if gens else None
    _save(session)
    return session


def clear_generations() -> dict:
    """Vacía el historial de trabajos (imágenes incluidas) CONSERVANDO la
    sesión y su config (misma base/LoRA/prompt para seguir generando)."""
    session = get_session()
    if not session:
        raise ExploreError("no hay sesión de exploración activa")
    for g in session["generations"]:
        img = EXPLORE_DIR / f"{g['id']}.png"
        if img.exists():
            img.unlink()
    session["generations"] = []
    session["reference"] = None
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


def base_image_path() -> Path:
    """Imagen del checkpoint SIN LoRA (misma sesión: checkpoint+prompt+
    sampling+seed), usada como comparación en el slider de la referencia.
    Generada perezosamente por generate() en la primera generación."""
    if not BASE_FILE.is_file():
        raise ExploreError("no hay imagen base (sin LoRA) generada aún")
    return BASE_FILE


async def _render(session: dict, config: dict, prompt: dict,
                  comfy,
                  on_progress: Callable[[int, int], None] | None = None
                  ) -> tuple[bytes, dict, float]:
    """Corre el workflow selectivo (nodo "10") con la config de bloques dada y
    un prompt dado. Devuelve (png, config_normalizada, segundos). No toca la
    sesión en disco: sirve tanto a la exploración (prompt de sesión) como a la
    batería (config fija × N prompts)."""
    arch = session["arch"]
    adapter = get_adapter(arch)
    config = normalize_config(config, arch)

    template = load_workflow(adapter.workflow_name("txt2img_lora_selective"),
                             arch)
    exponent = session.get("dose_exponent", 2)
    template["10"]["inputs"].update(node_inputs(config, arch, exponent))
    from .model_config import model_files, loader_name
    files = model_files(arch)
    params = {
        "model": session["model"],
        "lora": session["lora"].replace("/", os.sep),
        "lora_strength": session["strength"],
        "prompt": prompt["text"],
        "negative": prompt.get("negative", ""),
        "seed": int(prompt["seed"]),
        **session["sampling"],
    }
    # layouts por piezas cargan TE/VAE aparte; el checkpoint completo no
    for k in ("text_encoder", "vae"):
        if files.get(k):
            params[k] = loader_name(files[k])
    t0 = time.time()
    png = await comfy.run_workflow(template, params, on_progress=on_progress)
    return png, config, round(time.time() - t0, 1)


def _append_generation(gen: dict, png: bytes) -> dict:
    """Escribe la imagen y añade el trabajo a la sesión (releyéndola por si
    cambió durante la generación). La primera queda como referencia."""
    EXPLORE_DIR.mkdir(parents=True, exist_ok=True)
    (EXPLORE_DIR / f"{gen['id']}.png").write_bytes(png)
    session = get_session()
    if not session:
        raise ExploreError("la sesión se cerró durante la generación")
    session["generations"].append(gen)
    if session["reference"] is None:
        session["reference"] = gen["id"]
    _save(session)
    return session


async def generate(comfy, config: dict,
                   on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Genera una variante con la config dada (prompt fijo de la sesión) y la
    añade al historial. La primera generación queda como referencia.

    Además, si todavía no existe base.png (checkpoint puro, sin LoRA — misma
    sesión: prompt/sampling/seed), la genera de paso una única vez para que
    el frontend pueda comparar la referencia contra "sin LoRA" en un slider.
    Se reutiliza para el resto de la sesión (no depende de la config de
    bloques)."""
    session = get_session()
    if not session:
        raise ExploreError("no hay sesión de exploración activa")
    arch = session["arch"]
    steps = session["sampling"].get("steps", 1) or 1
    need_base = not BASE_FILE.is_file()
    total = steps * 2 if need_base else steps

    if need_base:
        def base_progress(step, _total):
            if on_progress:
                on_progress(step, total)
        base_png, _, _ = await _render(
            session, {"blocks": {}, "other": 0.0}, session["prompt"],
            comfy, base_progress)
        EXPLORE_DIR.mkdir(parents=True, exist_ok=True)
        BASE_FILE.write_bytes(base_png)

    def main_progress(step, _total):
        if on_progress:
            on_progress((steps if need_base else 0) + step, total)
    png, config, secs = await _render(session, config, session["prompt"],
                                      comfy, main_progress)
    gen = {"id": uuid.uuid4().hex[:8], "config": config,
           "summary": config_summary(config, arch),
           "seconds": secs, "created_at": _now()}
    _append_generation(gen, png)
    return gen


async def generate_battery_item(comfy, config: dict, prompt: dict,
                                battery: str, battery_label: str = "",
                                on_progress: Callable[[int, int], None] | None = None
                                ) -> dict:
    """Genera un ítem de batería: MISMA config de bloques, prompt de la batería.
    El trabajo queda marcado (`battery`, `prompt_id`, `prompt_text`) para que el
    historial lo muestre por prompt y no por config."""
    session = get_session()
    if not session:
        raise ExploreError("no hay sesión de exploración activa")
    arch = session["arch"]
    png, config, secs = await _render(session, config, prompt,
                                      comfy, on_progress)
    label = prompt.get("id") or prompt["text"][:32]
    gen = {"id": uuid.uuid4().hex[:8], "config": config,
           "summary": f"🔋 {label}",
           "battery": battery, "battery_label": battery_label,
           "prompt_id": prompt.get("id", ""),
           "prompt_text": prompt["text"], "seed": int(prompt["seed"]),
           "seconds": secs, "created_at": _now()}
    _append_generation(gen, png)
    return gen
