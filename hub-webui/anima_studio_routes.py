"""Anima Studio API: project libraries, calibration and isolated facial edits."""
import asyncio
import base64
import copy
import io
import json
import os
import secrets
import sys
import time
import uuid
from dataclasses import asdict, fields
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

import comfy_jobs
from backend_sessions import BackendSessions

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "apps"))
from anima_studio.core.catalog import Catalog
from anima_studio.core.catalog_assets import CatalogAssets
from anima_studio.core.comfy_client import ComfyClient
from anima_studio.core.library import Library, LibraryConflict
from anima_studio.core.prompt_library import PromptLibrary, PromptLibraryError, PromptNotFound, PromptConflict
from anima_studio.core.storage import StorageLifecycle
from anima_studio.core import pipeline, composition, pose_control, pieces as piece_templates

anima_studio_router = APIRouter(prefix="/api/anima-studio", tags=["anima-studio"])
_library = Library(Path(os.environ.get("ANIMA_STUDIO_DATA", _ROOT / "apps/anima_studio/data")))
_comfy = ComfyClient()


def _backend_bridge():
    from hub_bridge import bridge
    return bridge


_sessions = BackendSessions(_comfy, _backend_bridge)
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_active_masters: set[str] = set()
_active_projects: dict[str, int] = {}
_catalog = None
_catalog_path = None
_catalog_result = None
_catalog_assets = None
_catalog_time = 0.0
_catalog_lock = asyncio.Lock()
_KINDS = ("projects", "styles", "pieces", "recipes")
_PIECE_KINDS = ("character", "personality", "outfit", "pose", "expression", "background", "item", "scene", "style")


def _storage() -> StorageLifecycle:
    config_path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
    root = _ROOT / "apps/comfyui"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        root = Path(config.get("installed_apps", {}).get("comfyui", {}).get("dir") or root)
    return StorageLifecycle(_library, root)


async def _maintain_storage(*, include_orphans=False):
    try:
        storage = _storage()
        if not storage.has_pending():
            return {"cleanup_pending": False, "removed_files": 0, "deleted_images": [], "errors": []}
    except (ValueError, OSError) as exc:
        return {"cleanup_pending": True, "errors": [str(exc)]}
    try:
        queue = await _comfy._get("/queue")
    except Exception:
        return {"cleanup_pending": True, "deferred": True,
                "errors": ["La limpieza se reintentará cuando ComfyUI permita comprobar que está libre."]}
    if (not isinstance(queue, dict) or any(not isinstance(queue.get(key), list)
                                          for key in ("queue_running", "queue_pending"))):
        return {"cleanup_pending": True, "deferred": True,
                "errors": ["ComfyUI no devolvió un estado de cola válido; no se limpiaron archivos."]}
    if queue.get("queue_running") or queue.get("queue_pending"):
        return {"cleanup_pending": True, "deferred": True,
                "errors": ["La limpieza se reintentará al terminar los trabajos de ComfyUI."]}
    try:
        return await asyncio.to_thread(storage.maintain, include_orphans=include_orphans)
    except (ValueError, OSError) as exc:
        return {"cleanup_pending": True, "errors": [str(exc)]}


@comfy_jobs.exclusive("Anima Studio")
async def _automatic_storage_cleanup():
    return await _maintain_storage()


def _models_root() -> Path:
    path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        root = Path(config["paths"]["models"])
        if not root.is_dir():
            raise ValueError("La carpeta de modelos configurada no existe.")
        return root
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(400, f"Revisa la carpeta de modelos en los ajustes del Hub: {exc}") from exc


def _read(kind: str, item_id: str, revision=None) -> dict:
    try:
        return _library.get(kind, item_id, revision=revision)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc


def _project_access(record: dict, project_id: str):
    if record.get("project_id") not in (None, "", project_id):
        raise HTTPException(400, "El recurso pertenece a otro proyecto.")


def _seed(value) -> str:
    if value is None or str(value).strip() in ("", "-1"):
        return str(secrets.randbits(64))
    try:
        number = int(str(value))
        if str(value).strip() != str(number) or not 0 <= number <= 2**64 - 1:
            raise ValueError()
        return str(number)
    except (ValueError, TypeError):
        raise HTTPException(400, "La seed debe ser un entero entre 0 y 18446744073709551615.")


@anima_studio_router.get("/catalog")
async def catalog(refresh: bool = False):
    global _catalog, _catalog_path, _catalog_result, _catalog_time, _catalog_assets
    root = _models_root()
    async with _catalog_lock:
        if _catalog is None or _catalog_path != root:
            _catalog, _catalog_path = Catalog(root), root
            _catalog_result = None
        if refresh or _catalog_result is None or time.monotonic() - _catalog_time > 60:
            config_path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
            assets = CatalogAssets(root, config_path.resolve().parent / ".cache/model_vault.db")
            def scan_display():
                return assets.enrich(_catalog.scan())
            _catalog_result = await asyncio.to_thread(scan_display)
            _catalog_assets = assets
            _catalog_time = time.monotonic()
        return copy.deepcopy(_catalog_result)


