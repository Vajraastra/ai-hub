"""
Orquestación de merges LoRA → checkpoint derivado + registro de linaje.

Reglas duras (handoff §4):
  - Merge SIEMPRE sobre De-Turbo (o un derivado suyo), nunca el Turbo destilado.
  - Los merges corren en CPU/RAM (64 GB), no en GPU → proceso aparte con el
    venv de ComfyUI (torch), forzado a CPU. ComfyUI NO necesita estar arrancado.
  - Las zonas de adapter.forbidden_zones() no se tocan sin override explícito.

El cálculo vive en merge_worker.py (mismo directorio); aquí va el catálogo de
LoRAs, el lanzamiento/seguimiento del worker y el registro de checkpoints
derivados con su linaje:

  data/checkpoints/<name>.json                       (se versiona en git)
  <models>/diffusion_models/forge_lab/<name>.safetensors   (el peso, ~12 GB)

Los nodos comfyUI-Realtime-Lora (carga selectiva en runtime) quedan para la
exploración de bloques de la Fase 4; la derivación a fichero es de aquí.
"""
import asyncio
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

_CORE = Path(__file__).parent
_FORGE_ROOT = _CORE.parent                    # apps/forge_lab
_REPO_ROOT = _FORGE_ROOT.parent.parent
CHECKPOINTS_DIR = _FORGE_ROOT / "data" / "checkpoints"

# Python del venv de ComfyUI (torch + safetensors); ver handoff "Venvs".
WORKER_PYTHON = _REPO_ROOT / "apps" / "comfyui" / "venv" / "Scripts" / "python.exe"

