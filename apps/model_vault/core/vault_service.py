import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from .hasher import calculate_sha256
from .scanner import scan_models
from .civitai_client import CivitaiClient, CivitaiError
from .database import VaultDatabase
from .metadata_processor import MetadataProcessor
from .header_classifier import classify

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
        """Index local files without network calls or changes to model sidecars.

        Known paths reuse their hash unless deep_scan explicitly requests a rehash.
        Civitai synchronization is a separate, optional operation.
        """
        raw_models = scan_models(root_dir)
        total = len(raw_models)
        known = {self.db._path_key(m["file_path"]): m
                 for m in self.db.get_all_models() if m.get("file_path")}
        known.update({entry["path_key"]: entry for entry in self.db.get_file_records()})
        physical_hashes = {(entry.get("file_id"), entry.get("file_size"), entry.get("file_mtime_ns")): entry["hash"]
                           for entry in known.values() if entry.get("file_id")}
        verified = {}
        stats = {"indexed": 0, "added": 0, "errors": 0, "error_detail": []}

        for i, model in enumerate(raw_models):
            if progress_callback:
                progress_callback(i + 1, total, model["name"])
            try:
                existing = known.get(self.db._path_key(model["path"]))
                info = os.stat(model["path"])
                file_info = {"file_size": info.st_size, "file_mtime_ns": info.st_mtime_ns,
                             "file_id": f"{info.st_dev}:{info.st_ino}" if info.st_ino else None}
                identity = (file_info["file_id"], info.st_size, info.st_mtime_ns)
                unchanged = existing and (existing.get("file_size") is None or (
                    existing.get("file_size") == info.st_size and existing.get("file_mtime_ns") == info.st_mtime_ns
                    and existing.get("file_id") in (None, file_info["file_id"])))
                if not deep_scan and unchanged:
                    file_hash = existing["hash"]
                else:
                    file_hash = verified.get(identity) if info.st_ino else None
                    if not file_hash and not deep_scan and info.st_ino:
                        file_hash = physical_hashes.get(identity)
                    if not file_hash:
                        file_hash = calculate_sha256(model["path"])
                    after = os.stat(model["path"])
                    if (after.st_size, after.st_mtime_ns, after.st_ino) != (info.st_size, info.st_mtime_ns, info.st_ino):
                        raise OSError("El archivo cambió durante la revisión; vuelve a actualizar la biblioteca")
                if not file_hash:
                    raise OSError("No se pudo leer el archivo para calcular su hash")
                if info.st_ino:
                    verified[identity] = file_hash
                existing_hash = self.db.get_model_by_hash(file_hash)
                record = self._local_record(model, file_hash, root_dir, existing_hash)
                self.db.index_local_model(record, file_info=file_info,
                                          previous_hash=existing["hash"] if existing else None)
                stats["indexed"] += 1
                stats["added"] += int(existing_hash is None)
            except Exception as exc:
                stats["errors"] += 1
                stats["error_detail"].append({"path": model["path"], "error": str(exc)})
        return stats

    def _local_record(self, model, file_hash, root_dir, existing):
        """Read optional local metadata, accepting hashed sidecars only on a match."""
        record = {"hash": file_hash, "file_path": model["path"], "name": model["name"]}
        for key in ("metadata_path", "cm_info_path"):
            path = model.get(key)
            if not path:
                continue
            try:
                with open(path, encoding="utf-8-sig") as stream:
                    data = json.load(stream)
                if not isinstance(data, dict):
                    continue
                hashes = data.get("Hashes") or {}
                sidecar_hash = data.get("sha256") or (hashes.get("SHA256") if isinstance(hashes, dict) else None)
                if sidecar_hash and str(sidecar_hash).lower() != file_hash.lower():
                    continue
                version = data.get("civitai")
                if isinstance(version, dict):
                    meta = self.client.extract_metadata(version)
                    values = {
                        "display_name": meta.get("modelName"), "creator_name": meta.get("creator"),
                        "base_model": meta.get("baseModel"), "model_type": meta.get("modelType"),
                        "version_id": meta.get("modelVersionId"), "model_id": version.get("modelId"),
                        "version_name": meta.get("versionName"),
                        "triggers": self.processor.format_triggers(meta.get("triggers") or []),
                        "description": self.processor.clean_html(meta.get("description")),
                        "civitai_tags": ", ".join(meta.get("tags") or []),
                    }
                else:
                    values = {
                        "display_name": data.get("ModelName") or data.get("display_name"),
                        "creator_name": data.get("creator_name"),
                        "base_model": data.get("BaseModel") or data.get("base_model"),
                        "model_type": data.get("ModelType") or data.get("model_type"),
                        "version_id": data.get("VersionId"), "model_id": data.get("ModelId"),
                        "version_name": data.get("VersionName"),
                        "triggers": self.processor.format_triggers(data.get("TrainedWords") or []),
                    }
                for field, value in values.items():
                    if value is not None and value != "" and str(value).lower() != "unknown":
                        record.setdefault(field, value)
            except (OSError, ValueError, TypeError, AttributeError):
                # A damaged sidecar must not hide an otherwise readable model.
                continue

        unknown = (None, "", "unknown")
        needs_type = str((existing or {}).get("model_type") or record.get("model_type") or "").lower() in unknown
        needs_arch = str((existing or {}).get("base_model") or record.get("base_model") or "").lower() in unknown
        if needs_type or needs_arch:
            classification = classify(model["path"])
            if needs_arch:
                record.setdefault("base_model", classification.arch)
            if needs_type:
                local_type = classification.model_type
                if local_type == "unknown":
                    category = os.path.relpath(model["path"], root_dir).replace("\\", "/").split("/")[0].lower()
                    local_type = {"checkpoints": "checkpoint", "diffusion_models": "diffusion_model",
                                  "loras": "lora", "embeddings": "textualinversion", "vae": "vae",
                                  "clip": "clip", "text_encoders": "clip", "controlnet": "controlnet",
                                  "upscale_models": "upscaler", "ipadapter": "ipadapter"}.get(category, local_type)
                record.setdefault("model_type", "textualinversion" if local_type == "embedding" else local_type)
        return record

    def sync_model(self, file_path, file_hash, version_data=None):
        """
        Fetches metadata from Civitai using hash and updates local sidecars + DB.
        If version_data is provided, it skips the first API call.
        """
        # 1. Try to find in Civitai if not provided
        if not version_data:
            version_data = self.client.get_model_version_by_hash(file_hash)
        
        if not version_data:
            # Not found or error
            return

        # 2. Extract and clean metadata
        meta = self.client.extract_metadata(version_data)
        
        # 2.5 Fetch full model data to get the creator's name (by-hash usually omits this)
        model_id = version_data.get("modelId")
        if model_id:
            full_model_info = self.client.get_model_info(model_id)
            if full_model_info and "creator" in full_model_info:
                meta["creator"] = full_model_info["creator"].get("username", "Unknown")

        # 3. Update DB
        model_record = {
            "hash": file_hash,
            "file_path": file_path,
            "name": os.path.basename(file_path),
            "display_name": meta.get("modelName") or os.path.basename(file_path),
            "creator_name": meta.get("creator", "Unknown"),
            "base_model": meta["baseModel"],
            "model_type": meta["modelType"],
            "version_id": meta["modelVersionId"],
            "model_id": version_data.get("modelId"),
            "version_name": meta["versionName"],
            "triggers": self.processor.format_triggers(meta["triggers"]),
            "civitai_tags": ", ".join(meta.get("tags", [])) if meta.get("tags") else "",
            "description": meta["description"]
        }
        info = os.stat(file_path)
        self.db.index_local_model(model_record, file_info={
            "file_size": info.st_size, "file_mtime_ns": info.st_mtime_ns,
            "file_id": f"{info.st_dev}:{info.st_ino}" if info.st_ino else None,
        })
        self.db.update_civitai_data(file_hash, model_record)
        self.db.mark_civitai_synced(file_hash)

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
    @staticmethod
    def needs_civitai_sync(model):
        """An empty tag list is valid; successful metadata retrieval is tracked explicitly."""
        required = ("model_id", "version_id", "display_name", "base_model", "model_type")
        complete = all(str(model.get(key) or "").strip().lower() not in ("", "unknown") for key in required)
        legacy_synced = bool((model.get("civitai_tags") or "").strip())
        return not (complete and (model.get("civitai_synced_at") or legacy_synced))

    def pending_models(self, force=False):
        return [model for model in self.db.get_all_models()
                if not model.get("missing") and (force or self.needs_civitai_sync(model))]

    def sync_all(self, progress_cb=None, force=False) -> dict:
        """
        Re-sincroniza con Civitai todos los modelos ya registrados en DB.
        Solo completa datos pendientes, salvo que force=True.
        progress_cb(current, total, model_name) para actualizar UI.
        Retorna stats: {"synced": N, "skipped": N, "not_found": N, "errors": N}
        """
        available = [model for model in self.db.get_all_models() if not model.get("missing")]
        models = [model for model in available if force or self.needs_civitai_sync(model)]
        stats = {"synced": 0, "skipped": len(available) - len(models), "not_found": 0,
                 "errors": 0, "error_detail": []}
        total = len(models)

        for i, model in enumerate(models):
            if progress_cb:
                progress_cb(i + 1, total, model.get("display_name") or model.get("name", ""))

            try:
                version_data = self.client.get_model_version_by_hash(model["hash"])
                if not version_data:
                    stats["not_found"] += 1
                    time.sleep(0.7)
                    continue

                meta = self.client.extract_metadata(version_data)

                # Tags y creador requieren la llamada completa al modelo
                model_id = version_data.get("modelId")
                if model_id and (not meta.get("tags") or meta.get("creator") == "Unknown"):
                    full_info = self.client.get_model_info(model_id)
                    if full_info:
                        if not meta.get("tags"):
                            meta["tags"] = full_info.get("tags", [])
                        if meta.get("creator") == "Unknown" and full_info.get("creator"):
                            meta["creator"] = full_info["creator"].get("username", "Unknown")
                    time.sleep(0.4)

                civitai_data = {
                    "name": os.path.basename(model["file_path"]),
                    "display_name": meta.get("modelName") or model.get("display_name"),
                    "base_model": meta["baseModel"],
                    "model_type": meta["modelType"],
                    "version_id": meta["modelVersionId"],
                    "model_id": version_data.get("modelId"),
                    "version_name": meta["versionName"],
                    "triggers": self.processor.format_triggers(meta["triggers"]),
                    "description": self.processor.clean_html(meta.get("description", "")),
                    "creator_name": meta.get("creator", "Unknown"),
                    "civitai_tags": ", ".join(meta.get("tags", [])) if meta.get("tags") else "",
                }
                self.db.update_civitai_data(model["hash"], civitai_data)
                self.db.mark_civitai_synced(model["hash"])
                stats["synced"] += 1
                time.sleep(0.7)

            except Exception as e:
                if isinstance(e, CivitaiError) and e.status_code in (401, 403):
                    raise
                stats["errors"] += 1
                stats["error_detail"].append({"path": model["file_path"], "error": str(e)})

        return stats

    def check_for_updates(self, model_hash):
        """
        Compares local version with the latest available on Civitai
        for the same model and architecture.
        """
        model = self.db.get_model_by_hash(model_hash)
        if not model or not model.get("model_id"):
            return None

        latest = self.client.get_latest_version_for_arch(model["model_id"], model["base_model"])
        if not latest:
            return None

        # Compare version IDs
        if latest["id"] != model["version_id"]:
            return latest
        
        return None

    def quick_register(self, file_path, file_hash, version_data):
        """Syncs a single file immediately with the provided metadata."""
        return self.sync_model(file_path, file_hash, version_data)
