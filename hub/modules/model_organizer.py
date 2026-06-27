"""
AI Hub - Model Organizer
Gestión centralizada de rutas de modelos para todas las apps registradas.

Responsabilidades:
  1. sync_all_app_configs() — genera extra_model_paths.yaml (ComfyUI) y
     args CLI (Forge Neo) para todas las apps instaladas, de una sola llamada.
  2. sync_krita_priority() — crea subcarpetas krita/ con symlinks para que
     el plugin Krita AI Diffusion priorice los modelos correctos.
  3. get_model_summary() — resumen legible del estado actual de modelos.

Depende de storage_manager.build_app_model_args() para la lógica de mapeo.
Zero dependencias externas — solo stdlib.
"""

import os
import json
import logging

log = logging.getLogger(__name__)

# Patrones de nombre de archivo que el plugin Krita busca por categoría.
# Si un archivo en la carpeta contiene alguno de estos strings, se considera
# un match. El plugin prioriza archivos dentro de una subcarpeta krita/.
KRITA_SEARCH_PATTERNS = {
    "clip": [
        "clip_l", "clip_g",
        "t5xxl_fp16", "t5xxl_fp8_e4m3fn", "t5-v1_1-xxl",
        "qwen_2.5_vl_7b", "qwen2.5-vl-7b",
        "qwen_3_4b", "qwen3-4b",
    ],
    "vae": [
        "vae-ft-mse-840000-ema", "sdxl_vae", "ae.s",
        "flux-", "flux_",
    ],
    "controlnet": [
        "control_v11p_sd15_inpaint", "control_v11p_sd15_scribble",
        "control_v11p_sd15_lineart", "control_sd15_depth",
        "control_v11p_sd15_openpose", "union-sdxl", "xinsirunion",
        "mistoline_flux", "flux-depth",
    ],
    "loras": [
        "lcm-lora-sdv1-5", "sdxl_lightning_8step_lora",
        "Hyper-SD15-8steps", "Hyper-SDXL-8steps",
        "flux.1-turbo", "anima-turbo-lora",
    ],
    "upscale_models": [
        "4x_NMKD-Superscale", "HAT_SRx4", "OmniSR_X2", "OmniSR_X4",
    ],
    "ipadapter": [
        "ip-adapter_sd15", "ip-adapter_sdxl_vit-h",
        "ip-adapter-faceid-plusv2_sd15", "ip-adapter-faceid_sdxl",
    ],
    "clip_vision": [
        "clip_vision_g", "clip_vision_vit", "clip_vision_h",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Sync all app configs
# ─────────────────────────────────────────────────────────────────────────────

def sync_all_app_configs(hub_config: dict, registry: dict,
                         installed_apps: dict, apps_dir: str) -> dict:
    """
    Genera configuraciones de rutas de modelos para todas las apps instaladas.
    Llama a storage_manager.build_app_model_args() por cada app.

    Returns:
        dict con resultados por app_id: {"ok": bool, "args": list, "error": str}
    """
    from modules.storage_manager import build_app_model_args

    models_dir = hub_config.get("paths", {}).get("models", "")
    if not models_dir or not os.path.isdir(models_dir):
        log.warning("sync_all_app_configs: models_dir no válido: %s", models_dir)
        return {}

    results = {}
    all_apps = {**registry.get("apps", {}), **registry.get("utilities", {})}

    for app_id, app_cfg in all_apps.items():
        if not app_cfg.get("model_map"):
            continue
        if app_id not in installed_apps:
            continue

        app_dir = installed_apps[app_id].get("dir", "")
        if not app_dir or not os.path.isdir(app_dir):
            continue

        try:
            args = build_app_model_args(app_cfg, models_dir, app_dir)
            results[app_id] = {"ok": True, "args": args}
            log.info("sync_all_app_configs: %s — OK (%d args)", app_id, len(args))
        except Exception as e:
            results[app_id] = {"ok": False, "args": [], "error": str(e)}
            log.error("sync_all_app_configs: %s — ERROR: %s", app_id, e)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Krita priority symlinks
# ─────────────────────────────────────────────────────────────────────────────

def _link_model_file(real_path: str, link_path: str, rel_target: str) -> None:
    """Enlaza un archivo de modelo dentro de krita/.
    En Windows el symlink de archivo exige privilegios: usamos hardlink
    (os.link), que no los requiere para archivos en el mismo volumen — y
    E:\\Models contiene tanto el archivo real como la carpeta krita/.
    En POSIX mantenemos el symlink relativo (portable al mover la carpeta)."""
    if os.name == "nt":
        os.link(real_path, link_path)          # hardlink, sin privilegios
    else:
        os.symlink(rel_target, link_path)      # symlink relativo

def sync_krita_priority(models_dir: str, dry_run: bool = False) -> dict:
    """
    Crea subcarpetas krita/ dentro de cada categoría relevante.
    Dentro de krita/ crea symlinks a los archivos que coinciden con los
    patrones que el plugin Krita busca. El plugin los prioriza automáticamente.

    Args:
        models_dir: Ruta base de modelos (e.g. /run/media/.../Models)
        dry_run: Si True, solo reporta cambios sin ejecutarlos.

    Returns:
        dict con estadísticas: {"created": int, "skipped": int, "errors": list}
    """
    MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft"}
    stats = {"created": 0, "skipped": 0, "errors": []}

    for category, patterns in KRITA_SEARCH_PATTERNS.items():
        cat_dir = os.path.join(models_dir, category)
        if not os.path.isdir(cat_dir):
            continue

        krita_dir = os.path.join(cat_dir, "krita")

        for root, dirs, files in os.walk(cat_dir):
            # No recursionar dentro de la propia carpeta krita/
            dirs[:] = [d for d in dirs if d != "krita"]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in MODEL_EXTENSIONS:
                    continue

                fname_lower = fname.lower()
                if not any(p.lower() in fname_lower for p in patterns):
                    continue

                link_path = os.path.join(krita_dir, fname)
                if os.path.lexists(link_path):
                    stats["skipped"] += 1
                    continue

                # Ruta relativa del symlink al archivo real
                real_path = os.path.join(root, fname)
                rel_target = os.path.relpath(real_path, krita_dir)

                if dry_run:
                    log.info("[DRY] krita symlink: %s/%s → %s", category, fname, rel_target)
                    stats["created"] += 1
                    continue

                try:
                    os.makedirs(krita_dir, exist_ok=True)
                    _link_model_file(real_path, link_path, rel_target)
                    stats["created"] += 1
                    log.info("krita link: %s/%s → %s", category, fname, rel_target)
                except Exception as e:
                    stats["errors"].append(f"{category}/{fname}: {e}")
                    log.error("Error creando link krita %s/%s: %s", category, fname, e)

    return stats


def remove_krita_priority(models_dir: str) -> int:
    """Elimina todas las subcarpetas krita/ creadas por sync_krita_priority."""
    import shutil
    removed = 0
    for category in KRITA_SEARCH_PATTERNS:
        krita_dir = os.path.join(models_dir, category, "krita")
        if os.path.isdir(krita_dir):
            shutil.rmtree(krita_dir)
            removed += 1
            log.info("Eliminada carpeta krita/: %s", krita_dir)
    return removed


# ─────────────────────────────────────────────────────────────────────────────
# Model summary
# ─────────────────────────────────────────────────────────────────────────────

def get_model_summary(models_dir: str) -> list[dict]:
    """
    Devuelve un resumen de las carpetas canónicas: nombre, archivos, tamaño.
    Solo carpetas reales (no symlinks) para evitar doble conteo.
    """
    from modules.storage_manager import _load_categories

    MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft", ".bin"}
    categories = _load_categories()
    canonical_names = set(categories.keys())
    summary = []

    if not os.path.isdir(models_dir):
        return summary

    for entry in sorted(os.scandir(models_dir), key=lambda e: e.name.lower()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.name not in canonical_names:
            continue

        file_count = 0
        total_bytes = 0
        for root, _, files in os.walk(entry.path):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in MODEL_EXTENSIONS:
                    try:
                        total_bytes += os.path.getsize(os.path.join(root, f))
                        file_count += 1
                    except OSError:
                        pass

        summary.append({
            "category":  entry.name,
            "files":     file_count,
            "size_gb":   round(total_bytes / (1024 ** 3), 2),
            "path":      entry.path,
        })

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Path validation and orphan management
# ─────────────────────────────────────────────────────────────────────────────

MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft", ".bin"}
SIDECAR_EXTENSIONS = {".json", ".preview.jpeg", ".preview.jpg", ".preview.png", ".png", ".txt"}


def check_models_path(path: str) -> dict:
    """
    Analiza el estado del directorio de modelos.

    Returns dict:
      state: "ok" | "not_found" | "incomplete" | "empty"
      missing_dirs: list[str]  — carpetas canónicas que faltan
      orphan_count: int        — archivos de modelo fuera del árbol canónico
      orphan_preview: list[str] — primeros 5 paths de huérfanos para mostrar al usuario
    """
    from modules.storage_manager import DEFAULT_MODEL_SUBDIRS

    if not path:
        return {"state": "not_found", "missing_dirs": [], "orphan_count": 0, "orphan_preview": []}

    if not os.path.exists(path):
        return {"state": "not_found", "missing_dirs": list(DEFAULT_MODEL_SUBDIRS),
                "orphan_count": 0, "orphan_preview": []}

    # Qué carpetas canónicas faltan
    missing = [d for d in DEFAULT_MODEL_SUBDIRS if not os.path.isdir(os.path.join(path, d))]

    # Buscar archivos de modelo fuera del árbol canónico:
    # - archivos en la raíz del directorio
    # - archivos en carpetas no canónicas y no-symlink
    canonical_set = set(DEFAULT_MODEL_SUBDIRS)
    orphans = []

    try:
        for entry in os.scandir(path):
            if entry.is_file():
                if os.path.splitext(entry.name)[1].lower() in MODEL_EXTENSIONS:
                    orphans.append(entry.path)
            elif entry.is_dir() and not entry.is_symlink():
                if entry.name not in canonical_set:
                    for root, _, files in os.walk(entry.path):
                        for f in files:
                            if os.path.splitext(f)[1].lower() in MODEL_EXTENSIONS:
                                orphans.append(os.path.join(root, f))
    except OSError:
        pass

    if missing:
        state = "incomplete"
    elif not os.listdir(path):
        state = "empty"
    else:
        state = "ok"

    return {
        "state": state,
        "missing_dirs": missing,
        "orphan_count": len(orphans),
        "orphan_preview": orphans[:5],
    }


def move_orphans_to_inbox(models_dir: str) -> dict:
    """
    Mueve archivos de modelo huérfanos (fuera del árbol canónico) a _inbox/.
    Por cada modelo mueve también sus archivos sidecar relacionados (mismo stem).

    Returns:
      {"moved": int, "skipped": int, "errors": list[str]}
    """
    import shutil
    from modules.storage_manager import DEFAULT_MODEL_SUBDIRS

    inbox_dir = os.path.join(models_dir, "_inbox")
    os.makedirs(inbox_dir, exist_ok=True)

    canonical_set = set(DEFAULT_MODEL_SUBDIRS)
    stats = {"moved": 0, "skipped": 0, "errors": []}

    # Recopilar (model_path, stem, parent_dir) de huérfanos
    to_move = []  # list of (src_path, stem, src_dir)

    try:
        for entry in os.scandir(models_dir):
            if entry.is_file():
                if os.path.splitext(entry.name)[1].lower() in MODEL_EXTENSIONS:
                    stem = os.path.splitext(entry.name)[0]
                    to_move.append((entry.path, stem, models_dir))
            elif entry.is_dir() and not entry.is_symlink():
                if entry.name not in canonical_set:
                    for root, _, files in os.walk(entry.path):
                        for f in files:
                            if os.path.splitext(f)[1].lower() in MODEL_EXTENSIONS:
                                stem = os.path.splitext(f)[0]
                                to_move.append((os.path.join(root, f), stem, root))
    except OSError as e:
        stats["errors"].append(f"Error escaneando {models_dir}: {e}")
        return stats

    for model_path, stem, src_dir in to_move:
        if not os.path.exists(model_path):
            stats["skipped"] += 1
            continue

        dest_path = os.path.join(inbox_dir, os.path.basename(model_path))
        # Evitar sobreescribir si ya existe en inbox
        if os.path.exists(dest_path):
            stats["skipped"] += 1
            continue

        try:
            shutil.move(model_path, dest_path)
            stats["moved"] += 1
            log.info("move_orphans: %s → _inbox/", os.path.basename(model_path))
        except Exception as e:
            stats["errors"].append(f"{os.path.basename(model_path)}: {e}")
            continue

        # Mover sidecars con el mismo stem (json, preview, etc.)
        for fname in os.listdir(src_dir):
            if fname == os.path.basename(model_path):
                continue
            fname_lower = fname.lower()
            # Coincide si empieza con el stem y tiene extensión sidecar
            if fname_lower.startswith(stem.lower()):
                suffix = fname_lower[len(stem):]
                if suffix in SIDECAR_EXTENSIONS or any(
                    suffix.endswith(ext) for ext in SIDECAR_EXTENSIONS
                ):
                    sidecar_src = os.path.join(src_dir, fname)
                    sidecar_dest = os.path.join(inbox_dir, fname)
                    if os.path.exists(sidecar_src) and not os.path.exists(sidecar_dest):
                        try:
                            shutil.move(sidecar_src, sidecar_dest)
                            log.info("move_orphans sidecar: %s → _inbox/", fname)
                        except Exception:
                            pass  # sidecars no son críticos

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    models_dir = sys.argv[1] if len(sys.argv) > 1 else "/run/media/system/Kilaya/Models"

    print(f"\n=== Model Summary: {models_dir} ===")
    for row in get_model_summary(models_dir):
        print(f"  {row['category']:20s}  {row['files']:5d} archivos  {row['size_gb']:7.1f} GB")

    print("\n=== Krita Priority (dry-run) ===")
    stats = sync_krita_priority(models_dir, dry_run=True)
    print(f"  Symlinks a crear : {stats['created']}")
    print(f"  Ya existentes    : {stats['skipped']}")
    if stats["errors"]:
        for e in stats["errors"]:
            print(f"  ERROR: {e}")
