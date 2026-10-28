"""Locate and preserve previews and metadata belonging to one model content hash."""
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path


PREVIEW_SUFFIXES = (".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp", ".preview.gif")
SIDECAR_SUFFIXES = (".cm-info.json", ".metadata.json")


def _model_paths(file_path, file_paths=()):
    seen = set()
    for path in (file_path, *(file_paths or ())):
        if not path:
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            yield str(path)


def find_preview(file_path, file_paths=()):
    """Look beside the primary model and every known alias without rewriting files."""
    for path in _model_paths(file_path, file_paths):
        base = os.path.splitext(path)[0]
        for suffix in PREVIEW_SUFFIXES:
            candidate = base + suffix
            try:
                if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                    return candidate
            except OSError:
                continue
    return None


def image_media_type(path):
    """Existing libraries often store PNG/WebP bytes with a .jpeg extension."""
    try:
        with open(path, "rb") as stream:
            header = stream.read(16)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "image/webp"
    except OSError:
        pass
    return "application/octet-stream"


def _load_sidecar(path, file_hash, suffix):
    if not os.path.lexists(path):
        return {}, "missing"
    try:
        if Path(path).is_symlink():
            return None, "protected"
        with open(path, encoding="utf-8-sig") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            return None, "protected"
        if suffix == ".cm-info.json":
            recognized = any(key in data for key in ("ModelId", "VersionId", "Hashes", "BaseModel", "ModelName", "ModelType"))
            hashes = data.get("Hashes") or {}
            stored_hash = hashes.get("SHA256") if isinstance(hashes, dict) else None
        else:
            recognized = any(key in data for key in ("sha256", "civitai", "from_civitai"))
            stored_hash = data.get("sha256")
        if not recognized or (stored_hash and file_hash and str(stored_hash).casefold() != str(file_hash).casefold()):
            return None, "protected"
        if suffix == ".cm-info.json":
            complete = bool(data.get("ModelId") and data.get("VersionId")) and all(
                _useful(data.get(key)) for key in ("ModelName", "ModelType", "BaseModel"))
        else:
            version = data.get("civitai")
            complete = isinstance(version, dict) and bool(version.get("id") and version.get("modelId"))
        return data, "complete" if complete else "incomplete"
    except (OSError, ValueError, TypeError):
        return None, "protected"


def _sidecar_candidates(file_path, file_paths, suffix):
    return [(path, os.path.splitext(path)[0] + suffix)
            for path in _model_paths(file_path, file_paths)]


@lru_cache(maxsize=8192)
def _snapshot_status(path, file_hash, suffix, size, mtime_ns, inode):
    return _load_sidecar(path, file_hash, suffix)[1]


def _sidecar_state(path, file_hash, suffix):
    try:
        info = os.stat(path)
        return _snapshot_status(path, file_hash, suffix, info.st_size, info.st_mtime_ns, info.st_ino)
    except OSError:
        return "missing"


def sidecar_status(file_path, file_paths=(), file_hash=""):
    problems = []
    for suffix in SIDECAR_SUFFIXES:
        states = [_sidecar_state(path, file_hash, suffix)
                  for _, path in _sidecar_candidates(file_path, file_paths, suffix)]
        if "complete" not in states:
            problems.append("protected" if "protected" in states else "incomplete" if "incomplete" in states else "missing")
    for state in ("protected", "missing", "incomplete"):
        if state in problems:
            return state
    return "complete"


def assets_status(model):
    preview = find_preview(model.get("file_path"), model.get("file_paths", ()))
    sidecars = sidecar_status(model.get("file_path"), model.get("file_paths", ()), model.get("hash", ""))
    stored = model.get("preview_status")
    checked = bool(model.get("civitai_assets_synced_at"))
    preview_state = "available" if preview else stored if checked and stored in ("unavailable", "not_found") else "pending"
    complete = (bool(preview) or (checked and stored == "unavailable")) and sidecars == "complete"
    if checked and stored == "not_found":
        complete = True
    return {
        "has_thumbnail": bool(preview), "has_sidecars": sidecars == "complete",
        "sidecar_status": sidecars, "preview_status": preview_state, "assets_complete": complete,
        "lookup_not_found": checked and stored == "not_found",
    }


def _useful(value):
    return value not in (None, "", [], {}) and not (isinstance(value, str) and value.strip().casefold() == "unknown")


def _merge(existing, updates):
    result = dict(existing)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        elif _useful(value) or key not in result:
            result[key] = value
    return result


def _write_json(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                         prefix=".vault-sidecar-", suffix=".json.tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def update_sidecars(model, version_data):
    """Merge known Civitai fields atomically, leaving personal/foreign files alone."""
    file_path = model["file_path"]
    file_hash = model["hash"]
    common = {
        "ModelId": model.get("model_id"), "ModelName": model.get("display_name"),
        "ModelType": model.get("model_type"), "VersionId": model.get("version_id"),
        "VersionName": model.get("version_name"), "BaseModel": model.get("base_model"),
        "Hashes": {"SHA256": file_hash},
        "TrainedWords": model.get("triggers", "").split(", ") if model.get("triggers") else [],
    }
    updated = protected = 0
    for suffix in SIDECAR_SUFFIXES:
        candidates = _sidecar_candidates(file_path, model.get("file_paths", ()), suffix)
        complete = [(source, path) for source, path in candidates
                    if _sidecar_state(path, file_hash, suffix) == "complete"]
        incomplete = [(source, path) for source, path in candidates
                      if _sidecar_state(path, file_hash, suffix) == "incomplete"]
        selected = next(iter(complete or incomplete), None)
        if selected is None:
            selected = next(((source, path) for source, path in candidates if os.path.lexists(path)), candidates[0])
        source, path = selected
        existing, state = _load_sidecar(path, file_hash, suffix)
        if state == "protected":
            protected += 1
            continue
        updates = common if suffix == ".cm-info.json" else {
            "file_name": os.path.basename(source), "sha256": file_hash,
            "base_model": model.get("base_model"), "from_civitai": True, "civitai": version_data,
        }
        merged = _merge(existing, updates)
        if state == "missing" or merged != existing:
            _write_json(path, merged)
            updated += 1
    return {"sidecars_updated": updated, "sidecars_protected": protected}
