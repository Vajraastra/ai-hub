"""Prompt repository API checks without a renderer, backend session or GPU."""
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_api as fixtures
from anima_studio.core.library import Library

routes = fixtures.routes


class PromptApiChecks(unittest.IsolatedAsyncioTestCase):
    # Reuse the ASGI transport; these endpoints deliberately need no project/catalog fixture.
    request = fixtures.ApiChecks.request

    async def asyncSetUp(self):
        self.parent = Path(__file__).resolve().parents[3] / ".cache" / "prompt-api-tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.root = Path(self.temp.name)
        self.library = Library(self.root)
        self.original = routes._library
        routes._library = self.library
        self.api = fixtures.FastAPI()
        self.api.include_router(routes.anima_studio_router)
        self.patches = [
            patch.object(routes._comfy, "_get", AsyncMock(side_effect=AssertionError("No consultar ComfyUI"))),
            patch.object(routes._comfy, "health_check", AsyncMock(side_effect=AssertionError("No iniciar sesión"))),
            patch.object(routes.pipeline, "generate", AsyncMock(side_effect=AssertionError("No generar"))),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        routes._library = self.original
        self.assertTrue(self.root.resolve().is_relative_to(self.parent.resolve()))
        self.temp.cleanup()

    @staticmethod
    def payload(**values):
        return {"name": "  Ilustración cálida  ", "style_prompt": "  painted lines\r\nsoft light  ",
                "style_negative": "grain\n", "test_prompt": "adult traveler, courtyard", **values}

    async def test_create_list_and_reload_preserve_named_texts_without_a_project(self):
        code, saved = await self.request("POST", "/prompts", self.payload())
        self.assertEqual(code, 200)
        self.assertEqual(saved["name"], "Ilustración cálida")
        self.assertEqual(saved["style_prompt"], "  painted lines\r\nsoft light  ")
        self.assertEqual(saved["style_negative"], "grain\n")
        self.assertEqual(saved["test_prompt"], "adult traveler, courtyard")
        self.assertEqual(saved["revision"], 1)
        self.assertNotIn("project_id", saved)
        routes._library = Library(self.root)  # Simulate a new Hub instance using the same data root.
        code, listed = await self.request("GET", "/prompts")
        self.assertEqual(code, 200)
        self.assertEqual(listed, [saved])

    async def test_duplicate_names_do_not_replace_saved_texts(self):
        _, saved = await self.request("POST", "/prompts", self.payload())
        code, error = await self.request("POST", "/prompts", self.payload(name="ILUSTRACIÓN CÁLIDA", style_prompt="changed"))
        self.assertEqual(code, 409)
        self.assertIn("nombre", error["detail"])
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [saved])

    async def test_explicit_update_needs_the_current_revision_and_preserves_creation_time(self):
        _, saved = await self.request("POST", "/prompts", self.payload())
        code, error = await self.request("POST", "/prompts", self.payload(id=saved["id"]))
        self.assertEqual(code, 400)
        self.assertIn("revisión", error["detail"])
        code, updated = await self.request("POST", "/prompts", self.payload(
            id=saved["id"], expected_revision=1, name="Revisado", style_prompt="new lines"))
        self.assertEqual(code, 200)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["created_at"], saved["created_at"])
        self.assertEqual(updated["style_prompt"], "new lines")
        code, _ = await self.request("POST", "/prompts", self.payload(id=saved["id"], expected_revision=1))
        self.assertEqual(code, 409)
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [updated])

    async def test_delete_checks_revision_and_never_removes_another_entry(self):
        _, one = await self.request("POST", "/prompts", self.payload())
        _, two = await self.request("POST", "/prompts", self.payload(name="Otro"))
        code, _ = await self.request("DELETE", f"/prompts/{one['id']}")
        self.assertEqual(code, 422)
        code, _ = await self.request("DELETE", f"/prompts/{one['id']}", expected_revision=2)
        self.assertEqual(code, 409)
        code, deleted = await self.request("DELETE", f"/prompts/{one['id']}", expected_revision=1)
        self.assertEqual(code, 200)
        self.assertEqual(deleted, {"id": one["id"], "deleted": True})
        code, _ = await self.request("DELETE", f"/prompts/{one['id']}", expected_revision=1)
        self.assertEqual(code, 404)
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [two])

    async def test_global_prompts_survive_project_deletion_and_are_not_in_project_exports(self):
        project = self.library.save("projects", {"name": "Disposable"})
        _, saved = await self.request("POST", "/prompts", self.payload())
        code, exported = await self.request("GET", f"/projects/{project['id']}/export")
        self.assertEqual(code, 200)
        self.assertNotIn("prompts", exported)
        self.assertNotIn(saved["id"], str(exported))
        code, _ = await self.request("DELETE", f"/projects/{project['id']}")
        self.assertEqual(code, 200)
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [saved])

    async def test_invalid_payloads_and_paths_do_not_create_records(self):
        for payload in (self.payload(name=" "), self.payload(name=12), self.payload(style_prompt=[]),
                        self.payload(style_prompt="", style_negative="", test_prompt=" "),
                        self.payload(id="../escape", expected_revision=1),
                        self.payload(expected_revision=1)):
            with self.subTest(payload=payload):
                code, _ = await self.request("POST", "/prompts", payload)
                self.assertEqual(code, 400)
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [])

    async def test_data_roots_are_independent_and_do_not_reuse_an_earlier_prompt_store(self):
        _, saved = await self.request("POST", "/prompts", self.payload())
        other = self.root / "another-data-root"
        routes._library = Library(other)
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [])
        await self.request("POST", "/prompts", self.payload(name="Other root"))
        routes._library = self.library
        _, listed = await self.request("GET", "/prompts")
        self.assertEqual(listed, [saved])

    async def test_prompt_crud_does_not_compete_for_comfyui_job_admission(self):
        # An occupied coordinator must not affect simple text library operations.
        with patch.object(fixtures.comfy_jobs, "_active", object()):
            code, saved = await self.request("POST", "/prompts", self.payload())
            self.assertEqual(code, 200)
            code, _ = await self.request("GET", "/prompts")
            self.assertEqual(code, 200)
            code, _ = await self.request("DELETE", f"/prompts/{saved['id']}", expected_revision=1)
            self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()
