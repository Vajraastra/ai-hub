"""
Validador de compatibilidad entre LoRAs antes del merge.
"""
from dataclasses import dataclass, field
from typing import Optional
from .detector import LoRAInfo, get_common_rank


# Grupos de arquitecturas compatibles entre sí
_COMPAT_GROUPS: list[set[str]] = [
    {"sd15", "sd15_unet_only"},
    {"sdxl", "sdxl_unet_only"},
    {"flux"},
    {"sd3"},
    {"wan22"},
    {"dit_generic"},
]


@dataclass
class ValidationResult:
    compatible: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rank_mismatch: bool = False
    ranks_by_lora: list[Optional[int]] = field(default_factory=list)
    # rank de cada LoRA
    suggested_target_rank: Optional[int] = None


def validate(loras: list[LoRAInfo]) -> ValidationResult:
    """
    Valida la compatibilidad de un conjunto de LoRAs para merge.
    """
    result = ValidationResult(compatible=True)

    if len(loras) < 2:
        result.errors.append("Se necesitan al menos 2 LoRAs para hacer un merge.")
        result.compatible = False
        return result

    # Verificar errores de carga
    for lora in loras:
        if lora.error:
            result.errors.append(
                f"'{lora.path}' tiene un error: {lora.error}"
            )
            result.compatible = False

    if not result.compatible:
        return result

    # Verificar arquitecturas
    arch_ids = [l.arch_id for l in loras]
    first_group = None
    for group in _COMPAT_GROUPS:
        if arch_ids[0] in group:
            first_group = group
            break

    arch_conflict = False
    for i, arch_id in enumerate(arch_ids[1:], start=1):
        if first_group is None or arch_id not in first_group:
            result.errors.append(
                f"Arquitecturas incompatibles: '{loras[0].arch}' vs '{loras[i].arch}'. "
                "No se pueden fusionar LoRAs de modelos base distintos."
            )
            arch_conflict = True

    if arch_conflict:
        result.compatible = False
        return result

    # Advertencia si uno tiene TE y otro no
    te_flags = [l.has_te for l in loras]
    if len(set(te_flags)) > 1:
        result.warnings.append(
            "Uno de los LoRAs incluye pesos de Text Encoder y el otro no. "
            "El merge funcionará, pero las capas de TE solo estarán en un LoRA."
        )

    # Verificar ranks
    ranks = [get_common_rank(l) for l in loras]
    result.ranks_by_lora = ranks

    unique_ranks = set(r for r in ranks if r is not None)
    if len(unique_ranks) > 1:
        result.rank_mismatch = True
        ranks_str = ", ".join(str(r) for r in ranks)
        result.warnings.append(
            f"Los LoRAs tienen ranks diferentes ({ranks_str}). "
            "Será necesario re-comprimir via SVD al rank objetivo. "
            "Esto puede reducir la calidad marginalmente."
        )
        # Sugerir el rank mayor para mínima pérdida
        valid_ranks = [r for r in ranks if r is not None]
        result.suggested_target_rank = max(valid_ranks)
    elif unique_ranks:
        result.suggested_target_rank = list(unique_ranks)[0]

    # Advertencia si hay muchos LoRAs (>4 puede dar resultados impredecibles)
    if len(loras) > 4:
        result.warnings.append(
            f"Estás combinando {len(loras)} LoRAs. Con más de 4, "
            "los resultados pueden ser difíciles de predecir. "
            "Se recomienda usar DARE+TIES para este caso."
        )

    return result
