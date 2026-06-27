#!/usr/bin/env python3
"""
Auditoría de dependencias de las apps externas del AI Hub.

Objetivo: registrar qué instala cada app de terceros para evaluar un futuro
POOL COMPARTIDO de dependencias (hoy cada app tiene su venv con su propio
torch/CUDA → GB duplicados, mala portabilidad).

Dos vistas:
  - DECLARADAS: lo que pide cada requirements.txt/pyproject (intención).
  - RESUELTAS:  lo que pip freeze deja realmente en el venv (transitivas +
                versiones exactas). Aquí aparecen los torch/cuda duplicados.

Uso:
  python audit.py declared            # matriz de declaradas -> MATRIX.md
  python audit.py snapshot <app> <venv_python>   # captura pip freeze del venv
  python audit.py matrix              # consolida declaradas + resolved/ -> MATRIX.md

El target de markers es Windows + CPython 3.12 (la plataforma soportada).
"""
import os
import re
import sys
import json
import subprocess
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):   # consola Windows = cp1252, evita crash con ≥/⚠️
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_DIR  = os.path.join(ROOT, "apps")
AUDIT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOLVED  = os.path.join(AUDIT_DIR, "resolved")
MATRIX_MD = os.path.join(AUDIT_DIR, "MATRIX.md")

# Apps de TERCEROS (las propias —painter/lora_merger/model_vault— viven
# in-process en el venv del hub-webui y no entran en este análisis).
EXTERNAL_APPS = {
    "ai-toolkit":               ["requirements.txt"],
    "anima-standalone-trainer": ["requirements.txt"],
    "comfyui":                  ["requirements.txt"],
    "dataset-refiner":          ["requirements.txt"],
    "facefusion":               ["requirements.txt"],
    "sd-webui-forge-neo":       ["requirements.txt"],
    "taggui":                   ["requirements.txt"],
}

# ── Normalización de nombres (PEP 503) ──────────────────────────────────────
def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())

# ── Evaluación grosera de markers para el target Windows/py3.12 ──────────────
def marker_applies(marker: str) -> bool:
    m = marker.lower()
    if "platform_system" in m or "sys_platform" in m:
        wants_win = '"windows"' in m or "'windows'" in m or '"win32"' in m
        is_neg    = "!=" in m
        if wants_win:
            return not is_neg          # == windows -> sí ; != windows -> no
        # menciona otra plataforma (linux/darwin) sin windows
        return is_neg                  # != linux -> sí ; == linux -> no
    if "python_version" in m:
        # aceptamos 3.12 como target
        return "3.11" not in m or "3.12" in m
    return True

