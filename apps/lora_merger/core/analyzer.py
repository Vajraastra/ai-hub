"""
Analizador de impacto por capas para LoRAs.

Calcula un score de "cuánto aprendió" cada bloque, basándose en la
magnitud de las matrices A/B. No carga el modelo base; trabaja solo
con el archivo LoRA.

Inspirado en comfyUI-Realtime-Lora (shootthesound).
"""
import struct
import json
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .detector import LoRAInfo


@dataclass
class LayerScore:
    prefix: str           # nombre base de la capa
    block_name: str       # bloque (ej: "down_blocks.0", "double_blocks.3")
    score: float          # 0.0 – 100.0
    rank: int
    alpha: float


@dataclass
class AnalysisResult:
    lora_path: str
    layer_scores: list[LayerScore] = field(default_factory=list)
    block_scores: dict[str, float] = field(default_factory=dict)
    content_type: str = "mixto"   # "estilo", "personaje", "concepto", "mixto"
    content_explanation: str = ""
    error: Optional[str] = None


# ── Utilidades de lectura lazy ─────────────────────────────────────────── #

def _load_tensor_as_numpy(path: str, key: str) -> Optional[np.ndarray]:
    """
    Carga un único tensor del archivo safetensors como numpy array.
    Evita cargar todo el archivo en memoria.
    """
    try:
        with open(path, "rb") as f:
            raw_n = f.read(8)
            n = struct.unpack("<Q", raw_n)[0]
            raw_header = f.read(n)
        header = json.loads(raw_header)
        header.pop("__metadata__", None)

        if key not in header:
            return None

        info = header[key]
        dtype_map = {
            "F32": np.float32, "F16": np.float16, "BF16": np.float32,  # BF16 → F32 approx
            "F64": np.float64, "I32": np.int32, "I16": np.int16, "I8": np.int8,
            "U8": np.uint8,
        }
        dtype_str = info.get("dtype", "F32")
        dtype = dtype_map.get(dtype_str, np.float32)
        shape = info["shape"]
        offsets = info["data_offsets"]
        byte_start = 8 + n + offsets[0]
        byte_end   = 8 + n + offsets[1]
        nbytes = byte_end - byte_start

        with open(path, "rb") as f:
            f.seek(byte_start)
            raw = f.read(nbytes)

        arr = np.frombuffer(raw, dtype=dtype).reshape(shape)

        # BF16 no tiene soporte nativo en numpy — aproximar
        if dtype_str == "BF16":
            # interpretar bytes como uint16 y desplazar a float32
            u16 = np.frombuffer(raw, dtype=np.uint16)
            f32 = (u16.astype(np.uint32) << 16).view(np.float32)
            arr = f32.reshape(shape)

        return arr.astype(np.float32)
    except Exception:
        return None


# ── Extracción de nombre de bloque ─────────────────────────────────────── #

def _extract_block_name(prefix: str, arch_id: str) -> str:
    """
    A partir del prefijo de una capa (ej: lora_unet_down_blocks_0_attentions_1_...)
    extrae el nombre del bloque principal (ej: "down_blocks.0").
    """
    p = prefix

    # Flux
    if "double_blocks" in p:
        try:
            idx = p.split("double_blocks_")[1].split("_")[0]
            return f"double_blocks.{idx}"
        except Exception:
            return "double_blocks"
    if "single_blocks" in p:
        try:
            idx = p.split("single_blocks_")[1].split("_")[0]
            return f"single_blocks.{idx}"
        except Exception:
            return "single_blocks"

    # SD1.5 / SDXL UNet
    for segment in ("down_blocks", "up_blocks", "mid_block", "output_blocks", "input_blocks"):
        if segment in p:
            try:
                idx = p.split(f"{segment}_")[1].split("_")[0]
                return f"{segment.replace('_', '_')}.{idx}"
            except Exception:
                return segment

    # Text encoder
    if "lora_te" in p or "text_model" in p:
        return "text_encoder"

    # Genérico
    parts = p.split("_")
    return "_".join(parts[:4]) if len(parts) >= 4 else p


# ── Análisis principal ─────────────────────────────────────────────────── #

