"""
Presets curados para los LoRAs de decensurado (`Hearmeman/minimax-h3-loras`, preservados en
`loras/h3/` — ver `model_downloader.PRESERVED_LORAS`). A diferencia de un LoRA loader genérico,
esta colección viene documentada por el propio autor con: trigger word obligatoria por archivo,
fuerza sugerida (que en `HMCumshot` cambia según el modo), y un orden de apilado fijo — no tiene
sentido dejar que la UI los trate como items sueltos reordenables.

Fuente de las descripciones/fuerzas/orden: README de `Hearmeman/minimax-h3-loras` (leído s94).
Cualquier archivo que aparezca en `loras/h3/` y NO esté en este registro (una LoRA nueva que el
cliente agregue, un preset todavía no catalogado) sigue siendo utilizable — la ruta genérica
`/loras` no desaparece, esto solo agrega una capa curada encima para los que sí conocemos.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoraFile:
    filename: str
    default_strength: float   # fuerza relativa documentada por el autor


@dataclass(frozen=True)
class LoraPreset:
    id: str
    label: str
    trigger: str               # se agrega al final del prompt al activar (no se retira al desactivar)
    stage: str                 # "anatomy" | "action" — controla el orden de apilado, fijo
    description: str           # tooltip
    files: list[LoraFile] = field(default_factory=list)
    default_strength_turbo: float | None = None   # si la fuerza sugerida cambia en modo turbo


# Orden de apilado documentado por el autor: anatomía primero, acción encima. Este orden es EL
# que se aplica siempre al construir el workflow — independiente del orden en que el usuario
# active los toggles en la UI.
#
# Labels discretos (petición del usuario, s98): los nombres visibles en la UI evitan términos
# explícitos — el detalle real queda en el tooltip (description), que solo aparece al pasar el
# cursor. El mapeo archivo↔preset no cambia.
HEARMEMAN_PRESETS: list[LoraPreset] = [
    LoraPreset(
        id="hm_pussy", label="Detalle anatómico F·1",
        trigger="Vagina, hmpussy", stage="anatomy",
        description="Restituye legibilidad estructural en anatomía genital femenina explícita "
                     "(el modelo base la renderiza como forma aproximada sin esto). Dos archivos "
                     "acoplados con fuerza relativa fija 1.0 / 0.35 — el slider mueve ambos "
                     "manteniendo esa proporción.",
        files=[
            LoraFile("vagassist_e40.safetensors", 1.0),
            LoraFile("hmpussy_v6_epoch30.safetensors", 0.35),
        ],
    ),
    LoraPreset(
        id="hm_innie", label="Detalle anatómico F·2 (variante)",
        trigger="inniepussy", stage="anatomy",
        description="Variante de forma genital “innie” — en el prompt la trigger "
                     "'inniepussy' REEMPLAZA la palabra 'pussy', no se usan las dos juntas. "
                     "Requiere especificar ángulo de cámara (de frente/atrás/abajo). Alternativa "
                     "a HMPussy, no pensada para combinarse con ella.",
        files=[LoraFile("HMInnie_v1_e50.safetensors", 1.0)],
    ),
    LoraPreset(
        id="hm_breasts", label="Detalle de busto",
        trigger="HMBreasts", stage="anatomy",
        description="Controla tamaño, forma, areola y color de pezón vía ejes descritos en "
                     "inglés natural en el prompt (ej. “large, natural breasts, medium "
                     "sized brown areolas”) — la trigger sola no alcanza, hay que describir.",
        files=[LoraFile("HMBreasts_085e0750_e40.safetensors", 1.0)],
    ),
    LoraPreset(
        id="hm_penis", label="Detalle anatómico M",
        trigger="HMPenis", stage="anatomy",
        description="Anatomía masculina explícita — funciona mejor en tamaños medio-grandes. "
                     "Especificar ángulo de cámara (frente/atrás/lado) en el prompt.",
        files=[LoraFile("HMPenis_v2_e35.safetensors", 1.0)],
    ),
    LoraPreset(
        id="hm_nsfw_aio", label="Movimiento de acción",
        trigger="hmmotion", stage="action",
        description="Movimiento y acción explícitos: missionary, doggy, cowgirl, handjob, "
                     "blowjob, penetración. El autor recomienda mantener la fuerza en 0.5 o "
                     "menos — pasarse de ahí degrada el movimiento.",
        files=[LoraFile("HMNSFW_AIO_V2.safetensors", 0.5)],
    ),
    LoraPreset(
        id="hm_cumshot", label="Acción final (clímax)",
        trigger="cumshot", stage="action",
        description="Se apila SOBRE HMPussy/HMPenis (no reemplaza la anatomía, la complementa). "
                     "Fuerza sugerida depende del modo: 0.9 en generación normal, ~0.2 con Turbo "
                     "de 8 pasos — el slider arranca en el valor correcto según el toggle Turbo "
                     "al momento de activarlo.",
        files=[LoraFile("HMCumshot_V2.safetensors", 0.9)],
        default_strength_turbo=0.2,
    ),
]


def presets_for_files(available_files: list[str]) -> list[dict]:
    """Filtra los presets a los que TODOS sus archivos existen en `available_files` (nombres tal
    como los devuelve ComfyUI, con separador de SO) y los serializa para la API/UI."""
    norm = {f.replace("\\", "/").rsplit("/", 1)[-1] for f in available_files}
    out = []
    for p in HEARMEMAN_PRESETS:
        if not all(f.filename in norm for f in p.files):
            continue
        out.append({
            "id": p.id, "label": p.label, "trigger": p.trigger, "stage": p.stage,
            "description": p.description,
            "files": [{"filename": f.filename, "default_strength": f.default_strength}
                     for f in p.files],
            "default_strength_turbo": p.default_strength_turbo,
        })
    return out


def preset_extra_files(available_files: list[str]) -> list[str]:
    """Archivos en loras/h3/ que no pertenecen a ningún preset conocido ni son el turbo LoRA
    (ese ya tiene su propio selector dedicado) — LoRAs nuevas que el cliente agregue, o futuros
    presets todavía sin catalogar. Se listan sueltos, sin curación."""
    known = {f.filename for p in HEARMEMAN_PRESETS for f in p.files}
    h3_files = [f for f in available_files
                if "h3" in f.replace("\\", "/").split("/")]
    return [f for f in h3_files
            if f.replace("\\", "/").rsplit("/", 1)[-1] not in known
            and "turbo" not in f.lower()]
