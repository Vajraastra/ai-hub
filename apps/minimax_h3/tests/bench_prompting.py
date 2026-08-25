"""
Banco de calibración del contrato de prompts H3.

Tres pasadas, ninguna necesita GPU ni LLM:

  1. `oficiales` — valida los cinco prompts que resolvió el propio MiniMax en
     sus guías. Deben pasar sin un solo error: si el validador rechaza un
     ejemplo oficial, el error es nuestro, no del ejemplo.
  2. `mutaciones` — rompe a propósito un prompt válido de diez maneras y exige
     que el validador cace cada una. Sin esta pasada, un validador que siempre
     dice "ok" también aprobaría la primera.
  3. `round-trip` — parse -> assemble sobre esos mismos ejemplos, para probar
     que el ensamblado determinista devuelve el prompt intacto.

Los casos se extraen de `prompting/guides/*.txt` en caliente, no están copiados
acá: si MiniMax revisa sus guías, se reemplazan los .txt y el banco se actualiza
solo.

    python apps/minimax_h3/tests/bench_prompting.py
"""
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from prompting.guides import load_guide                                    # noqa: E402
from prompting.schema import Mode, effective_duration                  # noqa: E402
from prompting.validator import Context, validate_text, errors             # noqa: E402

_BLOCK = re.compile(r"```text\n(.*?)```", re.S)
_CASE = re.compile(r"### Case \d+: (\w+)")


@dataclass
class Case:
    name: str
    mode: Mode
    text: str
    ctx: Context = field(default_factory=Context)


# Duración y referencias conectadas de cada ejemplo. La guía las declara en su
# prosa ("an eight-second single shot"); los casos 1 y 2 no la dicen, así que se
# infiere del corte más tardío.
_OFFICIAL_CTX = {
    "T2VA":  Context(duration_s=10, n_pictures=0),
    "I2VA":  Context(duration_s=5,  n_pictures=1),
    "FL2VA": Context(duration_s=8,  n_pictures=2),
    "L2VA":  Context(duration_s=6,  n_pictures=1),
}
_REF_CTX = Context(duration_s=8, n_pictures=4, n_videos=2, n_audios=1)


def load_official_cases() -> list[Case]:
    cases = []
    body = load_guide("base").section("5")
    for m in _CASE.finditer(body):
        mode_name = m.group(1)
        block = _BLOCK.search(body, m.end())
        if not block:
            continue
        cases.append(Case(name=mode_name, mode=Mode[mode_name],
                          text=block.group(1).strip(), ctx=_OFFICIAL_CTX[mode_name]))
    ref = _BLOCK.search(load_guide("ref").section("7"))
    if ref:
        cases.append(Case(name="REF2VA", mode=Mode.REF2VA,
                          text=ref.group(1).strip(), ctx=_REF_CTX))
    return cases


def run_official() -> int:
    cases = load_official_cases()
    print(f"\n=== Ejemplos oficiales ({len(cases)}) ===")
    if len(cases) != 5:
        print(f"  ! se esperaban 5 casos en las guías, se extrajeron {len(cases)}")
    failed = 0
    for c in cases:
        _, issues = validate_text(c.text, c.mode, c.ctx)
        errs, warns = errors(issues), [i for i in issues if i.severity == "warning"]
        mark = "OK  " if not errs else "FALLA"
        print(f"  [{mark}] {c.name:<7} {len(c.text):>5} chars  "
              f"{len(errs)} errores, {len(warns)} avisos")
        for i in errs:
            print(f"          ERROR {i.code} :: {i.field}: {i.message}")
        for i in warns:
            print(f"          aviso {i.code} :: {i.field}")
        failed += bool(errs)
    return failed


# ── Mutaciones: romper a propósito y exigir que se detecte ───────────────────

