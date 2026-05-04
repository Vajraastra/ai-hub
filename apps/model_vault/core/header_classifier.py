"""
header_classifier.py — Clasifica el tipo de modelo leyendo solo el header.

Soporta .safetensors (sin cargar tensores en RAM).
Para .gguf/.ckpt/.bin usa heurísticas de extensión + tamaño.
Reutiliza los patrones de detector.py del LoRA Merger para LoRAs.
"""

import os
import struct
import json
from dataclasses import dataclass
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de salida
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelClassification:
    file_path:          str
    model_type:         str    # "lora", "checkpoint", "vae", "controlnet",
                               # "clip", "upscaler", "ipadapter",
                               # "diffusion_model", "embedding", "unknown"
    suggested_category: str    # nombre canónico de carpeta ComfyUI
    arch:               str    # "SD 1.5", "SDXL", "Flux.1", "Unknown", …
    confidence:         float  # 0.0 – 1.0
    source:             str    # "header" | "extension" | "civitai"
    size_mb:            float
    error:              str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Lectura de header safetensors
# ─────────────────────────────────────────────────────────────────────────────

def _read_safetensors_header(path: str) -> tuple[dict, dict]:
    with open(path, "rb") as f:
        raw_n = f.read(8)
        if len(raw_n) < 8:
            raise ValueError("Archivo demasiado pequeño")
        n = struct.unpack("<Q", raw_n)[0]
        if n > 100 * 1024 * 1024:  # header > 100 MB → sospechoso
            raise ValueError("Header demasiado grande")
        raw_header = f.read(n)
    header = json.loads(raw_header)
    metadata = header.pop("__metadata__", {})
    return header, metadata


# ─────────────────────────────────────────────────────────────────────────────
# Reglas de clasificación por prefijos de clave
# ─────────────────────────────────────────────────────────────────────────────

