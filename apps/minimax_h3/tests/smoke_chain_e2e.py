r"""
Smoke test E2E del encadenado de tramos v2 (s96) -- via HTTP real contra un
hub-webui de prueba (:8799) y ComfyUI real (:8188). Crea una cadena, genera
2 tramos cortos (GPU real, encadenados vía I2V), acepta cada version, exporta
el clip final y verifica el resultado con ffprobe. NO toca pipeline.py.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\smoke_chain_e2e.py
"""
import sys
import time
import requests
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8799/api/minimax-h3"
_HERE = Path(__file__).resolve().parent
_APPS = _HERE.parent.parent
sys.path.insert(0, str(_APPS))
import imageio_ffmpeg
import subprocess


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
    ms = _get("/models/status")
    if not ms["ready"]:
        print(f"Faltan modelos: {ms['missing']}"); return

    print("== crear cadena ==")
    chain = _post("/chains", {
        "name": "Smoke E2E",
        "unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "video_vae_name": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae_name": "minimax_h3_audio_vae_fp32.safetensors",
        "width": 1344, "height": 768,
        "turbo": True,
        "turbo_lora_name": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        "turbo_strength": 1.0, "turbo_steps": 8, "steps": 20,
        "loras": [],
    })
    cid = chain["id"]
    print(f"chain_id={cid}")

    print("\n== tramo 1: agregar + generar ==")
    seg1 = _post(f"/chains/{cid}/segments", {
        "prompt": ("Static wide shot of a red vintage bicycle leaning against a brick wall in a "
                   "sunlit alley, dust particles floating in the light, no people. Audio: distant "
                   "city ambience, soft wind."),
        "duration_s": 3.0, "seed": 555,
    })
    r = _post(f"/chains/{cid}/segments/{seg1['id']}/generate", {})
    run1 = poll_job(r["job_id"], "tramo1")

    print("== tramo 1: aceptar ==")
    _post(f"/chains/{cid}/segments/{seg1['id']}/accept", {"run_id": run1})

    print("\n== tramo 2: agregar + generar (debe continuar del último frame de tramo 1) ==")
    seg2 = _post(f"/chains/{cid}/segments", {
        "prompt": ("The camera holds on the same red vintage bicycle against the brick wall, "
                   "a gentle gust moves the dust particles more, light shifts slightly. Audio: "
                   "distant city ambience continues, soft wind."),
        "duration_s": 3.0, "seed": 556,
    })
    r = _post(f"/chains/{cid}/segments/{seg2['id']}/generate", {})
    run2 = poll_job(r["job_id"], "tramo2")

    print("== tramo 2: aceptar ==")
    _post(f"/chains/{cid}/segments/{seg2['id']}/accept", {"run_id": run2})

    print("\n== exportar clip final ==")
    exp = _post(f"/chains/{cid}/export", {})
    print(f"✓ export run_id={exp['run_id']}")

    # Verificar el archivo final con ffprobe (via ffmpeg -i, imageio-ffmpeg no trae ffprobe aparte)
    meta = _get(f"/history/{exp['run_id']}/meta")
    video_path = _APPS / "minimax_h3" / "data" / "history" / f"{exp['run_id']}.{meta['ext']}"
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    p = subprocess.run([exe, "-i", str(video_path)], capture_output=True, text=True)
    info_lines = [l.strip() for l in p.stderr.splitlines() if "Duration" in l or "Stream" in l]
    print("\n== verificación del clip exportado ==")
    print(f"archivo: {video_path} ({video_path.stat().st_size/1e6:.2f} MB)")
    for l in info_lines:
        print(" ", l)

    print("\n== limpieza: borrar la cadena de prueba (los clips quedan en Historial) ==")
    _delete(f"/chains/{cid}")
    print("✓ cadena de prueba borrada")

    print("\n" + "="*60)
    print("SMOKE TEST E2E: OK")
    print("="*60)


if __name__ == "__main__":
    main()
