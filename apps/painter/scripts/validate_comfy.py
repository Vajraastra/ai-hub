"""
Smoke test standalone — valida el pipeline completo con ComfyUI.
Prueba txt2img e inpaint end-to-end.

Uso:
    python validate_comfy.py
    python validate_comfy.py --checkpoint nombre.safetensors
    python validate_comfy.py --arch sdxl
    python validate_comfy.py --host localhost --port 8188
"""
import sys
import asyncio
import argparse
import base64
from pathlib import Path
from io import BytesIO

# Añadir core al path
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from comfy_client import ComfyClient, ComfyError, load_workflow, SUPPORTED_ARCHITECTURES

OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "outputs" / "painter_validate"


def _make_test_mask_b64(width: int = 512, height: int = 512) -> str:
    """Crea una máscara simple: franja central blanca (zona a inpaintar)."""
    try:
        from PIL import Image
        img = Image.new("L", (width, height), 0)
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.rectangle([width//4, height//4, width*3//4, height*3//4], fill=255)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        # fallback: PNG 1x1 negro (máscara vacía — inpaint no hará nada pero valida el pipeline)
        EMPTY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        return EMPTY_PNG


async def run_tests(host: str, port: int, checkpoint: str | None, arch: str = "sdxl"):
    client = ComfyClient(host=host, port=port)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nArquitectura: {arch.upper()}")

    # ── Verificar conexión ─────────────────────────────────────────────────
    print(f"\n[1/4] Verificando conexión a ComfyUI en {host}:{port}…")
    if not await client.health_check():
        print("  FAIL — ComfyUI no responde")
        sys.exit(1)
    print("  OK")

    # ── Seleccionar checkpoint ─────────────────────────────────────────────
    print("\n[2/4] Buscando checkpoints disponibles…")
    checkpoints = await client.get_models("checkpoints")
    if not checkpoints:
        print("  FAIL — No hay checkpoints instalados")
        sys.exit(1)

    if checkpoint:
        if checkpoint not in checkpoints:
            print(f"  FAIL — Checkpoint '{checkpoint}' no encontrado")
            print(f"  Disponibles: {checkpoints}")
            sys.exit(1)
        ckpt = checkpoint
    else:
        ckpt = checkpoints[0]

    print(f"  Usando: {ckpt}")

    # ── Test txt2img ───────────────────────────────────────────────────────
    print(f"\n[3/4] Test txt2img ({arch})…")
    wf = load_workflow("txt2img.json", arch)
    params = {
        "checkpoint":       ckpt,
        "prompt":           "a red apple on a white table, simple, clean",
        "negative_prompt":  "blurry, low quality",
        "width":            512,
        "height":           512,
        "seed":             42,
        "steps":            10,
        "cfg":              7.0,
        "sampler":          "euler",
        "scheduler":        "normal",
    }

    last_step = [0]
    def on_progress(step, total):
        if step != last_step[0]:
            print(f"  paso {step}/{total}", end="\r", flush=True)
            last_step[0] = step

    try:
        img_bytes = await client.run_workflow(wf, params, on_progress)
        out_path  = OUTPUT_DIR / "txt2img_result.png"
        out_path.write_bytes(img_bytes)
        print(f"\n  OK — guardado en {out_path} ({len(img_bytes)//1024} KB)")
    except ComfyError as e:
        print(f"\n  FAIL — {e}")
        sys.exit(1)

    # ── Test inpaint (básico — no requiere comfyui-inpaint-nodes) ──────────
    print(f"\n[4/4] Test inpaint básico ({arch})…")

    img_b64    = base64.b64encode(img_bytes).decode()
    msk_b64    = _make_test_mask_b64(512, 512)
    wf_inpaint = load_workflow("inpaint_basic.json", arch)
    params_inpaint = {
        "checkpoint":       ckpt,
        "prompt":           "a green pear on a white table, simple, clean",
        "negative_prompt":  "blurry, low quality",
        "image_b64":        img_b64,
        "mask_b64":         msk_b64,
        "seed":             123,
        "steps":            10,
        "cfg":              7.0,
        "sampler":          "euler",
        "scheduler":        "normal",
        "denoise":          0.85,
    }

    last_step[0] = 0
    try:
        img_bytes2 = await client.run_workflow(wf_inpaint, params_inpaint, on_progress)
        out_path2  = OUTPUT_DIR / "inpaint_result.png"
        out_path2.write_bytes(img_bytes2)
        print(f"\n  OK — guardado en {out_path2} ({len(img_bytes2)//1024} KB)")
    except ComfyError as e:
        print(f"\n  FAIL — {e}")
        sys.exit(1)

    print("\n✓ Todos los tests pasaron. Pipeline ComfyUI validado.\n")


def main():
    parser = argparse.ArgumentParser(description="Valida el pipeline de Painter con ComfyUI")
    parser.add_argument("--host",       default="localhost")
    parser.add_argument("--port",       type=int, default=8188)
    parser.add_argument("--checkpoint", default=None,
                        help="Nombre del checkpoint a usar (default: primero disponible)")
    parser.add_argument("--arch",       default="sdxl",
                        choices=SUPPORTED_ARCHITECTURES,
                        help=f"Arquitectura de modelo (default: sdxl)")
    args = parser.parse_args()
    asyncio.run(run_tests(args.host, args.port, args.checkpoint, args.arch))


if __name__ == "__main__":
    main()
