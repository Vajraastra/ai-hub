"""
Catálogo de LoRAs para Ideogram 4: escaneo del almacén global de modelos con
detección de arquitectura por el header del safetensors (nunca carga pesos).

Independiente de forge_lab (módulos desacoplados, un fallo no cascada): se
replica aquí el lector de header mínimo. Un LoRA es de Ideogram 4 si su
metadata declara base_model ideogram4 o si sus tensores tienen la firma del
transformer de Ideogram (qkv fusionado + adaln_modulation + feed_forward.w1/w2/w3
sobre diffusion_model.layers), que lo distingue de zimage (to_q/to_k, dim 3840),
flux (lora_unet_*) y SDXL (lora_te1/lora_unet_*).
"""
import json
import struct
from pathlib import Path

_PREVIEW_EXT = (".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp")

# cache del header por (mtime_ns, size): leer 1400+ headers cuesta segundos; solo
# se paga el primer escaneo y los ficheros nuevos.
_cache: dict[str, tuple[tuple, dict]] = {}


def _read_header(path: Path) -> tuple[dict, dict]:
    """(tensores, metadata) del header de un .safetensors. Solo el header."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n <= 0 or n > 100 * 1024 * 1024:
            raise ValueError(f"header sospechoso ({n} bytes)")
        hdr = json.loads(f.read(n))
    meta = hdr.pop("__metadata__", {}) or {}
    return hdr, meta


def _find_preview(p: Path) -> Path | None:
    """Imagen de preview junto al safetensors (convención del Model Vault)."""
    base = str(p)[: -len(p.suffix)] if p.suffix else str(p)
    for ext in _PREVIEW_EXT:
        c = Path(base + ext)
        if c.is_file():
            return c
    return None


# Dimensión interna del transformer de Ideogram 4 (entrada de qkv/o). zimage
# usa 3840, así que sirve para discriminar loras sin metadata fiable.
_IDEOGRAM_DIM = 4608


def is_ideogram(hdr: dict, meta: dict) -> bool:
    """True si el LoRA es de Ideogram 4. Señal primaria: metadata base_model.
    Señal secundaria (loras sin metadata): atención con qkv FUSIONADO cuya dim
    de entrada es 4608 (la del modelo) — distingue de zimage (qkv/to_* dim 3840)
    y de flux/SDXL (prefijo lora_unet_*)."""
    bm = (meta.get("ss_base_model_version") or meta.get("ss_base_model") or "")
    if "ideogram" in bm.lower():
        return True
    for k, v in hdr.items():
        if (k.startswith("diffusion_model.layers.") and ".attention.qkv." in k
                and k.endswith("lora_A.weight")):
            shape = v.get("shape") or []
            if len(shape) == 2 and shape[1] == _IDEOGRAM_DIM:
                return True
    return False


def _rank(hdr: dict) -> int | None:
    return max((v["shape"][0] for k, v in hdr.items()
               if k.endswith("lora_A.weight") and v.get("shape")), default=None)


def list_loras(models_root: Path, show_all: bool = False) -> list[dict]:
    """LoRAs del almacén (<models>/loras) con detección de arquitectura.
    Por defecto solo Ideogram 4; show_all=True devuelve todos (escape de la UI).
    Ordena los compatibles primero. Ficheros con header roto se saltan sin
    tirar el catálogo."""
    base = Path(models_root) / "loras"
    if not base.exists():
        return []
    out = []
    for p in sorted(base.rglob("*.safetensors")):
        try:
            st = p.stat()
        except OSError:
            continue
        key, stamp = str(p), (st.st_mtime_ns, st.st_size)
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            entry = cached[1]
        else:
            try:
                hdr, meta = _read_header(p)
            except Exception:
                continue
            bm = meta.get("ss_base_model_version") or meta.get("ss_base_model") or ""
            entry = {
                "file": p.relative_to(base).as_posix(),
                "name": p.stem,
                "subfolder": ("" if p.parent == base
                              else p.parent.relative_to(base).as_posix()),
                "size_bytes": st.st_size,
                "arch_match": is_ideogram(hdr, meta),
                "base_model": bm or None,
                "rank": _rank(hdr),
            }
            _cache[key] = (stamp, entry)
        out.append(entry)
    # has_preview fuera del cache: el .preview.* puede aparecer/borrarse sin que
    # cambie el mtime del safetensors (llave del cache).
    out = [{**e, "has_preview": _find_preview(base / e["file"]) is not None}
           for e in out]
    if not show_all:
        out = [e for e in out if e["arch_match"]]
    out.sort(key=lambda e: (not e["arch_match"], e["name"].lower()))
    return out


def preview_path(models_root: Path, file: str) -> Path:
    """Path del .preview.* de un LoRA (rel posix a <models>/loras). Guard de
    traversal + existencia."""
    base = (Path(models_root) / "loras").resolve()
    p = (base / file).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise FileNotFoundError(file)
    prev = _find_preview(p)
    if not prev:
        raise FileNotFoundError(f"sin preview: {file}")
    return prev