# ── Parseo de una línea de requirements ─────────────────────────────────────
def parse_line(line: str):
    """Devuelve (canon_name, version_spec, kind) o None."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith(("--", "-i ", "--extra-index-url", "--index-url")):
        return None

    # marker de entorno
    spec, marker = (line.split(";", 1) + [""])[:2]
    spec = spec.strip()
    if marker and not marker_applies(marker):
        return None

    # paquete local editable / path
    if spec.startswith(("-e", "./", ".\\")) or spec in (".", "-e ."):
        path = spec.replace("-e", "", 1).strip()
        if path in (".", "", "./", ".\\"):
            return None                       # self-install (-e .), no es una dep
        nm = os.path.basename(path.rstrip("/\\"))
        return (canon(nm), "local", "local")

    # wheel/URL directo: extraer nombre+version del archivo
    if spec.startswith(("http://", "https://")):
        fn = spec.split("/")[-1]
        mwhl = re.match(r"([A-Za-z0-9_.\-]+?)-(\d[^-]*)", fn)
        if mwhl:
            return (canon(mwhl.group(1)), mwhl.group(2), "url")
        return (canon(fn.split("-")[0]), "url", "url")

    # git+
    if spec.startswith("git+"):
        mg = re.search(r"/([A-Za-z0-9_.\-]+?)(?:\.git)?(?:@|$)", spec)
        nm = mg.group(1) if mg else spec
        return (canon(nm), "git", "git")

    # estándar: nombre[extras]op version
    mreq = re.match(r"^([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(.*)$", spec)
    if not mreq:
        return None
    return (canon(mreq.group(1)), mreq.group(3).strip() or "*", "pin")

# ── Carga declaradas de una app (sigue -r recursivo) ────────────────────────
def load_declared(app: str, rel_files) -> dict:
    deps = {}
    seen = set()

    def walk(path):
        if path in seen or not os.path.isfile(path):
            return
        seen.add(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                s = raw.strip()
                if s.startswith("-r "):
                    walk(os.path.join(os.path.dirname(path), s[3:].strip()))
                    continue
                p = parse_line(s)
                if p:
                    deps[p[0]] = p[1]
    for rel in rel_files:
        walk(os.path.join(APPS_DIR, app, rel))
    return deps

# ── pip freeze de un venv ───────────────────────────────────────────────────
def snapshot(app: str, venv_python: str):
    if not os.path.isfile(venv_python):
        sys.exit(f"No existe el python del venv: {venv_python}")
    out = subprocess.run([venv_python, "-m", "pip", "freeze"],
                         capture_output=True, text=True)
    os.makedirs(RESOLVED, exist_ok=True)
    dst = os.path.join(RESOLVED, f"{app}.txt")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out.stdout)
    n = len([l for l in out.stdout.splitlines() if l.strip() and "==" in l])
    print(f"[snapshot] {app}: {n} paquetes resueltos -> {dst}")

def load_resolved(app: str) -> dict:
    path = os.path.join(RESOLVED, f"{app}.txt")
    deps = {}
    if not os.path.isfile(path):
        return deps
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "==" in line:
                nm, ver = line.split("==", 1)
                deps[canon(nm)] = ver.split(";")[0].strip()
            elif " @ " in line:
                nm = line.split(" @ ", 1)[0]
                deps[canon(nm)] = "url"
    return deps

# ── Construcción de la matriz ───────────────────────────────────────────────
def build(view: str):
    """view: 'declared' o 'resolved'. Devuelve dict dep -> {app: version}."""
    matrix = defaultdict(dict)
    apps_present = []
    for app, files in EXTERNAL_APPS.items():
        deps = load_declared(app, files) if view == "declared" else load_resolved(app)
        if not deps:
            continue
        apps_present.append(app)
        for dep, ver in deps.items():
            matrix[dep][app] = ver
    return matrix, apps_present

def render(matrix, apps_present, view):
    crossed = {d: v for d, v in matrix.items() if len(v) >= 2}
    unique  = {d: v for d, v in matrix.items() if len(v) == 1}

    # conflictos de versión entre apps (mismo dep, specs distintos)
    conflicts = {d: v for d, v in crossed.items() if len({x for x in v.values()}) > 1}

    lines = []
    lines.append(f"### Vista: {view.upper()}")
    lines.append("")
    lines.append(f"Apps con datos ({len(apps_present)}): {', '.join(sorted(apps_present))}")
    lines.append(f"Total deps distintas: {len(matrix)} · "
                 f"cruzadas (≥2 apps): **{len(crossed)}** · únicas: {len(unique)} · "
                 f"cruzadas con conflicto de versión: **{len(conflicts)}**")
    lines.append("")
    lines.append("#### Cruzadas (candidatas a pool compartido)")
    lines.append("| Dependencia | #apps | Versiones por app | ¿Conflicto? |")
    lines.append("|---|---|---|---|")
    for dep in sorted(crossed, key=lambda d: (-len(crossed[d]), d)):
        v = crossed[dep]
        vers = ", ".join(f"{a}={v[a]}" for a in sorted(v))
        conf = "⚠️ SÍ" if dep in conflicts else "ok"
        lines.append(f"| `{dep}` | {len(v)} | {vers} | {conf} |")
    lines.append("")
    lines.append(f"#### Únicas ({len(unique)})")
    lines.append(", ".join(f"`{d}`({list(unique[d])[0]})" for d in sorted(unique)))
    lines.append("")
    return "\n".join(lines)

def cmd_matrix():
    blocks = ["# Matriz de dependencias — apps externas AI Hub",
              "",
              "_Generado por `deps-audit/audit.py`. DECLARADAS = lo pedido en "
              "requirements; RESUELTAS = pip freeze real del venv (incluye "
              "transitivas). El objetivo es cuantificar el duplicado (torch/CUDA) "
              "de cara a un pool compartido._", ""]
    dm, da = build("declared")
    blocks.append(render(dm, da, "declared"))
    rm, ra = build("resolved")
    if ra:
        blocks.append(render(rm, ra, "resolved"))
    else:
        blocks.append("### Vista: RESUELTAS\n\n_(aún sin snapshots — se llena al "
                      "instalar cada app con `audit.py snapshot <app> <venv_python>`)_")
    with open(MATRIX_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks) + "\n")
    print(f"[matrix] escrito {MATRIX_MD}")
    # resumen a consola
    print(render(dm, da, "declared").split("#### Cruzadas")[0])

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "matrix"
    if cmd == "snapshot":
        snapshot(sys.argv[2], sys.argv[3])
    elif cmd in ("declared", "matrix"):
        cmd_matrix()
    else:
        sys.exit(__doc__)
