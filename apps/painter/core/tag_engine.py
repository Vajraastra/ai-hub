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


def _norm(s: str) -> str:
    """Normaliza: sin diacríticos, lowercase, underscores → espacios."""
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return s.lower().replace('_', ' ').strip()


def _tokens(s: str) -> list[str]:
    """Extrae tokens normalizados de un nombre o alias."""
    return _norm(s).split()


def load_csv(csv_name: str, path: Path | None = None) -> int:
    """
    Carga el CSV indicado y lo indexa bajo csv_name.
    Si path es None usa _CSV_DIR/{csv_name}.csv.
    Devuelve el número de tags cargados (0 si el archivo no existe).
    """
    p = path or (_CSV_DIR / f"{csv_name}.csv")
    if not p.exists():
        return 0

    tags: list[Tag] = []
    with open(p, encoding='utf-8', newline='', errors='replace') as f:
        reader = csv.reader(f)
        next(reader, None)                     # saltar cabecera
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

    with _lock:
        _engines[csv_name] = {"tags": tags, "word_index": word_index, "count": len(tags)}
    return len(tags)


def _score(q: str, q_tokens: set[str], tag: Tag) -> Optional[float]:
    name_raw   = tag.name.lower()
    name_spc   = name_raw.replace('_', ' ')
    name_clean = re.sub(r'_\([^)]+\)$', '', name_raw).replace('_', ' ')
    nct        = set(name_clean.split())
    aliases    = [a.lower().replace('_', ' ') for a in tag.aliases]

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
    Busca tags en el CSV indicado. Devuelve lista vacía si no está cargado.
    Usa índice invertido por token: "futaba" encuentra sakura_futaba_(persona_5)
    independientemente del orden en que el usuario escriba las palabras.
    """
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
            a_norm = a.lower().replace('_', ' ')
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

    results.sort(key=lambda x: -x['_sc'])
    for r in results:
        del r['_sc']
    return results[:limit]


def is_loaded(csv_name: str) -> bool:
    with _lock:
        return csv_name in _engines


def tag_count(csv_name: str) -> int:
    with _lock:
        return _engines.get(csv_name, {}).get('count', 0)


def csv_path_for(csv_name: str) -> Path:
    return _CSV_DIR / f"{csv_name}.csv"


def loaded_names() -> list[str]:
    with _lock:
        return list(_engines.keys())
