"""
Batería de pruebas del alias diffusers→kohya (lora_alias.py) sobre la
colección REAL de LoRAs. Diseñada para correrse de forma autónoma (sin
intervención manual) — con >1400 ficheros el test a mano es inviable.

Niveles (acumulativos, de barato a caro):

  0  autotest de la tabla     sin colección. Cada módulo traducido debe
                              existir en sdxl.lora_key_map(); conteos exactos.
  1  censo (header-only)      toda la colección: clasifica cada LoRA y hace
                              un dry-run de traducción. Reporta convertibles,
                              rechazos y su motivo. No escribe nada.
  2  conversión + integridad  convierte TODOS los convertibles y verifica:
                              mismos tensores/shapes/dtypes, bloque de datos
                              byte-idéntico (sha256), cobertura OK, y cada
                              target del mapeo existe en un checkpoint SDXL
                              real con dims compatibles (equivale a probar el
                              merge sin torch). Idempotencia del cache.
  3  runtime (ComfyUI :8188)  muestra pequeña: genera con el nodo selectivo
                              todo-ON vs todo-OFF con la copia convertida y
                              exige diff de píxeles > umbral (detector del
                              fallo mudo de la s56). Necesita ComfyUI+GPU.

Uso (venv del hub):
  python alias_battery.py --level 2                # niveles 0..2
  python alias_battery.py --level 3 --sample 3     # + runtime con 3 LoRAs
  python alias_battery.py --models-root E:\\Models

Reporte: resumen por stdout + JSON detallado en
  apps/forge_lab/data/alias_battery_report.json
"""
import argparse
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # apps/

from forge_lab.core import lora_alias  # noqa: E402
from forge_lab.core.architectures import get_adapter  # noqa: E402
from forge_lab.core.lora_format import LoraFormatError, collect_pairs  # noqa: E402

REPORT_PATH = Path(__file__).parent.parent / "data" / "alias_battery_report.json"


def read_header(path: Path) -> tuple[dict, int]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 * 1024 * 1024:
            raise ValueError(f"header sospechoso ({n} bytes)")
        return json.loads(f.read(n)), 8 + n


