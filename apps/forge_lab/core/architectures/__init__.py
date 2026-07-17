"""
Registro de adaptadores de arquitectura.

`zimage` y `sdxl` implementados; `ideogram4` se añadirá cuando toque,
implementando la misma interfaz ArchAdapter.
"""
from .base import ArchAdapter, BlockGroup, SamplingDefaults
from .zimage import ZImageAdapter
from .sdxl import SdxlAdapter
from .anima import AnimaAdapter

_ADAPTERS: dict[str, ArchAdapter] = {
    a.name: a for a in [ZImageAdapter(), SdxlAdapter(), AnimaAdapter()]
}

SUPPORTED_ARCHITECTURES = list(_ADAPTERS)


def get_adapter(name: str) -> ArchAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise ValueError(f"Arquitectura no soportada: {name!r} "
                         f"(disponibles: {SUPPORTED_ARCHITECTURES})") from None
