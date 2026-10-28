"""Owned ComfyUI files: durable per-run leases and conservative legacy cleanup.

Callers must wait for a terminal backend result, or establish that its queue is
idle, before reclaiming a lease. This module never interrupts a backend job.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from PIL import Image

_RUN = re.compile(r"[0-9a-f]{32}\Z")
_OUTPUT = re.compile(r"anima_studio(?:_expression)?_\d+_\.png\Z")
_INPUT = re.compile(r"anima-studio-[0-9a-f]{32}\.png\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class ArtifactError(ValueError):
    pass


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _workflow_hash(value) -> str:
    if not isinstance(value, dict):
        return ""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _reparse(path: Path) -> bool:
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))


class ArtifactStore:
    def __init__(self, comfy_root: Path, library_root: Path):
        self.comfy_root = Path(comfy_root).resolve()
        self.library_root = Path(library_root).resolve()
        self.manifests = self.library_root / ".backend_artifacts"
        self._lock = threading.RLock()

    @staticmethod
    def _run(run_id: str) -> str:
        if not isinstance(run_id, str) or not _RUN.fullmatch(run_id):
            raise ArtifactError("Identificador de archivos temporales no válido.")
        return run_id

    @staticmethod
    def _safe(root: Path, relative: str) -> Path:
        if not isinstance(relative, str) or "\\" in relative:
            raise ArtifactError("Ruta de archivo temporal no válida.")
        parts = relative.split("/")
        if any(part in ("", ".", "..") or ":" in part for part in parts):
            raise ArtifactError("La ruta de archivos temporales sale de su carpeta.")
        path = root.joinpath(*parts)
        if not path.resolve().is_relative_to(root) or path.resolve() == root:
            raise ArtifactError("La ruta de archivos temporales sale de su carpeta.")
        current = root
        for part in parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if _reparse(current):
                    raise ArtifactError("No se limpian enlaces ni junctions de archivos temporales.")
        return path

    def _manifest_path(self, run_id: str) -> Path:
        return self._safe(self.library_root, f".backend_artifacts/{self._run(run_id)}.json")

    def _write(self, run_id: str, value: dict):
        destination = self._manifest_path(run_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{run_id}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            self._manifest_path(run_id)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _read(self, run_id: str) -> dict:
        try:
            value = json.loads(self._manifest_path(run_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ArtifactError("No se pudo leer el registro de archivos temporales.") from exc
        if not isinstance(value, dict) or value.get("run_id") != run_id:
            raise ArtifactError("El registro de archivos temporales está dañado.")
        return value

    def start_run(self, run_id: str) -> dict:
        run_id = self._run(run_id)
        with self._lock:
            if self._manifest_path(run_id).exists():
                raise ArtifactError("Ya existe ese registro de archivos temporales.")
            # The lease is durable before creating folders or making an upload.
            value = {"schema_version": 1, "run_id": run_id, "state": "active", "artifacts": []}
            self._write(run_id, value)
            for area in ("input", "output"):
                self._safe(self.comfy_root, f"{area}/anima_studio/{run_id}").mkdir(parents=True, exist_ok=True)
            return value

    def _descriptor(self, run_id: str, descriptor: dict) -> dict:
        if not isinstance(descriptor, dict) or descriptor.get("type") not in ("input", "output"):
            raise ArtifactError("Tipo de archivo temporal no válido.")
        expected = f"anima_studio/{self._run(run_id)}"
        subfolder = descriptor.get("subfolder", "")
        filename = descriptor.get("filename")
        if subfolder != expected or not isinstance(filename, str) or "/" in filename or "\\" in filename:
            raise ArtifactError("El archivo temporal no pertenece a esta generación.")
        self._safe(self.comfy_root, f"{descriptor['type']}/{subfolder}/{filename}")
        if descriptor.get("sha256") and not _SHA.fullmatch(str(descriptor["sha256"])):
            raise ArtifactError("Hash de archivo temporal no válido.")
        return {key: descriptor[key] for key in ("type", "subfolder", "filename", "sha256", "state") if key in descriptor}

    def record_artifact(self, run_id: str, descriptor: dict):
        with self._lock:
            value = self._read(run_id)
            entry = self._descriptor(run_id, descriptor)
            identity = lambda item: (item["type"], item["subfolder"], item["filename"])
            value["artifacts"] = [item for item in value.get("artifacts", []) if identity(item) != identity(entry)]
            value["artifacts"].append(entry)
            self._write(run_id, value)

    def _verify_created(self, value: dict, *, require_hash: bool, allow_missing: bool):
        for raw in value.get("artifacts", []):
            entry = self._descriptor(value["run_id"], raw)
            if entry.get("state") != "created":
                continue
            relative = f"{entry['type']}/{entry['subfolder']}/{entry['filename']}"
            path = self._safe(self.comfy_root, relative)
            if not path.exists():
                if allow_missing:
                    continue
                raise ArtifactError(
                    f"No se encontró {relative} en la carpeta local configurada; se conserva su registro de limpieza.")
            if not path.is_file():
                raise ArtifactError(f"{relative} no es un archivo normal.")
            expected = entry.get("sha256")
            if require_hash and not expected:
                raise ArtifactError(f"Falta el hash comprobable de {relative}; se conserva su registro de limpieza.")
            if expected and _sha(path) != expected:
                raise ArtifactError(f"El contenido de {relative} cambió; se conservó el archivo y su registro.")

    def finish_run(self, run_id: str, descriptors=()):
        with self._lock:
            value = self._read(run_id)
            if value.get("state") in ("complete", "cleanup_ready"):
                # A partial cleanup can already have removed some verified
                # files. Repeating completion must not demand them again.
                self._verify_created(value, require_hash=False, allow_missing=True)
                return
            for entry in descriptors:
                self.record_artifact(run_id, entry)
            value = self._read(run_id)
            try:
                self._verify_created(value, require_hash=True, allow_missing=False)
            except (OSError, ArtifactError) as exc:
                value["verification_error"] = str(exc)
                self._write(run_id, value)
                raise
            value["state"] = "complete"
            value.pop("verification_error", None)
            self._write(run_id, value)

    def pending_runs(self) -> list[dict]:
        with self._lock:
            folder = self._safe(self.library_root, ".backend_artifacts")
            if not folder.exists():
                return []
            return [self._read(path.stem) for path in sorted(folder.glob("*.json")) if _RUN.fullmatch(path.stem)]

    def _tree(self, relative: str) -> tuple[list[Path], list[Path]]:
        root = self._safe(self.comfy_root, relative)
        files, directories = [], []
        if not root.exists():
            return files, directories
        if not root.is_dir():
            raise ArtifactError("La carpeta de archivos temporales no es un directorio.")
        def walk(folder):
            for item in folder.iterdir():
                item = self._safe(self.comfy_root, item.relative_to(self.comfy_root).as_posix())
                if item.is_dir():
                    walk(item)
                elif item.is_file():
                    files.append(item)
                else:
                    raise ArtifactError("Tipo de archivo temporal no permitido.")
            directories.append(folder)
        walk(root)
        return files, directories

    def cleanup_run(self, run_id: str) -> dict:
        """Reclaim a terminal run; caller establishes backend quiescence."""
        with self._lock:
            value = self._read(run_id)  # Never reclaim a UUID folder without its lease.
            result = {"removed": [], "bytes_freed": 0, "errors": [], "pending": True}
            try:
                verified = value.get("state") in ("complete", "cleanup_ready")
                self._verify_created(value, require_hash=False, allow_missing=verified)
                if not verified:
                    # Persist verification before first unlink: a partial
                    # cleanup must remain retryable after a process restart.
                    value["state"] = "cleanup_ready"
                    value.pop("verification_error", None)
                    self._write(run_id, value)
                files, directories = [], []
                for area in ("input", "output"):
                    found, folders = self._tree(f"{area}/anima_studio/{run_id}")
                    files.extend(found)
                    directories.extend(folders)
                # Validate the entire namespace before deleting its first file.
                for path in files:
                    self._safe(self.comfy_root, path.relative_to(self.comfy_root).as_posix())
                for path in files:
                    relative = path.relative_to(self.comfy_root).as_posix()
                    path = self._safe(self.comfy_root, relative)
                    size = path.stat().st_size
                    path.unlink()
                    result["removed"].append(relative)
                    result["bytes_freed"] += size
                for path in directories:
                    self._safe(self.comfy_root, path.relative_to(self.comfy_root).as_posix()).rmdir()
                self._manifest_path(run_id).unlink()
                result["pending"] = False
            except (OSError, ArtifactError) as exc:
                result["errors"].append(str(exc))
            return result

    def _records(self) -> list[dict]:
        root = self._safe(self.library_root, "history")
        if not root.exists():
            return []
        result = []
        for path in root.glob("*.json"):
            path = self._safe(self.library_root, path.relative_to(self.library_root).as_posix())
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                result.append(value)
        return result

    @staticmethod
    def _legacy_inputs(record: dict) -> set[str]:
        result = set()
        workflow = record.get("workflow", {})
        if isinstance(workflow, dict):
            for node in workflow.values():
                if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                    value = node.get("inputs", {}).get("image")
                    if isinstance(value, str) and _INPUT.fullmatch(value):
                        result.add(value)
        return result

    @staticmethod
    def _workshop_graph(graph) -> bool:
        """Recognize our complete legacy output graph, never only a filename."""
        if not isinstance(graph, dict):
            return False
        try:
            nodes = {key: value for key, value in graph.items() if key != "_comment"}
            expression = nodes["9"]["inputs"]["filename_prefix"] == "anima_studio_expression"
            prefix = "anima_studio_expression" if expression else "anima_studio"
            expected = {"1": {"UNETLoader", "CheckpointLoaderSimple"}, "2": {"CLIPLoader"},
                        "3": {"VAELoader"}, "4": {"CLIPTextEncode"}, "5": {"CLIPTextEncode"},
                        "6": {"SetLatentNoiseMask" if expression else "EmptySD3LatentImage"},
                        "7": {"KSampler"}, "8": {"VAEDecode"}, "9": {"SaveImage"}}
            if expression:
                expected.update({"10": {"LoadImage"}, "11": {"LoadImage"},
                                 "12": {"ImageToMask"}, "13": {"VAEEncode"}})
            extra = set(nodes) - set(expected)
            loras = [str(100 + index) for index in range(len(extra))]
            if extra != set(loras) or not set(expected).issubset(nodes):
                return False
            for key, classes in expected.items():
                if nodes[key]["class_type"] not in classes or not isinstance(nodes[key]["inputs"], dict):
                    return False
            model = ["1", 0]
            for key in loras:
                if (nodes[key]["class_type"] != "LoraLoaderModelOnly"
                        or nodes[key]["inputs"].get("model") != model):
                    return False
                model = [key, 0]
            links = [("4", "clip", ["2", 0]), ("5", "clip", ["2", 0]),
                     ("7", "model", model), ("7", "positive", ["4", 0]),
                     ("7", "negative", ["5", 0]), ("7", "latent_image", ["6", 0]),
                     ("8", "samples", ["7", 0]), ("8", "vae", ["3", 0]),
                     ("9", "images", ["8", 0])]
            if expression:
                links.extend([("12", "image", ["11", 0]), ("13", "pixels", ["10", 0]),
                              ("13", "vae", ["3", 0]), ("6", "samples", ["13", 0]),
                              ("6", "mask", ["12", 0])])
                if (nodes["12"]["inputs"].get("channel") != "red"
                        or not all(_INPUT.fullmatch(str(nodes[key]["inputs"].get("image", "")))
                                   for key in ("10", "11"))):
                    return False
            return (nodes["9"]["inputs"].get("filename_prefix") == prefix
                    and nodes["2"]["inputs"].get("type") == "cosmos"
                    and all(nodes[key]["inputs"].get(field) == value for key, field, value in links))
        except (KeyError, TypeError, AttributeError):
            return False

    def legacy_plan(self, records=None) -> dict:
        """Dry run: only exact, proven legacy files, never prefix-only deletion."""
        with self._lock:
            records = self._records() if records is None else list(records)
            live = [record for record in records if not record.get("purged_at")]
            gone = [record for record in records if record.get("purged_at")]
            digest = lambda r: r.get("image_sha256", "")
            workflow = lambda r: r.get("workflow_sha256") or _workflow_hash(r.get("workflow"))
            live_hashes = {digest(r) for r in live if digest(r)}
            live_workflows = {workflow(r) for r in live if workflow(r)}
            live_inputs = set().union(*(self._legacy_inputs(r) for r in live)) if live else set()
            # Older ComfyUI versions can add defaults or normalize graph nodes
            # before storing their PNG prompt. The unique uploaded crop+mask
            # names still identify an expression run without using its prompt.
            live_input_sets = {frozenset(self._legacy_inputs(r)) for r in live
                               if self._legacy_inputs(r)}
            gone_input_sets = {r.get("id"): self._legacy_inputs(r) for r in gone}
            input_owners = {}
            orphan_inputs = set()
            for record in gone:
                for name in self._legacy_inputs(record):
                    input_owners.setdefault(name, []).append(record.get("id"))
            result = {"candidates": [], "protected": [], "unproven": [],
                      "purged_ids": [r.get("id") for r in gone], "bytes_reclaimable": 0}
            for area, pattern in (("output", _OUTPUT), ("input", _INPUT)):
                folder = self._safe(self.comfy_root, area)
                if not folder.exists():
                    continue
                for path in sorted(folder.iterdir()):
                    if not pattern.fullmatch(path.name):
                        continue
                    relative = f"{area}/{path.name}"
                    path = self._safe(self.comfy_root, relative)
                    if not path.is_file():
                        raise ArtifactError("Un archivo legado no es un archivo normal.")
                    image_hash = _sha(path)
                    workflow_hash = ""
                    png_inputs = set()
                    owned_graph = False
                    if area == "output":
                        try:
                            with Image.open(path) as source:
                                prompt = source.info.get("prompt")
                            graph = json.loads(prompt) if prompt else None
                            workflow_hash = _workflow_hash(graph)
                            png_inputs = self._legacy_inputs({"workflow": graph})
                            expected_prefix = ("anima_studio_expression" if path.name.startswith("anima_studio_expression_")
                                               else "anima_studio")
                            owned_graph = (self._workshop_graph(graph)
                                           and graph["9"]["inputs"]["filename_prefix"] == expected_prefix)
                        except (OSError, ValueError, TypeError):
                            pass
                    owners = ([r.get("id") for r in gone if image_hash == digest(r)
                               or (workflow_hash and workflow_hash == workflow(r))
                               or (png_inputs and png_inputs == gone_input_sets[r.get("id")])]
                              if area == "output" else input_owners.get(path.name, []))
                    entry = {"relative": relative, "sha256": image_hash,
                             "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
                             "record_ids": owners}
                    protected = (image_hash in live_hashes or (workflow_hash and workflow_hash in live_workflows)
                                 or (png_inputs and frozenset(png_inputs) in live_input_sets)
                                 if area == "output" else path.name in live_inputs)
                    if protected:
                        result["protected"].append(entry)
                    elif owners:
                        result["candidates"].append(entry)
                        result["bytes_reclaimable"] += entry["bytes"]
                    elif (area == "output" and owned_graph) or (area == "input" and path.name in orphan_inputs):
                        entry["proof"] = "workshop_graph" if area == "output" else "workshop_graph_input"
                        if area == "output":
                            entry["input_paths"] = [f"input/{name}" for name in sorted(png_inputs)]
                        result["candidates"].append(entry)
                        result["bytes_reclaimable"] += entry["bytes"]
                        if area == "output":
                            orphan_inputs.update(png_inputs)
                    else:
                        result["unproven"].append(entry)
            return result

    def apply_legacy_plan(self, plan: dict) -> dict:
        """Apply an exact dry run after checking current ownership and bytes again."""
        with self._lock:
            current = {entry["relative"]: entry for entry in self.legacy_plan()["candidates"]}
            result = {"removed": [], "bytes_freed": 0, "errors": [], "pending": False}
            failed = set()
            # An orphan expression's PNG is the only durable proof of its
            # uploads. Keep that proof until all of its removable inputs are
            # gone, so a locked input remains reclaimable on the next retry.
            entries = sorted(plan.get("candidates", []), key=lambda item: not str(item.get("relative", "")).startswith("input/"))
            for entry in entries:
                relative = entry.get("relative")
                expected = current.get(relative)
                if expected is None or any(entry.get(key) != expected[key] for key in ("sha256", "bytes", "mtime_ns")):
                    result["errors"].append(f"Cambió la propiedad o el contenido de {relative}; se conservó.")
                    failed.add(relative)
                    continue
                if expected.get("proof") == "workshop_graph" and failed.intersection(expected.get("input_paths", [])):
                    result["errors"].append(f"Se conserva {relative} hasta poder limpiar sus archivos de entrada.")
                    failed.add(relative)
                    continue
                try:
                    path = self._safe(self.comfy_root, relative)
                    if _sha(path) != entry["sha256"]:
                        raise ArtifactError(f"Cambió el contenido de {relative}; se conservó.")
                    path.unlink()
                    result["removed"].append(relative)
                    result["bytes_freed"] += entry["bytes"]
                except (OSError, ArtifactError) as exc:
                    result["errors"].append(str(exc))
                    failed.add(relative)
            result["pending"] = bool(result["errors"])
            return result
