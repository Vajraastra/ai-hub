"""
AI Hub - App Installer
Handles cloning, venv creation, and installation of AI apps.
Uses uv for Python version management per app.
Zero external dependencies - stdlib only.
"""

import subprocess
import os
import sys
import json
import shutil
import stat


def robust_rmtree(path: str, retries: int = 5, retry_delay: float = 0.5) -> None:
    """
    Delete a directory tree, clearing Windows read-only attributes on failure.
    Git repos set pack files (.git/objects/pack/*) read-only, which makes plain
    shutil.rmtree() raise PermissionError on Windows — this is why apps could
    not be uninstalled at all.

    Also retries on locked files (WinError 5): right after killing an app's
    process, Windows can take a brief moment to release DLLs it had memory-mapped
    (e.g. Prisma's query_engine-windows.dll.node), so an immediate delete fails
    even though the process is already dead.
    """
    import time

    def _on_error(func, target, exc_info):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    last_error = None
    for attempt in range(retries):
        try:
            shutil.rmtree(path, onerror=_on_error)
            return
        except (PermissionError, OSError) as e:
            last_error = e
            if not os.path.isdir(path):
                return
            time.sleep(retry_delay)
    raise last_error


_UV_CACHE_CORRUPTION_MARKERS = (
    "failed to read from the distribution cache",
    "missing version prefix",
)


def _find_uv_cache_corruption_packages(lines: list) -> list:
    """
    Detecta la firma de una entrada de cache de uv corrupta: 'Failed to
    download and build `pkg==ver`' seguido de 'Failed to read from the
    distribution cache' / 'missing version prefix'. No es un fallo real del
    paquete — la cache de uv es compartida entre TODAS las apps del hub
    (Nivel 0 de consolidación de deps), así que un build corrupto de una
    sesión anterior rompe cualquier reinstalación futura de cualquier app que
    dependa de ese paquete, hasta limpiarlo a mano. Devuelve los nombres para
    limpiarlos quirúrgicamente en vez de purgar toda la cache.
    """
    import re
    text = "\n".join(lines).lower()
    if not any(marker in text for marker in _UV_CACHE_CORRUPTION_MARKERS):
        return []
    pkgs = []
    for line in lines:
        m = re.search(r"failed to (?:download and build|build)\s*`([^`=<>~! ]+)",
                      line, re.IGNORECASE)
        if m and m.group(1) not in pkgs:
            pkgs.append(m.group(1))
    return pkgs


def _run_streamed(cmd, cwd=None, env=None, logger=None, tag="install",
                  timeout=None, shell=False, _retry=True) -> int:
    """
    Run a subprocess routing each output line through the logger (stderr fused
    into stdout). Without esto, en la webui la salida real de git/uv/npm iba
    solo a la consola del servidor y la UI se quedaba con el exit code pelado.
    El logger decide los destinos (el _UILogger del bridge emite a UI + consola;
    el HubLogger del CLI imprime a terminal). Sin logger, cae a la consola.

    Auto-reparación: si el comando es un `pip install` de uv y falla con la
    firma de cache corrupta (ver _find_uv_cache_corruption_packages), limpia
    solo los paquetes afectados (`uv cache clean <pkg>`) y reintenta UNA vez
    antes de rendirse — así una instalación/desinstalación repetida no vuelve
    a chocar con la misma entrada dañada.

    Returns the exit code; raises subprocess.TimeoutExpired (proceso ya matado).
    """
    import threading

    captured = []
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, shell=shell,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1
    )

    def _pump():
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            captured.append(line)
            if logger:
                logger.info(tag, line)
            else:
                print(line, flush=True)

    pump = threading.Thread(target=_pump, daemon=True)
    pump.start()
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    pump.join(timeout=5)

    if returncode != 0 and _retry:
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if "pip install" in cmd_str:
            corrupt_pkgs = _find_uv_cache_corruption_packages(captured)
            if corrupt_pkgs:
                uv = _get_uv_path()
                if logger:
                    logger.warn(tag, f"Cache de uv corrupta para "
                                      f"{', '.join(corrupt_pkgs)} — "
                                      f"limpiando y reintentando...")
                for pkg in corrupt_pkgs:
                    subprocess.run([uv, "cache", "clean", pkg], cwd=cwd, env=env,
                                    capture_output=True, text=True, timeout=60)
                return _run_streamed(cmd, cwd=cwd, env=env, logger=logger, tag=tag,
                                     timeout=timeout, shell=shell, _retry=False)

    return returncode


