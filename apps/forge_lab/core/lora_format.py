"""
Parseo del formato de claves de un LoRA (compartido por merge_worker y la
validación de sesión de exploración). Stdlib puro: solo mira NOMBRES de
claves, nunca carga tensores — sirve para validar leyendo el header del
safetensors sin torch.

Formatos soportados (mismos que ComfyUI en model_lora_keys_unet):
  - PEFT/diffusers: <prefijo>modulo.lora_A.weight / .lora_B.weight, con
    prefijo contenedor diffusion_model. / transformer. (Z-Image y cía.)
  - kohya: lora_unet_modulo.lora_down.weight / .lora_up.weight / .alpha
    (SDXL/Civitai). lora_down ≡ A, lora_up ≡ B.
"""

# Prefijos contenedor del formato PEFT según la herramienta de entrenamiento.
# El formato kohya conserva su prefijo (lora_unet_…) como parte del módulo.
PEFT_PREFIXES = ("diffusion_model.", "transformer.")
PEFT_SUFFIXES = {".lora_A.weight": "A", ".lora_B.weight": "B"}
KOHYA_SUFFIXES = {".lora_down.weight": "A", ".lora_up.weight": "B"}


class LoraFormatError(ValueError):
    """Clave con formato desconocido/no soportado (DoRA, diff, strays)."""


def collect_pairs(lora_keys, ignored_prefixes):
    """Agrupa claves del LoRA por módulo → {"A": key, "B": key, "alpha": key?}.
    Devuelve (pairs, skipped_policy): los módulos con prefijo ignorable
    (text encoders en sdxl) se apartan en vez de tratarse como error.
    Lanza LoraFormatError si hay claves de formato no soportado."""
    pairs: dict[str, dict] = {}
    skipped: set[str] = set()
    strays = []
    for k in lora_keys:
        if ".dora_" in k or k.endswith(".diff") or k.endswith(".diff_b"):
            raise LoraFormatError(f"clave {k!r}: formato DoRA/diff no soportado")
        if k.endswith(".alpha"):
            module, part = k[: -len(".alpha")], "alpha"
        else:
            suffix = next((s for s in {**PEFT_SUFFIXES, **KOHYA_SUFFIXES}
                           if k.endswith(s)), None)
            if suffix is None:
                strays.append(k)
                continue
            module = k[: -len(suffix)]
            part = (PEFT_SUFFIXES.get(suffix) or KOHYA_SUFFIXES[suffix])
        if any(module.startswith(p) for p in ignored_prefixes):
            skipped.add(module)
            continue
        for p in PEFT_PREFIXES:
            if module.startswith(p):
                module = module[len(p):]
                break
        pairs.setdefault(module, {})[part] = k
    if strays:
        raise LoraFormatError(
            f"claves LoRA con formato desconocido: {strays[:5]}"
            f"{' …' if len(strays) > 5 else ''}")
    for module, parts in pairs.items():
        if not {"A", "B"} <= set(parts):
            raise LoraFormatError(f"módulo {module!r}: par lora down/up incompleto")
    return pairs, skipped
