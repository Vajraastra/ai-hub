"""Regresiones de búsqueda y exclusión de actualizaciones, sin GPU ni red."""
import asyncio
import hashlib
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vault_routes as routes
from fastapi import HTTPException


WAI = {
    "hash": "wai", "name": "waiANIMA_v10Base10.safetensors",
    "display_name": "WAI-ANIMA", "model_type": "Checkpoint", "base_model": "Anima",
    "file_path": "E:/Models/checkpoints\\waiANIMA_v10Base10.safetensors",
}


class SearchChecks(unittest.IsolatedAsyncioTestCase):
    async def test_architecture_search_checks_alias_paths(self):
        record = {**WAI, "file_paths": [r"E:\Models\checkpoints\other\alias.safetensors"]}
        svc = SimpleNamespace(db=SimpleNamespace(get_all_models=lambda: [record]))
        with patch.object(routes, "_get_service", return_value=svc):
            result = await routes.get_models(q=r"E:\Models\checkpoints\other\alias.safetensors")
        self.assertEqual(result["total"], 1)

    async def test_architecture_map_includes_alias_filenames(self):
        record = {**WAI, "file_paths": [WAI["file_path"], r"E:\Models\checkpoints\alias.safetensors"]}
        svc = SimpleNamespace(db=SimpleNamespace(get_all_models=lambda: [record]))
        with patch.object(routes, "_get_service", return_value=svc):
            result = await routes.arch_map()
        self.assertEqual(result, {"waiANIMA_v10Base10": "Anima", "alias": "Anima"})

    async def test_architecture_is_independent_of_physical_folder(self):
        records = [WAI, {**WAI, "hash": "sdxl", "base_model": "SDXL"}]
        svc = SimpleNamespace(db=SimpleNamespace(get_all_models=lambda: records))
        with patch.object(routes, "_get_service", return_value=svc), \
                patch.object(routes, "_get_models_root", return_value="E:/Models"):
            result = await routes.get_models(arch="anima", cat="checkpoint")
        self.assertEqual([m["hash"] for m in result["models"]], ["wai"])
        self.assertEqual(result["models"][0]["subfolder"], "")

    async def test_windows_path_and_filename_find_display_named_model(self):
        svc = SimpleNamespace(db=SimpleNamespace(get_all_models=lambda: [WAI]))
        with patch.object(routes, "_get_service", return_value=svc):
            for query in (r"E:\Models\checkpoints\waiANIMA_v10Base10.safetensors",
                          "e:/models/checkpoints/waiANIMA_v10Base10.safetensors",
                          "waiANIMA_v10Base10.safetensors"):
                with self.subTest(query=query):
                    result = await routes.get_models(q=query)
                    self.assertEqual(result["total"], 1)

    async def test_scan_sends_partial_error_stats(self):
        svc = SimpleNamespace(
            scan_and_index=lambda *args, **kwargs: {
                "indexed": 2, "added": 1, "errors": 1, "error_detail": ["bad.safetensors"]},
            db=SimpleNamespace(get_all_models=lambda: [WAI]),
        )
        with patch.object(routes, "_get_service", return_value=svc), \
                patch.object(routes, "_get_models_root", return_value=str(Path(__file__).parent)):
            response = await routes.start_scan()
            events = await routes.scan_progress(response["session_id"])
            payload = "".join([part async for part in events.body_iterator])
        self.assertIn('"errors": 1', payload)
        self.assertIn('"type": "done"', payload)
        self.assertFalse(routes._operation_lock.locked())

    async def test_moves_rejected_while_scanning(self):
        routes._operation_lock.acquire()
        try:
            with self.assertRaises(HTTPException) as caught:
                await routes.apply_moves(routes.ApplyMovesPayload(moves=[]))
            self.assertEqual(caught.exception.status_code, 409)
        finally:
            routes._operation_lock.release()

    async def test_unknown_model_update_does_not_report_success(self):
        db = SimpleNamespace(get_model_by_hash=lambda _: None,
                             update_user_notes=Mock(), update_custom_tags=Mock())
        with patch.object(routes, "_get_service", return_value=SimpleNamespace(db=db)):
            with self.assertRaises(HTTPException) as caught:
                await routes.update_model("missing", routes.UpdateModelPayload(user_notes="note"))
        self.assertEqual(caught.exception.status_code, 404)
        db.update_user_notes.assert_not_called()

    async def test_pending_and_force_counts_share_the_service_candidates(self):
        svc = SimpleNamespace(pending_models=Mock(side_effect=lambda force=False: [WAI] * (2 if force else 1)))
        with patch.object(routes, "_get_service", return_value=svc), \
                patch.object(routes, "_start_operation", return_value="session"):
            self.assertEqual(await routes.get_civitai_pending(), {"pending": 1})
            self.assertEqual((await routes.start_civitai_sync(force=True))["pending"], 2)

    async def test_partial_moves_identify_success_and_preserve_failed_sources(self):
        temporary_root = Path(__file__).resolve().parents[2] / ".cache" / "test-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            root = Path(temporary)
            source = root / "checkpoints"
            destination = root / "diffusion_models" / "anima"
            source.mkdir()
            destination.mkdir(parents=True)
            (source / "good.safetensors").write_bytes(b"good")
            (source / "collision.safetensors").write_bytes(b"source")
            (destination / "collision.safetensors").write_bytes(b"existing")
            records = {name: {"file_path": str(source / f"{name}.safetensors")}
                       for name in ("good", "collision")}
            update = Mock()
            svc = SimpleNamespace(db=SimpleNamespace(get_model_by_hash=records.get, update_file_path=update))
            payload = routes.ApplyMovesPayload(moves=[
                routes.MoveEntry(hash=name, target_folder="diffusion_models/anima") for name in records])
            with patch.object(routes, "_get_service", return_value=svc), \
                    patch.object(routes, "_get_models_root", return_value=str(root)):
                result = await routes.apply_moves(payload)
            self.assertEqual(result["moved"], 1)
            self.assertEqual(result["moved_hashes"], ["good"])
            self.assertEqual(result["failed_hashes"], ["collision"])
            self.assertEqual((destination / "collision.safetensors").read_bytes(), b"existing")
            self.assertTrue((source / "collision.safetensors").exists())
            update.assert_called_once_with("good", str(destination / "good.safetensors"),
                                           old_path=str(source / "good.safetensors"))

    async def test_moving_existing_alias_updates_that_alias_when_stored_primary_is_missing(self):
        from model_vault.core.database import VaultDatabase
        temporary_root = Path(__file__).resolve().parents[2] / ".cache" / "test-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
            root = Path(temporary) / "Models"
            source = root / "checkpoints"
            source.mkdir(parents=True)
            primary = source / "primary.safetensors"
            alternate = source / "alternate.safetensors"
            primary.write_bytes(b"same model")
            alternate.write_bytes(primary.read_bytes())
            digest = hashlib.sha256(primary.read_bytes()).hexdigest()
            db = VaultDatabase(str(Path(temporary) / "vault.db"))
            for path in (primary, alternate):
                db.index_local_model({"hash": digest, "file_path": str(path), "name": path.name})
            primary.unlink()
            self.assertEqual(db.get_model_by_hash(digest)["file_path"], str(alternate))
            with patch.object(routes, "_get_service", return_value=SimpleNamespace(db=db)), \
                    patch.object(routes, "_get_models_root", return_value=str(root)):
                result = await routes.apply_moves(routes.ApplyMovesPayload(moves=[
                    routes.MoveEntry(hash=digest, target_folder="diffusion_models/anima")]))
            self.assertEqual(result["errors"], 0, result)
            destination = root / "diffusion_models" / "anima" / alternate.name
            record = db.get_model_by_hash(digest)
            self.assertEqual(record["file_path"], str(destination))
            self.assertIn(str(destination), record["file_paths"])
            self.assertNotIn(str(alternate), record["file_paths"])
            self.assertFalse(record["missing"])

    async def test_cancelled_http_request_keeps_lock_until_move_thread_finishes(self):
        entered = threading.Event()
        release = threading.Event()
        def move(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test move timed out")
            return "E:/Models/checkpoints/anima/wai.safetensors", True
        svc = SimpleNamespace(db=SimpleNamespace(get_model_by_hash=lambda _: WAI))
        with patch.object(routes, "_get_service", return_value=svc), \
                patch.object(routes, "_get_models_root", return_value="E:/Models"), \
                patch("model_vault.core.file_operations.move_model_files", side_effect=move):
            task = asyncio.create_task(routes.apply_moves(routes.ApplyMovesPayload(moves=[
                routes.MoveEntry(hash="wai", target_folder="checkpoints/anima")])) )
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(routes._operation_lock.locked())
                with self.assertRaises(HTTPException) as caught:
                    await routes.start_scan()
                self.assertEqual(caught.exception.status_code, 409)
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(routes._operation_lock.acquire, True, 5))
                routes._operation_lock.release()


