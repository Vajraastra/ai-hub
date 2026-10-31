"""Named, global prompt presets, independent of projects and generated assets."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class PromptLibraryError(ValueError):
    pass


class PromptNotFound(PromptLibraryError):
    pass


class PromptConflict(PromptLibraryError):
    pass


_ID = re.compile(r"^[0-9a-f]{32}$")
_TEXT_FIELDS = ("style_prompt", "style_negative", "test_prompt")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _identifier(value) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PromptLibraryError("Identificador de prompt no válido.")
    return value


def _revision(value) -> int:
    if type(value) is not int or value < 1:
        raise PromptLibraryError("Indica la revisión vigente del prompt.")
    return value


class PromptLibrary:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root / "prompts"
        key = os.path.normcase(str(self.root))
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.RLock())

    def _safe_path(self, path: Path) -> Path:
        """Reject links/junctions as well as paths which resolve outside the data root."""
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise PromptLibraryError("La ruta sale de la biblioteca de prompts.") from exc
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise PromptLibraryError("La biblioteca de prompts no admite enlaces ni junctions.")
        resolved = path.resolve()
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise PromptLibraryError("La ruta sale de la biblioteca de prompts.")
        return path

    def _path(self, entry_id: str) -> Path:
        return self._safe_path(self.directory / f"{_identifier(entry_id)}.json")

    @staticmethod
    def _validate(value: dict) -> dict:
        if not isinstance(value, dict):
            raise PromptLibraryError("Los datos del prompt deben ser un objeto.")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise PromptLibraryError("Escribe un nombre de entre 1 y 120 caracteres.")
        result = {"name": name.strip()}
        for field in _TEXT_FIELDS:
            text = value.get(field, "")
            if not isinstance(text, str) or len(text) > 65536:
                raise PromptLibraryError("Cada prompt debe ser texto de hasta 65 536 caracteres.")
            result[field] = text
        if not any(result[field].strip() for field in _TEXT_FIELDS):
            raise PromptLibraryError("Escribe al menos un prompt o un negativo para guardar.")
        return result

    def _read(self, path: Path) -> dict:
        self._safe_path(path)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PromptNotFound("No se encontró el prompt guardado.") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PromptLibraryError("Un prompt guardado está dañado.") from exc
        if not isinstance(record, dict):
            raise PromptLibraryError("Un prompt guardado está dañado.")
        # Validate stored records too, so corrupt data cannot become an overwrite source.
        self._validate(record)
        if record.get("id") != path.stem or not _ID.fullmatch(path.stem):
            raise PromptLibraryError("Un prompt guardado tiene un identificador no válido.")
        _revision(record.get("revision"))
        if not all(isinstance(record.get(field), str) for field in ("created_at", "updated_at")):
            raise PromptLibraryError("Un prompt guardado no conserva sus fechas.")
        return record

    def list(self) -> list[dict]:
        with self._lock:
            directory = self._safe_path(self.directory)
            if not directory.exists():
                return []
            records = [self._read(path) for path in directory.glob("*.json")]
            records.sort(key=lambda value: (value["name"].casefold(), value["id"]))
            return records

    def get(self, entry_id: str) -> dict:
        with self._lock:
            return self._read(self._path(entry_id))

    def _write(self, path: Path, value: dict):
        self._safe_path(self.directory).mkdir(parents=True, exist_ok=True)
        self._safe_path(path)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            with self._safe_path(temp).open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # A directory might have been changed since the temporary file was opened.
            self._safe_path(temp)
            self._safe_path(path)
            os.replace(temp, path)
        finally:
            self._safe_path(temp).unlink(missing_ok=True)

    def save(self, payload: dict) -> dict:
        fields = self._validate(payload)
        with self._lock:
            previous = None
            if "id" in payload:
                entry_id = _identifier(payload["id"])
                previous = self._read(self._path(entry_id))
                expected = _revision(payload.get("expected_revision"))
                if previous["revision"] != expected:
                    raise PromptConflict("El prompt cambió en otra ventana. Actualiza la biblioteca antes de guardarlo.")
            else:
                if "expected_revision" in payload:
                    raise PromptLibraryError("La revisión sólo corresponde a un prompt ya guardado.")
                entry_id = uuid.uuid4().hex
            for record in self.list():
                if record["id"] != entry_id and record["name"].strip().casefold() == fields["name"].casefold():
                    raise PromptConflict("Ya existe un prompt con ese nombre. Elige otro nombre o actualiza el guardado.")
            now = _now()
            value = {"id": entry_id, **fields, "revision": previous["revision"] + 1 if previous else 1,
                     "created_at": previous["created_at"] if previous else now, "updated_at": now}
            self._write(self._path(entry_id), value)
            return copy.deepcopy(value)

    def delete(self, entry_id: str, expected_revision: int) -> dict:
        with self._lock:
            path = self._path(entry_id)
            previous = self._read(path)
            if previous["revision"] != _revision(expected_revision):
                raise PromptConflict("El prompt cambió en otra ventana. Actualiza la biblioteca antes de borrarlo.")
            self._safe_path(path).unlink()
            return {"id": entry_id, "deleted": True}
