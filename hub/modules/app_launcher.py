"""
AI Hub - App Launcher
Launches AI apps with correct CUDA env vars, model path arguments,
and output directory configuration.
Manages the running process lifecycle.
Zero external dependencies - stdlib only.
"""

import subprocess
import os
import json
import signal
import sys


def _inject_output_config(app_config: dict, app_dir: str,
                          outputs_dir: str, logger=None):
    """
    Inject output directory settings into the app's configuration.
    For method=config_json: writes outdir_* entries into config.json.
    """
    output_map = app_config.get("output_map", {})
    method = output_map.get("method")

    if method != "config_json" or not outputs_dir:
        return

    settings = output_map.get("settings", {})
    if not settings:
        return

    config_path = os.path.join(app_dir, "config.json")

    # Load existing config.json (or start fresh)
    app_cfg = {}
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                app_cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            app_cfg = {}

    # Inject output directory paths
    changed = False
    for setting_key, subdir in settings.items():
        full_path = os.path.join(outputs_dir, subdir)
        os.makedirs(full_path, exist_ok=True)
        if app_cfg.get(setting_key) != full_path:
            app_cfg[setting_key] = full_path
            changed = True

    if changed:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(app_cfg, f, indent=4)
            if logger:
                logger.info("output", f"Output dirs configured → {outputs_dir}")
        except OSError as e:
            if logger:
                logger.error("output", f"Failed to write config.json: {e}")


