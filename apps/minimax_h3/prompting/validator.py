"""
El contrato de un prompt H3, verificado sin LLM de por medio.

Un modelo local de 8-12B no falla por falta de ideas: falla por salirse del
formato — se come un campo, "mejora" la línea de diálogo del usuario, cita una
<Picture 3> que no existe, o antepone "Here is your prompt:". Todo eso es
detectable con reglas y sin criterio, así que se detecta acá y se le devuelve
al modelo para que repare, en vez de mandarlo a un render de 100 minutos.

Los mensajes de `Issue` van dirigidos al LLM en el bucle de reparación: por eso
están en inglés (la guía lo está) y dicen qué hacer, no solo qué está mal.

Calibración: `tests/bench_prompting.py` corre este validador contra los cinco
ejemplos resueltos por el propio MiniMax. Si rechaza uno, el error es nuestro.
"""
import re
from dataclasses import dataclass, field

from .schema import (Mode, BASE_FIELDS, REF_FIELDS, H3Prompt, H3RefPrompt,
                     effective_duration, timecode, shots, cuts, parse, leading_text)

# ── Vocabularios cerrados de la guía ─────────────────────────────────────────

CAMERA_MOTIONS = (
    "zoom in", "zoom out", "push in", "pull out", "pan left", "pan right",
    "truck left", "truck right", "tilt up", "tilt down", "pedestal up",
    "pedestal down", "arc shot", "tracking shot", "static shot",
    "shake slightly", "shake strongly", "pov", "roll clockwise",
    "roll counterclockwise",
)
# Conjugaciones que la guía usa en sus propios ejemplos ("the camera pushes in").
_CAMERA_ALIASES = ("pushes in", "pulls out", "zooms in", "zooms out", "pans left",
                   "pans right", "trucks left", "trucks right", "tilts up",
                   "tilts down", "arcs", "tracks", "holds a static shot",
                   "shakes slightly", "shakes strongly", "rolls clockwise",
                   "rolls counterclockwise")

AMPLITUDES = ("with small amplitude", "with large amplitude")
SPEEDS = ("at slow speed", "at fast speed")

STYLES = ("cinematic", "live-action", "2d-animated", "3d cg", "claymation",
          "watercolor", "vintage film")

CUT_VERBS = ("the camera cuts to", "the shot cuts to", "the shot transitions to",
             "the shot changes to", "the shot switches to",
             "cross-dissolve", "fade", "wipe")

# Los 11 idiomas de soporte estable, más los alias en que la propia guía escribe
# su etiqueta (su caso de 4.5 usa 中文).
LANGUAGES = {"arabic", "chinese", "english", "french", "german", "italian",
             "japanese", "korean", "portuguese", "russian", "spanish"}
LANGUAGE_ALIASES = {"中文", "日本語", "한국어", "español", "français", "deutsch",
                    "italiano", "português", "русский", "العربية"}

RETENTION_VISUAL = ("fully_preserved", "partially_preserved", "attribute_transfer",
                    "weak_reference")
RETENTION_AUDIO = ("fully_copy", "partially_copy", "reference", "weak_reference")

TASK_TYPES = ("keyframe completion", "reference generation", "video editing",
              "video continuation", "audio reuse", "audio reference")

API_CHAR_LIMIT = 7000   # tope de la API v2; en local no hay truncado documentado


@dataclass
class Issue:
    code: str
    severity: str      # "error" (no se manda a ComfyUI) | "warning" (se avisa)
    field: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.field}: {self.message}"


@dataclass
class Context:
    """Lo que el backend ya sabe y el LLM no debe adivinar."""
    duration_s: float = 5.0
    n_pictures: int = 0
    n_videos: int = 0
    n_audios: int = 0
    dialogue: list[str] = field(default_factory=list)   # líneas del usuario, verbatim
    silence_requested: bool = False
    delivery: str = "local"                              # "local" | "api"


_D_RE = re.compile(r"<d>\s*(?:\[([^\]]+)\]\s*)?(.*?)</d>", re.S)
_SPK_RE = re.compile(r"\(S(\d+(?:\s*,\s*S?\d+)*)\)")
_REF_RE = re.compile(r"<?(Picture|Video|Audio|Subject)\s+(\d+)>?")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _err(out, code, fld, msg):
    out.append(Issue(code, "error", fld, msg))


