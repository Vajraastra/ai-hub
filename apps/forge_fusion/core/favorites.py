"""
Favoritas del picker — LoRAs y checkpoints marcados por el usuario, persistidos
en data/favorites.json.

Transversal a arquitectura: la clave es el identificador estable de cada tipo
(ruta relativa posix del LoRA; nombre del checkpoint del registro). El picker
las sube al principio del orden y muestra la estrella marcada.
"""
import json
from pathlib import Path

_DATA = Path(__file__).parent.parent / "data"
_FILE = _DATA / "favorites.json"
_KINDS = ("loras", "checkpoints")


class FavoritesError(Exception):
    pass


def load() -> dict[str, list[str]]:
    """{'loras': [...], 'checkpoints': [...]}; tolerante a fichero ausente o
    corrupto (no es motivo para tumbar el picker)."""
    d = {}
    if _FILE.exists():
        try:
            d = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    return {k: list(d.get(k, [])) for k in _KINDS}


def ids(kind: str) -> set[str]:
    return set(load().get(kind, []))


def _save(d: dict) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def toggle(kind: str, item_id: str, on: bool | None = None) -> bool:
    """Marca/desmarca una favorita. `on=None` alterna; True/False fuerza el
    estado. Devuelve el estado final (True = favorita)."""
    if kind not in _KINDS:
        raise FavoritesError(f"tipo de favorita desconocido: {kind!r}")
    if not item_id:
        raise FavoritesError("id de favorita vacío")
    d = load()
    lst = d[kind]
    has = item_id in lst
    want = (not has) if on is None else bool(on)
    if want and not has:
        lst.append(item_id)
    elif not want and has:
        lst.remove(item_id)
    _save(d)
    return want
