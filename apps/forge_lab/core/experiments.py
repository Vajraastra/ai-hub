"""
Registro de experimentos: linaje de checkpoints + qué se tocó + resultados.

Cada paso de derivación guarda: checkpoint padre, operación (merge de LoRA,
block merge, edición), parámetros exactos, resultados del set de validación y
notas del usuario. Este registro es la materia prima de los tooltips y del
diccionario de traducción; sin él no hay curva de degradación.

Persistencia: SQLite en data/experiments.sqlite (decisión 2026-07-04);
las imágenes van a disco, la BD guarda rutas.

Implementación: Fases 3 y 5.
"""
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "experiments.sqlite"


class ExperimentLog:
    """Pendiente de implementar (Fases 3/5).

    Contrato previsto:
      .record_step(parent_ckpt, operation, params, results, notes) -> step_id
      .lineage(ckpt) -> lista de pasos desde el modelo base
      .degradation_curve(set_name) -> [(n_merges, metricas)] (Fase 6)
    """

    def __init__(self):
        raise NotImplementedError("Fases 3/5 — ver TASKS.md (Forge Lab)")