def _mutations(base: str) -> list[tuple[str, str, str]]:
    """(nombre, código de error esperado, texto roto)."""
    out = []
    out.append(("cháchara antes del prompt", "leading_text",
                "Here is your optimized prompt:\n\n" + base))
    out.append(("campo faltante", "missing_fields",
                re.sub(r"\n\nnon_diegetic_music:.*", "", base, flags=re.S)))
    out.append(("campos desordenados", "field_order",
                "\n\n".join(reversed(base.split("\n\n")))))
    out.append(("corte fuera de la duración", "cut_out_of_range",
                base.replace("At 00:05.000", "At 00:59.000")))
    out.append(("timestamp en el Shot 1", "shot1_timestamp",
                base.replace("[Shot 1] Live-action", "[Shot 1] At 00:01.000, Live-action")))
    out.append(("diálogo sin etiqueta de idioma", "dialogue_no_lang",
                base.replace("<d>[English] ", "<d>")))
    out.append(("referencia inexistente", "ref_out_of_range",
                base.replace("[Shot 2]", "[Shot 2] <Picture 7>")))
    out.append(("bloque markdown", "markdown_fence", f"```\n{base}\n```"))
    out.append(("soundscape en N/A sin pedir silencio", "soundscape_na",
                re.sub(r"overall_soundscape: [^\n]+", "overall_soundscape: N/A", base)))
    # el diálogo del usuario, "mejorado" por el modelo
    out.append(("diálogo reescrito", "dialogue_not_verbatim",
                base.replace("First batch of the morning.", "The first batch of the morning!")))
    return out


def run_mutations() -> int:
    t2va = next(c for c in load_official_cases() if c.name == "T2VA")
    ctx = Context(duration_s=10, n_pictures=0,
                  dialogue=["First batch of the morning."])
    print("\n=== Mutaciones (debe cazarlas todas) ===")
    failed = 0
    for name, expected, broken in _mutations(t2va.text):
        _, issues = validate_text(broken, Mode.T2VA, ctx)
        codes = {i.code for i in errors(issues)}
        ok = expected in codes
        print(f"  [{'OK  ' if ok else 'FALLA'}] {name:<38} -> {expected}"
              + ("" if ok else f"   (detectó: {sorted(codes) or 'nada'})"))
        failed += not ok
    return failed


def run_roundtrip() -> int:
    """parse -> assemble debe devolver el prompt oficial intacto. Es lo que
    prueba que el ensamblado (línea de instrucción incluida) es determinista y
    no depende del LLM."""
    print("\n=== Round-trip parse -> assemble ===")
    failed = 0
    for c in load_official_cases():
        prompt, _ = validate_text(c.text, c.mode, c.ctx)
        rebuilt = prompt.assemble()
        norm = lambda s: re.sub(r"\s+", " ", s).strip()                    # noqa: E731
        # Corrección esperada, no divergencia: las guías escriben la duración
        # PEDIDA en la línea de alineación porque en la API son lo mismo. En
        # local el grid de 17n+5 frames la desplaza (6.00 -> 6.58) y el clip
        # real dura eso, así que el ensamblado ancla el último frame al final
        # verdadero. Si no lo hiciera, quedaría material después del keyframe.
        nominal, real = f"{min(c.ctx.duration_s, 15.0):.2f}", f"{effective_duration(c.ctx.duration_s):.2f}"
        want = norm(c.text).replace(f"{nominal}-second", f"{real}-second")
        same = norm(rebuilt) == want
        # la nota solo aplica a los modos que llevan línea de alineación
        shows_duration = f"{nominal}-second" in norm(c.text)
        note = f"   (duración cuadrada al grid: {nominal} -> {real})" if shows_duration and nominal != real else ""
        print(f"  [{'OK  ' if same else 'FALLA'}] {c.name}{note}")
        if not same:
            for a, b in zip(norm(c.text).split(" "), norm(rebuilt).split(" ")):
                if a != b:
                    print(f"          diverge en {a!r} != {b!r}")
                    break
        failed += not same
    return failed


def main() -> int:
    bad = run_official() + run_mutations() + run_roundtrip()
    print("\n" + ("Todo en orden." if not bad else f"{bad} caso(s) fallando."))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