def _get_uv_path() -> str:
    """Get the path to uv binary (portable or system)."""
    # Check for portable uv from run.sh/run.bat
    uv_path = os.environ.get("AI_HUB_UV_PATH", "")
    if uv_path and os.path.isfile(uv_path):
        return uv_path
    hub_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.name == "nt":
        # En Windows el binario es uv.exe. OJO: hub/tools/uv (sin extensión) es
        # el ELF Linux arrastrado del disco externo y NO es ejecutable aquí.
        for cand in (os.path.join(hub_dir, "tools", "win", "uv.exe"),
                     os.path.join(hub_dir, "tools", "uv.exe")):
            if os.path.isfile(cand):
                return cand
        return "uv"  # del PATH
    # POSIX: uv portable sin extensión
    tools_uv = os.path.join(hub_dir, "tools", "uv")
    if os.path.isfile(tools_uv):
        return tools_uv
    return "uv"


def _get_install_env() -> dict:
    """Build environment for install commands with UV cache on same drive."""
    env = os.environ.copy()
    hub_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root_dir = os.path.dirname(hub_dir)
    # Always force cache to the hub drive — overrides any env var from the host
    # shell that might point to a different filesystem, which would prevent uv
    # from hardlinking and waste disk space duplicating packages.
    # Ruta ÚNICA compartida con run.bat (raíz): <root>\.cache\uv — si divergen,
    # la deduplicación se fragmenta en caches paralelos.
    env["UV_CACHE_DIR"] = os.path.join(root_dir, ".cache", "uv")
    env["UV_LINK_MODE"] = "hardlink"
    env["UV_PYTHON_INSTALL_DIR"] = os.path.join(hub_dir, "tools", "python")
    # No engancharse a Pythons PEP 514 de otros proyectos: usar solo los
    # managed por uv (descargados a UV_PYTHON_INSTALL_DIR).
    env["UV_PYTHON_PREFERENCE"] = "only-managed"
    return env


def install_uv(logger=None) -> bool:
    """Check if uv is available (portable or system)."""
    uv = _get_uv_path()
    try:
        result = subprocess.run(
            [uv, "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            if logger:
                logger.info("uv", f"uv disponible: {result.stdout.strip()} ({uv})")
            return True
    except FileNotFoundError:
        pass

    if logger:
        logger.info("uv", "Installing uv package manager...")

    if os.name == "nt":
        # Windows
        cmd = ["powershell", "-ExecutionPolicy", "ByPass", "-c",
               "irm https://astral.sh/uv/install.ps1 | iex"]
    else:
        # Linux / macOS
        cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            if logger:
                logger.success("uv", "uv installed successfully")
            return True
        else:
            if logger:
                logger.error("uv", f"uv install failed: {proc.stderr}")
            return False
    except (subprocess.TimeoutExpired, OSError) as e:
        if logger:
            logger.error("uv", f"uv install error: {e}")
        return False


def clone_app(repo_url: str, branch: str, target_dir: str, logger=None) -> bool:
    """Clone an app repository."""
    if os.path.exists(target_dir):
        if logger:
            logger.warn("git", f"Directory already exists: {target_dir}")
        return True

    if logger:
        logger.info("git", f"Cloning {repo_url} (branch: {branch})...")

    try:
        returncode = _run_streamed(
            ["git", "clone", repo_url, target_dir, "--branch", branch, "--depth", "1"],
            logger=logger, tag="git", timeout=300
        )
        if returncode == 0:
            if logger:
                logger.success("git", f"Cloned to {target_dir}")
            return True
        else:
            if logger:
                logger.error("git", f"Clone failed (exit {returncode})")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        if logger:
            logger.error("git", f"Clone error: {e}")
        return False


def create_app_venv(app_dir: str, python_version: str, use_uv: bool = True,
                    logger=None) -> bool:
    """Create an isolated venv for the app with the specified Python version."""
    venv_dir = os.path.join(app_dir, "venv")
    venv_python = get_app_python(app_dir)

    # Sólo reutilizar el venv si tiene el python EJECUTABLE de ESTA plataforma.
    # Un dir "venv" puede ser un venv de otro SO arrastrado (p. ej. ELF Linux
    # con bin/ en vez de Scripts/python.exe) — inservible en Windows.
    if os.path.isfile(venv_python):
        if logger:
            logger.info("venv", f"Venv already exists at {venv_dir}")
        return True
    if os.path.isdir(venv_dir):
        if logger:
            logger.warn("venv", f"Venv inválido (sin {os.path.basename(venv_python)}) — recreando...")
        import shutil
        shutil.rmtree(venv_dir, ignore_errors=True)

    if logger:
        logger.info("venv", f"Creating venv with Python {python_version}...")

    if use_uv:
        uv = _get_uv_path()
        env = _get_install_env()
        try:
            returncode = _run_streamed(
                [uv, "venv", venv_dir, "--python", python_version, "--seed"],
                cwd=app_dir, env=env, logger=logger, tag="venv", timeout=300
            )
            if returncode == 0:
                if logger:
                    logger.success("venv", f"Venv created with Python {python_version}")
                return True
            else:
                if logger:
                    logger.error("venv", f"uv venv failed (exit {returncode})")
                return False
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            if logger:
                logger.error("venv", f"uv error: {e}")
            return False
    else:
        # Fallback: use system Python's venv module
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "venv", venv_dir],
                capture_output=True, text=True, timeout=60,
                cwd=app_dir
            )
            if proc.returncode == 0:
                if logger:
                    logger.success("venv", "Venv created with system Python")
                return True
            else:
                if logger:
                    logger.error("venv", f"venv creation failed: {proc.stderr}")
                return False
        except (subprocess.TimeoutExpired, OSError) as e:
            if logger:
                logger.error("venv", f"venv error: {e}")
            return False