@anima_studio_router.get("/catalog/thumbnail/{identifier}")
async def catalog_thumbnail(identifier: str):
    await catalog()
    if _catalog_assets is None:
        raise HTTPException(404, "Miniatura no disponible")
    try:
        path, media_type = await asyncio.to_thread(_catalog_assets.thumbnail, identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@anima_studio_router.get("/status")
async def status():
    return {"comfyui": await _comfy.health_check(), "backend": "Anima / ComfyUI",
            "reference_adapter": False, "pose_control": True, "pose_editor": "skeleton2d",
            "expressions": "inpaint facial + composición exacta",
            "limitations": ["Editor de maniquí 3D pendiente. El control de esqueleto es experimental.",
                            "Txt2img produce RGB; pedir alfa en un perfil no elimina el fondo."]}


@anima_studio_router.post("/session/open")
async def session_open(payload: dict = Body(...)):
    return await _sessions.open(payload.get("session_id"))


@anima_studio_router.post("/session/heartbeat")
async def session_heartbeat(payload: dict = Body(...)):
    return await _sessions.heartbeat(payload.get("session_id"))


@anima_studio_router.post("/session/close")
async def session_close(payload: dict = Body(...)):
    return await _sessions.close(payload.get("session_id"))


@anima_studio_router.websocket("/session/socket/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str):
    await _sessions.socket(websocket, session_id)


@anima_studio_router.get("/sampling-options")
async def sampling_options():
    try:
        info = await _comfy._get("/object_info/KSampler")
        inputs = info["KSampler"]["input"]["required"]
        return {"samplers": inputs["sampler_name"][0], "schedulers": inputs["scheduler"][0]}
    except Exception:
        return {"samplers": ["er_sde", "euler", "euler_ancestral"], "schedulers": ["simple", "normal"]}


def _prompts() -> PromptLibrary:
    # Resolve the current data root for isolated sessions/tests. Store instances
    # share a lock per root, independently of ComfyUI admission and project data.
    return PromptLibrary(_library.root)


def _prompt_call(method: str, *args):
    try:
        return getattr(_prompts(), method)(*args)
    except PromptConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PromptNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PromptLibraryError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "No se pudo acceder a la biblioteca de prompts. Revisa la carpeta de datos.") from exc


@anima_studio_router.get("/prompts")
def list_prompts():
    return _prompt_call("list")


@anima_studio_router.post("/prompts")
def save_prompt(payload: dict = Body(...)):
    return _prompt_call("save", payload)


@anima_studio_router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str, expected_revision: int = Query(..., ge=1)):
    return _prompt_call("delete", prompt_id, expected_revision)


def _save(kind: str, payload: dict):
    if kind == "pieces" and payload.get("kind") not in _PIECE_KINDS:
        raise HTTPException(400, "Tipo de pieza desconocido.")
    if kind == "styles":
        config = copy.deepcopy(payload.get("config", {}))
        if "seed" in config:
            config["seed"] = _seed(config["seed"])
        payload = {**payload, "config": config}
    if kind == "recipes":
        payload = copy.deepcopy(payload)
        payload.setdefault("piece_revisions", {
            item_id: _read("pieces", item_id)["revision"] for item_id in payload.get("piece_ids", [])})
        if payload.get("style_id") and not payload.get("style_revision"):
            payload["style_revision"] = _read("styles", payload["style_id"])["revision"]
        if payload.get("format_id") and not payload.get("format_snapshot"):
            project = _read("projects", payload["project_id"])
            formats = project.get("formats", [])
            snapshot = next((item for item in formats if item["id"] == payload["format_id"]), None)
            if snapshot is None:
                raise HTTPException(400, "El formato no pertenece al proyecto.")
            payload["format_snapshot"] = copy.deepcopy(snapshot)
    try:
        return _library.save(kind, payload)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _register(kind):
    async def list_items(project_id: str | None = None):
        return _library.list(kind, project_id=project_id)
    async def save_item(payload: dict = Body(...)):
        return _save(kind, payload)
    async def delete_item(item_id: str):
        try:
            if kind == "projects" and _active_projects.get(item_id):
                raise LibraryConflict("Este proyecto tiene una generación en curso. Espera a que termine.")
            return _library.delete(kind, item_id)
        except LibraryConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
    anima_studio_router.add_api_route(f"/{kind}", list_items, methods=["GET"], name=f"list_{kind}")
    anima_studio_router.add_api_route(f"/{kind}", save_item, methods=["POST"], name=f"save_{kind}")
    anima_studio_router.add_api_route(f"/{kind}/{{item_id}}", delete_item,
                                      methods=["DELETE"], name=f"delete_{kind}")


for _kind in _KINDS:
    _register(_kind)


@anima_studio_router.get("/bootstrap")
async def bootstrap():
    await asyncio.to_thread(_library.recover_pending_metadata_deletions)
    has_project_history = (any(_library._path("revisions", "projects").glob("*"))
                           or any(_library._path(".trash", "projects").glob("*")))
    if not _library.initialized and (_library.list("projects") or has_project_history):
        _library.mark_initialized()
    if not _library.list("projects") and not _library.initialized:
        for path in sorted((_ROOT / "apps/anima_studio/templates").glob("*.json")):
            _save("projects", json.loads(path.read_text(encoding="utf-8")))
    try:
        models, catalog_error = await catalog(), ""
    except HTTPException as exc:
        models, catalog_error = {"models": [], "loras": [], "text_encoders": [], "vaes": []}, exc.detail
    try:
        cleanup = await _automatic_storage_cleanup()
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        cleanup = {"cleanup_pending": True, "deferred": True, "errors": []}
    return {**{kind: _library.list(kind) for kind in _KINDS},
            "generations": _library.list_generations(), "catalog": models,
            "piece_test_templates": copy.deepcopy(composition.TEST_TEMPLATES),
            "catalog_error": catalog_error, "status": await status(), "storage_cleanup": cleanup}


