import os
import sys
import signal
import shutil
import threading
import subprocess
from PySide6.QtCore import QObject, Signal

# Ensure we can import from hub.modules
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(GUI_DIR)
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

from gui.state import state

# Import required modules for launching
try:
    from modules.cuda_guardian import build_env_overrides
    from modules.storage_manager import build_app_model_args
    from modules.app_launcher import launch_app
except ImportError as e:
    print(f"Error importing modules: {e}")


class WorkerSignals(QObject):
    """
    Defines the signals available from a running worker thread.
    These are used to safely update the GUI from background threads.
    """
    app_state_changed = Signal(str)  # Emits app_id when state changes
    log_message = Signal(str, str)   # Emits (app_id, log_line)
    error_message = Signal(str, str) # Emits (app_id, error_string)


class HubWorkers:
    """Manages background operations for the Hub GUI so the UI thread doesn't block."""

    def __init__(self):
        self.signals = WorkerSignals()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _stop_running_app(self, exclude_app_id: str = None):
        """
        Stop any currently running app (single-app safeguard).
        If exclude_app_id is set, only stops other apps (not that one).
        Blocks until the process is dead.
        """
        running_id = state.get_running_app()
        if running_id is None or running_id == exclude_app_id:
            return

        proc = state.running_apps.get(running_id)
        if proc is None:
            # Stale entry — clean it up
            state.running_apps.pop(running_id, None)
            return

        running_name = state.registry_apps.get(running_id, {}).get("name", running_id)
        print(f"[workers] Deteniendo '{running_name}' antes de lanzar otra app...")
        self.signals.log_message.emit(
            running_id,
            f"⏹️ Deteniendo {running_name} para liberar memoria..."
        )
        # Mark as busy so the card updates immediately
        state.busy_apps[running_id] = "stopping"
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
            state.running_apps.pop(running_id, None)
            state.busy_apps.pop(running_id, None)
            self.signals.log_message.emit(
                running_id,
                f"✅ {running_name} detenida correctamente."
            )
            self.signals.app_state_changed.emit(running_id)

    def _make_console_logger(self):
        """Simple logger that writes to stdout (visible in the terminal)."""
        class _Logger:
            def info(self, tag, msg):    print(f"  [{tag}] {msg}")
            def success(self, tag, msg): print(f"✅ [{tag}] {msg}")
            def error(self, tag, msg):   print(f"❌ [{tag}] {msg}")
            def warn(self, tag, msg):    print(f"⚠️  [{tag}] {msg}")
            def section(self, msg):      print(f"\n{'='*10} {msg} {'='*10}")
        return _Logger()

    # ------------------------------------------------------------------ #
    # Launch                                                               #
    # ------------------------------------------------------------------ #

    def start_app(self, app_id: str):
        """Launches an app asynchronously. Stops any currently running app first."""

        app_cfg = state.registry_apps.get(app_id)
        if not app_cfg:
            return

        app_dir = state.installed_apps.get(app_id, {}).get("dir")
        if not app_dir or not os.path.isdir(app_dir):
            err = f"Directorio de {app_id} no encontrado."
            print(f"❌ {err}")
            self.signals.error_message.emit(app_id, err)
            return

        def _launch_thread():
            # --- Single-app safeguard ---
            self._stop_running_app(exclude_app_id=app_id)

            try:
                import json
                with open(state.config_file, "r") as f:
                    hub_config = json.load(f)

                cuda_env = build_env_overrides(hub_config.get("cuda", {}))
                cuda_env["PYTHONUNBUFFERED"] = "1"

                model_args = []
                models_dir = hub_config.get("paths", {}).get("models")
                if models_dir and os.path.isdir(models_dir):
                    model_args = build_app_model_args(app_cfg, models_dir, app_dir)

                outputs_dir = hub_config.get("paths", {}).get("outputs")
                extra_args = app_cfg.get("extra_args", [])

                # Merge user-configured extra args
                user_args = state.get_user_extra_args(app_id)

                # Apply port override if set
                port_override = state.get_port_override(app_id)
                if port_override:
                    # Remove any existing --port from extra_args and user_args, then add override
                    extra_args = [a for a in extra_args if not str(a).startswith("--port")]
                    user_args  = [a for a in user_args  if not str(a).startswith("--port")]
                    user_args.append(f"--port={port_override}")

                proc = launch_app(
                    app_cfg, app_dir, cuda_env, model_args,
                    extra_args + user_args,
                    outputs_dir, logger=None, async_mode=True, capture_output=False
                )

                if isinstance(proc, subprocess.Popen):
                    state.running_apps[app_id] = proc
                    state.log_event("LAUNCH", app_id, f"Iniciada — PID {proc.pid}")
                    self.signals.app_state_changed.emit(app_id)

                    self.signals.log_message.emit(
                        app_id,
                        f"--- App iniciada (PID: {proc.pid}) — Revisa la terminal principal ---"
                    )

                    proc.wait()

                    state.log_event("STOP", app_id, "Proceso terminado")
                    state.running_apps.pop(app_id, None)
                    self.signals.app_state_changed.emit(app_id)

            except Exception as e:
                err = f"Error lanzando {app_id}: {e}"
                print(f"❌ {err}")
                state.log_event("ERROR", app_id, f"Error al lanzar: {e}")
                self.signals.error_message.emit(app_id, str(e))
                state.running_apps.pop(app_id, None)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_launch_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Stop                                                                 #
    # ------------------------------------------------------------------ #

    def stop_app(self, app_id: str):
        """Stops a running app gracefully, then forcefully if needed."""
        proc = state.running_apps.get(app_id)
        if not proc:
            return

        def _stop_thread():
            try:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                state.log_event("STOP", app_id, "Detenida por usuario")
            except Exception as e:
                print(f"[workers] Error stopping {app_id}: {e}")
                self.signals.error_message.emit(app_id, str(e))
            finally:
                state.running_apps.pop(app_id, None)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_stop_thread, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Install                                                              #
    # ------------------------------------------------------------------ #

    def install_app(self, app_id: str):
        """Runs app_installer in a background thread."""

        if app_id in state.busy_apps:
            print(f"[workers] '{app_id}' ya tiene una operación en curso.")
            return

        def _install_thread():
            state.busy_apps[app_id] = "installing"
            self.signals.app_state_changed.emit(app_id)
            try:
                import json
                with open(state.config_file, "r") as f:
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
                    logger=self._make_console_logger()
                )

                if result.get("success"):
                    state.installed_apps[app_id] = {
                        "dir": os.path.join(apps_dir, app_id),
                        "installed": True,
                    }
                    state.save_state()
                    state.log_event("INSTALL", app_id, "Instalación exitosa")
                    print(f"✅ Instalación exitosa de {app_id}.")
                else:
                    err = result.get("error", "Error desconocido")
                    state.log_event("ERROR", app_id, f"Instalación fallida: {err}")
                    print(f"❌ Falló instalación de {app_id}: {err}")
                    self.signals.error_message.emit(app_id, f"Error durante instalación: {err}")

            except Exception as e:
                self.signals.error_message.emit(app_id, f"Error del instalador: {e}")
            finally:
                state.busy_apps.pop(app_id, None)
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
        if app_id in state.busy_apps:
            print(f"[workers] '{app_id}' ya tiene una operación en curso.")
            return

        app_info = state.installed_apps.get(app_id)
        if not app_info:
            self.signals.error_message.emit(app_id, f"'{app_id}' no está instalada.")
            return

        app_dir = app_info.get("dir")
        if not app_dir or not os.path.isdir(app_dir):
            self.signals.error_message.emit(app_id, f"Directorio de '{app_id}' no encontrado.")
            return

        def _update_thread():
            # Stop the app if it's running before updating
            if app_id in state.running_apps:
                print(f"[workers] Deteniendo '{app_id}' antes de actualizar...")
                self._stop_running_app(exclude_app_id=None)

            state.busy_apps[app_id] = "updating"
            self.signals.app_state_changed.emit(app_id)
            try:
                import json
                with open(state.config_file, "r") as f:
                    hub_config = json.load(f)

                app_cfg = state.registry_apps.get(app_id, {})
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
                    logger=self._make_console_logger()
                )

                if result.get("success"):
                    if result.get("updated"):
                        old = (result.get("old_commit") or "")[:8]
                        new = (result.get("new_commit") or "")[:8]
                        state.log_event("UPDATE", app_id, f"Actualizada: {old} → {new}")
                        print(f"✅ {app_id} actualizada: {old} → {new}")
                    else:
                        state.log_event("UPDATE", app_id, "Ya estaba al día")
                        print(f"✅ {app_id} ya estaba al día.")
                else:
                    err = result.get("error", "Error desconocido")
                    state.log_event("ERROR", app_id, f"Error al actualizar: {err}")
                    print(f"❌ Error actualizando {app_id}: {err}")
                    self.signals.error_message.emit(app_id, f"Error al actualizar: {err}")

            except Exception as e:
                self.signals.error_message.emit(app_id, f"Error del actualizador: {e}")
            finally:
                state.busy_apps.pop(app_id, None)
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
        if app_id in state.busy_apps:
            print(f"[workers] '{app_id}' ya tiene una operación en curso.")
            return

        app_info = state.installed_apps.get(app_id)
        if not app_info:
            self.signals.error_message.emit(app_id, f"'{app_id}' no está instalada.")
            return

        app_dir = app_info.get("dir")

        def _uninstall_thread():
            # Stop the app if it's running before uninstalling
            if app_id in state.running_apps:
                print(f"[workers] Deteniendo '{app_id}' antes de desinstalar...")
                self._stop_running_app(exclude_app_id=None)

            state.busy_apps[app_id] = "uninstalling"
            self.signals.app_state_changed.emit(app_id)
            try:
                # Delete directory
                if app_dir and os.path.isdir(app_dir):
                    print(f"[{app_id}] Eliminando {app_dir} ...")
                    shutil.rmtree(app_dir)
                    state.log_event("UNINSTALL", app_id, f"Directorio eliminado: {app_dir}")
                    print(f"✅ Carpeta de {app_id} eliminada.")
                else:
                    print(f"⚠️  Directorio de '{app_id}' no encontrado, sólo limpiando config.")

                state.installed_apps.pop(app_id, None)
                state.save_state()
                state.log_event("UNINSTALL", app_id, "Desinstalación completada")
                print(f"✅ {app_id} desinstalada correctamente.")

            except Exception as e:
                err = f"Error desinstalando {app_id}: {e}"
                print(f"❌ {err}")
                self.signals.error_message.emit(app_id, err)
            finally:
                state.busy_apps.pop(app_id, None)
                self.signals.app_state_changed.emit(app_id)

        threading.Thread(target=_uninstall_thread, daemon=True).start()
