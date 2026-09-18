"""
Instalador compartido de custom nodes de ComfyUI.

ComfyUI es un solo proceso compartido por todos nuestros módulos, así que sus
custom nodes son una dependencia transversal: si `apps/comfyui/custom_nodes/`
está vacío (instalación limpia — la carpeta está gitignored), Painter, Face
Swap, Forge Fusion y MiniMax H3 arrancan rotos.

Cada módulo declara los suyos en `apps/<modulo>/nodes_registry.json`:

    {
      "required": [                      # sin esto el módulo no funciona
        {
          "id":         "comfyui-tooling-nodes",
          "repo":       "https://github.com/Acly/comfyui-tooling-nodes",
          "commit":     "a1b2c3d",       # opcional pero recomendado, ver abajo
          "probe_node": "ETN_LoadImageBase64",
          "reason":     "Carga de imagen/máscara como base64"
        }
      ],
      "enhanced": [ ... ]                # degrada, no rompe; admite
    }                                    # "fallback_workflow"

También se admite `"local_src"` (ruta relativa a la raíz del repo) en vez de
`repo`, para nodos de un solo archivo que mantenemos nosotros, y
`"pip_packages"` para dependencias que el nodo no declara en su
`requirements.txt`.

**Por qué se fija el commit:** un `git clone` a secas trae HEAD, así que la
misma instalación hecha con seis meses de diferencia trae código distinto y el
módulo puede romperse sin que nada haya cambiado de nuestro lado. Con `commit`
puesto, una instalación limpia reproduce exactamente lo que probamos. Sin él la
instalación sigue funcionando (se avisa por evento), que es lo que necesitamos
para no bloquear un pack cuyo commit todavía no hemos fijado.

Este módulo no habla con ComfyUI ni asume que esté corriendo: quien llama
decide cómo averiguar qué nodos ya están cargados y pasa el resultado en
`loaded_nodes`. Así sirve igual para el bootstrap (ComfyUI aún no existe) que
para la pantalla de setup de un módulo (ComfyUI vivo, se consulta
`/object_info` **una vez** en vez de un sondeo por nodo).
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterator

from modules.python_runtime import resolve_python_runtime

_REGISTRY_NAME = "nodes_registry.json"

# Los clones son de decenas de MB, pero un `pip install` de un nodo puede
# arrastrar onnxruntime/insightface y tardar. Timeouts explícitos: sin ellos un
# cuelgue a media descarga no devuelve error nunca (ver CLAUDE.md global, B3).
_GIT_TIMEOUT = 300
_PIP_TIMEOUT = 900


# ── Lectura de los registros declarativos ───────────────────────────────────

def load_registries(apps_dir: Path) -> tuple[list[dict], list[str]]:
    """
    Escanea `apps/*/nodes_registry.json` y devuelve (entradas, avisos).

    Cada entrada lleva `kind` ("required"/"enhanced") y `modules` (qué módulos
    la piden). Un pack que piden dos módulos se instala una sola vez, y si
    cualquiera de los dos lo marca `required`, la entrada resultante es
    `required`: la exigencia más fuerte gana.
    """
    apps_dir = Path(apps_dir)
    merged: dict[str, dict] = {}
    warnings: list[str] = []

    for reg_file in sorted(apps_dir.glob(f"*/{_REGISTRY_NAME}")):
        module = reg_file.parent.name
        try:
            data = json.loads(reg_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            warnings.append(f"{module}: no se pudo leer {_REGISTRY_NAME} — {e}")
            continue

        for kind in ("required", "enhanced"):
            for entry in data.get(kind, []):
                node_id = entry.get("id")
                if not node_id:
                    warnings.append(f"{module}: entrada sin 'id', ignorada")
                    continue
                if not entry.get("repo") and not entry.get("local_src"):
                    warnings.append(f"{module}: '{node_id}' no declara 'repo' ni 'local_src'")
                    continue

                prev = merged.get(node_id)
                if prev is None:
                    merged[node_id] = {**entry, "kind": kind, "modules": [module]}
                    continue

                prev["modules"].append(module)
                if kind == "required":
                    prev["kind"] = "required"
                # Dos módulos pidiendo commits distintos del mismo pack es un
                # conflicto real: se queda el primero y se avisa, porque elegir
                # en silencio deja a uno de los dos corriendo código que no
                # probó.
                if entry.get("commit") and prev.get("commit") \
                        and entry["commit"] != prev["commit"]:
                    warnings.append(
                        f"'{node_id}': {module} pide el commit {entry['commit']} "
                        f"y {prev['modules'][0]} el {prev['commit']}; se instala "
                        f"el de {prev['modules'][0]}")
                elif entry.get("commit") and not prev.get("commit"):
                    prev["commit"] = entry["commit"]

    # required primero: si uno falla se corta antes de gastar tiempo en los
    # opcionales.
    entries = sorted(merged.values(), key=lambda e: (e["kind"] != "required", e["id"]))
    return entries, warnings


# ── Instalación ─────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int, cwd: Path | None = None) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=str(cwd) if cwd else None)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "").strip()
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"timeout tras {timeout}s: {' '.join(cmd[:3])}…"
    except (OSError, FileNotFoundError) as e:
        return False, str(e)


def clone_node(repo: str, dest: Path, commit: str = "") -> tuple[bool, str]:
    """
    Clona `repo` en `dest`, fijado a `commit` si se indica.

    Con commit no se puede usar `git clone --depth 1` (trae HEAD y el SHA
    pedido casi nunca está en esa profundidad). Se usa fetch por SHA, que
    GitHub soporta y baja un solo commit; si el remoto no lo permite, se
    recurre a un clone completo y checkout.
    """
    dest = Path(dest)
    if not commit:
        return _run(["git", "clone", "--depth", "1", repo, str(dest)], _GIT_TIMEOUT)

    dest.mkdir(parents=True, exist_ok=True)
    ok, err = _run(["git", "init", "-q", str(dest)], _GIT_TIMEOUT)
    if not ok:
        return False, err
    ok, err = _run(["git", "remote", "add", "origin", repo], _GIT_TIMEOUT, cwd=dest)
    if not ok:
        return False, err

    ok, _ = _run(["git", "fetch", "--depth", "1", "origin", commit], _GIT_TIMEOUT, cwd=dest)
    if ok:
        ok, err = _run(["git", "checkout", "-q", "FETCH_HEAD"], _GIT_TIMEOUT, cwd=dest)
        if ok:
            return True, ""

    # Fallback: historial completo y checkout del SHA.
    ok, err = _run(["git", "fetch", "origin"], _GIT_TIMEOUT, cwd=dest)
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        return False, err
    ok, err = _run(["git", "checkout", "-q", commit], _GIT_TIMEOUT, cwd=dest)
    if not ok:
        shutil.rmtree(dest, ignore_errors=True)
        return False, f"commit {commit} no encontrado en {repo}: {err}"
    return True, ""


def pip_cmd(comfyui_dir: Path, python_path: Path | None = None,
            uv_path: Path | None = None) -> list[str]:
    """Comando base de pip contra el venv de ComfyUI (uv si está disponible)."""
    if python_path is not None and uv_path is not None:
        return [str(uv_path), "pip", "install", "--python", str(python_path)]
    comfyui_dir = Path(comfyui_dir)
    repo_root = comfyui_dir.parent.parent
    registry = repo_root / "hub" / "config" / "app_registry.json"
    if registry.is_file():
        try:
            config = json.loads(registry.read_text(encoding="utf-8"))["apps"]["comfyui"]
            runtime = resolve_python_runtime(config, str(comfyui_dir), str(repo_root))
            if Path(runtime.python).is_file() and config.get("shared_runtime"):
                uv_env = os.environ.get("AI_HUB_UV_PATH", "")
                candidates = [Path(uv_env)] if uv_env else []
                candidates.extend([
                    repo_root / "hub" / "tools" / "win" / "uv.exe",
                    repo_root / "hub" / "tools" / "uv.exe",
                ])
                uv = next((path for path in candidates if path.is_file()), None)
                if uv:
                    return [str(uv), "pip", "install", "--python", runtime.python]
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            pass

    bin_dir = Path(comfyui_dir) / "venv" / ("Scripts" if os.name == "nt" else "bin")
    exe     = ".exe" if os.name == "nt" else ""
    uv      = bin_dir / f"uv{exe}"
    python  = bin_dir / f"python{exe}"
    pip     = bin_dir / f"pip{exe}"
    if uv.exists():
        # uv necesita --python explícito: el subprocess no tiene venv activo.
        return [str(uv), "pip", "install", "--python", str(python)]
    return [str(pip), "install"]


def install_deps(comfyui_dir: Path, node_dir: Path,
                 extra_packages: list[str] | None = None,
                 python_path: Path | None = None,
                 uv_path: Path | None = None) -> tuple[bool, str]:
    """
    Instala el `requirements.txt` del nodo más los `pip_packages` declarados.

    Las dependencias `git+` van aparte y en best-effort: suelen ser extras
    pesados y opcionales (sam2 y compañía), y un fallo ahí no debe tumbar un
    nodo que por lo demás carga.
    """
    pip = pip_cmd(comfyui_dir, python_path, uv_path)
    req = Path(node_dir) / "requirements.txt"
    normal: list[str] = list(extra_packages or [])
    git_deps: list[str] = []

    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            (git_deps if line.startswith("git+") else normal).append(line)

    if normal:
        ok, err = _run(pip + normal, _PIP_TIMEOUT)
        if not ok:
            return False, err

    for dep in git_deps:
        _run(pip + [dep], _PIP_TIMEOUT)

    return True, ""


def install_local_node(repo_root: Path, local_src: str,
                       comfyui_dir: Path) -> tuple[bool, str]:
    """Copia un nodo de un solo archivo (mantenido por nosotros) a custom_nodes."""
    src = Path(repo_root) / local_src
    if not src.is_file():
        return False, f"archivo local no encontrado: {src}"
    try:
        shutil.copy2(src, Path(comfyui_dir) / "custom_nodes" / src.name)
        return True, ""
    except OSError as e:
        return False, str(e)


def installed_commit(node_dir: Path) -> str:
    """SHA corto del nodo instalado, o '' si no es un repo git propio."""
    node_dir = Path(node_dir)
    if not (node_dir / ".git").exists():
        return ""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=30,
                           cwd=str(node_dir))
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ── Orquestador ─────────────────────────────────────────────────────────────

def install_nodes(comfyui_dir: Path, apps_dir: Path, repo_root: Path,
                  loaded_nodes: set[str] | None = None,
                  only_modules: list[str] | None = None,
                  dry_run: bool = False,
                  python_path: Path | None = None,
                  uv_path: Path | None = None) -> Iterator[dict]:
    """
    Instala lo que declaren los registros de los módulos.

    Generador síncrono de eventos `{"step", "status", "msg"}` — el mismo
    contrato que ya consume el SSE de Painter.

    `loaded_nodes`: nombres de nodos que ComfyUI ya tiene cargados (de
    `/object_info`). Si es None se trabaja offline y la referencia es la
    presencia del directorio, que es lo único comprobable sin el servidor.

    `only_modules`: limita la instalación a los registros de esos módulos.

    `dry_run`: informa de lo que haría sin clonar ni instalar nada. Sirve para
    auditar una instalación existente, que es el caso normal aquí: los packs se
    pusieron a mano durante meses y hay que saber qué falta antes de tocar
    nada.

    El último evento es siempre `done`, y su campo `data` lleva el resumen
    (`needs_restart`, `warnings`, `fallbacks`, `installed`).
    """
    comfyui_dir, apps_dir, repo_root = Path(comfyui_dir), Path(apps_dir), Path(repo_root)
    custom_nodes = comfyui_dir / "custom_nodes"

    summary = {"needs_restart": False, "warnings": [], "fallbacks": {},
               "installed": [], "failed": [], "missing": []}

    def event(step, status, msg, **extra):
        return {"step": step, "status": status, "msg": msg, **extra}

    if not comfyui_dir.is_dir():
        yield event("comfyui", "error",
                    f"ComfyUI no está instalado en {comfyui_dir}. Instálalo desde el hub.")
        yield event("done", "error", "Sin ComfyUI no hay dónde instalar nodos.", data=summary)
        return

    custom_nodes.mkdir(parents=True, exist_ok=True)

    entries, reg_warnings = load_registries(apps_dir)
    for w in reg_warnings:
        summary["warnings"].append(w)
        yield event("registry", "warning", w)

    if only_modules:
        wanted = set(only_modules)
        entries = [e for e in entries if wanted & set(e["modules"])]

    if not entries:
        yield event("done", "ok", "Ningún módulo declara custom nodes.", data=summary)
        return

    yield event("plan", "info",
                f"{len(entries)} pack(s) declarado(s) por "
                f"{len({m for e in entries for m in e['modules']})} módulo(s)")

    for entry in entries:
        node_id  = entry["id"]
        step     = f"node_{node_id}"
        kind     = entry["kind"]
        reason   = entry.get("reason", "")
        commit   = entry.get("commit", "")
        probe    = entry.get("probe_node")
        by       = ", ".join(entry["modules"])

        def fail(msg):
            """Un fallo en required corta; en enhanced degrada a aviso."""
            if kind == "required":
                summary["failed"].append(node_id)
                return event(step, "error", msg), True
            summary["warnings"].append(msg)
            fb = entry.get("fallback_workflow")
            if fb:
                summary["fallbacks"][node_id] = fb
            return event(step, "warning", msg), False

        # ¿Ya cargado? Solo se puede afirmar con ComfyUI vivo.
        if loaded_nodes is not None and probe and probe in loaded_nodes:
            yield event(step, "ok", f"{node_id} — ya cargado ({by})")
            continue

        # ── Nodo local de un archivo ────────────────────────────────────────
        if entry.get("local_src"):
            dest_file = custom_nodes / Path(entry["local_src"]).name
            existed = dest_file.exists()
            if dry_run:
                if not existed:
                    summary["missing"].append(node_id)
                yield event(step, "ok" if existed else "warning",
                            f"{node_id} — {'presente' if existed else 'FALTA'} ({by})")
                continue
            if not existed:
                yield event(step, "info", f"Instalando {node_id} — {reason}")
                ok, err = install_local_node(repo_root, entry["local_src"], comfyui_dir)
                if not ok:
                    ev, stop = fail(f"Error copiando {node_id}: {err}")
                    yield ev
                    if stop:
                        yield event("done", "error", f"Falta {node_id}, que es obligatorio.",
                                    data=summary)
                        return
                    continue
            pkgs = entry.get("pip_packages", [])
            if pkgs:
                ok, err = install_deps(
                    comfyui_dir, dest_file.parent, pkgs, python_path, uv_path
                )
                if not ok:
                    ev, stop = fail(f"Error instalando deps de {node_id}: {err}")
                    yield ev
                    if stop:
                        yield event("done", "error", f"Falta {node_id}, que es obligatorio.",
                                    data=summary)
                        return
                    continue
            summary["needs_restart"] = True
            summary["installed"].append(node_id)
            yield event(step, "ok", f"{node_id} instalado — requiere reiniciar ComfyUI")
            continue

        # ── Nodo externo (git) ──────────────────────────────────────────────
        dest = custom_nodes / node_id
        if dest.is_dir():
            # El directorio está pero el nodo no aparece cargado. Con ComfyUI
            # vivo eso significa que falló al importar (dependencia ausente);
            # offline solo significa que aún no ha arrancado.
            if loaded_nodes is not None and not dry_run:
                yield event(step, "info",
                            f"{node_id} presente pero no cargado — reinstalando dependencias")
                ok, err = install_deps(
                    comfyui_dir, dest, entry.get("pip_packages"),
                    python_path, uv_path
                )
                if not ok:
                    ev, stop = fail(f"Error reinstalando deps de {node_id}: {err}")
                    yield ev
                    if stop:
                        yield event("done", "error", f"Falta {node_id}, que es obligatorio.",
                                    data=summary)
                        return
                    continue
            have = installed_commit(dest)
            if commit and have and not commit.startswith(have) and not have.startswith(commit):
                msg = (f"{node_id}: instalado en {have}, el registro fija {commit}. "
                       "No se toca automáticamente para no romper lo que ya funciona.")
                summary["warnings"].append(msg)
                yield event(step, "warning", msg)
            elif not have:
                msg = (f"{node_id}: instalado sin repositorio git, no se puede "
                       "verificar la versión ni actualizar. Reclonar cuando se pueda.")
                summary["warnings"].append(msg)
                yield event(step, "warning", msg)
            if dry_run:
                yield event(step, "ok", f"{node_id} — presente en {have or 'HEAD desconocido'} ({by})")
                continue
            summary["needs_restart"] = True
            yield event(step, "info", f"{node_id} — ya presente, requiere reiniciar ComfyUI")
            continue

        summary["missing"].append(node_id)
        pin = f" @ {commit[:7]}" if commit else " (sin commit fijado)"
        if dry_run:
            yield event(step, "warning",
                        f"FALTA {node_id}{pin} — lo pide {by}: {reason}")
            continue

        yield event(step, "info", f"Clonando {node_id}{pin} — {reason} [{by}]")
        ok, err = clone_node(entry["repo"], dest, commit)
        if not ok:
            ev, stop = fail(f"Error clonando {node_id} desde {entry['repo']}: {err}")
            yield ev
            if stop:
                yield event("done", "error", f"Falta {node_id}, que es obligatorio.",
                            data=summary)
                return
            continue

        yield event(step, "info", f"Instalando dependencias de {node_id}…")
        ok, err = install_deps(
            comfyui_dir, dest, entry.get("pip_packages"), python_path, uv_path
        )
        if not ok:
            ev, stop = fail(f"Error instalando deps de {node_id}: {err}")
            yield ev
            if stop:
                yield event("done", "error", f"Falta {node_id}, que es obligatorio.",
                            data=summary)
                return
            continue

        summary["needs_restart"] = True
        summary["installed"].append(node_id)
        yield event(step, "ok", f"{node_id} instalado correctamente")

    if summary["needs_restart"]:
        yield event("restart_required", "warning",
                    "Hay nodos nuevos: reinicia ComfyUI para que los cargue.")

    status = "warning" if (summary["warnings"] or summary["needs_restart"]) else "ok"
    yield event("done", status, "Custom nodes al día.", data=summary)


async def install_nodes_async(comfyui_dir: Path, apps_dir: Path, repo_root: Path,
                              loaded_nodes: set[str] | None = None,
                              only_modules: list[str] | None = None,
                              dry_run: bool = False,
                              python_path: Path | None = None,
                              uv_path: Path | None = None):
    """
    `install_nodes` sin bloquear el event loop.

    Los clones y los `pip install` son subprocesos de hasta varios minutos:
    consumir el generador síncrono desde una corrutina congelaría el hub
    entero mientras tanto (incluidos los jobs de cualquier otro módulo). El
    trabajo va a un hilo y los eventos vuelven por una cola, así que el SSE
    sigue emitiendo progreso mientras se instala.
    """
    import asyncio

    loop  = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _END  = object()

    def _worker():
        try:
            for ev in install_nodes(comfyui_dir, apps_dir, repo_root,
                                    loaded_nodes, only_modules, dry_run,
                                    python_path, uv_path):
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as e:                       # no dejar el SSE colgado
            loop.call_soon_threadsafe(queue.put_nowait, {
                "step": "done", "status": "error",
                "msg": f"Error instalando custom nodes: {e}",
                "data": {"needs_restart": False, "warnings": [], "fallbacks": {},
                         "installed": [], "failed": ["<excepción>"], "missing": []}})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _END)

    task = loop.run_in_executor(None, _worker)
    try:
        while True:
            ev = await queue.get()
            if ev is _END:
                break
            yield ev
    finally:
        await task


def install_nodes_logged(comfyui_dir: Path, apps_dir: Path, repo_root: Path,
                         only_modules: list[str] | None = None,
                         logger=None, python_path: Path | None = None,
                         uv_path: Path | None = None) -> dict:
    """
    Envoltorio para el bootstrap: consume el generador contra un HubLogger y
    devuelve el resumen. Offline por definición — durante la instalación
    ComfyUI todavía no está corriendo.
    """
    _LOG = {"ok": "success", "error": "error", "warning": "warn", "info": "info"}
    summary = {}
    for ev in install_nodes(
            comfyui_dir, apps_dir, repo_root, loaded_nodes=set(),
            only_modules=only_modules, python_path=python_path,
            uv_path=uv_path):
        if logger:
            getattr(logger, _LOG.get(ev["status"], "info"))("custom_nodes", ev["msg"])
        else:
            print(f"[custom_nodes] {ev['msg']}", flush=True)
        if ev.get("data"):
            summary = ev["data"]
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────
# El bootstrap solo cubre una instalación desde cero. Esta máquina (y la de
# cualquiera que ya tenga el hub) llegó aquí con los nodos puestos a mano, así
# que hace falta poder auditar y reparar una instalación existente sin
# reinstalar ComfyUI.
#
#   python hub/modules/comfy_nodes.py            # qué falta, sin tocar nada
#   python hub/modules/comfy_nodes.py --install  # clonar lo que falte
#   python hub/modules/comfy_nodes.py --install --module minimax_h3

if __name__ == "__main__":
    import argparse

    _ROOT = Path(__file__).resolve().parent.parent.parent   # ai-hub/

    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--install", action="store_true",
                    help="instalar de verdad (por defecto solo informa)")
    ap.add_argument("--module", action="append", dest="modules",
                    help="limitar a este módulo (repetible)")
    ap.add_argument("--comfyui", default=str(_ROOT / "apps" / "comfyui"),
                    help="directorio de ComfyUI")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    _MARK = {"ok": "  ok  ", "warning": " AVISO", "error": " ERROR", "info": "  ..  "}
    result = {}
    for ev in install_nodes(Path(args.comfyui), _ROOT / "apps", _ROOT,
                            only_modules=args.modules, dry_run=not args.install):
        print(f"{_MARK.get(ev['status'], '      ')}  {ev['msg']}")
        if ev.get("data"):
            result = ev["data"]

    if not args.install and result.get("missing"):
        print(f"\nFaltan {len(result['missing'])} pack(s). "
              f"Para instalarlos: python {Path(__file__).name} --install")
    sys.exit(1 if result.get("failed") else 0)
