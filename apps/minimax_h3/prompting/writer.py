"""
El escritor: idea suelta -> prompt H3 válido, con un LLM local.

Repone lo que hace H3-Context-IR en la nube. La estrategia es no pedirle el
prompt entero de una vez: la guía completa son ~10k tokens de system prompt y
un 12B local se ahoga. Se pide una cosa por llamada, cada una con solo la
sección de guía que le toca:

    0. captions   (solo Ref2VA)  imágenes -> qué es cada referencia
    1. timeline                  idea -> planos con su segundo de corte
    2. visual                    timeline -> integrated_multimodal_description
    3. soundscape                -> overall_soundscape
    4. music                     -> non_diegetic_music

Cada llamada devuelve SOLO el contenido de su campo —nunca el nombre del campo,
nunca la línea de instrucción, nunca markdown—: lo que el modelo no escribe no
lo puede romper, y el ensamblado determinista de `schema.py` pone el resto.

Lo que sale se valida con `validator.py`; si falla, se le devuelven los errores
al modelo y se reescribe SOLO el campo culpable, hasta `max_repairs`. Las
cuatro llamadas mapean 1:1 con las tres cajas de la interfaz, así que el mismo
código sirve para "enriquecer todo" y para "reescribir solo la música".
"""
import re

import time
from dataclasses import dataclass, field

from .guides import load_guide
from .schema import (Mode, H3Prompt, H3RefPrompt, BASE_FIELDS, REF_FIELDS,
                     effective_duration, timecode, shots, cuts)
from .validator import Context, validate_text, errors, repair_brief, Issue
from ..core.lmstudio import LMStudio, LMStudioError


class WriterError(Exception):
    pass


@dataclass
class WriterConfig:
    model: str = ""
    temperature: float = 0.4        # obedecer un contrato, no inventar prosa
    # Presupuesto generoso a propósito: los razonadores (Gemma 4, Qwen3.x)
    # cobran su `reasoning_content` contra max_tokens, y con un system prompt de
    # varios KB se lo gastan entero pensando — el síntoma es un `content` vacío
    # que no parece un problema de presupuesto (ver core/lmstudio.py).
    max_tokens: int = 3000
    max_repairs: int = 2
    timeout: float = 300.0
    # "none" apaga el razonamiento de los modelos que lo traen (verificado con
    # Gemma 4 en LM Studio: pasa de ~300 tokens de razonamiento a 0 sin perder
    # calidad en la respuesta). Acá interesa obedecer un contrato, no deliberar,
    # y razonar cuesta tiempo y presupuesto. "auto" lo deja como venga.
    reasoning: str = "none"


@dataclass
class Step:
    """Traza de una llamada, para el banco y para enseñar qué pasó en la UI."""
    name: str
    seconds: float
    chars: int
    repaired: int = 0
    note: str = ""


@dataclass
class Result:
    prompt: object                       # H3Prompt | H3RefPrompt
    issues: list = field(default_factory=list)
    steps: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not errors(self.issues)

    @property
    def repairs(self) -> int:
        return sum(s.repaired for s in self.steps)

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    def text(self) -> str:
        return self.prompt.assemble()


# ── Reglas de salida comunes a todas las llamadas ────────────────────────────

_OUTPUT_RULES = """
OUTPUT RULES (strict):
- Return ONLY the content of the requested field, as continuous English prose.
- Do NOT write the field name, do NOT add a preamble, a closing remark, quotes,
  markdown, bullet lists or code fences.
- Write descriptions in English. Preserve dialogue, lyrics and on-screen text in
  their original language, character for character.
- Never invent spoken lines. Use exactly the dialogue the brief provides, if any.
"""


def _system(guide: str, recipe: str, task: str) -> str:
    """System prompt de una llamada: el trozo de guía que aplica + su tarea."""
    return (load_guide(guide).recipe(recipe,
            preamble="You are the prompt preprocessor for the MiniMax H3 video model. "
                     "Follow this excerpt of the official H3 prompt writing guide exactly.")
            + "\n\n" + task.strip() + "\n" + _OUTPUT_RULES)


