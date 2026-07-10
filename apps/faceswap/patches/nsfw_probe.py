"""
Probe de medición del filtro NSFW — corre SOLO el clasificador sobre una imagen
y reporta el score, sin hacer ningún swap. Se ejecuta con el venv de ComfyUI
(que tiene transformers + torch + el modelo). Uso:

    python nsfw_probe.py <imagen> <ruta_modelo>

Imprime una línea JSON: {"score": 0.xxxx, "all": [...]}. En CPU a propósito:
la calibración es esporádica y así no compite por VRAM con ComfyUI.
"""
import sys
import json

from transformers import pipeline
from PIL import Image


def main():
    img_path, model_path = sys.argv[1], sys.argv[2]
    clf = pipeline("image-classification", model=model_path, device=-1)
    with Image.open(img_path) as im:
        res = clf(im.convert("RGB"))
    score = next((r["score"] for r in res if r["label"] == "nsfw"), 0.0)
    print(json.dumps({"score": round(float(score), 4), "all": res}))


if __name__ == "__main__":
    main()
