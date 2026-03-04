#!/usr/bin/env python3
"""
AI Hub - Main Entry Point
Handles first-run detection, CLI menu, and orchestration of all modules.
Zero external dependencies - stdlib only.
"""

import os
import sys
import json
import shutil

# Ensure hub directory is in Python path
HUB_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HUB_DIR)

from modules.gpu_detector import detect_gpu, check_prerequisites
from modules.cuda_guardian import get_optimal_cuda, build_env_overrides, get_cpu_fallback
from modules.logger import HubLogger
from modules.storage_manager import (
    create_model_dirs, create_output_dirs, get_disk_space,
    build_app_model_args, validate_path, scan_model_dirs
)
from modules.app_installer import install_app, get_app_python
from modules.app_launcher import launch_app
from modules.app_updater import check_for_updates, update_app

# ============================================================
# ANSI Colors (works on modern terminals everywhere)
# ============================================================
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"

    @staticmethod
    def disable():
        """Disable colors (for non-ANSI terminals)."""
        for attr in ["RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW",
                      "BLUE", "CYAN", "WHITE"]:
            setattr(C, attr, "")


# Disable colors on Windows cmd if needed
if os.name == "nt" and "WT_SESSION" not in os.environ:
    try:
        os.system("")  # Enable ANSI on Windows 10+
    except Exception:
        C.disable()


# ============================================================
# Configuration
# ============================================================
CONFIG_FILE = os.path.join(HUB_DIR, "hub_config.json")
REGISTRY_FILE = os.path.join(HUB_DIR, "config", "app_registry.json")
HUB_ROOT = os.path.dirname(HUB_DIR)  # Parent of hub/
APPS_DIR = os.path.join(HUB_ROOT, "apps")
BACKUPS_DIR = os.path.join(HUB_ROOT, "backups")
BITACORA_DIR = os.path.join(HUB_DIR, "bitacora")

VERSION = "0.1.0"


def load_config() -> dict | None:
    """Load hub configuration file."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_config(config: dict):
    """Save hub configuration file."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_registry() -> dict:
    """Load app registry."""
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"apps": {}}