def _select_config_file(app_config: dict, app_dir: str) -> str:
    """
    Let user select a training config file (for apps with launch_args_style=config_file).
    Returns the config file path, or empty string if cancelled.
    """
    config_dir = os.path.join(app_dir, app_config.get("config_dir", "config"))
    examples_dir = os.path.join(app_dir, app_config.get("config_examples_dir", "config/examples"))

    # Collect config files from both dirs
    configs = []
    for search_dir in [config_dir, examples_dir]:
        if not os.path.isdir(search_dir):
            continue
        for fname in sorted(os.listdir(search_dir)):
            if fname.endswith((".yaml", ".yml", ".json")):
                rel_path = os.path.relpath(
                    os.path.join(search_dir, fname), app_dir
                )
                configs.append(rel_path)

    print("\n  \033[1m═══ Seleccionar config de entrenamiento ═══\033[0m\n")

    if configs:
        print("  \033[2mConfigs disponibles:\033[0m")
        for i, cfg in enumerate(configs, 1):
            print(f"    {i}. {cfg}")
        print(f"    {len(configs) + 1}. \033[2mEscribir ruta manualmente\033[0m")
        print(f"    0. \033[2mCancelar\033[0m")

        try:
            choice = input("\n  Opción: ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

        if choice == "0" or not choice:
            return ""

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(configs):
                return configs[idx]
            elif idx == len(configs):
                # Manual path
                pass
            else:
                print("  \033[91mOpción inválida\033[0m")
                return ""
        except ValueError:
            # Treat raw input as path
            if os.path.isfile(os.path.join(app_dir, choice)):
                return choice
            elif os.path.isfile(choice):
                return choice
            print("  \033[91mArchivo no encontrado\033[0m")
            return ""

    # Manual entry
    try:
        path = input("  Ruta al config (.yaml/.json): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""

    if not path:
        return ""

    # Resolve relative to app_dir or as absolute
    if os.path.isfile(os.path.join(app_dir, path)):
        return path
    elif os.path.isfile(path):
        return path

    print("  \033[91mArchivo no encontrado\033[0m")
    return ""


def _get_launch_command(app_config: dict, app_dir: str) -> list:
    """Build the launch command for an app based on platform and launch type."""
    launch_type = app_config.get("launch_type", "shell")

    if launch_type == "python":
        # Run via the app's own venv Python
        if os.name == "nt":
            python = os.path.join(app_dir, "venv", "Scripts", "python.exe")
        else:
            python = os.path.join(app_dir, "venv", "bin", "python")
        script = app_config.get("launch_script", "launch.py")
        cmd = [python, os.path.join(app_dir, script)]

        # Append static launch_args from registry (e.g., ["run", "--open-browser"] for FaceFusion)
        launch_args = app_config.get("launch_args", [])
        if launch_args:
            cmd.extend(launch_args)

        # For config_file style: append the selected config path
        if app_config.get("launch_args_style") == "config_file":
            config_path = _select_config_file(app_config, app_dir)
            if not config_path:
                return []  # User cancelled
            cmd.append(config_path)

        return cmd


    elif launch_type == "npm":
        # Run via npm (e.g., Next.js UI)
        import shutil as _shutil
        npm_name = "npm.cmd" if os.name == "nt" else "npm"
        npm = _shutil.which(npm_name)
        if not npm:
            # Check AI_HUB_NODE_DIR (set by run.sh)
            node_dir = os.environ.get("AI_HUB_NODE_DIR", "")
            if node_dir:
                candidate = os.path.join(node_dir, npm_name)
                if os.path.isfile(candidate):
                    npm = candidate
        if not npm:
            # Check hub tools portable node
            hub_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidate = os.path.join(hub_dir, "tools", "node", "bin", npm_name)
            if os.path.isfile(candidate):
                npm = candidate
        if not npm:
            # Last resort: common system paths
            for candidate in ["/usr/bin/npm", "/usr/local/bin/npm"]:
                if os.path.isfile(candidate):
                    npm = candidate
                    break
        if not npm:
            return []  # npm truly not found

        # Smart script selection: skip rebuild if already compiled
        build_script = app_config.get("launch_script", "build_and_start")
        run_script = app_config.get("launch_script_run", "start")
        launch_subdir = app_config.get("launch_subdir", "")
        
        if launch_subdir:
            build_marker = os.path.join(app_dir, launch_subdir, ".next")
        else:
            build_marker = os.path.join(app_dir, ".next")

        if os.path.isdir(build_marker):
            # Already built — fast start
            script = run_script
        else:
            # First launch or post-update — full build
            script = build_script

        return [npm, "run", script]

    else:
        # Shell script launch
        if os.name == "nt":
            script = app_config.get("launch_script_win", "webui-user.bat")
            return [os.path.join(app_dir, script)]
        else:
            script = app_config.get("launch_script", "webui.sh")
            script_path = os.path.join(app_dir, script)
            return ["bash", script_path]



def launch_app(app_config: dict, app_dir: str, cuda_env: dict,
               model_args: list = None, extra_args: list = None,
               outputs_dir: str = None, logger=None, async_mode: bool = False, capture_output: bool = False):
    """
    Launch an AI app with CUDA enforcement, model paths, and output config.
    
    Args:
        app_config: App configuration from registry
        app_dir: Path to the installed app
        cuda_env: CUDA environment overrides from cuda_guardian
        model_args: CLI args for model directories (from storage_manager)
        extra_args: Additional CLI args from user config
        outputs_dir: Path to centralized output directory
        logger: HubLogger instance
    
    Returns:
        Process return code if async_mode is False, otherwise the Popen object
    """
    app_name = app_config.get("name", app_config.get("id", "unknown"))

    if logger:
        logger.section(f"Launching {app_name}")

    # Verify app directory exists
    if not os.path.isdir(app_dir):
        if logger:
            logger.error("launch", f"App directory not found: {app_dir}")
        return -1

    # Build environment with CUDA overrides
    env = os.environ.copy()
    env.update(cuda_env)

    # Inject output directory config before launch
    if outputs_dir and os.path.isdir(outputs_dir):
        _inject_output_config(app_config, app_dir, outputs_dir, logger)

    # Add hub/tools/ to PATH so the app can find our portable uv
    tools_dir = os.environ.get("AI_HUB_TOOLS_DIR", "")
    if not tools_dir:
        # Fallback: derive from this module's location
        hub_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tools_dir = os.path.join(hub_dir, "tools")
    if os.path.isdir(tools_dir):
        env["PATH"] = tools_dir + os.pathsep + env.get("PATH", "")

    # Add portable Node.js to PATH (needed for npm-based app UIs)
    node_dir = os.environ.get("AI_HUB_NODE_DIR", "")
    if not node_dir:
        candidate = os.path.join(tools_dir, "node", "bin")
        if os.path.isdir(candidate):
            node_dir = candidate
    if node_dir and os.path.isdir(node_dir):
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

    # Set VIRTUAL_ENV so uv and pip detect the app's venv
    venv_dir = os.path.join(app_dir, "venv")
    if os.path.isdir(venv_dir):
        env["VIRTUAL_ENV"] = venv_dir
        # Add venv bin to PATH so npm workers can access Python + torch
        if os.name == "nt":
            venv_bin = os.path.join(venv_dir, "Scripts")
        else:
            venv_bin = os.path.join(venv_dir, "bin")
        if os.path.isdir(venv_bin):
            env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")

    if logger:
        for key, value in cuda_env.items():
            logger.info("cuda", f"  {key} = {value}")

    # Build the command
    cmd = _get_launch_command(app_config, app_dir)

    if not cmd:
        # User cancelled (e.g., config file selection)
        if logger:
            logger.info("launch", "Launch cancelled by user")
        return 0

    # Add model directory arguments
    if model_args:
        # For forge-neo, these go into COMMANDLINE_ARGS env var
        existing_args = env.get("COMMANDLINE_ARGS", "")
        extra_cli = " ".join(model_args)
        if existing_args:
            env["COMMANDLINE_ARGS"] = f"{existing_args} {extra_cli}"
        else:
            env["COMMANDLINE_ARGS"] = extra_cli
        if logger:
            logger.info("launch", f"  COMMANDLINE_ARGS = {env['COMMANDLINE_ARGS']}")

    # Add extra user args
    if extra_args:
        existing = env.get("COMMANDLINE_ARGS", "")
        extra_cli = " ".join(extra_args)
        env["COMMANDLINE_ARGS"] = f"{existing} {extra_cli}".strip()

    # Determine working directory (npm apps may use a subdir)
    launch_subdir = app_config.get("launch_subdir", "")
    if launch_subdir:
        work_dir = os.path.join(app_dir, launch_subdir)
    else:
        work_dir = app_dir

    if logger:
        logger.info("launch", f"Command: {' '.join(cmd)}")
        logger.info("launch", f"Working dir: {work_dir}")

    # Launch the app in foreground
    try:
        kwargs = {
            "cwd": work_dir,
            "env": env
        }
        if capture_output:
            kwargs["stdin"] = subprocess.PIPE
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.STDOUT
            kwargs["bufsize"] = 1
            kwargs["universal_newlines"] = True
        else:
            kwargs["stdin"] = sys.stdin
            kwargs["stdout"] = sys.stdout
            kwargs["stderr"] = sys.stderr

        proc = subprocess.Popen(cmd, **kwargs)

        if logger:
            logger.info("launch", f"App started (PID: {proc.pid})")

        if async_mode:
            return proc

        # Wait for the app to finish (user exits or presses Ctrl+C)
        try:
            return_code = proc.wait()
        except KeyboardInterrupt:
            if logger:
                logger.info("launch", "Received Ctrl+C, stopping app...")
            proc.send_signal(signal.SIGINT)
            try:
                return_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                return_code = proc.wait(timeout=5)

        if logger:
            logger.info("launch", f"App exited with code: {return_code}")

        return return_code

    except FileNotFoundError as e:
        if logger:
            logger.error("launch", f"Launch script not found: {e}")
        return -1
    except OSError as e:
        if logger:
            logger.error("launch", f"Launch error: {e}")
        return -1


# === Self-test ===
if __name__ == "__main__":
    print("=" * 50)
    print("AI Hub - App Launcher")
    print("=" * 50)
    print("\n  Launcher module loaded successfully.")
    print("  Use launch_app() to start an installed application.")