@anima_studio_router.get("/projects/{project_id}/export")
async def export_project(project_id: str):
    project = _read("projects", project_id)
    resources = {kind: _library.list(kind, project_id=project_id) for kind in _KINDS[1:]}
    revisions = {}
    for kind, entries in resources.items():
        revisions[kind] = {}
        for entry in entries:
            history = []
            for revision in range(1, entry["revision"] + 1):
                try:
                    history.append(_library.get(kind, entry["id"], revision))
                except ValueError:
                    continue  # an interrupted prior save may leave a revision gap
            # Current views hide deleted sample previews; retain that same view
            # in the exported latest revision without rewriting the local store.
            if history and history[-1].get("revision") == entry["revision"]:
                history[-1] = copy.deepcopy(entry)
            revisions[kind][entry["id"]] = history
    return JSONResponse({"schema": "anima-studio-profile-v2", "project": project,
                         **resources, "revisions": revisions},
                        headers={"Content-Disposition": f'attachment; filename="anima-project-{project_id}.json"'})


@anima_studio_router.post("/projects/import")
async def import_project(payload: dict = Body(...)):
    import tempfile

    # Validate the entire bundle in an isolated store before publishing any
    # project/resource. Old style/piece revisions retain their meaning after ID
    # remapping; a v1 bundle missing a pinned revision is rejected explicitly.
    try:
        bundle = payload if "project" in payload else {"project": payload}
        if bundle.get("schema") not in (None, "anima-studio-profile-v1", "anima-studio-profile-v2"):
            raise ValueError("Versión de perfil no compatible.")
        if not isinstance(bundle.get("project"), dict):
            raise ValueError("El perfil debe contener un proyecto.")
        origin_project = bundle["project"].get("id")
        histories = bundle.get("revisions", {})
        if not isinstance(histories, dict):
            raise ValueError("Las revisiones del perfil deben ser un objeto.")
        resources, mapping, current_revisions = {}, {}, {}
        total_revisions = 0
        for kind in ("pieces", "styles", "recipes"):
            entries = bundle.get(kind, [])
            if not isinstance(entries, list) or len(entries) > 1000:
                raise ValueError("Cada biblioteca importada admite hasta 1000 recursos.")
            resources[kind], mapping[kind], current_revisions[kind] = [], {}, {}
            kind_history = histories.get(kind, {})
            if not isinstance(kind_history, dict):
                raise ValueError("Formato de revisiones no válido.")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                    raise ValueError("Cada recurso importado necesita un identificador.")
                entry_id = entry["id"]
                if entry_id in mapping[kind]:
                    raise ValueError("Hay identificadores duplicados en el perfil.")
                snapshots = kind_history.get(entry_id, [entry])
                if not isinstance(snapshots, list) or not snapshots:
                    raise ValueError("Faltan las revisiones de un recurso.")
                versions = []
                for snapshot in snapshots:
                    if not isinstance(snapshot, dict) or snapshot.get("id") != entry_id:
                        raise ValueError("Una revisión no coincide con su recurso.")
                    revision = snapshot.get("revision", 1)
                    if type(revision) is not int or revision < 1:
                        raise ValueError("Número de revisión no válido.")
                    if snapshot.get("project_id") not in (None, "", origin_project):
                        raise ValueError("El perfil contiene un recurso de otro proyecto.")
                    versions.append((revision, snapshot))
                versions.sort(key=lambda item: item[0])
                if len({revision for revision, _ in versions}) != len(versions):
                    raise ValueError("Hay revisiones duplicadas.")
                current_revision = entry.get("revision", 1)
                if type(current_revision) is not int or current_revision != versions[-1][0]:
                    raise ValueError("La revisión actual no coincide con el historial exportado.")
                if versions[-1][1] != entry:
                    raise ValueError("El contenido actual no coincide con su última revisión.")
                total_revisions += len(versions)
                if total_revisions > 5000:
                    raise ValueError("El perfil excede 5000 revisiones.")
                resources[kind].append((entry_id, versions))
                mapping[kind][entry_id] = uuid.uuid4().hex
                current_revisions[kind][entry_id] = current_revision
            if set(kind_history) - set(mapping[kind]):
                raise ValueError("Hay revisiones sin un recurso asociado.")

        stage_parent = _library._path(".imports")
        stage_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="profile-", dir=stage_parent) as temporary:
            stage_root = Path(temporary).resolve()
            if not stage_root.is_relative_to(_library.root.resolve()):
                raise ValueError("Ruta temporal no válida.")
            stage = Library(stage_root)
            project_data = {key: value for key, value in bundle["project"].items()
                            if key not in {"id", "revision", "created_at", "updated_at"}}
            project = stage.save("projects", project_data)
            revision_map = {kind: {} for kind in resources}

            def reference(kind, old_id, old_revision=None):
                if old_id not in mapping[kind]:
                    raise ValueError(f"Falta un recurso requerido de {kind}.")
                revision = current_revisions[kind][old_id] if old_revision is None else old_revision
                if type(revision) is not int or (old_id, revision) not in revision_map[kind]:
                    raise ValueError("Falta una revisión fijada por una receta; vuelve a exportar el perfil completo.")
                return mapping[kind][old_id], revision_map[kind][(old_id, revision)]

            # Dependencies are imported first. All historical revisions are
            # reconstructed in order, including ones no current recipe uses.
            for kind in ("styles", "pieces", "recipes"):
                entries = resources[kind]
                if kind == "pieces":
                    # A variant may sort before its master in an exported list.
                    indexed = dict(entries)
                    ordered, complete, visiting = [], set(), set()

                    def visit(piece_id):
                        if piece_id in complete:
                            return
                        if piece_id in visiting:
                            raise ValueError("El perfil contiene un ciclo de maestros y variantes.")
                        if piece_id not in indexed:
                            raise ValueError("Falta el maestro de una variante del perfil.")
                        visiting.add(piece_id)
                        for _, version in indexed[piece_id]:
                            if version.get("master_id"):
                                visit(version["master_id"])
                        visiting.remove(piece_id)
                        complete.add(piece_id)
                        ordered.append((piece_id, indexed[piece_id]))

                    for piece_id in indexed:
                        visit(piece_id)
                    entries = ordered
                for old_id, versions in entries:
                    for old_revision, snapshot in versions:
                        data = copy.deepcopy(snapshot)
                        for key in ("id", "revision", "created_at", "updated_at"):
                            data.pop(key, None)
                        data.update(id=mapping[kind][old_id], project_id=project["id"],
                                    imported_from={"id": old_id, "revision": old_revision})
                        if data.get("sample_ids"):
                            data["source_sample_ids"] = data.pop("sample_ids")
                        if kind == "pieces":
                            if data.get("sample_asset_id"):
                                data["source_sample_asset_id"] = data.pop("sample_asset_id")
                            if data.get("master_id"):
                                data["master_id"], data["master_revision"] = reference(
                                    "pieces", data["master_id"], data.get("master_revision"))
                            saved_style = data.get("generation_snapshot", {}).get("snapshot", {}).get("style")
                            if isinstance(saved_style, dict):
                                old_style_id, old_style_revision = saved_style.get("id"), saved_style.get("revision")
                                if (old_style_id, old_style_revision) in revision_map["styles"]:
                                    saved_style["id"], saved_style["revision"] = reference(
                                        "styles", old_style_id, old_style_revision)
                                    saved_style["imported_from"] = {"id": old_style_id, "revision": old_style_revision}
                                saved_style["project_id"] = project["id"]
                        if kind == "recipes":
                            if data.get("style_id"):
                                data["style_id"], data["style_revision"] = reference(
                                    "styles", data["style_id"], data.get("style_revision"))
                            piece_ids = data.get("piece_ids", [])
                            if not isinstance(piece_ids, (list, dict)):
                                raise ValueError("Las piezas de una receta deben ser una lista u objeto.")
                            old_pins = data.get("piece_revisions", {})
                            if not isinstance(old_pins, dict):
                                raise ValueError("Las revisiones de piezas deben ser un objeto.")
                            pins = {}

                            def remap_piece(old_piece_id):
                                new_id, revision = reference("pieces", old_piece_id, old_pins.get(old_piece_id))
                                pins[new_id] = revision
                                return new_id

                            data["piece_ids"] = ({key: remap_piece(value) for key, value in piece_ids.items()}
                                                 if isinstance(piece_ids, dict)
                                                 else [remap_piece(value) for value in piece_ids])
                            data["piece_revisions"] = pins
                        saved = stage.save(kind, data, imported=True)
                        revision_map[kind][(old_id, old_revision)] = saved["revision"]

            # Publish all validated files under one library lock. Newly allocated
            # UUIDs mean nothing existing is replaced; roll back on disk failure.
            moved = []
            with _library._lock:
                try:
                    for source in sorted(stage_root.rglob("*.json")):
                        destination = _library._path(source.relative_to(stage_root).as_posix())
                        if destination.exists():
                            raise ValueError("El identificador importado ya existe.")
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(source, destination)
                        moved.append((source, destination))
                except BaseException:
                    for source, destination in reversed(moved):
                        source.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(destination, source)
                    raise
            return project
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(400, f"No se importó el perfil: {exc}") from exc


