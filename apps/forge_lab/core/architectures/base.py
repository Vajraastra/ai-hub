"""
Interfaz ArchAdapter — contrato entre el núcleo agnóstico y cada arquitectura.

El núcleo (set de validación, experimentos, merges, exploración, rutas web)
no conoce detalles de ninguna arquitectura: bloques, workflows, layout de
ficheros, defaults de sampling y zonas prohibidas se piden siempre al
adaptador. Añadir una arquitectura (sdxl, ideogram4) = implementar esta
clase; el mapa de bloques NO transfiere entre arquitecturas, el método y la
herramienta sí.

Dos layouts de modelo soportados:
  - "por piezas" (zimage): DiT + text encoder + VAE en ficheros separados,
    base oficial única configurada (multi_base = False).
  - "checkpoint completo" (sdxl): un solo fichero con UNet + TEs + VAE bajo
    checkpoints/; cualquier checkpoint del almacén sirve de base de merge
    (multi_base = True) y los tensores van prefijados (container_prefix).
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


class ArchAdapter(ABC):
    """Contrato por arquitectura. Implementaciones viven en este paquete y se
    registran en architectures/__init__.py."""

    name: str = ""    # id corto y estable: "zimage", "sdxl", "ideogram4"
    label: str = ""   # nombre mostrado en la UI

    # ── Layout del modelo ─────────────────────────────────────────────────

    #: carpeta del almacén donde vive el peso principal Y donde se escriben
    #: los derivados de forge_lab (<weights_root>/forge_lab/<name>.safetensors)
    weights_root: str = "diffusion_models"

    #: prefijo de las claves de tensor del modelo de difusión dentro del
    #: fichero base ("" = claves desnudas; sdxl: "model.diffusion_model.")
    container_prefix: str = ""

    #: False = una sola base oficial (la de model_files); True = cualquier
    #: fichero de weights_root (fuera de forge_lab/) es base elegible
    multi_base: bool = False

    @abstractmethod
    def file_keys(self) -> dict[str, tuple[str, ...]]:
        """Claves de fichero que la arquitectura necesita y carpeta(s) raíz
        admitida(s) para cada una dentro del almacén global. La clave
        "diffusion_model" (el peso principal) es obligatoria; text_encoder /
        vae solo existen en layouts por piezas."""

    @abstractmethod
    def model_files(self) -> dict[str, str]:
        """Paths por defecto (relativos al almacén, posix) para cada clave
        de file_keys(). El usuario puede pisarlos desde la UI."""

    def required_nodes(self) -> list[str]:
        """Nodos custom de ComfyUI que la exploración por bloques necesita."""
        return []

    # ── Bloques ───────────────────────────────────────────────────────────

    @abstractmethod
    def list_blocks(self) -> list[str]:
        """Claves de bloque individuales (nivel medio de la UX), en orden de
        profundidad. Son prefijos de tensor SIN container_prefix: los
        entienden el merge worker y la exploración."""

    @abstractmethod
    def block_groups(self) -> list[BlockGroup]:
        """Agrupación macro con nombres humanos (nivel macro de la UX)."""

    @abstractmethod
    def forbidden_zones(self) -> list[str]:
        """Prefijos de tensor (clave COMPLETA del fichero base) que NO se
        tocan por defecto (solo con override explícito). Ej.: el adapter de
        texto en zimage; conditioner./first_stage_model. en sdxl."""

    # ── Merge ─────────────────────────────────────────────────────────────

    def lora_key_map(self) -> dict[str, tuple[str, tuple[int, int, int] | None]]:
        """Mapa módulo-LoRA → tensor del fichero base.

        Clave: módulo tal y como queda tras quitar el sufijo lora
        (.lora_A.weight / .lora_down.weight / …) y, en formato PEFT, el
        prefijo contenedor (diffusion_model. / transformer.). En formato
        kohya la clave incluye su prefijo (lora_unet_…) con underscores.
        Valor: (clave COMPLETA `.weight` del fichero base, franja
        `(dim, inicio, tamaño)` o None si el delta cubre el tensor entero).
        Debe coincidir con el mapeo runtime de ComfyUI para que mergear a
        fichero ≡ cargar el LoRA en memoria."""
        raise NotImplementedError(f"{self.name}: sin soporte de merge LoRA")

    #: prefijos de módulo LoRA que se SALTAN por política en vez de fallar
    #: como "sin mapeo" (sdxl: los text encoders lora_te1_/lora_te2_)
    ignored_lora_prefixes: tuple[str, ...] = ()

    # ── Exploración por bloques (Fase 4) ──────────────────────────────────

    def explore_switches(self) -> list[dict]:
        """Switches de la grid de exploración: [{"id", "label"}]. Los ids
        son bloques de list_blocks() más, si la arquitectura lo define, el
        pseudo-bloque "other" (resto de pesos)."""
        return [{"id": b, "label": b} for b in self.list_blocks()]

    def dose_exponent(self, lora_has_alpha: bool) -> int:
        """Los nodos selectivos de Realtime-Lora escalan TODOS los tensores
        del bloque por su strength: el delta efectivo va como str² (lora_up
        × lora_down) o str³ si además hay tensor alpha. La dosis de la UI es
        lineal; al nodo se le pasa dosis**(1/exponente)."""
        return 3 if lora_has_alpha else 2

    def selective_node_inputs(self, config: dict, exponent: int) -> dict:
        """Inputs del nodo selectivo (nodo "10" del workflow
        txt2img_lora_selective.json) para una config normalizada
        {"blocks": {id: dosis}, "other": dosis} con dosis lineal."""
        raise NotImplementedError(f"{self.name}: sin exploración por bloques")

    def config_to_merge_blocks(self, config: dict) -> dict[str, float]:
        """Config de exploración → parámetro `blocks` del merge worker
        (dosis por prefijo de bloque, sin container_prefix)."""
        raise NotImplementedError(f"{self.name}: sin exploración por bloques")

    # ── Inferencia ────────────────────────────────────────────────────────

    @abstractmethod
    def sampling_defaults(self) -> SamplingDefaults:
        """Defaults de sampling para el modelo de trabajo de la arquitectura."""

    @abstractmethod
    def workflow_name(self, task: str) -> str:
        """Nombre del template JSON en workflows/<name>/ para una tarea.
        Tareas mínimas: "txt2img". Lanza KeyError si la tarea no existe."""
