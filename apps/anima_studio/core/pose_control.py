"""Validated pose maps and native Anima LLLite model patches; no GPU imports."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import struct
import uuid

import aiohttp
from PIL import Image, ImageDraw, ImageOps

MAX_MAP_BYTES = 4 * 1024 * 1024
MAX_MODEL_BYTES = 200 * 1024 * 1024
# Pose is intentionally a soft control, trained against Preview3. The native
# loader consumes the named-key v2 LLLite format, independent of model release.
MODELS = {
    "kohya-pose": {
        "id": "kohya-pose", "name": "Anima · esqueleto (experimental)",
        "file": "anima/anima-lllite-pose-1.safetensors", "representation": "openpose",
        "url": "https://huggingface.co/kohya-ss/Anima-LLLite/resolve/83218cc5a19962b25dc4b9ebdf144fbc0cf2b082/anima-lllite-pose-1.safetensors",
        "sha256": "ddb543be5e74ce8ca79ae45807aa0c1328a4888e00706727c6fd7100dde7c864",
        "size_bytes": 23310040,
        "source": "https://huggingface.co/kohya-ss/Anima-LLLite/blob/main/PREVIEW3.md",
        "note": "Control suave de esqueleto; comprobar el resultado con el modelo elegido.",
    },
}
LIMBS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8),
         (8, 9), (9, 10), (1, 11), (11, 12), (12, 13), (1, 0),
         (0, 14), (14, 16), (0, 15), (15, 17)]
COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0),
          (170, 255, 0), (85, 255, 0), (0, 255, 0), (0, 255, 85),
          (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255),
          (0, 0, 255), (85, 0, 255), (170, 0, 255), (255, 0, 255),
          (255, 0, 170), (255, 0, 85)]


def relative_model(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Selecciona un modelo de control de pose.")
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (path.is_absolute() or ":" in value or any(part in ("", ".", "..") for part in value.split("/"))
            or path.suffix.lower() != ".safetensors"):
        raise ValueError("Ruta de control de pose no válida.")
    return value


def image_bytes(value):
    if not isinstance(value, str):
        raise ValueError("Falta el mapa PNG de pose.")
    encoded = value
    if value.startswith("data:"):
        if not value.startswith("data:image/png;base64,"):
            raise ValueError("El mapa de pose debe ser PNG.")
        encoded = value.split(",", 1)[1]
    if len(encoded) > ((MAX_MAP_BYTES + 2) // 3) * 4:
        raise ValueError("El mapa de pose supera 4 MiB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_MAP_BYTES:
            raise ValueError()
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format != "PNG" or min(image.size) < 16 or max(image.size) > 2048
                    or image.width * image.height > 3_145_728):
                raise ValueError()
            image.verify()
    except Exception as exc:
        raise ValueError("Mapa de pose PNG inválido: usa hasta 2048 píxeles por lado y 3 megapíxeles.") from exc
    return raw


def validate_config(value):
    """Return an isolated, canonical config or None for explicitly disabled control."""
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("enabled", True)) is not bool:
        raise ValueError("Configuración de control de pose inválida.")
    if not value.get("enabled", True):
        return None
    representation = value.get("representation", "openpose")
    if representation not in ("openpose", "render"):
        raise ValueError("Representación de pose desconocida.")
    result = {"enabled": True, "representation": representation,
              "model_patch": relative_model(value.get("model_patch"))}
    raw = image_bytes(value.get("image_base64"))
    result["image_base64"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    for key, default in (("strength", 1.0), ("start_percent", 0.0), ("end_percent", 1.0)):
        val = value.get(key, default)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError("Fuerza o intervalo de control de pose inválido.")
        if not 0 <= val <= (3 if key == "strength" else 1):
            raise ValueError("Usa fuerza de pose entre 0 y 3 e intervalo entre 0 y 1.")
        result[key] = float(val)
    if result["start_percent"] >= result["end_percent"]:
        raise ValueError("El final del control de pose debe ser posterior al inicio.")
    if value.get("skeleton") is not None:
        result["skeleton"] = validate_skeleton(value["skeleton"])
    return result


def validate_skeleton(value):
    if not isinstance(value, dict) or value.get("format") != "openpose18":
        raise ValueError("Esqueleto de pose desconocido.")
    points = value.get("points")
    if not isinstance(points, list) or len(points) != 18:
        raise ValueError("El esqueleto requiere 18 articulaciones.")
    checked = []
    for point in points:
        if (not isinstance(point, list) or len(point) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or not 0 <= v <= 1 for v in point)):
            raise ValueError("Articulación de pose inválida.")
        checked.append(list(point))
    return {"format": "openpose18", "points": checked}


def render_map(value, width, height):
    """Render normalized joints on the destination canvas, or pad an imported map.

    ComfyUI's patch node center-crops mismatched ratios. Preparing the map here
    avoids silently cutting limbs off when changing a generation format.
    """
    if value.get("skeleton") is not None:
        skeleton = validate_skeleton(value["skeleton"])
        image = Image.new("RGB", (width, height), "black")
        draw = ImageDraw.Draw(image)
        points = [(round(x * (width - 1)), round(y * (height - 1))) for x, y in skeleton["points"]]
        thickness = max(2, round(min(width, height) / 128))
        for index, (start, end) in enumerate(LIMBS):
            draw.line((points[start], points[end]), fill=COLORS[index], width=thickness)
        radius = thickness
        for index, (x, y) in enumerate(points):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=COLORS[index])
    else:
        with Image.open(io.BytesIO(image_bytes(value["image_base64"]))) as source:
            fitted = ImageOps.contain(source.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
            image = Image.new("RGB", (width, height), "black")
            image.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def patch_header(path):
    with Path(path).open("rb") as stream:
        length_bytes = stream.read(8)
        if len(length_bytes) != 8:
            raise ValueError("Archivo de control incompleto.")
        length = struct.unpack("<Q", length_bytes)[0]
        if not 2 <= length <= 8 * 1024 * 1024 or length + 8 >= Path(path).stat().st_size:
            raise ValueError("Cabecera de control inválida.")
        header = json.loads(stream.read(length))
    if not isinstance(header, dict):
        raise ValueError("Cabecera de control inválida.")
    entry = header.get("lllite_conditioning1.conv1.weight", {})
    shape = entry.get("shape", []) if isinstance(entry, dict) else []
    metadata = header.get("__metadata__", {})
    if not isinstance(metadata, dict) or metadata.get("lllite.version", "2") != "2":
        raise ValueError("El control no tiene formato Anima LLLite v2.")
    if len(shape) != 4 or shape[1] != 3:
        raise ValueError("El archivo no es un control RGB de pose Anima.")
    modules = [entry for key, entry in header.items()
               if key.startswith("lllite_dit_blocks_") and key.endswith(".down.weight")]
    if not any(isinstance(entry, dict) and len(entry.get("shape", [])) == 2
               and entry["shape"][1] == 2048 for entry in modules):
        raise ValueError("El archivo no es un control de Anima compatible.")
    return header


def _path(root, value):
    base = (Path(root).resolve() / "model_patches").resolve()
    target = base.joinpath(*PurePosixPath(relative_model(value)).parts)
    # Reject links inside model_patches, including the directory itself. We only
    # install/read explicitly owned files, not aliases to an unrelated location.
    if not base.is_relative_to(Path(root).resolve()) or not target.resolve().is_relative_to(base):
        raise ValueError("El control sale de la biblioteca de modelos.")
    return target


def validate_model_patch(root, value):
    if not value:
        return None
    filename = relative_model(value.get("model_patch"))
    path = _path(root, filename)
    if not path.is_file():
        raise ValueError("Instala el modelo de control de pose desde su editor.")
    patch_header(path)
    known = next((model for model in MODELS.values() if model["file"] == filename), None)
    if known and known["representation"] != value.get("representation", "openpose"):
        raise ValueError("El modelo elegido requiere un esqueleto OpenPose, no un render de maniquí.")
    stat = path.stat()
    if known and stat.st_size != known["size_bytes"]:
        raise ValueError("El control de pose está incompleto o corresponde a otra versión.")
    digest = None
    if known:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != known["sha256"]:
            raise ValueError("El control de pose no coincide con la versión publicada.")
    return {"file": filename, "size_bytes": stat.st_size,
            "fingerprint": f"{stat.st_size}:{stat.st_mtime_ns}", "sha256": digest}


def catalog(root):
    models = []
    for model in MODELS.values():
        item = copy.deepcopy(model)
        item.pop("url")
        try:
            validate_model_patch(root, {"model_patch": model["file"], "representation": model["representation"]})
            item["installed"] = True
        except (OSError, ValueError, TypeError, KeyError):
            item["installed"] = False
        models.append(item)
    return {"models": models, "native_node": "AnimaLLLiteApply", "experimental": True}


async def install(root, model_id):
    model = MODELS.get(model_id)
    if model is None:
        raise ValueError("Modelo de pose desconocido.")
    target = _path(root, model["file"])
    if target.is_file():
        try:
            validate_model_patch(root, {"model_patch": model["file"]})
            return {"id": model_id, "installed": True, "existing": True}
        except ValueError as exc:
            raise ValueError("El archivo existente está dañado; consérvalo para revisión antes de reemplazarlo.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    # Recheck after mkdir in case an alias was introduced while preparing folders.
    target = _path(root, model["file"])
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        digest = hashlib.sha256()
        total = 0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180, sock_read=30)) as session:
            async with session.get(model["url"]) as response:
                response.raise_for_status()
                if response.content_length is not None and response.content_length > MAX_MODEL_BYTES:
                    raise ValueError("El modelo de pose supera el tamaño previsto.")
                with temporary.open("xb") as stream:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        total += len(chunk)
                        if total > MAX_MODEL_BYTES:
                            raise ValueError("El modelo de pose supera el tamaño previsto.")
                        digest.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
        if total != model["size_bytes"] or digest.hexdigest() != model["sha256"]:
            raise ValueError("La descarga del control no coincide con la versión publicada; vuelve a intentarlo.")
        patch_header(temporary)
        _path(root, model["file"])
        os.replace(temporary, target)
        return {"id": model_id, "installed": True, "sha256": digest.hexdigest(), "size_bytes": total}
    finally:
        temporary.unlink(missing_ok=True)
