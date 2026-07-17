"""
Batería de prompts — validación ligera de una config de bloques (Fase 4).

A diferencia de los sets de validación (`validation_set.py`, vara de medir con
lock/fingerprint), la batería es LIGERA: sin lock, sin comparación formal. Su
único fin es el "uso 1" del usuario: cuando crees que ya tienes una config de
bloques definitiva, correr de golpe VARIOS prompts contra ESA misma config
(config fija × N prompts) para ver que aguanta más allá del prompt con el que
la afinaste. Los resultados caen al historial del workspace como trabajos
normales, marcados por prompt.

Se sirven 6 plantillas de arranque = 3 tipos de LoRA × 2 estilos de prompt:

  · Personaje / Estilo / Concepto   (qué estresa la batería)
  · prosa  → Z-Image   (frases descriptivas naturales)
    tags   → SDXL      (etiquetas booru/danbooru separadas por comas)

Son DEFAULTS editables. Al editar/guardar una plantilla se escribe un custom en
`data/prompt_batteries/<id>.json` que la sombrea; `reset` borra el custom y
recupera el default. También se pueden crear baterías nuevas desde cero.

Estilo por arch: la afinidad prosa↔zimage / tags↔sdxl es una SUGERENCIA de la
UI (ordena por `style`), no una restricción — cualquier batería corre en la
sesión activa, sea cual sea su arquitectura.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

BATTERY_DIR = Path(__file__).parent.parent / "data" / "prompt_batteries"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

LORA_TYPES = ["character", "style", "concept"]
STYLES = ["prosa", "tags"]


class BatteryError(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ── Plantillas de arranque (defaults, editables) ────────────────────────────
#
# El LoRA a probar ya va montado en la sesión (nodo selectivo), así que los
# prompts NO necesitan trigger words salvo para "concepto", donde el marcador
# CONCEPT se reemplaza por la palabra de activación del LoRA. En personaje/
# estilo, añade tu trigger si tu LoRA lo requiere.

def _p(pid, text, seed, negative=""):
    return {"id": pid, "text": text, "negative": negative, "seed": seed}


_DEFAULTS: dict[str, dict] = {
    # ── PERSONAJE ──────────────────────────────────────────────────────────
    "character-prosa": {
        "id": "character-prosa", "label": "Personaje · prosa (Z-Image)",
        "lora_type": "character", "style": "prosa",
        "hint": "Estresa la identidad del personaje en vistas, poses y "
                "entornos distintos. Añade tu trigger si el LoRA lo pide.",
        "prompts": [
            _p("retrato", "close-up portrait, facing the camera, neutral "
               "expression, soft even lighting, plain background, sharp focus",
               110011),
            _p("cuerpo-completo", "full body shot standing in a relaxed pose, "
               "casual clothing, studio lighting, plain background", 120012),
            _p("sonrisa", "upper body, warm genuine smile, looking to the "
               "side, gentle window light", 130013),
            _p("perfil", "side profile view, calm expression, rim lighting "
               "against a dark background", 140014),
            _p("accion", "dynamic action pose mid-movement, hair and clothes "
               "in motion, dramatic lighting", 150015),
            _p("exterior", "walking down a busy city street at golden hour, "
               "full body, natural sunlight, shallow depth of field", 160016),
        ],
    },
    "character-tags": {
        "id": "character-tags", "label": "Personaje · tags (SDXL/Anima)",
        "lora_type": "character", "style": "tags",
        "hint": "Etiquetas booru. Ajusta 1girl/1boy y añade el trigger del "
                "LoRA si lo requiere.",
        "prompts": [
            _p("retrato", "1girl, solo, portrait, looking at viewer, neutral "
               "expression, simple background, upper body", 110011,
               "lowres, bad anatomy, bad hands, blurry"),
            _p("cuerpo-completo", "1girl, solo, full body, standing, casual "
               "clothes, simple background", 120012,
               "lowres, bad anatomy, bad hands, blurry"),
            _p("sonrisa", "1girl, solo, upper body, smile, looking to the "
               "side, soft lighting", 130013,
               "lowres, bad anatomy, bad hands, blurry"),
            _p("perfil", "1girl, solo, profile, from side, serious, backlighting",
               140014, "lowres, bad anatomy, bad hands, blurry"),
            _p("accion", "1girl, solo, dynamic pose, action, motion blur, "
               "floating hair", 150015,
               "lowres, bad anatomy, bad hands, blurry"),
            _p("exterior", "1girl, solo, full body, city street, outdoors, "
               "sunset, depth of field", 160016,
               "lowres, bad anatomy, bad hands, blurry"),
        ],
    },
    # ── ESTILO ─────────────────────────────────────────────────────────────
    "style-prosa": {
        "id": "style-prosa", "label": "Estilo · prosa (Z-Image)",
        "lora_type": "style", "style": "prosa",
        "hint": "Aplica el estilo sobre contenidos muy distintos para ver si "
                "se mantiene coherente sin deformar la anatomía ni la escena.",
        "prompts": [
            _p("retrato", "a portrait of a young woman with freckles, calm "
               "expression, looking at the viewer", 210021),
            _p("paisaje", "a wide mountain landscape at dawn, a river winding "
               "through pine forest, mist over the valley", 220022),
            _p("animal", "a red fox sitting in a snowy clearing, alert ears, "
               "detailed fur", 230023),
            _p("bodegon", "a still life of fruit and a ceramic jug on a wooden "
               "table near a window", 240024),
            _p("ciudad", "a rainy city street at night, neon signs reflected "
               "on wet asphalt, a lone pedestrian with an umbrella", 250025),
            _p("fantasia", "a floating castle above the clouds, waterfalls "
               "spilling off its edges, distant flying creatures", 260026),
        ],
    },
    "style-tags": {
        "id": "style-tags", "label": "Estilo · tags (SDXL/Anima)",
        "lora_type": "style", "style": "tags",
        "hint": "Contenidos variados en etiquetas booru para juzgar el estilo, "
                "no un personaje concreto.",
        "prompts": [
            _p("retrato", "1girl, solo, portrait, freckles, looking at viewer, "
               "simple background", 210021,
               "lowres, bad anatomy, bad hands, blurry"),
            _p("paisaje", "scenery, landscape, mountains, forest, river, mist, "
               "no humans, dawn", 220022, "lowres, blurry, jpeg artifacts"),
            _p("animal", "no humans, animal focus, fox, snow, detailed fur, "
               "outdoors", 230023, "lowres, blurry, jpeg artifacts"),
            _p("bodegon", "no humans, still life, fruit, jug, wooden table, "
               "window light", 240024, "lowres, blurry, jpeg artifacts"),
            _p("ciudad", "no humans, cityscape, night, neon lights, rain, wet "
               "street, reflection", 250025, "lowres, blurry, jpeg artifacts"),
            _p("fantasia", "no humans, floating castle, clouds, waterfall, "
               "fantasy, scenery", 260026, "lowres, blurry, jpeg artifacts"),
        ],
    },
    # ── CONCEPTO ───────────────────────────────────────────────────────────
    "concept-prosa": {
        "id": "concept-prosa", "label": "Concepto · prosa (Z-Image)",
        "lora_type": "concept", "style": "prosa",
        "hint": "Reemplaza CONCEPT por la palabra de activación de tu LoRA. "
                "Invoca el concepto en contextos y sujetos distintos.",
        "prompts": [
            _p("mujer", "a woman featuring CONCEPT, upper body, studio "
               "lighting, plain background", 310031),
            _p("hombre", "a man featuring CONCEPT, upper body, soft daylight, "
               "plain background", 320032),
            _p("detalle", "extreme close-up detail of CONCEPT, sharp focus, "
               "high detail", 330033),
            _p("escena", "a wide scene prominently showing CONCEPT, "
               "environmental context, natural lighting", 340034),
            _p("noche", "CONCEPT at night, dramatic low-key lighting, cool "
               "tones", 350035),
            _p("multiple", "several instances of CONCEPT arranged together, "
               "clear separation, even lighting", 360036),
        ],
    },
    "concept-tags": {
        "id": "concept-tags", "label": "Concepto · tags (SDXL/Anima)",
        "lora_type": "concept", "style": "tags",
        "hint": "Reemplaza CONCEPT por el trigger del LoRA. Etiquetas booru.",
        "prompts": [
            _p("mujer", "CONCEPT, 1girl, solo, upper body, simple background",
               310031, "lowres, bad anatomy, bad hands, blurry"),
            _p("hombre", "CONCEPT, 1boy, solo, upper body, simple background",
               320032, "lowres, bad anatomy, bad hands, blurry"),
            _p("detalle", "CONCEPT, close-up, detailed, simple background",
               330033, "lowres, blurry, jpeg artifacts"),
            _p("escena", "CONCEPT, scenery, wide shot, detailed background",
               340034, "lowres, blurry, jpeg artifacts"),
            _p("noche", "CONCEPT, night, dark, dramatic lighting", 350035,
               "lowres, blurry, jpeg artifacts"),
            _p("multiple", "CONCEPT, multiple, simple background, even lighting",
               360036, "lowres, blurry, jpeg artifacts"),
        ],
    },
}


# ── Validación ──────────────────────────────────────────────────────────────

def _validate_prompts(prompts: list[dict]) -> list[dict]:
    if not prompts:
        raise BatteryError("la batería no tiene prompts")
    clean, seen = [], set()
    for i, p in enumerate(prompts):
        pid = str(p.get("id", "")).strip()
        if not _SLUG_RE.match(pid):
            raise BatteryError(
                f"prompt[{i}]: id inválido {pid!r} (minúsculas/dígitos/guiones)")
        if pid in seen:
            raise BatteryError(f"prompt id duplicado: {pid!r}")
        seen.add(pid)
        text = str(p.get("text", "")).strip()
        if not text:
            raise BatteryError(f"prompt {pid!r}: texto vacío")
        try:
            seed = int(p.get("seed"))
        except (TypeError, ValueError):
            raise BatteryError(f"prompt {pid!r}: seed debe ser entero")
        clean.append({"id": pid, "text": text,
                      "negative": str(p.get("negative", "")).strip(),
                      "seed": seed})
    return clean


def _validate_battery(d: dict) -> dict:
    bid = str(d.get("id", "")).strip()
    if not _SLUG_RE.match(bid):
        raise BatteryError(f"id de batería inválido {bid!r}")
    lt = d.get("lora_type")
    if lt not in LORA_TYPES:
        raise BatteryError(f"lora_type {lt!r} no es uno de {LORA_TYPES}")
    st = d.get("style")
    if st not in STYLES:
        raise BatteryError(f"style {st!r} no es uno de {STYLES}")
    return {"id": bid, "label": str(d.get("label", bid)).strip() or bid,
            "lora_type": lt, "style": st,
            "hint": str(d.get("hint", "")).strip(),
            "prompts": _validate_prompts(d.get("prompts", []))}


# ── Persistencia (custom sombrea al default) ────────────────────────────────

def _custom_path(bid: str) -> Path:
    return BATTERY_DIR / f"{bid}.json"


def _custom_ids() -> set[str]:
    if not BATTERY_DIR.exists():
        return set()
    return {f.stem for f in BATTERY_DIR.glob("*.json")}


def get_battery(bid: str) -> dict:
    """Devuelve la batería (custom si existe, si no el default)."""
    path = _custom_path(bid)
    if path.exists():
        d = _validate_battery(json.loads(path.read_text(encoding="utf-8")))
        d["is_custom"] = True
        d["is_default"] = bid in _DEFAULTS
        return d
    if bid in _DEFAULTS:
        d = _validate_battery(dict(_DEFAULTS[bid]))
        d["is_custom"] = False
        d["is_default"] = True
        return d
    raise BatteryError(f"no existe la batería {bid!r}")


def list_batteries() -> list[dict]:
    """Resúmenes de todas las baterías (defaults + customs), ordenadas por
    tipo de LoRA y luego estilo. `modified` marca los defaults con override."""
    ids = set(_DEFAULTS) | _custom_ids()
    out = []
    for bid in ids:
        b = get_battery(bid)
        out.append({"id": b["id"], "label": b["label"],
                    "lora_type": b["lora_type"], "style": b["style"],
                    "hint": b["hint"], "n_prompts": len(b["prompts"]),
                    "is_default": b["is_default"],
                    "modified": b["is_custom"] and b["is_default"]})
    out.sort(key=lambda b: (LORA_TYPES.index(b["lora_type"])
                            if b["lora_type"] in LORA_TYPES else 9,
                            STYLES.index(b["style"]) if b["style"] in STYLES
                            else 9, b["id"]))
    return out


def save_battery(d: dict) -> dict:
    """Crea o sobrescribe una batería (escribe un custom en disco)."""
    b = _validate_battery(d)
    BATTERY_DIR.mkdir(parents=True, exist_ok=True)
    b["updated_at"] = _now()
    _custom_path(b["id"]).write_text(
        json.dumps(b, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return get_battery(b["id"])


def delete_battery(bid: str):
    """Borra la batería. En un default con override, recupera el default; en
    uno creado desde cero, lo elimina. Un default sin override no se borra."""
    path = _custom_path(bid)
    if path.exists():
        path.unlink()
        return
    if bid in _DEFAULTS:
        raise BatteryError(f"{bid!r} es una plantilla de fábrica sin cambios; "
                           "no hay nada que borrar")
    raise BatteryError(f"no existe la batería {bid!r}")


# ── Ejecución (config fija × N prompts) ─────────────────────────────────────

async def run_battery(comfy, battery_id: str, config: dict,
                      on_progress: Callable[[dict], None] | None = None
                      ) -> dict:
    """Corre todos los prompts de la batería contra la MISMA config de bloques,
    dentro de la sesión de exploración activa. Secuencial (la 5060 Ti no da
    para más). Cada ítem cae al historial marcado como trabajo de batería.

    on_progress recibe {item_index, total, prompt_id, step, steps_total}.
    Devuelve {battery, label, items:[gen_id...]}.
    """
    from . import explore

    session = explore.get_session()
    if not session:
        raise explore.ExploreError("no hay sesión de exploración activa")
    bat = get_battery(battery_id)
    prompts = bat["prompts"]
    total = len(prompts)
    items = []
    for i, p in enumerate(prompts):
        def _cb(step, steps_total, _i=i, _pid=p["id"]):
            if on_progress:
                on_progress({"item_index": _i, "total": total,
                             "prompt_id": _pid, "step": step,
                             "steps_total": steps_total})
        _cb(0, session["sampling"].get("steps", 0))
        gen = await explore.generate_battery_item(
            comfy, config, p, battery=bat["id"],
            battery_label=bat["label"], on_progress=_cb)
        items.append(gen["id"])
    return {"battery": bat["id"], "label": bat["label"], "items": items}
