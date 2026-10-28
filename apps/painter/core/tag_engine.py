"""
Tag autocomplete engine — soporte multi-CSV.
Cada modelo puede tener su propia base de datos de tags optimizada.
CSVs almacenados en: apps/painter/tags/{csv_name}.csv
Formato columnas: tag, category, post_count, aliases
"""
import csv
import math
import re
import unicodedata
import threading
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

_CSV_DIR = Path(__file__).parent.parent / "tags"


@dataclass
class Tag:
    name:       str
    category:   int
    post_count: int
    aliases:    list[str] = field(default_factory=list)


# Registro de CSVs cargados: csv_name → {tags, word_index, count}
# word_index: token_completo → set de índices de tags que contienen ese token
_engines: dict[str, dict] = {}
_lock    = threading.Lock()
_csv_locks: dict[str, threading.RLock] = {}


def csv_path_for(csv_name: str) -> Path:
    """Resolve a simple CSV identifier inside the shared tag library."""
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if (not isinstance(csv_name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", csv_name)
            or csv_name.endswith(".") or csv_name.split(".")[0].upper() in reserved):
        raise ValueError("Nombre de biblioteca de tags no válido")
    base = _CSV_DIR.resolve()
    path = (base / f"{csv_name}.csv").resolve()
    if path.parent != base:
        raise ValueError("La biblioteca de tags debe permanecer en su carpeta")
    return path


def _csv_lock(csv_name: str):
    csv_path_for(csv_name)
    with _lock:
        return _csv_locks.setdefault(csv_name, threading.RLock())


def _norm(s: str) -> str:
    """Normaliza: sin diacríticos, lowercase, underscores → espacios."""
    s = ''.join(char for char in unicodedata.normalize('NFKD', s)
                if not unicodedata.combining(char))
    return ' '.join(s.casefold().replace('_', ' ').split())


def _tokens(s: str) -> list[str]:
    """Extrae tokens normalizados de un nombre o alias."""
    return _norm(s).split()


def prepare_csv(csv_name: str, path: Path) -> dict:
    """
    Build and validate an index without publishing it. Used by atomic downloads.
    The explicit path is internal staging/test input, never an HTTP parameter.
    """
    csv_path_for(csv_name)
    tags: list[Tag] = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f, strict=True)
        # Sin salto de cabecera: el CSV de DraconicDragon no la trae (la primera
        # fila ya es un tag) y una cabecera real cae sola en el except ValueError.
        for row in reader:
            if len(row) < 3:
                continue
            name = row[0].strip()
            if not name:
                continue
            try:
                category   = int(row[1])
                post_count = int(row[2])
            except ValueError:
                continue
            aliases = (
                [a.strip() for a in row[3].split(',') if a.strip()]
                if len(row) > 3 and row[3].strip() else []
            )
            tags.append(Tag(name=name, category=category,
                            post_count=post_count, aliases=aliases))

    if not tags:
        raise ValueError("La biblioteca descargada no contiene tags válidos")

    # Índice invertido por token completo (palabra).
    # Cubre nombre y todos los aliases → resuelve búsquedas en cualquier orden.
    word_index: dict[str, set[int]] = {}
    for i, tag in enumerate(tags):
        # Tokens del nombre (sin suffix de serie para indexar)
        name_clean = re.sub(r'_\([^)]+\)$', '', tag.name.lower())
        for tok in _tokens(name_clean):
            word_index.setdefault(tok, set()).add(i)
        # También indexar tokens del nombre completo (incluye serie)
        for tok in _tokens(tag.name):
            word_index.setdefault(tok, set()).add(i)
        # Tokens de cada alias
        for alias in tag.aliases:
            for tok in _tokens(alias):
                word_index.setdefault(tok, set()).add(i)

    return {"tags": tags, "word_index": word_index, "count": len(tags)}


def load_csv(csv_name: str, path: Path | None = None) -> int:
    """Rebuild one CSV; publish only a complete valid index, retaining the old on error."""
    source = Path(path) if path is not None else csv_path_for(csv_name)
    with _csv_lock(csv_name):
        if not source.is_file():
            return 0
        engine = prepare_csv(csv_name, source)
        with _lock:
            _engines[csv_name] = engine
        return engine["count"]


