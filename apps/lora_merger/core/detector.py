"""
Detector de arquitectura para archivos LoRA (.safetensors).
Lee solo el header del archivo (sin cargar tensores en memoria).
"""
import struct
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class LoRAInfo:
    path: str
    arch: str                    # "SD 1.5", "SDXL", "Flux", etc.
    arch_id: str                 # slug interno: "sd15", "sdxl", "flux", etc.
    keys: list[str] = field(default_factory=list)
    ranks: dict[str, int] = field(default_factory=dict)   # prefix -> rank
    alphas: dict[str, float] = field(default_factory=dict) # prefix -> alpha
    num_layers: int = 0
    has_te: bool = False          # contiene pesos de text encoder
    has_unet: bool = False
    size_mb: float = 0.0
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None


# ── Patrones por arquitectura ─────────────────────────────────────────── #
# El detector evalúa cada conjunto de reglas en orden; se queda con
# la primera que supere el umbral de coincidencia.

_ARCH_RULES: list[tuple[str, str, list[str], list[str]]] = [
    # (arch_id, arch_display, must_contain_any, must_NOT_contain_any)
    (
        "flux",
        "Flux.1",
        ["lora_unet_double_blocks_", "double_blocks.", "lora_unet_single_blocks_",
         "single_blocks.", "transformer.double_blocks", "transformer.single_blocks"],
        [],
    ),
    (
        "wan22",
        "Wan 2.2",
        ["lora_unet_blocks_", "wan_"],
        ["double_blocks", "single_blocks", "lora_te1_", "lora_te2_"],
    ),
    (
        "sd3",
        "SD 3.x",
        ["transformer_blocks.", "joint_transformer_blocks"],
        ["double_blocks", "lora_te1_", "lora_te2_"],
    ),
    (
        "sdxl",
        "SDXL / Pony / Illustrious",
        ["lora_te1_", "lora_te2_"],
        ["double_blocks", "single_blocks"],
    ),
    (
        "sdxl_unet_only",
        "SDXL (solo UNet)",
        ["lora_unet_down_blocks_", "lora_unet_up_blocks_", "lora_unet_mid_block_",
         "lora_unet_output_blocks_", "lora_unet_input_blocks_"],
        ["lora_te_", "lora_te1_", "double_blocks", "single_blocks",
         "joint_transformer"],
    ),
    (
        "sd15",
        "SD 1.5",
        ["lora_unet_down_blocks_", "lora_unet_up_blocks_", "lora_te_text_model_"],
        ["lora_te1_", "lora_te2_", "double_blocks"],
    ),
    (
        "sd15_unet_only",
        "SD 1.5 (solo UNet)",
        ["lora_unet_down_blocks_", "lora_unet_up_blocks_"],
        ["lora_te_", "lora_te1_", "lora_te2_", "double_blocks"],
    ),
    (
        "dit_generic",
        "DiT genérico",
        ["transformer_blocks"],
        [],
    ),
]


def _read_header(path: str) -> tuple[dict, dict]:
    """
    Lee el JSON de cabecera de un archivo safetensors.
    Devuelve (tensors_dict, metadata_dict).
    """
    with open(path, "rb") as f:
        raw_n = f.read(8)
        if len(raw_n) < 8:
            raise ValueError("Archivo demasiado pequeño para ser safetensors")
        n = struct.unpack("<Q", raw_n)[0]
        raw_header = f.read(n)
    header = json.loads(raw_header)
    metadata = header.pop("__metadata__", {})
    return header, metadata


def _score_arch(keys_set: set[str], must: list[str], must_not: list[str]) -> float:
    """Retorna el porcentaje de claves 'must' encontradas. 0 si alguna 'must_not' aparece."""
    for pat in must_not:
        if any(pat in k for k in keys_set):
            return 0.0
    if not must:
        return 0.0
    hits = sum(1 for pat in must if any(pat in k for k in keys_set))
    return hits / len(must)