def _warn(out, code, fld, msg):
    out.append(Issue(code, "warning", fld, msg))


# ── Reglas compartidas por todos los modos ───────────────────────────────────

def _check_body(body: str, fld: str, ctx: Context, out: list, ref_mode: bool = False):
    dur = effective_duration(ctx.duration_s)

    # --- planos y cortes (§4.2) ---
    idx = shots(body)
    if not idx:
        _err(out, "no_shots", fld, "The body must start with [Shot 1].")
    else:
        if idx[0] != 1:
            _err(out, "shot_start", fld,
                 f"The first shot must be [Shot 1], found [Shot {idx[0]}].")
        expected = list(range(1, len(idx) + 1))
        if idx != expected:
            _err(out, "shot_order", fld,
                 f"Shots must be numbered consecutively from 1 in playback order; "
                 f"found {idx}, expected {expected}.")

    cut_list = cuts(body)
    if any(i == 1 for i, _ in cut_list):
        _err(out, "shot1_timestamp", fld,
             "[Shot 1] must not carry a timestamp; only later shots state a cut time.")
    for i in idx[1:]:
        if i not in [c for c, _ in cut_list]:
            _err(out, "missing_cut_time", fld,
                 f"[Shot {i}] must begin with a cut time, e.g. '[Shot {i}] At 00:03.500, "
                 f"the camera cuts to ...'.")
    times = [t for _, t in cut_list]
    if times != sorted(times) or len(set(times)) != len(times):
        _err(out, "cut_order", fld,
             f"Cut times must strictly increase; found {[timecode(t) for t in times]}.")
    for i, t in cut_list:
        if t >= dur:
            _err(out, "cut_out_of_range", fld,
                 f"[Shot {i}] cuts at {timecode(t)} but the effective video duration is "
                 f"{dur:.2f}s. Every cut must fall inside the clip.")
    if len(idx) > 1 and not any(v in body.lower() for v in CUT_VERBS):
        _warn(out, "cut_verb", fld,
              "Multi-shot bodies should name the transition, e.g. 'the camera cuts to'.")

    # --- estilo de apertura (§4.1 / ref §5.2) ---
    # STYLES es la lista de estilos *comunes* de la guía, no un vocabulario
    # cerrado: el propio ejemplo de ref-en abre con "multi-camera sitcom style".
    # Basta con que declare uno.
    head = body[:200].lower()
    if not any(s in head for s in STYLES) and "style" not in head:
        _warn(out, "style_missing", fld,
              f"State the visual style at the opening (e.g. {', '.join(STYLES[:4])}).")

    # --- cámara (§4.3) ---
    low = body.lower()
    if not any(m in low for m in CAMERA_MOTIONS + _CAMERA_ALIASES):
        _warn(out, "camera_missing", fld,
              "No camera motion stated. Name one motion type per shot; use "
              "'holds a static shot' if the camera does not move.")
    for word, allowed in (("amplitude", AMPLITUDES), ("speed", SPEEDS)):
        if word in low and not any(a in low for a in allowed):
            _warn(out, f"camera_{word}", fld,
                  f"Camera {word} must use the fixed wording: {' / '.join(allowed)}.")

    # --- diálogo (§4.4) ---
    if body.count("<d>") != body.count("</d>"):
        _err(out, "dialogue_unbalanced", fld,
             f"Unbalanced dialogue tags: {body.count('<d>')} <d> vs {body.count('</d>')} </d>.")

    spoken = []
    for m in _D_RE.finditer(body):
        lang, said = m.group(1), (m.group(2) or "").strip()
        spoken.append(said)
        if not lang:
            _err(out, "dialogue_no_lang", fld,
                 f"Missing language tag in <d>{said[:40]}...</d>. Write "
                 f"<d>[English] ...</d> with the language of the spoken line.")
        elif lang.strip().lower() not in LANGUAGES and lang.strip() not in LANGUAGE_ALIASES:
            _warn(out, "dialogue_lang", fld,
                  f"Language tag '{lang}' is not one of the 11 stably supported languages.")
        if not said:
            _err(out, "dialogue_empty", fld, "Empty <d></d> block.")
        # el hablante se declara fuera de <d>, antes de la línea
        before = body[max(0, m.start() - 320):m.start()]
        if not _SPK_RE.search(before) and "<Audio" not in before:
            _err(out, "dialogue_no_speaker", fld,
                 f"The line <d>{said[:40]}...</d> has no speaker. Put the identifying "
                 f"description and a stable ID such as (S1) outside <d>, before the line.")

    # verbatim: lo que escribió el usuario no se reescribe (§4.4)
    for want in ctx.dialogue:
        want = want.strip()
        if want and not any(want == s for s in spoken):
            near = next((s for s in spoken if want.lower().rstrip(".!?") in s.lower()), None)
            _err(out, "dialogue_not_verbatim", fld,
                 f"The user's line must appear inside <d> character for character: "
                 f"{want!r}" + (f" (found the altered {near!r})" if near else " (not found)"))
    if ctx.dialogue:
        for s in spoken:
            if s and not any(s.strip() == d.strip() for d in ctx.dialogue):
                _warn(out, "dialogue_invented", fld,
                      f"Invented spoken line not provided by the user: {s[:60]!r}")

    # voz en off (§4.4)
    if "voiceover" in low or "voice-over" in low:
        if "says in an off-screen voiceover" not in low:
            _err(out, "voiceover_phrase", fld,
                 "Voiceover must use the exact phrase 'says in an off-screen voiceover'.")
        if "lips remain" not in low:
            _err(out, "voiceover_lips", fld,
                 "After a voiceover <d> block, state that the on-screen character's lips "
                 "remain completely closed.")

    # continuidad a través del corte (§4.4)
    n_trans = body.count("<scenetrans>")
    if n_trans == 1:
        _err(out, "scenetrans_unpaired", fld,
             "<scenetrans> marks both sides of the cut: use it in the shot before and "
             "in the shot after, and state that the audio continues across the cut.")

    # --- referencias existentes (el backend sabe cuáles hay) ---
    limits = {"Picture": ctx.n_pictures, "Video": ctx.n_videos, "Audio": ctx.n_audios}
    for kind, n in ((m.group(1), int(m.group(2))) for m in _REF_RE.finditer(body)):
        if kind == "Subject":
            continue
        if n > limits.get(kind, 0):
            _err(out, "ref_out_of_range", fld,
                 f"<{kind} {n}> is cited but only {limits.get(kind, 0)} {kind.lower()} "
                 f"reference(s) are connected.")

    # --- hablantes consecutivos (§4.4) ---
    ids = set()
    for m in _SPK_RE.finditer(body):
        ids.update(int(x) for x in re.findall(r"\d+", m.group(1)))
    if ids and sorted(ids) != list(range(1, max(ids) + 1)):
        _err(out, "speaker_gap", fld,
             f"Speaker IDs must be assigned in order of first vocal event without gaps; "
             f"found {sorted(ids)}.")


