import sqlite3
import os
from contextlib import contextmanager

class VaultDatabase:
    """
    SQLite database to cache model metadata for fast UI loading
    and tracking versions/updates.
    """
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS models (
                    hash TEXT PRIMARY KEY,
                    file_path TEXT,
                    name TEXT,
                    base_model TEXT,
                    model_type TEXT,
                    version_id INTEGER,
                    model_id INTEGER,
                    version_name TEXT,
                    triggers TEXT,
                    description TEXT,
                    user_notes TEXT,
                    creator_name TEXT, -- New
                    custom_tags TEXT,  -- New
                    display_name TEXT, -- New
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- New
                    last_scan TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Migration check
            cursor = conn.execute("PRAGMA table_info(models)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if "model_type" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'model_type'...")
                conn.execute("ALTER TABLE models ADD COLUMN model_type TEXT")
            
            if "description" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'description'...")
                conn.execute("ALTER TABLE models ADD COLUMN description TEXT")
                
            if "user_notes" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'user_notes'...")
                conn.execute("ALTER TABLE models ADD COLUMN user_notes TEXT")

            if "creator_name" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'creator_name'...")
                conn.execute("ALTER TABLE models ADD COLUMN creator_name TEXT")

            if "custom_tags" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'custom_tags'...")
                conn.execute("ALTER TABLE models ADD COLUMN custom_tags TEXT")

            if "display_name" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'display_name'...")
                conn.execute("ALTER TABLE models ADD COLUMN display_name TEXT")
                # Pre-fill display_name with name if it was null
                conn.execute("UPDATE models SET display_name = name WHERE display_name IS NULL")

            if "date_added" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'date_added'...")
                conn.execute("ALTER TABLE models ADD COLUMN date_added TIMESTAMP DEFAULT '2020-01-01 00:00:00'")
                conn.execute("UPDATE models SET date_added = CURRENT_TIMESTAMP WHERE date_added = '2020-01-01 00:00:00'")

            if "civitai_tags" not in columns:
                print("[db] Migrando base de datos: Añadiendo columna 'civitai_tags'...")
                conn.execute("ALTER TABLE models ADD COLUMN civitai_tags TEXT")
            if "civitai_synced_at" not in columns:
                conn.execute("ALTER TABLE models ADD COLUMN civitai_synced_at TEXT")
            for column in ("civitai_assets_synced_at", "preview_status"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE models ADD COLUMN {column} TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_files (
                    path_key TEXT PRIMARY KEY, file_path TEXT NOT NULL, hash TEXT NOT NULL,
                    file_size INTEGER, file_mtime_ns INTEGER, file_id TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS model_files_hash ON model_files(hash)")
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_folders (
                    path TEXT PRIMARY KEY,
                    last_scan TIMESTAMP
                )
            """)

    def upsert_model(self, model_data):
        """
        model_data: dict with hash, file_path, name, base_model, model_type, version_id, 
        model_id, version_name, triggers, description, creator_name, display_name
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO models (hash, file_path, name, base_model, model_type, version_id, 
                                  model_id, version_name, triggers, description, creator_name, display_name, custom_tags)
                VALUES (:hash, :file_path, :name, :base_model, :model_type, :version_id, 
                        :model_id, :version_name, :triggers, :description, :creator_name, :display_name, :custom_tags)
                ON CONFLICT(hash) DO UPDATE SET
                    file_path=excluded.file_path,
                    name=excluded.name,
                    base_model=excluded.base_model,
                    model_type=excluded.model_type,
                    version_id=excluded.version_id,
                    model_id=excluded.model_id,
                    version_name=excluded.version_name,
                    triggers=excluded.triggers,
                    description=excluded.description,
                    creator_name=excluded.creator_name,
                    display_name=excluded.display_name,
                    custom_tags=excluded.custom_tags,
                    last_scan=CURRENT_TIMESTAMP
            """, model_data)

    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def _canonical(paths, current=""):
        """Keep a usable principal path, preferring originals over Krita hardlinks."""
        existing = [path for path in paths if os.path.isfile(path)]
        preferred = [path for path in existing if "krita" not in path.replace("\\", "/").lower().split("/")]
        choices = preferred or existing or paths
        return current if current in choices else (sorted(choices, key=str.casefold)[0] if choices else "")

    def get_file_records(self):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM model_files")]

    def index_local_model(self, model_data, file_info=None, previous_hash=None):
        """Refresh inventory and fill missing metadata; preserve user/Civitai edits."""
        fields = ("display_name", "creator_name", "base_model", "model_type", "version_id",
                  "model_id", "version_name", "triggers", "description", "civitai_tags")
        data = {key: model_data.get(key) for key in ("hash", "file_path", "name", *fields)}
        data["display_name"] = data["display_name"] or data["name"]
        data["base_model"] = data["base_model"] or "Unknown"
        data["model_type"] = data["model_type"] or "unknown"
        # Only inventory fields are authoritative during a local rescan. Richer metadata
        # is filled once; manual notes/tags and existing remote metadata stay untouched.
        updates = ["last_scan=CURRENT_TIMESTAMP"]
        for field in fields:
            missing = f"lower(trim(coalesce(models.{field}, ''))) IN ('', 'unknown')"
            if field == "display_name":
                missing += " OR models.display_name=models.name"
            updates.append(f"{field}=CASE WHEN {missing} THEN excluded.{field} ELSE models.{field} END")
        columns = ("hash", "file_path", "name", *fields)
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            old = None
            if previous_hash and previous_hash != data["hash"]:
                old = conn.execute("SELECT * FROM models WHERE hash=?", (previous_hash,)).fetchone()
            conn.execute(
                f"INSERT INTO models ({', '.join(columns)}) VALUES ({', '.join(':'+k for k in columns)}) "
                f"ON CONFLICT(hash) DO UPDATE SET {', '.join(updates)}", data,
            )
            info = file_info or {}
            conn.execute("""
                INSERT INTO model_files(path_key, file_path, hash, file_size, file_mtime_ns, file_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path_key) DO UPDATE SET file_path=excluded.file_path, hash=excluded.hash,
                    file_size=excluded.file_size, file_mtime_ns=excluded.file_mtime_ns, file_id=excluded.file_id
            """, (self._path_key(data["file_path"]), data["file_path"], data["hash"],
                  info.get("file_size"), info.get("file_mtime_ns"), info.get("file_id")))
            if old:
                # A replaced file keeps personal annotations, never the old model's remote identity.
                target = conn.execute("SELECT user_notes, custom_tags FROM models WHERE hash=?", (data["hash"],)).fetchone()
                conflict = any(old[field] and target[field] and old[field] != target[field]
                               for field in ("user_notes", "custom_tags"))
                conn.execute("""UPDATE models SET
                    user_notes=coalesce(nullif(user_notes, ''), ?),
                    custom_tags=coalesce(nullif(custom_tags, ''), ?) WHERE hash=?""",
                    (old["user_notes"], old["custom_tags"], data["hash"]))
                remaining = [row[0] for row in conn.execute(
                    "SELECT file_path FROM model_files WHERE hash=?", (previous_hash,))]
                if remaining:
                    canonical = self._canonical(remaining, old["file_path"])
                    conn.execute("UPDATE models SET file_path=?, name=? WHERE hash=?",
                                 (canonical, os.path.basename(canonical), previous_hash))
                elif conflict:
                    # Keep the old identity recoverable when both models have distinct annotations.
                    conn.execute("UPDATE models SET file_path='' WHERE hash=?", (previous_hash,))
                else:
                    conn.execute("DELETE FROM models WHERE hash=?", (previous_hash,))
            current = conn.execute("SELECT file_path FROM models WHERE hash=?", (data["hash"],)).fetchone()[0]
            locations = [row[0] for row in conn.execute("SELECT file_path FROM model_files WHERE hash=?", (data["hash"],))]
            canonical = self._canonical(locations, current)
            conn.execute("UPDATE models SET file_path=?, name=? WHERE hash=?",
                         (canonical, os.path.basename(canonical), data["hash"]))

    def update_civitai_data(self, model_hash: str, data: dict):
        """Actualiza solo campos Civitai; no toca user_notes ni custom_tags."""
        fields = {
            "name", "display_name", "base_model", "model_type",
            "version_id", "model_id", "version_name", "triggers",
            "description", "creator_name", "civitai_tags",
        }
        # An incomplete provider response must never erase useful local information.
        updates = {k: v for k, v in data.items() if k in fields
                   and v not in (None, "", [], {})
                   and not (isinstance(v, str) and v.strip().casefold() == "unknown")}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=:{k}" for k in updates)
        updates["_hash"] = model_hash
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE models SET {set_clause}, last_scan=CURRENT_TIMESTAMP WHERE hash=:_hash",
                updates,
            )

    def mark_civitai_synced(self, model_hash):
        with self._get_conn() as conn:
            conn.execute("UPDATE models SET civitai_synced_at=CURRENT_TIMESTAMP WHERE hash=?", (model_hash,))

    def mark_civitai_assets(self, model_hash, preview_status, complete=True):
        with self._get_conn() as conn:
            timestamp = "CURRENT_TIMESTAMP" if complete else "NULL"
            conn.execute(f"UPDATE models SET civitai_assets_synced_at={timestamp}, preview_status=? WHERE hash=?",
                         (preview_status, model_hash))

    def update_custom_tags(self, model_hash, tags):
        with self._get_conn() as conn:
            conn.execute("UPDATE models SET custom_tags = ? WHERE hash = ?", (tags, model_hash))

    def update_user_notes(self, model_hash, notes):
        with self._get_conn() as conn:
            conn.execute("UPDATE models SET user_notes = ? WHERE hash = ?", (notes, model_hash))

    def update_file_path(self, model_hash, new_path, old_path=None):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            model = conn.execute("SELECT * FROM models WHERE hash=?", (model_hash,)).fetchone()
            if not model:
                return
            old_path = old_path or model["file_path"]
            updated = conn.execute("UPDATE model_files SET path_key=?, file_path=? WHERE path_key=? AND hash=?",
                                   (self._path_key(new_path), new_path, self._path_key(old_path), model_hash))
            if not updated.rowcount:
                info = os.stat(new_path)
                conn.execute("""INSERT INTO model_files(path_key, file_path, hash, file_size, file_mtime_ns, file_id)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             (self._path_key(new_path), new_path, model_hash, info.st_size, info.st_mtime_ns,
                              f"{info.st_dev}:{info.st_ino}" if info.st_ino else None))
            conn.execute(
                "UPDATE models SET file_path=?, name=?, last_scan=CURRENT_TIMESTAMP WHERE hash=?",
                (new_path, os.path.basename(new_path), model_hash),
            )

    def _with_locations(self, conn, models):
        by_hash = {}
        hashes = [model["hash"] for model in models]
        if not hashes:
            return models
        for row in conn.execute(f"SELECT hash, file_path FROM model_files WHERE hash IN ({','.join('?' for _ in hashes)})", hashes):
            by_hash.setdefault(row[0], []).append(row[1])
        for model in models:
            locations = by_hash.get(model["hash"], [])
            if not locations and model.get("file_path"):
                locations = [model["file_path"]]
            canonical = self._canonical(locations, model.get("file_path") or "")
            model["file_path"] = canonical
            model["name"] = os.path.basename(canonical) if canonical else model.get("name")
            model["file_paths"] = [canonical] + [path for path in locations if path != canonical] if canonical else locations
            model["missing"] = not any(os.path.isfile(path) for path in locations)
        return models

    def get_all_models(self):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM models ORDER BY date_added DESC, rowid DESC")
            return self._with_locations(conn, [dict(row) for row in cursor.fetchall()])

    def get_model_by_hash(self, file_hash):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM models WHERE hash = ?", (file_hash,))
            row = cursor.fetchone()
            return self._with_locations(conn, [dict(row)])[0] if row else None

    def get_model_by_path(self, file_path):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""SELECT models.* FROM models JOIN model_files USING(hash)
                                     WHERE model_files.path_key=?""", (self._path_key(file_path),))
            row = cursor.fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM models WHERE replace(file_path, char(92), '/')=? COLLATE NOCASE",
                                   (file_path.replace("\\", "/"),)).fetchone()
            return self._with_locations(conn, [dict(row)])[0] if row else None
