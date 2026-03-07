"""
Motor de merge de LoRAs.

Implementa los algoritmos de fusión sobre las matrices A/B de LoRA sin
expandir los deltas completos en memoria de una vez. Procesa capa por capa.

Algoritmos disponibles:
  - weighted_sum : suma ponderada de deltas  (caso general)
  - slerp        : interpolación esférica     (exactamente 2 LoRAs)
  - ties         : resolución de conflictos por signo
  - dare         : poda aleatoria por magnitud
  - dare_ties    : DARE + TIES combinados
"""
import struct
import json
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .detector import LoRAInfo


# ── Tipos ─────────────────────────────────────────────────────────────── #

@dataclass
class MergeConfig:
    method: str = "weighted_sum"          # uno de los algoritmos
    weights: list[float] = field(default_factory=list)  # un peso por LoRA
    target_rank: Optional[int] = None     # None → usar el rank mayor
    layer_filter: str = "full"            # "full", "attn-only", "mlp-only", "attn-mlp"
    # DARE params
    dare_density: float = 0.5            # fracción de parámetros a conservar (0-1)
    # TIES params
    ties_k: float = 0.2                  # top-k fracción para sparsificación
    # SLERP param
    slerp_t: float = 0.5                 # interpolación 0→LoRA1, 1→LoRA2
    output_path: str = ""
    output_dtype: str = "bf16"           # "bf16", "f16", "f32"


@dataclass
class MergeProgress:
    current: int = 0
    total: int = 0
    layer_name: str = ""
    done: bool = False
    error: Optional[str] = None


# ── Lectura lazy de tensores ──────────────────────────────────────────── #

def _read_header(path: str) -> tuple[dict, dict]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        raw = f.read(n)
    header = json.loads(raw)
    meta = header.pop("__metadata__", {})
    return header, meta


def _load_tensor(path: str, key: str, header: dict) -> Optional[np.ndarray]:
    if key not in header:
        return None
    info = header[key]
    dtype_map = {
        "F32": np.float32, "F16": np.float16,
        "F64": np.float64, "I32": np.int32,
        "I8": np.int8, "U8": np.uint8,
    }
    dtype = dtype_map.get(info.get("dtype", "F32"), np.float32)
    shape = info["shape"]
    offsets = info["data_offsets"]

    with open(path, "rb") as f:
        raw_n = struct.unpack("<Q", f.read(8))[0]
        byte_start = 8 + raw_n + offsets[0]
        f.seek(byte_start)
        raw = f.read(offsets[1] - offsets[0])

    dtype_str = info.get("dtype", "F32")
    if dtype_str == "BF16":
        u16 = np.frombuffer(raw, dtype=np.uint16)
        f32 = (u16.astype(np.uint32) << 16).view(np.float32)
        return f32.reshape(shape)

    return np.frombuffer(raw, dtype=dtype).reshape(shape).astype(np.float32)


def _load_scalar(path: str, key: str, header: dict) -> Optional[float]:
    arr = _load_tensor(path, key, header)
    if arr is None:
        return None
    return float(arr.flat[0])


# ── SVD re-compresión ────────────────────────────────────────────────── #

