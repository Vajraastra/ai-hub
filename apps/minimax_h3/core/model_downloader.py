"""
Descargador de pesos MiniMax H3 desde HuggingFace + registro de qué archivo va
dónde.

Los modelos base (DiT/encoder/VAEs/turbo LoRA) suman ~36 GB y quedan FUERA de
la instalación automática del módulo — se bajan bajo demanda cuando el usuario
pulsa "Descargar modelos" en la UI (decisión del usuario, s94): una instalación
fresca del hub no debe arrastrar esos GB sin que el cliente lo pida.

Los LoRAs de decensurado (`Hearmeman/minimax-h3-loras`) son la excepción: están
en riesgo real de takedown (ver TASKS.md, s94) así que el hub los preserva
descargándolos una vez y quedan versionados como parte del stock de modelos del
cliente, no gated detrás del botón — si desaparecen de HuggingFace mañana, ya
los tenemos.

Descarga por streaming HTTP con reintento + resume (Range) porque un archivo de
10-16 GB puede colgarse a media descarga sin devolver error (mismo riesgo que
documenta la regla B3 de las reglas globales para wheels de pip, aplicado aquí
a pesos de modelo en vez de dependencias).
"""
import time
import requests
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator

_CHUNK = 8 * 1024 * 1024      # 8 MiB
_CONNECT_TIMEOUT = 15
_READ_TIMEOUT = 60            # sin bytes nuevos en 60s -> se considera colgado
_MAX_RETRIES = 3


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class ModelSpec:
    key: str            # id estable para status/UI, p.ej. "dit"
    label: str           # nombre humano para la UI
    repo_id: str
    filename: str         # ruta dentro del repo HF (puede llevar subcarpetas)
    dest_dir: str          # subcarpeta bajo <models_root>, p.ej. "diffusion_models"
    dest_name: str         # nombre final del archivo en disco
    size_bytes: int         # tamaño aproximado (verificado a mano, ago 2026) —
                            # solo para mostrar progreso/ETA antes del primer byte