def _expression_source(payload):
    """Older clients selecting a composed master get the same replay contract."""
    if payload.get("mode") != "expression" or payload.get("source_asset_id") or not payload.get("master_asset_id"):
        return payload
    try:
        record = _library.get_generation(payload["master_asset_id"])
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not record.get("snapshot") or not record.get("config", {}).get("model"):
        return payload  # Imported PNGs and pre-composition records use explicit settings.
    result = copy.deepcopy(payload)
    result["source_asset_id"] = record["id"]
    if not any(key in result for key in ("expression_piece_id", "expression_prompt", "expression_negative")):
        pins = result.get("piece_revisions", {})
        for piece_id in result.get("piece_ids", []):
            piece = _read("pieces", piece_id, pins.get(piece_id))
            if piece.get("kind") == "expression":
                result.update(expression_piece_id=piece_id, expression_piece_revision=piece["revision"])
                break
        else:
            result.update(expression_prompt=result.get("prompt", ""), expression_negative=result.get("negative", ""))
    return result


def _draft_piece(piece_test, project_id):
    """Validate a test snapshot without creating or revising an inventory piece."""
    incoming = piece_test["draft"]
    allowed = {"kind", "name", "prompt", "negative", "tags", "seed", "loras", "master_id",
               "master_revision", "sample_asset_id", "pose_control", "definition", "project_id"}
    if not isinstance(incoming, dict) or set(incoming) - allowed:
        raise ValueError("El borrador contiene campos no válidos; sus ajustes se recuperan desde una prueba real.")
    if incoming.get("kind", piece_test["kind"]) != piece_test["kind"]:
        raise ValueError("El borrador y la prueba deben ser del mismo tipo.")
    if incoming.get("project_id") not in (None, "", project_id):
        raise ValueError("El borrador pertenece a otro proyecto.")
    value = copy.deepcopy(incoming)
    name = value.get("name", "")
    if not isinstance(name, str):
        raise ValueError("El nombre del borrador debe ser texto.")
    labels = {"character": "personaje", "outfit": "outfit", "pose": "pose", "expression": "expresión",
              "background": "fondo", "item": "objeto", "personality": "personalidad", "scene": "escenario"}
    value.update(id=f"draft-{uuid.uuid4().hex}", kind=piece_test["kind"], project_id=project_id,
                 name=name.strip() or f"Prueba de {labels[piece_test['kind']]}")
    # The same materializer validates LoRAs, pose, exact seeds and pinned master /
    # sample references used by normal saves. Client-supplied replay snapshots
    # never enter this path; only the library can resolve them.
    value = piece_templates.materialize_piece(_library, value, value, {})
    _library._validate("pieces", value)
    value.update(revision=0, draft=True)
    return value


