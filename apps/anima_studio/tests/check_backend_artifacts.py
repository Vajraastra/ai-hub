"""Physical ComfyUI artifact lifetime, ownership and legacy cleanup checks."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "apps"))
from anima_studio.core.backend_artifacts import ArtifactError, ArtifactStore, _workflow_hash
from anima_studio.core.comfy_client import load_workflow


def png(workflow=None):
    metadata = PngInfo()
    if workflow is not None:
        metadata.add_text("prompt", json.dumps(workflow))
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(stream, format="PNG", pnginfo=metadata)
    return stream.getvalue()


class ArtifactChecks(unittest.TestCase):
    def setUp(self):
        self.parent = REPO / ".cache/backend-artifact-tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.root = Path(self.temp.name).resolve()
        self.comfy = self.root / "comfy"
        self.library = self.root / "library"
        for folder in (self.comfy / "input", self.comfy / "output", self.library / "history"):
            folder.mkdir(parents=True)
        self.store = ArtifactStore(self.comfy, self.library)
        self.run = "a" * 32

    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(self.parent.resolve()))
        self.temp.cleanup()

    def write(self, relative, data=b"image"):
        path = self.comfy / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def record(self, identifier, *, purged=False, image=None, workflow=None):
        value = {"id": identifier, "purged_at": "2026-10-28" if purged else None}
        if image is not None:
            value["image_sha256"] = hashlib.sha256(image).hexdigest()
        if workflow is not None:
            value["workflow"] = workflow
            value["workflow_sha256"] = _workflow_hash(workflow)
        (self.library / "history" / f"{identifier}.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def test_run_manifest_survives_restart_and_reclaims_partial_unreported_outputs(self):
        self.store.start_run(self.run)
        self.store.record_artifact(self.run, {"type": "input", "subfolder": f"anima_studio/{self.run}",
                                             "filename": "master.png", "state": "planned"})
        original = self.write(f"input/anima_studio/{self.run}/master.png", b"master")
        # A disconnect can prevent receipt of the actual output descriptor.
        output = self.write(f"output/anima_studio/{self.run}/render_00001_.png", b"render")
        other = self.write("output/another_module.png", b"keep")
        second = "b" * 32
        self.store.start_run(second)
        unrelated_run = self.write(f"output/anima_studio/{second}/render.png", b"keep run")
        restarted = ArtifactStore(self.comfy, self.library)
        self.assertEqual(len(restarted.pending_runs()), 2)
        result = restarted.cleanup_run(self.run)
        self.assertFalse(result["pending"])
        self.assertEqual(result["bytes_freed"], 12)
        self.assertFalse(original.exists())
        self.assertFalse(output.exists())
        self.assertTrue(other.exists())
        self.assertTrue(unrelated_run.exists())
        self.assertEqual([item["run_id"] for item in restarted.pending_runs()], [second])

    def test_descriptors_cannot_escape_the_lease(self):
        self.store.start_run(self.run)
        for descriptor in [
            {"type": "temp", "subfolder": f"anima_studio/{self.run}", "filename": "x.png"},
            {"type": "output", "subfolder": "../output", "filename": "x.png"},
            {"type": "output", "subfolder": f"anima_studio/{self.run}", "filename": "../x.png"},
            {"type": "output", "subfolder": f"anima_studio/{self.run}", "filename": "C:x.png"},
            {"type": "output", "subfolder": f"anima_studio/{'b' * 32}", "filename": "x.png"},
        ]:
            with self.subTest(descriptor=descriptor), self.assertRaises(ArtifactError):
                self.store.record_artifact(self.run, descriptor)
        with self.assertRaises(ArtifactError):
            self.store.cleanup_run("../output")

    def test_finish_updates_planned_artifact_and_cleanup_removes_manifest(self):
        self.store.start_run(self.run)
        entry = {"type": "output", "subfolder": f"anima_studio/{self.run}", "filename": "render.png"}
        self.store.record_artifact(self.run, {**entry, "state": "planned"})
        path = self.write(f"output/{entry['subfolder']}/{entry['filename']}", png())
        self.store.finish_run(self.run, [{**entry, "state": "created", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}])
        manifest = self.store.pending_runs()[0]
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual(len(manifest["artifacts"]), 1)
        self.assertEqual(manifest["artifacts"][0]["state"], "created")
        self.assertEqual(self.store.cleanup_run(self.run)["errors"], [])
        self.assertEqual(self.store.pending_runs(), [])

    def test_missing_or_changed_created_file_cannot_be_marked_complete(self):
        for failure in ("missing", "changed"):
            with self.subTest(failure=failure):
                run_id = ("c" if failure == "missing" else "d") * 32
                self.store.start_run(run_id)
                entry = {"type": "output", "subfolder": f"anima_studio/{run_id}",
                         "filename": "render.png", "state": "created",
                         "sha256": hashlib.sha256(b"expected render").hexdigest()}
                path = self.comfy / f"output/{entry['subfolder']}/{entry['filename']}"
                if failure == "changed":
                    path.write_bytes(b"different file")
                with self.assertRaises(ArtifactError):
                    self.store.finish_run(run_id, [entry])
                manifest = next(item for item in self.store.pending_runs() if item["run_id"] == run_id)
                self.assertEqual(manifest["state"], "active")
                self.assertTrue(manifest["verification_error"])
                cleaned = self.store.cleanup_run(run_id)
                self.assertTrue(cleaned["pending"])
                self.assertTrue(cleaned["errors"])
                if failure == "changed":
                    self.assertEqual(path.read_bytes(), b"different file")

    def test_verified_partial_cleanup_does_not_require_already_removed_created_files(self):
        self.store.start_run(self.run)
        paths = [self.write(f"{area}/anima_studio/{self.run}/render.png", area.encode())
                 for area in ("input", "output")]
        entries = [{"type": area, "subfolder": f"anima_studio/{self.run}", "filename": "render.png",
                    "state": "created", "sha256": hashlib.sha256(area.encode()).hexdigest()}
                   for area in ("input", "output")]
        self.store.finish_run(self.run, entries)
        original_unlink = Path.unlink
        def deny(target, *args, **kwargs):
            if target == paths[1]:
                raise PermissionError("locked output")
            return original_unlink(target, *args, **kwargs)
        with patch.object(Path, "unlink", deny):
            failed = self.store.cleanup_run(self.run)
        self.assertTrue(failed["pending"])
        self.assertFalse(paths[0].exists())
        self.assertTrue(paths[1].exists())
        restarted = ArtifactStore(self.comfy, self.library)
        restarted.finish_run(self.run, entries)
        result = restarted.cleanup_run(self.run)
        self.assertFalse(result["pending"])
        self.assertEqual(restarted.pending_runs(), [])

    def test_reparse_namespace_is_rejected_before_deleting_any_file(self):
        self.store.start_run(self.run)
        safe = self.write(f"input/anima_studio/{self.run}/master.png")
        blocked = self.write(f"output/anima_studio/{self.run}/render.png")
        with patch("anima_studio.core.backend_artifacts._reparse", side_effect=lambda p: p == blocked.parent):
            result = self.store.cleanup_run(self.run)
        self.assertTrue(result["pending"])
        self.assertTrue(result["errors"])
        self.assertTrue(safe.exists())
        self.assertTrue(blocked.exists())

    def test_locked_file_retains_manifest_and_retry_finishes(self):
        self.store.start_run(self.run)
        path = self.write(f"output/anima_studio/{self.run}/render.png")
        original = Path.unlink
        def deny(target, *args, **kwargs):
            if target == path:
                raise PermissionError("locked")
            return original(target, *args, **kwargs)
        with patch.object(Path, "unlink", deny):
            result = self.store.cleanup_run(self.run)
        self.assertTrue(result["pending"])
        self.assertEqual(len(self.store.pending_runs()), 1)
        self.assertTrue(path.exists())
        self.assertFalse(self.store.cleanup_run(self.run)["pending"])

    def test_a_uuid_directory_without_a_manifest_cannot_be_reclaimed(self):
        path = self.write(f"output/anima_studio/{self.run}/render.png")
        with self.assertRaises(ArtifactError):
            self.store.cleanup_run(self.run)
        self.assertTrue(path.exists())

    def test_legacy_only_deletes_proven_purged_files_and_preserves_live_aliases(self):
        gone, shared = png(), b"shared cached PNG"
        first = self.write("output/anima_studio_00001_.png", gone)
        protected = self.write("output/anima_studio_00002_.png", shared)
        unknown = self.write("output/anima_studio_00003_.png", b"unproven")
        other = self.write("output/foreign_00001_.png", gone)
        self.record("gone", purged=True, image=gone)
        self.record("also-gone", purged=True, image=shared)
        self.record("live", image=shared)
        plan = self.store.legacy_plan()
        self.assertEqual([x["relative"] for x in plan["candidates"]], ["output/anima_studio_00001_.png"])
        self.assertEqual(len(plan["protected"]), 1)
        self.assertEqual(len(plan["unproven"]), 1)
        result = self.store.apply_legacy_plan(plan)
        self.assertEqual(result["errors"], [])
        self.assertFalse(first.exists())
        self.assertTrue(protected.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(other.exists())

    def test_expression_workflow_proves_render_and_input_ownership(self):
        name = f"anima-studio-{'c' * 32}.png"
        workflow = {"10": {"class_type": "LoadImage", "inputs": {"image": name}},
                    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "anima_studio_expression"}}}
        rendered = self.write("output/anima_studio_expression_00001_.png", png(workflow))
        uploaded = self.write(f"input/{name}", b"crop")
        self.record("expression", purged=True, image=b"different final composite", workflow=workflow)
        plan = self.store.legacy_plan()
        self.assertEqual(len(plan["candidates"]), 2)
        self.assertEqual(self.store.apply_legacy_plan(plan)["errors"], [])
        self.assertFalse(rendered.exists())
        self.assertFalse(uploaded.exists())

    def test_live_expression_workflow_preserves_shared_render_and_inputs(self):
        name = f"anima-studio-{'d' * 32}.png"
        workflow = {"10": {"class_type": "LoadImage", "inputs": {"image": name}}}
        self.write("output/anima_studio_expression_00001_.png", png(workflow))
        self.write(f"input/{name}")
        self.record("gone", purged=True, workflow=workflow)
        self.record("live", workflow=workflow)
        plan = self.store.legacy_plan()
        self.assertEqual(len(plan["protected"]), 2)
        self.assertEqual(plan["candidates"], [])

    def test_normalized_expression_graph_uses_exact_uploaded_names_as_proof(self):
        names = [f"anima-studio-{'e' * 32}.png", f"anima-studio-{'f' * 32}.png"]
        original = {str(index): {"class_type": "LoadImage", "inputs": {"image": name}}
                    for index, name in enumerate(names)}
        normalized = {**original, "server-default": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "anima_studio_expression", "extra_default": True}}}
        self.assertNotEqual(_workflow_hash(original), _workflow_hash(normalized))
        path = self.write("output/anima_studio_expression_00001_.png", png(normalized))
        self.record("gone-expression", purged=True, image=b"final composite", workflow=original)
        plan = self.store.legacy_plan()
        self.assertEqual([x["relative"] for x in plan["candidates"]], ["output/anima_studio_expression_00001_.png"])
        self.record("live-expression", image=b"another final composite", workflow=original)
        protected = self.store.legacy_plan()
        self.assertEqual(protected["candidates"], [])
        self.assertEqual([x["relative"] for x in protected["protected"]], ["output/anima_studio_expression_00001_.png"])
        result = self.store.apply_legacy_plan(plan)
        self.assertTrue(result["errors"])
        self.assertTrue(path.exists())

    def test_orphan_workshop_graph_and_its_uploads_can_be_reclaimed_without_history(self):
        graph = load_workflow("expression.json")
        for key, letter in (("10", "a"), ("11", "b")):
            graph[key]["inputs"]["image"] = f"anima-studio-{letter * 32}.png"
            self.write(f"input/{graph[key]['inputs']['image']}", b"old upload")
        original = self.write("output/anima_studio_expression_00001_.png", png(graph))
        plan = self.store.legacy_plan()
        self.assertEqual(len(plan["candidates"]), 3)
        self.assertTrue(all(not entry["record_ids"] for entry in plan["candidates"]))
        self.assertEqual({entry["proof"] for entry in plan["candidates"]},
                         {"workshop_graph", "workshop_graph_input"})
        self.assertEqual(self.store.apply_legacy_plan(plan)["errors"], [])
        self.assertFalse(original.exists())
        self.assertFalse(list((self.comfy / "input").glob("*.png")))

    def test_foreign_or_extra_output_graphs_do_not_prove_orphan_ownership(self):
        graph = load_workflow("txt2img.json")
        graph["99"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "another_module"}}
        foreign = self.write("output/anima_studio_00001_.png", png(graph))
        wrong_link = load_workflow("txt2img.json")
        wrong_link["9"]["inputs"]["images"] = ["foreign", 0]
        other = self.write("output/anima_studio_00002_.png", png(wrong_link))
        mismatched = self.write("output/anima_studio_expression_00003_.png", png(load_workflow("txt2img.json")))
        plan = self.store.legacy_plan()
        self.assertEqual(plan["candidates"], [])
        self.assertEqual(len(plan["unproven"]), 3)
        self.assertTrue(foreign.exists())
        self.assertTrue(other.exists())
        self.assertTrue(mismatched.exists())

    def test_orphan_expression_keeps_png_proof_if_an_upload_is_locked(self):
        graph = load_workflow("expression.json")
        paths = []
        for key, letter in (("10", "a"), ("11", "b")):
            name = f"anima-studio-{letter * 32}.png"
            graph[key]["inputs"]["image"] = name
            paths.append(self.write(f"input/{name}", b"old upload"))
        rendered = self.write("output/anima_studio_expression_00001_.png", png(graph))
        original_unlink = Path.unlink
        def deny(target, *args, **kwargs):
            if target == paths[0]:
                raise PermissionError("locked upload")
            return original_unlink(target, *args, **kwargs)
        with patch.object(Path, "unlink", deny):
            result = self.store.apply_legacy_plan(self.store.legacy_plan())
        self.assertTrue(result["pending"])
        self.assertTrue(rendered.exists())
        self.assertTrue(paths[0].exists())
        retry = self.store.apply_legacy_plan(self.store.legacy_plan())
        self.assertEqual(retry["errors"], [])
        self.assertFalse(rendered.exists())
        self.assertFalse(paths[0].exists())

    def test_legacy_apply_rechecks_ownership_content_and_timestamp(self):
        for change in ("content", "timestamp", "new-live-owner"):
            with self.subTest(change=change):
                path = self.write("output/anima_studio_00001_.png", b"gone")
                self.record("gone", purged=True, image=b"gone")
                live = self.library / "history/live.json"
                live.unlink(missing_ok=True)
                plan = self.store.legacy_plan()
                self.assertEqual(len(plan["candidates"]), 1)
                if change == "content":
                    path.write_bytes(b"replaced")
                elif change == "timestamp":
                    value = path.stat()
                    os.utime(path, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000_000))
                else:
                    self.record("live", image=b"gone")
                result = self.store.apply_legacy_plan(plan)
                self.assertTrue(result["pending"])
                self.assertTrue(result["errors"])
                self.assertTrue(path.exists())

    def test_manipulated_plan_cannot_delete_outside_proven_files(self):
        foreign = self.write("output/foreign.png")
        result = self.store.apply_legacy_plan({"candidates": [{"relative": "../foreign.png", "sha256": "a" * 64}]})
        self.assertTrue(result["errors"])
        self.assertTrue(foreign.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