def _check_audio_fields(soundscape: str, music: str, ctx: Context, out: list):
    """§4.6 y §4.7: los dos campos de audio que no llevan diálogo."""
    for fld, text, lo, hi in (("overall_soundscape", soundscape, 1, 4),
                              ("non_diegetic_music", music, 1, 3)):
        t = text.strip()
        if not t:
            _err(out, "empty_field", fld,
                 f"{fld} is mandatory. Use 'N/A' only when it genuinely does not apply.")
            continue
        if t.upper() == "N/A":
            if fld == "overall_soundscape" and not ctx.silence_requested:
                _err(out, "soundscape_na", fld,
                     "overall_soundscape may be N/A only when the user explicitly asked for "
                     "complete silence. H3 always generates a soundtrack: describe the room "
                     "tone and physical sounds, or it will invent them.")
            continue
        if "<d>" in t:
            _err(out, "audio_dialogue", fld,
                 f"Dialogue, singing and diegetic music belong in the body, not in {fld}.")
        n = len(_sentences(t))
        if not (lo <= n <= hi):
            _warn(out, "audio_length", fld,
                  f"{fld} should be {lo}-{hi} English sentences in one paragraph; found {n}.")


# ── Entradas públicas ────────────────────────────────────────────────────────

def validate(prompt, ctx: Context) -> list[Issue]:
    """Valida un prompt ya estructurado."""
    out: list[Issue] = []

    if isinstance(prompt, H3RefPrompt):
        for fld in REF_FIELDS:
            if not getattr(prompt, fld).strip():
                _err(out, "empty_field", fld, f"{fld} is mandatory and must not be empty.")
        _check_ref_sections(prompt, ctx, out)
        _check_body(prompt.detailed_description, "detailed_description", ctx, out, ref_mode=True)
        _check_audio_fields(prompt.overall_soundscape, prompt.non_diegetic_music, ctx, out)
        return out

    p: H3Prompt = prompt
    if not p.integrated_multimodal_description.strip():
        _err(out, "empty_field", "integrated_multimodal_description",
             "integrated_multimodal_description is mandatory and must not be empty.")
    else:
        _check_body(p.integrated_multimodal_description,
                    "integrated_multimodal_description", ctx, out)
    _check_audio_fields(p.overall_soundscape, p.non_diegetic_music, ctx, out)

    # coherencia entre modo y referencias conectadas
    need = {Mode.I2VA: 1, Mode.L2VA: 1, Mode.FL2VA: 2}.get(p.mode, 0)
    if ctx.n_pictures < need:
        _err(out, "mode_refs", "mode",
             f"{p.mode.value.upper()} needs {need} reference picture(s), {ctx.n_pictures} connected.")
    if p.mode is Mode.FL2VA and len(set(shots(p.integrated_multimodal_description))) > 1:
        _warn(out, "fl2va_multishot", "integrated_multimodal_description",
              "FL2VA favours a single shot so the model can interpolate continuously "
              "between the two frames; use multiple shots only if explicitly requested.")
    return out


