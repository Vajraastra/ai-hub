"""
model_placer.py — Orquesta el flujo Inbox del Model Vault.

Flujo completo:
  1. scan_inbox()          → lista archivos en la carpeta _inbox/
  2. classify_inbox_file() → tipo por header + enriquece con Civitai
  3. suggest_subfolder()   → sugiere subcarpeta basada en tags vs carpetas existentes
  4. place_model()         → mueve el archivo a la ruta final + registra en DB
"""

import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft", ".bin"}

INBOX_DIRNAME = "_inbox"


# ─────────────────────────────────────────────────────────────────────────────
# Estructura de un ítem en el Inbox
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InboxItem:
    file_path:          str
    filename:           str
    size_mb:            float
    model_type:         str           # "lora", "checkpoint", "vae", …
    arch:               str           # "Flux.1", "SDXL", …
    confidence:         float         # confianza de la clasificación
    classify_source:    str           # "header" | "civitai" | "extension"
    suggested_category: str           # carpeta canónica: "loras", "checkpoints", …
    suggested_subfolder: str          # subcarpeta dentro de la categoría
    civitai_name:       str = ""
    civitai_tags:       list[str] = field(default_factory=list)
    civitai_id:         Optional[int] = None
    civitai_version_id: Optional[int] = None
    status:             str = "pending"   # "pending" | "placed" | "ignored"
    error:              str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────name────────

def get_inbox_dir(models_dir: str) -> str:
    return os.path.join(models_dir, INBOX_DIRNAME)


def ensure_inbox_dir(models_dir: str) -> str:
    path = get_inbox_dir(models_dir)
    os.makedirs(path, exist_ok=True)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 1. Escanear inbox
# ─────────────────────────────────────────────────────────────────────────────

def scan_inbox(models_dir: str) -> list[str]:
    """
    Retorna lista de paths de archivos de modelo dentro de _inbox/.
    Solo archivos, no subdirectorios.
    """
    inbox_dir = get_inbox_dir(models_dir)
    if not os.path.isdir(inbox_dir):
        return []

    files = []
    for entry in os.scandir(inbox_dir):
        if entry.is_file() and Path(entry.name).suffix.lower() in MODEL_EXTENSIONS:
            files.append(entry.path)

    return sorted(files)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Clasificar un archivo del inbox
# ─────────────────────────────────────────────────────────────────────────────

