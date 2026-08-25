"""
Los campos de un prompt H3 y su ensamblado determinista.

El prompt final no es prosa libre: son campos con nombre literal, en orden, y
para los modos con keyframe una primera línea de alineación de formato fijo.
Ensamblar eso es aritmética y plantillas — nunca criterio del LLM. El LLM
escribe el contenido de cada campo; esta capa lo monta y `validator.py` lo
verifica.

Estructura (guía base, §2):

    <línea de instrucción>          <- solo I2VA / FL2VA / L2VA
                                    <- línea en blanco obligatoria
    integrated_multimodal_description: [Shot 1] ...

    overall_soundscape: ...

    non_diegetic_music: ...

Ref2VA (guía ref, §1) no usa esa estructura: son seis campos y el cuerpo
principal se llama `detailed_description`.
"""
import re
from enum import Enum
from dataclasses import dataclass, field


class Mode(str, Enum):
    T2VA = "t2va"      # solo texto
    I2VA = "i2va"      # primer frame  -> el modo de cada tramo encadenado
    FL2VA = "fl2va"    # primer y último frame
    L2VA = "l2va"      # último frame
    REF2VA = "ref2va"  # referencias (identidad/estilo/voz) -> tramo 1 de una cadena


BASE_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_FIELDS = ("subject_definitions", "summary", "retention_analysis",
              "detailed_description", "overall_soundscape", "non_diegetic_music")

_FPS = 24
MAX_SECONDS = 15.0
MIN_SECONDS = 4.0   # la guía habla de 4-15s; el pipeline admite menos, ver nota en frame_length()


def frame_length(seconds: float) -> int:
    """Segundos -> frames válidos para H3 (grid 17n+5 a 24fps).

    Misma fórmula que `core.pipeline._frame_length` (el ComfyMathExpression del
    template oficial). Duplicada a propósito: este paquete no debe arrastrar
    aiohttp ni el cliente de ComfyUI solo para calcular una duración, porque el
    banco de pruebas corre sin nada de eso levantado."""
    seconds = max(0.2, min(float(seconds), MAX_SECONDS))
    base = max(5, round(seconds * _FPS))
    return base + (5 - (base % 17)) % 17


def effective_duration(seconds: float) -> float:
    """Duración real del clip tras cuadrar al grid. Es la que va en la línea de
    instrucción y contra la que se validan los timestamps de corte: pedir 8s
    produce 8.21s, y un corte en 00:08.100 es legal aunque parezca fuera."""
    return frame_length(seconds) / _FPS


def timecode(seconds: float) -> str:
    """0.0 -> '00:00.000' (formato de corte de la guía, §4.2)."""
    m, s = divmod(max(0.0, float(seconds)), 60)
    return f"{int(m):02d}:{s:06.3f}"


def parse_timecode(tc: str) -> float:
    m, _, s = tc.partition(":")
    return int(m) * 60 + float(s)


_SHOT_RE = re.compile(r"\[Shot (\d+)\]")
_CUT_RE = re.compile(r"\[Shot (\d+)\]\s*At (\d{2}:\d{2}\.\d{3})\s*,")


def shots(body: str) -> list[int]:
    """Índices de plano en orden de aparición."""
    return [int(m.group(1)) for m in _SHOT_RE.finditer(body)]


def cuts(body: str) -> list[tuple[int, float]]:
    """(índice de plano, segundo del corte) para los planos con timestamp."""
    return [(int(m.group(1)), parse_timecode(m.group(2))) for m in _CUT_RE.finditer(body)]


def last_shot_index(body: str) -> int:
    idx = shots(body)
    return max(idx) if idx else 1


# ── Línea de instrucción (guía base, §2.1) ───────────────────────────────────
# Literales tal como los fija la guía. Las tres difieren entre sí en si envuelven
# la referencia en <> y [] — la guía es inconsistente ahí (compárense sus casos 3
# y 4) y el validador acepta ambas formas, pero al escribir se copia el literal.

