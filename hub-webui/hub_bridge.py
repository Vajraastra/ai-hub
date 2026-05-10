"""
Hub Bridge — adaptador sin PySide6 para el WebUI completo.
Envuelve toda la lógica del hub usando callbacks Python simples.
"""
import os
import sys
import json
import signal
import socket
import threading
import subprocess
import time

# ── Paths ────────────────────────────────────────────────────────────────
_POC_DIR  = os.path.dirname(os.path.abspath(__file__))
_HUB_DIR  = os.path.join(os.path.dirname(_POC_DIR), "hub")
_ROOT_DIR = os.path.dirname(_POC_DIR)
_UI_VENV_PY = os.path.join(_HUB_DIR, ".ui_venv", "bin", "python3")

for p in [_HUB_DIR, _ROOT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from gui.state import state as _state


# ── Helpers de puerto ────────────────────────────────────────────────────

def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _kill_processes_on_port(port: int) -> list[int]:
    killed = []
    try:
        result = subprocess.run(["lsof", "-ti", f":{port}"],
                                capture_output=True, text=True, timeout=5)
        pids = [int(p) for p in result.stdout.strip().split() if p.isdigit()]
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except ProcessLookupError:
                pass
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return killed


def _wait_port_free(port: int, timeout: float = 3.0) -> bool:
    elapsed = 0.0
    while elapsed < timeout:
        if not _is_port_in_use(port):
            return True
        time.sleep(0.2)
        elapsed += 0.2
    return False


# ── Hub Bridge ─────────────────────────────────────────────────────────────

class HubBridge:

    def __init__(self):
        self._state = _state
        self._on_state_changed_handlers: list = []
        self._on_log_handlers: list = []
        self._run_startup_sync()

    # ── Startup sync ────────────────────────────────────────────────────

    def _run_startup_sync(self):
        """Al arrancar el hub, regenera configs de rutas para todas las apps."""
        try:
            from modules.model_organizer import sync_all_app_configs, sync_krita_priority

            with open(self._state.config_file, "r") as f:
                hub_config = json.load(f)

            with open(os.path.join(_HUB_DIR, "config", "app_registry.json"), "r") as f:
                registry = json.load(f)

            apps_dir = os.path.join(_ROOT_DIR, "apps")
            sync_all_app_configs(
                hub_config, registry,
                self._state.installed_apps, apps_dir
            )

            models_dir = hub_config.get("paths", {}).get("models", "")
            if models_dir and os.path.isdir(models_dir):
                from modules.storage_manager import create_model_dirs
                create_model_dirs(models_dir)  # garantizar árbol canónico al arrancar
                sync_krita_priority(models_dir)

        except Exception as e:
            import traceback
            print(f"[model_organizer] Error en startup sync: {e}")
            traceback.print_exc()

    # ── Registro de handlers ────────────────────────────────────────────

    def on_state_changed(self, fn):
        self._on_state_changed_handlers.append(fn)

    def on_log(self, fn):
        self._on_log_handlers.append(fn)

    def _emit_state(self, app_id: str):
        for fn in self._on_state_changed_handlers:
            try: fn(app_id)
            except Exception: pass

    def _emit_log(self, app_id: str, line: str):
        for fn in self._on_log_handlers:
            try: fn(app_id, line)
            except Exception: pass

    # ── Apps — lectura ──────────────────────────────────────────────────

    @property
    def state(self):
        return self._state

    def get_apps_payload(self) -> list[dict]:
        self._state.cleanup_stale()
        any_running = bool(self._state.running_apps) or any(
            s == "stopping" for s in self._state.busy_apps.values()
        )
        result = []
        all_apps = {**self._state.registry_apps, **self._state.registry_utilities}
        for app_id, cfg in all_apps.items():
            if cfg.get("is_utility") or cfg.get("is_internal"):
                continue
            proc = self._state.running_apps.get(app_id)
            result.append({
                "id":          app_id,
                "name":        cfg.get("name", app_id),
                "description": cfg.get("description", ""),
                "port":        cfg.get("default_port"),
                "status":      self._state.get_app_status(app_id),
                "pid":         proc.pid if proc else None,
                "any_running": any_running,
                "busy_label":  self._state.busy_apps.get(app_id),
            })
        return result

    def get_sysinfo(self) -> dict:
        info = self._state.sys_info
        return {
            "gpu":  info.get("gpu_name", "GPU desconocida"),
            "cuda": info.get("cuda_tag", ""),
            "disk": info.get("disk_free", ""),
        }

    # ── Hub Settings ────────────────────────────────────────────────────

    def get_hub_settings(self) -> dict:
        """Devuelve paths, civitai_key e info CUDA para la página de ajustes."""
        paths = self._state.get_paths()
        try:
            with open(self._state.config_file, "r") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        cuda = cfg.get("cuda", {})
        return {
            "paths": {
                "models":  paths.get("models", ""),
                "outputs": paths.get("outputs", ""),
            },
            "civitai_key": self._state.get_civitai_key(),
            "cuda": {
                "tag":             cuda.get("tag", ""),
                "torch_version":   cuda.get("torch_version", ""),
                "index_url":       cuda.get("index_url", ""),
            },
            "gpu": {
                "name":    cfg.get("gpu_name", ""),
                "vram_mb": cfg.get("vram_mb", 0),
                "arch":    cfg.get("gpu_arch", ""),
            },
        }

    def save_hub_settings(self, models_path: str, outputs_path: str,
                          civitai_key: str) -> dict:
        """Persiste paths y civitai_key en hub_config.json."""
        try:
            self._state.set_paths(
                models_dir  = models_path  or None,
                outputs_dir = outputs_path or None,
            )
            self._state.set_civitai_key(civitai_key)

            # Si la ruta de modelos es válida, garantizar árbol canónico ComfyUI
            if models_path and os.path.isdir(models_path):
                from modules.storage_manager import create_model_dirs
                create_model_dirs(models_path)

            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def validate_models_path(self, path: str) -> dict:
        """
        Analiza el estado de un directorio de modelos candidato.
        Llamado en tiempo real mientras el usuario escribe o al salir del campo.
        """
        try:
            from modules.model_organizer import check_models_path
            return check_models_path(path)
        except Exception as e:
            return {"state": "error", "error": str(e),
                    "missing_dirs": [], "orphan_count": 0, "orphan_preview": []}

    def apply_models_path(self, path: str, create_if_missing: bool = False) -> dict:
        """
        Aplica un nuevo models_path:
        - Si no existe y create_if_missing=True: crea el directorio + árbol canónico.
        - Si existe: añade las carpetas que falten silenciosamente.
        - Persiste el path en hub_config.
        Returns: {"ok": bool, "created": bool, "missing_added": list, "error": str}
        """
        try:
            from modules.storage_manager import create_model_dirs, DEFAULT_MODEL_SUBDIRS
            from modules.model_organizer import check_models_path

            if not path:
                return {"ok": False, "error": "Path vacío"}

            created = False
            if not os.path.exists(path):
                if not create_if_missing:
                    return {"ok": False, "error": "Directorio no encontrado"}
                os.makedirs(path, exist_ok=True)
                created = True

            # Detectar qué faltaba antes de crear
            missing_before = [d for d in DEFAULT_MODEL_SUBDIRS
                              if not os.path.isdir(os.path.join(path, d))]

            create_model_dirs(path)

            # Persiste en hub_config
            self._state.set_paths(models_dir=path)

            return {
                "ok": True,
                "created": created,
                "missing_added": missing_before,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reorganize_orphans(self, models_path: str) -> dict:
        """
        Mueve archivos de modelo fuera del árbol canónico a _inbox/.
        Incluye sidecars (.json, .preview.*) del mismo modelo.
        """
        try:
            from modules.model_organizer import move_orphans_to_inbox
            return move_orphans_to_inbox(models_path)
        except Exception as e:
            return {"moved": 0, "skipped": 0, "errors": [str(e)]}

    # ── Event Log ───────────────────────────────────────────────────────

    def get_event_log(self, max_lines: int = 200) -> list[dict]:
        """Devuelve el log de eventos como lista de dicts parseados."""
        raw = self._state.read_event_log(max_lines)
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Formato: [2026-04-16 12:34:56] [CATEGORY] app_id: mensaje
            try:
                ts_end = line.index("]") + 1
                ts = line[1:ts_end - 1]
                rest = line[ts_end:].strip()
                cat_end = rest.index("]") + 1
                category = rest[1:cat_end - 1]
                rest2 = rest[cat_end:].strip()
                if ": " in rest2:
                    app_id, message = rest2.split(": ", 1)
                else:
                    app_id, message = "", rest2
                entries.append({
                    "ts": ts, "category": category,
                    "app_id": app_id.strip(), "message": message.strip()
                })
            except (ValueError, IndexError):
                entries.append({"ts": "", "category": "", "app_id": "", "message": line})
        return list(reversed(entries))  # más recientes primero

    # ── Per-app Settings ────────────────────────────────────────────────

    def get_app_config(self, app_id: str) -> dict:
        """
        Devuelve la configuración editable de una app:
          - port_override
          - user_extra_args
          - flags disponibles (de app_flags.json) con su estado actual
        """
        port = self._state.get_port_override(app_id)
        user_args = self._state.get_user_extra_args(app_id)
        default_port = self._state.registry_apps.get(app_id, {}).get("default_port")

        all_flags = self._state.load_app_flags()
        app_flags_def = all_flags.get(app_id, {}).get("flags", [])

        # Determinar cuáles flags están activos (presentes en user_args)
        active_set = set(user_args)
        flags = []
        for f in app_flags_def:
            flags.append({
                "flag":        f["flag"],
                "label":       f.get("label", f["flag"]),
                "description": f.get("description", ""),
                "risk":        f.get("risk", "low"),
                "risk_note":   f.get("risk_note", ""),
                "requires_arg": f.get("requires_arg", False),
                "arg_placeholder": f.get("arg_placeholder"),
                "active":      f["flag"] in active_set,
                # Valor del argumento si lo requiere
                "arg_value": next(
                    (a.split("=", 1)[1] for a in user_args
                     if a.startswith(f["flag"] + "=")),
                    ""
                ) if f.get("requires_arg") else None,
            })

        # Args libres: los que NO son flags conocidos
        known_flags = {f["flag"] for f in app_flags_def}
        free_args = [
            a for a in user_args
            if not any(a == kf or a.startswith(kf + "=") for kf in known_flags)
               and not a.startswith("--port")
        ]

        return {
            "app_id":       app_id,
            "port_override": port,
            "default_port":  default_port,
            "free_args":    " ".join(free_args),
            "flags":        flags,
        }

    def save_app_config(self, app_id: str, port_override,
                        active_flags: list[dict], free_args: str) -> dict:
        """
        Guarda la configuración de una app:
          - port_override (int o None)
          - active_flags: lista de {flag, active, arg_value}
          - free_args: string con args adicionales
        """
        try:
            # Construir user_extra_args desde flags activos + free_args
            new_args = []
            for f in active_flags:
                if f.get("active"):
                    if f.get("requires_arg") and f.get("arg_value"):
                        new_args.append(f"{f['flag']}={f['arg_value']}")
                    else:
                        new_args.append(f["flag"])

            if free_args and free_args.strip():
                new_args.extend(free_args.split())

            # Port override
            port = None
            if port_override and str(port_override).strip():
                try:
                    port = int(str(port_override).strip())
                except ValueError:
                    pass

            self._state.set_user_extra_args(app_id, new_args)
            self._state.set_port_override(app_id, port)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Cleanup ─────────────────────────────────────────────────────────

    def cleanup_stale(self) -> dict:
        """
        Limpia procesos muertos del estado del hub y mata procesos externos
        que ocupen puertos conocidos. Devuelve un resumen.
        """
        lines = []
        killed_total = 0

        # Limpiar procesos hub-gestionados ya muertos
        dead = _state.cleanup_stale()
        for aid in dead:
            name = self._state.registry_apps.get(aid, {}).get("name", aid)
            lines.append({"icon": "↺", "text": f"{name} — proceso muerto, estado limpiado"})

        # Matar procesos externos en puertos conocidos
        for app_id, app_cfg in self._state.registry_apps.items():
            port = app_cfg.get("default_port")
            if not port:
                continue
            name = app_cfg.get("name", app_id)

            if app_id in self._state.running_apps:
                proc = self._state.running_apps.get(app_id)
                if proc and proc.poll() is None:
                    try:
                        proc.send_signal(signal.SIGINT)
                        lines.append({"icon": "⏹", "text": f"{name} :{port} — SIGINT enviado"})
                        killed_total += 1
                    except Exception as e:
                        lines.append({"icon": "✗", "text": f"{name} — error: {e}"})
                continue

            if _is_port_in_use(int(port)):
                killed = _kill_processes_on_port(int(port))
                if killed:
                    lines.append({"icon": "✓", "text": f"{name} :{port} — PID {killed} terminado"})
                    killed_total += len(killed)
                else:
                    lines.append({"icon": "✗", "text": f"{name} :{port} — ocupado, no se pudo liberar"})

        return {
            "killed": killed_total,
            "lines":  lines,
            "clean":  not lines,
        }

    # ── Acciones de apps ────────────────────────────────────────────────

    def launch(self, app_id: str) -> bool:
        if app_id in self._state.busy_apps:
            return False
        app_cfg = self._state.registry_apps.get(app_id)
        if not app_cfg:
            return False
        app_dir = self._state.installed_apps.get(app_id, {}).get("dir")
        if not app_dir or not os.path.isdir(app_dir):
            self._emit_log(app_id, f"❌ Directorio no encontrado: {app_dir}")
            return False
        self._state.set_busy(app_id, "launching")
        self._emit_state(app_id)
        threading.Thread(target=self._launch_thread, args=(app_id,), daemon=True).start()
        return True

    def stop(self, app_id: str) -> bool:
        proc = self._state.running_apps.get(app_id)
        if not proc:
            return False
        self._state.set_busy(app_id, "stopping")
        self._emit_state(app_id)
        threading.Thread(target=self._stop_thread, args=(app_id, proc), daemon=True).start()
        return True

    def install(self, app_id: str) -> bool:
        if app_id in self._state.busy_apps:
            return False
        threading.Thread(target=self._install_thread, args=(app_id,), daemon=True).start()
        return True

    def update(self, app_id: str) -> bool:
        if app_id in self._state.busy_apps:
            return False
        threading.Thread(target=self._update_thread, args=(app_id,), daemon=True).start()
        return True

    def uninstall(self, app_id: str) -> bool:
        if app_id in self._state.busy_apps:
            return False
        threading.Thread(target=self._uninstall_thread, args=(app_id,), daemon=True).start()
        return True

    # ── Threads de fondo ────────────────────────────────────────────────

    def _launch_thread(self, app_id: str):
        try:
            from modules.cuda_guardian import build_env_overrides
            from modules.storage_manager import build_app_model_args
            from modules.app_launcher import launch_app

            app_cfg = self._state.registry_apps[app_id]
            app_dir = self._state.installed_apps[app_id]["dir"]

            with open(self._state.config_file, "r") as f:
                hub_config = json.load(f)

            cuda_env = build_env_overrides(hub_config.get("cuda", {}))
            cuda_env["PYTHONUNBUFFERED"] = "1"
            # Limpiar PYTHONPATH para que los procesos hijos no hereden los
            # site-packages del hub-webui (Python 3.13) y contaminen sus
            # propios venvs (e.g. ComfyUI en Python 3.12 → numpy roto).
            cuda_env["PYTHONPATH"] = ""

            model_args = []
            models_dir = hub_config.get("paths", {}).get("models")
            if models_dir and os.path.isdir(models_dir):
                model_args = build_app_model_args(app_cfg, models_dir, app_dir)

            port_override = self._state.get_port_override(app_id)
            user_args     = self._state.get_user_extra_args(app_id)
            extra_args    = list(app_cfg.get("extra_args", []))

            if port_override:
                extra_args = [a for a in extra_args if not str(a).startswith("--port")]
                user_args  = [a for a in user_args  if not str(a).startswith("--port")]
                user_args.append(f"--port={port_override}")

            effective_port = port_override or app_cfg.get("default_port")

            if effective_port and _is_port_in_use(int(effective_port)):
                self._emit_log(app_id, f"⚠ Puerto {effective_port} ocupado — liberando...")
                killed = _kill_processes_on_port(int(effective_port))
                if killed:
                    _wait_port_free(int(effective_port))
                    self._emit_log(app_id, f"✓ Puerto liberado (PID {killed})")

            outputs_dir = hub_config.get("paths", {}).get("outputs")
            proc = launch_app(
                app_cfg, app_dir, cuda_env,
                model_args, extra_args + user_args,
                outputs_dir, logger=None, async_mode=True, capture_output=True
            )

            if not isinstance(proc, subprocess.Popen):
                self._state.clear_busy(app_id)
                self._emit_state(app_id)
                return

            self._state.clear_busy(app_id)
            self._state.set_running(app_id, proc)
            self._state.log_event("LAUNCH", app_id, f"PID {proc.pid}")
            self._emit_state(app_id)
            self._emit_log(app_id, f"=== {app_cfg.get('name', app_id)} iniciada (PID {proc.pid}) ===")

            if app_cfg.get("auto_open_browser") and effective_port:
                threading.Thread(
                    target=self._open_browser_when_ready,
                    args=(app_id, int(effective_port), proc),
                    daemon=True
                ).start()

            stdout_done = threading.Event()

            def _read_stdout(p=proc, aid=app_id):
                try:
                    for line in p.stdout:
                        if isinstance(line, bytes):
                            line = line.decode("utf-8", errors="replace")
                        self._emit_log(aid, line.rstrip("\n"))
                except Exception:
                    pass
                finally:
                    stdout_done.set()

            threading.Thread(target=_read_stdout, daemon=True).start()
            proc.wait()
            stdout_done.wait(timeout=2)
            self._state.log_event("STOP", app_id, "Proceso terminado")
            self._state.clear_running(app_id)
            self._emit_log(app_id, "=== App detenida ===")
            self._emit_state(app_id)

        except Exception as e:
            import traceback
            self._emit_log(app_id, f"❌ Error lanzando {app_id}: {e}")
            traceback.print_exc()
            self._state.clear_running(app_id)
            self._state.clear_busy(app_id)
            self._emit_state(app_id)

    def _stop_thread(self, app_id: str, proc: subprocess.Popen):
        try:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            self._state.log_event("STOP", app_id, "Detenida por usuario")
        except Exception as e:
            self._emit_log(app_id, f"Error deteniendo {app_id}: {e}")
        finally:
            self._state.clear_running(app_id)
            self._state.clear_busy(app_id)
            self._emit_state(app_id)

    def _install_thread(self, app_id: str):
        self._state.set_busy(app_id, "installing")
        self._emit_state(app_id)
        try:
            from modules.gpu_detector import detect_gpu
            from modules.cuda_guardian import get_optimal_cuda, build_env_overrides
            from modules.app_installer import install_app as _installer

            with open(self._state.config_file, "r") as f:
                hub_config = json.load(f)

            with open(os.path.join(_HUB_DIR, "config", "app_registry.json"), "r") as f:
                registry = json.load(f)

            app_cfg = registry.get("apps", {}).get(app_id)
            if not app_cfg:
                self._emit_log(app_id, "❌ App no encontrada en registry.")
                return

            apps_dir    = os.path.join(_ROOT_DIR, "apps")
            sys_info    = detect_gpu()
            cuda_config = get_optimal_cuda(sys_info.get("max_cuda", "12.1"))
            cuda_env    = build_env_overrides(hub_config.get("cuda", cuda_config))

            result = _installer(
                app_config=app_cfg, apps_dir=apps_dir,
                cuda_env=cuda_env,
                backups_dir=os.path.join(_HUB_DIR, ".cache", "backups"),
                cuda_config=cuda_config, logger=None
            )

            if result.get("success"):
                self._state.installed_apps[app_id] = {
                    "dir": os.path.join(apps_dir, app_id),
                    "installed": True,
                }
                self._state.save_state()
                self._emit_log(app_id, f"✅ {app_id} instalada correctamente.")
            else:
                self._emit_log(app_id, f"❌ Instalación fallida: {result.get('error','?')}")
        except Exception as e:
            self._emit_log(app_id, f"❌ Error del instalador: {e}")
        finally:
            self._state.clear_busy(app_id)
            self._emit_state(app_id)

    def _update_thread(self, app_id: str):
        self._state.set_busy(app_id, "updating")
        self._emit_state(app_id)
        try:
            from modules.cuda_guardian import build_env_overrides
            from modules.app_updater import update_app as _updater

            with open(self._state.config_file, "r") as f:
                hub_config = json.load(f)

            app_cfg  = self._state.registry_apps.get(app_id, {})
            app_dir  = self._state.installed_apps.get(app_id, {}).get("dir", "")
            cuda_env = build_env_overrides(hub_config.get("cuda", {}))

            result = _updater(
                app_dir=app_dir,
                branch=app_cfg.get("branch", "main"),
                backups_dir=os.path.join(_HUB_DIR, ".cache", "backups"),
                cuda_env=cuda_env,
                cuda_tag=hub_config.get("cuda", {}).get("tag", "cpu"),
                app_config=app_cfg,
                cuda_config=hub_config.get("cuda", {}),
                logger=None
            )

            if result.get("success"):
                if result.get("updated"):
                    old = (result.get("old_commit") or "")[:8]
                    new = (result.get("new_commit") or "")[:8]
                    self._emit_log(app_id, f"✅ Actualizada: {old} → {new}")
                else:
                    self._emit_log(app_id, "✅ Ya estaba al día.")
            else:
                self._emit_log(app_id, f"❌ Error al actualizar: {result.get('error','?')}")
        except Exception as e:
            self._emit_log(app_id, f"❌ Error del actualizador: {e}")
        finally:
            self._state.clear_busy(app_id)
            self._emit_state(app_id)

    def _uninstall_thread(self, app_id: str):
        import shutil
        self._state.set_busy(app_id, "uninstalling")
        self._emit_state(app_id)
        try:
            app_dir = self._state.installed_apps.get(app_id, {}).get("dir", "")
            if app_dir and os.path.isdir(app_dir):
                shutil.rmtree(app_dir)
            self._state.installed_apps.pop(app_id, None)
            self._state.save_state()
            self._emit_log(app_id, f"✅ {app_id} desinstalada.")
        except Exception as e:
            self._emit_log(app_id, f"❌ Error desinstalando: {e}")
        finally:
            self._state.clear_busy(app_id)
            self._emit_state(app_id)

    def _open_browser_when_ready(self, app_id: str, port: int,
                                  proc: subprocess.Popen):
        import webbrowser
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            if _is_port_in_use(port):
                time.sleep(0.8)
                webbrowser.open(url)
                self._emit_log(app_id, f"🌐 Browser abierto → {url}")
                return
            time.sleep(0.5)


bridge = HubBridge()