def _check_ref_sections(p: H3RefPrompt, ctx: Context, out: list):
    """Reglas propias de Ref2VA (guía ref §3 y §4)."""
    # summary: prefijo de tipo de tarea
    s = p.summary.strip()
    m = re.match(r"\[([^\]]+)\]", s)
    if not m:
        _err(out, "summary_prefix", "summary",
             f"summary must start with a bracketed task-type prefix, one or more of: "
             f"{', '.join(TASK_TYPES)} joined with ' + '.")
    else:
        parts = [t.strip() for t in m.group(1).split("+")]
        for t in parts:
            if t not in TASK_TYPES:
                _err(out, "summary_task_type", "summary",
                     f"Unknown task type {t!r}. Allowed: {', '.join(TASK_TYPES)}.")
        if len(parts) != len(set(parts)):
            _err(out, "summary_task_dup", "summary", "Do not repeat a task type in the prefix.")

    # etiquetas definidas vs usadas
    defined = {f"{k} {n}" for k, n in
               ((m.group(1), m.group(2)) for m in _REF_RE.finditer(p.subject_definitions))}
    for fld in ("summary", "retention_analysis", "detailed_description"):
        for mm in _REF_RE.finditer(getattr(p, fld)):
            label = f"{mm.group(1)} {mm.group(2)}"
            if label not in defined:
                _err(out, "undefined_label", fld,
                     f"<{label}> is used but never defined in subject_definitions.")

    # retention_analysis: un marcador cerrado por línea, y sin IDs de hablante
    for line in (l.strip() for l in p.retention_analysis.splitlines() if l.strip()):
        mm = _REF_RE.match(line)
        if not mm:
            _err(out, "retention_line", "retention_analysis",
                 f"Each line must start with a reference label: {line[:60]!r}")
            continue
        allowed = RETENTION_AUDIO if mm.group(1) == "Audio" else RETENTION_VISUAL
        if not re.search(rf":\s*({'|'.join(allowed)})\s+-\s+", line):
            _err(out, "retention_marker", "retention_analysis",
                 f"<{mm.group(1)} {mm.group(2)}> needs one fixed relationship marker "
                 f"followed by ' - ': {', '.join(allowed)}.")
    if _SPK_RE.search(p.retention_analysis):
        _err(out, "retention_speaker", "retention_analysis",
             "Do not write speaker IDs (Sx) in retention_analysis.")

    # detailed_description: el estilo va ANTES de [Shot 1] en este modo
    body = p.detailed_description.strip()
    if body.startswith("[Shot 1]"):
        _warn(out, "ref_style_position", "detailed_description",
              "In full-reference mode the style goes in one or two sentences BEFORE [Shot 1], "
              "not inside it.")
    words = len(body.split())
    if not (350 <= words <= 500):
        _warn(out, "ref_length", "detailed_description",
              f"Generation tasks normally run 350-500 English words; found {words}.")