def sha256_from(path: Path, offset: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        while chunk := f.read(16 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def ok(msg):
    print(f"  ✔ {msg}")


def fail(msg):
    print(f"  ✘ {msg}")
    return 1


# ── Nivel 0 — autotest de la tabla ─────────────────────────────────────────

def level0() -> tuple[int, dict]:
    print("── Nivel 0: autotest de la tabla de traducción")
    errors = 0
    adapter = get_adapter("sdxl")
    key_map = adapter.lora_key_map()
    mmap = lora_alias._module_map()

    bad = [v for v in mmap.values() if v not in key_map]
    if bad:
        errors += fail(f"{len(bad)} destinos fuera de lora_key_map: {bad[:3]}")
    else:
        ok(f"los {len(mmap)} destinos existen en lora_key_map() de sdxl")

    # conteo exacto: 5 transformers depth-2 (22 módulos) + 6 depth-10 (102)
    expect = 5 * 22 + 6 * 102
    if len(mmap) != expect:
        errors += fail(f"tabla con {len(mmap)} módulos, esperados {expect}")
    else:
        ok(f"conteo exacto de módulos: {expect}")

    if len(set(mmap.values())) != len(mmap):
        errors += fail("destinos duplicados en la tabla")
    else:
        ok("sin destinos duplicados (mapeo 1:1)")

    # el naming canónico jamás debe re-traducirse
    canon_in_map = [k for k in mmap if k in key_map]
    if canon_in_map:
        errors += fail(f"módulos canónicos como origen: {canon_in_map[:3]}")
    else:
        ok("ningún módulo canónico aparece como origen de traducción")
    return errors, {"table_modules": len(mmap)}


# ── Nivel 1 — censo de la colección (dry-run, header-only) ────────────────

def level1(loras_root: Path) -> tuple[int, dict]:
    print("── Nivel 1: censo de la colección (dry-run de traducción)")
    t0 = time.time()
    adapter = get_adapter("sdxl")
    key_map = adapter.lora_key_map()
    res = {"total": 0, "unreadable": 0, "kohya_ok": 0, "no_sdxl": 0,
           "te_only": 0, "convertible": [], "rejected": []}

    for p in sorted(loras_root.rglob("*.safetensors")):
        rel = p.relative_to(loras_root).as_posix()
        if rel.startswith(lora_alias.ALIAS_SUBDIR + "/"):
            continue
        res["total"] += 1
        try:
            hdr, _ = read_header(p)
        except Exception:
            res["unreadable"] += 1
            continue
        hdr.pop("__metadata__", None)
        if not lora_alias.needs_alias(hdr):
            # ¿es un kohya sdxl ya usable, TE-only, u otra arquitectura?
            try:
                pairs, skipped = collect_pairs(hdr, adapter.ignored_lora_prefixes)
            except LoraFormatError:
                res["no_sdxl"] += 1
                continue
            if not pairs:
                res["te_only"] += 1
            elif all(m in key_map for m in pairs):
                res["kohya_ok"] += 1
            else:
                res["no_sdxl"] += 1
            continue
        try:
            renames = lora_alias.translate_keys(hdr)
            n_tr = sum(1 for a, b in renames.items() if a != b)
            res["convertible"].append({"file": rel, "translated_keys": n_tr,
                                       "keys": len(renames)})
        except lora_alias.LoraAliasError as e:
            res["rejected"].append({"file": rel, "reason": str(e)})

    ok(f"{res['total']} LoRAs escaneados en {time.time()-t0:.1f}s")
    ok(f"kohya canónico ya usable: {res['kohya_ok']} · otra arch/naming: "
       f"{res['no_sdxl']} · solo-TE: {res['te_only']} · ilegibles: {res['unreadable']}")
    ok(f"CONVERTIBLES con el alias: {len(res['convertible'])}")
    print(f"  · rechazados con motivo: {len(res['rejected'])}")
    for r in res["rejected"][:8]:
        print(f"      - {r['file']}\n        {r['reason'][:110]}")
    return 0, res


# ── Nivel 2 — conversión real + integridad + cross-check con checkpoint ───

def find_sdxl_checkpoint(models_root: Path) -> Path | None:
    """Primer checkpoint SDXL real del almacén (para el cross-check)."""
    ck_dir = models_root / "checkpoints"
    if not ck_dir.exists():
        return None
    for p in sorted(ck_dir.rglob("*.safetensors")):
        try:
            hdr, _ = read_header(p)
        except Exception:
            continue
        if any(k.startswith("model.diffusion_model.input_blocks.")
               for k in hdr):
            return p
    return None


def level2(models_root: Path, convertible: list[dict]) -> tuple[int, dict]:
    print("── Nivel 2: conversión + integridad + cross-check de mapeo")
    errors = 0
    loras_root = models_root / "loras"
    adapter = get_adapter("sdxl")
    key_map = adapter.lora_key_map()

    ckpt = find_sdxl_checkpoint(models_root)
    ckpt_hdr = None
    if ckpt:
        ckpt_hdr, _ = read_header(ckpt)
        ok(f"checkpoint de referencia: {ckpt.name}")
    else:
        print("  ⚠ sin checkpoint SDXL en el almacén: se omite el cross-check de dims")

    res = {"converted": 0, "failed": [], "bytes_checked": 0,
           "checkpoint_ref": ckpt.name if ckpt else None}
    t0 = time.time()
    for i, item in enumerate(convertible, 1):
        rel = item["file"]
        src = loras_root / rel
        try:
            alias_rel = lora_alias.ensure_alias(models_root, rel)
            dst = loras_root / alias_rel
            # 2a: integridad estructural — tensores/shapes/dtypes idénticos
            h_src, off_src = read_header(src)
            h_dst, off_dst = read_header(dst)
            m_src = h_src.pop("__metadata__", None)
            h_dst.pop("__metadata__", None)
            renames = lora_alias.translate_keys({**h_src, "__metadata__": m_src})
            for old, v in h_src.items():
                w = h_dst.get(renames[old])
                if w is None or v["shape"] != w["shape"] or v["dtype"] != w["dtype"]:
                    raise AssertionError(f"tensor {old!r} alterado en la copia")
            if len(h_src) != len(h_dst):
                raise AssertionError("número de tensores distinto")
            # 2b: bloque de datos byte-idéntico
            if sha256_from(src, off_src) != sha256_from(dst, off_dst):
                raise AssertionError("bloque de datos NO byte-idéntico")
            # 2c: cobertura (misma validación que la sesión) sobre la copia
            pairs, _ = collect_pairs(h_dst, adapter.ignored_lora_prefixes)
            unmapped = [m for m in pairs if m not in key_map]
            if not pairs or unmapped:
                raise AssertionError(f"cobertura de la copia falla: {unmapped[:3]}")
            # 2d: cross-check contra el checkpoint real — cada target existe
            # y sus dims casan con lora_up/lora_down (lo que verificaría el
            # merge_worker, pero sin torch)
            if ckpt_hdr:
                for m, parts in pairs.items():
                    tgt, _sl = key_map[m]
                    tv = ckpt_hdr.get(tgt)
                    if tv is None:
                        raise AssertionError(f"target inexistente: {tgt}")
                    up, down = h_dst[parts["B"]], h_dst[parts["A"]]
                    if (up["shape"][0] != tv["shape"][0]
                            or down["shape"][1] != tv["shape"][1]):
                        raise AssertionError(
                            f"dims incompatibles en {m}: up {up['shape']} / "
                            f"down {down['shape']} vs target {tv['shape']}")
            # 2e: idempotencia del cache (segunda llamada no reconvierte)
            mtime = dst.stat().st_mtime_ns
            if lora_alias.ensure_alias(models_root, rel) != alias_rel \
                    or dst.stat().st_mtime_ns != mtime:
                raise AssertionError("el cache reconvirtió sin cambios en el origen")
            res["converted"] += 1
            res["bytes_checked"] += src.stat().st_size
        except Exception as e:
            res["failed"].append({"file": rel, "error": f"{type(e).__name__}: {e}"})
        if i % 10 == 0 or i == len(convertible):
            print(f"  … {i}/{len(convertible)}")

    if res["failed"]:
        errors += fail(f"{len(res['failed'])} conversiones fallidas:")
        for f_ in res["failed"][:5]:
            print(f"      - {f_['file']}: {f_['error'][:110]}")
    ok(f"{res['converted']}/{len(convertible)} convertidos y verificados "
       f"({res['bytes_checked']/1e9:.2f} GB re-hasheados) en {time.time()-t0:.1f}s")
    return errors, res


# ── Nivel 3 — runtime: el nodo selectivo APLICA la copia (anti fallo mudo) ─

async def level3(models_root: Path, converted: list[dict], sample: int,
                 comfy_host: str, comfy_port: int) -> tuple[int, dict]:
    print(f"── Nivel 3: runtime todo-ON vs todo-OFF (muestra de {sample})")
    from forge_lab.core.comfy_client import ComfyClient, load_workflow
    from forge_lab.core import explore

    comfy = ComfyClient(comfy_host, comfy_port)
    if not await comfy.health_check():
        print("  ⚠ ComfyUI no responde — nivel 3 OMITIDO (arranca ComfyUI y repite)")
        return 0, {"skipped": "comfyui offline"}

    ckpt = find_sdxl_checkpoint(models_root)
    if not ckpt:
        print("  ⚠ sin checkpoint SDXL — nivel 3 OMITIDO")
        return 0, {"skipped": "sin checkpoint"}
    model_name = ckpt.relative_to(models_root / "checkpoints").as_posix()

    adapter = get_adapter("sdxl")
    sdef = vars(adapter.sampling_defaults())
    sdef.update({"steps": 12, "width": 832, "height": 832})
    errors, results = 0, []

    async def render(lora_rel: str, config: dict, exponent: int) -> bytes:
        import os
        template = load_workflow(adapter.workflow_name("txt2img_lora_selective"),
                                 "sdxl")
        config = explore.normalize_config(config, "sdxl")
        template["10"]["inputs"].update(
            explore.node_inputs(config, "sdxl", exponent))
        params = {"model": model_name,
                  "lora": lora_rel.replace("/", os.sep), "lora_strength": 1.0,
                  "prompt": "portrait of a woman in a garden, detailed",
                  "negative": "", "seed": 42, **sdef}
        return await comfy.run_workflow(template, params)

    def pixel_diff(a: bytes, b: bytes) -> float:
        try:
            import io
            from PIL import Image
            ia = Image.open(io.BytesIO(a)).convert("RGB")
            ib = Image.open(io.BytesIO(b)).convert("RGB")
            pa, pb = ia.tobytes(), ib.tobytes()
            return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)
        except ImportError:
            return -1.0 if a == b else 999.0   # sin PIL: igualdad de bytes

    for item in converted[:sample]:
        rel = item["file"]
        alias_rel = lora_alias.alias_rel_path(rel)
        hdr, _ = read_header(models_root / "loras" / alias_rel)
        exponent = adapter.dose_exponent(
            lora_has_alpha=any(k.endswith(".alpha") for k in hdr))
        try:
            img_on = await render(alias_rel, explore.full_config("sdxl"), exponent)
            off = {"blocks": {b: 0.0 for b in adapter.list_blocks()}, "other": 0.0}
            img_off = await render(alias_rel, off, exponent)
            d = pixel_diff(img_on, img_off)
            applied = d > 0.5 or d == 999.0
            results.append({"file": rel, "mean_abs_diff": round(d, 3),
                            "applied": applied})
            if applied:
                ok(f"{rel}: el LoRA convertido SÍ altera la imagen (diff {d:.2f})")
            else:
                errors += fail(f"{rel}: diff {d:.3f} — ¿fallo mudo?")
        except Exception as e:
            errors += fail(f"{rel}: {type(e).__name__}: {e}")
            results.append({"file": rel, "error": str(e)})
    return errors, {"results": results}


# ── main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--level", type=int, default=2, choices=(0, 1, 2, 3))
    ap.add_argument("--models-root", default=r"E:\Models")
    ap.add_argument("--sample", type=int, default=3,
                    help="LoRAs a probar en el nivel 3 (runtime)")
    ap.add_argument("--comfy-host", default="localhost")
    ap.add_argument("--comfy-port", type=int, default=8188)
    args = ap.parse_args()

    models_root = Path(args.models_root)
    report = {"date": time.strftime("%Y-%m-%d %H:%M:%S"),
              "models_root": str(models_root), "level": args.level}
    errors = 0

    e, report["level0"] = level0()
    errors += e
    if args.level >= 1:
        e, report["level1"] = level1(models_root / "loras")
        errors += e
    if args.level >= 2:
        e, report["level2"] = level2(models_root,
                                     report["level1"]["convertible"])
        errors += e
    if args.level >= 3:
        import asyncio
        conv_ok = [c for c in report["level1"]["convertible"]
                   if not any(f["file"] == c["file"]
                              for f in report["level2"]["failed"])]
        e, report["level3"] = asyncio.run(
            level3(models_root, conv_ok, args.sample,
                   args.comfy_host, args.comfy_port))
        errors += e

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"\nReporte: {REPORT_PATH}")
    print("RESULTADO: " + ("TODO OK ✔" if errors == 0 else f"{errors} FALLOS ✘"))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