class OperationChecks(unittest.TestCase):
    def setUp(self):
        self.sessions = {}
        self.sessions_lock = threading.Lock()

    def test_operations_exclude_each_other_and_unlock_after_completion(self):
        release = threading.Event()
        completed = threading.Event()

        def worker(q):
            release.wait(5)
            q.put({"type": "done"})
            completed.set()

        session = routes._start_operation(self.sessions, self.sessions_lock, worker)
        try:
            with self.assertRaises(HTTPException) as caught:
                routes._start_operation({}, threading.Lock(), worker)
            self.assertEqual(caught.exception.status_code, 409)
        finally:
            release.set()
            self.assertTrue(completed.wait(5))
        self.assertEqual(self.sessions[session].get(timeout=5)["type"], "done")
        # Esperar al finally del worker sin dejar un thread vivo al cerrar la prueba.
        self.assertTrue(routes._operation_lock.acquire(timeout=5))
        routes._operation_lock.release()

    def test_worker_failure_reports_error_and_releases_lock(self):
        def broken(q):
            raise ValueError("fallo simulado")

        session = routes._start_operation(self.sessions, self.sessions_lock, broken)
        self.assertEqual(self.sessions[session].get(timeout=5),
                         {"type": "error", "error": "fallo simulado"})
        self.assertTrue(routes._operation_lock.acquire(timeout=5))
        routes._operation_lock.release()

    def test_thread_start_failure_removes_session_and_releases_lock(self):
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("sin thread")):
            with self.assertRaises(RuntimeError):
                routes._start_operation(self.sessions, self.sessions_lock, lambda q: None)
        self.assertEqual(self.sessions, {})
        self.assertFalse(routes._operation_lock.locked())

    def test_progress_without_browser_is_bounded_and_retains_terminal_event(self):
        q = routes._SessionQueue()
        for index in range(1000):
            q.put({"type": "progress", "current": index})
        q.put({"type": "done"})
        self.assertEqual(q.qsize(), 2)
        self.assertEqual(q.get()["current"], 999)
        self.assertEqual(q.get()["type"], "done")

    def test_completed_sessions_expire_and_are_capped_without_removing_active(self):
        active = routes._SessionQueue()
        expired = routes._SessionQueue()
        expired.finished_at = time.monotonic() - 301
        sessions = {"active": active, "expired": expired}
        for index in range(20):
            q = routes._SessionQueue()
            q.finished_at = time.monotonic()
            sessions[str(index)] = q
        routes._prune_sessions(sessions, reserve=True)
        self.assertNotIn("expired", sessions)
        self.assertIs(sessions["active"], active)
        self.assertEqual(len(sessions), 15)


if __name__ == "__main__":
    unittest.main()
