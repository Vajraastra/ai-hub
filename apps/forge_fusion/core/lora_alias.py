"""
Alias de naming diffusers→kohya para LoRAs SDXL (conversión al vuelo).

Buena parte de los LoRAs de Civitai vienen con prefijo kohya pero rutas de
módulo del UNet de diffusers (`lora_unet_down_blocks_1_attentions_0_...`) en
vez del naming LDM canónico (`lora_unet_input_blocks_4_1_...`). Ni el nodo
selectivo ni el merge los entienden: el nodo clasificaría sus claves en
toggles inexistentes (unet_down_N) y las descartaría EN SILENCIO — la misma
clase de fallo mudo del bug s56.

Solución: un único punto de traducción. Al seleccionar uno de estos LoRAs se
escribe una copia con las claves renombradas al kohya canónico bajo
<models>/loras/_forge_alias/ y TODO el pipeline (preview runtime y merge)
consume esa copia → preview ≡ merge por construcción, sin tocar el nodo.

Renombrar claves de un safetensors no necesita torch: se reescribe el header
JSON (mismos dtype/shape/data_offsets) y se copia el bloque de datos tal
cual, byte a byte. Stdlib puro.

Correspondencia de atención (tabla cerrada, verificada contra la colección
real el 2026-07-16 — una sola huella de indexado en 82 ficheros):
  down_blocks.1.attentions.{0,1}   → input_blocks.{4,5}.1    (640, depth 2)
  down_blocks.2.attentions.{0,1}   → input_blocks.{7,8}.1    (1280, depth 10)
  mid_block.attentions.0           → middle_block.1          (1280, depth 10)
  up_blocks.0.attentions.{0,1,2}   → output_blocks.{0,1,2}.1 (1280, depth 10)
  up_blocks.1.attentions.{0,1,2}   → output_blocks.{3,4,5}.1 (640, depth 2)

Correspondencia conv/ResNet/emb (LoCon; añadida s60, censo real 52 ficheros):
  down_blocks.i.resnets.j          → input_blocks.{1+3i+j}.0
  down_blocks.{0,1}.downsamplers.0.conv → input_blocks.{3,6}.0.op
  mid_block.resnets.{0,1}          → middle_block.{0,2}
  up_blocks.i.resnets.j            → output_blocks.{3i+j}.0
  up_blocks.{0,1}.upsamplers.0.conv → output_blocks.{2,5}.2.conv
  conv_in / conv_out               → input_blocks.0.0 / out.2
  time_embedding.linear_{1,2}      → time_embed.{0,2}
  add_embedding.linear_{1,2}       → label_emb.0.{0,2}
  (subs de resnet: conv1→in_layers.2, conv2→out_layers.3,
   time_emb_proj→emb_layers.1, conv_shortcut→skip_connection — shortcut
   solo donde cambia el canal: input 4/7 y todo el decoder)

Guard de impostores: un LoRA SD1.5 usa las mismas rutas diffusers con otros
bloques (down 0–3, up 1–3), depth 1 y cross-attention de 768 (SDXL: 2048).
Los bloques fuera de tabla ya no traducen; además se comprueban dims del
header (attn1.to_q out ∈ {640,1280} según bloque, attn2.to_k in == 2048).

Política all-or-nothing: cualquier módulo UNet no traducible (fuera del
mapa SDXL: otros bloques, DoRA, formatos raros) aborta la conversión —
aplicar "a medias" rompería la garantía preview ≡ merge. Los prefijos de
text encoder se conservan tal cual (la política ya los apaga en nodo y
merge).
"""
import hashlib
import json
import re
import struct
from functools import lru_cache
from pathlib import Path

ALIAS_SUBDIR = "_forge_alias"

# Prefijos TE que se conservan sin traducir (política: nunca se aplican)
_TE_PREFIXES = ("lora_te1_", "lora_te2_", "lora_te_")

# (bloque kohya, bloque diffusers, depth, canal)
_ST_MAP: list[tuple[str, str, int, int]] = [
    ("input_blocks.4.1", "down_blocks.1.attentions.0", 2, 640),
    ("input_blocks.5.1", "down_blocks.1.attentions.1", 2, 640),
    ("input_blocks.7.1", "down_blocks.2.attentions.0", 10, 1280),
    ("input_blocks.8.1", "down_blocks.2.attentions.1", 10, 1280),
    ("middle_block.1", "mid_block.attentions.0", 10, 1280),
    ("output_blocks.0.1", "up_blocks.0.attentions.0", 10, 1280),
    ("output_blocks.1.1", "up_blocks.0.attentions.1", 10, 1280),
    ("output_blocks.2.1", "up_blocks.0.attentions.2", 10, 1280),
    ("output_blocks.3.1", "up_blocks.1.attentions.0", 2, 640),
    ("output_blocks.4.1", "up_blocks.1.attentions.1", 2, 640),
    ("output_blocks.5.1", "up_blocks.1.attentions.2", 2, 640),
]

