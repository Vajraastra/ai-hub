r"""
Smoke test E2E del MODO DE INICIO Ref2V de una cadena (s96) -- via HTTP real
contra un hub-webui de prueba (:8799) y ComfyUI real (:8188). Crea una cadena
con `ref_start` (2 imágenes de referencia -> tramo 1 vía ref2va), la acepta,
agrega un tramo 2 (debe encadenar vía fl2va+I2V desde el último frame del
tramo 1, IGUAL que cualquier cadena normal), exporta y verifica. NO toca
pipeline.py/chains.py -- solo ejercita las rutas ya escritas.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\smoke_ref2v_start_e2e.py
"""
import sys
import time
import base64
import requests
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8799/api/minimax-h3"
_HERE = Path(__file__).resolve().parent
_FRAMES = _HERE.parent / "data" / "continuity_frames"


def _get(p):
    r = requests.get(BASE+p, timeout=30); r.raise_for_status(); return r.json()

def _post(p, body=None):
    r = requests.post(BASE+p, json=body or {}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"{p} -> {r.status_code}: {r.text}")
    return r.json()

def _delete(p):
    r = requests.delete(BASE+p, timeout=30); r.raise_for_status(); return r.json()


def poll_job(job_id, label):
    t0 = time.time()
    while True:
        j = _get(f"/jobs/{job_id}")
        if j["status"] == "running":
            step = j.get("step", 0); total = j.get("steps_total", 0)
            print(f"   · {label} [{j.get('phase')}] {step}/{total}  ({time.time()-t0:.0f}s)", flush=True)
        elif j["status"] == "done":
            print(f"✓ {label} listo: run_id={j['run_id']} ({time.time()-t0:.0f}s)")
            return j["run_id"]
        elif j["status"] == "error":
            raise RuntimeError(f"{label} falló: {j['error']}")
        time.sleep(4)


def main():
    ms_ref2v = _get("/models/status/ref2v")
    if not ms_ref2v["ready"]:
        print(f"Faltan modelos Ref2V: {ms_ref2v['missing']}"); return

    ref_images = [
        base64.b64encode((_FRAMES / "base_last_frame.png").read_bytes()).decode("ascii"),
        base64.b64encode((_FRAMES / "i2v_continuation_first_frame.png").read_bytes()).decode("ascii"),
    ]

    print("== crear cadena con ref_start (2 imágenes de referencia) ==")
    chain = _post("/chains", {
        "name": "Smoke Ref2V-start E2E",
        "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "video_vae_name": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae_name": "minimax_h3_audio_vae_fp32.safetensors",
        "width": 1344, "height": 768,
        "turbo": True,
        "turbo_lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        "turbo_strength": 1.0, "turbo_steps": 8, "steps": 20,
        "loras": [],
        "ref_start": {
            "unet_name": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
            "turbo_lora_name": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
            "turbo_strength": 1.0, "turbo_steps": 4,
            "ref_images_b64": ref_images,
        },
    })
    cid = chain["id"]
    print(f"chain_id={cid} start_mode={chain['start']['mode']}")

    print("\n== tramo 1 (Ref2V, 2 referencias): agregar + generar ==")
    seg1 = _post(f"/chains/{cid}/segments", {
        "prompt": ("Use <Picture 1> as the reference for the person's identity and clothing. "
                   "Static wide shot, the person from <Picture 1> standing still in an empty gray "
                   "studio, looking at the camera. Audio: quiet room tone."),
        "duration_s": 3.0, "seed": 777,
    })
    r = _post(f"/chains/{cid}/segments/{seg1['id']}/generate", {})
    run1 = poll_job(r["job_id"], "tramo1(ref2v)")
    _post(f"/chains/{cid}/segments/{seg1['id']}/accept", {"run_id": run1})

    print("\n== tramo 2 (debe encadenar vía fl2va+I2V desde el último frame del tramo 1) ==")
    seg2 = _post(f"/chains/{cid}/segments", {
        "prompt": ("The same person continues standing in the empty gray studio, a slight head "
                   "turn, same lighting, static camera. Audio: quiet room tone continues."),
        "duration_s": 3.0, "seed": 778,
    })
    r = _post(f"/chains/{cid}/segments/{seg2['id']}/generate", {})
    run2 = poll_job(r["job_id"], "tramo2(fl2va-i2v)")
    _post(f"/chains/{cid}/segments/{seg2['id']}/accept", {"run_id": run2})

    print("\n== exportar clip final ==")
    exp = _post(f"/chains/{cid}/export", {})
    meta = _get(f"/history/{exp['run_id']}/meta")
    print(f"✓ export run_id={exp['run_id']} duration_s={meta['duration_s']}")

    # Verificación cualitativa: el render_params del tramo 1 debe traer ref2v=True
    seg1_meta = _get(f"/history/{run1}/meta")
    assert seg1_meta["render"].get("ref2v") is True, seg1_meta["render"]
    assert seg1_meta["render"].get("ref_image_count") == 2, seg1_meta["render"]
    print(f"✓ tramo1 confirmado como Ref2V con {seg1_meta['render']['ref_image_count']} referencias")

    seg2_meta = _get(f"/history/{run2}/meta")
    assert "ref2v" not in seg2_meta["render"], seg2_meta["render"]
    print("✓ tramo2 confirmado como fl2va (sin marca ref2v)")

    print("\n== limpieza ==")
    _delete(f"/chains/{cid}")
    print("✓ cadena de prueba borrada")

    print("\n" + "="*60)
    print("SMOKE TEST REF2V-START E2E: OK")
    print("="*60)


if __name__ == "__main__":
    main()