# Subcarpeta (dentro del weights_root de cada arquitectura) de los derivados
DERIVED_SUBDIR = "forge_lab"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class MergeError(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_st_header(path: Path) -> tuple[dict, dict]:
    """(tensores, metadata) del header de un .safetensors — solo el header,
    nunca los pesos; sirve para catalogar sin coste."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 * 1024 * 1024:
            raise MergeError(f"header sospechoso ({n} bytes) en {path.name}")
        hdr = json.loads(f.read(n))
    meta = hdr.pop("__metadata__", {}) or {}
    return hdr, meta


def _find_preview(p: Path) -> Path | None:
    """Imagen de preview junto al safetensors (misma convención del Vault)."""
    base = str(p)[: -len(p.suffix)] if p.suffix else str(p)
    for ext in (".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp"):
        c = Path(base + ext)
        if c.is_file():
            return c
    return None


def _sha256(path: Path, on_progress: Callable[[int, int], None] | None = None) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with open(path, "rb") as f:
        while chunk := f.read(16 * 1024 * 1024):
            h.update(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    return h.hexdigest()


class MergeOrchestrator:
    def __init__(self, models_root: Path):
        self.models_root = Path(models_root)
        # cache del catálogo: leer 1400+ headers cuesta ~5 s; con mtime+size
        # como llave solo se paga el primer escaneo y los ficheros nuevos
        self._lora_cache: dict[str, tuple[tuple, dict]] = {}
        # cache de la arquitectura real de cada checkpoint (para filtrar el
        # scan multi_base: un fichero z-image en checkpoints/ no es base sdxl)
        self._ckpt_arch_cache: dict[str, tuple[tuple, str]] = {}

    # ── Catálogo de LoRAs ─────────────────────────────────────────────────

    @staticmethod
    def _header_matches_arch(hdr: dict, arch: str) -> bool:
        """Detección de arquitectura por las claves/shapes del header.
        zimage: claves diffusers del S3-DiT (lora_A de entrada 3840/256).
        sdxl: formato kohya del UNet LDM con cross-attention de 2048
        (attn2.to_k entra desde el pooled CLIP-L+G; en SD15 sería 768)."""
        if arch == "zimage":
            for k, v in hdr.items():
                if k.startswith("diffusion_model.layers.") and ".lora_A." in k:
                    return v.get("shape", [None, None])[1] in (3840, 256)
        elif arch == "sdxl":
            for k, v in hdr.items():
                if (k.startswith("lora_unet_") and "attn2_to_k" in k
                        and k.endswith(".lora_down.weight")):
                    return v.get("shape", [None, None])[1] == 2048
        return False

    @staticmethod
    def _checkpoint_header_arch(hdr: dict) -> str:
        """Arquitectura de un checkpoint COMPLETO por sus claves de tensor.
        SDXL: UNet LDM (`model.diffusion_model.input_blocks.*`). Z-Image:
        DiT (`...layers.N` o claves desnudas del S3-DiT). '' si no se
        reconoce (no se ofrece como base de ninguna arquitectura)."""
        keys = hdr.keys()
        for k in keys:
            if k.startswith("model.diffusion_model.input_blocks."):
                return "sdxl"
        for k in keys:
            if (k.startswith("model.diffusion_model.layers.")
                    or k.startswith("layers.")):
                return "zimage"
        return ""

    def _checkpoint_arch(self, path: Path) -> str:
        """Arquitectura detectada de un checkpoint, cacheada por mtime+size."""
        try:
            st = path.stat()
        except OSError:
            return ""
        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._ckpt_arch_cache.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            hdr, _ = _read_st_header(path)
            arch = self._checkpoint_header_arch(hdr)
        except Exception:
            arch = ""
        self._ckpt_arch_cache[str(path)] = (stamp, arch)
        return arch

    def list_loras(self, arch: str = "zimage") -> list[dict]:
        """LoRAs del almacén con detección de arquitectura por header
        (metadata de entrenamiento o claves/shapes, ver _header_matches_arch)."""
        base = self.models_root / "loras"
        out = []
        if not base.exists():
            return out
        from .lora_alias import ALIAS_SUBDIR
        for p in sorted(base.rglob("*.safetensors")):
            if p.relative_to(base).parts[0] == ALIAS_SUBDIR:
                continue    # copias de conversión diffusers→kohya (cache)
            st = p.stat()
            # arch va en la llave: la entrada cacheada depende de la
            # arquitectura consultada (arch_match, base_model…)
            cache_key, stamp = f"{arch}::{p}", (st.st_mtime_ns, st.st_size)
            cached = self._lora_cache.get(cache_key)
            if cached and cached[0] == stamp:
                out.append(cached[1])
                continue
            try:
                hdr, meta = _read_st_header(p)
            except Exception:
                continue    # fichero raro: no es motivo para tirar el catálogo
            base_model = (meta.get("ss_base_model_version")
                          or meta.get("ss_base_model") or "")
            is_arch = (arch.lower() in base_model.lower()
                       or self._header_matches_arch(hdr, arch))
            rank = max((v["shape"][0] for k, v in hdr.items()
                        if k.endswith(("lora_A.weight", "lora_down.weight"))),
                       default=None)
            entry = {
                "file": p.relative_to(base).as_posix(),
                "name": p.stem,
                "subfolder": ("" if p.parent == base
                              else p.parent.relative_to(base).as_posix()),
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,          # orden "recientes" del picker
                "arch_match": is_arch,
                "base_model": base_model or None,
                "rank": rank,
                "n_tensors": len(hdr),
                "hash": meta.get("sshs_model_hash"),
            }
            self._lora_cache[cache_key] = (stamp, entry)
            out.append(entry)
        # has_preview fuera del cache: el .preview.* puede aparecer/borrarse
        # sin que cambie el mtime del safetensors (llave del cache)
        out = [{**e, "has_preview":
                _find_preview(self.models_root / "loras" / e["file"]) is not None}
               for e in out]
        out.sort(key=lambda e: (not e["arch_match"], e["name"].lower()))
        return out

    def lora_preview_path(self, lora_file: str) -> Path:
        """Path del preview de un LoRA (rel posix a <models>/loras) o error."""
        base = (self.models_root / "loras").resolve()
        p = (base / lora_file).resolve()
        if not p.is_relative_to(base) or not p.is_file():
            raise MergeError(f"no existe el LoRA {lora_file!r}")
        prev = _find_preview(p)
        if prev is None:
            raise MergeError(f"{lora_file!r} no tiene preview")
        return prev

    # ── Registro de checkpoints ───────────────────────────────────────────

    def derived_dir(self, arch: str) -> Path:
        from .architectures import get_adapter
        return self.models_root / get_adapter(arch).weights_root / DERIVED_SUBDIR

    def _official_entries(self, arch: str) -> list[dict]:
        """Bases oficiales elegibles. Layout por piezas (zimage): solo la
        configurada en model_files. multi_base (sdxl): todo checkpoint del
        weights_root fuera de forge_lab/ es base válida."""
        from .architectures import get_adapter
        from .model_config import model_files, loader_name
        adapter = get_adapter(arch)
        root = adapter.weights_root

        def entry(rel: str, label: str) -> dict:
            path = self.models_root / rel
            return {
                "kind": "official",
                "name": Path(rel).name.removesuffix(".safetensors"),
                "arch": arch, "file": Path(rel).relative_to(root).as_posix(),
                "unet_name": loader_name(rel),  # subpath con os.sep si anidado
                "present": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "has_preview": _find_preview(path) is not None,
                "label": label,
            }

        if not adapter.multi_base:
            rel = model_files(arch)["diffusion_model"]
            return [entry(rel, "base oficial (De-Turbo)")]
        base = self.models_root / root
        out = []
        if base.exists():
            for p in sorted(base.rglob("*.safetensors")):
                rel = p.relative_to(self.models_root).as_posix()
                if rel.startswith(f"{root}/{DERIVED_SUBDIR}/"):
                    continue
                # filtro por arquitectura real: otros modelos que compartan la
                # carpeta checkpoints/ (p.ej. z-image) no son base sdxl
                if self._checkpoint_arch(p) != arch:
                    continue
                out.append(entry(rel, "checkpoint del almacén"))
        return out

    def list_checkpoints(self, arch: str = "zimage") -> list[dict]:
        """Bases oficiales + derivados (linaje completo), oficiales primero."""
        from .architectures import get_adapter
        weights_root = get_adapter(arch).weights_root
        out = self._official_entries(arch)
        if CHECKPOINTS_DIR.exists():
            for f in sorted(CHECKPOINTS_DIR.glob("*.json")):
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("arch") != arch:
                    continue
                d["kind"] = "derived"
                # unet_name = ruta que ComfyUI lista en Windows (os.sep)
                d["unet_name"] = d["file"].replace("/", os.sep)
                wpath = self.models_root / weights_root / d["file"]
                d["present"] = wpath.exists()
                d["has_preview"] = _find_preview(wpath) is not None
                out.append(d)
        out.sort(key=lambda e: (e["kind"] != "official",
                                e.get("created_at") or "", e["name"]))
        return out

    def get_checkpoint(self, name: str, arch: str = "zimage") -> dict:
        for c in self.list_checkpoints(arch):
            if c["name"] == name:
                return c
        raise MergeError(f"no existe el checkpoint {name!r}")

    def checkpoint_preview_path(self, name: str, arch: str = "zimage") -> Path:
        """Path del preview .preview.* de un checkpoint (oficial o derivado)."""
        from .architectures import get_adapter
        c = self.get_checkpoint(name, arch)
        weights_root = get_adapter(arch).weights_root
        weight = self.models_root / weights_root / c["file"]
        prev = _find_preview(weight)
        if prev is None:
            raise MergeError(f"el checkpoint {name!r} no tiene preview")
        return prev

    def delete_checkpoint(self, name: str):
        """Borra un derivado (peso + registro). El linaje de otros derivados
        que lo referencien queda como histórico (texto), igual que los runs."""
        from .architectures import get_adapter
        reg = CHECKPOINTS_DIR / f"{name}.json"
        if not reg.exists():
            raise MergeError(f"no existe el checkpoint derivado {name!r}")
        d = json.loads(reg.read_text(encoding="utf-8"))
        weights_root = get_adapter(d["arch"]).weights_root
        weight = self.models_root / weights_root / d["file"]
        if weight.exists():
            weight.unlink()
        reg.unlink()

    # ── Merge ─────────────────────────────────────────────────────────────

    async def merge_lora(self, arch: str, base: str, lora_file: str,
                         strength: float, name: str, label: str = "",
                         blocks: list[str] | dict[str, float] | None = None,
                         allow_forbidden: bool = False,
                         on_progress: Callable[[dict], None] | None = None) -> dict:
        """Merge completo (blocks=None, Fase 3) o por bloques (Fase 4);
        dict = dosis por bloque (escala final del delta = strength × dosis).

        base: nombre de un checkpoint del registro (oficial o derivado) —
        el linaje encadena derivados para la curva de degradación (Fase 6).
        Devuelve la entrada de registro del checkpoint nuevo."""
        if not _SLUG_RE.match(name):
            raise MergeError(f"nombre inválido {name!r} "
                             "(minúsculas/dígitos/guiones, sin espacios)")
        if (CHECKPOINTS_DIR / f"{name}.json").exists():
            raise MergeError(f"ya existe un checkpoint derivado llamado {name!r}")
        if not WORKER_PYTHON.exists():
            raise MergeError(f"no existe el Python del worker: {WORKER_PYTHON}")
        if not 0.0 < strength <= 2.0:
            raise MergeError(f"strength {strength} fuera de rango razonable (0–2]")

        from .architectures import get_adapter
        weights_root = get_adapter(arch).weights_root
        base_entry = self.get_checkpoint(base, arch)
        if not base_entry["present"]:
            raise MergeError(f"el checkpoint base {base!r} no está en disco")
        base_path = self.models_root / weights_root / base_entry["file"]

        lora_path = self.models_root / "loras" / lora_file
        if not lora_path.is_file():
            raise MergeError(f"no existe el LoRA {lora_file!r}")
        lora_hdr, lora_meta = _read_st_header(lora_path)

        # naming diffusers → el worker consume la copia kohya-canónica (la
        # misma que usaría el preview); el registro conserva el original
        lora_alias_rel = None
        if arch == "sdxl":
            from .lora_alias import LoraAliasError, ensure_alias, needs_alias
            if needs_alias(lora_hdr):
                try:
                    lora_alias_rel = await asyncio.to_thread(
                        ensure_alias, self.models_root, lora_file)
                except LoraAliasError as e:
                    raise MergeError(
                        f"LoRA con naming diffusers no convertible: {e}")
                lora_path = self.models_root / "loras" / lora_alias_rel

        out_rel = f"{DERIVED_SUBDIR}/{name}.safetensors"
        out_path = self.models_root / weights_root / out_rel
        if out_path.exists():
            raise MergeError(f"ya existe el fichero {out_rel!r}")

        job = {
            "arch": arch, "base_path": str(base_path),
            "lora_path": str(lora_path), "out_path": str(out_path),
            "strength": strength, "blocks": blocks,
            "allow_forbidden": allow_forbidden,
            "metadata": {
                "forge_lab.name": name,
                "forge_lab.arch": arch,
                "forge_lab.base": base_entry["name"],
                "forge_lab.lora": lora_file,
                "forge_lab.lora_alias": lora_alias_rel or "",
                "forge_lab.lora_hash": lora_meta.get("sshs_model_hash") or "",
                "forge_lab.strength": strength,
                "forge_lab.blocks": (
                    "full" if blocks is None
                    else ",".join(f"{b}:{d}" for b, d in blocks.items())
                    if isinstance(blocks, dict) else ",".join(blocks)),
                "forge_lab.created_at": _now(),
            },
        }
        t0 = time.time()
        result = await self._run_worker(job, on_progress)

        if on_progress:
            on_progress({"phase": "hash", "done": 0, "total": 1})
        sha = await asyncio.to_thread(_sha256, out_path)
        if on_progress:
            on_progress({"phase": "hash", "done": 1, "total": 1})

        entry = {
            "name": name, "arch": arch, "file": out_rel, "label": label,
            "base": {"name": base_entry["name"], "kind": base_entry["kind"],
                     "file": base_entry["file"]},
            "lora": {"file": lora_file,
                     "hash": lora_meta.get("sshs_model_hash"),
                     "strength": strength},
            "blocks": blocks,
            "created_at": _now(),
            "seconds": round(time.time() - t0, 1),
            "sha256": sha,
            "size_bytes": out_path.stat().st_size,
            "worker": {k: result[k] for k in
                       ("merged_tensors", "lora_pairs", "skipped_blocks",
                        "skipped_policy") if k in result},
        }
        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
        (CHECKPOINTS_DIR / f"{name}.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return entry

    async def _run_worker(self, job: dict,
                          on_progress: Callable[[dict], None] | None) -> dict:
        """Lanza merge_worker.py con el venv de ComfyUI (CPU forzada) y
        traduce su protocolo de líneas JSON. Limpia el parcial si falla."""
        out_path = Path(job["out_path"])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as tf:
            json.dump(job, tf)
            job_file = Path(tf.name)
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}   # regla: merge en CPU
        try:
            # stderr fusionado en stdout: si solo se lee un pipe y el otro se
            # llena (warnings de torch), el worker se bloquearía.
            proc = await asyncio.create_subprocess_exec(
                str(WORKER_PYTHON), str(_CORE / "merge_worker.py"), str(job_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env=env)
            result, error, noise = None, None, []
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:                # warnings de libs, etc.
                    noise = (noise + [line])[-40:]
                    continue
                if "error" in msg:
                    error = msg["error"]
                elif msg.get("ok"):
                    result = msg
                elif on_progress:
                    on_progress(msg)
            rc = await proc.wait()
            if error or rc != 0 or result is None:
                raise MergeError(error or f"worker terminó con código {rc}: "
                                 + " | ".join(noise[-8:]))
            return result
        except BaseException:
            out_path.unlink(missing_ok=True)      # nunca dejar un peso a medias
            raise
        finally:
            job_file.unlink(missing_ok=True)


# alias histórico del stub de Fase 0 (por si algo lo importaba)
MergeOrchestratorError = MergeError
