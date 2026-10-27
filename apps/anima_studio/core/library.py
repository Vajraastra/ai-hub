"""Local, versioned library and reviewable assets for Anima Studio.

Only paths below the supplied data directory are written. The history JSON is
the authoritative index; accepted/rejected image paths may change, asset IDs do
not. This store intentionally never writes into a game's export directory.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter
from typing import Any


class LibraryError(ValueError):
    pass


_KINDS = {"projects", "styles", "pieces", "recipes"}
_PIECES = {"character", "personality", "outfit", "pose", "expression", "style", "scene"}
_STATES = {"pending", "accepted", "rejected"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_VARIANT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DEFAULT_CATEGORIES = ["characters", "backgrounds", "items", "cards"]
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _clone(value):
    return copy.deepcopy(value)


def _slug(value: Any, fallback: str = "asset", limit: int = 36) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-_. ").lower()[:limit]
    text = text or fallback
    return f"asset-{text}" if text in _RESERVED else text


def _identifier(value: Any) -> str:
    if (not isinstance(value, str) or not _ID.fullmatch(value)
            or value.lower() in _RESERVED):
        raise LibraryError("Identificador no válido.")
    return value


class Library:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise LibraryError("La ruta sale de la biblioteca.")
        return path

    @staticmethod
    def _kind(kind: str):
        if kind not in _KINDS:
            raise LibraryError("Tipo de biblioteca desconocido.")

    def _read(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LibraryError("No se encontró el recurso.") from exc
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise LibraryError("El recurso guardado está dañado.") from exc
        if not isinstance(value, dict):
            raise LibraryError("El recurso guardado no es un objeto.")
        return value

    def _atomic_bytes(self, path: Path, content: bytes):
        # Re-resolve immediately before mutation to reject junctions/symlinks.
        self._path(str(path.relative_to(self.root)))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _write(self, path: Path, payload: dict):
        try:
            raw = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise LibraryError("Los datos deben ser JSON válido y finito.") from exc
        self._atomic_bytes(path, raw)

    def get(self, kind: str, entry_id: str, revision: int | None = None) -> dict:
        self._kind(kind)
        entry_id = _identifier(entry_id)
        with self._lock:
            if revision is None:
                path = self._path(kind, f"{entry_id}.json")
            else:
                if type(revision) is not int or revision < 1:
                    raise LibraryError("Revisión no válida.")
                path = self._path("revisions", kind, entry_id, f"{revision:08d}.json")
            return self._read(path)

    def list(self, kind: str, project_id: str | None = None) -> list[dict]:
        self._kind(kind)
        if project_id is not None:
            self.get("projects", project_id)
        with self._lock:
            folder = self._path(kind)
            entries = []
            for path in sorted(folder.glob("*.json")):
                self._path(str(path.relative_to(self.root)))
                value = self._read(path)
                if project_id is not None:
                    if kind == "projects" and value["id"] != project_id:
                        continue
                    if kind != "projects" and value.get("project_id") not in (None, project_id):
                        continue
                entries.append(value)
            return entries

    def _validate_project(self, value: dict):
        settings = value.get("settings", {})
        if not isinstance(settings, dict):
            raise LibraryError("La configuración del proyecto debe ser un objeto.")
        categories = settings.get("categories", value.get("categories", _DEFAULT_CATEGORIES))
        if not isinstance(categories, list) or not categories:
            raise LibraryError("Define al menos una categoría de recursos.")
        for category in categories:
            name = category.get("id") if isinstance(category, dict) else category
            if not isinstance(name, str) or not name.strip():
                raise LibraryError("Categoría no válida.")
        formats = settings.get("formats", value.get("formats", []))
        if not isinstance(formats, list):
            raise LibraryError("Los formatos deben ser una lista.")
        for fmt in formats:
            if not isinstance(fmt, dict) or not fmt.get("id") or not fmt.get("name"):
                raise LibraryError("Cada formato necesita identificador y nombre.")
            if any(type(fmt.get(axis)) is not int or not 1 <= fmt[axis] <= 16384
                   for axis in ("width", "height")):
                raise LibraryError("Dimensiones de formato no válidas.")
        days = settings.get("retention_days", value.get("retention_days"))
        if days is not None and (type(days) is not int or not 0 <= days <= 36500):
            raise LibraryError("Retención no válida.")

    def _same_scope(self, kind: str, entry_id: str, project_id: str | None,
                    revision: int | None = None):
        value = self.get(kind, entry_id, revision)
        if value.get("project_id") not in (None, project_id):
            raise LibraryError("El recurso pertenece a otro proyecto.")

    def _validate(self, kind: str, value: dict):
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 180:
            raise LibraryError("Escribe un nombre de hasta 180 caracteres.")
        if kind == "projects":
            self._validate_project(value)
            return
        project_id = value.get("project_id")
        if project_id is not None:
            self.get("projects", project_id)
        for field in ("prompt", "negative"):
            if field in value and not isinstance(value[field], str):
                raise LibraryError(f"{field} debe ser texto.")
        if kind == "pieces":
            piece_kind = value.get("kind", value.get("piece_type", value.get("type")))
            if piece_kind not in _PIECES:
                raise LibraryError("Tipo de pieza no válido.")
            value["kind"] = piece_kind
        if kind == "styles" and not isinstance(value.get("config", {}), dict):
            raise LibraryError("La configuración de estilo debe ser un objeto.")
        if kind == "recipes":
            style_id = value.get("style_id")
            if style_id:
                self._same_scope("styles", style_id, project_id, value.get("style_revision"))
            piece_ids = value.get("piece_ids", [])
            if isinstance(piece_ids, dict):
                piece_ids = list(piece_ids.values())
            if not isinstance(piece_ids, list):
                raise LibraryError("Las piezas de una receta deben ser una lista.")
            pins = value.get("piece_revisions", {})
            if not isinstance(pins, dict):
                raise LibraryError("Las revisiones de piezas deben ser un objeto.")
            for piece_id in piece_ids:
                if piece_id:
                    self._same_scope("pieces", piece_id, project_id, pins.get(piece_id))

    def save(self, kind: str, payload: dict) -> dict:
        self._kind(kind)
        if not isinstance(payload, dict):
            raise LibraryError("El recurso debe ser un objeto.")
        with self._lock:
            incoming = _clone(payload)
            entry_id = _identifier(incoming.get("id") or uuid.uuid4().hex)
            path = self._path(kind, f"{entry_id}.json")
            old = self._read(path) if path.exists() else {}
            value = {**old, **incoming}
            if old and kind != "projects" and old.get("project_id") != value.get("project_id"):
                raise LibraryError("No se puede cambiar el proyecto de un recurso existente; crea una copia.")
            self._validate(kind, value)
            now = _now()
            revisions = self._path("revisions", kind, entry_id)
            highest = max((int(p.stem) for p in revisions.glob("*.json") if p.stem.isdigit()), default=0)
            value.update(id=entry_id, revision=highest + 1, schema_version=1,
                         created_at=old.get("created_at", now), updated_at=now)
            # Serialization is validated before creating either file.
            archive = self._path("revisions", kind, entry_id, f"{value['revision']:08d}.json")
            self._write(archive, value)
            self._write(path, value)
            return _clone(value)

    def _generation_path(self, asset_id: str) -> Path:
        return self._path("history", f"{_identifier(asset_id)}.json")

    def get_generation(self, asset_id: str) -> dict:
        with self._lock:
            return self._read(self._generation_path(asset_id))

    def list_generations(self, project_id: str | None = None, status: str | None = None,
                         include_purged: bool = False) -> list[dict]:
        if project_id is not None:
            self.get("projects", project_id)
        if status is not None and status not in _STATES:
            raise LibraryError("Estado de revisión no válido.")
        with self._lock:
            out = []
            for path in self._path("history").glob("*.json"):
                value = self._read(self._path(str(path.relative_to(self.root))))
                if ((project_id is None or value.get("project_id") == project_id)
                        and (status is None or value.get("status") == status)
                        and (include_purged or not value.get("purged_at"))):
                    out.append(value)
            return sorted(out, key=lambda value: value["created_at"], reverse=True)

    def image_path(self, asset_id: str, variant: str = "image") -> Path:
        with self._lock:
            value = self.get_generation(asset_id)
            relative = value.get("file") if variant == "image" else value.get("variants", {}).get(variant)
            if not relative or value.get("purged_at"):
                raise LibraryError("Esta imagen no está disponible.")
            path = self._path(relative)
            if not path.is_file():
                raise LibraryError("No se encontró el archivo de imagen.")
            return path

    def save_generation(self, metadata: dict, image_bytes: bytes,
                        extras: dict[str, bytes] | None = None) -> dict:
        if not isinstance(metadata, dict) or not isinstance(image_bytes, bytes) or not image_bytes:
            raise LibraryError("Faltan los datos o la imagen generada.")
        with self._lock:
            record = _clone(metadata)
            for review_field in ("purged_at", "rejected_at", "review_reason"):
                record.pop(review_field, None)
            project_id = record.get("project_id")
            project = self.get("projects", project_id)
            master_id = record.get("master_asset_id") or record.get("master_id")
            if master_id:
                master = self.get_generation(master_id)
                if master["project_id"] != project_id or master.get("purged_at"):
                    raise LibraryError("El maestro no está disponible para este proyecto.")
            category = record.get("category", "characters")
            categories = project.get("settings", {}).get("categories", project.get("categories", _DEFAULT_CATEGORIES))
            allowed = {c.get("id") if isinstance(c, dict) else c for c in categories}
            if category not in allowed:
                raise LibraryError("La categoría no pertenece al perfil del proyecto.")
            asset_id = uuid.uuid4().hex
            now = _now()
            record.update(id=asset_id, project_id=project_id, category=category, status="pending",
                          created_at=now, updated_at=now, review_revision=0,
                          file=f"history/{asset_id}.png", variants={}, schema_version=1,
                          image_sha256=hashlib.sha256(image_bytes).hexdigest())
            for seed_key in ("seed", "master_seed", "edit_seed"):
                if record.get(seed_key) is not None:
                    record[seed_key] = str(record[seed_key])
            files = [(self._path(record["file"]), image_bytes)]
            for name, content in (extras or {}).items():
                if not _VARIANT.fullmatch(name) or name == "image" or not isinstance(content, bytes):
                    raise LibraryError("Nombre o contenido de archivo adicional no válido.")
                relative = f"history/{asset_id}.{name}.png"
                record["variants"][name] = relative
                files.append((self._path(relative), content))
            # Fail before writing images on unserializable metadata.
            try:
                json.dumps(record, allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise LibraryError("Metadatos no válidos.") from exc
            written = []
            try:
                for path, content in files:
                    self._atomic_bytes(path, content)
                    written.append(path)
                self._write(self._generation_path(asset_id), record)
            except BaseException:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
            return _clone(record)

    @staticmethod
    def _label(record: dict, field: str) -> str:
        value = record.get(f"{field}_name") or record.get(field) or record.get("labels", {}).get(field)
        return str(value) if isinstance(value, (str, int)) else ""

    def _review_target(self, record: dict, status: str) -> Path:
        if status == "pending":
            return self._path("history", f"{record['id']}.png")
        if status == "rejected":
            return self._path("rejected", record["rejected_at"][:10], f"{record['id']}.png")
        project = self.get("projects", record["project_id"])
        project_slug = _slug(project.get("slug") or project["name"])
        category = _slug(record["category"])
        character = _slug(self._label(record, "character"), "")
        outfit = _slug(self._label(record, "outfit"), "")
        expression = _slug(self._label(record, "expression"), "")
        context = dict(project=project_slug, category=category, character=character,
                       outfit=outfit, expression=expression,
                       revision=f"{record['review_revision']:03d}", id=record["id"])
        pattern = project.get("settings", {}).get("naming") or project.get("naming")
        if isinstance(pattern, str) and pattern:
            try:
                fields = list(Formatter().parse(pattern))
                if any(field and (field not in context or spec or conv)
                       for _, field, spec, conv in fields):
                    raise ValueError("Unknown naming field")
                prefix = pattern.format(**context)
            except (ValueError, KeyError, IndexError) as exc:
                raise LibraryError("Plantilla de nombres no válida.") from exc
        else:
            prefix = "_".join(filter(None, (project_slug, category, character, outfit,
                                            expression, f"v{context['revision']}")))
        # The ID is appended regardless of the user template, so names never collide.
        filename = f"{_slug(prefix, limit=100)}_{record['id']}.png"
        folders = ["accepted", project_slug, category]
        if character:
            folders.append(character)
        if outfit:
            folders.append(outfit)
        return self._path(*folders, filename)

    def review(self, asset_id: str, status: str, reason: str = "") -> dict:
        if status not in _STATES:
            raise LibraryError("Estado de revisión no válido.")
        if not isinstance(reason, str) or len(reason) > 4000:
            raise LibraryError("Motivo de revisión no válido.")
        with self._lock:
            original = self.get_generation(asset_id)
            if original.get("purged_at"):
                raise LibraryError("No se puede restaurar una imagen eliminada.")
            record = _clone(original)
            if record["status"] == status and record.get("review_reason", "") == reason:
                return record
            record.update(status=status, updated_at=_now(), review_reason=reason,
                          review_revision=record.get("review_revision", 0) + 1)
            if status == "rejected":
                record["rejected_at"] = original.get("rejected_at") or _now()
            else:
                record.pop("rejected_at", None)
            target = self._review_target(record, status)
            source = self.image_path(asset_id)
            moves = [(source, target)]
            record["file"] = target.relative_to(self.root).as_posix()
            for name, relative in original.get("variants", {}).items():
                variant_target = target.with_name(f"{target.stem}.{name}.png")
                moves.append((self._path(relative), variant_target))
                record["variants"][name] = variant_target.relative_to(self.root).as_posix()
            for source_file, target_file in moves:
                self._path(str(source_file.relative_to(self.root)))
                self._path(str(target_file.relative_to(self.root)))
                if not source_file.is_file():
                    raise LibraryError("Falta un archivo del recurso; no se movió nada.")
                if source_file != target_file and target_file.exists():
                    raise LibraryError("El destino ya existe; no se sobrescribió.")
            sidecar = target.with_suffix(".json")
            old_sidecar = source.with_suffix(".json")
            index = self._generation_path(asset_id)
            moved = []
            try:
                for source_file, target_file in moves:
                    if source_file != target_file:
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(source_file, target_file)
                        moved.append((source_file, target_file))
                if sidecar != index:
                    self._write(sidecar, record)
                self._write(index, record)
            except BaseException:
                for source_file, target_file in reversed(moved):
                    os.replace(target_file, source_file)
                if sidecar != index and sidecar != old_sidecar:
                    sidecar.unlink(missing_ok=True)
                raise
            if old_sidecar not in (index, sidecar):
                self._path(str(old_sidecar.relative_to(self.root))).unlink(missing_ok=True)
            return _clone(record)

    @staticmethod
    def _contains_id(value: Any, asset_id: str) -> bool:
        if isinstance(value, str):
            return value == asset_id
        if isinstance(value, dict):
            return any(Library._contains_id(child, asset_id) for child in value.values())
        if isinstance(value, list):
            return any(Library._contains_id(child, asset_id) for child in value)
        return False

    def _used(self, asset_id: str) -> bool:
        folders = [self._path("history"), *(self._path(kind) for kind in _KINDS)]
        for folder in folders:
            for path in folder.glob("*.json"):
                record = self._read(self._path(str(path.relative_to(self.root))))
                if record.get("id") != asset_id and not record.get("purged_at"):
                    if self._contains_id(record, asset_id):
                        return True
        # Historical style/recipe revisions remain loadable, so their source
        # images are dependencies too, even if the current revision changed.
        for path in self._path("revisions").rglob("*.json"):
            record = self._read(self._path(str(path.relative_to(self.root))))
            if record.get("id") != asset_id and self._contains_id(record, asset_id):
                return True
        return False

    def purge_rejected(self, ids: list[str], min_age_days: int = 0) -> dict:
        if type(min_age_days) is not int or min_age_days < 0:
            raise LibraryError("Antigüedad mínima no válida.")
        if not isinstance(ids, list) or len(ids) > 1000:
            raise LibraryError("Selecciona hasta 1000 rechazados para limpiar.")
        summary = {"purged": [], "protected": [], "skipped": [], "bytes_freed": 0}
        with self._lock:
            # Preflight all IDs before the first destructive operation, so one
            # malformed/stale selection cannot yield an unexplained partial run.
            unique_ids = list(dict.fromkeys(_identifier(asset_id) for asset_id in ids))
            records = [self.get_generation(asset_id) for asset_id in unique_ids]
            for asset_id, record in zip(unique_ids, records):
                if record.get("status") != "rejected" or record.get("purged_at"):
                    summary["skipped"].append({"id": asset_id, "reason": "No es un rechazado disponible."})
                    continue
                rejected_at = datetime.fromisoformat(record["rejected_at"])
                if (datetime.now(timezone.utc) - rejected_at).total_seconds() < min_age_days * 86400:
                    summary["skipped"].append({"id": asset_id, "reason": "Todavía no alcanza la antigüedad elegida."})
                    continue
                if self._used(asset_id):
                    summary["protected"].append(asset_id)
                    continue
                paths = [self._path(record["file"]),
                         *(self._path(relative) for relative in record.get("variants", {}).values())]
                # No external paths, directories or authoritative history JSON may be removed.
                for path in paths:
                    if path.exists() and not path.is_file():
                        raise LibraryError("No se puede eliminar una carpeta como imagen.")
                freed = sum(path.stat().st_size for path in paths if path.exists())
                tombstone = {**record, "purged_at": _now(), "file": None, "variants": {}}
                # Keep the complete settings/seed record even after images are purged.
                self._write(self._generation_path(asset_id), tombstone)
                for path in paths:
                    path.unlink(missing_ok=True)
                self._path(record["file"]).with_suffix(".json").unlink(missing_ok=True)
                summary["purged"].append(asset_id)
                summary["bytes_freed"] += freed
            return summary
