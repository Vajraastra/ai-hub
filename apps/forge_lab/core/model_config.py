"""
Selección de ficheros de modelo por arquitectura (paths ya no hardcodeados).

Los defaults viven en el adaptador (ArchAdapter.model_files()); aquí se
persisten los overrides que el usuario elige en la UI (data/model_config.json,
NO versionado: es preferencia local, como hub_config) y se resuelven los
nombres que esperan los loaders de ComfyUI.

Contrato de paths: siempre relativos al almacén global de modelos
(hub_config paths.models), en posix: "clip/qwen_3_4b.safetensors".
Las claves de fichero y su(s) carpeta(s) raíz válida(s) las define cada
adaptador (file_keys()) — son las que listan los loaders de ComfyUI; un
fichero fuera de ellas no sería seleccionable allí. zimage va por piezas
(diffusion_model + text_encoder + vae); sdxl es un único checkpoint completo.
"""
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_PATH = DATA_DIR / "model_config.json"


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
    adapter = get_adapter(arch)
    files = dict(adapter.model_files())
    saved = _load().get(arch, {})
    for k in adapter.file_keys():
        if saved.get(k):
            files[k] = saved[k]
    return files


def set_model_files(arch: str, files: dict, models_root: Path) -> dict:
    """Persiste overrides. Valor vacío = volver al default del adaptador.
    Valida clave, carpeta raíz y existencia del fichero."""
    from .architectures import get_adapter
    roots = get_adapter(arch).file_keys()
    bad = set(files) - set(roots)
    if bad:
        raise ModelConfigError(f"claves desconocidas: {sorted(bad)}")
    for k, rel in files.items():
        if not rel:
            continue
        rel = rel.replace("\\", "/")
        top = rel.split("/", 1)[0]
        if top not in roots[k]:
            raise ModelConfigError(
                f"{k}: debe vivir bajo {' o '.join(roots[k])}/ (no {top!r})")
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


def list_options(models_root: Path, arch: str) -> dict:
    """Ficheros elegibles por clave, escaneando el almacén global.
    Los derivados de forge_lab no aparecen: son checkpoints del registro,
    no bases seleccionables como fichero suelto."""
    from .merge import DERIVED_SUBDIR
    from .architectures import get_adapter
    root = Path(models_root)
    out: dict[str, list] = {}
    for key, tops in get_adapter(arch).file_keys().items():
        opts = []
        for top in tops:
            base = root / top
            if not base.exists():
                continue
            for p in sorted(base.rglob("*.safetensors")):
                rel = p.relative_to(root).as_posix()
                if rel.startswith(f"{top}/{DERIVED_SUBDIR}/"):
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
