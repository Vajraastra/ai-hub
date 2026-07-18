"""
Worker de fusión LoRA A + LoRA B → LoRA fusionado (proceso aparte, venv de
ComfyUI). Hermano de merge_worker.py: mismo venv (torch + safetensors), mismo
protocolo de líneas JSON, TODO en CPU/RAM (la GPU se reserva para inferencia).

Matemática (exacta, sin pérdida — twist s58/s62):

    Los deltas de dos LoRAs sobre el mismo peso suman linealmente:
        δ = δ_A + δ_B = f_A·(Bₐ@Aₐ) + f_B·(B_b@A_b)
        f_X = strength_X · dosis_X · scale_X   (scale = alpha/rank o 1.0)

    Se representa como UN LoRA por CONCATENACIÓN de rangos (no suma de
    matrices low-rank → inmune a rangos distintos, r_A ≠ r_B):
        down = [Aₐ ; A_b]           (apila filas → rank = r_A + r_B)
        up   = [f_A·Bₐ | f_B·B_b]   (concatena columnas; el factor va en up)
        alpha = rank_fused          (⇒ scale = alpha/rank = 1.0 al cargar)

    up @ down = f_A·(Bₐ@Aₐ) + f_B·(B_b@A_b) = δ_A + δ_B  ≡ preview encadenado.

    Módulos presentes en un solo LoRA se copian con su factor. Capas conv
    (LoCon: down (r,in,kh,kw), up (out,r,1,1)) concatenan por el eje de rango
    igual que las lineales — flatten(1) de la concat = concat de los flatten.

Compresión SVD (job["svd_energy"] ∈ (0,1]; null = concat puro exacto): tras
concatenar, cada módulo se recomprime al rango MÍNIMO que conserva esa fracción
de energía Frobenius² (Σσ²). Sin materializar el delta denso (out×in): SVD
económico sobre el espacio de rango r=rₐ+r_b vía dos QR + un SVD r×r —
    up=QᵤRᵤ,  downᵀ=Q_dR_d,  M=RᵤR_dᵀ=UΣVᵀ  ⇒  ΔW=(QᵤU)Σ(Q_dV)ᵀ, trunc a k.
Esto es lo que evita que las fusiones ENCADENADAS exploten el rango (concat lo
hace crecer; SVD lo devuelve a un tamaño sano) — el sentido de componer merges.

Uso:  python fuse_worker.py <job.json>

job.json:
  {
    "arch": "sdxl",
    "lora_a_path": "...", "lora_b_path": "...",
    "strength_a": 1.0, "strength_b": 1.0,
    "blocks_a": null | {"output_blocks.5.1": 0.5, ...},   # dosis por bloque
    "blocks_b": null | {...},                             # (config_to_merge_blocks)
    "out_path": "...",
    "svd_energy": 0.99,                  # null = concat exacto; (0,1] = comprimir
    "base_model": "",                    # ss_base_model del LoRA A (detección arch)
    "metadata": { ... }
  }

Protocolo (idéntico a merge_worker):
  {"phase": "map|fuse|write", "done": n, "total": n}
  {"ok": true, "modules": n, "rank_max": n, "rank_pre": n, "skipped_policy": n}
  {"error": "..."} y exit(1) si algo no cuadra. Sin fusiones "a medias".
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
    out_path = Path(job["out_path"])
    svd_energy = job.get("svd_energy")
    if svd_energy is not None and not 0.0 < float(svd_energy) <= 1.0:
        fail(f"svd_energy {svd_energy} fuera de (0,1] (null = sin comprimir)")

    import torch  # tarda; después del parseo barato
    from safetensors import safe_open
    from safetensors.torch import save_file

    def compress(down, up, energy):
        """(down_c, up_c, k): recomprime ΔW=up@down al rango mínimo que
        conserva `energy` de Σσ². Trabaja en fp32 sobre el espacio de rango
        (dos QR + SVD r×r), nunca sobre out×in. Reparte √σ en ambos lados."""
        d2 = down.flatten(1).to(torch.float32)     # (r, N)  N = in[·kh·kw]
        u2 = up.flatten(1).to(torch.float32)       # (out, r)
        Qu, Ru = torch.linalg.qr(u2, mode="reduced")          # Qu(out,r) Ru(r,r)
        Qd, Rd = torch.linalg.qr(d2.T, mode="reduced")        # Qd(N,r)  Rd(r,r)
        U, S, Vh = torch.linalg.svd(Ru @ Rd.T)                # M = Ru·Rdᵀ (r,r)
        total = float((S * S).sum())
        if total <= 0:
            k = 1
        else:
            cum = torch.cumsum(S * S, 0) / total
            k = int((cum < energy).sum().item()) + 1          # 1er k con ≥ energy
            k = max(1, min(k, S.shape[0]))
        s = torch.sqrt(S[:k])
        up_c = (Qu @ U[:, :k]) * s                            # (out, k)
        down_c = s.unsqueeze(1) * (Vh[:k, :] @ Qd.T)          # (k, N)
        # re-formar conv: down (k,in,kh,kw), up (out,k,1,1)
        if down.dim() == 4:
            down_c = down_c.reshape(k, *down.shape[1:])
            up_c = up_c.reshape(up.shape[0], k, *up.shape[2:])
        return down_c, up_c, k

    key_map = adapter.lora_key_map()
    cprefix = adapter.container_prefix

    def strip_container(key: str) -> str:
        return key[len(cprefix):] if cprefix and key.startswith(cprefix) else key

    def dose_of(module: str, block_dose: dict | None) -> float | None:
        """Dosis del bloque al que pertenece el módulo LoRA (None = filtrado).
        Sin filtro → 1.0. El bloque se resuelve por el target del key_map,
        exactamente como merge_worker (misma matemática de dosis)."""
        if block_dose is None:
            return 1.0
        target = key_map.get(module)
        if target is None:
            return "unmapped"   # centinela: filtro activo y módulo sin mapeo
        bare = strip_container(target[0])
        for b, d in block_dose.items():
            if bare == b or bare.startswith(b + "."):
                return d if d != 0 else None
        return None

    def plan(path: str, blocks) -> tuple[dict, dict, int]:
        """(pairs, doses, n_skipped_policy) del LoRA en `path`. `doses` solo
        incluye los módulos que sobreviven al filtro de bloques (dosis > 0)."""
        with safe_open(path, framework="pt", device="cpu") as lf:
            keys = list(lf.keys())
        try:
            pairs, skipped_policy = collect_pairs(
                keys, adapter.ignored_lora_prefixes)
        except LoraFormatError as e:
            fail(str(e))
        block_dose = (None if blocks is None
                      else {b: float(d) for b, d in blocks.items()})
        doses, unmapped = {}, []
        for module in pairs:
            d = dose_of(module, block_dose)
            if d == "unmapped":
                unmapped.append(module)
            elif d is not None:
                doses[module] = d
        if unmapped:
            fail(f"claves LoRA sin mapeo en {adapter.name!r} (filtro de "
                 f"bloques activo): {unmapped[:5]}"
                 f"{' …' if len(unmapped) > 5 else ''}")
        return pairs, doses, len(skipped_policy)

    # ── 1. Planear ambos LoRAs contra la máscara de bloques ───────────────
    pairs_a, doses_a, skip_a = plan(job["lora_a_path"], job.get("blocks_a"))
    pairs_b, doses_b, skip_b = plan(job["lora_b_path"], job.get("blocks_b"))
    modules = sorted(set(doses_a) | set(doses_b))
    if not modules:
        fail("ningún módulo aplica tras el filtro de bloques (¿dosis todas a 0?)")
    progress("map", len(modules), len(modules))

    sa, sb = float(job["strength_a"]), float(job["strength_b"])

    # ── 2. Fusionar módulo a módulo por concatenación de rangos ───────────
    out_tensors: dict[str, torch.Tensor] = {}
    rank_max, rank_pre_max = 0, 0
    total = len(modules)
    with safe_open(job["lora_a_path"], framework="pt", device="cpu") as lf_a, \
         safe_open(job["lora_b_path"], framework="pt", device="cpu") as lf_b:
        sources = [(pairs_a, doses_a, sa, lf_a), (pairs_b, doses_b, sb, lf_b)]
        for i, module in enumerate(modules):
            downs, ups, out_dtype, rank = [], [], None, 0
            for pairs, doses, strength, lf in sources:
                if module not in doses:
                    continue
                p = pairs[module]
                dn = lf.get_tensor(p["A"])        # lora_down ≡ A
                up = lf.get_tensor(p["B"])        # lora_up   ≡ B
                if out_dtype is None:
                    out_dtype = up.dtype
                if dn.dim() not in (2, 4) or up.dim() not in (2, 4):
                    fail(f"{module}: tensores LoRA de dim {dn.dim()}/{up.dim()}"
                         " — no soportado")
                dn = dn.to(torch.float32)
                up = up.to(torch.float32)
                scale = 1.0
                if "alpha" in p:
                    scale = float(lf.get_tensor(p["alpha"])) / dn.shape[0]
                factor = strength * doses[module] * scale
                downs.append(dn)
                ups.append(up * factor)           # absorbe f_X en up
                rank += dn.shape[0]
            try:
                down_fused = torch.cat(downs, dim=0)
                up_fused = torch.cat(ups, dim=1)
            except RuntimeError as e:
                fail(f"{module}: formas incompatibles entre A y B ({e})")
            rank_pre_max = max(rank_pre_max, rank)
            if svd_energy is not None:
                down_fused, up_fused, rank = compress(
                    down_fused, up_fused, float(svd_energy))
            out_tensors[f"{module}.lora_down.weight"] = down_fused.to(out_dtype)
            out_tensors[f"{module}.lora_up.weight"] = up_fused.to(out_dtype)
            # alpha = rank ⇒ scale = alpha/rank = 1.0 (el factor ya está en up)
            out_tensors[f"{module}.alpha"] = torch.tensor(
                float(rank), dtype=out_dtype)
            rank_max = max(rank_max, rank)
            if (i + 1) % 40 == 0 or i + 1 == total:
                progress("fuse", i + 1, total)

    # ── 3. Escribir con linaje embebido ───────────────────────────────────
    progress("write", 0, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {k: str(v) for k, v in (job.get("metadata") or {}).items()}
    meta["forge_lab.format"] = "fused-lora-v1"
    meta["forge_lab.svd_energy"] = "concat" if svd_energy is None else str(svd_energy)
    meta["forge_lab.rank"] = f"{rank_pre_max}->{rank_max}"
    if job.get("base_model"):                     # que list_loras lo lea SDXL
        meta.setdefault("ss_base_model_version", str(job["base_model"]))
    save_file(out_tensors, str(out_path), metadata=meta)
    progress("write", 1, 1)

    print(json.dumps({"ok": True, "modules": len(modules),
                      "rank_max": rank_max, "rank_pre": rank_pre_max,
                      "skipped_policy": skip_a + skip_b}), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        fail("uso: fuse_worker.py <job.json>")
    try:
        main(sys.argv[1])
    except SystemExit:
        raise
    except Exception as e:  # cualquier cosa inesperada → línea JSON + exit 1
        fail(f"{type(e).__name__}: {e}")