def install_npm_deps(app_dir: str, app_config: dict, logger=None) -> bool:
    """
    Install Node dependencies for launch_type == "npm" apps (e.g. AI Toolkit's
    Next.js UI). Runs npm install, regenerates the Prisma client/DB if the
    project defines an update_db script, and pre-builds so the launcher's
    "already built" marker (.next) is set right after install instead of on
    first launch.
    """
    from modules.app_launcher import find_npm

    npm = find_npm()
    if not npm:
        if logger:
            logger.error("npm", "npm no encontrado (ni sistema ni portable)")
        return False

    launch_subdir = app_config.get("launch_subdir", "")
    work_dir = os.path.join(app_dir, launch_subdir) if launch_subdir else app_dir

    # node_modules arrastrado de otra plataforma (p. ej. migración Linux→Windows):
    # en Windows, cmd.exe necesita los wrappers .cmd/.ps1 en node_modules/.bin
    # para ejecutar cualquier binario — si no existen, el árbol es de otro SO.
    node_modules_dir = os.path.join(work_dir, "node_modules")
    bin_dir = os.path.join(node_modules_dir, ".bin")
    if os.name == "nt" and os.path.isdir(bin_dir):
        bin_entries = os.listdir(bin_dir)
        if bin_entries and not any(f.endswith(".cmd") for f in bin_entries):
            if logger:
                logger.warn("npm", "node_modules de otra plataforma detectado — reinstalando")
            robust_rmtree(node_modules_dir)
            next_dir = os.path.join(work_dir, ".next")
            if os.path.isdir(next_dir):
                robust_rmtree(next_dir)

    env = os.environ.copy()
    env["PATH"] = os.path.dirname(npm) + os.pathsep + env.get("PATH", "")

    if logger:
        logger.info("npm", f"Ejecutando npm install en {work_dir}...")
    try:
        if _run_streamed([npm, "install"], cwd=work_dir, env=env,
                         logger=logger, tag="npm", timeout=900) != 0:
            if logger:
                logger.error("npm", "npm install falló")
            return False
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        if logger:
            logger.error("npm", f"npm install error: {e}")
        return False

    # Si el package.json define un script de setup de DB (p. ej. Prisma
    # generate + db push), correrlo — el cliente/engine es específico de SO.
    package_json = os.path.join(work_dir, "package.json")
    if os.path.isfile(package_json):
        try:
            with open(package_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            if "update_db" in pkg.get("scripts", {}):
                if logger:
                    logger.info("npm", "Regenerando cliente/DB (update_db)...")
                if _run_streamed([npm, "run", "update_db"], cwd=work_dir, env=env,
                                 logger=logger, tag="npm", timeout=120) != 0 and logger:
                    logger.warn("npm", "update_db falló (no crítico)")
        except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            if logger:
                logger.warn("npm", f"No se pudo leer package.json: {e}")

    # No todas las apps npm tienen paso de build (p. ej. un server Express
    # plano solo con "start"/"dev") — correrlo solo si el script existe.
    has_build_script = False
    if os.path.isfile(package_json):
        try:
            with open(package_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            has_build_script = "build" in pkg.get("scripts", {})
        except (OSError, json.JSONDecodeError):
            pass

    if has_build_script:
        if logger:
            logger.info("npm", "Compilando build de producción (npm run build)...")
        try:
            if _run_streamed([npm, "run", "build"], cwd=work_dir, env=env,
                             logger=logger, tag="npm", timeout=900) != 0:
                if logger:
                    logger.error("npm", "npm run build falló")
                return False
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
            if logger:
                logger.error("npm", f"build error: {e}")
            return False
    elif logger:
        logger.info("npm", "Sin script 'build' en package.json — se omite (app sin paso de build)")

    if logger:
        logger.success("npm", "Dependencias Node instaladas y lista para lanzar")
    return True


def get_app_python(app_dir: str) -> str:
    """Get the path to an app's venv Python binary."""
    if os.name == "nt":
        return os.path.join(app_dir, "venv", "Scripts", "python.exe")
    else:
        return os.path.join(app_dir, "venv", "bin", "python")


def backup_requirements(app_dir: str, backups_dir: str, logger=None) -> str | None:
    """Snapshot current pip freeze for rollback capability."""
    python_path = get_app_python(app_dir)
    app_name = os.path.basename(app_dir)

    os.makedirs(backups_dir, exist_ok=True)

    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(backups_dir, f"{app_name}_{timestamp}_freeze.txt")

    try:
        proc = subprocess.run(
            [python_path, "-m", "pip", "freeze"],
            capture_output=True, text=True, timeout=30
        )
        if proc.returncode == 0:
            with open(backup_file, "w") as f:
                f.write(proc.stdout)
            if logger:
                logger.info("backup", f"Requirements snapshot: {backup_file}")
            return backup_file
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        if logger:
            logger.error("backup", f"Backup failed: {e}")

    return None


def install_app(app_config: dict, apps_dir: str, cuda_env: dict,
                backups_dir: str, cuda_config: dict = None,
                gpu_info: dict = None, hub_dir: str = None,
                logger=None) -> dict:
    """
    Full app installation pipeline.

    Args:
        app_config: App definition from registry
        apps_dir: Base directory for installed apps
        cuda_env: Environment overrides from CUDA Guardian
        backups_dir: Directory for requirement backups
        cuda_config: CUDA config dict with tag, torch_version, torchvision_version
        gpu_info: GPU detection dict from gpu_detector (arch, max_cuda, etc.)
        hub_dir: Path to the hub/ directory (for post_install_script resolution)
        logger: HubLogger instance

    Returns:
        dict with installation results
    """
    result = {
        "success": False,
        "app_name": app_config.get("id", "unknown"),
        "app_dir": None,
        "steps_completed": [],
        "error": None,
    }

    app_id = app_config["id"]
    repo_url = app_config["repo"]
    branch = app_config["branch"]
    python_version = app_config.get("python_version", "3.12")
    use_uv = app_config.get("use_uv", True)

    app_dir = os.path.join(apps_dir, app_id)
    result["app_dir"] = app_dir

    if logger:
        logger.section(f"Installing {app_config.get('name', app_id)}")

    # Step 1: Ensure uv is available (if needed)
    if use_uv:
        if not install_uv(logger):
            result["error"] = "Failed to install uv"
            return result
        result["steps_completed"].append("uv_ready")

    # Step 2: Clone repository
    if not clone_app(repo_url, branch, app_dir, logger):
        result["error"] = "Failed to clone repository"
        return result
    result["steps_completed"].append("cloned")

    # Step 3: Create venv
    if not create_app_venv(app_dir, python_version, use_uv, logger):
        result["error"] = "Failed to create venv"
        return result
    result["steps_completed"].append("venv_created")

    # Step 3.5: Node deps + build for launch_type == "npm" apps (e.g. ai-toolkit UI)
    if app_config.get("launch_type") == "npm":
        if not install_npm_deps(app_dir, app_config, logger):
            result["error"] = "Failed to install npm dependencies"
            return result
        result["steps_completed"].append("npm_deps")

    # Steps 4-5: Only if app wants hub to manage deps
    # (forge-neo: install_deps=false → deps install via its own launch.py)
    # (ai-toolkit: install_deps=true → we install torch + requirements.txt)
    if app_config.get("install_deps", False):
        # Step 4: Run pre-install commands (e.g., install torch before requirements)
        pre_cmds = app_config.get("pre_install_commands", [])
        if pre_cmds:
            if not _run_pre_install_commands(pre_cmds, app_dir, cuda_env,
                                             cuda_config, logger):
                result["error"] = "Pre-install commands failed"
                return result
            result["steps_completed"].append("pre_install")

        # Step 5: Install requirements.txt if present
        reqs_file = os.path.join(app_dir, "requirements.txt")
        if os.path.isfile(reqs_file):
            uv = _get_uv_path()
            env = _get_install_env()
            env["VIRTUAL_ENV"] = os.path.join(app_dir, "venv")
            env.update(cuda_env)

            if logger:
                logger.info("install", "Installing requirements.txt...")

            # Handle pip_exclude_packages: filter out packages before install
            # Useful for platform-specific packages (e.g., flash-attn on incompatible CUDA)
            excludes = [p.lower() for p in app_config.get("pip_exclude_packages", [])]
            active_reqs_file = reqs_file
            filtered_reqs_file = None
            if excludes:
                filtered_reqs_file = os.path.join(app_dir, ".pip_filtered_reqs.txt")
                try:
                    with open(reqs_file, "r") as rf, open(filtered_reqs_file, "w") as wf:
                        for line in rf:
                            stripped = line.strip()
                            skip = False
                            for excl in excludes:
                                excl_norm = excl.lower().replace("-", "_")
                                stripped_low = stripped.lower()
                                # Case 1: normal package spec line "flash-attn==2.8.3; ..."
                                pkg_part = stripped_low.split(";")[0].strip()
                                pkg_slug = pkg_part.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
                                pkg_slug_norm = pkg_slug.replace("-", "_")
                                if pkg_slug_norm == excl_norm:
                                    skip = True
                                    break
                                # Case 2: URL wheel line that contains the package name
                                # e.g. https://github.com/.../flash_attn-2.8.3+...-win_amd64.whl
                                if stripped_low.startswith("http") and excl_norm in stripped_low.replace("-", "_"):
                                    skip = True
                                    break
                            if skip:
                                if logger:
                                    logger.info("install", f"  Skipping excluded package: {stripped[:80]}")
                                continue
                            wf.write(line)

                    active_reqs_file = filtered_reqs_file
                    if logger:
                        logger.info("install", f"  Excluded packages: {excludes}")
                except (OSError, IOError) as e:
                    if logger:
                        logger.warn("install", f"  Could not filter requirements: {e} — using original")
                    active_reqs_file = reqs_file

            # Build uv pip install command
            cmd = [uv, "pip", "install", "-r", active_reqs_file]

            # Handle pip_overrides: force specific versions for Python/CUDA compat
            overrides = app_config.get("pip_overrides", [])
            # Resolve template vars in overrides
            if cuda_config:
                overrides = [
                    ov.replace("{torch_version}", cuda_config.get("torch_version", ""))
                      .replace("{torchvision_version}", cuda_config.get("torchvision_version", ""))
                      .replace("{torchaudio_version}", cuda_config.get("torchaudio_version", cuda_config.get("torch_version", "")))
                    for ov in overrides
                ]
            overrides_file = None
            if overrides:
                overrides_file = os.path.join(app_dir, ".pip_overrides.txt")
                with open(overrides_file, "w") as f:
                    for ov in overrides:
                        f.write(ov + "\n")
                cmd.extend(["--override", overrides_file])
                if logger:
                    logger.info("install", f"  Overrides: {overrides}")

            try:
                returncode = _run_streamed(cmd, cwd=app_dir, env=env,
                                           logger=logger, tag="install")

                # Cleanup temp files
                if overrides_file and os.path.isfile(overrides_file):
                    os.remove(overrides_file)
                if filtered_reqs_file and os.path.isfile(filtered_reqs_file):
                    os.remove(filtered_reqs_file)

                if returncode == 0:
                    if logger:
                        logger.success("install", "Requirements installed")
                    result["steps_completed"].append("requirements")
                else:
                    result["error"] = f"requirements.txt install failed (exit {returncode})"
                    return result
            except (OSError, FileNotFoundError) as e:
                result["error"] = f"Install error: {e}"
                return result
        else:
            if logger:
                logger.info("install", "No requirements.txt found.")
    else:
        # App handles deps on first launch (e.g., forge-neo via launch.py)
        if logger:
            logger.info("install", "App manages its own deps on first launch.")

    # Step 6: Apply post-install patches (text substitution in source files)
    # Useful for disabling content filters, hash checks, or other modifications
    patches = app_config.get("post_install_patches", [])
    if patches:
        if logger:
            logger.info("install", f"Applying {len(patches)} post-install patch(es)...")
        for patch in patches:
            patch_file = patch.get("file", "")
            find_text = patch.get("find", "")
            replace_text = patch.get("replace", "")
            if not patch_file or not find_text:
                if logger:
                    logger.warn("install", f"  Skipping invalid patch (missing file or find): {patch}")
                continue
            target_path = os.path.join(app_dir, patch_file)
            if not os.path.isfile(target_path):
                if logger:
                    logger.warn("install", f"  Patch target not found: {patch_file}")
                continue
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if find_text not in content:
                    if logger:
                        logger.warn("install", f"  Patch text not found in {patch_file} — already patched?")
                    continue
                patched = content.replace(find_text, replace_text, 1)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(patched)
                if logger:
                    logger.success("install", f"  Patched: {patch_file}")
                # Clear pycache for the patched module so Python recompiles it
                module_dir = os.path.dirname(target_path)
                pycache_dir = os.path.join(module_dir, "__pycache__")
                if os.path.isdir(pycache_dir):
                    import shutil as _shutil
                    _shutil.rmtree(pycache_dir, ignore_errors=True)
                    if logger:
                        logger.info("install", f"  Cleared __pycache__ for {os.path.basename(module_dir)}")
            except (OSError, IOError) as e:
                if logger:
                    logger.warn("install", f"  Could not apply patch to {patch_file}: {e}")

    if logger:
        for key, value in cuda_env.items():
            logger.info("cuda", f"  {key} = {value}")

    # Step 7: Post-install. Preferimos un script Python cross-platform
    # (post_install_py, ejecutado con el venv de la app, SIN bash); se mantiene
    # post_install_script (.sh vía bash) como legacy para apps no migradas.
    # Recibe info GPU/CUDA por env vars para decisiones de arquitectura.
    post_py     = app_config.get("post_install_py", "")
    post_script = app_config.get("post_install_script", "")
    if post_py or post_script:
        base = hub_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        env = _get_install_env()
        env["APP_DIR"]          = app_dir
        env["VENV_DIR"]         = os.path.join(app_dir, "venv")
        env["VENV_PYTHON"]      = get_app_python(app_dir)   # Scripts\python.exe en Windows
        env["HUB_DIR"]          = base
        env["PLATFORM"]         = sys.platform  # linux, win32, darwin
        env["CUDA_TAG"]         = cuda_config.get("tag", "cu126")          if cuda_config else "cu126"
        env["TORCH_VERSION"]    = cuda_config.get("torch_version", "2.6.0") if cuda_config else "2.6.0"
        env["TORCHVISION_VERSION"] = cuda_config.get("torchvision_version", "0.21.0") if cuda_config else "0.21.0"
        env["TORCHAUDIO_VERSION"]  = cuda_config.get("torchaudio_version", cuda_config.get("torch_version", "2.6.0")) if cuda_config else "2.6.0"
        env["GPU_ARCH"]         = (gpu_info or {}).get("gpu_arch", "unknown")
        env["GPU_VRAM_MB"]      = str((gpu_info or {}).get("vram_mb", 0))
        env["MAX_CUDA"]         = (gpu_info or {}).get("max_cuda", "")     or ""
        env.update(cuda_env)

        if post_py:
            script_path = post_py if os.path.isabs(post_py) else os.path.join(base, post_py)
            runner = [get_app_python(app_dir), script_path]
        else:
            script_path = post_script if os.path.isabs(post_script) else os.path.join(base, post_script)
            runner = ["bash", script_path]

        if not os.path.isfile(script_path):
            result["error"] = f"post_install no encontrado: {script_path}"
            return result

        if logger:
            logger.info("post_install", f"Ejecutando: {os.path.basename(script_path)}")

        try:
            proc = subprocess.Popen(
                runner, env=env, cwd=app_dir,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    if logger:
                        logger.info("post_install", line)
                    else:
                        print(f"[post_install] {line}", flush=True)
            proc.wait()
            if proc.returncode != 0:
                result["error"] = f"post_install falló (exit {proc.returncode})"
                return result
            result["steps_completed"].append("post_install")
            if logger:
                logger.success("post_install", "Completado")
        except (OSError, FileNotFoundError) as e:
            result["error"] = f"Error ejecutando post_install: {e}"
            return result

    result["steps_completed"].append("configured")
    result["success"] = True

    if logger:
        logger.success("install", f"{app_id} installation complete!")

    return result



def _run_pre_install_commands(commands: list, app_dir: str,
                              cuda_env: dict, cuda_config: dict = None,
                              logger=None) -> bool:
    """
    Run pre-install commands (e.g., install torch before requirements.txt).
    Template vars:
        {uv}                = uv path
        {cuda_tag}          = CUDA wheel tag (e.g., cu130)
        {torch_version}     = torch version from cuda_guardian
        {torchvision_version} = torchvision version from cuda_guardian
    """
    uv = _get_uv_path()
    env = _get_install_env()
    env["VIRTUAL_ENV"] = os.path.join(app_dir, "venv")
    env.update(cuda_env)

    # Extract parameters from cuda_config or fallback to env URLs
    cuda_tag = "cu126"
    torch_version = "2.6.0"
    torchvision_version = "0.21.0"
    torchaudio_version = "2.6.0"

    if cuda_config:
        cuda_tag = cuda_config.get("tag", cuda_tag)
        torch_version = cuda_config.get("torch_version", torch_version)
        torchvision_version = cuda_config.get("torchvision_version", torchvision_version)
        torchaudio_version = cuda_config.get("torchaudio_version", torch_version)
    else:
        # Fallback extraction from URL if cuda_config is totally missing
        for val in cuda_env.values():
            if isinstance(val, str) and "whl/cu" in val:
                cuda_tag = val.split("whl/")[-1].strip("/")
                break

    if logger:
        logger.info("pre_install", f"CUDA tag: {cuda_tag}")
        logger.info("pre_install", f"torch: {torch_version}, torchvision: {torchvision_version}, torchaudio: {torchaudio_version}")
        logger.info("pre_install", f"Running {len(commands)} pre-install command(s)...")

    for cmd_template in commands:
        cmd_str = (cmd_template
                   .replace("{uv}", uv)
                   .replace("{cuda_tag}", cuda_tag)
                   .replace("{torch_version}", torch_version)
                   .replace("{torchvision_version}", torchvision_version)
                   .replace("{torchaudio_version}", torchaudio_version))

        if logger:
            logger.info("pre_install", f"  $ {cmd_str}")

        try:
            returncode = _run_streamed(cmd_str, shell=True, cwd=app_dir, env=env,
                                       logger=logger, tag="pre_install")
            if returncode != 0:
                if logger:
                    logger.error("pre_install", f"Command failed (exit {returncode})")
                return False
        except OSError as e:
            if logger:
                logger.error("pre_install", f"Error: {e}")
            return False

    if logger:
        logger.success("pre_install", "Pre-install commands completed")
    return True


# === Self-test ===
if __name__ == "__main__":
    print("=" * 50)
    print("AI Hub - App Installer")
    print("=" * 50)
    print(f"\n  uv check: ", end="")
    try:
        r = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=5)
        print(f"✓ {r.stdout.strip()}" if r.returncode == 0 else "✗ not found")
    except FileNotFoundError:
        print("✗ not installed")
    print(f"  App Python (linux): {get_app_python('/test/app')}")
    print(f"  App Python (mock win): venv/Scripts/python.exe")