def analyze(lora_info: LoRAInfo) -> AnalysisResult:
    """
    Analiza el impacto por capas de un LoRA.
    Carga las matrices A y B de cada capa para calcular la norma del delta.
    """
    result = AnalysisResult(lora_path=lora_info.path)

    if lora_info.error:
        result.error = lora_info.error
        return result

    path = lora_info.path
    prefixes = list(lora_info.ranks.keys())

    if not prefixes:
        result.error = "No se encontraron capas LoRA en este archivo."
        return result

    raw_scores: list[tuple[str, float, int, float]] = []
    # (prefix, magnitud_delta, rank, alpha)

    for prefix in prefixes:
        rank = lora_info.ranks[prefix]
        alpha = rank  # default: alpha == rank → scale = 1.0

        A = _load_tensor_as_numpy(path, prefix + ".lora_down.weight")
        B = _load_tensor_as_numpy(path, prefix + ".lora_up.weight")

        if A is None or B is None:
            continue

        # Normalizar por alpha/rank para obtener la magnitud efectiva
        scale = alpha / rank

        # Aproximar la norma de frobenius del delta completo sin expandirlo
        # ||delta||_F = ||B @ A||_F ≈ ||B||_F * ||A||_F  (upper bound)
        # Usamos la traza de B^T B * A A^T para ser más preciso:
        # Traza(delta^T delta) = Traza(A^T B^T B A)
        # = sum over i of ||B_i||^2 * ||A_i||^2  (aprox por columnas)
        # Usamos norma de Frobenius de A y B directamente
        norm_A = float(np.linalg.norm(A))
        norm_B = float(np.linalg.norm(B))
        magnitude = scale * norm_A * norm_B

        raw_scores.append((prefix, magnitude, rank, alpha))

    if not raw_scores:
        result.error = "No se pudieron cargar tensores de ninguna capa."
        return result

    # Normalizar scores a 0-100
    magnitudes = [s[1] for s in raw_scores]
    max_mag = max(magnitudes) if magnitudes else 1.0
    if max_mag == 0:
        max_mag = 1.0

    layer_scores: list[LayerScore] = []
    block_totals: dict[str, list[float]] = {}

    for prefix, magnitude, rank, alpha in raw_scores:
        score = (magnitude / max_mag) * 100.0
        block = _extract_block_name(prefix, lora_info.arch_id)

        ls = LayerScore(
            prefix=prefix,
            block_name=block,
            score=score,
            rank=rank,
            alpha=alpha,
        )
        layer_scores.append(ls)
        block_totals.setdefault(block, []).append(score)

    # Score por bloque = promedio de sus capas
    block_scores = {b: float(np.mean(vals)) for b, vals in block_totals.items()}

    result.layer_scores = sorted(layer_scores, key=lambda x: -x.score)
    result.block_scores = block_scores

    # Inferir tipo de contenido
    result.content_type, result.content_explanation = _infer_content_type(
        block_scores, lora_info.arch_id
    )

    return result


def _infer_content_type(block_scores: dict[str, float], arch_id: str) -> tuple[str, str]:
    """
    Infiere el tipo de contenido del LoRA según qué bloques dominan.
    Retorna (tipo, explicacion).
    """
    if not block_scores:
        return "desconocido", "No hay datos suficientes."

    te_score = block_scores.get("text_encoder", 0.0)
    scores_list = sorted(block_scores.items(), key=lambda x: -x[1])
    top_blocks = [b for b, _ in scores_list[:3]]

    # Heurística por arquitectura
    if arch_id in ("sd15", "sdxl", "sdxl_unet_only"):
        shallow = any("down_blocks.0" in b or "input_blocks.1" in b for b in top_blocks)
        deep = any("up_blocks.3" in b or "output_blocks.9" in b
                   or "mid_block" in b for b in top_blocks)

        if te_score > 60:
            return "concepto", (
                "Domina el Text Encoder — LoRA de concepto o estilo semántico. "
                "Afecta principalmente cómo el modelo interpreta el texto."
            )
        if shallow:
            return "estilo", (
                "Domina en capas superficiales (down_blocks iniciales). "
                "Típico de LoRAs de estilo artístico o paleta de colores."
            )
        if deep:
            return "personaje / sujeto", (
                "Domina en capas profundas (up_blocks o mid_block). "
                "Típico de LoRAs de personajes, objetos o sujetos específicos."
            )
        return "mixto", (
            "El impacto está distribuido por varios bloques. "
            "Posiblemente un LoRA de estilo + personaje combinado."
        )

    if arch_id == "flux":
        n_double = sum(1 for b in top_blocks if "double_blocks" in b)
        n_single = sum(1 for b in top_blocks if "single_blocks" in b)
        if n_double > n_single:
            return "concepto / texto", (
                "Domina en double_blocks (atención imagen-texto). "
                "Afecta principalmente cómo el modelo interpreta el prompt."
            )
        return "detalles / estilo", (
            "Domina en single_blocks. "
            "Afecta principalmente detalles visuales y estilo."
        )

    return "mixto", "Distribución de impacto general en todos los bloques."
