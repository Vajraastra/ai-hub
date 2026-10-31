"""Resolve prompt pieces without changing their saved design or style settings."""
from __future__ import annotations

import copy

from . import piece_content


ORDER = ("style", "character", "personality", "outfit", "pose", "expression", "background", "item", "scene")
INTEGRATION_FRAMING = {
    "full-body": "Full body sprite, entire figure visible including feet, generous margins.",
    "waist-up": "Waist-up sprite, framed from the waist to the head.",
    "portrait": "Close-up character portrait, framed around the head and shoulders.",
}
TEST_TEMPLATES = {
    "character": ("Single character design study, neutral standing posture, plain fitted base clothing, "
                  "entire figure visible, simple neutral background.", "text, watermark, collage, multiple views"),
    "outfit": ("Clothing design displayed on one featureless neutral mannequin, neutral standing posture, "
               "entire outfit visible including shoes and accessories, simple neutral background.",
               "detailed face, hair, text, watermark, collage, multiple views"),
    "pose": ("Single featureless neutral mannequin demonstrating the selected posture, entire figure visible, "
             "simple neutral background.", "text, watermark, collage, multiple views"),
    "expression": ("Single front-facing portrait, facial expression study, simple neutral background.",
                   "text, watermark, collage, multiple views"),
    "background": ("Environment illustration, readable large shapes, space for foreground characters, "
                   "no people, no text or interface.", "people, characters, text, watermark, interface"),
    "scene": ("Environment illustration, readable large shapes, space for foreground characters, "
              "no people, no text or interface.", "people, characters, text, watermark, interface"),
    "item": ("One isolated object, fully visible and centered with generous margins, simple plain background, "
             "readable silhouette at small size.", "person, mannequin, text, watermark, interface, collage"),
    "personality": ("Single character portrait expressing the described personality, simple neutral background.",
                    "text, watermark, collage, multiple views"),
}


def merge_loras(style_loras, pieces):
    """An identical shared LoRA runs once; contradictory strengths require a decision."""
    result, seen = [], {}
    if not isinstance(style_loras, list):
        raise ValueError("Los LoRAs del estilo deben ser una lista.")
    entries = list(style_loras)
    for piece in pieces:
        entries.extend(piece.get("loras", []))
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("file"), str)
                or not entry["file"] or isinstance(entry.get("strength", 1), bool)):
            raise ValueError("Un LoRA del estilo o de una pieza no tiene un formato válido.")
        if not entry.get("enabled", True):
            continue
        file = entry.get("file", "")
        key = file.replace("\\", "/").casefold()
        strength = float(entry.get("strength", 1))
        if key in seen:
            if seen[key] != strength:
                raise ValueError(f"El LoRA «{file}» tiene pesos distintos entre el estilo y las piezas. "
                                 "Unifica su peso antes de generar.")
            continue
        seen[key] = strength
        result.append({"file": file, "strength": strength})
    return result


def blocks_for(style, pieces, *, include_content=True):
    ordered = sorted(pieces, key=lambda p: (ORDER.index(p["kind"]) if p["kind"] in ORDER else 99, p["id"]))
    blocks = []
    for item in ([style] if style else []) + ordered:
        prompt = str(item.get("prompt", "")).strip()
        if item is not style:
            if include_content:
                content = piece_content.prompt(item)
                if content:
                    prompt = "\n".join(filter(None, (prompt, content)))
            for lora in item.get("loras", []):
                trigger = lora.get("trigger", "").strip()
                if lora.get("enabled", True) and trigger and trigger.casefold() not in prompt.casefold():
                    prompt = ", ".join(filter(None, (prompt, trigger)))
        blocks.append({"kind": item.get("kind", "style"), "id": item.get("id"),
                       "revision": item.get("revision"), "name": item.get("name", ""),
                       "prompt": prompt, "negative": str(item.get("negative", "")).strip()})
    return blocks


def join_blocks(blocks, additional_prompt="", additional_negative=""):
    prompt = "\n".join(b["prompt"] for b in blocks if b.get("prompt"))
    negative = "\n".join(dict.fromkeys(b["negative"] for b in blocks if b.get("negative")))
    return ("\n".join(filter(None, (prompt, additional_prompt.strip()))),
            "\n".join(filter(None, (negative, additional_negative.strip()))))


def assemble(project, style, pieces, config, additional_prompt="", additional_negative="", *, piece_test=None,
             negative_override=None):
    if negative_override is not None and not isinstance(negative_override, str):
        raise ValueError("El negativo de la prueba debe ser texto.")
    blocks = blocks_for(style, pieces, include_content=piece_test is None)
    config = copy.deepcopy(config)
    config["loras"] = merge_loras(config.get("loras", []), pieces)
    pose = next((p for p in pieces if p.get("kind") == "pose"), None)
    if pose and "pose_control" not in config and pose.get("pose_control"):
        config["pose_control"] = copy.deepcopy(pose["pose_control"])
    if piece_test:
        template = TEST_TEMPLATES.get(piece_test["kind"])
        if template:
            additional_prompt = "\n".join(filter(None, (piece_test.get("context_prompt", template[0]), additional_prompt.strip())))
            additional_negative = "\n".join(filter(None, (piece_test.get("context_negative", template[1]), additional_negative.strip())))
    prompt, negative = join_blocks(blocks, additional_prompt, additional_negative)
    if negative_override is not None:
        negative = negative_override
    saved = {"schema_version": 1, "blocks": copy.deepcopy(blocks),
             "additional_prompt": additional_prompt, "additional_negative": additional_negative,
             "piece_test": copy.deepcopy(piece_test)}
    if negative_override is not None:
        saved["negative_override"] = negative_override
    return {"prompt": prompt, "negative": negative, "config": config, "blocks": blocks,
            "snapshot": copy.deepcopy({"project": project, "style": style, "pieces": pieces}),
            "composition": saved}


