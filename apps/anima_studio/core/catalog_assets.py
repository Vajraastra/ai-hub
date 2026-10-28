"""Read-only display metadata and local previews for verified catalogue entries."""
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3

from PIL import Image


PREVIEWS = (".preview.jpeg", ".preview.jpg", ".preview.png", ".preview.webp", ".preview.gif")


class CatalogAssets:
    def __init__(self, root, vault_db=None):
        self.root = Path(root).resolve()
        self.vault_db = Path(vault_db) if vault_db else None
        self._previews = {}

    def _inside(self, path):
        try:
            path = Path(path).resolve()
            return path if path.is_relative_to(self.root) else None
        except (OSError, ValueError, RuntimeError):
            return None

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(path))

    def _vault_records(self):
        """Never construct VaultDatabase: its initializer performs migrations."""
        if self.vault_db is None or not self.vault_db.is_file():
            return {}
        try:
            with closing(sqlite3.connect(self.vault_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                models = {row["hash"]: dict(row) for row in db.execute("SELECT * FROM models")}
                locations = {}
                for record in models.values():
                    path = record.get("file_path")
                    record["locations"] = [path] if path else []
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "model_files" in tables:
                    for row in db.execute("SELECT hash, file_path FROM model_files"):
                        if row["hash"] in models:
                            models[row["hash"]]["locations"].append(row["file_path"])
                for record in models.values():
                    record["locations"] = list(dict.fromkeys(record["locations"]))
                    for path in record["locations"]:
                        if self._inside(path):
                            locations[self._key(path)] = record
                return locations
        except (OSError, ValueError, sqlite3.Error):
            return {}

    @staticmethod
    def _sidecar(path):
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                return {}
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _tags(value):
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                value = decoded if isinstance(decoded, list) else value.split(",")
            except ValueError:
                value = value.split(",")
        return [str(tag).strip() for tag in value if isinstance(tag, (str, int)) and str(tag).strip()] if isinstance(value, list) else []

    def _details(self, path, record):
        name, version, tags = "", "", []
        known_names = {path.name.casefold(), path.stem.casefold()}
        display = record.get("display_name")
        if isinstance(display, str) and display.strip().casefold() not in known_names:
            name = display.strip()
        version = record.get("version_name") or ""
        tags += self._tags(record.get("custom_tags")) + self._tags(record.get("civitai_tags"))
        paths = [path] + [Path(alias) for alias in record.get("locations", ()) if self._inside(alias)]
        for location in dict.fromkeys(paths):
            for suffix in (".cm-info.json", ".metadata.json"):
                sidecar = self._inside(location.with_suffix(suffix))
                if sidecar is None:
                    continue
                data = self._sidecar(sidecar)
                meta = data.get("civitai", data)
                if not isinstance(meta, dict):
                    continue
                model = meta.get("model") if isinstance(meta.get("model"), dict) else {}
                candidate = meta.get("ModelName") or meta.get("modelName") or meta.get("display_name") or model.get("name")
                if not name and isinstance(candidate, str):
                    name = candidate.strip()
                candidate = meta.get("VersionName") or meta.get("versionName") or meta.get("version_name")
                if not version and isinstance(candidate, str):
                    version = candidate.strip()
                tags += self._tags(meta.get("tags")) + self._tags(model.get("tags"))
                tags += self._tags(meta.get("TrainedWords") or meta.get("trainedWords") or meta.get("triggers"))
        name = name or path.stem
        if version and str(version).casefold() not in name.casefold():
            name += " · " + str(version)
        return name, list(dict.fromkeys(tags))

    def _preview(self, path, record):
        for location in dict.fromkeys([path, *record.get("locations", ())]):
            location = self._inside(location)
            if location is None:
                continue
            for suffix in PREVIEWS:
                preview = self._inside(location.with_suffix(suffix))
                if preview is not None and preview.is_file():
                    return preview
        return None

    def enrich(self, catalogue):
        records, previews = self._vault_records(), {}
        for group in ("models", "loras"):
            for entry in catalogue.get(group, ()):
                relative = entry.get("relative_path")
                if not relative:
                    relative = str(Path(entry.get("folder", group)) / entry.get("backend_file", entry["file"]))
                path = self._inside(self.root / relative)
                entry.update(display_name=entry.get("name", ""), tags=[], thumbnail_url=None)
                if path is None:
                    continue
                record = records.get(self._key(path), {})
                entry["display_name"], entry["tags"] = self._details(path, record)
                preview = self._preview(path, record)
                if preview is not None:
                    identifier = hashlib.sha256(path.relative_to(self.root).as_posix().encode()).hexdigest()
                    try:
                        stamp = preview.stat()
                    except OSError:
                        continue
                    previews[identifier] = preview
                    entry["thumbnail_url"] = f"/api/anima-studio/catalog/thumbnail/{identifier}?v={stamp.st_mtime_ns}-{stamp.st_size}"
        self._previews = previews
        return catalogue

    def thumbnail(self, identifier):
        path = self._inside(self._previews.get(identifier, self.root))
        if path is None or not path.is_file():
            raise ValueError("Miniatura no disponible")
        try:
            # Several existing .jpeg previews actually contain PNG or WebP.
            with Image.open(path) as image:
                media_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}.get(image.format)
                if not media_type:
                    raise ValueError("Formato de miniatura no compatible")
                image.verify()
            return path, media_type
        except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError("Miniatura dañada o no disponible") from exc