def _classify_by_keys(keys: set[str], metadata: dict) -> tuple[str, str, str, float]:
    """
    Retorna (model_type, suggested_category, arch, confidence).
    Evalúa los patrones en orden de especificidad descendente.
    """

    # ── LoRA / LyCORIS (cualquier arquitectura) ────────────────────────────
    lora_signals = {
        "lora_down.weight", "lora_up.weight", ".lora_A.", ".lora_B.",
        ".hada_w1_a", ".hada_w1_b",   # LoHa (LyCORIS)
        ".lokr_w1",                    # LoKr (LyCORIS)
        ".oft_blocks",                 # OFT (LyCORIS)
        ".boft_blocks",                # BOFT
        ".glora_",                     # GLoRA
    }
    if any(any(sig in k for k in keys) for sig in lora_signals):
        arch = _detect_lora_arch(keys, metadata)
        return "lora", "loras", arch, 0.95

    # ── ControlNet ─────────────────────────────────────────────────────────
    cn_signals = [
        "controlnet_cond_embedding", "control_model.input_blocks",
        "ctrl_", "hint_model",
        "controlnet_blocks.",        # Flux ControlNet
        "controlnet_x_embedder",
    ]
    if any(any(sig in k for k in keys) for sig in cn_signals):
        arch = _detect_base_arch_from_keys(keys)
        return "controlnet", "controlnet", arch, 0.92

    # ── IP-Adapter ─────────────────────────────────────────────────────────
    ipa_signals = ["image_proj.latents", "ip_adapter.", "image_proj_model."]
    if any(any(sig in k for k in keys) for sig in ipa_signals):
        return "ipadapter", "ipadapter", "Universal", 0.92

    # ── VAE ────────────────────────────────────────────────────────────────
    vae_signals = [
        "encoder.down.0.block.0", "decoder.up.0.block.0",
        "first_stage_model.encoder", "encoder.conv_in",
        "quant_conv.weight", "post_quant_conv.weight",
    ]
    # VAE puro: tiene encoder+decoder pero NO diffusion_model
    if any(any(sig in k for k in keys) for sig in vae_signals):
        if not any("diffusion_model" in k or "model.diffusion" in k for k in keys):
            return "vae", "vae", "Universal", 0.90

    # ── Text encoder / CLIP ────────────────────────────────────────────────
    clip_signals = [
        "transformer.text_model.encoder.layers",
        "text_model.encoder.layers",
        "encoder.layers.0.self_attn",
        "model.embed_tokens",           # T5 / LLM encoders
        "language_model.model.embed",   # Qwen
        "encoder.block.0.layer.",       # T5 formato HuggingFace
        "shared.weight",                # T5 embedding compartido
    ]
    if any(any(sig in k for k in keys) for sig in clip_signals):
        if not any("diffusion_model" in k for k in keys):
            return "clip", "clip", "Universal", 0.88

    # ── Upscaler (RRDB / HAT / OmniSR) ────────────────────────────────────
    upscale_signals = [
        "model.0.sub.0.RDB", "body.0.rdb", "RRDB_trunk.",
        "relative_position_bias_table", "window_attn.proj",
        "conv_after_body", "upsample.0.weight",
    ]
    if any(any(sig in k for k in keys) for sig in upscale_signals):
        return "upscaler", "upscale_models", "Universal", 0.88

    # ── Checkpoint completo (SD1.5 / SDXL / SD3) ──────────────────────────
    # Evaluado ANTES del diffusion_model porque un checkpoint SDXL contiene
    # double_blocks en su diffusion_model embebido.
    ckpt_signals = [
        "model.diffusion_model.input_blocks",  # SD1.5
        "conditioner.embedders.0",             # SDXL
        "model.diffusion_model.joint_blocks",  # SD3
        "model.diffusion_model.double_blocks", # Flux checkpoint completo
    ]
    if any(any(sig in k for k in keys) for sig in ckpt_signals):
        arch = _detect_base_arch_from_keys(keys)
        return "checkpoint", "checkpoints", arch, 0.90

    # ── Diffusion model standalone (Flux, Wan, UNET sin checkpoint) ────────
    diffusion_signals = [
        "double_blocks.", "single_blocks.",          # Flux standalone
        "transformer.double_blocks",
        "blocks.0.attn.",                            # Wan / genérico DiT
    ]
    if any(any(sig in k for k in keys) for sig in diffusion_signals):
        arch = _detect_base_arch_from_keys(keys)
        return "diffusion_model", "diffusion_models", arch, 0.90

    return "unknown", "checkpoints", "Unknown", 0.30


def _detect_lora_arch(keys: set[str], metadata: dict) -> str:
    if any("double_blocks" in k or "single_blocks" in k for k in keys):
        return "Flux.1"
    if any("lora_te1_" in k or "lora_te2_" in k for k in keys):
        return "SDXL / Pony / Illustrious"
    if any("transformer_blocks" in k for k in keys):
        return "SD 3.x"
    if any("lora_unet_down_blocks_" in k for k in keys):
        if any("lora_te_text_model_" in k for k in keys):
            return "SD 1.5"
        return "SD 1.5 (UNet)"
    ver = metadata.get("ss_base_model_version", "").lower()
    if "sdxl" in ver:
        return "SDXL / Pony / Illustrious"
    if "sd-1" in ver:
        return "SD 1.5"
    if "flux" in ver:
        return "Flux.1"
    return "Unknown"


