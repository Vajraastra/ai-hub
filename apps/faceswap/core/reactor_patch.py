"""
Auto-reparación del parche del filtro NSFW de ReActor.

El control del umbral vive como parche sobre reactor_sfw.py (un archivo del nodo
externo ReActor). Una actualización de ReActor puede pisarlo y devolver el
comportamiento roto (cuadro negro mudo, umbral fijo 0.979) justo en medio de una
tanda de edición con deadline corto. Para que eso nunca deje al operador sin
poder trabajar, guardamos una copia canónica del archivo parcheado y la
re-aplicamos automáticamente al abrir el módulo (idempotente) — y hay un botón
manual "Reparar filtro" para forzarla.
"""
import shutil
from pathlib import Path

_HERE = Path(__file__).resolve()
_APP = _HERE.parents[1]                       # apps/faceswap
_TARGET = (_APP.parent / "comfyui" / "custom_nodes" / "ComfyUI-ReActor"
           / "scripts" / "reactor_sfw.py")
_CANON = _APP / "patches" / "reactor_sfw.patched.py"
_BACKUP = _TARGET.with_name("reactor_sfw.py.aihub-bak")
_MARKER = "Filtro NSFW modulable (parche AI Hub)"


def is_installed() -> bool:
    return _TARGET.exists()


def is_patched() -> bool:
    try:
        return _MARKER in _TARGET.read_text(encoding="utf-8")
    except Exception:
        return False


def ensure_patch(force: bool = False) -> dict:
    """Aplica el parche si falta (o si force=True). Devuelve el resultado.
    Nunca lanza: si algo falla, reporta el motivo y el módulo sigue funcionando
    (con el filtro en su comportamiento por defecto)."""
    try:
        if not _TARGET.exists():
            return {"ok": False, "action": "skip", "reason": "ReActor no instalado"}
        if not _CANON.exists():
            return {"ok": False, "action": "skip", "reason": "falta la copia canónica del parche"}
        if is_patched() and not force:
            return {"ok": True, "action": "already-patched"}
        # Respaldar el original la primera vez (por si hace falta revertir).
        if not _BACKUP.exists() and not is_patched():
            try:
                shutil.copy2(_TARGET, _BACKUP)
            except Exception:
                pass
        shutil.copy2(_CANON, _TARGET)
        return {"ok": True, "action": "patched",
                "note": "reiniciá ComfyUI para que tome el parche"}
    except Exception as e:
        return {"ok": False, "action": "error", "reason": str(e)}


def status() -> dict:
    return {"installed": is_installed(), "patched": is_patched(),
            "backup": _BACKUP.exists()}
