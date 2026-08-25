r"""
Descarga puntual del text encoder Qwen3-VL-32B en INT8 ConvRot (s96) --
candidato para probar si la cuantizacion agresiva del encoder (NVFP4/AWQ)
contribuye a la perdida de identidad en Ref2V. Ver TASKS.md para el contexto
completo (incluye el hallazgo de que H3-Context-IR, hosteado, nunca se
libero -- este test probablemente da como maximo una mejora marginal).
NO requiere ComfyUI -- descarga HTTP pura.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\download_encoder_int8.py
"""
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))
from minimax_h3.core import model_downloader as dl

_MODELS_ROOT = Path("E:/Models")


def main():
    missing = dl.missing_specs(_MODELS_ROOT, dl.TEXT_ENCODER_INT8_CONVROT)
    if not missing:
        print("Ya está descargado.")
        print("✓ descarga completa")
        return
    t0 = time.time()
    last_pct = -5
    for ev in dl.download_specs(_MODELS_ROOT, dl.TEXT_ENCODER_INT8_CONVROT):
        if ev["type"] == "file_start":
            print(f"[{ev['index']}/{ev['total_files']}] {ev['label']} "
                  f"({ev['size_bytes']/1e9:.1f} GB)")
        elif ev["type"] == "progress":
            if ev["pct"] - last_pct >= 5:
                last_pct = ev["pct"]
                print(f"  {ev['pct']:5.1f}%  {ev['bytes_done']/1e9:.2f}/{ev['bytes_total']/1e9:.2f} GB "
                      f"({time.time()-t0:.0f}s)", flush=True)
        elif ev["type"] == "file_done":
            print(f"✓ listo ({time.time()-t0:.0f}s)")
        elif ev["type"] == "error":
            print(f"✗ error: {ev['msg']}")
    print("✓ descarga completa")


if __name__ == "__main__":
    main()