# módulos conv/ResNet/emb (LoCon): (kohya, diffusers), correspondencia 1:1
_RESNET_SUBS = (("in_layers.2", "conv1"), ("out_layers.3", "conv2"),
                ("emb_layers.1", "time_emb_proj"))


def _conv_map() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = [
        ("input_blocks.0.0", "conv_in"), ("out.2", "conv_out"),
        ("time_embed.0", "time_embedding.linear_1"),
        ("time_embed.2", "time_embedding.linear_2"),
        ("label_emb.0.0", "add_embedding.linear_1"),
        ("label_emb.0.2", "add_embedding.linear_2"),
        ("input_blocks.3.0.op", "down_blocks.0.downsamplers.0.conv"),
        ("input_blocks.6.0.op", "down_blocks.1.downsamplers.0.conv"),
        ("output_blocks.2.2.conv", "up_blocks.0.upsamplers.0.conv"),
        ("output_blocks.5.2.conv", "up_blocks.1.upsamplers.0.conv"),
    ]
    for i in range(3):                      # ResNets del encoder
        for j in range(2):
            k, d = f"input_blocks.{1 + 3*i + j}.0", f"down_blocks.{i}.resnets.{j}"
            pairs += [(f"{k}.{ks}", f"{d}.{ds}") for ks, ds in _RESNET_SUBS]
            if i > 0 and j == 0:            # cambio de canal: input 4.0 y 7.0
                pairs.append((f"{k}.skip_connection", f"{d}.conv_shortcut"))
    for j in (0, 1):                        # ResNets del bottleneck
        k, d = f"middle_block.{2*j}", f"mid_block.resnets.{j}"
        pairs += [(f"{k}.{ks}", f"{d}.{ds}") for ks, ds in _RESNET_SUBS]
    for i in range(3):                      # ResNets del decoder (todos con skip)
        for j in range(3):
            k, d = f"output_blocks.{3*i + j}.0", f"up_blocks.{i}.resnets.{j}"
            pairs += [(f"{k}.{ks}", f"{d}.{ds}") for ks, ds in _RESNET_SUBS]
            pairs.append((f"{k}.skip_connection", f"{d}.conv_shortcut"))
    return pairs


_SUFFIXES = (".alpha", ".lora_down.weight", ".lora_up.weight",
             ".lora_A.weight", ".lora_B.weight")

# claves diffusers de UNet que delatan el naming (con prefijo kohya)
_DIFF_RE = re.compile(r"^lora_unet_(down_blocks|up_blocks|mid_block)_")

_CROSS_ATTN_DIM = 2048   # attn2.to_k/to_v entran del CLIP-L+G (SD1.5: 768)


class LoraAliasError(ValueError):
    """LoRA con naming diffusers que NO se puede traducir con garantías."""


def _flat(path: str) -> str:
    return "lora_unet_" + path.replace(".", "_")


@lru_cache(maxsize=1)
def _module_map() -> dict[str, str]:
    """módulo diffusers (aplanado kohya) → módulo kohya canónico."""
    m: dict[str, str] = {}
    for kohya, diff, depth, _ in _ST_MAP:
        subs = ["proj_in", "proj_out"]
        for t in range(depth):
            tb = f"transformer_blocks.{t}"
            for attn in ("attn1", "attn2"):
                for proj in ("to_q", "to_k", "to_v", "to_out.0"):
                    subs.append(f"{tb}.{attn}.{proj}")
            subs.append(f"{tb}.ff.net.0.proj")
            subs.append(f"{tb}.ff.net.2")
        for s in subs:
            m[_flat(f"{diff}.{s}")] = _flat(f"{kohya}.{s}")
    for kohya, diff in _conv_map():         # conv/ResNet/emb (LoCon)
        m[_flat(diff)] = _flat(kohya)
    return m


@lru_cache(maxsize=1)
def _channel_by_diff_block() -> dict[str, int]:
    """módulo diffusers aplanado (solo el bloque) → canal esperado."""
    return {_flat(diff): ch for _, diff, _, ch in _ST_MAP}


def _split(key: str) -> tuple[str, str] | None:
    """(módulo, sufijo) o None si el sufijo no es de LoRA conocido."""
    for s in _SUFFIXES:
        if key.endswith(s):
            return key[: -len(s)], s
    return None


def needs_alias(keys) -> bool:
    """¿Contiene claves UNet con naming diffusers (candidato a conversión)?"""
    return any(_DIFF_RE.match(k) for k in keys if k != "__metadata__")