@anima_studio_router.post("/compose")
async def compose(payload: dict = Body(...)):
    payload = _expression_source(payload)
    if not isinstance(payload.get("config", {}), dict):
        raise HTTPException(400, "La configuración debe ser un objeto.")
    if any(not isinstance(payload.get(key, ""), str) for key in
           ("prompt", "negative", "expression_prompt", "expression_negative")):
        raise HTTPException(400, "Los bloques de prompt y negativo deben ser texto.")
    if "negative_override" in payload and not isinstance(payload["negative_override"], str):
        raise HTTPException(400, "El negativo de la prueba debe ser texto.")
    project_id = payload.get("project_id", "")
    project = _read("projects", project_id)
    piece_test = payload.get("piece_test")
    if "integration" in payload and not isinstance(payload["integration"], bool):
        raise HTTPException(400, "El modo del integrador debe ser verdadero o falso.")
    integration = payload.get("integration", False)
    if "integration_framing" in payload:
        framing = payload["integration_framing"]
        if not integration or not isinstance(framing, str) or framing not in composition.INTEGRATION_FRAMING:
            raise HTTPException(400, "Elige un encuadre del integrador: cuerpo entero, medio cuerpo o retrato.")
    if integration and piece_test is not None:
        raise HTTPException(400, "Elige una prueba individual o una composición del integrador.")
    if integration and not payload.get("source_asset_id"):
        if "negative_override" in payload:
            raise HTTPException(400, "El integrador conserva los negativos del estilo y las piezas. Usa el negativo adicional de la toma.")
        if not isinstance(payload.get("style_id"), str) or not payload["style_id"]:
            raise HTTPException(400, "Elige el estilo con el que quieres integrar tus piezas.")
        if type(payload.get("style_revision")) is not int or payload["style_revision"] < 1:
            raise HTTPException(400, "Selecciona una revisión concreta del estilo del integrador.")
        if set(payload.get("config", {})) - {"seed", "width", "height", "pose_control"}:
            raise HTTPException(400, "El modelo, los LoRAs de acabado y el sampling pertenecen al estilo. Cámbialos en Estilos.")
    source_piece = None
    if piece_test is not None:
        if not isinstance(piece_test, dict) or piece_test.get("kind") not in composition.TEST_TEMPLATES:
            raise HTTPException(400, "Elige el tipo de pieza que quieres probar.")
        if any(key in piece_test and not isinstance(piece_test[key], str)
               for key in ("context_prompt", "context_negative")):
            raise HTTPException(400, "El contexto de la prueba y su negativo deben ser texto.")
        if "reuse_master_settings" in piece_test and not isinstance(piece_test["reuse_master_settings"], bool):
            raise HTTPException(400, "La recuperación de ajustes debe ser verdadera o falsa.")
        if "specialized" in piece_test and not isinstance(piece_test["specialized"], bool):
            raise HTTPException(400, "El modo del editor debe ser verdadero o falso.")
        if "source_piece_id" in piece_test or "source_piece_revision" in piece_test:
            source_id, source_revision = piece_test.get("source_piece_id"), piece_test.get("source_piece_revision")
            if not isinstance(source_id, str) or not source_id or type(source_revision) is not int or source_revision < 1:
                raise HTTPException(400, "Selecciona una pieza de origen y su revisión concreta.")
            source_piece = _read("pieces", source_id, source_revision)
            _project_access(source_piece, project_id)
            if source_piece.get("kind") != piece_test["kind"]:
                raise HTTPException(400, "La pieza de origen y el borrador deben ser del mismo tipo.")
        if piece_test.get("specialized"):
            if not isinstance(payload.get("style_id"), str) or not payload["style_id"]:
                raise HTTPException(400, "Elige un estilo calibrado para probar este diseño.")
            if type(payload.get("style_revision")) is not int or payload["style_revision"] < 1:
                raise HTTPException(400, "Selecciona una revisión concreta del estilo calibrado.")
            if set(payload.get("config", {})) - {"seed", "width", "height", "pose_control"}:
                raise HTTPException(400, "El modelo, los LoRAs de acabado y el sampling pertenecen al estilo. Cámbialos en Estilos.")
        token = piece_test.get("token")
        if token is not None and (not isinstance(token, str) or not token or len(token) > 120
                                  or any(ord(char) < 32 for char in token)):
            raise HTTPException(400, "El identificador de la sesión de pruebas no es válido.")
        if "draft" in piece_test and payload.get("source_asset_id"):
            raise HTTPException(400, "Prueba el borrador separado de una composición fijada.")
    if payload.get("source_asset_id"):
        try:
            source = _library.get_generation(payload["source_asset_id"])
            _library.image_path(source["id"])
            _project_access(source, project_id)
            if integration:
                saved_style = source.get("snapshot", {}).get("style") or {}
                if (payload.get("style_id") not in (None, "", saved_style.get("id"))
                        or payload.get("style_revision") not in (None, saved_style.get("revision"))):
                    raise ValueError("Libera la composición recuperada antes de elegir otro estilo.")
            expression = None
            if payload.get("expression_piece_id"):
                expression = _read("pieces", payload["expression_piece_id"], payload.get("expression_piece_revision"))
                _project_access(expression, project_id)
                if expression.get("kind") != "expression":
                    raise ValueError("Selecciona una pieza de expresión.")
            result = composition.frozen(source, expression=expression,
                expression_prompt=payload.get("expression_prompt"),
                expression_negative=payload.get("expression_negative"),
                negative_override=payload.get("negative_override"))
            # Local edit controls do not alter the master's model, seed or prompt.
            if payload.get("mode") == "expression":
                controls = payload.get("config", {})
                for name in ("denoise", "crop_padding", "feather"):
                    if name in controls:
                        result["config"][name] = controls[name]
            return result
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
    style = None
    revisions = payload.get("piece_revisions", {})
    ids = payload.get("piece_ids", [])
    if not isinstance(revisions, dict) or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(400, "Las piezas y sus revisiones no tienen un formato válido.")
    if len(ids) > 24:
        raise HTTPException(400, "Selecciona hasta 24 piezas por receta.")
    draft = None
    if piece_test is not None and "draft" in piece_test:
        if ids or revisions or piece_test.get("id"):
            raise HTTPException(400, "Una prueba usa un borrador o una pieza guardada, no ambos.")
        try:
            draft = _draft_piece(piece_test, project_id)
            if piece_test.get("specialized") and "seed" in payload.get("config", {}):
                # A real sample provides trusted style provenance, while this
                # new trial may deliberately choose a different variation.
                draft["seed"] = piece_templates.seed_text(payload["config"]["seed"])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
    elif piece_test is not None:
        if piece_test.get("id"):
            ids = [piece_test["id"]]
    pieces = [draft] if draft is not None else [_read("pieces", value, revisions.get(value)) for value in ids]
    for piece in pieces:
        _project_access(piece, project_id)
    if piece_test is not None:
        pieces = [p for p in pieces if p.get("kind") == piece_test["kind"]]
        if not pieces:
            raise HTTPException(400, "Guarda y selecciona una pieza de ese tipo antes de probarla.")
    if len({p["id"] for p in pieces}) != len(pieces) or len({p["kind"] for p in pieces}) != len(pieces):
        raise HTTPException(400, "Selecciona una sola pieza de cada tipo para la composición.")
    inherited_config = {}
    if piece_test is not None and piece_test.get("specialized"):
        for piece in pieces + ([source_piece] if source_piece is not None else []):
            saved_style = piece.get("generation_snapshot", {}).get("snapshot", {}).get("style")
            if (isinstance(saved_style, dict) and saved_style.get("id") == payload.get("style_id")
                    and saved_style.get("revision") == payload.get("style_revision")):
                _project_access(saved_style, project_id)
                style = copy.deepcopy(saved_style)
                break
    if piece_test is not None and not piece_test.get("specialized") and piece_test.get("reuse_master_settings", True):
        replay = pieces[0].get("generation_snapshot", {})
        saved_style = replay.get("snapshot", {}).get("style")
        if isinstance(saved_style, dict) and saved_style.get("id") == payload.get("style_id"):
            _project_access(saved_style, project_id)
            style = copy.deepcopy(saved_style)
            # Effective LoRAs include the old character. Reassemble those from
            # the saved style and the selected variant instead of applying twice.
            inherited_config = {key: copy.deepcopy(value) for key, value in replay.get("config", {}).items()
                if key in {"model", "text_encoder", "vae", "width", "height", "steps", "cfg", "sampler", "scheduler", "seed"}}
    if style is None and payload.get("style_id"):
        style = _read("styles", payload["style_id"], payload.get("style_revision"))
        _project_access(style, project_id)
    config = copy.deepcopy(style.get("config", {}) if style else {})
    config.update(inherited_config)
    if not integration and "seed" not in payload.get("config", {}):
        character = next((p for p in pieces if p.get("kind") == "character" and p.get("seed")), None)
        tested = pieces[0] if piece_test and pieces[0].get("seed") else character
        if tested:
            config["seed"] = tested["seed"]
    config.update(copy.deepcopy(payload.get("config", {})))
    if payload.get("format_id"):
        fmt = payload.get("format_snapshot")
        if fmt is not None and (not isinstance(fmt, dict) or fmt.get("id") != payload["format_id"]):
            raise HTTPException(400, "La copia guardada del formato no coincide con su identificador.")
        if fmt is None:
            fmt = next((item for item in project.get("formats", []) if item["id"] == payload["format_id"]), None)
        if fmt is None:
            raise HTTPException(400, "El formato no pertenece al proyecto.")
        config.update(width=fmt["width"], height=fmt["height"])
    try:
        user_prompt = str(payload.get("prompt", "")).strip()
        prompt = "\n".join(filter(None, (user_prompt, composition.INTEGRATION_FRAMING.get(payload.get("integration_framing"), ""))))
        result = composition.assemble(project, style, pieces, config, prompt,
                                      str(payload.get("negative", "")).strip(), piece_test=piece_test,
                                      negative_override=payload.get("negative_override"))
        if integration and "integration_framing" in payload:
            result["composition"].update(integration_framing=payload["integration_framing"],
                                         integration_user_prompt=payload.get("prompt", ""))
        if payload.get("format_id"):
            result["composition"].update(format_id=fmt["id"], format_snapshot=copy.deepcopy(fmt))
        return result
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _image(value: str) -> bytes:
    try:
        encoded = value.split(",", 1)[-1]
        if len(encoded) > 32 * 1024 * 1024:
            raise ValueError("La imagen supera 24 MiB.")
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 16_777_216:
                raise ValueError("La imagen supera 16 megapíxeles.")
            image.verify()
        return raw
    except Exception as exc:
        raise HTTPException(400, f"Imagen o máscara no válida: {exc}") from exc


