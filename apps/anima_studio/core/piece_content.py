"""Semantic piece controls, independent from preview staging and rendering style."""
from __future__ import annotations


_VIEWS = {
    "front": "Front view.",
    "threequarter": "Three-quarter view.",
    "profile": "Side profile view.",
    "back": "Back view.",
}
_ENVIRONMENT = {
    "light": {"day": "Daylight.", "dusk": "Warm dusk lighting.", "night": "Night-time lighting."},
    "weather": {"clear": "Clear weather.", "rain": "Rainy weather.",
                "snow": "Snowy weather.", "fog": "Foggy weather."},
    "space": {
        "left": "Leave uncluttered foreground space on the left for a character.",
        "right": "Leave uncluttered foreground space on the right for a character.",
        "center": "Leave uncluttered foreground space in the center for characters.",
    },
}
_CONTROLS = {
    "pose": {"view": _VIEWS},
    "expression": {"intensity": {
        "subtle": "Subtle restrained facial expression.",
        "strong": "Strong clearly readable facial expression.",
    }},
    "background": _ENVIRONMENT,
    "scene": _ENVIRONMENT,
    "item": {"view": {key: value for key, value in _VIEWS.items() if key != "back"}},
}


def prompt(piece):
    """Return only authored content choices; never inject preview templates.

    Character framing/view, outfit mannequin view, and inventory-icon staging
    describe a test image. The integrator owns its final framing and scene.
    Unknown historical controls stay in provenance without becoming text.
    """
    definition = piece.get("definition", {})
    if not isinstance(definition, dict):
        return ""
    presentation = definition.get("presentation", {})
    if not isinstance(presentation, dict):
        return ""
    parts = []
    for key, choices in _CONTROLS.get(piece.get("kind"), {}).items():
        value = presentation.get(key)
        if isinstance(value, str) and value in choices:
            parts.append(choices[value])
    return "\n".join(parts)
