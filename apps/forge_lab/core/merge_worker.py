"""
Worker de merge LoRA → checkpoint (proceso aparte, venv de ComfyUI).

Se ejecuta con el Python del venv de ComfyUI (torch + safetensors) porque el
venv de hub-webui es ligero a propósito. TODO el cálculo va en CPU/RAM (regla
dura del handoff: la GPU se reserva para inferencia).

Matemática (idéntica a la carga runtime de ComfyUI):

    W' = W + strength * scale * (B @ A)      en fp32, resultado al dtype orig.
    scale = alpha / rank  si el módulo trae tensor alpha (formato kohya),
            1.0           si no (formato PEFT sin alpha, p. ej. ai-toolkit)

Formatos de fichero LoRA soportados:
  - PEFT/diffusers: <prefijo>modulo.lora_A.weight / .lora_B.weight, con
    prefijo contenedor diffusion_model. / transformer. (Z-Image y cía.)
  - kohya: lora_unet_modulo.lora_down.weight / .lora_up.weight / .alpha
    (SDXL/Civitai). lora_down ≡ A, lora_up ≡ B. Las claves de text encoder
    (adapter.ignored_lora_prefixes) se SALTAN por política, nunca se mergean.

Para proyecciones fusionadas (qkv de Z-Image) el delta aterriza en su franja
según el lora_key_map() del adaptador — réplica del mapeo de ComfyUI, así
mergear a fichero ≡ cargar el LoRA en memoria (verificado empíricamente).
El filtro de bloques compara contra la clave SIN adapter.container_prefix
(sdxl: "model.diffusion_model."); las zonas prohibidas, contra la completa.

Uso:  python merge_worker.py <job.json>

job.json:
  {
    "arch": "zimage" | "sdxl",
    "base_path": "...", "lora_path": "...", "out_path": "...",
    "strength": 1.0,
    "blocks": null                        # null = merge completo (Fase 3)
            | ["layers.0", ...]           # solo esos bloques, dosis 1.0
            | {"layers.0": 0.5, ...},     # dosis por bloque (Fase 4);
                                          #   escala final = strength * dosis
    "allow_forbidden": false,
    "metadata": { ... }                   # se embebe en el safetensors
  }

Protocolo: una línea JSON por evento en stdout —
  {"phase": "map|merge|write", "done": n, "total": n}
  {"ok": true, "merged_tensors": n, "lora_pairs": n, "skipped_blocks": n,
   "skipped_policy": n}
  {"error": "..."} y exit(1) si algo no cuadra. Nada de merges "a medias":
  cualquier clave LoRA no mapeable o con shape inesperada aborta el trabajo.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from architectures import get_adapter  # noqa: E402  (stdlib puro)
from lora_format import LoraFormatError, collect_pairs  # noqa: E402


def fail(msg: str):
    print(json.dumps({"error": msg}), flush=True)
    sys.exit(1)


def progress(phase: str, done: int, total: int):
    print(json.dumps({"phase": phase, "done": done, "total": total}), flush=True)


def main(job_path: str):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    adapter = get_adapter(job["arch"])
    strength = float(job["strength"])
    blocks = job.get("blocks")           # None → completo
    base_path, lora_path = job["base_path"], job["lora_path"]
    out_path = Path(job["out_path"])

    import torch  # tarda; después del parseo barato
    from safetensors import safe_open
    from safetensors.torch import save_file

    key_map = adapter.lora_key_map()
    forbidden = adapter.forbidden_zones()
    cprefix = adapter.container_prefix

    def strip_container(key: str) -> str:
        return key[len(cprefix):] if cprefix and key.startswith(cprefix) else key

    # ── 1. Mapear el LoRA contra el checkpoint ────────────────────────────
    with safe_open(lora_path, framework="pt", device="cpu") as lf:
        lora_keys = list(lf.keys())
    try:
        pairs, skipped_policy = collect_pairs(lora_keys,
                                              adapter.ignored_lora_prefixes)
    except LoraFormatError as e:
        fail(str(e))
    if not pairs:
        fail("el LoRA solo contiene claves saltadas por política "
             "(¿LoRA de text encoder puro?)")

    # normalizar blocks: None → sin filtro; lista → dosis 1.0; dict → dosis
    block_dose: dict[str, float] | None
    if blocks is None:
        block_dose = None
    elif isinstance(blocks, list):
        block_dose = {b: 1.0 for b in blocks}
    else:
        block_dose = {b: float(d) for b, d in blocks.items()}

    def dose_for(base_key: str) -> float | None:
        """Dosis del bloque al que pertenece el tensor (None = filtrado).
        Los ids de bloque van sin container_prefix (contrato del adaptador)."""
        if block_dose is None:
            return 1.0
        bare = strip_container(base_key)
        for b, d in block_dose.items():
            if bare == b or bare.startswith(b + "."):
                return d if d != 0 else None
        return None

    # deltas[base_key] = lista de (módulo, franja, dosis) sobre ese tensor
    deltas: dict[str, list[tuple[str, tuple | None, float]]] = {}
    unmapped, skipped_blocks = [], set()
    for module in pairs:
        target = key_map.get(module)
        if target is None:
            unmapped.append(module)
            continue
        base_key, sl = target
        dose = dose_for(base_key)
        if dose is None:
            skipped_blocks.add(module)
            continue
        if any(base_key == z or
               base_key.startswith(z if z.endswith(".") else z + ".")
               for z in forbidden) and not job.get("allow_forbidden"):
            fail(f"el LoRA toca la zona prohibida {base_key!r} "
                 "(override explícito requerido)")
        deltas.setdefault(base_key, []).append((module, sl, dose))
    if unmapped:
        fail(f"claves LoRA sin mapeo en la arquitectura {adapter.name!r}: "
             f"{unmapped[:5]}{' …' if len(unmapped) > 5 else ''}")
    if not deltas:
        fail("ningún módulo del LoRA aplica (¿filtro de bloques vacío?)")
    progress("map", len(pairs), len(pairs))

    # ── 2. Merge tensor a tensor (fp32 → dtype original) ─────────────────
    out_tensors: dict[str, torch.Tensor] = {}
    n_merged = 0
    with safe_open(base_path, framework="pt", device="cpu") as bf, \
         safe_open(lora_path, framework="pt", device="cpu") as lf:
        base_keys = list(bf.keys())
        missing = [k for k in deltas if k not in base_keys]
        if missing:
            fail(f"el mapeo apunta a tensores inexistentes en el base: {missing[:5]}")
        total = len(base_keys)
        for i, k in enumerate(base_keys):
            t = bf.get_tensor(k)
            if k in deltas:
                orig_dtype = t.dtype
                t = t.to(torch.float32)
                for module, sl, dose in deltas[k]:
                    A = lf.get_tensor(pairs[module]["A"]).to(torch.float32)
                    B = lf.get_tensor(pairs[module]["B"]).to(torch.float32)
                    if A.dim() != 2 or B.dim() != 2:
                        fail(f"{module}: tensores LoRA de dim {A.dim()}/"
                             f"{B.dim()} (¿LoCon/conv?) — no soportado")
                    scale = 1.0
                    if "alpha" in pairs[module]:
                        alpha = float(lf.get_tensor(pairs[module]["alpha"]))
                        scale = alpha / A.shape[0]   # rank = filas de lora_down
                    d = (strength * dose * scale) * (B @ A)
                    if sl is None:
                        if d.shape != t.shape:
                            fail(f"{module}: delta {tuple(d.shape)} ≠ "
                                 f"tensor base {tuple(t.shape)} ({k})")
                        t += d
                    else:
                        dim, start, size = sl
                        want = list(t.shape)
                        want[dim] = size
                        if list(d.shape) != want:
                            fail(f"{module}: delta {tuple(d.shape)} ≠ franja "
                                 f"{want} de {k}")
                        t.narrow(dim, start, size).add_(d)
                t = t.to(orig_dtype)
                n_merged += 1
            out_tensors[k] = t
            if (i + 1) % 40 == 0 or i + 1 == total:
                progress("merge", i + 1, total)

    # ── 3. Escribir con linaje embebido ──────────────────────────────────
    progress("write", 0, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {k: str(v) for k, v in (job.get("metadata") or {}).items()}
    meta["forge_lab.format"] = "merged-checkpoint-v1"
    save_file(out_tensors, str(out_path), metadata=meta)
    progress("write", 1, 1)

    print(json.dumps({"ok": True, "merged_tensors": n_merged,
                      "lora_pairs": len(pairs),
                      "skipped_blocks": len(skipped_blocks),
                      "skipped_policy": len(skipped_policy)}), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        fail("uso: merge_worker.py <job.json>")
    try:
        main(sys.argv[1])
    except SystemExit:
        raise
    except Exception as e:  # cualquier cosa inesperada → línea JSON + exit 1
        fail(f"{type(e).__name__}: {e}")
