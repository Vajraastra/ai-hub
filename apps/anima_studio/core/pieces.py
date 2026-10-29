"""Pinned piece masters and variants, independent from prompt composition."""
from __future__ import annotations

import copy
import math
from pathlib import PurePosixPath


KINDS = frozenset({"character", "personality", "outfit", "pose", "expression", "style",
                   "scene", "background", "item"})
_INHERITED = ("kind", "project_id", "prompt", "negative", "seed", "loras", "tags",
              "definition", "pose_control", "generation_snapshot")
_REPLAY_FIELDS = ("config", "prompt", "negative", "snapshot", "composition", "mode",
                  "category", "selected_files", "workflow_sha256", "master_seed", "edit_seed")


def seed_text(value):
    """Keep all 64 seed bits through JSON and browser round trips."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("La seed de la pieza debe ser un entero de 64 bits.")
    text = str(value).strip()
    if not text.isascii() or not text.isdecimal() or not 0 <= int(text) <= 2**64 - 1:
        raise ValueError("La seed de la pieza debe estar entre 0 y 18446744073709551615.")
    return str(int(text))


def piece_loras(value):
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError("Selecciona hasta 12 LoRAs de personaje.")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ValueError("LoRA de personaje no válido.")
        file = item["file"].replace("\\", "/")
        path = PurePosixPath(file)
        if (not file or path.is_absolute() or ".." in path.parts or ":" in file
                or path.suffix.lower() != ".safetensors"
                or any(char in file for char in "\r\n\0")):
            raise ValueError("La ruta del LoRA debe ser relativa a la biblioteca.")
        file = path.as_posix()
        key = file.casefold()
        if key in seen:
            raise ValueError("No repitas el mismo LoRA de personaje.")
        seen.add(key)
        strength = item.get("strength", 1)
        if (isinstance(strength, bool) or not isinstance(strength, (int, float))
                or not math.isfinite(strength) or not -3 <= strength <= 3):
            raise ValueError("El peso del LoRA debe estar entre -3 y 3.")
        if "trigger" in item and not isinstance(item["trigger"], str):
            raise ValueError("Los activadores del LoRA deben ser texto.")
        if "enabled" in item and not isinstance(item["enabled"], bool):
            raise ValueError("La activación del LoRA debe ser verdadera o falsa.")
        result.append({**copy.deepcopy(item), "file": file, "strength": float(strength)})
    return result


def _snapshot(library, asset_id, project_id):
    if not isinstance(asset_id, str) or not asset_id:
        raise ValueError("Selecciona una prueba guardada de la pieza.")
    record = library.get_generation(asset_id)
    if record.get("purged_at"):
        raise ValueError("La prueba de la pieza ya fue borrada.")
    if project_id is not None and record.get("project_id") != project_id:
        raise ValueError("La prueba de la pieza pertenece a otro proyecto.")
    library.image_path(asset_id)
    config = record.get("config", record.get("params", {}))
    if not isinstance(config, dict):
        raise ValueError("La prueba no conserva una configuración válida.")
    seed = seed_text(record.get("seed", config.get("seed")))
    snapshot = {key: copy.deepcopy(record[key]) for key in _REPLAY_FIELDS if key in record}
    snapshot.update(asset_id=asset_id, seed=seed, config={**copy.deepcopy(config), "seed": seed})
    return snapshot


def _check_snapshot(value):
    if not isinstance(value, dict):
        raise ValueError("La configuración guardada de la prueba debe ser un objeto.")
    if "config" in value and not isinstance(value["config"], dict):
        raise ValueError("La configuración guardada de la prueba no es válida.")
    if "snapshot" in value and not isinstance(value["snapshot"], dict):
        raise ValueError("La procedencia de la prueba no es válida.")
    if "seed" in value:
        value["seed"] = seed_text(value["seed"])


def materialize_piece(library, value, incoming, old, *, imported=False):
    """Return a materialized revision; a later parent edit never changes it.

    `imported` is an internal profile-import option. HTTP piece saves must not
    accept caller-supplied replay settings as a verified local image snapshot.
    """
    value = copy.deepcopy(value)
    master_id = value.get("master_id") or None
    if old:
        if master_id != (old.get("master_id") or None):
            raise ValueError("No se puede cambiar el maestro de una pieza existente; crea una variante.")
        if (master_id and incoming.get("master_revision", old.get("master_revision"))
                != old.get("master_revision")):
            raise ValueError("La revisión del maestro está fijada; crea otra variante para cambiarla.")
    inherited_snapshot = None
    if master_id:
        revision = value.get("master_revision")
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError("La revisión del maestro debe ser un entero positivo.")
        master = library.get("pieces", master_id, revision)
        revision = master["revision"]
        if master.get("project_id") not in (None, value.get("project_id", master.get("project_id"))):
            raise ValueError("El maestro pertenece a otro proyecto.")
        kind = value.get("kind", value.get("piece_type", value.get("type", master.get("kind"))))
        if kind != master.get("kind"):
            raise ValueError("La variante y su maestro deben ser del mismo tipo.")
        seen = {value.get("id")}
        ancestor = master
        while ancestor:
            if ancestor.get("id") in seen or len(seen) > 64:
                raise ValueError("Los maestros de piezas no pueden formar ciclos ni cadenas mayores de 64.")
            seen.add(ancestor["id"])
            if not ancestor.get("master_id"):
                break
            ancestor = library.get("pieces", ancestor["master_id"], ancestor.get("master_revision"))
        inherited_snapshot = master.get("generation_snapshot")
        if not old:
            inherited = {key: copy.deepcopy(master[key]) for key in _INHERITED if key in master}
            value = {**inherited, **value}
        value.update(master_id=master_id, master_revision=revision, kind=kind)
    elif value.get("master_revision") is not None:
        raise ValueError("Una revisión de maestro necesita un maestro.")
    else:
        value.pop("master_id", None)
        value.pop("master_revision", None)

    if "generation_snapshot" in incoming and not imported:
        known = old.get("generation_snapshot") if old else inherited_snapshot
        if incoming["generation_snapshot"] != known and not incoming.get("sample_asset_id"):
            raise ValueError("Captura una prueba guardada para recuperar sus ajustes reales.")
    sample = value.get("sample_asset_id")
    if "sample_asset_id" in incoming:
        if incoming["sample_asset_id"] in (None, ""):
            value.pop("sample_asset_id", None)
            # The frozen settings still describe the originating test even when
            # its preview is cleared or removed from disk.
        else:
            snapshot = _snapshot(library, incoming["sample_asset_id"], value.get("project_id"))
            value.update(sample_asset_id=incoming["sample_asset_id"], generation_snapshot=snapshot,
                         seed=snapshot["seed"])
    elif sample:
        try:
            library.image_path(sample)
        except (ValueError, OSError):
            value.pop("sample_asset_id", None)

    kind = value.get("kind", value.get("piece_type", value.get("type")))
    if kind not in KINDS:
        raise ValueError("Tipo de pieza no válido.")
    value["kind"] = kind
    if value.get("seed") is None or value.get("seed") == "":
        value.pop("seed", None)
    elif "seed" in value:
        value["seed"] = seed_text(value["seed"])
    if "loras" in value:
        value["loras"] = piece_loras(value["loras"])
        if kind != "character" and value["loras"]:
            raise ValueError("Sólo el personaje puede vincular LoRAs de identidad.")
    if "pose_control" in value:
        if value["pose_control"] is not None and not isinstance(value["pose_control"], dict):
            raise ValueError("El control de pose debe ser un objeto.")
        if kind != "pose" and value["pose_control"]:
            raise ValueError("El control de pose pertenece a una pieza de pose.")
        if value["pose_control"]:
            from .pose_control import validate_config
            value["pose_control"] = validate_config(value["pose_control"])
        if not value.get("pose_control"):
            value.pop("pose_control", None)
    if "definition" in value and not isinstance(value["definition"], dict):
        raise ValueError("La definición de la pieza debe ser un objeto.")
    if "generation_snapshot" in value:
        _check_snapshot(value["generation_snapshot"])
    return value