def _legacy_suffix(full, prefix, label):
    if not prefix:
        return full
    if full == prefix:
        return ""
    if full.startswith(prefix + "\n"):
        return full[len(prefix) + 1:]
    raise ValueError(f"La imagen anterior no conserva bloques de {label} recuperables. "
                     "Carga sus ajustes como una composición nueva.")


def frozen(record, *, expression=None, expression_prompt=None, expression_negative=None, negative_override=None):
    """Replay immutable effective inputs, replacing only the expression when requested."""
    snapshot = copy.deepcopy(record.get("snapshot", {}))
    if not isinstance(snapshot.get("project"), dict) or not isinstance(snapshot.get("pieces"), list):
        raise ValueError("La imagen no conserva una composición. Carga sus ajustes como una composición nueva.")
    saved = record.get("composition")
    inherited_override = saved.get("negative_override") if isinstance(saved, dict) else None
    for override in (negative_override, inherited_override):
        if override is not None and not isinstance(override, str):
            raise ValueError("El negativo de la prueba debe ser texto.")
    if isinstance(saved, dict) and isinstance(saved.get("blocks"), list):
        blocks = copy.deepcopy(saved["blocks"])
        additional_prompt = str(saved.get("additional_prompt", ""))
        additional_negative = str(saved.get("additional_negative", ""))
    else:
        # Historical generations used the same deterministic ordering, before
        # character LoRA triggers and typed templates were introduced.
        blocks = blocks_for(snapshot.get("style"), snapshot["pieces"], include_content=False)
        prefix, negative_prefix = join_blocks(blocks)
        additional_prompt = _legacy_suffix(str(record.get("prompt", "")), prefix, "prompt")
        additional_negative = _legacy_suffix(str(record.get("negative", "")), negative_prefix, "negativo")
    replacing = expression is not None or expression_prompt is not None or expression_negative is not None
    if replacing:
        blocks = [b for b in blocks if b.get("kind") != "expression"]
        snapshot["pieces"] = [p for p in snapshot["pieces"] if p.get("kind") != "expression"]
        if expression is not None:
            snapshot["pieces"].append(copy.deepcopy(expression))
            expression_blocks = blocks_for(None, [expression])
            if expression_negative:
                expression_blocks[0]["negative"] = "\n".join(filter(None, (
                    expression_blocks[0]["negative"], expression_negative.strip())))
            blocks.extend(expression_blocks)
        elif expression_prompt is not None or expression_negative is not None:
            blocks.append({"kind": "expression", "id": None, "revision": None, "name": "Expresión de esta toma",
                           "prompt": str(expression_prompt or "").strip(),
                           "negative": str(expression_negative or "").strip()})
        blocks.sort(key=lambda b: ORDER.index(b["kind"]) if b["kind"] in ORDER else 99)
    prompt, negative = join_blocks(blocks, additional_prompt, additional_negative)
    effective_override = negative_override if negative_override is not None else inherited_override
    if effective_override is not None:
        # The saved full negative remains authoritative. Expression-only edits
        # may add their own exclusions without resurrecting removed style tags.
        if negative_override is None and replacing:
            expression_negatives = [b["negative"] for b in blocks if b.get("kind") == "expression" and b.get("negative")]
            effective_override = "\n".join(dict.fromkeys(filter(None, (effective_override, *expression_negatives))))
        negative = effective_override
    config = copy.deepcopy(record.get("config", record.get("params", {})))
    if not config.get("model"):
        raise ValueError("La imagen no conserva su configuración efectiva de generación.")
    config["seed"] = str(record.get("seed", config.get("seed", "")))
    if replacing:
        # Replace expression LoRAs along with its prompt, preserving the exact
        # frozen base and every other selected piece. Unknown legacy LoRAs stay.
        original = record.get("snapshot", {}).get("pieces", [])
        key = lambda entry: str(entry.get("file", "")).replace("\\", "/").casefold()
        piece_files = {key(lora) for piece in original for lora in piece.get("loras", [])
                       if lora.get("enabled", True)}
        saved_style = snapshot.get("style") or {}
        style_files = {key(lora) for lora in saved_style.get("config", {}).get("loras", [])
                       if lora.get("enabled", True)}
        base = [lora for lora in config.get("loras", []) if key(lora) not in piece_files or key(lora) in style_files]
        config["loras"] = merge_loras(base, snapshot["pieces"])
    result = {"prompt": prompt, "negative": negative, "config": config, "blocks": blocks, "snapshot": snapshot,
              "composition": {"schema_version": 1, "blocks": copy.deepcopy(blocks),
                              "additional_prompt": additional_prompt, "additional_negative": additional_negative,
                              "source_asset_id": record["id"]}}
    if effective_override is not None:
        result["composition"]["negative_override"] = effective_override
    if isinstance(saved, dict):
        for field in ("integration_framing", "integration_user_prompt"):
            if field in saved:
                result["composition"][field] = copy.deepcopy(saved[field])
    return result