def hf_url(repo_id: str, filename: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"


# ── Registro de modelos ──────────────────────────────────────────────────────
# Verificado con fuentes en s94 (ver TASKS.md). El DiT fue NVFP4 podado hasta
# s96: se eligió por soporte de hardware nativo en la 5060 Ti (Blackwell) y
# por ser la cuantización más chica. Se detectó en s95/s96 que esa NVFP4 es
# la causa raíz de un problema de calidad "VHS"/textura fibrosa — confirmado
# con comparación en vivo de las 3 variantes del DiT (NVFP4 / INT8 ConvRot /
# Mixed INT4-INT8 ConvRot), con y sin turbo LoRA. Veredicto del usuario (s96):
# NVFP4 inusable; con el turbo LoRA activado, INT8 y Mixed tienen ghosting
# fuerte; SIN turbo (20 pasos) el ghosting desaparece casi del todo y la
# diferencia es "abrumadora" a favor de la calidad — e INT8 sin turbo es
# claramente mejor que Mixed sin turbo, ni se plantea usar Mixed. Decisión:
# **DiT = INT8 ConvRot, SIN turbo por default** (`GenParams.turbo=False`,
# ya era el default de la dataclass; lo que cambió acá es qué DiT baja el
# gate). NVFP4 (`FL2V_NVFP4_DEPRECATED`), Mixed (`FL2V_MIXED_REJECTED`) y el
# encoder INT8 ConvRot experimental fueron BORRADOS del disco en s98 con
# aprobación del usuario (~52 GB recuperados) — las listas quedan solo como
# referencia de re-descarga por si algún día hicieran falta.

GATED_MODELS: list[ModelSpec] = [
    ModelSpec(
        key="dit", label="DiT (motor de difusión, INT8 ConvRot)",
        repo_id="Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot",
        filename="MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors",
        dest_dir="diffusion_models",
        dest_name="minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        size_bytes=21_000_000_000,
    ),
    ModelSpec(
        key="encoder", label="Text encoder (Qwen3-VL-32B, NVFP4/AWQ)",
        repo_id="Comfy-Org/MiniMax-H3",
        filename="text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        dest_dir="text_encoders",
        dest_name="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        size_bytes=16_868_000_000,
    ),
    ModelSpec(
        key="video_vae", label="VAE de video",
        repo_id="Comfy-Org/MiniMax-H3",
        filename="vae/minimax_h3_video_vae_fp16.safetensors",
        dest_dir="vae",
        dest_name="minimax_h3_video_vae_fp16.safetensors",
        size_bytes=5_593_000_000,
    ),
    ModelSpec(
        key="audio_vae", label="VAE de audio",
        repo_id="Comfy-Org/MiniMax-H3",
        filename="vae/minimax_h3_audio_vae_fp32.safetensors",
        dest_dir="vae",
        dest_name="minimax_h3_audio_vae_fp32.safetensors",
        size_bytes=650_000_000,
    ),
    ModelSpec(
        key="turbo_lora", label="Turbo LoRA (8 pasos, oficial lightx2v)",
        repo_id="lightx2v/Minimax-h3-Turbo",
        filename="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        dest_dir="loras/h3",
        dest_name="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        size_bytes=1_956_193_000,
    ),
]

# Ref2V (ref2va) — DiT propio + turbo propio, encoder/VAEs compartidos con
# T2V/I2V/FL2V (no se repiten acá). Prototipo s95: lista separada de
# GATED_MODELS porque es una vía experimental, no parte del flujo default.
REF2V_MODELS: list[ModelSpec] = [
    ModelSpec(
        key="ref2v_dit", label="DiT Ref2V (INT8 ConvRot podado)",
        repo_id="Comfy-Org/MiniMax-H3",
        filename="diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        dest_dir="diffusion_models",
        dest_name="minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        size_bytes=20_970_379_616,
    ),
    ModelSpec(
        key="ref2v_turbo", label="Turbo LoRA Ref2V (4 pasos, oficial)",
        repo_id="Comfy-Org/MiniMax-H3",
        filename="loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        dest_dir="loras/h3",
        dest_name="minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        size_bytes=1_956_193_000,
    ),
]

# FL2VA NVFP4 (s96, DEPRECADA — borrada del disco en s98) — era el DiT de GATED_MODELS hasta
# que se identificó como causa raíz del problema de calidad "VHS". Lista solo como referencia
# de re-descarga; no participa de ningún flujo.
FL2V_NVFP4_DEPRECATED: list[ModelSpec] = [
    ModelSpec(
        key="fl2v_nvfp4_dit", label="DiT FL2VA (NVFP4, deprecado por calidad — s96)",
        repo_id="Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot",
        filename="MiniMax_H3_FL2VA_pruned_nvfp4.safetensors",
        dest_dir="diffusion_models",
        dest_name="minimax_h3_fl2va_pruned_nvfp4.safetensors",
        size_bytes=12_528_636_865,
    ),
]

# FL2VA Mixed INT4/INT8 Convrot (s96, RECHAZADA) — probada como alternativa más chica a INT8
# puro (15.9GB vs 21GB). Comparada en vivo con/sin turbo contra INT8 e NVFP4: sin turbo la
# diferencia de calidad Mixed-vs-INT8 es "abrumadora" a favor de INT8 (veredicto del usuario) —
# no se usa para nada. Borrada del disco en s98; lista solo como referencia de re-descarga.
FL2V_MIXED_REJECTED: list[ModelSpec] = [
    ModelSpec(
        key="fl2v_mixed_dit", label="DiT FL2VA (Mixed INT4/INT8 ConvRot, rechazada por calidad — s96)",
        repo_id="Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot",
        filename="MiniMax_H3_FL2VA_pruned_mixed_int4_int8_convrot.safetensors",
        dest_dir="diffusion_models",
        dest_name="minimax_h3_fl2va_pruned_mixed_int4_int8_convrot.safetensors",
        size_bytes=15_900_000_000,
    ),
]

# Encoder Qwen3-VL-32B en INT8 ConvRot (s96) — probando si la cuantización agresiva del encoder
# (NVFP4/AWQ, el de GATED_MODELS) contribuye a la pérdida de identidad en Ref2V. IMPORTANTE:
# se confirmó en el repo oficial (github.com/MiniMax-AI/MiniMax-H3) que la fidelidad de
# identidad de las plataformas comerciales depende de "H3-Context-IR", un sistema de
# preprocesamiento HOSTEADO que MiniMax nunca liberó ("critical to the quality of the final
# output") — este test probablemente da como máximo una mejora marginal, no cierra esa brecha
# arquitectónica. Descartado: la pérdida de identidad resultó ser un bug de plomería (s97),
# no del encoder. Borrado del disco en s98; lista solo como referencia de re-descarga.
TEXT_ENCODER_INT8_CONVROT: list[ModelSpec] = [
    ModelSpec(
        key="encoder_int8_convrot", label="Text encoder Qwen3-VL-32B (INT8 ConvRot, candidato)",
        repo_id="Comfy-Org/MiniMax-H3",
        filename="text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        dest_dir="text_encoders",
        dest_name="qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        size_bytes=27_100_000_000,
    ),
]

# Encoder "Heretic" (ablacionado) — investigado y descartado en s93 por análisis de coseno
# comunitario (gap 0.0023, "practicamente no hace nada"), retomado en s95 para confirmarlo
# empiricamente en vez de solo confiar en el analisis de terceros. Lista separada, experimental.
HERETIC_MODELS: list[ModelSpec] = [
    ModelSpec(
        key="heretic_encoder", label="Text encoder Heretic (Qwen3-VL-32B ablacionado, NVFP4)",
        repo_id="sakamakismile/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4",
        filename="qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
        dest_dir="text_encoders",
        dest_name="qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
        size_bytes=15_683_129_587,
    ),
]

# LoRAs de decensurado — preservadas, no gated. Ver módulo docstring.
PRESERVED_LORAS: list[ModelSpec] = [
    ModelSpec(
        key=f"hm_{name.rsplit('.', 1)[0].lower()}", label=name,
        repo_id="Hearmeman/minimax-h3-loras",
        filename=name, dest_dir="loras/h3", dest_name=name,
        size_bytes=size,
    )
    for name, size in [
        ("HMBreasts_085e0750_e40.safetensors", 310_168_344),
        ("HMCumshot_V2.safetensors", 626_294_968),
        ("HMInnie_v1_e50.safetensors", 310_168_344),
        ("HMNSFW_AIO_V2.safetensors", 310_168_344),
        ("HMPenis_v2_e35.safetensors", 310_168_344),
        ("hmpussy_v6_epoch30.safetensors", 626_294_968),
        ("vagassist_e40.safetensors", 310_168_344),
    ]
]


def dest_path(models_root: Path, spec: ModelSpec) -> Path:
    return models_root / spec.dest_dir / spec.dest_name


def missing_specs(models_root: Path, specs: list[ModelSpec]) -> list[ModelSpec]:
    return [s for s in specs if not dest_path(models_root, s).is_file()]


# ── Descarga con resume + reintento ──────────────────────────────────────────

def download_file(url: str, dest: Path, expected_size: int = 0) -> Iterator[dict]:
    """Generador: yields {'bytes_done','bytes_total','pct'} mientras descarga.
    Reanuda desde dest.part si existe (Range). Si el socket se queda sin datos
    nuevos _READ_TIMEOUT s, reintenta desde donde llegó hasta _MAX_RETRIES veces
    en total antes de rendirse."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        resume_from = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        try:
            with requests.get(url, headers=headers, stream=True,
                               timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT)) as r:
                if resume_from and r.status_code == 200:
                    # El servidor ignoró el Range (raro pero posible) — reempezar.
                    resume_from = 0
                    part.unlink(missing_ok=True)
                elif r.status_code not in (200, 206):
                    raise DownloadError(f"HTTP {r.status_code} descargando {url}")
                total = expected_size or (int(r.headers.get("Content-Length", 0)) + resume_from)
                done = resume_from
                with open(part, "ab" if resume_from else "wb") as f:
                    for chunk in r.iter_content(chunk_size=_CHUNK):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        yield {"bytes_done": done, "bytes_total": total or done,
                               "pct": round(done / total * 100, 1) if total else 0.0}
            part.replace(dest)
            return
        except (requests.exceptions.RequestException, OSError) as e:
            last_err = e
            if attempt >= _MAX_RETRIES:
                break
            time.sleep(3 * attempt)
    raise DownloadError(f"descarga fallida tras {_MAX_RETRIES} intentos: {last_err}") from last_err


def download_specs(models_root: Path, specs: list[ModelSpec]) -> Iterator[dict]:
    """Descarga una lista de ModelSpec en orden, saltando los que ya existen.
    Yields eventos {'type': 'file_start'|'progress'|'file_done', ...}."""
    todo = missing_specs(models_root, specs)
    for i, spec in enumerate(todo, start=1):
        dest = dest_path(models_root, spec)
        yield {"type": "file_start", "key": spec.key, "label": spec.label,
               "index": i, "total_files": len(todo), "size_bytes": spec.size_bytes}
        url = hf_url(spec.repo_id, spec.filename)
        for prog in download_file(url, dest, expected_size=spec.size_bytes):
            yield {"type": "progress", "key": spec.key, **prog}
        yield {"type": "file_done", "key": spec.key}
