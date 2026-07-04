"""
Sets de validación fijos — el cimiento no negociable (handoff §5).

Un set = lista de prompts con seed propia + parámetros de sampling comunes.
Los parámetros solo son editables ANTES del primer bloqueo; después el set es
inmutable (solo se puede clonar a un set nuevo). Toda comparación antes/después
de un merge se hace regenerando el MISMO set bloqueado.

Categorías de prompt requeridas por set:
  - "target"  : dominio objetivo (render 3D multiestilo)
  - "general" : fuera de dominio (medir pérdida de generalidad)
  - "stress"  : casos que rompen modelos (manos, multi-personaje, texto, poses)

Persistencia: JSON editable en data/validation_sets/ (decisión 2026-07-04).

Implementación: Fase 2 (crear/bloquear/regenerar + grid de resultados).
"""
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "validation_sets"

CATEGORIES = ["target", "general", "stress"]


class ValidationSet:
    """Pendiente de implementar (Fase 2).

    Contrato previsto:
      ValidationSet.create(name, arch, prompts, sampling) -> ValidationSet
      ValidationSet.load(name) -> ValidationSet
      .lock()                  # bloquea params; a partir de aquí, inmutable
      .run(comfy, model) ->    # regenera el set completo contra un modelo
          lista de resultados {prompt_id, seed, image_path}
    """

    def __init__(self):
        raise NotImplementedError("Fase 2 — ver TASKS.md (Forge Lab)")
