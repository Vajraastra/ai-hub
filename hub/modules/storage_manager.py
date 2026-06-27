"""
AI Hub - Storage Manager
Handles model and output directory configuration.
Creates directories, validates paths, builds per-app CLI args using
category-alias mapping system.
Zero external dependencies - stdlib only.
"""

import os
import json
import stat
import shutil


# ============================================================
# Paths
# ============================================================
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
_CATEGORIES_FILE = os.path.join(_CONFIG_DIR, "model_categories.json")


# ============================================================
# Directory links — cross-platform (POSIX symlink / Windows junction)
# ============================================================
# En Windows os.symlink de directorio exige privilegios (Modo Desarrollador
# o admin). Usamos junctions (mklink /J), que NO requieren privilegios y son
# transparentes para las apps de terceros que escanean su propio models_dir.

def _is_dir_link(path):
    """True si `path` es un symlink (POSIX) o junction/symlink-dir (Windows)."""
    if os.path.islink(path):
        return True
    if os.name == "nt":
        # os.path.islink devuelve False para junctions: detectarlas por el
        # atributo reparse point.
        try:
            attrs = os.lstat(path).st_file_attributes
            return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except (OSError, AttributeError):
            return False
    return False


def _link_points_to(link_path, target):
    """True si `link_path` (symlink/junction) resuelve al directorio `target`."""
    try:
        return os.path.samefile(link_path, target)
    except OSError:
        return False


def _remove_dir_link(path):
    """Borra el link sin tocar su contenido (rmdir sobre el reparse point en
    Windows; unlink sobre el symlink en POSIX)."""
    if os.name == "nt":
        try:
            os.rmdir(path)          # junction o symlink-a-directorio
        except OSError:
            os.unlink(path)         # symlink-a-archivo
    else:
        os.unlink(path)


def _create_dir_link(src, dst):
    """Crea un link de directorio `dst` -> `src`. Junction en Windows
    (sin privilegios), symlink en POSIX. Lanza OSError si falla."""
    if os.name == "nt":
        import subprocess
        # mklink espera backslashes; models_dir puede venir con "/" del config
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", os.path.normpath(dst), os.path.normpath(src)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not os.path.exists(dst):
            raise OSError(result.stderr.strip() or "mklink /J falló")
    else:
        os.symlink(src, dst)


# Hub's recommended structure — canonical ComfyUI names
DEFAULT_MODEL_SUBDIRS = [
    "checkpoints",
    "loras",
    "vae",
    "vae_approx",
    "clip",
    "clip_vision",
    "embeddings",
    "controlnet",
    "ipadapter",
    "diffusion_models",
    "upscale_models",
    "style_models",
    "hypernetworks",
    "ultralytics",
    "ultralytics/bbox",   # detectores YOLO (ADetailer)
    "inpaint",
    "model_patches",
    "gligen",
    "sams",
    "photomaker",
    "classifiers",
    "_inbox",
]

# Output subdirectories
OUTPUT_SUBDIRS = [
    "txt2img",
    "img2img",
    "extras",
    "grids",
]


