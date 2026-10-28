"""Painter/Anima shared tag API tests; HTTP downloads are entirely simulated."""
import asyncio
import json
import importlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub-webui"))
import tag_routes as tag_api
from fastapi import FastAPI, HTTPException

tags = tag_api._te


async def request(app, path, query=b"", method="GET"):
    """Exercise ASGI routing directly, with no sockets or application lifespan."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "scheme": "http", "method": method, "path": path, "raw_path": path.encode(),
               "query_string": query, "root_path": "", "headers": [],
               "client": ("127.0.0.1", 1234), "server": ("fixture.invalid", 80)}, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, json.loads(body)


class FakeContent:
    def __init__(self, chunks, entered=None, release=None):
        self.chunks, self.entered, self.release = chunks, entered, release

    async def iter_chunked(self, _):
        for chunk in self.chunks:
            if self.entered is not None:
                self.entered.set()
                await self.release.wait()
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class FakeResponse:
    def __init__(self, chunks, status=200, total=None, **kwargs):
        self.status = status
        self.headers = {} if total is None else {"Content-Length": str(total)}
        self.content = FakeContent(chunks, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def get(self, url):
        if url != "https://fixture.invalid/tags.csv":
            raise AssertionError("Unexpected test URL")
        return self.response


class TagRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_root = ROOT / ".cache" / "tag-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=temporary_root)))
        self.stack.enter_context(patch.object(tags, "_CSV_DIR", self.directory))
        tags._engines.clear()
        tags._csv_locks.clear()
        tag_api._tag_operations.clear()
        self.path = tags.csv_path_for("fixture")
        self.path.write_text("old_tag,0,1,\n", encoding="utf-8")
        profile = {"profiles": [{"id": "fixture", "tag_csv": "fixture",
                                  "tag_csv_url": "https://fixture.invalid/tags.csv"}]}
        self.stack.enter_context(patch.object(tag_api, "_load_profiles_data", return_value=profile))

    def network(self, response):
        return self.stack.enter_context(patch("aiohttp.ClientSession", return_value=FakeSession(response)))

    async def consume(self):
        response = await tag_api.tags_download("fixture")
        return [json.loads(chunk.removeprefix("data: ")) async for chunk in response.body_iterator]

    def previous(self):
        tags.load_csv("fixture")
        return self.path.read_bytes(), tags._engines["fixture"]

    def unchanged(self, previous):
        self.assertEqual(self.path.read_bytes(), previous[0])
        self.assertIs(tags._engines["fixture"], previous[1])
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_import_router_does_not_preload_or_import_painter_backend(self):
        source = "\n".join([
            "import sys, threading",
            "from unittest.mock import patch",
            f"sys.path.insert(0, {str(ROOT / 'hub-webui')!r})",
            "with patch.object(threading.Thread, 'start', side_effect=AssertionError('unexpected background thread')):",
            "    import tag_routes",
            "assert 'painter_routes' not in sys.modules",
            "assert 'setup' not in sys.modules",
            "assert tag_routes._te.loaded_names() == []",
        ])
        result = subprocess.run([sys.executable, "-B", "-c", source], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_painter_mounts_shared_tags_once_without_duplicate_prefix(self):
        backend = importlib.import_module("painter_routes")
        app = FastAPI()
        app.include_router(backend.painter_router)
        paths = list(app.openapi()["paths"])
        self.assertEqual([path for path in paths if path.endswith("/tags/status")], ["/api/painter/tags/status"])
        self.assertEqual([path for path in paths if path.endswith("/tags/search")], ["/api/painter/tags/search"])
        self.assertIs(sys.modules["tag_engine"], tags)
        status, body = await request(app, "/api/painter/tags/status", b"csv=fixture")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"loaded": True, "count": 1, "csv_present": True})
        status, body = await request(app, "/api/painter/tags/search", b"csv=fixture&q=old")
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["name"], "old_tag")

    async def test_standalone_exposes_tags_without_starting_backend_or_touching_real_data(self):
        with patch.dict(os.environ, {"ANIMA_STUDIO_DATA": str(self.directory / "library")}):
            standalone = importlib.import_module("anima_studio_poc")
        status, body = await request(standalone.api, "/api/painter/tags/status", b"csv=fixture")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"loaded": True, "count": 1, "csv_present": True})
        status, body = await request(standalone.api, "/api/painter/tags/search", b"csv=fixture&q=old")
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["name"], "old_tag")
        status, body = await request(standalone.api, "/api/painter/tags/profiles")
        self.assertEqual(status, 200)
        self.assertEqual(body["profiles"][0]["id"], "fixture")

    async def test_status_lazy_loads_only_requested_csv_off_event_loop(self):
        (self.directory / "unused.csv").write_text("unused_tag,0,1,\n", encoding="utf-8")
        main_thread = threading.get_ident()
        used_threads = []
        prepare = tags.prepare_csv

        def observed(*args):
            used_threads.append(threading.get_ident())
            return prepare(*args)

        with patch.object(tags, "prepare_csv", side_effect=observed) as build:
            statuses = await asyncio.gather(*(tag_api.tags_status("fixture") for _ in range(8)))
        self.assertTrue(all(status == {"loaded": True, "count": 1, "csv_present": True} for status in statuses))
        self.assertEqual(build.call_count, 1)
        self.assertTrue(all(thread != main_thread for thread in used_threads))
        self.assertEqual(tags.loaded_names(), ["fixture"])

    async def test_missing_status_and_empty_search_do_not_build_an_index(self):
        self.assertEqual(await tag_api.tags_status("missing"), {"loaded": False, "count": 0, "csv_present": False})
        self.assertEqual(await tag_api.tags_search("", "fixture"), {"results": []})
        self.assertEqual(tags.loaded_names(), [])

    async def test_route_limits_are_bounded_and_search_runs_on_worker(self):
        self.path.write_text("".join(f"blue_{i},0,10,\n" for i in range(30)), encoding="utf-8")
        main_thread = threading.get_ident()
        used_threads = []
        search = tags.search

        def observed(*args):
            used_threads.append(threading.get_ident())
            return search(*args)

        with patch.object(tags, "search", side_effect=observed):
            self.assertEqual(len((await tag_api.tags_search("blue", "fixture", -1))["results"]), 1)
            self.assertEqual(len((await tag_api.tags_search("blue", "fixture", 999))["results"]), 20)
        self.assertTrue(all(thread != main_thread for thread in used_threads))

    async def test_all_path_accepting_routes_reject_unsafe_csv_names(self):
        for call in (tag_api.tags_status("../outside"), tag_api.tags_search("old", "..\\outside"),
                     tag_api.tags_reload("C:\\outside")):
            with self.assertRaises(HTTPException) as caught:
                await call
            self.assertEqual(caught.exception.status_code, 400)
        with patch.object(tag_api, "_load_profiles_data", return_value={"profiles": [{"id": "bad", "tag_csv": "../outside", "tag_csv_url": "https://fixture.invalid/tags.csv"}]}):
            with self.assertRaises(HTTPException) as caught:
                await tag_api.tags_download("bad")
            self.assertEqual(caught.exception.status_code, 400)

    async def test_reload_preserves_contract_and_loads_new_data(self):
        self.previous()
        self.path.write_text("new_tag,4,5,\n", encoding="utf-8")
        self.assertEqual(await tag_api.tags_reload("fixture"), {"ok": True, "count": 1})
        self.assertEqual((await tag_api.tags_search("new", "fixture"))["results"][0]["name"], "new_tag")

    async def test_corrupt_csv_has_readable_error_and_keeps_previous_index(self):
        previous = self.previous()
        self.path.write_bytes(b"\xffinvalid utf-8")
        with self.assertRaises(HTTPException) as caught:
            await tag_api.tags_reload("fixture")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("No se pudo leer la biblioteca de tags", caught.exception.detail)
        self.assertIs(tags._engines["fixture"], previous[1])
        tags._engines.clear()
        for call in (tag_api.tags_status("fixture"), tag_api.tags_search("new", "fixture")):
            with self.assertRaises(HTTPException) as caught:
                await call
            self.assertEqual(caught.exception.status_code, 503)
            self.assertIn("No se pudo leer la biblioteca de tags", caught.exception.detail)
        self.assertEqual(tags.loaded_names(), [])

    async def test_download_validates_then_replaces_file_and_index(self):
        previous = self.previous()
        content = b"new_tag,4,12,\n"
        self.network(FakeResponse([content[:4], content[4:]], total=len(content)))
        events = await self.consume()
        self.assertEqual([event["type"] for event in events], ["start", "progress", "progress", "loading", "done"])
        self.assertEqual(events[-1], {"type": "done", "count": 1, "csv": "fixture"})
        self.assertEqual(self.path.read_bytes(), content)
        self.assertIsNot(tags._engines["fixture"], previous[1])
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    async def test_failed_http_invalid_csv_truncation_and_network_cut_preserve_old_library(self):
        for response in (FakeResponse([], status=503), FakeResponse([b"<html>error</html>"]),
                         FakeResponse([b"new_tag,4,12,\n"], total=999),
                         FakeResponse([b"new_tag,4,12,\n", OSError("connection cut")])):
            with self.subTest(response=response):
                previous = self.previous()
                self.network(response)
                events = await self.consume()
                self.assertEqual(events[-1]["type"], "error")
                self.assertNotIn("done", [event["type"] for event in events])
                self.unchanged(previous)

    async def test_closing_stream_mid_download_keeps_old_file_and_index(self):
        previous = self.previous()
        self.network(FakeResponse([b"new_tag,4,12,\n", b"second_tag,0,1,\n"]))
        response = await tag_api.tags_download("fixture")
        iterator = response.body_iterator
        await anext(iterator)  # start
        await anext(iterator)  # first chunk
        await iterator.aclose()
        self.unchanged(previous)

    async def test_cancel_during_indexing_waits_for_reader_and_keeps_old_library(self):
        previous = self.previous()
        self.network(FakeResponse([b"new_tag,4,12,\n"]))
        entered, release = threading.Event(), threading.Event()
        prepare = tags.prepare_csv

        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test reader was not released")
            return prepare(*args)

        with patch.object(tags, "prepare_csv", side_effect=blocked):
            downloading = asyncio.create_task(self.consume())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                downloading.cancel()
                await asyncio.sleep(0)
                self.assertFalse(downloading.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await downloading
        self.unchanged(previous)

    async def test_reload_waits_for_download_and_reloads_the_committed_csv(self):
        self.previous()
        entered, release = asyncio.Event(), asyncio.Event()
        self.network(FakeResponse([b"new_tag,4,12,\n"], entered=entered, release=release))
        downloading = asyncio.create_task(self.consume())
        await asyncio.wait_for(entered.wait(), 5)
        reloading = asyncio.create_task(tag_api.tags_reload("fixture"))
        try:
            await asyncio.sleep(0)
            self.assertFalse(reloading.done())
        finally:
            release.set()
        self.assertEqual((await downloading)[-1]["type"], "done")
        self.assertEqual(await reloading, {"ok": True, "count": 1})
        self.assertEqual(tags.search("fixture", "new")[0]["name"], "new_tag")


if __name__ == "__main__":
    unittest.main()