def _brief(idea: str, ctx: Context, mode: Mode, extra: str = "") -> str:
    """El encargo, con lo que el backend ya sabe (y el modelo no debe adivinar)."""
    dur = effective_duration(ctx.duration_s)
    lines = [f"MODE: {mode.name}",
             f"EFFECTIVE DURATION: {dur:.2f} seconds (every cut time must fall inside it)",
             f"CONNECTED REFERENCES: {ctx.n_pictures} image(s), {ctx.n_videos} video(s), "
             f"{ctx.n_audios} audio(s)"]
    if ctx.dialogue:
        spoken = "\n".join(f"  - {d}" for d in ctx.dialogue)
        lines.append("DIALOGUE THE USER WROTE (reproduce character for character inside "
                     f"<d>[Language] ...</d>, never translate or rephrase):\n{spoken}")
    if ctx.silence_requested:
        lines.append("The user asked for complete silence: overall_soundscape is N/A.")
    lines.append(f"\nUSER IDEA (may be written in any language):\n{idea.strip()}")
    if extra:
        lines.append("\n" + extra.strip())
    return "\n".join(lines)


# El plan de planos se pide en texto plano con separadores, NO con
# response_format json_schema: los VLM de Gemma en LM Studio devuelven content
# vacío bajo gramática (con imagen y sin ella), y es la misma conclusión a la
# que llegó Ideogram. Un formato de cuatro columnas se parsea igual de bien y
# funciona con cualquier backend.
_TIMELINE_FORMAT = """
Output format — one line per shot and nothing else:

STYLE | <visual style of the clip>
SHOT 1 | 0 | <camera motion> | <what happens in this shot>
SHOT 2 | <cut time in seconds> | <camera motion> | <what happens in this shot>
"""

_TL_LINE = re.compile(r"^\s*SHOT\s*(\d+)\s*[|:]\s*([\d.]+)\s*\|(.*)$", re.I)
_TL_STYLE = re.compile(r"^\s*STYLE\s*[|:]\s*(.+)$", re.I | re.M)
# El \s* va DENTRO del lookahead: si queda fuera, retrocede a cero caracteres,
# el lookahead se evalúa sobre el espacio, no ve el "At ..." que sí estaba, y
# vuelve a sellar un plano ya sellado.
_SHOT_HEAD = re.compile(r"\[Shot (\d+)\](?!\s*At \d{2}:\d{2}\.\d{3})\s*")


def _stamp_cuts(body: str, tl: dict, dur: float = 0.0) -> str:
    """Pone el tiempo de corte que el modelo se dejó.

    Los tiempos ya los decidió la fase de timeline, así que un [Shot N] sin
    `At MM:SS.mmm,` no es información que falte: es transcripción. Escribirla
    acá evita gastar reparaciones —y se comprobó que un modelo puede quemar tres
    seguidas sin acertar— en algo que el código sabe con certeza.

    El modelo también escribe planos que no estaban en el plan (típico en FL2VA,
    donde el plan trae uno solo porque la guía lo prefiere así). A esos se les
    reparte el tiempo que queda hasta el final del clip, en vez de dejarlos sin
    sellar: el corte inventado es discutible, pero un corte sin hora es inválido
    y no hay forma de que el modelo adivine la hora correcta."""
    times = {i: s["at"] for i, s in enumerate(tl.get("shots", []), start=1)}

    # Hay modelos que escriben la prosa correcta y se olvidan del marcador de
    # plano —observado de forma sistemática en un finetune, 0 de 3 pasadas, sin
    # que tres reparaciones lo corrigieran—. Si no marcó ninguno, el plano es el
    # primero por definición y se le pone la etiqueta en vez de discutirlo.
    if not re.search(r"\[Shot \d+\]", body) and body.strip():
        body = f"[Shot 1] {body.lstrip()}"

    written = [int(m.group(1)) for m in re.finditer(r"\[Shot (\d+)\]", body)]
    extra = sorted({n for n in written if n > 1 and n not in times})
    if extra and dur > 0:
        last = max([times[n] for n in written if n in times] or [0.0])
        step = (dur - last) / (len(extra) + 1)
        for k, n in enumerate(extra, start=1):
            times[n] = round(last + step * k, 3)

    def repl(m):
        n = int(m.group(1))
        if n == 1 or n not in times:
            return m.group(0)          # el primer plano nunca lleva timestamp
        return f"[Shot {n}] At {timecode(times[n])}, "

    return _SHOT_HEAD.sub(repl, body)