def validate_text(raw: str, mode: Mode, ctx: Context) -> tuple[object, list[Issue]]:
    """Valida lo que devolvió el LLM, crudo. Devuelve (prompt, issues).

    Además de las reglas de contenido revisa la envoltura: cháchara antes del
    primer campo, campos desordenados o ausentes, y markdown."""
    out: list[Issue] = []
    names = REF_FIELDS if mode is Mode.REF2VA else BASE_FIELDS

    lead = leading_text(raw, mode)
    if lead:
        _err(out, "leading_text", "output",
             f"Output must contain only the prompt fields. Remove the text before the first "
             f"field: {lead[:80]!r}")
    if "```" in raw:
        _err(out, "markdown_fence", "output", "Remove markdown code fences from the output.")
    if re.search(r"^\s*[-*]\s+", raw, re.M) and mode is not Mode.REF2VA:
        _warn(out, "markdown_bullets", "output",
              "Write continuous prose, not bullet lists.")

    order = [n for n in re.findall(rf"^({'|'.join(names)})\s*:", raw, re.M)]
    missing = [n for n in names if n not in order]
    if missing:
        _err(out, "missing_fields", "output",
             f"Missing mandatory field(s): {', '.join(missing)}. Emit exactly "
             f"{len(names)} fields in this order: {', '.join(names)}.")
    if order and order != [n for n in names if n in order]:
        _err(out, "field_order", "output",
             f"Fields must appear in this order: {', '.join(names)}; found {', '.join(order)}.")

    prompt = parse(raw, mode, ctx.duration_s)
    out += validate(prompt, ctx)

    # la instrucción de alineación solo la lleva el modo que la pide (§2.1)
    if mode in (Mode.I2VA, Mode.FL2VA, Mode.L2VA):
        expected = prompt.instruction()
        # El grid de 17n+5 frames desplaza la duración: pedir 6s produce 6.58s.
        # La guía escribe la duración pedida (en la API son la misma cosa) y en
        # local lo correcto es la real, así que se aceptan ambas.
        nominal = expected.replace(f"{effective_duration(ctx.duration_s):.2f}",
                                   f"{min(ctx.duration_s, 15.0):.2f}")
        first = raw.strip().splitlines()[0].strip() if raw.strip() else ""
        if first not in (expected, nominal):
            _err(out, "instruction_line", "output",
                 f"The first line must be exactly:\n{expected}")
    elif mode is Mode.T2VA and raw.strip().startswith(("For the target video,",
                                                       "How the reference pictures align")):
        _err(out, "instruction_forbidden", "output",
             "T2VA has no image-alignment instruction; start directly with the fields.")

    if ctx.delivery == "api" and len(raw) > API_CHAR_LIMIT:
        _err(out, "too_long", "output",
             f"{len(raw)} characters exceeds the {API_CHAR_LIMIT}-character API limit.")
    return prompt, out


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def repair_brief(issues: list[Issue]) -> str:
    """Los errores, redactados para devolvérselos al LLM en la reparación."""
    errs = errors(issues)
    if not errs:
        return ""
    lines = "\n".join(f"- {i.field}: {i.message}" for i in errs)
    return ("Your previous output violated the MiniMax H3 prompt contract. Fix exactly "
            "these problems and return the corrected prompt, with no other changes and no "
            f"commentary:\n{lines}")
