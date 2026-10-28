"""Local, versioned library and reviewable assets for Anima Studio.

Only paths below the supplied data directory are written. The history JSON is
the authoritative index; accepted/rejected image paths may change, asset IDs do
not. This store intentionally never writes into a game's export directory.
"""
from __future__ import annotations

import copy
import base64
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


class LibraryConflict(LibraryError):
    """A saved dependency or running job prevents deletion."""


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

    @property
    def initialized(self) -> bool:
        return self._path(".initialized").is_file()

    def mark_initialized(self):
        """Remember intentional empty libraries after the final project is deleted."""
        with self._lock:
            if not self.initialized:
                self._atomic_bytes(self._path(".initialized"), b"anima-studio-library-v1\n")

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
            value = self._read(path)
            return self._style_view(value) if kind == "styles" and revision is None else value

    def _style_view(self, value: dict) -> dict:
        """Hide unavailable preview samples without rewriting saved revisions."""
        result = _clone(value)
        if "sample_ids" in result:
            available = []
            for sample_id in result["sample_ids"]:
                try:
                    self.image_path(sample_id)
                except (LibraryError, OSError):
                    continue
                available.append(sample_id)
            result["sample_ids"] = available
        return result

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
                entries.append(self._style_view(value) if kind == "styles" else value)
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
        if kind == "styles":
            if not isinstance(value.get("config", {}), dict):
                raise LibraryError("La configuración de estilo debe ser un objeto.")
            samples = value.get("sample_ids", [])
            if not isinstance(samples, list):
                raise LibraryError("Las muestras del estilo deben ser una lista de imágenes.")
            for sample_id in samples:
                sample = self.get_generation(sample_id)
                if sample.get("purged_at"):
                    raise LibraryError("Una muestra del estilo ya fue borrada. Quita esa selección antes de guardar.")
                if project_id is not None and sample.get("project_id") != project_id:
                    raise LibraryError("Una muestra del estilo pertenece a otro proyecto.")
                self.image_path(sample_id)
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
            if old and kind == "styles":
                old = self._style_view(old)
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
            if kind == "projects":
                self.mark_initialized()
            return _clone(value)

    def _generation_path(self, asset_id: str) -> Path:
        return self._path("history", f"{_identifier(asset_id)}.json")

    def delete(self, kind: str, entry_id: str) -> dict:
        """Remove a resource and its revisions without invalidating saved recipes.

        Generation snapshots are self-contained. Saved recipes, including their
        older revisions, still need their referenced style/piece to be available.
        Metadata and all revisions are removed from disk after dependency checks.
        """
        self._kind(kind)
        entry_id = _identifier(entry_id)
        with self._lock:
            record = self.get(kind, entry_id)
            if kind == "projects":
                for scoped_kind in _KINDS - {"projects"}:
                    for dependent in self.list(scoped_kind):
                        if dependent.get("project_id") == entry_id:
                            raise LibraryConflict(
                                f'Borra primero «{dependent["name"]}» y los demás recursos del proyecto.')
                if self.list_generations(project_id=entry_id):
                    raise LibraryConflict("Borra primero las imágenes del proyecto; todavía conserva resultados.")
            for dependent_kind in _KINDS - {"projects"}:
                paths = list(self._path(dependent_kind).glob("*.json"))
                paths.extend(self._path("revisions", dependent_kind).rglob("*.json"))
                for path in paths:
                    dependent = self._read(self._path(str(path.relative_to(self.root))))
                    if (kind != "projects" and (dependent_kind != kind or dependent.get("id") != entry_id)
                            and self._contains_id(dependent, entry_id)):
                        raise LibraryConflict(
                            f'No se puede borrar «{record["name"]}»: lo utiliza '
                            f'«{dependent.get("name", dependent["id"])}». Borra primero esa receta o recurso.')
            current = self._path(kind, f"{entry_id}.json")
            revisions = self._path("revisions", kind, entry_id)
            if kind == "projects":
                self.mark_initialized()
            freed = self._erase_metadata([current, revisions])
            return {"deleted": True, "id": entry_id, "kind": kind, "name": record["name"],
                    "bytes_freed": freed}

    def _metadata_snapshot(self, paths: list[Path]) -> dict[Path, bytes]:
        snapshots = {}
        for path in paths:
            self._path(str(path.relative_to(self.root)))
            if not path.exists():
                continue
            children = [path, *path.rglob("*")] if path.is_dir() else [path]
            for child in children:
                self._path(str(child.relative_to(self.root)))
                if child.is_symlink() or child.is_junction():
                    raise LibraryError("No se pueden borrar enlaces como metadatos del recurso.")
                if child.is_file():
                    if child.suffix.lower() != ".json":
                        raise LibraryError("La carpeta del recurso contiene archivos ajenos a sus metadatos.")
                    snapshots[child] = child.read_bytes()
                elif not child.is_dir():
                    raise LibraryError("La carpeta del recurso contiene un archivo no válido.")
        return snapshots

    def _metadata_journal(self, paths: list[Path], snapshots: dict[Path, bytes], transaction: str) -> dict:
        return {"schema": "anima-metadata-delete-v1", "transaction": transaction,
                "entries": [{"original": path.relative_to(self.root).as_posix(), "staged": str(index)}
                            for index, path in enumerate(paths)],
                "files": [{"original": path.relative_to(self.root).as_posix(),
                           "content": base64.b64encode(content).decode("ascii")}
                          for path, content in snapshots.items()]}

    def _check_metadata_journal(self, destination: Path, journal: dict):
        """Validate exact metadata ownership before using a persisted journal."""
        if (not isinstance(journal, dict) or journal.get("schema") != "anima-metadata-delete-v1"
                or journal.get("transaction") != destination.name
                or not re.fullmatch(r"[0-9a-f]{32}", destination.name)
                or not isinstance(journal.get("entries"), list)
                or not isinstance(journal.get("files"), list)):
            raise LibraryError("El registro de borrado no es válido.")
        entries, originals = [], set()
        for index, entry in enumerate(journal["entries"]):
            relative = entry.get("original") if isinstance(entry, dict) else None
            if (not isinstance(relative, str) or "\\" in relative or ":" in relative
                    or any(part in ("", ".", "..") for part in relative.split("/"))
                    or relative in originals or entry.get("staged") != str(index)):
                raise LibraryError("La ruta del registro de borrado no es válida.")
            parts = relative.split("/")
            shape, identifier = None, None
            if len(parts) == 2 and parts[0] in _KINDS | {"history"} and parts[1].endswith(".json"):
                shape, identifier = "file", parts[1][:-5]
            elif len(parts) == 3 and parts[0] == "revisions" and parts[1] in _KINDS:
                shape, identifier = "revisions", parts[2]
            elif len(parts) == 3 and parts[0] == ".trash" and parts[1] in _KINDS:
                identifier, separator, suffix = parts[2].rpartition("-")
                if separator and re.fullmatch(r"[0-9a-f]{32}", suffix):
                    shape = "legacy"
            if shape is None:
                raise LibraryError("El registro contiene una ruta ajena a los metadatos del taller.")
            _identifier(identifier)
            original = self._path(relative)
            entries.append({"original": original, "staged": destination / str(index),
                            "relative": relative, "shape": shape, "id": identifier})
            originals.add(relative)
        if not entries:
            raise LibraryError("El registro de borrado está vacío.")
        snapshots, staged_files, seen_files = {}, set(), set()
        for file in journal["files"]:
            relative = file.get("original") if isinstance(file, dict) else None
            if not isinstance(relative, str) or relative in seen_files:
                raise LibraryError("El archivo del registro de borrado no es válido.")
            seen_files.add(relative)
            owner = next((entry for entry in entries if relative == entry["relative"]
                          or relative.startswith(entry["relative"] + "/")), None)
            if owner is None:
                raise LibraryError("El registro contiene archivos ajenos al recurso.")
            tail = relative[len(owner["relative"]):].lstrip("/")
            valid = ((owner["shape"] == "file" and not tail)
                     or (owner["shape"] == "revisions" and re.fullmatch(r"[0-9]{8}\.json", tail))
                     or (owner["shape"] == "legacy" and
                         (tail == owner["id"] + ".json"
                          or re.fullmatch(re.escape(owner["id"]) + r"/[0-9]{8}\.json", tail))))
            if not valid:
                raise LibraryError("El registro contiene archivos que no pertenecen al recurso.")
            try:
                content = base64.b64decode(file["content"], validate=True)
                payload = json.loads(content)
            except (ValueError, TypeError, KeyError) as exc:
                raise LibraryError("Los metadatos de recuperación están dañados.") from exc
            if (not isinstance(payload, dict) or payload.get("id") != owner["id"]
                    or (owner["relative"].startswith("history/") and not payload.get("purged_at"))):
                raise LibraryError("El contenido del registro no corresponde al recurso retirado.")
            path = self._path(relative)
            snapshots[path] = content
            staged_files.add(owner["staged"] / tail if tail else owner["staged"])
        return entries, snapshots, staged_files

    def _check_metadata_stage(self, destination: Path, journal: dict):
        entries, snapshots, staged_files = self._check_metadata_journal(destination, journal)
        allowed_dirs = {entry["staged"] for entry in entries if entry["shape"] != "file"}
        for file in staged_files:
            allowed_dirs.update(parent for parent in file.parents if parent.is_relative_to(destination) and parent != destination)
        for child in destination.rglob("*"):
            self._path(str(child.relative_to(self.root)))
            if child.is_symlink() or child.is_junction():
                raise LibraryError("La carpeta de borrado contiene un enlace no válido.")
            if child == destination / "journal.json":
                continue
            if (not (child.is_file() or child.is_dir()) or (child.is_file() and child not in staged_files)
                    or (child.is_dir() and child not in allowed_dirs)):
                raise LibraryError("La carpeta de borrado contiene archivos ajenos al registro.")
        return entries, snapshots

    def _remove_metadata_stage(self, destination: Path, journal: dict):
        self._check_metadata_stage(destination, journal)
        manifest = destination / "journal.json"
        children = sorted(destination.rglob("*"), key=lambda p: len(p.parts), reverse=True)
        for child in children:
            if child == manifest:
                continue
            self._path(str(child.relative_to(self.root)))
            child.rmdir() if child.is_dir() else child.unlink()
        # The durable snapshot is the final file removed. A process crash before
        # this point can restore even metadata already unlinked from staging.
        if manifest.exists():
            manifest.unlink()
        destination.rmdir()
        try:
            destination.parent.rmdir()
        except OSError:
            pass

    def recover_pending_metadata_deletions(self) -> dict:
        """Roll back unconfirmed metadata deletions after a process crash.

        Journal data is limited to JSON owned by exact resource paths. Recovery
        never overwrites a resource changed since the interrupted operation.
        """
        summary = {"recovered": [], "discarded_empty": [], "skipped": []}
        with self._lock:
            parent = self._path(".deleting")
            for destination in sorted(parent.glob("*")):
                try:
                    self._path(str(destination.relative_to(self.root)))
                    if (not re.fullmatch(r"[0-9a-f]{32}", destination.name)
                            or not destination.is_dir() or destination.is_symlink() or destination.is_junction()):
                        raise LibraryError("Carpeta de borrado de procedencia desconocida.")
                    manifest = destination / "journal.json"
                    if not manifest.exists():
                        # Empty directories are the only residue possible after
                        # a committed deletion. Before the first move, a crash
                        # during the atomic journal write can also leave only
                        # its strictly named temporary files; no source moved.
                        children = list(destination.iterdir())
                        for child in children:
                            self._path(str(child.relative_to(self.root)))
                            if (not re.fullmatch(r"\.journal\.json\.[0-9a-f]{32}\.tmp", child.name)
                                    or not child.is_file() or child.is_symlink() or child.is_junction()):
                                raise LibraryError("Falta el registro para recuperar el borrado interrumpido.")
                        for child in children:
                            self._path(str(child.relative_to(self.root)))
                            child.unlink()
                        destination.rmdir()
                        summary["discarded_empty"].append(destination.name)
                        continue
                    self._path(str(manifest.relative_to(self.root)))
                    journal = self._read(manifest)
                    entries, snapshots = self._check_metadata_stage(destination, journal)
                    existing = self._metadata_snapshot([entry["original"] for entry in entries])
                    if any(path not in snapshots or snapshots[path] != content for path, content in existing.items()):
                        raise LibraryConflict("El recurso cambió después del borrado interrumpido; no se sobrescribirá.")
                    for path, content in snapshots.items():
                        if path not in existing:
                            self._atomic_bytes(path, content)
                    self._remove_metadata_stage(destination, journal)
                    summary["recovered"].append(destination.name)
                except (LibraryError, OSError) as exc:
                    summary["skipped"].append({"transaction": destination.name, "reason": str(exc)})
            try:
                parent.rmdir()
            except OSError:
                pass
        return summary

    def _erase_metadata(self, paths: list[Path]) -> int:
        """Stage exact owned paths with durable recovery and ordinary rollback.

        The staging directory is transient, never a hidden resource archive.
        A failed unlink must not turn into a successful-but-invisible deletion.
        """
        snapshots = self._metadata_snapshot(paths)
        destination = self._path(".deleting", uuid.uuid4().hex)
        journal = self._metadata_journal(paths, snapshots, destination.name)
        self._check_metadata_journal(destination, journal)
        destination.mkdir(parents=True)
        moved = []

        try:
            self._write(destination / "journal.json", journal)
            for index, source in enumerate(paths):
                self._path(str(source.relative_to(self.root)))
                if source.exists():
                    target = destination / str(index)
                    os.replace(source, target)
                    moved.append((source, target))
            self._remove_metadata_stage(destination, journal)
        except BaseException:
            # Some staged files may already be gone. Restore all bytes, rather
            # than assuming that moving the remaining directory is sufficient.
            for source, target in reversed(moved):
                if source.is_file() or not source.exists():
                    if target.exists():
                        os.replace(target, source)
                elif target.exists():
                    raise LibraryError("No se pudo restaurar el recurso tras un fallo de borrado.")
            for original, content in snapshots.items():
                self._atomic_bytes(original, content)
            if destination.exists():
                self._remove_metadata_stage(destination, journal)
            raise
        return sum(len(content) for content in snapshots.values())

    def purge_legacy_trash(self) -> dict:
        """Remove only recognized, inactive resource archives from old versions."""
        summary = {"deleted": [], "skipped": [], "bytes_freed": 0}
        with self._lock:
            for kind in sorted(_KINDS):
                parent = self._path(".trash", kind)
                for archive in sorted(parent.glob("*")):
                    identifier, separator, suffix = archive.name.rpartition("-")
                    if (not separator or not re.fullmatch(r"[0-9a-f]{32}", suffix)
                            or not _ID.fullmatch(identifier)):
                        summary["skipped"].append({"path": str(archive.relative_to(self.root)),
                                                   "reason": "Archivo de procedencia desconocida."})
                        continue
                    self._path(str(archive.relative_to(self.root)))
                    current = archive / f"{identifier}.json"
                    if (not archive.is_dir() or archive.is_symlink() or archive.is_junction()
                            or not current.is_file() or self._read(current).get("id") != identifier):
                        summary["skipped"].append({"id": identifier, "reason": "Respaldo no reconocido."})
                        continue
                    if self._path(kind, f"{identifier}.json").exists():
                        summary["skipped"].append({"id": identifier, "reason": "El recurso sigue activo."})
                        continue
                    dependent = None
                    for other in _KINDS - {"projects"}:
                        paths = [*self._path(other).glob("*.json"),
                                 *self._path("revisions", other).rglob("*.json")]
                        for path in paths:
                            record = self._read(self._path(str(path.relative_to(self.root))))
                            if self._contains_id(record, identifier):
                                dependent = record.get("name", record.get("id"))
                                break
                        if dependent:
                            break
                    if dependent:
                        summary["skipped"].append({"id": identifier,
                                                   "reason": f"Todavía lo utiliza «{dependent}»."})
                        continue
                    allowed = {current, archive / identifier}
                    if any(child not in allowed for child in archive.iterdir()):
                        summary["skipped"].append({"id": identifier, "reason": "El respaldo contiene archivos ajenos."})
                        continue
                    if kind == "projects":
                        self.mark_initialized()
                    summary["bytes_freed"] += self._erase_metadata([archive])
                    summary["deleted"].append({"id": identifier, "kind": kind})
                try:
                    parent.rmdir()
                except OSError:
                    pass
            try:
                self._path(".trash").rmdir()
            except OSError:
                pass
        return summary

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

    @staticmethod
    def _image_reference(value: Any, asset_id: str, *, samples: bool) -> bool:
        """Only live image roles are dependencies; provenance is metadata."""
        if isinstance(value, dict):
            for field, child in value.items():
                if field in ("source_sample_ids", "imported_from"):
                    continue
                if field in ("master_id", "master_asset_id") and child == asset_id:
                    return True
                if field == "sample_ids":
                    if samples and isinstance(child, list) and asset_id in child:
                        return True
                    continue
                if Library._image_reference(child, asset_id, samples=samples):
                    return True
        elif isinstance(value, list):
            return any(Library._image_reference(child, asset_id, samples=samples) for child in value)
        return False

    def _used(self, asset_id: str, *, samples: bool = True) -> bool:
        folders = [self._path("history"), *(self._path(kind) for kind in _KINDS)]
        for folder in folders:
            for path in folder.glob("*.json"):
                record = self._read(self._path(str(path.relative_to(self.root))))
                if record.get("id") != asset_id and not record.get("purged_at"):
                    if self._image_reference(record, asset_id, samples=samples):
                        return True
        # Accepted samples remain protected by saved revisions. Pending/rejected
        # previews can be discarded while those revisions keep their settings.
        for path in self._path("revisions").rglob("*.json"):
            record = self._read(self._path(str(path.relative_to(self.root))))
            if record.get("id") != asset_id and self._image_reference(record, asset_id, samples=samples):
                return True
        return False

    def purge_rejected(self, ids: list[str], min_age_days: int = 0) -> dict:
        return self._purge_generations(ids, min_age_days, rejected_only=True)

    def delete_generation(self, asset_id: str) -> dict:
        """Explicit deletion does not require rejecting an image or waiting days."""
        with self._lock:
            summary = self._purge_generations([asset_id], rejected_only=False)
            if summary["protected"]:
                if self.get_generation(asset_id).get("status") != "accepted":
                    raise LibraryConflict(
                        "Esta imagen se utiliza como maestro de otro resultado. "
                        "Borra primero los resultados derivados que la utilizan.")
                raise LibraryConflict(
                    "Esta imagen aceptada es una muestra guardada o un maestro utilizado. "
                    "Borra primero el estilo o los resultados que la utilizan.")
            return {"deleted": True, "id": asset_id, "bytes_freed": summary["bytes_freed"]}

    def finish_generation_deletion(self, ids: list[str]) -> dict:
        """Forget deleted image records only after external cleanup succeeded.

        Until this explicit finalization, tombstones retain the provenance needed
        to retry cleanup of ComfyUI outputs and expression uploads.
        """
        if not isinstance(ids, list) or len(ids) > 1000:
            raise LibraryError("Selecciona hasta 1000 imágenes para finalizar el borrado.")
        summary = {"deleted": [], "bytes_freed": 0}
        with self._lock:
            identifiers = list(dict.fromkeys(_identifier(value) for value in ids))
            records = []
            for asset_id in identifiers:
                index = self._generation_path(asset_id)
                if not index.exists():
                    continue
                record = self.get_generation(asset_id)
                if not record.get("purged_at"):
                    raise LibraryConflict("No se puede retirar la ficha de una imagen que sigue disponible.")
                records.append((asset_id, index, record))
            cleanup = []
            for asset_id, index, record in records:
                # Generated IDs are appended to every library image filename.
                # This also recovers interrupted old unlinks before finalizing.
                owned_name = re.compile(rf"(?:^|_){re.escape(asset_id)}(?:\.[a-z][a-z0-9_]*)?\.(?:png|json)$")
                for folder in ("history", "accepted", "rejected"):
                    for path in self._path(folder).rglob(f"*{asset_id}*"):
                        if path == index or not owned_name.search(path.name):
                            continue
                        self._path(str(path.relative_to(self.root)))
                        if path.is_symlink() or path.is_junction() or not path.is_file():
                            raise LibraryError("No se puede borrar un enlace como imagen del recurso.")
                        cleanup.append(path)
            for path in dict.fromkeys(cleanup):
                self._path(str(path.relative_to(self.root)))
                summary["bytes_freed"] += path.stat().st_size
                path.unlink()
            if records:
                summary["bytes_freed"] += self._erase_metadata([index for _, index, _ in records])
                summary["deleted"] = [asset_id for asset_id, _, _ in records]
        return summary

    def _purge_generations(self, ids: list[str], min_age_days: int = 0,
                           *, rejected_only: bool) -> dict:
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
                if (rejected_only and record.get("status") != "rejected") or record.get("purged_at"):
                    summary["skipped"].append({"id": asset_id, "reason": "No es un rechazado disponible."})
                    continue
                rejected_at = datetime.fromisoformat(record.get("rejected_at") or record["created_at"])
                if rejected_only and (datetime.now(timezone.utc) - rejected_at).total_seconds() < min_age_days * 86400:
                    summary["skipped"].append({"id": asset_id, "reason": "Todavía no alcanza la antigüedad elegida."})
                    continue
                if self._used(asset_id, samples=record.get("status") == "accepted"):
                    summary["protected"].append(asset_id)
                    continue
                paths = [self._path(record["file"]),
                         *(self._path(relative) for relative in record.get("variants", {}).values())]
                # No external paths, directories or authoritative history JSON may be removed.
                for path in paths:
                    if path.suffix.lower() != ".png" or path == self._generation_path(asset_id):
                        raise LibraryError("Solo se pueden eliminar los archivos PNG del recurso.")
                    if path.exists() and not path.is_file():
                        raise LibraryError("No se puede eliminar una carpeta como imagen.")
                freed = sum(path.stat().st_size for path in paths if path.exists())
                tombstone = {**record, "purged_at": _now(), "file": None, "variants": {}}
                # Keep the complete settings/seed record even after images are purged.
                self._write(self._generation_path(asset_id), tombstone)
                for path in paths:
                    path.unlink(missing_ok=True)
                sidecar = self._path(record["file"]).with_suffix(".json")
                if sidecar != self._generation_path(asset_id):
                    sidecar.unlink(missing_ok=True)
                summary["purged"].append(asset_id)
                summary["bytes_freed"] += freed
            return summary
