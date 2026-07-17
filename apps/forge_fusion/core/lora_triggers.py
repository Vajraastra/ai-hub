"""Trigger words de un LoRA — extracción barata, sin cargar el modelo.

Fuentes, por prioridad:
1. sidecar ``<stem>.metadata.json`` → ``civitai.trainedWords`` (lo que el
   autor declaró en Civitai; lo descarga el Lora Manager junto al fichero)
2. sidecar ``<stem>.cm-info.json`` → ``TrainedWords`` (formato alternativo
   del mismo manager)
3. header del safetensors → ``modelspec.trigger_phrase`` o
   ``ss_tag_frequency`` (kohya guarda la frecuencia de cada tag del dataset:
   el trigger suele ser el más frecuente; se filtran tags genéricos)

Los entries tipo lista de tags se separan por comas; las frases de prosa
(Z-Image y compañía) se conservan enteras — heurística: solo se parte un
entry si TODAS sus sub-partes tienen ≤4 palabras (una lista de tags), nunca
una oración. Cache en memoria invalidada por mtime del LoRA y sus sidecars.
"""
from __future__ import annotations

import json
import struct
from collections import Counter
from pathlib import Path


class TriggerError(Exception):
    pass


MAX_WORDS = 16          # tope de chips que devolvemos a la UI
MAX_HEADER_TAGS = 8     # top-N de ss_tag_frequency (fuente dataset)

# tags de dataset demasiado genéricos para ser trigger; si el filtrado se lo
# come todo, se devuelven los originales (mejor genéricos que nada)
_GENERIC = {
    "1girl", "1boy", "2girls", "2boys", "multiple girls", "multiple boys",
    "solo", "solo focus", "male focus", "female focus",
    "masterpiece", "best quality", "high quality", "very awa", "newest",
    "absurdres", "highres", "lowres",
    "score_9", "score_8_up", "score_7_up", "score_6_up",
    "source_anime", "source_furry", "source_pony", "source_cartoon",
    "looking at viewer", "simple background", "white background",
}


def _read_header_meta(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n > 100_000_000:
                raise TriggerError(f"header desmesurado en {path.name!r}")
            hdr = json.loads(f.read(n))
    except (OSError, struct.error, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise TriggerError(f"no pude leer el header de {path.name!r}: {e}")
    meta = hdr.get("__metadata__")
    return meta if isinstance(meta, dict) else {}


def _is_tag_list(entry: str) -> bool:
    """True si el entry es una lista de tags separada por comas (todas las
    partes cortas), False si es prosa con comas incidentales."""
    parts = [p.strip() for p in entry.split(",") if p.strip()]
    return len(parts) > 1 and all(len(p.split()) <= 4 for p in parts)


def _split_entries(entries) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for e in entries or []:
        e = str(e or "").strip().strip(",").strip()
        if not e:
            continue
        parts = ([p.strip() for p in e.split(",") if p.strip()]
                 if _is_tag_list(e) else [e])
        for p in parts:
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
    return out


def _drop_generic(words: list[str]) -> list[str]:
    kept = [w for w in words if w.lower() not in _GENERIC]
    return kept or words


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _sidecar_civitai(p: Path):
    data = _load_json(p.with_name(p.stem + ".metadata.json"))
    if isinstance(data, dict):
        civ = data.get("civitai")
        if isinstance(civ, dict):
            return civ.get("trainedWords")
    return None


def _sidecar_cminfo(p: Path):
    data = _load_json(p.with_name(p.stem + ".cm-info.json"))
    if isinstance(data, dict):
        return data.get("TrainedWords")
    return None


def _header_words(p: Path):
    meta = _read_header_meta(p)
    phrase = meta.get("modelspec.trigger_phrase") or meta.get("trigger_phrase")
    if phrase:
        return [phrase]
    raw = meta.get("ss_tag_frequency")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    freq: Counter = Counter()
    for tags in data.values():
        if not isinstance(tags, dict):
            continue
        for t, f in tags.items():
            t = str(t).strip()
            if t and t.lower() not in _GENERIC:
                freq[t] += int(f) if isinstance(f, (int, float)) else 1
    return [t for t, _ in freq.most_common(MAX_HEADER_TAGS)] or None


# (rel_posix) → (stamp, resultado); los sidecars casi nunca cambian, pero el
# stamp incluye sus mtimes para invalidar si el usuario los regenera
_cache: dict[str, tuple[tuple, dict]] = {}
_CACHE_MAX = 512


def _stamp(p: Path) -> tuple:
    parts = [p.stat().st_mtime_ns]
    for suffix in (".metadata.json", ".cm-info.json"):
        side = p.with_name(p.stem + suffix)
        parts.append(side.stat().st_mtime_ns if side.exists() else 0)
    return tuple(parts)


def triggers_for(models_root: Path, lora_rel: str) -> dict:
    """{"file", "source": civitai|lora-manager|dataset|None, "words": [...]}"""
    base = (models_root / "loras").resolve()
    p = (base / lora_rel).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise TriggerError(f"no existe el LoRA {lora_rel!r}")

    stamp = _stamp(p)
    cached = _cache.get(lora_rel)
    if cached and cached[0] == stamp:
        return cached[1]

    source, words = None, []
    for src, getter in (("civitai", _sidecar_civitai),
                        ("lora-manager", _sidecar_cminfo),
                        ("dataset", _header_words)):
        got = _split_entries(getter(p))
        if got:
            source, words = src, _drop_generic(got)[:MAX_WORDS]
            break

    result = {"file": lora_rel, "source": source, "words": words}
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[lora_rel] = (stamp, result)
    return result