def _svd_recompose(delta: np.ndarray, rank: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Dado un delta completo (m×n), lo descompone en B (m×rank) y A (rank×n)
    tal que B @ A ≈ delta con truncated SVD.
    """
    # Para matrices muy grandes, usar randomized SVD sería mejor,
    # pero para LoRAs los ranks son pequeños así que SVD exacta es OK
    orig_shape = delta.shape
    if delta.ndim > 2:
        # Conv: (out, in, kH, kW) → (out, in*kH*kW)
        delta = delta.reshape(orig_shape[0], -1)

    U, S, Vh = np.linalg.svd(delta, full_matrices=False)
    # Truncar al rank objetivo
    r = min(rank, len(S))
    U_r  = U[:, :r]               # (m, r)
    S_r  = S[:r]                   # (r,)
    Vh_r = Vh[:r, :]               # (r, n)

    B = U_r * S_r          # (m, r)  — absorber S en B
    A = Vh_r               # (r, n)

    # Reconstruir dimensiones originales para conv
    if len(orig_shape) > 2:
        A = A.reshape((r,) + orig_shape[1:])

    return B.astype(np.float32), A.astype(np.float32)


# ── Filtro de capas ───────────────────────────────────────────────────── #

def _passes_filter(prefix: str, layer_filter: str) -> bool:
    if layer_filter == "full":
        return True
    p = prefix.lower()
    is_attn = any(x in p for x in ("attn", "to_q", "to_k", "to_v", "to_out",
                                    "self_attn", "cross_attn"))
    is_mlp  = any(x in p for x in ("mlp", "ff.", "fc1", "fc2",
                                    "proj_in", "proj_out", "net.0", "net.2"))
    if layer_filter == "attn-only":
        return is_attn
    if layer_filter == "mlp-only":
        return is_mlp
    if layer_filter == "attn-mlp":
        return is_attn or is_mlp
    return True


# ── Algoritmos de merge ───────────────────────────────────────────────── #

def _merge_weighted_sum(
    deltas: list[np.ndarray], weights: list[float]
) -> np.ndarray:
    """Suma ponderada de deltas."""
    result = np.zeros_like(deltas[0])
    for d, w in zip(deltas, weights):
        result += w * d
    return result


def _merge_slerp(
    delta1: np.ndarray, delta2: np.ndarray, t: float
) -> np.ndarray:
    """Interpolación esférica entre dos deltas."""
    shape = delta1.shape
    v1 = delta1.flatten().astype(np.float64)
    v2 = delta2.flatten().astype(np.float64)

    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)

    if norm1 < 1e-12 or norm2 < 1e-12:
        # Si alguno es prácticamente cero, usar interpolación lineal
        return ((1 - t) * delta1 + t * delta2)

    v1_n = v1 / norm1
    v2_n = v2 / norm2
    dot = float(np.clip(np.dot(v1_n, v2_n), -1.0, 1.0))

    if abs(dot) > 0.9999:
        # Casi paralelos, interpolación lineal
        result = (1 - t) * v1 + t * v2
    else:
        omega = np.arccos(dot)
        sin_omega = np.sin(omega)
        result = (np.sin((1 - t) * omega) / sin_omega) * v1 + \
                 (np.sin(t * omega)       / sin_omega) * v2

    return result.reshape(shape).astype(np.float32)


def _ties_sparsify(delta: np.ndarray, k: float) -> np.ndarray:
    """Conserva solo el top-k de parámetros por magnitud."""
    flat = delta.flatten()
    threshold_idx = int(len(flat) * (1 - k))
    if threshold_idx <= 0:
        return delta.copy()
    sorted_idx = np.argsort(np.abs(flat))
    mask = np.ones_like(flat)
    mask[sorted_idx[:threshold_idx]] = 0.0
    return (flat * mask).reshape(delta.shape)


def _merge_ties(
    deltas: list[np.ndarray], weights: list[float], k: float = 0.2
) -> np.ndarray:
    """
    TIES merge:
    1. Sparsificar cada delta (top-k por magnitud)
    2. Voto mayoritario de signo por parámetro
    3. Sumar solo los que coincidan con el signo ganador
    """
    sparse = [_ties_sparsify(d, k) for d in deltas]

    # Voto de signo: suma ponderada de signos
    sign_vote = sum(w * np.sign(s) for s, w in zip(sparse, weights))
    majority_sign = np.sign(sign_vote)

    merged = np.zeros_like(deltas[0])
    weight_sum = np.zeros_like(deltas[0])

    for s, w in zip(sparse, weights):
        # Solo contribuir donde el signo coincide con la mayoría
        agree = (np.sign(s) * majority_sign) > 0
        merged += w * s * agree.astype(np.float32)
        weight_sum += w * agree.astype(np.float32)

    # Normalizar donde hay contribuciones
    nonzero = weight_sum > 1e-12
    merged[nonzero] /= weight_sum[nonzero]

    return merged


def _dare_prune(delta: np.ndarray, density: float) -> np.ndarray:
    """
    DARE pruning: eliminar aleatoriamente parámetros con prob (1-density),
    escalando los restantes por 1/density.
    """
    if density >= 1.0:
        return delta.copy()
    mask = (np.random.rand(*delta.shape) < density).astype(np.float32)
    return delta * mask / max(density, 1e-8)


def _merge_dare(
    deltas: list[np.ndarray], weights: list[float], density: float = 0.5
) -> np.ndarray:
    """DARE: podar deltas antes de la suma ponderada."""
    pruned = [_dare_prune(d, density) for d in deltas]
    return _merge_weighted_sum(pruned, weights)


def _merge_dare_ties(
    deltas: list[np.ndarray], weights: list[float],
    density: float = 0.5, k: float = 0.2
) -> np.ndarray:
    """DARE + TIES: podar primero, luego aplicar TIES."""
    pruned = [_dare_prune(d, density) for d in deltas]
    return _merge_ties(pruned, weights, k)


# ── Función principal de merge ─────────────────────────────────────────── #

def merge(
    loras: list[LoRAInfo],
    config: MergeConfig,
    progress_callback: Optional[Callable[[MergeProgress], None]] = None,
) -> Optional[str]:
    """
    Fusiona los LoRAs según config y escribe el resultado en config.output_path.
    Retorna la ruta del archivo generado, o None si hubo error.
    """
    if len(loras) < 2:
        raise ValueError("Se necesitan al menos 2 LoRAs.")
    if len(config.weights) != len(loras):
        raise ValueError("El número de pesos debe coincidir con el de LoRAs.")
    if not config.output_path:
        raise ValueError("No se especificó ruta de salida.")

    # Normalizar pesos a suma 1
    w_sum = sum(config.weights)
    if w_sum < 1e-8:
        raise ValueError("La suma de pesos es cero.")
    weights = [w / w_sum for w in config.weights]

    # Cargar headers
    headers: list[dict] = []
    metas:   list[dict] = []
    for lora in loras:
        h, m = _read_header(lora.path)
        headers.append(h)
        metas.append(m)

    # Determinar rank objetivo
    target_rank = config.target_rank
    if target_rank is None:
        all_ranks = [r for lora in loras for r in lora.ranks.values()]
        target_rank = max(all_ranks) if all_ranks else 4

    # Recopilar todos los prefijos (unión de capas)
    all_prefixes: set[str] = set()
    for lora in loras:
        all_prefixes.update(lora.ranks.keys())

    # Filtrar por layer_filter
    prefixes = sorted(
        p for p in all_prefixes if _passes_filter(p, config.layer_filter)
    )

    total = len(prefixes)
    prog = MergeProgress(total=total)

    # Acumulador de tensores para el archivo de salida
    output_tensors: dict[str, np.ndarray] = {}
    # alpha fijo = target_rank (scale = 1.0)

    for i, prefix in enumerate(prefixes):
        if progress_callback:
            prog.current = i + 1
            prog.layer_name = prefix.split("lora_unet_")[-1][:40]
            progress_callback(prog)

        # Obtener deltas de cada LoRA
        deltas: list[np.ndarray] = []
        valid_weights: list[float] = []

        for lora, header, w in zip(loras, headers, weights):
            A_key = prefix + ".lora_down.weight"
            B_key = prefix + ".lora_up.weight"
            alpha_key = prefix + ".alpha"

            A = _load_tensor(lora.path, A_key, header)
            B = _load_tensor(lora.path, B_key, header)

            if A is None or B is None:
                continue

            alpha_val = _load_scalar(lora.path, alpha_key, header)
            rank = A.shape[0]
            alpha = alpha_val if alpha_val is not None else float(rank)
            scale = alpha / rank

            # Expandir a delta completo
            # A shape: (rank, in) or (rank, in, kH, kW)
            # B shape: (out, rank)
            if A.ndim == 2:
                delta = scale * (B @ A)   # (out, in)
            else:
                # Conv: B: (out, rank, 1, 1), A: (rank, in, kH, kW)
                B_2d = B.reshape(B.shape[0], B.shape[1])
                A_2d = A.reshape(A.shape[0], -1)
                delta_2d = scale * (B_2d @ A_2d)
                delta = delta_2d.reshape((B.shape[0],) + A.shape[1:])

            deltas.append(delta)
            valid_weights.append(w)

        if not deltas:
            continue

        # Normalizar pesos de las capas disponibles
        vw_sum = sum(valid_weights)
        valid_weights = [vw / vw_sum for vw in valid_weights]

        # Aplicar algoritmo de merge
        if config.method == "weighted_sum":
            merged_delta = _merge_weighted_sum(deltas, valid_weights)
        elif config.method == "slerp":
            if len(deltas) == 2:
                merged_delta = _merge_slerp(deltas[0], deltas[1], config.slerp_t)
            else:
                # Fallback: weighted sum si hay más de 2
                merged_delta = _merge_weighted_sum(deltas, valid_weights)
        elif config.method == "ties":
            merged_delta = _merge_ties(deltas, valid_weights, config.ties_k)
        elif config.method == "dare":
            merged_delta = _merge_dare(deltas, valid_weights, config.dare_density)
        elif config.method == "dare_ties":
            merged_delta = _merge_dare_ties(
                deltas, valid_weights, config.dare_density, config.ties_k
            )
        else:
            merged_delta = _merge_weighted_sum(deltas, valid_weights)

        # Re-descomponer via SVD
        B_new, A_new = _svd_recompose(merged_delta, target_rank)
        output_tensors[prefix + ".lora_up.weight"]   = B_new
        output_tensors[prefix + ".lora_down.weight"] = A_new
        output_tensors[prefix + ".alpha"] = np.array(float(target_rank), dtype=np.float32)

    if not output_tensors:
        raise RuntimeError("No se generaron capas en el output. Verifica los LoRAs.")

    # Escribir archivo safetensors
    _write_safetensors(output_tensors, config.output_path, metas[0], config.output_dtype)

    if progress_callback:
        prog.done = True
        progress_callback(prog)

    return config.output_path


# ── Escritura safetensors ─────────────────────────────────────────────── #

def _convert_dtype(arr: np.ndarray, dtype_str: str) -> tuple[bytes, str]:
    """Convierte un array F32 a los bytes del dtype de salida."""
    arr32 = arr.astype(np.float32)
    if dtype_str == "bf16":
        # F32 → BF16: tomar los 2 bytes superiores de cada float32
        u32 = arr32.view(np.uint32)
        bf16 = (u32 >> 16).astype(np.uint16)
        return bf16.tobytes(), "BF16"
    elif dtype_str == "f16":
        return arr32.astype(np.float16).tobytes(), "F16"
    else:
        return arr32.tobytes(), "F32"


def _write_safetensors(
    tensors: dict[str, np.ndarray],
    output_path: str,
    base_metadata: dict,
    output_dtype: str = "bf16",
) -> None:
    """
    Escribe un archivo safetensors desde un dict de numpy arrays.
    output_dtype: "bf16", "f16" o "f32"
    """
    import os
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    metadata = {
        "merged_by": "AI Hub LoRA Merger",
        "ss_base_model_version": base_metadata.get("ss_base_model_version", ""),
    }
    metadata = {k: v for k, v in metadata.items() if v}

    # Primera pasada: convertir tensores y calcular offsets
    ordered_keys = sorted(tensors.keys())
    converted: dict[str, tuple[bytes, str, list]] = {}  # key → (raw_bytes, dtype_str, shape)
    offset = 0

    header: dict = {"__metadata__": metadata}

    for key in ordered_keys:
        arr = tensors[key]
        shape = list(arr.shape)
        # alpha siempre en F32 para máxima compatibilidad
        if key.endswith(".alpha"):
            raw, dtype_out = arr.astype(np.float32).tobytes(), "F32"
        else:
            raw, dtype_out = _convert_dtype(arr, output_dtype)
        converted[key] = (raw, dtype_out, shape)
        nbytes = len(raw)
        header[key] = {
            "dtype": dtype_out,
            "shape": shape,
            "data_offsets": [offset, offset + nbytes],
        }
        offset += nbytes

    header_json = json.dumps(header, separators=(",", ":"))
    header_bytes = header_json.encode("utf-8")
    # Alinear a 8 bytes
    padding = (8 - len(header_bytes) % 8) % 8
    header_bytes = header_bytes + b" " * padding

    header_size = len(header_bytes)

    with open(output_path, "wb") as f:
        f.write(struct.pack("<Q", header_size))
        f.write(header_bytes)
        for key in ordered_keys:
            f.write(converted[key][0])