def classify_inbox_file(file_path: str,
                        civitai_client=None,
                        hasher=None) -> InboxItem:
    """
    Clasifica un archivo del inbox:
      - Detecta tipo por header safetensors
      - Si civitai_client está disponible, consulta por hash SHA256
      - Retorna InboxItem con toda la info disponible
    """
    from core.header_classifier import classify, classify_with_civitai

    p = Path(file_path)
    size_mb = p.stat().st_size / (1024 * 1024)

    # Clasificación por header
    classification = classify(file_path)

    civitai_name = ""
    civitai_tags = []
    civitai_id = None
    civitai_version_id = None
    civitai_data = None

    # Enriquecimiento con Civitai (si hay cliente y hasher)
    if civitai_client and hasher:
        try:
            file_hash = hasher(file_path)
            if file_hash:
                civitai_data = civitai_client.get_model_version_by_hash(file_hash)
                if civitai_data:
                    classification = classify_with_civitai(file_path, civitai_data)
                    model_info = civitai_data.get("model", {})
                    civitai_name = model_info.get("name", "")
                    civitai_tags = model_info.get("tags", [])
                    civitai_id = civitai_data.get("modelId")
                    civitai_version_id = civitai_data.get("id")
        except Exception:
            pass  # Civitai no disponible — solo clasificación por header

    suggested_subfolder = suggest_subfolder(
        classification.suggested_category,
        civitai_tags,
        p.stem,
    )

    return InboxItem(
        file_path=file_path,
        filename=p.name,
        size_mb=round(size_mb, 1),
        model_type=classification.model_type,
        arch=classification.arch,
        confidence=classification.confidence,
        classify_source=classification.source,
        suggested_category=classification.suggested_category,
        suggested_subfolder=suggested_subfolder,
        civitai_name=civitai_name,
        civitai_tags=civitai_tags,
        civitai_id=civitai_id,
        civitai_version_id=civitai_version_id,
        error=classification.error,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Sugerir subcarpeta
# ─────────────────────────────────────────────────────────────────────────────

def suggest_subfolder(category: str, civitai_tags: list[str],
                      filename_stem: str = "",
                      models_dir: str = "") -> str:
    """
    Sugiere una subcarpeta dentro de la categoría canónica comparando:
      1. Tags de Civitai contra nombres de subcarpetas existentes
      2. Patrones conocidos en el nombre de archivo

    Retorna el nombre de la subcarpeta sugerida, o "" si va a la raíz.
    """
    # Palabras clave → subcarpeta sugerida (para loras en particular)
    KNOWN_TAGS = {
        "flux":        "flux",
        "flux.1":      "flux",
        "sdxl":        "illustrious",
        "illustrious": "illustrious",
        "pony":        "illustrious",
        "noobai":      "illustrious",
        "anime":       "illustrious",
        "sd 1.5":      "concept",
        "sd1.5":       "concept",
        "concept":     "concept",
        "clothing":    "clothes",
        "clothes":     "clothes",
        "outfit":      "clothes",
        "style":       "styles",
        "character":   "concept",
        "wan":         "wan",
        "qwen":        "qwen",
        "recipe":      "recipes",
        "utility":     "utils",
        "tool":        "utils",
    }

    tags_lower = [t.lower() for t in civitai_tags]
    name_lower = filename_stem.lower()

    # Buscar en tags de Civitai primero
    for tag in tags_lower:
        if tag in KNOWN_TAGS:
            return KNOWN_TAGS[tag]

    # Buscar en nombre del archivo
    for keyword, subfolder in KNOWN_TAGS.items():
        if keyword in name_lower:
            return subfolder

    # Comparar contra subcarpetas reales existentes (si models_dir está disponible)
    if models_dir:
        cat_dir = os.path.join(models_dir, category)
        if os.path.isdir(cat_dir):
            existing = [
                d.name.lower() for d in os.scandir(cat_dir)
                if d.is_dir() and d.name != "krita"
            ]
            for tag in tags_lower:
                if tag in existing:
                    return tag
            for subdir in existing:
                if subdir in name_lower:
                    return subdir

    return ""  # va a la raíz de la categoría


# ─────────────────────────────────────────────────────────────────────────────
# 4. Colocar el modelo
# ─────────────────────────────────────────────────────────────────────────────

def place_model(item: InboxItem, models_dir: str,
                category_override: str = "",
                subfolder_override: str = "") -> dict:
    """
    Mueve el archivo desde _inbox/ a su destino final.
    También mueve los sidecars (.cm-info.json, .metadata.json, .preview.jpeg)
    si existen junto al archivo.

    Args:
        item:               InboxItem con la clasificación
        models_dir:         Ruta base de modelos
        category_override:  Sobreescribe la categoría sugerida
        subfolder_override: Sobreescribe la subcarpeta sugerida

    Returns:
        {"ok": bool, "dest": str, "error": str}
    """
    category  = category_override  or item.suggested_category
    subfolder = subfolder_override if subfolder_override is not None else item.suggested_subfolder

    dest_dir = os.path.join(models_dir, category)
    if subfolder:
        dest_dir = os.path.join(dest_dir, subfolder)

    os.makedirs(dest_dir, exist_ok=True)

    src = Path(item.file_path)
    dest_file = os.path.join(dest_dir, src.name)

    if os.path.exists(dest_file):
        return {"ok": False, "dest": dest_file,
                "error": f"Ya existe: {dest_file}"}

    try:
        shutil.move(str(src), dest_file)

        # Mover sidecars si existen junto al archivo original
        base = src.with_suffix("")
        for sidecar_ext in (".cm-info.json", ".metadata.json", ".preview.jpeg"):
            sidecar_src = Path(str(base) + sidecar_ext)
            if sidecar_src.exists():
                sidecar_dest = os.path.join(dest_dir, sidecar_src.name)
                if not os.path.exists(sidecar_dest):
                    shutil.move(str(sidecar_src), sidecar_dest)

        return {"ok": True, "dest": dest_file, "error": ""}

    except Exception as e:
        return {"ok": False, "dest": "", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de UI
# ─────────────────────────────────────────────────────────────────────────────

def build_dest_display(models_dir: str, category: str, subfolder: str) -> str:
    """Retorna la ruta de destino como string legible para mostrar en UI."""
    parts = [os.path.basename(models_dir), category]
    if subfolder:
        parts.append(subfolder)
    return " / ".join(parts)


def get_existing_subfolders(models_dir: str, category: str) -> list[str]:
    """Lista subcarpetas existentes en una categoría canónica."""
    cat_dir = os.path.join(models_dir, category)
    if not os.path.isdir(cat_dir):
        return []
    return sorted(
        d.name for d in os.scandir(cat_dir)
        if d.is_dir() and d.name != "krita"
    )
