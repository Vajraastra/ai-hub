"""
Dominancia por bloque de un LoRA (F5) — el heatmap de los chips.

Score de "cuánto aprendió" cada switch de la exploración, calculado del
fichero LoRA con numpy puro (venv del hub, sin torch): por módulo se computa
la norma de Frobenius EXACTA del delta sin expandirlo,

    ‖s·B@A‖²_F = s² · trace((BᵀB)(AAᵀ))        s = alpha/rank (kohya) | 1.0

que solo multiplica matrices r×r (mejora sobre la cota floja ‖A‖·‖B‖ del
analyzer embrionario de lora_merger, BITACORA s62). La energía (norma²) de
cada módulo se suma a su switch de la UI reutilizando el MISMO contrato que
el merge worker: lora_key_map() para módulo→tensor base y
config_to_merge_blocks() para switch→prefijos de tensor — así el heatmap
mide exactamente lo que ese chip enciende/apaga.

Scores relativos DENTRO del LoRA (no dependen de strength ni de la base):
  score  0–100 = ‖Δ_bloque‖ / max_bloque  (100 = el bloque más dominante)
  share  %     = energía del bloque / energía total del delta

La relativización contra el checkpoint (‖ΔW‖/‖W_base‖) y la calibración
empírica generando con bloques apagados son escalones futuros (BITACORA s62).
"""
import json
import struct
from pathlib import Path

import numpy as np

from .architectures import get_adapter
from .lora_format import LoraFormatError, collect_pairs


class DominanceError(Exception):
    pass


# dtype safetensors → numpy (BF16 se reinterpreta a mano más abajo)
_DTYPES = {"F32": np.float32, "F16": np.float16, "F64": np.float64,
           "BF16": np.uint16}

# cache por (path resuelto, mtime, arch) — el análisis relee el fichero entero
_cache: dict[tuple, dict] = {}
_CACHE_MAX = 16


def _read_header(f) -> tuple[dict, int]:
    """(tensores del header, offset absoluto donde empiezan los datos)."""
    n = struct.unpack("<Q", f.read(8))[0]
    if n > 100 * 1024 * 1024:
        raise DominanceError(f"header sospechoso ({n} bytes)")
    hdr = json.loads(f.read(n))
    hdr.pop("__metadata__", None)
    return hdr, 8 + n


def _load(f, hdr: dict, data_off: int, key: str) -> np.ndarray:
    """Carga un tensor como float32 (BF16 vía reinterpretación uint16<<16)."""
    info = hdr[key]
    dtype_str = info.get("dtype", "F32")
    dtype = _DTYPES.get(dtype_str)
    if dtype is None:
        raise DominanceError(f"tensor {key!r}: dtype {dtype_str!r} no soportado")
    o0, o1 = info["data_offsets"]
    f.seek(data_off + o0)
    raw = f.read(o1 - o0)
    arr = np.frombuffer(raw, dtype=dtype)
    if dtype_str == "BF16":
        arr = (arr.astype(np.uint32) << 16).view(np.float32)
    return arr.astype(np.float32, copy=False).reshape(info["shape"])


def _switch_prefixes(adapter) -> list[tuple[str, tuple[str, ...]]]:
    """[(switch_id, prefijos de tensor sin container_prefix)] vía
    config_to_merge_blocks — mismo mapeo que enciende el chip en el merge."""
    out = []
    for sw in adapter.explore_switches():
        sid = sw["id"]
        cfg = ({"blocks": {}, "other": 1.0} if sid == "other"
               else {"blocks": {sid: 1.0}, "other": 0.0})
        out.append((sid, tuple(adapter.config_to_merge_blocks(cfg))))
    return out


def _module_energy(f, hdr, data_off, parts: dict) -> float:
    """Energía ‖s·B@A‖²_F del módulo (conv aplanada desde dim 1, como el
    merge worker). Acumulación en float64 para no perder cola."""
    A = _load(f, hdr, data_off, parts["A"])
    B = _load(f, hdr, data_off, parts["B"])
    A2 = A.reshape(A.shape[0], -1)            # (r, in·kh·kw)
    B2 = B.reshape(B.shape[0], -1)            # (out, r)
    scale = 1.0
    if "alpha" in parts:
        alpha = float(_load(f, hdr, data_off, parts["alpha"]))
        scale = alpha / A.shape[0]            # rank = filas de lora_down
    AAt = (A2 @ A2.T).astype(np.float64)      # r×r
    BtB = (B2.T @ B2).astype(np.float64)      # r×r
    # ‖B@A‖²_F = trace((BᵀB)(AAᵀ)); ambas simétricas → suma elemento a elemento
    return (scale ** 2) * float(np.sum(AAt * BtB))


def analyze(models_root: Path, lora_file: str, arch: str) -> dict:
    """Dominancia por switch de la UI para un LoRA del almacén.

    Devuelve {"scores": {switch_id: 0–100}, "shares": {switch_id: %},
    "modules": {switch_id: n}, "unassigned": n}. Cachea por mtime."""
    adapter = get_adapter(arch)
    path = (Path(models_root) / "loras" / lora_file).resolve()
    if not path.is_file():
        raise DominanceError(f"no existe el LoRA {lora_file!r}")

    ck = (str(path), path.stat().st_mtime_ns, arch)
    if ck in _cache:
        return _cache[ck]

    key_map = adapter.lora_key_map()
    cprefix = adapter.container_prefix
    switches = _switch_prefixes(adapter)
    has_other = any(sid == "other" for sid, _ in switches)

    energy: dict[str, float] = {sid: 0.0 for sid, _ in switches}
    modules: dict[str, int] = {sid: 0 for sid, _ in switches}
    unassigned = 0

    with open(path, "rb") as f:
        hdr, data_off = _read_header(f)
        try:
            pairs, _skipped = collect_pairs(list(hdr),
                                            adapter.ignored_lora_prefixes)
        except LoraFormatError as e:
            raise DominanceError(f"LoRA no soportado: {e}")
        if not pairs:
            raise DominanceError("el LoRA solo entrena text encoders: "
                                 "nada que analizar")
        for module, parts in pairs.items():
            target = key_map.get(module)
            if target is None:
                raise DominanceError(
                    f"módulo {module!r} sin mapeo en {arch!r} — mismo naming "
                    "no soportado que rechazaría la sesión")
            base_key = target[0]
            bare = (base_key[len(cprefix):]
                    if cprefix and base_key.startswith(cprefix) else base_key)
            e = _module_energy(f, hdr, data_off, parts)
            for sid, prefixes in switches:
                if any(bare == p or bare.startswith(p + ".")
                       for p in prefixes):
                    energy[sid] += e
                    modules[sid] += 1
                    break
            else:
                if has_other:
                    energy["other"] += e
                    modules["other"] += 1
                else:
                    unassigned += 1

    total = sum(energy.values())
    if total <= 0:
        raise DominanceError("delta nulo: todos los módulos con energía 0")
    norms = {sid: e ** 0.5 for sid, e in energy.items()}
    top = max(norms.values())
    result = {
        "arch": arch, "file": lora_file,
        "scores": {sid: round(100.0 * n / top, 1) for sid, n in norms.items()},
        "shares": {sid: round(100.0 * e / total, 1)
                   for sid, e in energy.items()},
        "modules": modules,
        "unassigned": unassigned,
    }
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[ck] = result
    return result
