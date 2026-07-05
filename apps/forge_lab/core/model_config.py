"""
Selección de ficheros de modelo por arquitectura (paths ya no hardcodeados).

Los defaults viven en el adaptador (ArchAdapter.model_files()); aquí se
persisten los overrides que el usuario elige en la UI (data/model_config.json,
NO versionado: es preferencia local, como hub_config) y se resuelven los
nombres que esperan los loaders de ComfyUI.

Contrato de paths: siempre relativos al almacén global de modelos
(hub_config paths.models), en posix: "clip/qwen_3_4b.safetensors".
Cada clave tiene su(s) carpeta(s) raíz válida(s) — son las que listan los
loaders de ComfyUI; un fichero fuera de ellas no sería seleccionable allí.
"""
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_PATH = DATA_DIR / "model_config.json"

# clave → carpetas raíz admitidas dentro del almacén
FILE_ROOTS = {
    "diffusion_model": ("diffusion_models",),
    "text_encoder": ("clip", "text_encoders"),
    "vae": ("vae",),
}


class ModelConfigError(Exception):
    pass


def _load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def model_files(arch: str) -> dict:
    """Paths efectivos (relativos al almacén global): defaults del adaptador
    pisados por los overrides guardados."""
    from .architectures import get_adapter
    files = dict(vars(get_adapter(arch).model_files()))
    saved = _load().get(arch, {})
    for k in FILE_ROOTS:
        if saved.get(k):
            files[k] = saved[k]
    return files


def set_model_files(arch: str, files: dict, models_root: Path) -> dict:
    """Persiste overrides. Valor vacío = volver al default del adaptador.
    Valida clave, carpeta raíz y existencia del fichero."""
    from .architectures import get_adapter
    get_adapter(arch)                       # valida la arquitectura
    bad = set(files) - set(FILE_ROOTS)
    if bad:
        raise ModelConfigError(f"claves desconocidas: {sorted(bad)}")
    for k, rel in files.items():
        if not rel:
            continue
        rel = rel.replace("\\", "/")
        top = rel.split("/", 1)[0]
        if top not in FILE_ROOTS[k]:
            raise ModelConfigError(
                f"{k}: debe vivir bajo {' o '.join(FILE_ROOTS[k])}/ (no {top!r})")
        if not (Path(models_root) / rel).is_file():
            raise ModelConfigError(f"{k}: no existe {rel!r} en el almacén")
    cfg = _load()
    entry = cfg.setdefault(arch, {})
    for k, rel in files.items():
        if rel:
            entry[k] = rel.replace("\\", "/")
        else:
            entry.pop(k, None)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return model_files(arch)


def list_options(models_root: Path) -> dict:
    """Ficheros elegibles por clave, escaneando el almacén global.
    Los derivados de forge_lab no aparecen: son checkpoints del registro,
    no bases seleccionables como fichero suelto."""
    from .merge import DERIVED_SUBDIR
    root = Path(models_root)
    out: dict[str, list] = {}
    for key, tops in FILE_ROOTS.items():
        opts = []
        for top in tops:
            base = root / top
            if not base.exists():
                continue
            for p in sorted(base.rglob("*.safetensors")):
                rel = p.relative_to(root).as_posix()
                if key == "diffusion_model" and rel.startswith(
                        f"{top}/{DERIVED_SUBDIR}/"):
                    continue
                opts.append({"path": rel,
                             "size_bytes": p.stat().st_size})
        out[key] = opts
    return out


def loader_name(rel: str) -> str:
    """'clip/sub/x.safetensors' → 'sub\\x.safetensors': el nombre que lista
    el loader de ComfyUI (relativo a su carpeta de tipo, con os.sep)."""
    parts = Path(rel.replace("\\", "/")).parts
    return os.sep.join(parts[1:]) if len(parts) > 1 else rel
