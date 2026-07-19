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

Dos escalones de score (BITACORA s62; el 3º — calibración empírica con GPU —
se deja madurar):
  1. relativo DENTRO del LoRA: score 0–100 = ‖Δ_bloque‖ / max_bloque.
  2. relativo AL CHECKPOINT (s83, si analyze recibe base_path): por switch,
     ratio = √(Σ‖Δ_mód‖² / Σ‖W_base_mód‖²) sobre los módulos que el LoRA
     entrena — cuánto cambia lo que toca, relativo a su magnitud en la base.
     score 0–100 = ratio/max_ratio y "ratios" trae el % absoluto (100·ratio).
     Las ‖W_base‖² por target del lora_key_map (con franja qkv aplicada) se
     cachean en disco por checkpoint (data/cache/): la primera pasada lee el
     modelo de difusión entero; después es un JSON.
  share % = energía del bloque / energía total del delta (siempre del delta).
"""
import hashlib
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


# ── Escalón 2: energías ‖W‖² del checkpoint base (cache por fichero) ───────

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
_base_mem: dict[tuple, dict] = {}   # (path, mtime_ns, arch) → {clave: energía}


def _slice_key(bare: str, sl) -> str:
    """Clave de cache de un target del lora_key_map: tensor base (sin
    container_prefix) + franja si la hay (qkv fusionado en zimage/anima)."""
    return bare if sl is None else f"{bare}#{sl[0]}:{sl[1]}:{sl[2]}"


def base_energies(base_path: Path, arch: str) -> dict[str, float]:
    """‖W‖²_F por target del lora_key_map de `arch` en el checkpoint base.

    Devuelve {_slice_key: energía} solo con los targets presentes en el
    fichero. La primera pasada lee el modelo de difusión entero (decenas de
    segundos en un checkpoint completo); el resultado se cachea en memoria y
    en disco (data/cache/, validado por mtime+size)."""
    adapter = get_adapter(arch)
    base_path = Path(base_path).resolve()
    if not base_path.is_file():
        raise DominanceError(f"no existe el checkpoint base {base_path}")
    st = base_path.stat()
    mk = (str(base_path), st.st_mtime_ns, arch)
    if mk in _base_mem:
        return _base_mem[mk]

    h = hashlib.sha1(f"{base_path}|{arch}".encode("utf-8")).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"base-norms_{base_path.stem[:40]}_{h}.json"
    if cache_file.is_file():
        try:
            d = json.loads(cache_file.read_text(encoding="utf-8"))
            if (d.get("mtime_ns") == st.st_mtime_ns
                    and d.get("size") == st.st_size):
                _base_mem[mk] = d["energies"]
                return d["energies"]
        except (OSError, ValueError, KeyError):
            pass                              # cache corrupta → recomputar

    key_map = adapter.lora_key_map()
    aprefix = adapter.container_prefix
    energies: dict[str, float] = {}
    with open(base_path, "rb") as f:
        hdr, data_off = _read_header(f)
        # variantes de prefijo del fichero (mismo criterio que merge_worker)
        cprefix = aprefix
        if aprefix and not any(k.startswith(aprefix) for k in hdr):
            for var in getattr(adapter, "container_prefix_variants", ()):
                if any(k.startswith(var) for k in hdr):
                    cprefix = var
                    break
        # franjas requeridas por tensor base (bare = target sin prefijo)
        wanted: dict[str, set] = {}
        for target, sl in key_map.values():
            bare = (target[len(aprefix):]
                    if aprefix and target.startswith(aprefix) else target)
            wanted.setdefault(bare, set()).add(tuple(sl) if sl else None)
        # lectura secuencial: en orden de offset dentro del fichero
        order = sorted((b for b in wanted if (cprefix + b) in hdr),
                       key=lambda b: hdr[cprefix + b]["data_offsets"][0])
        for bare in order:
            t = _load(f, hdr, data_off, cprefix + bare)
            for sl in wanted[bare]:
                v = t
                if sl is not None:
                    dim, start, size = sl
                    idx = [slice(None)] * t.ndim
                    idx[dim] = slice(start, start + size)
                    v = t[tuple(idx)]
                x = np.ascontiguousarray(v, dtype=np.float64).ravel()
                energies[_slice_key(bare, sl)] = float(x @ x)
    if not energies:
        raise DominanceError(
            f"el checkpoint base no contiene ningún target de {arch!r}")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(
        {"file": str(base_path), "arch": arch, "mtime_ns": st.st_mtime_ns,
         "size": st.st_size, "energies": energies}), encoding="utf-8")
    _base_mem[mk] = energies
    return energies


def analyze(models_root: Path, lora_file: str, arch: str,
            base_path: Path | None = None) -> dict:
    """Dominancia por switch de la UI para un LoRA del almacén.

    Devuelve {"scores": {switch_id: 0–100}, "shares": {switch_id: %},
    "modules": {switch_id: n}, "unassigned": n, "relative": bool}. Con
    base_path (escalón 2) los scores son relativos al checkpoint y "ratios"
    trae el % de cambio absoluto por switch; si la parte base falla se
    degrada al escalón 1 con "relative_error". Cachea por mtime."""
    adapter = get_adapter(arch)
    path = (Path(models_root) / "loras" / lora_file).resolve()
    if not path.is_file():
        raise DominanceError(f"no existe el LoRA {lora_file!r}")

    bkey = None
    if base_path is not None and Path(base_path).is_file():
        bp = Path(base_path).resolve()
        bkey = (str(bp), bp.stat().st_mtime_ns)
    ck = (str(path), path.stat().st_mtime_ns, arch, bkey)
    if ck in _cache:
        return _cache[ck]

    key_map = adapter.lora_key_map()
    cprefix = adapter.container_prefix
    switches = _switch_prefixes(adapter)
    has_other = any(sid == "other" for sid, _ in switches)

    energy: dict[str, float] = {sid: 0.0 for sid, _ in switches}
    modules: dict[str, int] = {sid: 0 for sid, _ in switches}
    targets: dict[str, list[str]] = {sid: [] for sid, _ in switches}
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
            base_key, sl = target
            bare = (base_key[len(cprefix):]
                    if cprefix and base_key.startswith(cprefix) else base_key)
            e = _module_energy(f, hdr, data_off, parts)
            for sid, prefixes in switches:
                if any(bare == p or bare.startswith(p + ".")
                       for p in prefixes):
                    energy[sid] += e
                    modules[sid] += 1
                    targets[sid].append(_slice_key(bare, sl))
                    break
            else:
                if has_other:
                    energy["other"] += e
                    modules["other"] += 1
                    targets["other"].append(_slice_key(bare, sl))
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
        "relative": False,
    }
    if bkey is not None:
        # escalón 2: ratio √(Σ‖Δ‖²/Σ‖W_base‖²) por switch, sobre los módulos
        # que el LoRA entrena; si la parte base falla, escalón 1 + motivo
        try:
            be = base_energies(Path(base_path), arch)
            missing = [k for keys in targets.values() for k in keys
                       if k not in be]
            if missing:
                raise DominanceError(
                    f"targets sin peso en el checkpoint base: {missing[:3]}"
                    f"{' …' if len(missing) > 3 else ''}")
            ratios = {}
            for sid in energy:
                b = sum(be[k] for k in targets[sid])
                ratios[sid] = (energy[sid] / b) ** 0.5 if b > 0 else 0.0
            rtop = max(ratios.values())
            if rtop <= 0:
                raise DominanceError("energía base nula en todos los switches")
            result["scores"] = {sid: round(100.0 * r / rtop, 1)
                                for sid, r in ratios.items()}
            result["ratios"] = {sid: round(100.0 * r, 2)
                                for sid, r in ratios.items()}
            result["relative"] = True
        except (DominanceError, OSError) as e:
            result["relative_error"] = str(e)
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[ck] = result
    return result
