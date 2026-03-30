import os
import sys
import signal
import shutil
import socket
import threading
import subprocess
from PySide6.QtCore import QObject, Signal

# Ensure we can import from hub.modules
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(GUI_DIR)
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

# gui.state is imported here only as fallback; the preferred approach
# is to pass the state object explicitly via HubWorkers(state)
try:
    from gui.state import state as _default_state
except ImportError:
    _default_state = None

# Import required modules for launching
try:
    from modules.cuda_guardian import build_env_overrides
    from modules.storage_manager import build_app_model_args
    from modules.app_launcher import launch_app
except ImportError as e:
    print(f"Error importing modules: {e}")


class _ConsoleLogger:
    """Logger ligero que escribe a stdout. Instancia única reutilizable."""
    def info(self, tag, msg):    print(f"  [{tag}] {msg}")
    def success(self, tag, msg): print(f"✅ [{tag}] {msg}")
    def error(self, tag, msg):   print(f"❌ [{tag}] {msg}")
    def warn(self, tag, msg):    print(f"⚠️  [{tag}] {msg}")
    def section(self, msg):      print(f"\n{'='*10} {msg} {'='*10}")

_console_logger = _ConsoleLogger()


def _kill_processes_on_port(port: int) -> list[int]:
    """
    Encuentra y termina todos los procesos escuchando en el puerto dado.
    Retorna lista de PIDs eliminados.
    """
    killed = []
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
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


