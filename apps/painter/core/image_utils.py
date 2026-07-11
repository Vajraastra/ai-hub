"""
Utilidades de imagen para el Painter.
Conversiones b64 ↔ PIL, validación de resolución, resize, parsing de tokens LoRA.
"""
import base64
import random
import re
from io import BytesIO
from pathlib import Path
from PIL import Image

# ── LoRA token parsing ────────────────────────────────────────────────────────

# Reconoce cualquier <lora:...> independientemente del formato
_LORA_RE = re.compile(r'<lora:([^>]+)>', re.IGNORECASE)


def parse_lora_tokens(prompt: str) -> tuple[str, list[dict]]:
    """
    Extrae tokens <lora:nombre:strength> del prompt.
    Devuelve (prompt_limpio, [{"name": str, "strength": float}]).
    El prompt limpio tiene los tokens removidos y comas/espacios sobrantes corregidos.
    """
    loras: list[dict] = []

    def _sub(m: re.Match) -> str:
        parts    = m.group(1).split(':', 1)
        name     = parts[0].strip()
        try:
            strength = float(parts[1].strip()) if len(parts) > 1 else 1.0
        except ValueError:
            strength = 1.0
        loras.append({"name": name, "strength": strength})
        return ""

    cleaned = _LORA_RE.sub(_sub, prompt)
    cleaned = re.sub(r',\s*,', ',', cleaned)          # comas dobles
    cleaned = re.sub(r'(^[\s,]+|[\s,]+$)', '', cleaned)  # bordes
    return cleaned, loras


def resolve_lora_name(stem: str, available: list[str]) -> str | None:
    """
    Resuelve un nombre de LoRA (con o sin extensión) contra la lista disponible.
    Orden de búsqueda: exacto → stem + extensión común → stem case-insensitive.
    """
    if stem in available:
        return stem
    for ext in ('.safetensors', '.pt', '.ckpt'):
        if stem + ext in available:
            return stem + ext
    stem_lower = stem.lower()
    for name in available:
        if Path(name).stem.lower() == stem_lower:
            return name
    return None

SUPPORTED_RESOLUTIONS = [512, 768, 1024, 1280, 1536, 2048]
MAX_HISTORY = 20


# ── Conversiones ─────────────────────────────────────────────────────────────

def b64_to_pil(b64: str) -> Image.Image:
    data = base64.b64decode(b64)
    return Image.open(BytesIO(data)).convert("RGB")


def pil_to_b64(img: Image.Image, fmt: str = "PNG") -> str:
    buf = BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)


def pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── Validación ────────────────────────────────────────────────────────────────

def validate_resolution(w: int, h: int):
    """Lanza ValueError si las dimensiones no son válidas para modelos de difusión."""
    if w % 8 != 0 or h % 8 != 0:
        raise ValueError(f"Las dimensiones deben ser múltiplos de 8 ({w}x{h})")
    if w < 512 or h < 512:
        raise ValueError(f"Dimensión mínima: 512px ({w}x{h})")
    if w > 2048 or h > 2048:
        raise ValueError(f"Dimensión máxima: 2048px ({w}x{h})")


def resolve_seed(seed: int) -> int:
    """Retorna un seed aleatorio si el valor es -1."""
    return random.randint(0, 2**32 - 1) if seed == -1 else seed


# ── Transformaciones ──────────────────────────────────────────────────────────

def resize_to_fit(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Redimensiona la imagen para que quepa en target_w x target_h
    preservando el aspect ratio. No agranda imágenes más pequeñas.
    """
    img_w, img_h = img.size
    if img_w <= target_w and img_h <= target_h:
        return img
    ratio = min(target_w / img_w, target_h / img_h)
    new_w = int(img_w * ratio)
    new_h = int(img_h * ratio)
    # ajustar a múltiplo de 8
    new_w = (new_w // 8) * 8
    new_h = (new_h // 8) * 8
    return img.resize((new_w, new_h), Image.LANCZOS)


def snap_to_multiple_of_8(value: int) -> int:
    return (value // 8) * 8
