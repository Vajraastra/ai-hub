import sqlite3
import os

class VaultDatabase:
    """
    SQLite database to cache model metadata for fast UI loading
    and tracking versions/updates.
    """
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

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

    def update_civitai_data(self, model_hash: str, data: dict):
        """Actualiza solo campos Civitai; no toca user_notes ni custom_tags."""
        fields = {
            "name", "display_name", "base_model", "model_type",
            "version_id", "model_id", "version_name", "triggers",
            "description", "creator_name", "civitai_tags",
        }
        updates = {k: v for k, v in data.items() if k in fields}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=:{k}" for k in updates)
        updates["_hash"] = model_hash
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE models SET {set_clause}, last_scan=CURRENT_TIMESTAMP WHERE hash=:_hash",
                updates,
            )

    def update_custom_tags(self, model_hash, tags):
        with self._get_conn() as conn:
            conn.execute("UPDATE models SET custom_tags = ? WHERE hash = ?", (tags, model_hash))

    def update_user_notes(self, model_hash, notes):
        with self._get_conn() as conn:
            conn.execute("UPDATE models SET user_notes = ? WHERE hash = ?", (notes, model_hash))

    def get_all_models(self):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM models")
            return [dict(row) for row in cursor.fetchall()]

    def get_model_by_hash(self, file_hash):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM models WHERE hash = ?", (file_hash,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_model_by_path(self, file_path):
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM models WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()
            return dict(row) if row else None
