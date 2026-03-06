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
                    model_type TEXT, -- New: LoRA, Checkpoint, etc.
                    version_id INTEGER,
                    model_id INTEGER,
                    version_name TEXT,
                    triggers TEXT, -- Comma separated
                    description TEXT,
                    user_notes TEXT,
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
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_folders (
                    path TEXT PRIMARY KEY,
                    last_scan TIMESTAMP
                )
            """)

    def upsert_model(self, model_data):
        """
        model_data: dict with hash, file_path, name, base_model, model_type, version_id, model_id, version_name, triggers, description
        """
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO models (hash, file_path, name, base_model, model_type, version_id, model_id, version_name, triggers, description)
                VALUES (:hash, :file_path, :name, :base_model, :model_type, :version_id, :model_id, :version_name, :triggers, :description)
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
                    last_scan=CURRENT_TIMESTAMP
            """, model_data)

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
