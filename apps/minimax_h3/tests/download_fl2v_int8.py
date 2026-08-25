r"""
Descarga puntual de la variante Mixed INT4/INT8 Convrot de FL2VA (s96) --
DEPRECADO desde que el usuario confirmo INT8 puro como ganador claro (ver
FL2V_MIXED_REJECTED en model_downloader.py). El INT8 puro ya es el DiT de
GATED_MODELS (dl.missing_specs/download_specs con GATED_MODELS lo descarga).
Este script queda solo por si hace falta re-descargar Mixed para alguna
comparacion futura. NO requiere ComfyUI -- descarga HTTP pura.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\download_fl2v_int8.py
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
    missing = dl.missing_specs(_MODELS_ROOT, dl.FL2V_MIXED_REJECTED)
    if not missing:
        print("Ya está descargado.")
        return
    t0 = time.time()
    last_pct = -5
    for ev in dl.download_specs(_MODELS_ROOT, dl.FL2V_MIXED_REJECTED):
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
    # `download_specs()` (a diferencia del wrapper SSE de las rutas) no emite
    # un evento "all_done" propio -- el marcador de fin lo ponemos acá, tras
    # agotar el generador, para que un `until grep` externo tenga una señal
    # real de "terminó" sin importar cuántos archivos se hayan descargado.
    print("✓ descarga completa")


if __name__ == "__main__":
    main()