def translate_keys(hdr: dict) -> dict[str, str]:
    """Mapa clave vieja → clave nueva para TODO el header (identidad en las
    que no cambian). All-or-nothing: lanza LoraAliasError si algún módulo
    UNet no traduce (conv/LoCon, SD1.5, formato raro) o si las dims del
    header no cuadran con SDXL."""
    mmap = _module_map()
    canonical = set(mmap.values())
    renames: dict[str, str] = {}
    untranslatable: list[str] = []
    n_conv = 0
    for k in hdr:
        if k == "__metadata__":
            continue
        if k.startswith(_TE_PREFIXES):
            renames[k] = k
            continue
        parts = _split(k)
        if parts is None:
            untranslatable.append(k)
            continue
        module, suffix = parts
        if module in mmap:
            renames[k] = mmap[module] + suffix
        elif module in canonical or not _DIFF_RE.match(k):
            # ya canónico (fichero mixto) o fuera del UNet: se conserva —
            # la validación de cobertura decidirá después si es aceptable
            renames[k] = k
        else:
            if re.search(r"_(resnets|downsamplers|upsamplers|conv\d*|"
                         r"time_emb|emb_layers|skip_connection)_?", module):
                n_conv += 1
            untranslatable.append(k)
    if untranslatable:
        extra = (f" ({n_conv} de conv/resnet fuera del mapa SDXL — "
                 "¿SD1.5 u otro UNet?)" if n_conv else "")
        raise LoraAliasError(
            f"{len(untranslatable)} claves UNet sin traducción a kohya"
            f"{extra} — p. ej. {untranslatable[0]!r}")
    _check_dims(hdr, renames)
    if len(set(renames.values())) != len(renames):
        raise LoraAliasError("colisión de claves al traducir (¿fichero con "
                             "módulos duplicados en ambos namings?)")
    return renames


def _check_dims(hdr: dict, renames: dict[str, str]):
    """Guard de impostores: dims del header contra el UNet SDXL real."""
    channels = _channel_by_diff_block()
    for k in renames:
        if k == "__metadata__" or k.startswith(_TE_PREFIXES):
            continue
        shape = hdr[k].get("shape") or []
        if k.endswith((".lora_up.weight", ".lora_B.weight")) \
                and "_attn1_to_q" in k:
            block = next((b for b in channels if k.startswith(b + "_")), None)
            if block and shape and shape[0] != channels[block]:
                raise LoraAliasError(
                    f"{k}: out-dim {shape[0]} ≠ {channels[block]} esperado "
                    "para SDXL (¿LoRA de SD1.5 con naming diffusers?)")
        if k.endswith((".lora_down.weight", ".lora_A.weight")) \
                and "_attn2_to_k" in k:
            if len(shape) == 2 and shape[1] != _CROSS_ATTN_DIM:
                raise LoraAliasError(
                    f"{k}: cross-attention de {shape[1]} ≠ {_CROSS_ATTN_DIM} "
                    "(SDXL) — es un LoRA de SD1.5, no convertible")


def alias_rel_path(lora_rel: str) -> str:
    """Ruta relativa posix (bajo <models>/loras) de la copia convertida."""
    stem = Path(lora_rel).stem
    tag = hashlib.sha1(lora_rel.replace("\\", "/").lower()
                       .encode("utf-8")).hexdigest()[:8]
    return f"{ALIAS_SUBDIR}/{stem}.dk{tag}.safetensors"


def _read_raw_header(path: Path) -> tuple[dict, int]:
    """(header json, offset del bloque de datos)."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 * 1024 * 1024:
            raise LoraAliasError(f"header sospechoso ({n} bytes)")
        hdr = json.loads(f.read(n))
    return hdr, 8 + n


def ensure_alias(models_root: Path, lora_rel: str) -> str:
    """Devuelve la ruta relativa (posix) de la copia kohya-canónica del LoRA,
    creándola si no existe o si el original cambió. Lanza LoraAliasError si
    la traducción no es posible con garantías."""
    loras = Path(models_root) / "loras"
    src = loras / lora_rel
    if not src.is_file():
        raise LoraAliasError(f"no existe el LoRA {lora_rel!r}")
    st = src.stat()
    rel = alias_rel_path(lora_rel)
    dst = loras / rel

    if dst.is_file():
        try:
            hdr_a, _ = _read_raw_header(dst)
            meta = hdr_a.get("__metadata__") or {}
            if (meta.get("forge_alias.src_mtime_ns") == str(st.st_mtime_ns)
                    and meta.get("forge_alias.src_size") == str(st.st_size)):
                return rel
        except Exception:
            pass    # cache corrupto/viejo → se reconstruye

    hdr, data_off = _read_raw_header(src)
    renames = translate_keys(hdr)

    meta = dict(hdr.get("__metadata__") or {})
    meta.update({
        "forge_alias.source": lora_rel.replace("\\", "/"),
        "forge_alias.src_size": str(st.st_size),
        "forge_alias.src_mtime_ns": str(st.st_mtime_ns),
        "forge_alias.format": "diffusers->kohya-v1",
    })
    new_hdr: dict = {"__metadata__": meta}
    for k, v in hdr.items():
        if k != "__metadata__":
            new_hdr[renames[k]] = v
    blob = json.dumps(new_hdr, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        fout.write(struct.pack("<Q", len(blob)))
        fout.write(blob)
        fin.seek(data_off)
        while chunk := fin.read(16 * 1024 * 1024):
            fout.write(chunk)
    tmp.replace(dst)
    return rel