@anima_studio_router.post("/generate")
@comfy_jobs.exclusive("Anima Studio")
@_sessions.job
async def generate(payload: dict = Body(...)):
    release = _pin_project(payload.get("project_id", ""))
    try:
        return await _generate(payload)
    finally:
        release()


def _pin_project(project_id: str):
    project_id = project_id if isinstance(project_id, str) else ""
    _active_projects[project_id] = _active_projects.get(project_id, 0) + 1
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            remaining = _active_projects[project_id] - 1
            if remaining:
                _active_projects[project_id] = remaining
            else:
                _active_projects.pop(project_id, None)
    return release


async def _generate(payload: dict):
    payload = _expression_source(copy.deepcopy(payload))
    if payload.get("source_asset_id") and payload.get("mode") == "expression":
        if payload.get("master_asset_id") not in (None, "", payload["source_asset_id"]):
            raise HTTPException(400, "La máscara y la composición deben pertenecer al mismo maestro.")
        payload["master_asset_id"] = payload["source_asset_id"]
    assembled = await compose(payload)
    if not assembled["prompt"].strip():
        raise HTTPException(400, "Escribe un prompt o selecciona piezas.")
    if payload.get("mode") == "expression" and not str(payload.get("prompt", "")).strip():
        if not any(item.get("kind") == "expression" and item.get("prompt", "").strip()
                   for item in assembled["blocks"]):
            raise HTTPException(400, "Selecciona una expresión guardada o descríbela en las instrucciones.")
    config = assembled["config"]
    config["seed"] = _seed(config.get("seed", "42"))
    available = await catalog()
    for key, name in (("text_encoder", "text_encoders"), ("vae", "vaes")):
        if not config.get(key) and available[name]:
            config[key] = available[name][0]["file"]
    try:
        supported = {item.name for item in fields(pipeline.GenParams)}
        params = pipeline.GenParams(**{key: val for key, val in {
            **config, "prompt": assembled["prompt"], "negative": assembled["negative"],
            "mode": payload.get("mode", "txt2img")}.items() if key in supported})
        params = pipeline.validate_params(params)
        selected_files = await asyncio.to_thread(_catalog.validate, params)
        if params.pose_control and params.mode == "txt2img":
            selected_files["pose_control"] = await asyncio.to_thread(
                pose_control.validate_model_patch, _models_root(), params.pose_control)
        config = asdict(params)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if params.mode not in ("txt2img", "expression"):
        raise HTTPException(400, "Modo de generación desconocido.")
    categories = assembled["snapshot"]["project"].get("categories", ["characters"])
    category_ids = [item.get("id") if isinstance(item, dict) else item for item in categories]
    category = payload.get("category") or category_ids[0]
    if category == "calibration":
        category = category_ids[0]
    if category not in category_ids:
        if payload.get("piece_test") is not None:
            # Tests are typed by piece_test, independently of the production
            # categories a project happens to organize its history under.
            category = category_ids[0]
        else:
            raise HTTPException(400, "La categoría no pertenece al proyecto.")
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI aún no está disponible. Espera al inicio o reintenta desde el taller.")
    master = mask = None
    master_record = None
    if params.mode == "expression":
        if payload.get("master_asset_id"):
            try:
                master_record = _library.get_generation(payload["master_asset_id"])
                _project_access(master_record, payload["project_id"])
                master = _library.image_path(master_record["id"]).read_bytes()
                params.master_seed = str(master_record.get("seed", master_record.get("config", {}).get("seed", "")))
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
        elif payload.get("master_base64"):
            master = _image(payload["master_base64"])
        if master is None or not payload.get("mask_base64"):
            raise HTTPException(400, "Selecciona un maestro y pinta una máscara facial.")
        mask = _image(payload["mask_base64"])
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"job_id": job_id, "status": "running", "phase": "Preparando", "progress": 0}
    # Completed jobs are small records; retain only the most recent 100.
    for key in list(_jobs)[:-100]:
        if _jobs[key]["status"] != "running":
            del _jobs[key]

    def progress(phase, detail=None):
        _jobs[job_id].update(phase=phase)
        if detail:
            _jobs[job_id]["progress"] = {"value": detail.get("step", 0),
                                         "max": detail.get("total", 1)}

    async def worker():
        run_id = uuid.uuid4().hex
        try:
            storage = _storage()
            storage.artifacts.start_run(run_id)
            result = await pipeline.generate(_comfy, params, progress, master_bytes=master, mask_bytes=mask,
                artifact_run_id=run_id,
                on_artifact=lambda item: storage.artifacts.record_artifact(run_id, item))
            project = assembled["snapshot"]["project"]
            pieces = assembled["snapshot"]["pieces"]
            names = {piece["kind"]: piece["name"] for piece in pieces}
            metadata = {**result["metadata"], "project_id": project["id"],
                        "project_name": project["name"], "snapshot": assembled["snapshot"],
                        "composition": assembled["composition"],
                        "selected_files": selected_files,
                        "config": {**config, **result["metadata"].get("params", {}),
                                   "seed": str(params.seed)}, "seed": str(params.seed),
                        "prompt": assembled["prompt"], "negative": assembled["negative"],
                        "category": category,
                        "character_name": names.get("character", "") if payload.get("source_asset_id")
                                          else payload.get("character") or names.get("character", ""),
                        "outfit_name": names.get("outfit", "") if payload.get("source_asset_id")
                                       else payload.get("outfit") or names.get("outfit", ""),
                        "expression_name": payload.get("expression") or names.get("expression", ""),
                        "master_id": payload.get("master_asset_id"),
                        "master_asset_id": payload.get("master_asset_id"),
                        "purpose": payload.get("purpose", "production")}
            if isinstance(payload.get("calibration"), dict):
                metadata["calibration"] = copy.deepcopy(payload["calibration"])
            if isinstance(payload.get("piece_test"), dict):
                metadata["piece_test"] = copy.deepcopy(payload["piece_test"])
            extras = {key: result[key] for key in ("patch", "mask", "generation_mask") if result.get(key)}
            if master is not None:
                extras["master"] = master
            record = await asyncio.to_thread(_library.save_generation, metadata, result["image"], extras)
            try:
                cleaned = await asyncio.to_thread(storage.complete_run, run_id,
                                                  result["metadata"].get("backend_artifacts", []))
                if cleaned["errors"]:
                    _jobs[job_id]["warning"] = "El resultado está guardado; queda una limpieza pendiente que se reintentará al actualizar."
            except (ValueError, OSError):
                _jobs[job_id]["warning"] = "El resultado está guardado; queda una limpieza pendiente que se reintentará al actualizar."
            _jobs[job_id].update(status="done", progress=100, phase="Terminado", result=record)
        except asyncio.CancelledError:
            _jobs[job_id].update(status="error", error="Tarea cancelada; comprueba el estado de ComfyUI.")
            raise
        except Exception as exc:
            _jobs[job_id].update(status="error", phase="Error", error=str(exc))

    if master_record:
        _active_masters.add(master_record["id"])
    release_project = _pin_project(payload["project_id"])
    try:
        task = comfy_jobs.spawn(worker())
    except BaseException:
        release_project()
        if master_record:
            _active_masters.discard(master_record["id"])
        raise
    _tasks.add(task)
    _sessions.track_worker(task)
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda _: release_project())
    if master_record:
        task.add_done_callback(lambda _: _active_masters.discard(master_record["id"]))
    return {"job_id": job_id}