def _extract_ranks_alphas(
    header: dict,
) -> tuple[dict[str, int], dict[str, float]]:
    """
    Extrae ranks y alphas del header.
    Busca tensores *.lora_down.weight para obtener rank (dim 0),
    y tensores *.alpha para el escalar alpha.
    """
    ranks: dict[str, int] = {}
    alphas: dict[str, float] = {}

    for key, info in header.items():
        shape = info.get("shape", [])
        if key.endswith(".lora_down.weight") and shape:
            prefix = key[: -len(".lora_down.weight")]
            ranks[prefix] = shape[0]  # rank = primera dim de A
        elif key.endswith(".alpha") and shape == []:
            # alpha es un escalar (tensor de forma [])
            prefix = key[: -len(".alpha")]
            # El valor real no está en el header (solo metadatos de forma/dtype)
            # Lo marcamos como None hasta cargarlo; aquí solo registramos existencia
            alphas[prefix] = None  # valor pendiente de cargar
        elif key.endswith(".alpha") and len(shape) == 1 and shape[0] == 1:
            prefix = key[: -len(".alpha")]
            alphas[prefix] = None

    return ranks, alphas


def detect(path: str) -> LoRAInfo:
    """
    Detecta la arquitectura de un archivo LoRA safetensors.
    No carga tensores completos en memoria.
    """
    p = Path(path)
    if not p.exists():
        return LoRAInfo(path=path, arch="Desconocida", arch_id="unknown",
                        error=f"Archivo no encontrado: {path}")
    if p.suffix.lower() != ".safetensors":
        return LoRAInfo(path=path, arch="Desconocida", arch_id="unknown",
                        error="El archivo no es .safetensors")

    try:
        header, metadata = _read_header(path)
    except Exception as e:
        return LoRAInfo(path=path, arch="Desconocida", arch_id="unknown",
                        error=f"Error leyendo header: {e}")

    keys = [k for k in header.keys()]
    keys_set = set(keys)

    # Detectar arquitectura
    best_arch_id = "unknown"
    best_arch = "Desconocida"
    best_score = 0.0

    for arch_id, arch_display, must, must_not in _ARCH_RULES:
        score = _score_arch(keys_set, must, must_not)
        if score > best_score:
            best_score = score
            best_arch_id = arch_id
            best_arch = arch_display

    # Metadatos de ss_ (kohya training metadata)
    if "ss_base_model_version" in metadata:
        ver = metadata["ss_base_model_version"].lower()
        if "sdxl" in ver and best_arch_id in ("unknown", "sd15_unet_only"):
            best_arch_id = "sdxl_unet_only"
            best_arch = "SDXL / Pony / Illustrious"
        elif "sd-1" in ver and best_arch_id == "unknown":
            best_arch_id = "sd15"
            best_arch = "SD 1.5"
        elif "flux" in ver and best_arch_id == "unknown":
            best_arch_id = "flux"
            best_arch = "Flux.1"

    ranks, alphas = _extract_ranks_alphas(header)

    has_te = any(
        pat in k for k in keys_set
        for pat in ("lora_te_", "lora_te1_", "lora_te2_", "text_model")
    )
    has_unet = any(
        pat in k for k in keys_set
        for pat in ("lora_unet_", "double_blocks", "single_blocks", "transformer_blocks")
    )

    size_mb = p.stat().st_size / (1024 * 1024)

    return LoRAInfo(
        path=path,
        arch=best_arch,
        arch_id=best_arch_id,
        keys=keys,
        ranks=ranks,
        alphas=alphas,
        num_layers=len(ranks),
        has_te=has_te,
        has_unet=has_unet,
        size_mb=size_mb,
        metadata=metadata,
    )


def get_common_rank(info: LoRAInfo) -> Optional[int]:
    """Retorna el rank más frecuente en el LoRA, o None si no tiene capas."""
    if not info.ranks:
        return None
    from collections import Counter
    counts = Counter(info.ranks.values())
    return counts.most_common(1)[0][0]


def get_rank_summary(info: LoRAInfo) -> str:
    """Descripción legible del rank del LoRA."""
    if not info.ranks:
        return "Sin capas detectadas"
    from collections import Counter
    counts = Counter(info.ranks.values())
    if len(counts) == 1:
        rank = list(counts.keys())[0]
        return f"Rank {rank}"
    parts = [f"Rank {r} ({c} capas)" for r, c in counts.most_common(3)]
    return ", ".join(parts) + (" ..." if len(counts) > 3 else "")