def _plan_from_prompt(prompt) -> tuple[str, dict]:
    """Reconstruye el plan de planos desde el cuerpo ya escrito.

    Una reescritura no vuelve a preguntar la línea de tiempo al modelo: los
    cortes ya están en el texto y volver a planificarlos costaría una llamada
    para llegar, con suerte, al mismo sitio."""
    body = (getattr(prompt, "detailed_description", "")
            or getattr(prompt, "integrated_multimodal_description", ""))
    at = dict(cuts(body))
    rows, plan_shots = [], []
    for n in sorted(set(shots(body)) | {1}):
        t = 0.0 if n == 1 else at.get(n, 0.0)
        plan_shots.append({"at": t, "camera": "", "beat": ""})
        rows.append(f"[Shot {n}] " + ("opens the clip" if n == 1
                                      else f"cuts at {timecode(t)}"))
    return ("SHOT PLAN ALREADY FIXED (keep exactly these shots and cut times):\n"
            + "\n".join(rows)), {"shots": plan_shots}

class PromptWriter:
    def __init__(self, lms: LMStudio, cfg: WriterConfig):
        self.lms = lms
        self.cfg = cfg

    # ── una llamada, con su reintento de reparación ──────────────────────────

    async def _ask(self, system: str, user: str, name: str, steps: list,
                   max_tokens: int | None = None) -> str:
        t0 = time.time()
        try:
            out = await self.lms.chat_text(
                self.cfg.model,
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                temperature=self.cfg.temperature,
                max_tokens=max_tokens or self.cfg.max_tokens,
                timeout=self.cfg.timeout,
                extra=None if self.cfg.reasoning == "auto"
                else {"reasoning_effort": self.cfg.reasoning})
        except LMStudioError as e:
            raise WriterError(f"{name}: {e}") from e
        out = _strip(out)
        steps.append(Step(name=name, seconds=time.time() - t0, chars=len(out)))
        return out

    # ── fases ────────────────────────────────────────────────────────────────

    async def _timeline(self, idea: str, ctx: Context, mode: Mode, steps: list) -> dict:
        """Los planos y sus tiempos de corte.

        El modelo propone; la aritmética la hacemos acá. Un corte que no cabe en
        la duración o que no crece se descarta en código en vez de pedirle al
        modelo que sepa contar."""
        system = _system("base", "timeline",
                         "Plan the shot list for the clip. Use as few shots as the idea "
                         "needs; a single shot is normal for short clips and is what FL2VA "
                         "wants. The first shot is always at 0 and later cut times must "
                         "strictly increase and stay below the effective duration. Camera "
                         "motion uses one motion type from the guide's table, plus amplitude "
                         "and speed only when meaningful." + _TIMELINE_FORMAT)
        raw = await self._ask(system, _brief(idea, ctx, mode), "timeline", steps,
                              max_tokens=1800)

        dur = effective_duration(ctx.duration_s)
        style_m = _TL_STYLE.search(raw)
        shots, seen = [], set()
        for line in raw.splitlines():
            m = _TL_LINE.match(line)
            if not m:
                continue
            at = 0.0 if not shots else float(m.group(2) or 0)
            rest = [p.strip() for p in m.group(3).split("|")]
            camera, beat = (rest + ["", ""])[:2]
            if shots and (at <= 0 or at >= dur or at in seen):
                continue                     # corte imposible: se descarta el plano
            seen.add(at)
            shots.append({"at": at, "camera": camera, "beat": beat})
        if not shots:
            # el modelo ignoró el formato: se sigue con un plano único en vez de
            # abortar, y el validador dirá si la prosa resultante sirve
            shots = [{"at": 0.0, "camera": "holds a static shot", "beat": idea.strip()[:400]}]
        data = {"style": style_m.group(1).strip() if style_m else "live-action, cinematic",
                "shots": shots}
        steps[-1].note = f"{len(shots)} plano(s)"
        return data

    def _timeline_text(self, tl: dict) -> str:
        rows = []
        for i, s in enumerate(tl.get("shots", []), start=1):
            when = "opens the clip" if i == 1 else f"cuts at {timecode(s['at'])}"
            rows.append(f"[Shot {i}] ({when}) camera: {s['camera']}\n  {s['beat']}")
        return f"VISUAL STYLE: {tl.get('style', '')}\nSHOT PLAN:\n" + "\n".join(rows)

    async def write(self, idea: str, ctx: Context, mode: Mode = Mode.T2VA,
                    captions: list[str] | None = None) -> Result:
        """Escribe el prompt completo de un modo base."""
        if mode is Mode.REF2VA:
            raise WriterError("Ref2VA usa write_ref(); su formato son seis secciones")
        steps: list[Step] = []
        tl = await self._timeline(idea, ctx, mode, steps)
        plan = self._timeline_text(tl)
        if captions:
            plan += "\n\nREFERENCE IMAGES:\n" + "\n".join(
                f"  <Picture {i}> {c}" for i, c in enumerate(captions, start=1))

        keyframe = {
            Mode.I2VA: "<Picture 1> is the actual first frame at 0.00s and belongs to "
                       "[Shot 1]: anchor its subject, composition and scene first, then "
                       "develop the action forward.",
            Mode.FL2VA: "Picture 1 is the opening and Picture 2 is the ending. Describe the "
                        "motion path that connects them; do not describe two static images.",
            Mode.L2VA: "<Picture 1> is the FINAL frame and belongs to the last shot. Infer a "
                       "plausible earlier state and converge onto it.",
        }.get(mode, "")

        visual = await self._ask(
            _system("base", "visual",
                    "Write the integrated_multimodal_description for the shot plan below: "
                    "visuals, actions, camera, speakers, dialogue and the sound tied to each "
                    "moment, along the timeline. Start with [Shot 1] and the visual style. "
                    "Later shots start with their cut time. " + keyframe),
            _brief(idea, ctx, mode, plan), "visual", steps)

        soundscape = await self._ask(
            _system("base", "soundscape",
                    "Write overall_soundscape: 1-4 sentences of ambience, physical action "
                    "sounds and non-verbal human sounds across the whole clip. No dialogue, "
                    "no singing, no score. H3 always generates audio: if you leave this "
                    "vague it invents room tone and speech-like noise."),
            _brief(idea, ctx, mode, plan), "soundscape", steps, max_tokens=300)

        music = await self._ask(
            _system("base", "music",
                    "Write non_diegetic_music: 1-3 sentences on instrumentation, tempo, "
                    "rhythm and dynamics of the audience-only score. No mood words, no "
                    "explanation of what it makes the viewer feel. Write exactly 'N/A' if "
                    "the clip should carry no score."),
            _brief(idea, ctx, mode, plan), "music", steps, max_tokens=200)

        prompt = H3Prompt(mode=mode, duration_s=ctx.duration_s,
                          integrated_multimodal_description=_stamp_cuts(
                              visual, tl, effective_duration(ctx.duration_s)),
                          overall_soundscape=soundscape,
                          non_diegetic_music=music)
        return await self._repair(prompt, ctx, mode, plan, steps)

    # ── Ref2VA: seis secciones ───────────────────────────────────────────────

    async def write_ref(self, idea: str, ctx: Context, captions: list[str] | None = None,
                        images_b64: list[str] | None = None) -> Result:
        """Ref2VA: el modo del tramo 1 de una cadena.

        No es el formato base con adornos — son seis secciones y el cuerpo se
        llama `detailed_description`. El orden importa porque cada sección se
        apoya en la anterior: primero se define qué es cada referencia, y recién
        con eso a la vista se escribe el resto."""
        steps: list[Step] = []
        if captions is None and images_b64:
            captions = await self.describe_references(images_b64, steps)
        refs = "\n".join(f"  <Picture {i}> {c}" for i, c in enumerate(captions or [], start=1))
        refs = f"WHAT EACH CONNECTED REFERENCE SHOWS:\n{refs}" if refs else ""

        subjects = await self._ask(
            _system("ref", "ref_subjects",
                    "Write the subject_definitions section: one line per piece of referenced "
                    "content that has to be tracked later, saying what its label denotes, its "
                    "reference role and the features to follow. Define only labels backed by "
                    "a connected reference."),
            _brief(idea, ctx, Mode.REF2VA, refs), "ref:subjects", steps)

        tl = await self._timeline(idea, ctx, Mode.REF2VA, steps)
        plan = self._timeline_text(tl)
        known = f"SUBJECT DEFINITIONS ALREADY WRITTEN (reuse these labels verbatim, never " \
                f"invent new ones):\n{subjects}"

        summary = await self._ask(
            _system("ref", "ref_summary",
                    "Write the summary section: one short paragraph starting with the "
                    "bracketed task-type prefix, using the labels already defined."),
            _brief(idea, ctx, Mode.REF2VA, f"{known}\n\n{plan}"), "ref:summary", steps,
            max_tokens=800)

        retention = await self._ask(
            _system("ref", "ref_retention",
                    "Write the retention_analysis section: one line per reference label, each "
                    "with exactly one fixed relationship marker followed by ' - ' and the "
                    "explanation. Never write speaker IDs here."),
            _brief(idea, ctx, Mode.REF2VA, f"{known}\n\n{plan}"), "ref:retention", steps,
            max_tokens=900)

        detailed = await self._ask(
            _system("ref", "ref_detailed",
                    "Write the detailed_description section: state the visual style in one or "
                    "two sentences BEFORE [Shot 1], then describe each shot in playback order "
                    "inserting the reference labels where they apply."),
            _brief(idea, ctx, Mode.REF2VA, f"{known}\n\n{plan}"), "ref:detailed", steps)

        soundscape = await self._ask(
            _system("ref", "ref_audio",
                    "Write ONLY the overall_soundscape section: ambience and physical sounds "
                    "across the clip, no dialogue and no score."),
            _brief(idea, ctx, Mode.REF2VA, known), "ref:soundscape", steps, max_tokens=400)

        music = await self._ask(
            _system("ref", "ref_audio",
                    "Write ONLY the non_diegetic_music section: audience-only score, with "
                    "instrumentation, tempo and dynamics. Write exactly 'N/A' if there is none."),
            _brief(idea, ctx, Mode.REF2VA, known), "ref:music", steps, max_tokens=300)

        prompt = H3RefPrompt(duration_s=ctx.duration_s, subject_definitions=subjects,
                             summary=summary, retention_analysis=retention,
                             detailed_description=_stamp_cuts(
                                 detailed, tl, effective_duration(ctx.duration_s)),
                             overall_soundscape=soundscape,
                             non_diegetic_music=music)
        return await self._repair(prompt, ctx, Mode.REF2VA, plan, steps)

    # ── reescritura de una sola caja ─────────────────────────────────────────

    async def rewrite_field(self, prompt, field: str, ctx: Context, note: str = "",
                            idea: str = "") -> Result:
        """Reescribe UN campo dejando los demás intactos.

        Es el botón "reescribir esta caja" de la interfaz. No se re-planifica la
        línea de tiempo: los planos y sus cortes se leen del cuerpo que ya está
        escrito, así que cambiar la música no puede mover un corte de plano."""
        mode = prompt.mode
        recipes = self._FIELD_RECIPE_REF if mode is Mode.REF2VA else self._FIELD_RECIPE
        if field not in recipes:
            raise WriterError(f"campo desconocido: {field}")
        guide, recipe = recipes[field]
        plan, tl = _plan_from_prompt(prompt)

        steps: list[Step] = []
        user = (_brief(idea, ctx, mode, plan)
                + f"\n\nTHE {field} AS IT STANDS:\n{getattr(prompt, field).strip()}")
        if note.strip():
            user += f"\n\nWHAT THE USER WANTS CHANGED:\n{note.strip()}"
        out = await self._ask(
            _system(guide, recipe,
                    f"Rewrite the {field} field of a prompt that already exists. Apply what "
                    "the user asks and keep everything else as it was; do not restructure "
                    "what was not questioned."),
            user, f"rewrite:{field}", steps)

        if field in ("integrated_multimodal_description", "detailed_description"):
            out = _stamp_cuts(out, tl, effective_duration(ctx.duration_s))
        setattr(prompt, field, out)
        return await self._repair(prompt, ctx, mode, plan, steps)

    # ── bucle de reparación ──────────────────────────────────────────────────

    _FIELD_RECIPE = {"integrated_multimodal_description": ("base", "visual"),
                     "overall_soundscape": ("base", "soundscape"),
                     "non_diegetic_music": ("base", "music")}
    _FIELD_RECIPE_REF = {"subject_definitions": ("ref", "ref_subjects"),
                         "summary": ("ref", "ref_summary"),
                         "retention_analysis": ("ref", "ref_retention"),
                         "detailed_description": ("ref", "ref_detailed"),
                         "overall_soundscape": ("ref", "ref_audio"),
                         "non_diegetic_music": ("ref", "ref_audio")}

    async def _repair(self, prompt, ctx: Context, mode: Mode, plan: str,
                      steps: list) -> Result:
        """Revalida y reescribe SOLO los campos con error. El modelo nunca ve
        el prompt entero para arreglar una coma: se le devuelve su campo, la
        sección de guía que incumplió y la lista de errores."""
        for _ in range(self.cfg.max_repairs + 1):
            _, issues = validate_text(prompt.assemble(), mode, ctx)
            errs = errors(issues)
            if not errs:
                return Result(prompt, issues, steps)

            recipes = (self._FIELD_RECIPE_REF if mode is Mode.REF2VA
                       else self._FIELD_RECIPE)
            targets = {i.field for i in errs} & set(recipes)
            if not targets:
                # errores que no cuelgan de un campo concreto (envoltura, modo):
                # el ensamblado ya los evita, así que no hay nada que reescribir
                return Result(prompt, issues, steps)

            for fld in targets:
                guide, recipe = recipes[fld]
                name = recipe
                brief = repair_brief([i for i in errs if i.field == fld])
                fixed = await self._ask(
                    _system(guide, recipe,
                            f"You previously wrote the {fld} field. Rewrite it fixing the "
                            "listed problems and changing nothing else."),
                    _brief("", ctx, mode, plan) + f"\n\nYOUR PREVIOUS {fld}:\n"
                    + getattr(prompt, fld) + "\n\n" + brief,
                    f"repair:{name}", steps)
                setattr(prompt, fld, fixed)
                steps[-1].repaired = 1
        _, issues = validate_text(prompt.assemble(), mode, ctx)
        return Result(prompt, issues, steps)

    # ── Ref2VA ───────────────────────────────────────────────────────────────

    async def describe_references(self, images_b64: list[str], steps: list | None = None
                                  ) -> list[str]:
        """Paso 0: qué hay en cada imagen de referencia, en una frase.

        Con esto el escritor puede ser text-only y, sobre todo, el modelo deja
        de adivinar a qué corresponde cada <Picture N>: el backend ya sabe qué
        inputs están conectados."""
        steps = steps if steps is not None else []
        out = []
        for i, b64 in enumerate(images_b64, start=1):
            t0 = time.time()
            msg = [{"role": "system",
                    "content": "Describe the reference image in one factual English sentence: "
                               "subject, distinctive appearance, clothing, setting. No "
                               "interpretation, no mood, no preamble."},
                   {"role": "user", "content": [
                       {"type": "text", "text": "Describe this reference image."},
                       {"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
            try:
                cap = _strip(await self.lms.chat_text(self.cfg.model, msg, temperature=0.2,
                                                      max_tokens=180,
                                                      timeout=self.cfg.timeout))
            except LMStudioError as e:
                raise WriterError(f"caption de <Picture {i}>: {e}") from e
            out.append(cap)
            steps.append(Step(name=f"caption:{i}", seconds=time.time() - t0, chars=len(cap)))
        return out


_ALL_FIELDS = tuple(dict.fromkeys(BASE_FIELDS + REF_FIELDS))
_FENCE = re.compile(r"^\s*```[a-z]*\n(.*?)```\s*$", re.S)
_FIELD_PREFIX = re.compile(rf"^\s*({'|'.join(_ALL_FIELDS)})\s*:\s*", re.I)
_NEXT_FIELD = re.compile(rf"^({'|'.join(_ALL_FIELDS)})\s*:", re.I | re.M)


def _strip(text: str) -> str:
    """Quita lo que el modelo añade por costumbre aunque se le pida que no:
    bloques markdown, el nombre del campo delante, comillas envolventes y —el
    más habitual— los campos siguientes. Se le pide un campo y contesta con el
    prompt entero; como cada campo tiene su propia llamada especializada, lo que
    sobra se corta en vez de aprovecharse."""
    t = (text or "").strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1).strip()
    t = _FIELD_PREFIX.sub("", t)
    nxt = _NEXT_FIELD.search(t)
    if nxt:
        t = t[:nxt.start()].strip()
    if len(t) > 1 and t[0] == t[-1] == '"':
        t = t[1:-1].strip()
    return t
