"""Move a model and its known sidecars without replacing existing files."""
import os
from pathlib import Path, PureWindowsPath
from .scanner import MODEL_EXTENSIONS


SIDECAR_SUFFIXES = (
    ".cm-info.json", ".metadata.json", ".civitai.info", ".json", ".txt",
    ".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp",
)


def _contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Ruta fuera del directorio de modelos: {path}")
    return resolved


def relative_folder(file_path: str, models_root: str) -> tuple[str, str]:
    """Physical folder and top-level storage category, relative to models_root."""
    root = Path(models_root).resolve()
    parent = _contained(Path(file_path).parent, root)
    parts = parent.relative_to(root).parts
    return "/".join(parts), parts[0] if parts else ""


def move_model_files(file_path: str, models_root: str, target_folder: str,
                     update_path=None) -> tuple[str, bool]:
    """Validate everything first; roll files back if any rename or DB update fails.

    The model root is a single local filesystem. os.rename on Windows rejects an
    existing destination; shutil.move's copy fallback would silently replace it.
    """
    if not models_root or not Path(models_root).is_dir():
        raise ValueError("Directorio de modelos no configurado o inexistente")
    root = Path(models_root).resolve()
    folder = str(target_folder).replace("\\", "/")
    windows = PureWindowsPath(folder)
    if (windows.drive or windows.root
            or any(part in (".", "..") for part in folder.split("/"))):
        raise ValueError("La carpeta destino debe ser relativa al directorio de modelos")
    source = Path(os.path.abspath(file_path))
    _contained(source, root)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Archivo de modelo no disponible: {source}")
    destination_dir = _contained(root / folder, root)
    destination = destination_dir / source.name
    if os.path.normcase(str(source)) == os.path.normcase(str(destination)):
        return str(destination), False

    files = [(source, destination)]
    stem = str(source.with_suffix(""))
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(stem + suffix)
        if os.path.lexists(sidecar):
            _contained(sidecar, root)
            if sidecar.is_symlink() or not sidecar.is_file():
                raise ValueError(f"Archivo compañero no válido: {sidecar}")
            files.append((sidecar, destination_dir / sidecar.name))
    if len(files) > 1:
        for sibling in source.parent.iterdir():
            if (sibling.stem.casefold() == source.stem.casefold()
                    and sibling.suffix.lower() in MODEL_EXTENSIONS | {".sft"}
                    and os.path.normcase(str(sibling)) != os.path.normcase(str(source))
                    and sibling.is_file()):
                raise ValueError(f"Archivos compañeros ambiguos: {source.name} y {sibling.name} comparten nombre")
    for _, target in files:
        if os.path.lexists(target):
            raise FileExistsError(f"Ya existe: {target}")

    destination_dir.mkdir(parents=True, exist_ok=True)
    completed = []
    try:
        for original, target in files:
            os.rename(original, target)
            completed.append((original, target))
        if update_path:
            update_path(str(destination))
    except Exception as exc:
        rollback_errors = []
        for original, target in reversed(completed):
            try:
                os.rename(target, original)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise OSError(f"{exc}; no se pudo revertir: {'; '.join(rollback_errors)}") from exc
        raise
    return str(destination), True