def _detect_base_arch_from_keys(keys: set[str]) -> str:
    if any("double_blocks" in k for k in keys):
        return "Flux.1"
    if any("conditioner.embedders" in k for k in keys):
        return "SDXL"
    if any("joint_blocks" in k for k in keys):
        return "SD 3.x"
    if any("input_blocks.0.0" in k for k in keys):
        return "SD 1.5"
    return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Heurísticas por extensión y tamaño (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _classify_by_extension(path: str, size_mb: float) -> tuple[str, str, str, float]:
    ext = Path(path).suffix.lower()
    name = Path(path).stem.lower()

    if ext == ".gguf":
        return "diffusion_model", "diffusion_models", "Unknown", 0.70

    if ext in (".ckpt", ".pt"):
        if size_mb < 200:
            if "vae" in name:
                return "vae", "vae", "Unknown", 0.65
            return "embedding", "embeddings", "Unknown", 0.55
        return "checkpoint", "checkpoints", "Unknown", 0.60

    if ext == ".bin":
        if "vae" in name:
            return "vae", "vae", "Unknown", 0.65
        if "unet" in name:
            return "diffusion_model", "diffusion_models", "Unknown", 0.65
        return "unknown", "checkpoints", "Unknown", 0.30

    return "unknown", "checkpoints", "Unknown", 0.20


# ─────────────────────────────────────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────────────────────────────────────

def classify(file_path: str) -> ModelClassification:
    """
    Clasifica un archivo de modelo. No carga pesos en memoria.
    """
    p = Path(file_path)
    if not p.exists():
        return ModelClassification(
            file_path=file_path, model_type="unknown",
            suggested_category="checkpoints", arch="Unknown",
            confidence=0.0, source="extension", size_mb=0.0,
            error="Archivo no encontrado",
        )

    size_mb = p.stat().st_size / (1024 * 1024)
    ext = p.suffix.lower()

    if ext == ".safetensors":
        try:
            header, metadata = _read_safetensors_header(file_path)
            keys = set(header.keys())
            model_type, category, arch, conf = _classify_by_keys(keys, metadata)
            return ModelClassification(
                file_path=file_path, model_type=model_type,
                suggested_category=category, arch=arch,
                confidence=conf, source="header", size_mb=size_mb,
            )
        except Exception as e:
            # Fallback a heurísticas si el header falla
            model_type, category, arch, conf = _classify_by_extension(file_path, size_mb)
            return ModelClassification(
                file_path=file_path, model_type=model_type,
                suggested_category=category, arch=arch,
                confidence=conf * 0.5, source="extension", size_mb=size_mb,
                error=f"Header fallido: {e}",
            )

    model_type, category, arch, conf = _classify_by_extension(file_path, size_mb)
    return ModelClassification(
        file_path=file_path, model_type=model_type,
        suggested_category=category, arch=arch,
        confidence=conf, source="extension", size_mb=size_mb,
    )


def classify_with_civitai(file_path: str, civitai_data: dict) -> ModelClassification:
    """
    Refina la clasificación con datos de Civitai (type + baseModel).
    Si Civitai conoce el archivo, la confianza sube a 1.0.
    """
    base = classify(file_path)
    if not civitai_data:
        return base

    CIVITAI_TYPE_MAP = {
        "Checkpoint":        ("checkpoint",    "checkpoints"),
        "LORA":              ("lora",          "loras"),
        "LoCon":             ("lora",          "loras"),
        "DoRA":              ("lora",          "loras"),
        "TextualInversion":  ("embedding",     "embeddings"),
        "VAE":               ("vae",           "vae"),
        "ControlNet":        ("controlnet",    "controlnet"),
        "Upscaler":          ("upscaler",      "upscale_models"),
        "AestheticGradient": ("embedding",     "embeddings"),
        "Hypernetwork":      ("hypernetwork",  "hypernetworks"),
        "Poses":             ("unknown",       "checkpoints"),
    }

    model_data = civitai_data.get("model", {})
    civ_type = model_data.get("type", "")
    civ_arch = civitai_data.get("baseModel", base.arch)

    model_type, category = CIVITAI_TYPE_MAP.get(civ_type, (base.model_type, base.suggested_category))

    return ModelClassification(
        file_path=base.file_path,
        model_type=model_type,
        suggested_category=category,
        arch=civ_arch or base.arch,
        confidence=1.0,
        source="civitai",
        size_mb=base.size_mb,
        error=base.error,
    )