# ============================================================
# Display Helpers
# ============================================================
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════╗
║              🤖  AI Hub  v{VERSION}              ║
╚══════════════════════════════════════════════╝{C.RESET}
""")


def print_separator():
    print(f"{C.DIM}{'─' * 48}{C.RESET}")


def print_status(config: dict):
    """Print system status bar."""
    gpu = config.get("gpu_name", "Unknown")
    cuda_tag = config.get("cuda", {}).get("tag", "?")
    platform_os = config.get("platform", {}).get("os", "?")
    container = config.get("platform", {}).get("container", "none")

    print(f"  {C.DIM}GPU:{C.RESET} {C.WHITE}{gpu}{C.RESET}")
    print(f"  {C.DIM}CUDA:{C.RESET} {C.GREEN}{cuda_tag}{C.RESET}  "
          f"{C.DIM}Platform:{C.RESET} {platform_os}"
          f"{f' ({container})' if container else ''}")

    # Disk space
    space = get_disk_space(HUB_ROOT)
    color = C.GREEN if space["free_gb"] > 20 else (C.YELLOW if space["free_gb"] > 5 else C.RED)
    print(f"  {C.DIM}Disk:{C.RESET} {color}{space['free_gb']}GB free{C.RESET} / {space['total_gb']}GB")

    # Installed apps count
    installed = config.get("installed_apps", {})
    print(f"  {C.DIM}Apps:{C.RESET} {len(installed)} installed")
    print()


def input_choice(prompt: str, valid: list) -> str:
    """Get user input with validation."""
    while True:
        try:
            choice = input(f"{C.CYAN}  {prompt}{C.RESET} ").strip()
            if choice in valid:
                return choice
            print(f"  {C.RED}Opción inválida. Opciones: {', '.join(valid)}{C.RESET}")
        except (EOFError, KeyboardInterrupt):
            print()
            return valid[-1]  # Return last option (usually exit/back)


def input_text(prompt: str, default: str = "") -> str:
    """Get text input from user with optional default."""
    try:
        suffix = f" [{default}]" if default else ""
        result = input(f"{C.CYAN}  {prompt}{suffix}: {C.RESET}").strip()
        return result if result else default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


# ============================================================
# First Run Wizard
# ============================================================
def first_run(logger: HubLogger) -> dict | None:
    """
    First-run wizard: checks prerequisites, detects GPU, creates structure.
    Returns config dict or None on failure.
    """
    clear_screen()
    print_banner()
    print(f"  {C.YELLOW}{C.BOLD}Primera ejecución detectada. Configurando...{C.RESET}\n")

    logger.section("AI Hub - First Run")

    # Step 1: Check prerequisites
    print(f"  {C.BOLD}[1/4] Verificando prerequisitos...{C.RESET}")
    prereqs = check_prerequisites()

    all_ok = True
    for tool, info in prereqs.items():
        if info["available"]:
            print(f"    {C.GREEN}✓{C.RESET} {tool}: {info.get('version', 'ok')}")
            logger.info("prereqs", f"{tool}: OK - {info.get('version', '')}")
        else:
            if tool == "uv":
                print(f"    {C.YELLOW}○{C.RESET} {tool}: no instalado (se instalará automáticamente)")
                logger.info("prereqs", f"{tool}: not found (will install)")
            elif tool == "nvidia_smi":
                print(f"    {C.YELLOW}⚠{C.RESET} {tool}: no encontrado (modo CPU disponible)")
                logger.warn("prereqs", f"{tool}: not found")
            else:
                print(f"    {C.RED}✗{C.RESET} {tool}: {C.RED}NO ENCONTRADO{C.RESET}")
                logger.error("prereqs", f"{tool}: MISSING")
                if tool == "git":
                    print(f"      {C.DIM}Instalar: sudo apt install git (Ubuntu) / winget install Git.Git (Windows){C.RESET}")
                    all_ok = False

    if not all_ok:
        print(f"\n  {C.RED}Falta software necesario. Instálalo y vuelve a ejecutar.{C.RESET}")
        return None

    print()

    # Step 2: Detect GPU
    print(f"  {C.BOLD}[2/4] Detectando GPU...{C.RESET}")
    gpu_info = detect_gpu()

    if gpu_info["gpu_available"]:
        print(f"    {C.GREEN}✓{C.RESET} {gpu_info['gpu_name']}")
        print(f"    {C.DIM}  VRAM: {gpu_info['vram_mb']}MB | Arch: {gpu_info['gpu_arch']} | "
              f"Driver: {gpu_info['driver_version']} | CUDA: {gpu_info['max_cuda']}{C.RESET}")
        logger.info("gpu", f"Detected: {gpu_info['gpu_name']} ({gpu_info['vram_mb']}MB) "
                    f"CUDA {gpu_info['max_cuda']}")
    else:
        print(f"    {C.RED}✗{C.RESET} No se detectó GPU NVIDIA.")
        print()
        print(f"    {C.RED}AI Hub requiere una GPU NVIDIA con drivers instalados.{C.RESET}")
        print(f"    {C.DIM}Las apps de AI generativa necesitan aceleración GPU.{C.RESET}")
        print(f"    {C.DIM}En CPU tardarían horas por imagen.{C.RESET}")
        logger.error("gpu", "No NVIDIA GPU detected - cannot continue")
        input(f"\n  {C.DIM}Presiona Enter para cerrar...{C.RESET}")
        return None

    print()

    # Step 3: Calculate optimal CUDA
    print(f"  {C.BOLD}[3/4] Configurando CUDA...{C.RESET}")

    if gpu_info["gpu_available"] and gpu_info["max_cuda"]:
        cuda_config = get_optimal_cuda(gpu_info["max_cuda"])
        if cuda_config:
            print(f"    {C.GREEN}✓{C.RESET} Seleccionado: {C.BOLD}{cuda_config['tag']}{C.RESET} "
                  f"(torch {cuda_config['torch_version']})")
            logger.info("cuda", f"Selected: {cuda_config['tag']} "
                       f"(torch {cuda_config['torch_version']})")
        else:
            print(f"    {C.RED}✗{C.RESET} CUDA {gpu_info['max_cuda']} no tiene wheels de PyTorch disponibles.")
            logger.error("cuda", f"No PyTorch wheels for CUDA {gpu_info['max_cuda']}")
            input(f"\n  {C.DIM}Presiona Enter para cerrar...{C.RESET}")
            return None
    else:
        # Should not reach here (run.sh checks GPU first)
        print(f"    {C.RED}✗ GPU no disponible.{C.RESET}")
        return None

    print()

    # Step 4: Create directory structure
    print(f"  {C.BOLD}[4/4] Creando estructura de directorios...{C.RESET}")

    dirs_to_create = [APPS_DIR, BACKUPS_DIR]
    for d in dirs_to_create:
        os.makedirs(d, exist_ok=True)
        print(f"    {C.GREEN}✓{C.RESET} {os.path.relpath(d, HUB_ROOT)}/")

    logger.info("dirs", f"Created: {', '.join(os.path.basename(d) for d in dirs_to_create)}")

    # Build config
    config = {
        "version": VERSION,
        "platform": gpu_info["platform"],
        "gpu_name": gpu_info.get("gpu_name"),
        "gpu_available": gpu_info["gpu_available"],
        "vram_mb": gpu_info.get("vram_mb", 0),
        "gpu_arch": gpu_info.get("gpu_arch"),
        "max_cuda": gpu_info.get("max_cuda"),
        "cuda": cuda_config,
        "paths": {
            "models": None,
            "outputs": None,
        },
        "installed_apps": {},
    }

    save_config(config)
    logger.success("setup", "Hub configuration saved")

    print(f"\n  {C.GREEN}{C.BOLD}✓ AI Hub instalado correctamente!{C.RESET}")
    print(f"  {C.DIM}Configuración guardada en hub_config.json{C.RESET}")
    input(f"\n  {C.DIM}Presiona Enter para continuar...{C.RESET}")

    return config


# ============================================================
# Menu: Apps
# ============================================================
def menu_apps(config: dict, logger: HubLogger):
    """Apps submenu: install, launch, update, uninstall."""
    registry = load_registry()

    while True:
        # Verify installed apps actually exist on disk
        _verify_installed_apps(config, logger)

        clear_screen()
        print_banner()
        print(f"  {C.BOLD}═══ Apps ═══{C.RESET}\n")

        # List available apps
        installed = config.get("installed_apps", {})
        apps = registry.get("apps", {})

        for i, (app_id, app_cfg) in enumerate(apps.items(), 1):
            if app_id in installed:
                status = f"{C.GREEN}[instalada]{C.RESET}"
            else:
                status = f"{C.DIM}[disponible]{C.RESET}"
            print(f"  {C.BOLD}{i}.{C.RESET} {app_cfg['name']} {status}")
            print(f"     {C.DIM}{app_cfg.get('description', '')}{C.RESET}")

        print()
        print_separator()
        print(f"  {C.BOLD}i{C.RESET} = Instalar app")
        print(f"  {C.BOLD}l{C.RESET} = Lanzar app")
        print(f"  {C.BOLD}u{C.RESET} = Actualizar app")
        print(f"  {C.BOLD}d{C.RESET} = Desinstalar app")
        print(f"  {C.BOLD}b{C.RESET} = Volver")
        print()

        choice = input_choice(">>", ["i", "l", "u", "d", "b"])

        if choice == "b":
            return

        # Get app list for selection
        app_list = list(apps.items())

        if choice == "i":
            # Install
            print()
            for i, (app_id, app_cfg) in enumerate(app_list, 1):
                if app_id not in installed:
                    print(f"  {i}. {app_cfg['name']}")

            num = input_text("Número de app a instalar (o 'b' para volver)")
            if num == "b":
                continue

            try:
                idx = int(num) - 1
                if 0 <= idx < len(app_list):
                    app_id, app_cfg = app_list[idx]
                    if app_id not in installed:
                        _do_install(app_id, app_cfg, config, logger)
                    else:
                        print(f"\n  {C.YELLOW}La app ya está instalada.{C.RESET}")
                        input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
            except (ValueError, IndexError):
                pass

        elif choice == "l":
            # Launch
            if not installed:
                print(f"\n  {C.YELLOW}No hay apps instaladas.{C.RESET}")
                input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
                continue

            print()
            installed_list = list(installed.items())
            for i, (app_id, app_info) in enumerate(installed_list, 1):
                app_cfg = apps.get(app_id, {})
                print(f"  {i}. {app_cfg.get('name', app_id)}")

            num = input_text("Número de app a lanzar (o 'b' para volver)")
            if num == "b":
                continue

            try:
                idx = int(num) - 1
                if 0 <= idx < len(installed_list):
                    app_id = installed_list[idx][0]
                    app_cfg = apps.get(app_id, {})
                    _do_launch(app_id, app_cfg, config, logger)
            except (ValueError, IndexError):
                pass

        elif choice == "u":
            # Update
            if not installed:
                print(f"\n  {C.YELLOW}No hay apps instaladas.{C.RESET}")
                input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
                continue

            print()
            installed_list = list(installed.items())
            for i, (app_id, app_info) in enumerate(installed_list, 1):
                app_cfg = apps.get(app_id, {})
                print(f"  {i}. {app_cfg.get('name', app_id)}")

            num = input_text("Número de app a actualizar (o 'b' para volver)")
            if num == "b":
                continue

            try:
                idx = int(num) - 1
                if 0 <= idx < len(installed_list):
                    app_id = installed_list[idx][0]
                    app_cfg = apps.get(app_id, {})
                    _do_update(app_id, app_cfg, config, logger)
            except (ValueError, IndexError):
                pass

        elif choice == "d":
            # Uninstall
            if not installed:
                print(f"\n  {C.YELLOW}No hay apps instaladas.{C.RESET}")
                input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
                continue

            print()
            installed_list = list(installed.items())
            for i, (app_id, app_info) in enumerate(installed_list, 1):
                app_cfg = apps.get(app_id, {})
                print(f"  {i}. {app_cfg.get('name', app_id)}")

            num = input_text("Número de app a desinstalar (o 'b' para volver)")
            if num == "b":
                continue

            try:
                idx = int(num) - 1
                if 0 <= idx < len(installed_list):
                    app_id = installed_list[idx][0]
                    app_cfg = apps.get(app_id, {})
                    _do_uninstall(app_id, app_cfg, config, logger)
            except (ValueError, IndexError):
                pass


def _verify_installed_apps(config: dict, logger: HubLogger):
    """Verify installed apps actually exist on disk. Remove stale entries."""
    installed = config.get("installed_apps", {})
    stale = []
    for app_id, app_info in installed.items():
        app_dir = os.path.join(APPS_DIR, app_id)
        if not os.path.isdir(app_dir):
            stale.append(app_id)

    if stale:
        for app_id in stale:
            del config["installed_apps"][app_id]
            logger.warn("verify", f"App '{app_id}' directory missing, removed from config")
        save_config(config)


def _do_install(app_id: str, app_cfg: dict, config: dict, logger: HubLogger):
    """Execute app installation."""
    print(f"\n  {C.BOLD}Instalando {app_cfg['name']}...{C.RESET}\n")

    cuda_config = config.get("cuda", {})
    cuda_env = build_env_overrides(cuda_config)
    result = install_app(app_cfg, APPS_DIR, cuda_env, BACKUPS_DIR, cuda_config, logger)

    if result["success"]:
        config.setdefault("installed_apps", {})[app_id] = {
            "dir": result["app_dir"],
            "installed": True,
        }
        save_config(config)
        print(f"\n  {C.GREEN}{C.BOLD}✓ {app_cfg['name']} instalada exitosamente!{C.RESET}")
        logger.success("install", f"{app_id} added to installed apps")
    else:
        print(f"\n  {C.RED}✗ Error: {result['error']}{C.RESET}")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


def _do_launch(app_id: str, app_cfg: dict, config: dict, logger: HubLogger):
    """Execute app launch."""
    app_dir = os.path.join(APPS_DIR, app_id)

    cuda_env = build_env_overrides(config.get("cuda", {}))

    # Build model path arguments if configured
    model_args = []
    models_dir = config.get("paths", {}).get("models")
    if models_dir and os.path.isdir(models_dir):
        model_args = build_app_model_args(app_cfg, models_dir)

    # Get outputs directory if configured
    outputs_dir = config.get("paths", {}).get("outputs")

    # Add default extra args from registry
    extra_args = app_cfg.get("extra_args", [])

    print(f"\n  {C.BOLD}Lanzando {app_cfg.get('name', app_id)}...{C.RESET}")
    print(f"  {C.DIM}Ctrl+C para detener{C.RESET}\n")

    launch_app(app_cfg, app_dir, cuda_env, model_args, extra_args,
               outputs_dir, logger)

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


def _do_update(app_id: str, app_cfg: dict, config: dict, logger: HubLogger):
    """Execute app update."""
    app_dir = os.path.join(APPS_DIR, app_id)
    branch = app_cfg.get("branch", "main")
    cuda_tag = config.get("cuda", {}).get("tag", "cpu")
    cuda_env = build_env_overrides(config.get("cuda", {}))

    # Check for updates first
    print(f"\n  {C.BOLD}Buscando actualizaciones para {app_cfg.get('name', app_id)}...{C.RESET}")
    check = check_for_updates(app_dir, branch)

    if not check["update_available"]:
        print(f"\n  {C.GREEN}✓ Ya está actualizada.{C.RESET}")
        input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
        return

    print(f"  {C.YELLOW}Actualización disponible.{C.RESET}")
    confirm = input_text("¿Actualizar? (s/n)", "s")
    if confirm.lower() != "s":
        return

    result = update_app(app_dir, branch, BACKUPS_DIR, cuda_env, cuda_tag, logger)

    if result["success"]:
        if result["updated"]:
            print(f"\n  {C.GREEN}✓ Actualizada: {result['old_commit'][:8]} → {result['new_commit'][:8]}{C.RESET}")
        else:
            print(f"\n  {C.GREEN}✓ Ya estaba actualizada.{C.RESET}")
    else:
        print(f"\n  {C.RED}✗ Error: {result['error']}{C.RESET}")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


def _do_uninstall(app_id: str, app_cfg: dict, config: dict, logger: HubLogger):
    """Uninstall an app (delete directory and remove from config)."""
    app_name = app_cfg.get("name", app_id)
    app_dir = os.path.join(APPS_DIR, app_id)

    print(f"\n  {C.YELLOW}¿Desinstalar {app_name}?{C.RESET}")
    print(f"  {C.DIM}Se eliminará: {app_dir}{C.RESET}")
    confirm = input_text("Confirmar (s/n)", "n")

    if confirm.lower() != "s":
        return

    # Delete directory
    if os.path.isdir(app_dir):
        try:
            shutil.rmtree(app_dir)
            logger.info("uninstall", f"Deleted {app_dir}")
            print(f"  {C.GREEN}✓{C.RESET} Carpeta eliminada")
        except OSError as e:
            logger.error("uninstall", f"Error deleting {app_dir}: {e}")
            print(f"  {C.RED}✗ Error eliminando carpeta: {e}{C.RESET}")
            input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
            return

    # Remove from config
    config.get("installed_apps", {}).pop(app_id, None)
    save_config(config)

    print(f"\n  {C.GREEN}✓ {app_name} desinstalada.{C.RESET}")
    logger.success("uninstall", f"{app_id} removed")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


# ============================================================
# Menu: Settings
# ============================================================
def menu_settings(config: dict, logger: HubLogger):
    """Settings submenu: configure paths, view system info."""
    while True:
        clear_screen()
        print_banner()
        print(f"  {C.BOLD}═══ Configuración ═══{C.RESET}\n")

        models_path = config.get("paths", {}).get("models")
        outputs_path = config.get("paths", {}).get("outputs")

        models_display = models_path or f"{C.DIM}(no configurado){C.RESET}"
        outputs_display = outputs_path or f"{C.DIM}(no configurado){C.RESET}"

        print(f"  {C.BOLD}1.{C.RESET} Carpeta de modelos:  {models_display}")

        # Show scan summary if models path is configured
        if models_path and os.path.isdir(models_path):
            scan = scan_model_dirs(models_path)
            recognized = [s for s in scan if s['category'] != 'unknown']
            total_files = sum(s['files'] for s in recognized)
            total_gb = sum(s['size_gb'] for s in recognized)
            if recognized:
                print(f"     {C.DIM}{len(recognized)} categorías, {total_files} modelos, {total_gb}GB{C.RESET}")

        print(f"  {C.BOLD}2.{C.RESET} Carpeta de outputs:  {outputs_display}")
        print(f"  {C.BOLD}3.{C.RESET} Ver escaneo de modelos")
        print(f"  {C.BOLD}4.{C.RESET} Ver información del sistema")
        print(f"  {C.BOLD}5.{C.RESET} Ver bitácora")
        print(f"  {C.BOLD}b.{C.RESET} Volver")
        print()

        choice = input_choice(">>", ["1", "2", "3", "4", "5", "b"])

        if choice == "b":
            return

        elif choice == "1":
            _configure_path(config, "models", "modelos", create_model_dirs, logger)

        elif choice == "2":
            _configure_path(config, "outputs", "outputs", create_output_dirs, logger)

        elif choice == "3":
            _show_model_scan(config)

        elif choice == "4":
            _show_system_info(config)

        elif choice == "5":
            _show_bitacora()


def _configure_path(config: dict, key: str, label: str,
                    create_fn, logger: HubLogger):
    """Configure a storage path (models or outputs)."""
    current = config.get("paths", {}).get(key)
    default = os.path.join(HUB_ROOT, key)

    print(f"\n  {C.BOLD}Configurar carpeta de {label}{C.RESET}")
    if current:
        print(f"  {C.DIM}Actual: {current}{C.RESET}")
    print(f"  {C.DIM}Default: {default}{C.RESET}")

    path = input_text(f"Ruta para {label}", default)
    path = os.path.abspath(path)

    validation = validate_path(path)
    if not validation["valid"]:
        print(f"\n  {C.RED}Ruta no válida: {validation.get('error', 'no se puede escribir')}{C.RESET}")
        input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
        return

    # Create directory structure
    if create_fn(path):
        config.setdefault("paths", {})[key] = path
        save_config(config)
        print(f"\n  {C.GREEN}✓ Carpeta de {label} configurada: {path}{C.RESET}")
        logger.info("config", f"{key} path set to: {path}")
    else:
        print(f"\n  {C.RED}Error creando directorios en {path}{C.RESET}")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


def _show_model_scan(config: dict):
    """Display detailed scan of the models directory."""
    models_path = config.get("paths", {}).get("models")
    if not models_path or not os.path.isdir(models_path):
        print(f"\n  {C.YELLOW}Configura la carpeta de modelos primero (opción 1).{C.RESET}")
        input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
        return

    print(f"\n  {C.BOLD}═══ Escaneo de modelos ═══{C.RESET}")
    print(f"  {C.DIM}{models_path}{C.RESET}\n")

    scan = scan_model_dirs(models_path)
    if not scan:
        print(f"  {C.DIM}(carpeta vacía){C.RESET}")
        input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
        return

    # Header
    print(f"  {C.DIM}{'Carpeta':25s} {'Categoría':20s} {'Archivos':>10s} {'Tamaño':>10s}{C.RESET}")
    print(f"  {C.DIM}{'-' * 67}{C.RESET}")

    for item in scan:
        folder = item['folder']
        cat = item['category']
        files = str(item['files'])
        size = f"{item['size_gb']}GB" if item['size_gb'] > 0 else "-"

        if cat == 'unknown':
            color = C.DIM
            cat_display = "(no reconocida)"
        else:
            color = C.GREEN
            cat_display = cat

        print(f"  {color}{folder:25s} {cat_display:20s} {files:>10s} {size:>10s}{C.RESET}")

    # Summary
    recognized = [s for s in scan if s['category'] != 'unknown']
    unknown = [s for s in scan if s['category'] == 'unknown']
    total_files = sum(s['files'] for s in recognized)
    total_gb = sum(s['size_gb'] for s in recognized)

    print(f"\n  {C.GREEN}✓{C.RESET} {len(recognized)} categorías reconocidas, {total_files} modelos, {total_gb}GB")
    if unknown:
        print(f"  {C.DIM}{len(unknown)} carpetas no reconocidas (ignoradas al lanzar apps){C.RESET}")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


def _show_system_info(config: dict):
    """Display detailed system information."""
    print(f"\n  {C.BOLD}═══ Sistema ═══{C.RESET}\n")

    p = config.get("platform", {})
    print(f"  OS:         {p.get('os', '?')} ({p.get('arch', '?')})")
    print(f"  Container:  {p.get('container', 'ninguno')}")
    if p.get("container") == "distrobox":
        print(f"  Container:  {p.get('container_name', '?')}")
    print(f"  Mutable:    {'sí' if p.get('mutable', True) else 'no'}")

    print(f"\n  GPU:        {config.get('gpu_name', 'N/A')}")
    print(f"  VRAM:       {config.get('vram_mb', 0)} MB")
    print(f"  Arch:       {config.get('gpu_arch', 'N/A')}")
    print(f"  Driver:     {config.get('max_cuda', 'N/A')} (max CUDA)")
    print(f"  CUDA tag:   {config.get('cuda', {}).get('tag', 'N/A')}")
    print(f"  PyTorch:    {config.get('cuda', {}).get('torch_version', 'N/A')}")

    space = get_disk_space(HUB_ROOT)
    print(f"\n  Disco:      {space['free_gb']}GB libres / {space['total_gb']}GB total")
    print(f"  Hub path:   {HUB_ROOT}")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


def _show_bitacora():
    """Display recent bitácora entries."""
    log_file = os.path.join(BITACORA_DIR, "bitacora.log")
    if not os.path.exists(log_file):
        print(f"\n  {C.DIM}Bitácora vacía.{C.RESET}")
        input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")
        return

    print(f"\n  {C.BOLD}═══ Bitácora (últimas 30 líneas) ═══{C.RESET}\n")
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines[-30:]:
                # Color-code log levels
                if "[ERROR]" in line:
                    print(f"  {C.RED}{line.rstrip()}{C.RESET}")
                elif "[WARN]" in line:
                    print(f"  {C.YELLOW}{line.rstrip()}{C.RESET}")
                elif "[OK]" in line:
                    print(f"  {C.GREEN}{line.rstrip()}{C.RESET}")
                else:
                    print(f"  {C.DIM}{line.rstrip()}{C.RESET}")
    except IOError:
        print(f"  {C.RED}Error leyendo bitácora.{C.RESET}")

    input(f"\n  {C.DIM}Enter para continuar...{C.RESET}")


# ============================================================
# Main Menu
# ============================================================
def main_menu(config: dict, logger: HubLogger):
    """Main hub menu loop."""
    while True:
        clear_screen()
        print_banner()
        print_status(config)
        print_separator()
        print(f"  {C.BOLD}1.{C.RESET} Apps        {C.DIM}(instalar, lanzar, actualizar){C.RESET}")
        print(f"  {C.BOLD}2.{C.RESET} Configuración  {C.DIM}(modelos, outputs, sistema){C.RESET}")
        print(f"  {C.BOLD}3.{C.RESET} Salir")
        print()

        choice = input_choice(">>", ["1", "2", "3"])

        if choice == "1":
            menu_apps(config, logger)
            # Reload config in case it changed
            config = load_config() or config
        elif choice == "2":
            menu_settings(config, logger)
            config = load_config() or config
        elif choice == "3":
            print(f"\n  {C.DIM}Hasta luego! 👋{C.RESET}\n")
            break


# ============================================================
# Entry Point
# ============================================================
def main():
    """Main entry point: first-run detection → menu."""
    # Initialize logger
    logger = HubLogger(BITACORA_DIR)

    # Load or create config
    config = load_config()

    if config is None:
        # First run!
        config = first_run(logger)
        if config is None:
            sys.exit(1)

    # Enter main menu
    main_menu(config, logger)


if __name__ == "__main__":
    main()
