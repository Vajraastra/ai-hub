"""
Control del filtro NSFW de ReActor desde nuestro módulo.

El nodo ReActor (patcheado) lee nsfw_filter.json en cada análisis y escribe el
último score en nsfw_last.json. Acá leemos/escribimos esa config y exponemos el
último score para que la UI muestre por qué se bloqueó (o no) una foto.

Semántica del umbral: el filtro bloquea si score_nsfw > threshold. Por eso un
umbral MÁS ALTO = MÁS permisivo. 1.0 nunca bloquea; enabled=False lo apaga del
todo (ni siquiera analiza).
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CFG_FILE = DATA_DIR / "nsfw_filter.json"
LAST_FILE = DATA_DIR / "nsfw_last.json"

DEFAULT_THRESHOLD = 0.979
MIN_THRESHOLD = 0.50   # por debajo de esto el filtro es puro estorbo


def get_config() -> dict:
    try:
        cfg = json.loads(CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "threshold": float(cfg.get("threshold", DEFAULT_THRESHOLD)),
    }


def set_config(enabled: bool, threshold: float) -> dict:
    threshold = max(MIN_THRESHOLD, min(1.0, float(threshold)))
    cfg = {"enabled": bool(enabled), "threshold": threshold}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CFG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def last_score() -> dict:
    """Último análisis reportado por el nodo ({} si aún no corrió ninguno)."""
    try:
        return json.loads(LAST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