@anima_studio_router.get("/jobs/{job_id}")
async def job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Trabajo no encontrado.")
    return _jobs[job_id]


@anima_studio_router.post("/interrupt")
@comfy_jobs.exclusive("Anima Studio", control=True)
async def interrupt():
    await _comfy.interrupt()
    return {"ok": True}


@anima_studio_router.get("/generations")
async def generations(project_id: str | None = None, status: str | None = None):
    return _library.list_generations(project_id=project_id, status=status)


@anima_studio_router.get("/generations/{asset_id}")
async def generation(asset_id: str):
    try:
        return _library.get_generation(asset_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


@anima_studio_router.get("/images/{asset_id}")
async def image(asset_id: str, variant: str = "image", download: bool = False):
    try:
        path = _library.image_path(asset_id, variant=variant)
        return FileResponse(path, media_type="image/png", filename=path.name if download else None)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


@anima_studio_router.post("/generations/{asset_id}/review")
async def review(asset_id: str, payload: dict = Body(...)):
    try:
        return _library.review(asset_id, payload.get("status", ""), payload.get("reason", ""))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@anima_studio_router.delete("/generations/{asset_id}")
@comfy_jobs.exclusive("Anima Studio")
async def delete_generation(asset_id: str):
    try:
        if asset_id in _active_masters:
            raise LibraryConflict("Esta imagen se está utilizando como maestro en una generación en curso.")
        result = _library.delete_generation(asset_id)
        cleanup = await _maintain_storage()
        return {**result, "cleanup_pending": cleanup.get("cleanup_pending", False),
                "cleanup_errors": cleanup.get("errors", [])}
    except LibraryConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@anima_studio_router.post("/rejected/purge")
@comfy_jobs.exclusive("Anima Studio")
async def purge(payload: dict = Body(...)):
    try:
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or len(ids) > 1000:
            raise ValueError("Selecciona hasta 1000 rechazados.")
        pinned = list(dict.fromkeys(item for item in ids if item in _active_masters))
        result = _library.purge_rejected([item for item in ids if item not in _active_masters],
                                        int(payload.get("min_age_days", 0)))
        result["protected"] = list(dict.fromkeys([*result["protected"], *pinned]))
        cleanup = await _maintain_storage()
        return {**result, "cleanup_pending": cleanup.get("cleanup_pending", False),
                "cleanup_errors": cleanup.get("errors", [])}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@anima_studio_router.post("/storage/cleanup")
@comfy_jobs.exclusive("Anima Studio")
async def cleanup_storage(payload: dict = Body(default={})):
    return await _maintain_storage(include_orphans=payload.get("include_orphans") is True)
