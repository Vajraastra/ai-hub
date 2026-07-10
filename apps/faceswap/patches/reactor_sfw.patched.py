from transformers import pipeline
from PIL import Image
import io
import json
import time
import logging
import os
from pathlib import Path
import comfy.model_management as model_management
from reactor_utils import download
from scripts.reactor_logger import logger

MODEL_EXISTS = False

# ── Filtro NSFW modulable (parche AI Hub) ────────────────────────────────────
# El umbral del filtro estaba hardcodeado (0.979) y una imagen marcada se
# reemplazaba por un cuadro negro sin explicación — falsos positivos en
# fotografía legítima (shorts deportivos, trajes de baño, hombros descubiertos)
# hacían el swap inservible. Ahora el módulo apps/faceswap controla el umbral en
# vivo por un archivo de config, y cada análisis reporta el score real para que
# el usuario calibre el slider en vez de adivinar.
_FS_DATA = Path(__file__).resolve().parents[4] / "faceswap" / "data"
_NSFW_CFG = _FS_DATA / "nsfw_filter.json"
_NSFW_LAST = _FS_DATA / "nsfw_last.json"
_DEFAULT_THRESHOLD = 0.979


def _nsfw_config():
    """(enabled, threshold) desde la config del módulo; defaults si falta/rota."""
    try:
        cfg = json.loads(_NSFW_CFG.read_text(encoding="utf-8"))
        enabled = bool(cfg.get("enabled", True))
        thr = float(cfg.get("threshold", _DEFAULT_THRESHOLD))
        return enabled, max(0.0, min(1.0, thr))
    except Exception:
        return True, _DEFAULT_THRESHOLD


def _report_score(score: float, blocked: bool):
    """Deja el último score en disco para que la UI lo muestre (transparencia)."""
    try:
        _FS_DATA.mkdir(parents=True, exist_ok=True)
        _NSFW_LAST.write_text(json.dumps(
            {"score": round(float(score), 4), "blocked": blocked, "ts": time.time()}),
            encoding="utf-8")
    except Exception:
        pass

def ensure_nsfw_model(nsfwdet_model_path):
    """Download NSFW detection model if it doesn't exist"""
    global MODEL_EXISTS
    downloaded = 0
    nd_urls = [
        "https://huggingface.co/AdamCodd/vit-base-nsfw-detector/resolve/main/config.json",
        "https://huggingface.co/AdamCodd/vit-base-nsfw-detector/resolve/main/model.safetensors",
        "https://huggingface.co/AdamCodd/vit-base-nsfw-detector/resolve/main/preprocessor_config.json",
    ]
    for model_url in nd_urls:
        model_name = os.path.basename(model_url)
        model_path = os.path.join(nsfwdet_model_path, model_name)
        if not os.path.exists(model_path):
            if not os.path.exists(nsfwdet_model_path):
                os.makedirs(nsfwdet_model_path)
            download(model_url, model_path, model_name)
        if os.path.exists(model_path):
            downloaded += 1
    MODEL_EXISTS = True if downloaded == 3 else False
    return MODEL_EXISTS

logging.getLogger("transformers").setLevel(logging.ERROR)

def nsfw_image(img_data, model_path: str):
    enabled, threshold = _nsfw_config()
    if not enabled:
        # Filtro desactivado desde el módulo: no analiza, no bloquea.
        _report_score(0.0, False)
        return False
    if not MODEL_EXISTS:
        logger.status("Ensuring NSFW detection model exists...")
        if not ensure_nsfw_model(model_path):
            return True
    device = model_management.get_torch_device()
    with Image.open(io.BytesIO(img_data)) as img:
        if "cpu" in str(device):
            predict = pipeline("image-classification", model=model_path)
        else:
            device_id = 0
            if "cuda" in str(device):
                device_id = int(str(device).split(":")[1])
            predict = pipeline("image-classification", model=model_path, device=device_id)
        result = predict(img)
        # score de la clase "nsfw" (0..1), venga o no primero en el ranking
        nsfw_score = next((r["score"] for r in result if r["label"] == "nsfw"), 0.0)
        blocked = nsfw_score > threshold
        _report_score(nsfw_score, blocked)
        logger.status(f'NSFW score={nsfw_score:.4f} (umbral={threshold:.3f}) '
                      f'-> {"BLOQUEADO" if blocked else "ok"}')
        return blocked