# ============================================================
# Category / Alias System
# ============================================================
def _load_categories() -> dict:
    """Load model categories with aliases from config."""
    try:
        with open(_CATEGORIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("categories", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _build_alias_map(categories: dict) -> dict:
    """
    Build reverse lookup: folder alias name → canonical category name.
    Example: 'StableDiffusion' → 'checkpoints', 'Lora' → 'loras'
    """
    alias_map = {}
    for category_name, info in categories.items():
        for alias in info.get("aliases", []):
            alias_map[alias] = category_name
    return alias_map


# ============================================================
# Directory Creation
# ============================================================
def create_model_dirs(base_path: str) -> bool:
    """Create the hub's recommended model directory structure."""
    try:
        for subdir in DEFAULT_MODEL_SUBDIRS:
            os.makedirs(os.path.join(base_path, subdir), exist_ok=True)
        return True
    except OSError as e:
        print(f"Error creating model dirs: {e}")
        return False


def create_output_dirs(base_path: str) -> bool:
    """Create the centralized output directory structure."""
    try:
        for subdir in OUTPUT_SUBDIRS:
            os.makedirs(os.path.join(base_path, subdir), exist_ok=True)
        return True
    except OSError as e:
        print(f"Error creating output dirs: {e}")
        return False


# ============================================================
# Disk Space
# ============================================================
def get_disk_space(path: str) -> dict:
    """Get disk space info for the given path."""
    try:
        usage = shutil.disk_usage(path)
        return {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent_used": round(usage.used / usage.total * 100, 1),
        }
    except OSError:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent_used": 0}


# ============================================================
# App Model Args Builder (core mapping logic)
# ============================================================
def build_app_model_args(app_config: dict, models_dir: str, app_dir: str = "") -> list:
    """
    Scan the user's models directory, match folder names to categories
    via aliases, then build CLI arguments for the target app.

    Args:
        app_config: App config from registry (must have 'model_map')
        models_dir: Base path to user's model directory
        app_dir: Installation directory of the app (needed for yaml method)

    Returns:
        List of CLI arguments, e.g. ['--ckpt-dirs', '/path/to/StableDiffusion'].
    """
    if not models_dir or not os.path.isdir(models_dir):
        return []

    # Get app's model map (category → {flag, type}) or {method: yaml}
    model_map = app_config.get("model_map", {})
    if not model_map:
        return []

    # --- YAML Method (e.g. ComfyUI) ---
    if model_map.get("method") == "yaml":
        if not app_dir:
            return [] # Cannot write yaml without knowing app directory

        filename = model_map.get("filename", "extra_model_paths.yaml")
        yaml_path = os.path.join(app_dir, filename)
        mappings = model_map.get("mappings", {})
        
        # Build alias map to find ALL folder variants for each category
        # (e.g. both "vae" and "VAE" for the vae category)
        categories = _load_categories()

        yaml_lines = [
            "aihub:",
            f"    base_path: {models_dir}"
        ]

        for comfy_key, hub_category in mappings.items():
            # Collect all folder names that belong to this category (canonical + aliases)
            category_aliases = set(
                categories.get(hub_category, {}).get("aliases", [hub_category])
            )
            # Find which of those folders actually exist and have model files.
            # Symlinks se saltan — apuntan al canónico que ya se incluirá.
            found_folders = []
            try:
                for entry in os.scandir(models_dir):
                    if entry.is_symlink():
                        continue
                    if not entry.is_dir():
                        continue
                    if entry.name not in category_aliases:
                        continue
                    # Include if folder contains at least one file at any depth
                    # (e.g. ultralytics keeps models in bbox/ subdirs)
                    has_files = any(files for _, _, files in os.walk(entry.path))
                    if has_files:
                        found_folders.append(entry.name)
            except OSError:
                pass

            if not found_folders:
                continue

            if len(found_folders) == 1:
                # Simple single-path syntax
                yaml_lines.append(f"    {comfy_key}: {found_folders[0]}")
            else:
                # Multi-path pipe syntax — ComfyUI will scan all listed folders
                yaml_lines.append(f"    {comfy_key}: |")
                for folder in sorted(found_folders):
                    yaml_lines.append(f"        {folder}")
                
        try:
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write("\n".join(yaml_lines) + "\n")
        except OSError as e:
            print(f"Error writing YAML: {e}")

        # Symlinks internos — algunas apps escanean su propio models_dir directamente
        # (ignorando el YAML). Para esos casos, la config declara symlinks a crear.
        # Formato: { "nombre_en_app": "carpeta_en_global_models_dir" }
        app_models_dir = os.path.join(app_dir, "models")
        for link_name, src_folder in model_map.get("symlinks", {}).items():
            src = os.path.join(models_dir, src_folder)
            dst = os.path.join(app_models_dir, link_name)
            if not os.path.exists(src):
                continue
            if _is_dir_link(dst):
                if _link_points_to(dst, src):
                    continue
                _remove_dir_link(dst)  # link apunta a otro lado — reemplazar
            elif os.path.exists(dst):
                continue  # directorio real — no tocar
            try:
                os.makedirs(app_models_dir, exist_ok=True)
                _create_dir_link(src, dst)
            except OSError as e:
                print(f"Error creando link {dst} -> {src}: {e}")

        # ComfyUI automatically loads extra_model_paths.yaml from its root.
        # No extra CLI flags needed for default name.
        if filename != "extra_model_paths.yaml":
            return ["--extra-model-paths-config", yaml_path]
        return []

    # --- CLI Flags Method (e.g. Forge) ---
    # Load categories and build alias lookup
    categories = _load_categories()
    alias_map = _build_alias_map(categories)

    args = []
    replaced = {}  # Track replace-type: flag → best (path, file_count)

    # Scan user's models directory for subdirectories
    try:
        subdirs = sorted(os.listdir(models_dir))
    except OSError:
        return []

    for subdir_name in subdirs:
        subdir_path = os.path.join(models_dir, subdir_name)
        if os.path.islink(subdir_path):
            continue  # skip symlinks — only use canonical dirs
        if not os.path.isdir(subdir_path):
            continue

        # Look up this folder name in our alias map
        category = alias_map.get(subdir_name)
        if category is None:
            continue  # Unknown folder, skip

        # Check if the app supports this category
        mapping = model_map.get(category)
        if mapping is None:
            continue  # App doesn't use this category

        flag = mapping.get("flag", "")
        arg_type = mapping.get("type", "append")

        if not flag:
            continue

        # Count files in this dir (skip empty dirs)
        try:
            file_count = sum(1 for f in os.listdir(subdir_path)
                           if os.path.isfile(os.path.join(subdir_path, f)))
        except OSError:
            file_count = 0

        if file_count == 0:
            continue  # Skip empty directories

        if arg_type == "replace":
            # For replace-type: pick the dir with the most files
            if flag not in replaced or file_count > replaced[flag][1]:
                replaced[flag] = (subdir_path, file_count)
        else:
            # For append-type: add all non-empty dirs
            args.extend([flag, subdir_path])

    # Add replace-type args (best match per flag)
    for flag, (path, _count) in replaced.items():
        args.extend([flag, path])

    return args


# ============================================================
# Path Validation
# ============================================================
def validate_path(path: str) -> dict:
    """Validate a user-provided path."""
    result = {
        "valid": False,
        "exists": False,
        "writable": False,
        "path": os.path.abspath(path),
        "error": None,
    }

    try:
        abs_path = os.path.abspath(path)
        result["path"] = abs_path

        if os.path.exists(abs_path):
            result["exists"] = True
            result["writable"] = os.access(abs_path, os.W_OK)
        else:
            # Check if parent is writable (we can create the dir)
            parent = os.path.dirname(abs_path)
            if os.path.exists(parent) and os.access(parent, os.W_OK):
                result["writable"] = True

        result["valid"] = result["writable"]

    except Exception as e:
        result["error"] = str(e)

    return result


# ============================================================
# Scan Summary (for settings menu display)
# ============================================================
def scan_model_dirs(models_dir: str) -> list:
    """
    Scan a models directory and return recognized categories.
    Used by settings menu to show what the hub detected.

    Returns list of dicts: [{"folder": "Lora", "category": "loras",
                              "files": 142, "size_gb": 252.3}, ...]
    """
    if not models_dir or not os.path.isdir(models_dir):
        return []

    categories = _load_categories()
    alias_map = _build_alias_map(categories)
    results = []

    try:
        subdirs = sorted(os.listdir(models_dir))
    except OSError:
        return []

    for subdir_name in subdirs:
        subdir_path = os.path.join(models_dir, subdir_name)
        if os.path.islink(subdir_path):
            continue  # skip symlinks — only canonical dirs
        if not os.path.isdir(subdir_path):
            continue

        category = alias_map.get(subdir_name, "unknown")

        # Count files (non-recursive, only model files)
        file_count = 0
        try:
            for f in os.listdir(subdir_path):
                if os.path.isfile(os.path.join(subdir_path, f)):
                    file_count += 1
        except OSError:
            pass

        # Skip empty unknown folders
        if file_count == 0 and category == "unknown":
            continue

        # Get folder size (quick estimate from du)
        try:
            usage = shutil.disk_usage(subdir_path)
            # This gives total disk usage, not folder size
            # For a rough estimate we just count file sizes
            total_size = 0
            for f in os.listdir(subdir_path):
                fp = os.path.join(subdir_path, f)
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)
        except OSError:
            total_size = 0

        results.append({
            "folder": subdir_name,
            "category": category,
            "files": file_count,
            "size_gb": round(total_size / (1024 ** 3), 1),
        })

    return results


# === Self-test ===
if __name__ == "__main__":
    print("=" * 50)
    print("AI Hub - Storage Manager")
    print("=" * 50)

    # Test disk space
    space = get_disk_space(".")
    print(f"\n  Disk: {space['free_gb']}GB free / {space['total_gb']}GB total")

    # Test alias map
    cats = _load_categories()
    amap = _build_alias_map(cats)
    print(f"\n  Categories: {len(cats)}")
    print(f"  Aliases: {len(amap)}")
    for alias, cat in sorted(amap.items()):
        print(f"    {alias:25s} → {cat}")
