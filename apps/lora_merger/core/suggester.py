"""
Sistema de sugerencias automáticas para el merge de LoRAs.
Propone método, pesos y filtro de capas basándose en el análisis.
"""
from dataclasses import dataclass, field
from typing import Optional

from .detector import LoRAInfo, get_common_rank
from .analyzer import AnalysisResult
from .validator import ValidationResult


@dataclass
class MergeSuggestion:
    method: str
    method_label: str
    weights: list[float]
    layer_filter: str
    target_rank: Optional[int]
    explanation: str
    warnings: list[str] = field(default_factory=list)


def suggest(
    loras: list[LoRAInfo],
    analyses: list[AnalysisResult],
    validation: ValidationResult,
) -> MergeSuggestion:
    """
    Genera la sugerencia de merge más adecuada para el conjunto de LoRAs.
    """
    n = len(loras)

    # Pesos iniciales proporcionales al impacto inverso
    # (LoRA de mayor impacto → peso menor para no sobresaturar)
    avg_scores = []
    for a in analyses:
        if a.layer_scores:
            avg = sum(ls.score for ls in a.layer_scores) / len(a.layer_scores)
        else:
            avg = 50.0
        avg_scores.append(avg)

    total_score = sum(avg_scores) or 1.0
    # Normalizar pesos inversamente proporcionales al impacto
    inv_scores = [total_score / max(s, 1.0) for s in avg_scores]
    inv_total = sum(inv_scores) or 1.0
    weights = [round(s / inv_total, 2) for s in inv_scores]
    # Asegurar que sumen ~1
    diff = 1.0 - sum(weights)
    weights[0] = round(weights[0] + diff, 2)

    target_rank = validation.suggested_target_rank

    # Elegir método
    method, method_label, explanation, layer_filter = _pick_method(
        n, loras, analyses, validation
    )

    warnings = list(validation.warnings)
    if validation.rank_mismatch:
        warnings.append(
            f"Se aplicará SVD para unificar ranks al Rank {target_rank}."
        )

    return MergeSuggestion(
        method=method,
        method_label=method_label,
        weights=weights,
        layer_filter=layer_filter,
        target_rank=target_rank,
        explanation=explanation,
        warnings=warnings,
    )


def _pick_method(
    n: int,
    loras: list[LoRAInfo],
    analyses: list[AnalysisResult],
    validation: ValidationResult,
) -> tuple[str, str, str, str]:
    """
    Selecciona el método de merge, su etiqueta UI, explicación y filtro de capas.
    """
    content_types = [a.content_type for a in analyses]

    if n == 2:
        # Con 2 LoRAs, SLERP es una opción elegante para estilos
        both_style = all(ct in ("estilo", "concepto", "concepto / texto")
                         for ct in content_types)
        one_style_one_char = (
            "estilo" in content_types
            and ("personaje / sujeto" in content_types or "detalles / estilo" in content_types)
        )

        if both_style:
            return (
                "slerp",
                "SLERP (interpolación esférica)",
                "Ambos LoRAs son de estilo. SLERP ofrece una interpolación suave "
                "que evita artefactos al mezclar dos estilos artísticos.",
                "full",
            )
        if one_style_one_char:
            return (
                "weighted_sum",
                "Weighted Sum (suma ponderada)",
                "Un LoRA de estilo y uno de personaje/sujeto. "
                "Weighted Sum con pesos balanceados da el mejor control manual.",
                "full",
            )
        return (
            "weighted_sum",
            "Weighted Sum (suma ponderada)",
            "Método estándar para 2 LoRAs. Simple y predecible. "
            "Ajusta los pesos según cuánto quieres que aporte cada uno.",
            "full",
        )

    # Más de 2 LoRAs
    if n > 4:
        return (
            "dare_ties",
            "DARE + TIES (poda + resolución de conflictos)",
            f"Con {n} LoRAs, DARE+TIES es la mejor opción: primero poda parámetros "
            "poco importantes en cada LoRA, luego resuelve conflictos de signo. "
            "Reduce el 'ruido' al combinar muchos modelos.",
            "full",
        )

    # 3-4 LoRAs
    return (
        "ties",
        "TIES (resolución de conflictos)",
        f"Con {n} LoRAs, TIES resuelve conflictos entre parámetros usando voto de signo. "
        "Es más robusto que Weighted Sum cuando los LoRAs tienen áreas de influencia superpuestas.",
        "full",
    )


def weights_from_impact(analyses: list[AnalysisResult]) -> list[float]:
    """
    Calcula pesos balanceados basados en el impacto promedio de cada LoRA.
    LoRAs con mayor impacto reciben peso menor para no dominar el merge.
    """
    avg_scores = []
    for a in analyses:
        if a.layer_scores:
            avg = sum(ls.score for ls in a.layer_scores) / len(a.layer_scores)
        else:
            avg = 50.0
        avg_scores.append(max(avg, 1.0))

    inv = [1.0 / s for s in avg_scores]
    total = sum(inv)
    return [round(v / total, 3) for v in inv]
