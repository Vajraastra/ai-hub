"""
Detección del banner de bloqueo del filtro de seguridad de Ideogram 4.

Cuando el filtro (aprendido, quemado en los pesos) se dispara, el modelo GENERA
un cuadro gris casi uniforme con un texto de aviso deforme, en vez de la imagen.
No es un swap de fichero: es una salida del propio modelo. Esta utilidad analiza
el PNG resultante y estima si es ese banner, para avisar y no dar por buena una
generación bloqueada.

La vía principal del módulo (JSON + bounding boxes) evita el filtro casi siempre;
esto es una red de seguridad post-render.

Idea y enfoque tomados del nodo ComfyUI-BlockGuard de INSANEF00L / Dragon7108
(github.com/Dragon7108/ComfyUI-BlockGuard), reimplementado aquí como análisis de
imagen para no depender de un nodo custom. Crédito a sus autores (se muestra en
la interfaz del módulo).
"""
import io

import numpy as np
from PIL import Image


def analyze(png_bytes: bytes) -> dict:
    """Métricas de 'grisura' de la imagen. El banner de bloqueo es un gris casi
    uniforme y desaturado."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    # submuestreo para rapidez
    img.thumbnail((256, 256))
    arr = np.asarray(img, dtype=np.float32)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]

    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    # división segura: mx==0 (negro puro) → sat 0 sin warning de /0
    sat = np.divide(mx - mn, mx, out=np.zeros_like(mx), where=mx > 0)  # HSV 0-1
    mean_sat = float(sat.mean())

    # fracción de píxeles "grises": canales muy próximos entre sí
    spread = mx - mn
    gray_frac = float((spread < 18).mean())

    brightness = arr.mean(axis=2)
    bright_std = float(brightness.std())
    bright_mean = float(brightness.mean())

    return {
        "mean_saturation": round(mean_sat, 4),
        "gray_fraction": round(gray_frac, 4),
        "brightness_std": round(bright_std, 2),
        "brightness_mean": round(bright_mean, 2),
    }


def is_safety_block(png_bytes: bytes) -> tuple[bool, dict]:
    """Estima si el PNG es el banner de bloqueo.

    Firma típica del banner: muy desaturado, gran mayoría de píxeles grises y
    brillo concentrado en un gris medio (poca dispersión). Umbrales conservadores
    para minimizar falsos positivos sobre imágenes reales monocromas/B&N.
    """
    m = analyze(png_bytes)
    blocked = (
        m["mean_saturation"] < 0.06
        and m["gray_fraction"] > 0.90
        and m["brightness_std"] < 45.0
        and 90.0 < m["brightness_mean"] < 200.0
    )
    return blocked, m
