"""Finish workshop deletion only after its owned backend files are reclaimed."""
from pathlib import Path

from .backend_artifacts import ArtifactStore
from .library import Library


class StorageLifecycle:
    def __init__(self, library: Library, comfy_root: Path):
        self.library = library
        self.artifacts = ArtifactStore(comfy_root, library.root)

    def has_pending(self) -> bool:
        return (any(record.get("purged_at") for record in
                    self.library.list_generations(include_purged=True))
                or any((self.library.root / ".trash").rglob("*.json"))
                or (self.library.root / ".cleanup_pending").is_file()
                or any((self.library.root / ".deleting").glob("*"))
                or any((self.library.root / ".backend_artifacts").glob("*.json")))

    def complete_run(self, run_id: str, descriptors=()) -> dict:
        """Called after terminal success and a durable save in the library."""
        self.artifacts.finish_run(run_id, descriptors)
        return self.artifacts.cleanup_run(run_id)

    def maintain(self, *, include_orphans: bool = False) -> dict:
        """Caller holds workshop admission and has verified an idle backend."""
        result = {"removed_files": 0, "deleted_images": [], "deleted_resources": [],
                  "bytes_freed": 0, "errors": [], "preserved_shared": 0,
                  "unproven_files": 0, "cleanup_pending": False}
        recovered = self.library.recover_pending_metadata_deletions()
        result["errors"].extend(item["reason"] for item in recovered["skipped"])
        for lease in self.artifacts.pending_runs():
            cleaned = self.artifacts.cleanup_run(lease["run_id"])
            result["removed_files"] += len(cleaned["removed"])
            result["bytes_freed"] += cleaned["bytes_freed"]
            result["errors"].extend(cleaned["errors"])
        # Keep full records until legacy proof has been checked a second time.
        records = self.library.list_generations(include_purged=True)
        plan = self.artifacts.legacy_plan(records)
        if not include_orphans:
            # A different library may share ComfyUI. Only an explicit workshop
            # cleanup reclaims recognized orphan graphs without a record here.
            orphans = [entry for entry in plan["candidates"] if not entry["record_ids"]]
            plan["candidates"] = [entry for entry in plan["candidates"] if entry["record_ids"]]
            plan["protected"].extend(orphans)
        cleaned = self.artifacts.apply_legacy_plan(plan)
        result["removed_files"] += len(cleaned["removed"])
        result["bytes_freed"] += cleaned["bytes_freed"]
        result["errors"].extend(cleaned["errors"])
        result["preserved_shared"] = len(plan["protected"])
        result["unproven_files"] = len(plan["unproven"])
        if result["unproven_files"]:
            result["errors"].append(f"Se conservaron {result['unproven_files']} archivos antiguos sin procedencia comprobada.")
        if not result["errors"]:
            for record in records:
                if not record.get("purged_at"):
                    continue
                run_id = record.get("backend_run_id")
                try:
                    if run_id:
                        self.artifacts._run(run_id)
                    if run_id and any(self.artifacts._safe(self.artifacts.comfy_root,
                                f"{area}/anima_studio/{run_id}").exists() for area in ("input", "output")):
                        raise ValueError("Quedan archivos de una generación sin un registro de limpieza válido.")
                    if not (run_id or record.get("image_sha256") or record.get("workflow_sha256")):
                        raise ValueError("Falta la procedencia necesaria para finalizar una imagen antigua.")
                except (ValueError, OSError) as exc:
                    result["errors"].append(str(exc))
                    continue
                finalized = self.library.finish_generation_deletion([record["id"]])
                result["deleted_images"].extend(finalized["deleted"])
                result["bytes_freed"] += finalized["bytes_freed"]
        trash = self.library.purge_legacy_trash()
        result["deleted_resources"] = trash["deleted"]
        result["bytes_freed"] += trash["bytes_freed"]
        result["errors"].extend(item["reason"] for item in trash["skipped"])
        result["cleanup_pending"] = bool(result["errors"])
        pending = self.library._path(".cleanup_pending")
        if result["cleanup_pending"]:
            self.library._write(pending, {"errors": result["errors"]})
        else:
            pending.unlink(missing_ok=True)
        return result
