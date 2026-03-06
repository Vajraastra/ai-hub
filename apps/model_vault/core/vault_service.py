import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from .hasher import calculate_sha256
from .scanner import scan_models
from .civitai_client import CivitaiClient
from .database import VaultDatabase
from .metadata_processor import MetadataProcessor

class VaultService:
    """
    The orchestrator for the Model Vault.
    Handles scanning, hashing, API synchronization, and database updates.
    """
    def __init__(self, db_path, api_key=None):
        self.db = VaultDatabase(db_path)
        self.client = CivitaiClient(api_key)
        self.processor = MetadataProcessor()
        self.executor = ThreadPoolExecutor(max_workers=4)

    def scan_and_index(self, root_dir, deep_scan=False, progress_callback=None):
        """
        Scans a directory and indexes models.
        If deep_scan=True, it calculates hashes for all files.
        Otherwise, only for files without metadata.
        """
        raw_models = scan_models(root_dir)
        total = len(raw_models)
        
        for i, model in enumerate(raw_models):
            if progress_callback:
                progress_callback(i + 1, total, model["name"])

            # Check if already in DB and not needing update
            existing = self.db.get_model_by_hash_path(model["path"]) # Need this method
            
            # Logic for when to re-hash
            file_hash = None
            if existing and not deep_scan:
                file_hash = existing["hash"]
            else:
                file_hash = calculate_sha256(model["path"])

            if file_hash:
                # Sync with Civitai if needed
                self.sync_model(model["path"], file_hash)

    def sync_model(self, file_path, file_hash):
        """
        Fetches metadata from Civitai using hash and updates local sidecars + DB.
        """
        # 1. Try to find in Civitai
        version_data = self.client.get_model_version_by_hash(file_hash)
        if not version_data:
            # Not found or error
            return

        # 2. Extract and clean metadata
        meta = self.client.extract_metadata(version_data)
        
        # 3. Update DB
        model_record = {
            "hash": file_hash,
            "file_path": file_path,
            "name": os.path.basename(file_path),
            "base_model": meta["baseModel"],
            "model_type": meta["modelType"],
            "version_id": meta["modelVersionId"],
            "model_id": version_data.get("modelId"),
            "version_name": meta["versionName"],
            "triggers": self.processor.format_triggers(meta["triggers"]),
            "description": meta["description"]
        }
        self.db.upsert_model(model_record)

        # 4. Save local JSON sidecars (Stability Matrix compatibility)
        self._update_local_sidecars(file_path, version_data, model_record)

    def _update_local_sidecars(self, file_path, civitai_data, db_record):
        base_name = os.path.splitext(file_path)[0]
        
        # .cm-info.json
        cm_info_path = base_name + ".cm-info.json"
        cm_data = {
            "ModelId": db_record["model_id"],
            "ModelName": civitai_data.get("model", {}).get("name", ""),
            "ModelType": civitai_data.get("model", {}).get("type", ""),
            "VersionId": db_record["version_id"],
            "VersionName": db_record["version_name"],
            "BaseModel": db_record["base_model"],
            "Hashes": {"SHA256": db_record["hash"]},
            "TrainedWords": db_record["triggers"].split(", ") if db_record["triggers"] else []
        }
        with open(cm_info_path, "w", encoding="utf-8") as f:
            json.dump(cm_data, f, indent=2)

        # .metadata.json
        meta_path = base_name + ".metadata.json"
        meta_data = {
            "file_name": os.path.basename(file_path),
            "sha256": db_record["hash"],
            "base_model": db_record["base_model"],
            "from_civitai": True,
            "civitai": civitai_data
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, indent=2)
            
        # Preview image
        preview_path = base_name + ".preview.jpeg"
        if not os.path.exists(preview_path):
            images = civitai_data.get("images", [])
            for img in images:
                image_url = img.get("url")
                if image_url:
                    # Attempt to download, download_image already filters for videos/formats
                    if self.client.download_image(image_url, preview_path):
                        break # Found a valid one
