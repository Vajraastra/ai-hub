"""
Interfaz ArchAdapter — contrato entre el núcleo agnóstico y cada arquitectura.

El núcleo (set de validación, experimentos, merges, rutas web) no conoce
detalles de ninguna arquitectura: bloques, workflows, defaults de sampling y
zonas prohibidas se piden siempre al adaptador. Añadir una arquitectura
(sdxl, ideogram4) = implementar esta clase; el mapa de bloques NO transfiere
entre arquitecturas, el método y la herramienta sí.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BlockGroup:
    """Grupo macro de bloques con nombre humano (nivel macro de la UX).

    La asignación bloque→grupo es una HIPÓTESIS de partida: se corrige
    empíricamente con los analyzers y el set de validación fijo. `hypothesis`
    queda en True hasta que la validación respalde la etiqueta (regla del
    handoff: una etiqueta humana que miente es peor que no tenerla).
    """
    id: str                      # "structure", "style", ...
    label: str                   # nombre humano mostrado en la UI
    blocks: list[str] = field(default_factory=list)
    description: str = ""
    hypothesis: bool = True


@dataclass(frozen=True)
class SamplingDefaults:
    """Parámetros de inferencia por defecto. El set de validación fijo los
    copia UNA vez al crearse y después quedan bloqueados dentro del set."""
    cfg: float
    steps: int
    sampler: str
    scheduler: str
    width: int
    height: int


@dataclass(frozen=True)
class ModelFiles:
    """Ficheros que la arquitectura espera en el almacén de modelos
    (rutas relativas a la raíz configurada en hub_config.json)."""
    diffusion_model: str
    text_encoder: str
    vae: str


class ArchAdapter(ABC):
    """Contrato por arquitectura. Implementaciones viven en este paquete y se
    registran en architectures/__init__.py."""

    name: str = ""    # id corto y estable: "zimage", "sdxl", "ideogram4"
    label: str = ""   # nombre mostrado en la UI

    # ── Modelo ────────────────────────────────────────────────────────────

    @abstractmethod
    def model_files(self) -> ModelFiles:
        """Ficheros necesarios para inferencia con esta arquitectura."""

    # ── Bloques ───────────────────────────────────────────────────────────

    @abstractmethod
    def list_blocks(self) -> list[str]:
        """Claves de bloque individuales (nivel medio de la UX), en orden de
        profundidad. Son los identificadores que entienden los nodos de merge
        selectivo y los analyzers."""

    @abstractmethod
    def block_groups(self) -> list[BlockGroup]:
        """Agrupación macro con nombres humanos (nivel macro de la UX)."""

    @abstractmethod
    def forbidden_zones(self) -> list[str]:
        """Prefijos de tensor que NO se tocan por defecto (solo con override
        explícito del usuario). Ej.: la zona del adapter de texto."""

    # ── Merge ─────────────────────────────────────────────────────────────

    def lora_key_map(self) -> dict[str, tuple[str, tuple[int, int, int] | None]]:
        """Mapa módulo-LoRA → tensor del checkpoint base.

        Clave: ruta del módulo tal y como aparece en el fichero LoRA, sin
        prefijo contenedor (`diffusion_model.` / `transformer.`) ni sufijo
        `.lora_A.weight` / `.lora_B.weight`.
        Valor: (clave `.weight` del checkpoint, franja `(dim, inicio, tamaño)`
        o None si el delta cubre el tensor entero). La franja existe porque
        algunos checkpoints fusionan proyecciones que los LoRAs entrenan por
        separado (ej. qkv). Debe coincidir con el mapeo runtime de ComfyUI
        para que mergear a fichero ≡ cargar el LoRA en memoria."""
        raise NotImplementedError(f"{self.name}: sin soporte de merge LoRA")

    # ── Inferencia ────────────────────────────────────────────────────────

    @abstractmethod
    def sampling_defaults(self) -> SamplingDefaults:
        """Defaults de sampling para el modelo de trabajo de la arquitectura."""

    @abstractmethod
    def workflow_name(self, task: str) -> str:
        """Nombre del template JSON en workflows/<name>/ para una tarea.
        Tareas mínimas: "txt2img". Lanza KeyError si la tarea no existe."""