def _is_port_in_use(port: int) -> bool:
    """Comprueba si el puerto está ocupado."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port_free(port: int, timeout: float = 3.0, interval: float = 0.2) -> bool:
    """
    Espera hasta que el puerto esté libre o se agote el timeout.
    Retorna True si el puerto quedó libre, False si se agotó el tiempo.
    """
    import time
    elapsed = 0.0
    while elapsed < timeout:
        if not _is_port_in_use(port):
            return True
        time.sleep(interval)
        elapsed += interval
    return False


class WorkerSignals(QObject):
    """
    Defines the signals available from a running worker thread.
    These are used to safely update the GUI from background threads.
    """
    app_state_changed = Signal(str)   # Emits app_id when state changes
    log_message = Signal(str, str)    # Emits (app_id, log_line)
    error_message = Signal(str, str)  # Emits (app_id, error_string)
    cleanup_result = Signal(str)      # Emits resumen de limpieza de puertos


class HubWorkers:
    """Manages background operations for the Hub GUI so the UI thread doesn't block."""

    def __init__(self, shared_state=None):
        self.signals = WorkerSignals()
        # Use the explicitly passed state object to guarantee it's the same
        # instance as the one in main.py. Fallback to the imported singleton.
        self._state = shared_state if shared_state is not None else _default_state

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _stop_running_app(self, exclude_app_id: str = None):
        """
        Stop any currently running app (single-app safeguard).
        If exclude_app_id is set, only stops other apps (not that one).
        Blocks until the process is dead.
        """
        running_id = self._state.get_running_app()
        if running_id is None or running_id == exclude_app_id:
            return

        proc = self._state.running_apps.get(running_id)
        if proc is None:
            # Stale entry — clean it up
            self._state.running_apps.pop(running_id, None)
            return

        running_name = self._state.registry_apps.get(running_id, {}).get("name", running_id)
        print(f"[workers] Deteniendo '{running_name}' antes de lanzar otra app...")
        self.signals.log_message.emit(
            running_id,
            f"⏹️ Deteniendo {running_name} para liberar memoria..."
        )
        # Mark as busy so the card updates immediately
        self._state.set_busy(running_id, "stopping")
        self.signals.app_state_changed.emit(running_id)

        try:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as e:
            print(f"[workers] Error stopping {running_id}: {e}")
        finally:
            self._state.clear_running(running_id)
            self._state.clear_busy(running_id)
            self.signals.log_message.emit(
                running_id,
                f"✅ {running_name} detenida correctamente."
            )
            self.signals.app_state_changed.emit(running_id)

    # ------------------------------------------------------------------ #
    # Launch                                                               #
    # ------------------------------------------------------------------ #

    def start_app(self, app_id: str):
        """Launches an app asynchronously. Stops any currently running app first."""

        app_cfg = self._state.registry_apps.get(app_id)
        if not app_cfg:
            return

        app_dir = self._state.installed_apps.get(app_id, {}).get("dir")
        if not app_dir or not os.path.isdir(app_dir):
            err = f"Directorio de {app_id} no encontrado."
            print(f"❌ {err}")
            self.signals.error_message.emit(app_id, err)
            return

        def _launch_thread():
            # busy_apps["launching"] ya fue marcado por on_app_action antes de
            # llegar acá. Solo necesitamos detener la app activa si la hay.
            self._stop_running_app(exclude_app_id=app_id)

            try:
                import json
                with open(self._state.config_file, "r") as f:
                    hub_config = json.load(f)

                cuda_env = build_env_overrides(hub_config.get("cuda", {}))
                cuda_env["PYTHONUNBUFFERED"] = "1"

                model_args = []
                models_dir = hub_config.get("paths", {}).get("models")
                if models_dir and os.path.isdir(models_dir):
                    model_args = build_app_model_args(app_cfg, models_dir, app_dir)

                outputs_dir = hub_config.get("paths", {}).get("outputs")
                extra_args = app_cfg.get("extra_args", [])

                user_args = self._state.get_user_extra_args(app_id)

                port_override = self._state.get_port_override(app_id)
                if port_override:
                    extra_args = [a for a in extra_args if not str(a).startswith("--port")]
                    user_args  = [a for a in user_args  if not str(a).startswith("--port")]
                    user_args.append(f"--port={port_override}")

                # Verificar si el puerto ya está en uso y liberar si es necesario
                effective_port = port_override or app_cfg.get("default_port")
                if effective_port and _is_port_in_use(int(effective_port)):
                    self.signals.log_message.emit(
                        app_id,
                        f"⚠ Puerto {effective_port} ocupado — terminando proceso previo..."
                    )
                    killed = _kill_processes_on_port(int(effective_port))
                    if killed:
                        freed = _wait_port_free(int(effective_port), timeout=3.0)
                        self.signals.log_message.emit(
                            app_id,
                            f"✓ Proceso(s) PID {killed} terminado(s). Continuando lanzamiento."
                        )
                    else:
                        self.signals.log_message.emit(
                            app_id,
                            f"✗ No se pudo liberar puerto {effective_port}. El lanzamiento podría fallar."
                        )

                proc = launch_app(
                    app_cfg, app_dir, cuda_env, model_args,
                    extra_args + user_args,
                    outputs_dir, logger=None, async_mode=True, capture_output=True
                )

                if isinstance(proc, subprocess.Popen):
                    # Clear launching state → now truly running
                    self._state.clear_busy(app_id)
                    self._state.set_running(app_id, proc)
                    self._state.log_event("LAUNCH", app_id, f"Iniciada — PID {proc.pid}")
                    self.signals.app_state_changed.emit(app_id)

                    self.signals.log_message.emit(
                        app_id,
                        f"=== {app_cfg.get('name', app_id)} iniciada (PID: {proc.pid}) ==="
                    )

                    # Leer stdout en hilo separado y emitir línea a línea.
                    # El hilo termina naturalmente cuando el proceso cierra su pipe.
                    stdout_done = threading.Event()

                    def _read_stdout(p=proc, aid=app_id):
                        try:
                            for line in p.stdout:
                                # decode with errors='replace' to handle non-UTF-8 output
                                if isinstance(line, bytes):
                                    line = line.decode("utf-8", errors="replace")
                                self.signals.log_message.emit(aid, line.rstrip('\n'))
                        except Exception:
                            pass
                        finally:
                            stdout_done.set()

                    threading.Thread(target=_read_stdout, daemon=True).start()

                    proc.wait()
                    # Esperar a que el lector de stdout drene el pipe antes de
                    # limpiar el estado. Timeout de 2s para no bloquear indefinidamente.
                    stdout_done.wait(timeout=2)

                    self._state.log_event("STOP", app_id, "Proceso terminado")
                    self._state.clear_running(app_id)
                    self.signals.log_message.emit(app_id, f"=== App detenida ===")
                    self.signals.app_state_changed.emit(app_id)
                else:
                    # launch_app returned 0 (cancelled) — clear launching
                    self._state.clear_busy(app_id)
                    self.signals.app_state_changed.emit(app_id)

            except Exception as e:
                err = f"Error lanzando {app_id}: {e}"
                print(f"❌ {err}")
                import traceback
                traceback.print_exc()
                self._state.log_event("ERROR", app_id, f"Error al lanzar: {e}")
                self.signals.error_message.emit(app_id, str(e))
                self._state.clear_running(app_id)
                self._state.clear_busy(app_id)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_launch_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Stop                                                                 #
    # ------------------------------------------------------------------ #

    def stop_app(self, app_id: str):
        """Stops a running app gracefully, then forcefully if needed."""
        proc = self._state.running_apps.get(app_id)
        if not proc:
            return

        # Feedback visual inmediato antes de lanzar el thread
        self._state.set_busy(app_id, "stopping")
        self.signals.app_state_changed.emit(app_id)

        def _stop_thread():
            try:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                self._state.log_event("STOP", app_id, "Detenida por usuario")
            except Exception as e:
                print(f"[workers] Error stopping {app_id}: {e}")
                self.signals.error_message.emit(app_id, str(e))
            finally:
                self._state.clear_running(app_id)
                self._state.clear_busy(app_id)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_stop_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Install                                                              #
    # ------------------------------------------------------------------ #

    def install_app(self, app_id: str):
        """Runs app_installer in a background thread."""

        if app_id in self._state.busy_apps:
            print(f"[workers] '{app_id}' ya tiene una operación en curso.")
            return

        def _install_thread():
            self._state.set_busy(app_id, "installing")
            self.signals.app_state_changed.emit(app_id)
            try:
                import json
                with open(self._state.config_file, "r") as f:
                    hub_config = json.load(f)

                hub_dir = HUB_DIR
                registry_file = os.path.join(hub_dir, "config", "app_registry.json")
                with open(registry_file, "r") as f:
                    registry = json.load(f)

                app_cfg = registry.get("apps", {}).get(app_id)
                if not app_cfg:
                    self.signals.error_message.emit(app_id, "App no encontrada en registry.")
                    return

                apps_dir = os.path.join(os.path.dirname(hub_dir), "apps")

                print(f"\n[{app_id}] Iniciando instalación. Revisa esta terminal para el progreso.")

                from modules.gpu_detector import detect_gpu
                sys_info = detect_gpu()

                from modules.cuda_guardian import get_optimal_cuda, build_env_overrides
                cuda_config = get_optimal_cuda(sys_info.get("max_cuda", "12.1"))
                cuda_env = build_env_overrides(hub_config.get("cuda", cuda_config))

                from modules.app_installer import install_app as _installer
                backups_dir = os.path.join(hub_dir, ".cache", "backups")
                result = _installer(
                    app_config=app_cfg,
                    apps_dir=apps_dir,
                    cuda_env=cuda_env,
                    backups_dir=backups_dir,
                    cuda_config=cuda_config,
                    logger=_console_logger
                )

                if result.get("success"):
                    self._state.installed_apps[app_id] = {
                        "dir": os.path.join(apps_dir, app_id),
                        "installed": True,
                    }
                    self._state.save_state()
                    self._state.log_event("INSTALL", app_id, "Instalación exitosa")
                    print(f"✅ Instalación exitosa de {app_id}.")
                else:
                    err = result.get("error", "Error desconocido")
                    self._state.log_event("ERROR", app_id, f"Instalación fallida: {err}")
                    print(f"❌ Falló instalación de {app_id}: {err}")
                    self.signals.error_message.emit(app_id, f"Error durante instalación: {err}")

            except Exception as e:
                self.signals.error_message.emit(app_id, f"Error del instalador: {e}")
            finally:
                self._state.clear_busy(app_id)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_install_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Update                                                               #
    # ------------------------------------------------------------------ #

    def update_app(self, app_id: str):
        """
        Runs git pull + CUDA verification via app_updater in a background thread.
        Stops the app first if it's currently running.
        """
        if app_id in self._state.busy_apps:
            print(f"[workers] '{app_id}' ya tiene una operación en curso.")
            return

        app_info = self._state.installed_apps.get(app_id)
        if not app_info:
            self.signals.error_message.emit(app_id, f"'{app_id}' no está instalada.")
            return

        app_dir = app_info.get("dir")
        if not app_dir or not os.path.isdir(app_dir):
            self.signals.error_message.emit(app_id, f"Directorio de '{app_id}' no encontrado.")
            return

        def _update_thread():
            # Stop the app if it's running before updating
            if app_id in self._state.running_apps:
                print(f"[workers] Deteniendo '{app_id}' antes de actualizar...")
                self._stop_running_app(exclude_app_id=None)

            self._state.set_busy(app_id, "updating")
            self.signals.app_state_changed.emit(app_id)
            try:
                import json
                with open(self._state.config_file, "r") as f:
                    hub_config = json.load(f)

                app_cfg = self._state.registry_apps.get(app_id, {})
                branch = app_cfg.get("branch", "main")
                cuda_tag = hub_config.get("cuda", {}).get("tag", "cpu")
                cuda_env = build_env_overrides(hub_config.get("cuda", {}))

                backups_dir = os.path.join(HUB_DIR, ".cache", "backups")

                print(f"\n[{app_id}] Actualizando — revisa la terminal para el progreso.")

                from modules.app_updater import update_app as _updater
                result = _updater(
                    app_dir=app_dir,
                    branch=branch,
                    backups_dir=backups_dir,
                    cuda_env=cuda_env,
                    cuda_tag=cuda_tag,
                    logger=_console_logger
                )

                if result.get("success"):
                    if result.get("updated"):
                        old = (result.get("old_commit") or "")[:8]
                        new = (result.get("new_commit") or "")[:8]
                        self._state.log_event("UPDATE", app_id, f"Actualizada: {old} → {new}")
                        print(f"✅ {app_id} actualizada: {old} → {new}")
                        # Regenerar config de modelos post-update
                        models_dir = hub_config.get("paths", {}).get("models")
                        if models_dir and os.path.isdir(models_dir):
                            build_app_model_args(app_cfg, models_dir, app_dir)
                            print(f"[{app_id}] Paths de modelos actualizados post-update.")
                    else:
                        self._state.log_event("UPDATE", app_id, "Ya estaba al día")
                        print(f"✅ {app_id} ya estaba al día.")
                else:
                    err = result.get("error", "Error desconocido")
                    self._state.log_event("ERROR", app_id, f"Error al actualizar: {err}")
                    print(f"❌ Error actualizando {app_id}: {err}")
                    self.signals.error_message.emit(app_id, f"Error al actualizar: {err}")

            except Exception as e:
                self.signals.error_message.emit(app_id, f"Error del actualizador: {e}")
            finally:
                self._state.clear_busy(app_id)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_update_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Uninstall                                                            #
    # ------------------------------------------------------------------ #

    def uninstall_app(self, app_id: str):
        """
        Deletes the app directory and removes it from the config.
        Stops the app first if it's currently running.
        """
        if app_id in self._state.busy_apps:
            print(f"[workers] '{app_id}' ya tiene una operación en curso.")
            return

        app_info = self._state.installed_apps.get(app_id)
        if not app_info:
            self.signals.error_message.emit(app_id, f"'{app_id}' no está instalada.")
            return

        app_dir = app_info.get("dir")

        def _uninstall_thread():
            # Stop the app if it's running before uninstalling
            if app_id in self._state.running_apps:
                print(f"[workers] Deteniendo '{app_id}' antes de desinstalar...")
                self._stop_running_app(exclude_app_id=None)

            self._state.set_busy(app_id, "uninstalling")
            self.signals.app_state_changed.emit(app_id)
            try:
                # Delete directory
                if app_dir and os.path.isdir(app_dir):
                    print(f"[{app_id}] Eliminando {app_dir} ...")
                    shutil.rmtree(app_dir)
                    self._state.log_event("UNINSTALL", app_id, f"Directorio eliminado: {app_dir}")
                    print(f"✅ Carpeta de {app_id} eliminada.")
                else:
                    print(f"⚠️  Directorio de '{app_id}' no encontrado, sólo limpiando config.")

                self._state.installed_apps.pop(app_id, None)
                self._state.save_state()
                self._state.log_event("UNINSTALL", app_id, "Desinstalación completada")
                print(f"✅ {app_id} desinstalada correctamente.")

            except Exception as e:
                err = f"Error desinstalando {app_id}: {e}"
                print(f"❌ {err}")
                self.signals.error_message.emit(app_id, err)
            finally:
                self._state.clear_busy(app_id)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_uninstall_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Cleanup stale processes                                              #
    # ------------------------------------------------------------------ #

    def cleanup_stale_processes(self):
        """
        Escanea los puertos de todas las apps conocidas y mata los procesos
        que los ocupan pero NO están trackeados por el hub.
        Emite cleanup_result con el resumen final.
        """
        def _cleanup_thread():
            import time
            lines = []
            killed_total = 0

            for app_id, app_cfg in self._state.registry_apps.items():
                port = app_cfg.get("default_port")
                if not port:
                    continue

                # No tocar apps que el hub ya está manejando
                if app_id in self._state.running_apps:
                    app_name = app_cfg.get("name", app_id)
                    lines.append(f"  ↷ {app_name} (:{port}) — gestionada por el hub, omitida")
                    continue

                if _is_port_in_use(int(port)):
                    app_name = app_cfg.get("name", app_id)
                    killed = _kill_processes_on_port(int(port))
                    if killed:
                        lines.append(f"  ✓ {app_name} (:{port}) — PID {killed} terminado")
                        killed_total += len(killed)
                    else:
                        lines.append(f"  ✗ {app_name} (:{port}) — puerto ocupado, no se pudo liberar")

            if killed_total:
                time.sleep(0.5)   # dar tiempo al OS para liberar los puertos

            if not lines:
                summary = "✓ No hay procesos stale. Todos los puertos conocidos están libres."
            else:
                header = f"Resultado de limpieza ({killed_total} proceso(s) terminado(s)):\n\n"
                summary = header + "\n".join(lines)

            self.signals.cleanup_result.emit(summary)

        threading.Thread(target=_cleanup_thread, daemon=True).start()