def instruction(mode: Mode, duration_s: float, last_shot: int = 1) -> str:
    d = f"{effective_duration(duration_s):.2f}"
    if mode is Mode.I2VA:
        return ("For the target video, at 0.00 seconds into the target video, "
                "<Picture 1> (from [Shot 1]) is fully referenced.")
    if mode is Mode.FL2VA:
        return ("How the reference pictures align with the target video — "
                "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
                f"Picture 2 (from Shot {last_shot}) aligns with the {d}-second mark of the target video.")
    if mode is Mode.L2VA:
        return ("How the reference pictures align with the target video — "
                f"<Picture 1> (from [Shot {last_shot}]) aligns with the {d}-second mark "
                "of the target video.")
    return ""   # T2VA arranca directo en los campos; Ref2VA no usa esta línea


@dataclass
class H3Prompt:
    """Prompt de un modo base (T2VA / I2VA / FL2VA / L2VA)."""
    mode: Mode = Mode.T2VA
    duration_s: float = 5.0
    integrated_multimodal_description: str = ""
    overall_soundscape: str = ""
    non_diegetic_music: str = ""

    @property
    def last_shot(self) -> int:
        return last_shot_index(self.integrated_multimodal_description)

    def instruction(self) -> str:
        return instruction(self.mode, self.duration_s, self.last_shot)

    def assemble(self) -> str:
        head = self.instruction()
        body = "\n\n".join(f"{k}: {getattr(self, k).strip()}" for k in BASE_FIELDS)
        return f"{head}\n\n{body}" if head else body


@dataclass
class H3RefPrompt:
    """Prompt de modo Ref2VA: seis secciones, cuerpo en `detailed_description`."""
    duration_s: float = 5.0
    subject_definitions: str = ""
    summary: str = ""
    retention_analysis: str = ""
    detailed_description: str = ""
    overall_soundscape: str = ""
    non_diegetic_music: str = ""
    mode: Mode = field(default=Mode.REF2VA, init=False)

    @property
    def last_shot(self) -> int:
        return last_shot_index(self.detailed_description)

    def assemble(self) -> str:
        # Ref2VA pone el contenido en la línea siguiente al nombre del campo
        # (sus entradas son multilínea: una por etiqueta de referencia).
        return "\n\n".join(f"{k}:\n{getattr(self, k).strip()}" for k in REF_FIELDS)


# ── Lectura de lo que devuelve el LLM ────────────────────────────────────────

def parse(text: str, mode: Mode = Mode.T2VA, duration_s: float = 5.0):
    """Texto suelto -> H3Prompt / H3RefPrompt.

    Tolera las dos formas de escribir un campo (`campo: valor` y `campo:` con
    el valor debajo) porque las guías usan una cada una. Lo que NO tolera es
    inventar campos: lo que caiga fuera de los nombres canónicos se descarta
    aquí y el validador lo reporta como texto sobrante."""
    is_ref = mode is Mode.REF2VA
    names = REF_FIELDS if is_ref else BASE_FIELDS
    pattern = re.compile(rf"^({'|'.join(names)})\s*:", re.M)

    found, marks = {}, list(pattern.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        found[m.group(1)] = text[m.end():end].strip()

    if is_ref:
        return H3RefPrompt(duration_s=duration_s, **{k: found.get(k, "") for k in REF_FIELDS})
    return H3Prompt(mode=mode, duration_s=duration_s,
                    **{k: found.get(k, "") for k in BASE_FIELDS})


def leading_text(text: str, mode: Mode = Mode.T2VA) -> str:
    """Lo que el LLM escribió ANTES del primer campo, quitando la línea de
    instrucción legítima. Casi siempre es su cháchara ('Here is your prompt:'),
    que es el fallo número uno al pedirle formato a un modelo pequeño."""
    names = REF_FIELDS if mode is Mode.REF2VA else BASE_FIELDS
    m = re.search(rf"^({'|'.join(names)})\s*:", text, re.M)
    head = text[:m.start()] if m else text
    for line in list(head.splitlines()):
        if line.startswith(("For the target video,", "How the reference pictures align")):
            head = head.replace(line, "", 1)
    return head.strip()
