import os
import sys
import json
import threading

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)


class HubState:
    """Manages the source of truth for the Hub GUI based on the JSON configs."""

    def __init__(self):
        self.registry_file  = os.path.join(HUB_DIR, "config", "app_registry.json")
        self.config_file    = os.path.join(HUB_DIR, "hub_config.json")
        self.flags_file     = os.path.join(HUB_DIR, "config", "app_flags.json")
        self.profiles_dir   = os.path.join(HUB_DIR, "config", "gpu_profiles")
        self.log_file       = os.path.join(HUB_DIR, ".log", "hub_events.log")

        # System status cache
        self.sys_info = {
            "gpu_name":     "Unknown",
            "max_cuda":     "N/A",
            "cuda_tag":     "N/A",
            "torch_version": "N/A",
            "disk_free":    "N/A",
        }

        # Apps cache
        self.registry_apps  = {}
        self.registry_utilities = {}  # Fixed: initialization
        self.installed_apps = {}

        # Lock for thread-safe access to mutable runtime state
        self._lock = threading.Lock()

        # INVARIANT: Only ONE app can be running at a time.
        # app_id -> subprocess.Popen object
        self.running_apps = {}

        # Operations in progress: app_id -> status string
        self.busy_apps = {}

        # GPU profile + app flags caches
        self._gpu_profile = None
        self._app_flags   = None

        self.reload_system_info()
        self.reload_apps()

    # ------------------------------------------------------------------ #
    # System info                                                          #
    # ------------------------------------------------------------------ #

    def reload_system_info(self):
        """Reloads GPU, CUDA, and disk info."""
        try:
            from modules.gpu_detector import detect_gpu
            gpu = detect_gpu()
            self.sys_info["gpu_name"] = gpu.get("gpu_name", "Unknown")
            self.sys_info["max_cuda"] = gpu.get("max_cuda", "N/A")
        except Exception as e:
            print(f"[state] Error loading GPU info: {e}")

        try:
            from modules.cuda_guardian import get_optimal_cuda
            cuda = get_optimal_cuda(self.sys_info["max_cuda"])
            if cuda:
                self.sys_info["cuda_tag"]      = cuda["tag"]
                self.sys_info["torch_version"] = cuda["torch_version"]
        except Exception as e:
            print(f"[state] Error loading CUDA guardian: {e}")

        try:
            import shutil
            apps_dir = os.path.join(os.path.dirname(HUB_DIR), "apps")
            if os.path.exists(apps_dir):
                _, _, free = shutil.disk_usage(apps_dir)
                self.sys_info["disk_free"] = f"{free / (1024**3):.1f} GB"
        except Exception as e:
            print(f"[state] Error checking disk: {e}")

    # ------------------------------------------------------------------ #
    # Apps registry and config                                             #
    # ------------------------------------------------------------------ #

    def reload_apps(self):
        """Reads registry and config files, then verifies directories on disk."""
        try:
            with open(self.registry_file, "r") as f:
                registry = json.load(f)
            self.registry_apps = registry.get("apps", {})
            # Merge utilities into registry_apps for display, or handle separately
            self.registry_utilities = registry.get("utilities", {})
        except Exception as e:
            print(f"[state] Error loading registry: {e}")
            self.registry_apps = {}
            self.registry_utilities = {}

        try:
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)
                self.installed_apps = config.get("installed_apps", {})
            else:
                self.installed_apps = {}
        except Exception as e:
            print(f"[state] Error loading hub config: {e}")
            self.installed_apps = {}

        self._verify_installed_apps()

    def _verify_installed_apps(self):
        """Remove stale entries whose directories no longer exist."""
        stale = [
            app_id for app_id, info in self.installed_apps.items()
            if not os.path.isdir(info.get("dir", ""))
        ]
        if stale:
            for app_id in stale:
                print(f"[state] '{app_id}' directory missing — removing from config.")
                del self.installed_apps[app_id]
            self.save_state()

    def save_state(self):
        """Persists the installed_apps dictionary back to hub_config.json."""
        try:
            config = {}
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)
            config["installed_apps"] = self.installed_apps
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"[state] Error saving hub config: {e}")

    # ------------------------------------------------------------------ #
    # App status                                                           #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Thread-safe accessors for runtime state                              #
    # ------------------------------------------------------------------ #

    def set_busy(self, app_id: str, status: str):
        """Thread-safe: marks an app as busy with the given status."""
        with self._lock:
            self.busy_apps[app_id] = status

    def clear_busy(self, app_id: str):
        """Thread-safe: clears the busy status for an app."""
        with self._lock:
            self.busy_apps.pop(app_id, None)

    def set_running(self, app_id: str, proc):
        """Thread-safe: registers a running process."""
        with self._lock:
            self.running_apps[app_id] = proc

    def clear_running(self, app_id: str):
        """Thread-safe: removes a running process entry."""
        with self._lock:
            self.running_apps.pop(app_id, None)

    def cleanup_stale(self):
        """
        Thread-safe: removes running_apps entries for processes that have
        already terminated. Should be called from the GUI thread.
        Returns list of stale app_ids that were cleaned up.
        """
        with self._lock:
            stale = [
                aid for aid, proc in self.running_apps.items()
                if proc.poll() is not None
            ]
            for aid in stale:
                self.running_apps.pop(aid, None)
        return stale

    def get_app_status(self, app_id: str) -> str:
        """
        Priority: busy (stopping/installing/…) > running > installed > not_installed
        """
        with self._lock:
            if app_id in self.busy_apps:
                return self.busy_apps[app_id]
            if app_id in self.running_apps:
                return "running"
        if app_id in self.installed_apps:
            return "installed"
        if app_id in self.registry_utilities:
            return "installed"  # Utils are built-in
        return "not_installed"

    def get_running_app(self) -> "str | None":
        """Returns the app_id of the currently running app, or None."""
        with self._lock:
            return next(iter(self.running_apps), None)

    # ------------------------------------------------------------------ #
    # Hub paths                                                            #
    # ------------------------------------------------------------------ #

    def get_paths(self) -> dict:
        """Returns the current paths config (models + outputs)."""
        try:
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r") as f:
                    return json.load(f).get("paths", {})
        except Exception:
            pass
        return {}

    def set_paths(self, models_dir: str = None, outputs_dir: str = None):
        """Updates and persists the paths config."""
        try:
            config = {}
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)
            paths = config.setdefault("paths", {})
            if models_dir  is not None: paths["models"]  = models_dir
            if outputs_dir is not None: paths["outputs"] = outputs_dir
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"[state] Error saving paths: {e}")

    def get_civitai_key(self) -> str:
        """Returns the stored Civitai API key."""
        try:
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r") as f:
                    return json.load(f).get("settings", {}).get("civitai_key", "")
        except Exception:
            pass
        return ""

    def set_civitai_key(self, key: str):
        """Persists the Civitai API key."""
        try:
            config = {}
            if os.path.isfile(self.config_file):
                with open(self.config_file, "r") as f:
                    config = json.load(f)
            settings = config.setdefault("settings", {})
            settings["civitai_key"] = key
            with open(self.config_file, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"[state] Error saving civitai key: {e}")

    # ------------------------------------------------------------------ #
    # User extra args + port override (per-app settings)                  #
    # ------------------------------------------------------------------ #

    def get_user_extra_args(self, app_id: str) -> list:
        """Returns the list of user-configured extra CLI args for an app."""
        return self.installed_apps.get(app_id, {}).get("user_extra_args", [])

    def set_user_extra_args(self, app_id: str, args: list):
        """Saves user-configured extra CLI args and persists to config."""
        if app_id in self.installed_apps:
            self.installed_apps[app_id]["user_extra_args"] = args
            self.save_state()

    def get_port_override(self, app_id: str) -> "int | None":
        """Returns the user port override for an app, or None if using default."""
        return self.installed_apps.get(app_id, {}).get("port_override", None)

    def set_port_override(self, app_id: str, port: "int | None"):
        """Saves a port override (or removes it if None) and persists."""
        if app_id in self.installed_apps:
            if port is None:
                self.installed_apps[app_id].pop("port_override", None)
            else:
                self.installed_apps[app_id]["port_override"] = port
            self.save_state()

    # ------------------------------------------------------------------ #
    # GPU profile + app flags                                             #
    # ------------------------------------------------------------------ #

    def load_gpu_profile(self) -> dict:
        """Detects the GPU and loads the matching profile JSON, fallback to default."""
        if self._gpu_profile is not None:
            return self._gpu_profile

        gpu_name = self.sys_info.get("gpu_name", "")
        profile = None

        if os.path.isdir(self.profiles_dir):
            for fname in os.listdir(self.profiles_dir):
                if fname == "default.json":
                    continue
                try:
                    with open(os.path.join(self.profiles_dir, fname), "r") as f:
                        p = json.load(f)
                    for pattern in p.get("names_match", []):
                        if pattern.lower() in gpu_name.lower():
                            profile = p
                            break
                    if profile:
                        break
                except Exception:
                    continue

        if profile is None:
            default_path = os.path.join(self.profiles_dir, "default.json")
            try:
                with open(default_path, "r") as f:
                    profile = json.load(f)
            except Exception:
                profile = {"app_flags": {}}

        self._gpu_profile = profile
        return self._gpu_profile

    def load_app_flags(self) -> dict:
        """Loads the app_flags.json definition file."""
        if self._app_flags is not None:
            return self._app_flags
        try:
            with open(self.flags_file, "r") as f:
                self._app_flags = json.load(f)
        except Exception as e:
            print(f"[state] Error loading app_flags.json: {e}")
            self._app_flags = {}
        return self._app_flags

    # ------------------------------------------------------------------ #
    # Event logging                                                        #
    # ------------------------------------------------------------------ #

    def log_event(self, category: str, app_id: str, message: str):
        """
        Writes a timestamped event to hub_events.log.
        category: INSTALL | LAUNCH | STOP | UPDATE | UNINSTALL | ERROR
        Rotates the file if it exceeds 500KB.
        """
        import datetime
        log_dir = os.path.dirname(self.log_file)
        os.makedirs(log_dir, exist_ok=True)

        # Rotate if too large
        if os.path.isfile(self.log_file) and os.path.getsize(self.log_file) > 512_000:
            bak = self.log_file + ".bak"
            if os.path.isfile(bak):
                os.remove(bak)
            os.rename(self.log_file, bak)

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{category}] {app_id}: {message}\n"
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            print(f"[state] Error writing event log: {e}")

    def read_event_log(self, max_lines: int = 100) -> str:
        """Returns the last N lines of hub_events.log as a single string."""
        try:
            if not os.path.isfile(self.log_file):
                return "(Sin eventos registrados aún)"
            with open(self.log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return "".join(lines[-max_lines:])
        except Exception as e:
            return f"(Error leyendo log: {e})"


# Global singleton state for the GUI
state = HubState()
