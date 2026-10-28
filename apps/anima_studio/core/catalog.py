"""Read-only Anima catalogue from the Hub's configured model library.

Only safetensors headers are read. Unknown architectures are deliberately not
offered, even when a filename or directory happens to contain "anima".
"""
import json
import math
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock


class Catalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._cache = {}
        self._lock = RLock()

    def _header(self, path):
        stat = path.stat()
        with path.open("rb") as stream:
            prefix = stream.read(8)
            if len(prefix) != 8:
                raise ValueError("Cabecera safetensors incompleta")
            length = struct.unpack("<Q", prefix)[0]
            if not 2 <= length <= min(100 * 1024 * 1024, stat.st_size - 8):
                raise ValueError("Cabecera safetensors inválida")
            header = json.loads(stream.read(length))
        if not isinstance(header, dict):
            raise ValueError("Cabecera safetensors inválida")
        return header

    def _classify(self, path):
        try:
            if not path.resolve().is_relative_to(self.root):
                return None
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self._cache.get(str(path))
            if cached and cached[0] == stamp:
                return cached[1]
            kind = self._kind(self._header(path))
            # Cache only the small classification, not gigabytes of unrelated
            # model headers when the Hub owns a large mixed library.
            self._cache[str(path)] = (stamp, kind)
            return kind
        except (OSError, ValueError, TypeError, KeyError, struct.error):
            return None

    @staticmethod
    def _shape(header, name):
        entry = header.get(name)
        if not isinstance(entry, dict):
            return []
        shape = entry.get("shape", [])
        if (not isinstance(shape, list)
                or any(type(dimension) is not int or dimension < 0 for dimension in shape)):
            return []
        return shape

    @classmethod
    def _kind(cls, header):
        keys = [key for key, value in header.items()
                if key != "__metadata__" and isinstance(value, dict)]
        # Detect the architecture, independent of the file's container prefix
        # or folder. Plain Cosmos without Anima's LLM adapter is excluded.
        if any(key.startswith("llm_adapter.") or ".llm_adapter." in key for key in keys):
            if any(key.endswith("blocks.0.self_attn.q_proj.weight") and
                   cls._shape(header, key) == [2048, 2048] for key in keys):
                return "models"
        for key in keys:
            target = (key.startswith("lora_unet_blocks_") and "self_attn_q_proj" in key
                      or key.startswith(("diffusion_model.blocks.", "blocks."))
                      and ".self_attn.q_proj." in key)
            if target and key.endswith(("lora_down.weight", "lora_A.weight")):
                shape = cls._shape(header, key)
                if len(shape) == 2 and shape[1] == 2048:
                    return "loras"
            if target and key.endswith(".lokr_w1"):
                # LyCORIS adapters factor a 2048x2048 projection into two
                # Kronecker matrices rather than lora_A/lora_down tensors.
                first = cls._shape(header, key)
                second = cls._shape(header, key.removesuffix("lokr_w1") + "lokr_w2")
                if (len(first) == len(second) == 2
                        and first[0] * second[0] == 2048
                        and first[1] * second[1] == 2048):
                    return "loras"
        # The shared Qwen3-0.6B encoder, not a Qwen3-VL/Qwen-Image encoder.
        if (cls._shape(header, "model.embed_tokens.weight") == [151936, 1024]
                and cls._shape(header, "model.layers.0.mlp.down_proj.weight") == [1024, 3072]
                and "model.layers.27.self_attn.q_proj.weight" in header):
            return "text_encoders"
        # Qwen-Image/Wan2.1 16-channel, RGB VAE used by Anima.
        if (cls._shape(header, "conv2.weight") == [16, 16, 1, 1, 1]
                and cls._shape(header, "encoder.conv1.weight") == [96, 3, 3, 3, 3]
                and cls._shape(header, "decoder.head.2.weight") == [3, 96, 3, 3, 3]):
            return "vaes"
        return None

    def scan(self):
        with self._lock:
            return self._scan()

    def _scan(self):
        result = {key: [] for key in ("models", "loras", "text_encoders", "vaes")}
        candidates = []
        for folder, allowed in (("diffusion_models", "models"), ("unet", "models"),
                                ("Diffusers", "models"), ("checkpoints", "models"),
                                ("loras", "loras"), ("text_encoders", "text_encoders"),
                                ("clip", "text_encoders"), ("vae", "vaes")):
            base = self.root / folder
            if not base.is_dir():
                continue
            try:
                paths = sorted(path for path in base.rglob("*")
                               if path.suffix.lower() in (".safetensors", ".sft") and path.is_file())
            except OSError:
                continue
            for path in paths:
                if folder == "loras" and path.relative_to(base).parts[0] == "_forge_alias":
                    continue
                candidates.append((path, base, allowed, folder))
        # File opening/antivirus dominates mixed-library scans on Windows.
        # Bounded parallel reads reduce cold-scan latency; no tensors are loaded.
        with ThreadPoolExecutor(max_workers=8) as executor:
            kinds = executor.map(self._classify, [entry[0] for entry in candidates])
            for (path, base, allowed, folder), kind in zip(candidates, kinds):
                try:
                    if kind != allowed:
                        continue
                    stat = path.stat()
                    backend_file = path.relative_to(base).as_posix()
                    # Keep existing portable IDs. Checkpoints belong to a
                    # distinct ComfyUI loader namespace, so they must not
                    # collide with a diffusion model of the same basename.
                    file = f"checkpoints::{backend_file}" if folder == "checkpoints" else backend_file
                    # ComfyUI's combined folder lists pick the first name. Avoid
                    # presenting a second, ambiguous choice with identical name.
                    if any(entry["file"] == file for entry in result[allowed]):
                        continue
                    result[allowed].append({
                        "file": file, "name": path.stem, "folder": folder,
                        "backend_file": backend_file,
                        "relative_path": path.relative_to(self.root).as_posix(),
                        "size_bytes": stat.st_size,
                        "fingerprint": f"{stat.st_size}:{stat.st_mtime_ns}",
                    })
                except (OSError, ValueError, TypeError, KeyError, struct.error):
                    continue
        result["vaes"].sort(key=lambda entry: (
            entry["file"].casefold() != "qwen_image_vae.safetensors",
            not entry["file"].casefold().endswith("qwen_image_vae.safetensors"),
            entry["file"].casefold()))
        return result

    def validate(self, params):
        """Reject stale/incompatible/untrusted filenames before queuing work.

        Returns a snapshot of the selected files for generation provenance.
        Names use ComfyUI's relative dropdown paths, never absolute paths.
        """
        catalogue = self.scan()
        selected = {}
        for field, group in (("model", "models"), ("text_encoder", "text_encoders"),
                             ("vae", "vaes")):
            value = getattr(params, field, None)
            selected[field] = self._select(catalogue[group], value, field)
        selected["loras"] = []
        for item in getattr(params, "loras", []):
            if not isinstance(item, dict):
                raise ValueError("LoRA inválido")
            entry = self._select(catalogue["loras"], item.get("file"), "LoRA")
            strength = float(item.get("strength", 1))
            if not math.isfinite(strength) or not -3 <= strength <= 3:
                raise ValueError("El peso de LoRA debe estar entre -3 y 3")
            selected["loras"].append({**entry, "strength": strength})
        return selected

    @staticmethod
    def _select(entries, value, label):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"Selecciona un {label} compatible con Anima")
        value = value.replace("\\", "/")
        path_value = value.removeprefix("checkpoints::")
        if path_value.startswith("/") or ":" in path_value or ".." in path_value.split("/"):
            raise ValueError(f"Ruta de {label} inválida")
        for entry in entries:
            if entry["file"] == value:
                return dict(entry)
        raise ValueError(f"{label} no está en el catálogo Anima: {value}")