def ensure_loaded(csv_name: str) -> int:
    """Load a CSV on demand, once even when concurrent requests arrive."""
    csv_path_for(csv_name)
    with _lock:
        engine = _engines.get(csv_name)
    if engine is not None:
        return engine["count"]
    with _csv_lock(csv_name):
        with _lock:
            engine = _engines.get(csv_name)
        return engine["count"] if engine is not None else load_csv(csv_name)


def install_csv(csv_name: str, path: Path, engine: dict) -> int:
    """Commit an already validated staged file and index together, without an await."""
    dest, staged = csv_path_for(csv_name), Path(path).resolve()
    if staged.parent != dest.parent or staged == dest or not engine.get("count"):
        raise ValueError("Biblioteca temporal de tags no válida")
    with _csv_lock(csv_name):
        os.replace(staged, dest)
        with _lock:
            _engines[csv_name] = engine
    return engine["count"]


def _score(q: str, q_tokens: set[str], tag: Tag) -> Optional[float]:
    name_spc   = _norm(tag.name)
    name_clean = _norm(re.sub(r'_\([^)]+\)$', '', tag.name))
    nct        = set(name_clean.split())
    aliases    = [_norm(alias) for alias in tag.aliases]

    if q == name_spc or q in aliases:
        tier = 1000
    elif q_tokens and q_tokens == nct:
        tier = 900
    elif name_spc.startswith(q):
        tier = 800
    elif any(a.startswith(q) for a in aliases):
        tier = 700
    elif q_tokens and all(any(nt.startswith(qt) for nt in nct) for qt in q_tokens):
        tier = 600
    elif q in name_spc or any(q in a for a in aliases):
        tier = 500
    else:
        return None

    return tier + math.log10(max(tag.post_count, 1))


def search(csv_name: str, query: str, limit: int = 5) -> list[dict]:
    """
    Busca tags en el CSV indicado; carga bajo demanda si está presente.
    Usa índice invertido por token: "futaba" encuentra sakura_futaba_(persona_5)
    independientemente del orden en que el usuario escriba las palabras.
    """
    limit = max(1, min(int(limit), 20))
    ensure_loaded(csv_name)
    with _lock:
        engine = _engines.get(csv_name)
        if not engine:
            return []
        tags       = engine["tags"]
        word_index = engine["word_index"]

    q = _norm(query)
    if not q:
        return []
    q_parts  = q.split()
    q_tokens = set(q_parts)

    # Candidatos: intersección de los sets de cada token de la query.
    # Tokens completados (no el último si es parcial) → lookup exacto.
    # Último token (puede ser parcial) → también busca por prefijo.
    candidate_ids: set[int] | None = None

    for i, tok in enumerate(q_parts):
        is_last = (i == len(q_parts) - 1)

        if is_last:
            # Buscar tags que contienen un token que empieza con este prefijo
            tok_hits: set[int] = set()
            tok_hits |= word_index.get(tok, set())          # exacto
            if len(tok) >= 2:                                # prefijo solo si ≥ 2 chars
                for word, ids in word_index.items():
                    if word != tok and word.startswith(tok):
                        tok_hits |= ids
        else:
            tok_hits = word_index.get(tok, set())           # exacto

        if candidate_ids is None:
            candidate_ids = set(tok_hits)
        else:
            candidate_ids &= tok_hits                       # AND entre tokens

    if not candidate_ids:
        candidate_ids = set()

    candidates = [tags[i] for i in candidate_ids]

    results = []
    for tag in candidates:
        sc = _score(q, q_tokens, tag)
        if sc is None:
            continue
        matched_alias = None
        for a in tag.aliases:
            a_norm = _norm(a)
            if a_norm.startswith(q) or a_norm == q:
                matched_alias = a
                break
        results.append({
            'name':          tag.name,
            'category':      tag.category,
            'post_count':    tag.post_count,
            'matched_alias': matched_alias,
            '_sc':           sc,
        })

    results.sort(key=lambda x: (-x['_sc'], _norm(x['name']), x['name']))
    for r in results:
        del r['_sc']
    return results[:limit]


def is_loaded(csv_name: str) -> bool:
    with _lock:
        return csv_name in _engines


def tag_count(csv_name: str) -> int:
    with _lock:
        return _engines.get(csv_name, {}).get('count', 0)


def loaded_names() -> list[str]:
    with _lock:
        return list(_engines.keys())
