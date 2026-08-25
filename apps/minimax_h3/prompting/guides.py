"""
Carga y troceo de las guías oficiales de prompting de H3.

Las guías completas son ~10k tokens (base) y ~14k (ref) de system prompt antes
de que el LLM escriba una palabra: demasiado para un 12B local. El escritor no
las manda enteras — pide una cosa por llamada y adjunta solo las secciones que
esa llamada necesita. Este módulo hace ese troceo.

Fuente: github.com/MiniMax-AI/MiniMax-H3, skills/h3-prompt-writing/references/.
Los .txt de `guides/` están sin editar; si MiniMax los revisa, se reemplazan y
las claves de sección de abajo son lo único que hay que revisar.
"""
from pathlib import Path
from dataclasses import dataclass, field

_GUIDES_DIR = Path(__file__).parent / "guides"

# Secciones que necesita cada llamada del escritor. Las claves son las de la
# guía ("4.3" = Camera Motion); una clave de nivel 1 ("2") arrastra sus
# subsecciones.
RECIPES = {
    # base-en.txt
    "timeline":   ["1", "3", "4.1", "4.2"],          # idea -> lista de planos
    "visual":     ["2", "4.1", "4.2", "4.3", "4.4", "4.5"],
    "soundscape": ["4.6"],
    "music":      ["4.7"],
    # ref-en.txt
    "ref_subjects":  ["2"],
    "ref_summary":   ["3"],
    "ref_retention": ["4"],
    "ref_detailed":  ["5"],
    "ref_audio":     ["6"],
}


@dataclass
class Section:
    key: str            # "4.3"
    title: str          # "Camera Motion: Motion Type + Amplitude + Speed"
    level: int          # 2 para "## ", 3 para "### "
    body: str           # texto de la sección, sin su propio encabezado


@dataclass
class Guide:
    name: str
    text: str
    sections: dict = field(default_factory=dict)

    def section(self, key: str) -> str:
        """Una sección con su encabezado. Una clave de nivel 1 arrastra sus
        subsecciones ("2" devuelve 2, 2.1, 2.2…)."""
        out = []
        for k, s in self.sections.items():
            if k == key or k.startswith(key + "."):
                out.append(f"{'#' * s.level} {k}. {s.title}\n\n{s.body}".rstrip())
        if not out:
            raise KeyError(f"{self.name}: no existe la sección {key!r}")
        return "\n\n".join(out)

    def compose(self, keys: list[str], preamble: str = "") -> str:
        """Extracto de la guía con solo las secciones pedidas, listo para
        pegar como system prompt."""
        parts = [preamble.strip()] if preamble else []
        parts += [self.section(k) for k in keys]
        return "\n\n".join(parts)

    def recipe(self, name: str, preamble: str = "") -> str:
        return self.compose(RECIPES[name], preamble)


def _parse(name: str, text: str) -> Guide:
    """Trocea por encabezados numerados (`## 4. …` / `### 4.3 …`). El texto
    anterior al primer encabezado numerado queda como sección "0" (la nota de
    cabecera de ref-en.txt, que sí importa: fija el idioma de salida)."""
    lines = text.splitlines()
    sections: dict[str, Section] = {}
    key, title, level, buf = "0", "Preamble", 2, []

    def flush():
        body = "\n".join(buf).strip()
        if body or key != "0":
            sections[key] = Section(key, title, level, body)

    for line in lines:
        head = None
        if line.startswith("### "):
            head, level_ = line[4:], 3
        elif line.startswith("## "):
            head, level_ = line[3:], 2
        if head is not None:
            num, _, rest = head.partition(" ")
            num = num.rstrip(".")
            # solo los encabezados numerados abren sección; el resto es cuerpo
            if num and num[0].isdigit():
                flush()
                key, title, level, buf = num, rest.strip(), level_, []
                continue
        buf.append(line)
    flush()
    return Guide(name=name, text=text, sections=sections)


_cache: dict[str, Guide] = {}


def load_guide(which: str) -> Guide:
    """`"base"` (T2VA/I2VA/FL2VA/L2VA) o `"ref"` (Ref2VA)."""
    if which not in _cache:
        path = _GUIDES_DIR / f"{which}-en.txt"
        if not path.exists():
            raise FileNotFoundError(
                f"falta {path}. Se descargan de "
                f"github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing/references"
            )
        _cache[which] = _parse(which, path.read_text(encoding="utf-8"))
    return _cache[which]
