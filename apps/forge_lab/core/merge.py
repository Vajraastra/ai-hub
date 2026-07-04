"""
Orquestación de merges vía nodos ComfyUI (comfyUI-Realtime-Lora).

Reglas duras (handoff §4):
  - Merge SIEMPRE sobre De-Turbo, nunca sobre el Turbo destilado.
  - Los merges por bloques corren en CPU/RAM (64 GB), no en GPU.
  - Las zonas de adapter.forbidden_zones() no se tocan sin override explícito.
  - Fuerza baja (0.3–0.7) + más pasos > fuerza alta + pocos pasos.
  - TIES/DARE cuando se acumulen merges (no opcional a largo plazo).

Nodos disponibles (instalados 2026-07-04, verificar vía API antes de usar):
  ZImageSelectiveLoRALoader — carga selectiva por capa (macro/medio ON/OFF)
  LoRALoaderWithAnalysis    — análisis de impacto por capa (mapa de bloques)
  ScheduledLoRALoader       — strength scheduling con keyframes

Implementación: Fase 3 (merge completo) y Fase 4 (block merge macro).
"""


class MergeOrchestrator:
    """Pendiente de implementar (Fases 3/4).

    Contrato previsto:
      .merge_lora(base_ckpt, lora, strength, blocks=None) -> nuevo checkpoint
        blocks=None  → merge completo (Fase 3)
        blocks=[...] → solo los bloques indicados (Fase 4)
    """

    def __init__(self):
        raise NotImplementedError("Fases 3/4 — ver TASKS.md (Forge Lab)")
