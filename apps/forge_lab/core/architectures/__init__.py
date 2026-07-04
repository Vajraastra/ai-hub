"""
Registro de adaptadores de arquitectura.

Solo `zimage` está implementado; `sdxl` e `ideogram4` se añadirán aquí cuando
toque, implementando la misma interfaz ArchAdapter.
"""
from .base import ArchAdapter, BlockGroup, ModelFiles, SamplingDefaults
from .zimage import ZImageAdapter

_ADAPTERS: dict[str, ArchAdapter] = {
    a.name: a for a in [ZImageAdapter()]
}

SUPPORTED_ARCHITECTURES = list(_ADAPTERS)


def get_adapter(name: str) -> ArchAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise ValueError(f"Arquitectura no soportada: {name!r} "
                         f"(disponibles: {SUPPORTED_ARCHITECTURES})") from None
