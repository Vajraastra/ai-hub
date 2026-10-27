"""Face crop and final RGBA composition, independent from the image generator."""
import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter

MAX_PIXELS = 16_777_216


def png_bytes(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def open_image(data, mode="RGBA"):
    if not isinstance(data, bytes) or not data:
        raise ValueError("Falta la imagen")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_PIXELS or max(image.size) > 8192:
            raise ValueError("La imagen excede el límite de 16 megapíxeles")
        image.load()
        return image.convert(mode)


@dataclass
class ExpressionCrop:
    master: Image.Image
    composite_mask: Image.Image
    generation_mask: Image.Image
    rect: tuple
    render_size: tuple
    image: bytes
    mask: bytes


def prepare(master_bytes, mask_bytes, padding=64, feather=8):
    master = open_image(master_bytes)
    with Image.open(io.BytesIO(mask_bytes)) as source:
        if source.size != master.size:
            raise ValueError("La máscara debe tener exactamente el tamaño del maestro")
        source.load()
        mask = source.convert("L")
        if source.mode == "RGBA":
            mask = ImageChops.multiply(mask, source.getchannel("A"))
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError("Dibuja una zona facial en la máscara")
    padding = int(padding)
    feather = float(feather)
    if not 0 <= padding <= 512 or not 0 <= feather <= 64:
        raise ValueError("El contexto o suavizado de la máscara es inválido")
    # Multiplication clips all blur back to the authorised support. A Gaussian
    # alone would modify pixels outside the user's region.
    composite = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather)))
    generation = mask.filter(ImageFilter.MaxFilter(17))
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(master.width, bbox[2] + padding)
    bottom = min(master.height, bbox[3] + padding)
    rect = (left, top, right, bottom)
    crop = master.crop(rect)
    # Give small faces enough latent resolution while keeping the exact inverse
    # transform for final composition. Both axes are valid VAE multiples.
    scale = max(1.0, 512 / max(crop.size))
    scale = min(scale, 1024 / max(crop.size))
    size = tuple(max(16, round(value * scale / 16) * 16) for value in crop.size)
    image = crop.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    crop_mask = generation.crop(rect).resize(size, Image.Resampling.NEAREST)
    return ExpressionCrop(master, composite, generation, rect, size,
                          png_bytes(image), png_bytes(crop_mask.convert("RGB")))


def compose(crop, rendered_bytes):
    rendered = open_image(rendered_bytes)
    if rendered.size != crop.render_size:
        raise ValueError("ComfyUI devolvió dimensiones distintas a las solicitadas")
    left, top, right, bottom = crop.rect
    patch = rendered.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
    # The original silhouette's alpha is immutable, even inside the face.
    patch.putalpha(crop.master.getchannel("A").crop(crop.rect))
    result = crop.master.copy()
    result.paste(patch, (left, top), crop.composite_mask.crop(crop.rect))
    outside = Image.eval(crop.composite_mask, lambda value: 255 if value == 0 else 0)
    difference = ImageChops.difference(result, crop.master)
    for channel in difference.split():
        if ImageChops.multiply(channel, outside).getbbox():
            raise ValueError("La composición modificó píxeles fuera de la máscara")
    return {"image": png_bytes(result), "patch": png_bytes(patch),
            "mask": png_bytes(crop.composite_mask),
            "generation_mask": png_bytes(crop.generation_mask),
            "details": {"crop_rect": list(crop.rect), "render_size": list(crop.render_size),
                        "canvas_size": list(crop.master.size),
                        "outside_mask_identical": True, "alpha_preserved": True}}
