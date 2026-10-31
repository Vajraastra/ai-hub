"""Pieces keep authored content while the selected integrator style owns rendering."""
import asyncio
import copy
import unittest
from unittest.mock import patch

import check_api as fixtures

routes = fixtures.routes


class PieceIntegrationChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiChecks.asyncSetUp
    asyncTearDown = fixtures.ApiChecks.asyncTearDown
    request = fixtures.ApiChecks.request
    payload = fixtures.ApiChecks.payload
    finish = fixtures.ApiChecks.finish
    render = staticmethod(fixtures.ApiChecks.render)

    def styles(self):
        self.catalog.DATA = copy.deepcopy(self.catalog.DATA)
        self.catalog.DATA["models"].append({"file": "anima/alternate.safetensors", "name": "Alternative"})
        self.catalog.DATA["loras"].extend([
            {"file": "anima/finish-b.safetensors", "name": "Finish B"},
            *({"file": f"pieces/{kind}.safetensors", "name": kind}
              for kind in routes.composition.TEST_TEMPLATES),
            {"file": "pieces/new-expression.safetensors", "name": "New expression"},
        ])
        base = self.payload()["config"]
        a = self.store.save("styles", {"project_id": self.one["id"], "name": "Trial A",
            "prompt": "finish A quality", "negative": "finish A flaws", "config": {**base,
                "steps": 7, "cfg": 1, "loras": [{"file": "anima/paint.safetensors", "strength": .3}]}})
        b = self.store.save("styles", {"project_id": self.one["id"], "name": "Output B",
            "prompt": "finish B quality", "negative": "finish B flaws", "config": {**base,
                "model": "anima/alternate.safetensors", "steps": 19, "cfg": 0, "sampler": "euler",
                "scheduler": "karras", "seed": "13",
                "loras": [{"file": "anima/finish-b.safetensors", "strength": 0}]}})
        return a, b

    def integration(self, style, pieces=(), **values):
        return {"project_id": self.one["id"], "integration": True,
            "style_id": style["id"], "style_revision": style["revision"],
            "piece_ids": [piece["id"] for piece in pieces], "prompt": "sprite take",
            "config": {"width": 128, "height": 256}, **values}

    async def save_tested_piece(self, style, kind):
        lora = {"file": f"pieces\\{kind}.safetensors", "strength": 0 if kind == "expression" else .6,
                "trigger": f"trigger-{kind}", "enabled": True}
        draft = {"kind": kind, "prompt": f"own {kind}", "negative": f"avoid {kind}", "loras": [lora]}
        status, result = await self.request("POST", "/compose", {**self.integration(style),
            "integration": False, "purpose": "piece_test", "piece_test": {
                "kind": kind, "draft": draft, "specialized": True, "reuse_master_settings": False}})
        self.assertEqual(status, 200, result)
        sample = self.store.save_generation({**result, "project_id": self.one["id"],
            "category": "characters", "seed": result["config"]["seed"]}, fixtures.png())
        piece = self.store.save("pieces", {**draft, "project_id": self.one["id"], "name": kind,
            "sample_asset_id": sample["id"], "definition": {"trial_negative": {
                "style_id": style["id"], "style_revision": style["revision"], "text": result["negative"]}}})
        return piece

    async def test_all_eight_pieces_tested_in_a_are_reusable_in_b_without_a_finish_or_bindings(self):
        a, b = self.styles()
        for kind in routes.composition.TEST_TEMPLATES:
            with self.subTest(kind=kind):
                piece = await self.save_tested_piece(a, kind)
                original = copy.deepcopy(piece)
                status, result = await self.request("POST", "/compose", self.integration(b, [piece]))
                self.assertEqual(status, 200, result)
                self.assertEqual(result["snapshot"]["style"], b)
                for field in ("model", "text_encoder", "vae", "steps", "cfg", "sampler", "scheduler", "seed"):
                    self.assertEqual(result["config"][field], b["config"][field], field)
                self.assertEqual(result["config"]["loras"], [
                    {"file": "anima/finish-b.safetensors", "strength": 0},
                    {"file": f"pieces/{kind}.safetensors", "strength": original["loras"][0]["strength"]}])
                self.assertIn(f"trigger-{kind}", result["prompt"])
                self.assertIn(f"own {kind}", result["prompt"])
                self.assertIn(f"avoid {kind}", result["negative"])
                self.assertNotIn("finish A", result["prompt"] + result["negative"])
                self.assertEqual(result["snapshot"]["pieces"], [original])
                self.assertNotIn("trial_negative", original["definition"])
                self.assertNotIn("style_id", original)
                self.assertEqual(original["generation_snapshot"]["snapshot"]["style"], a)
                self.assertEqual(self.store.get("pieces", piece["id"]), original)
        self.assertEqual(self.store.get("styles", a["id"]), a)
        self.assertEqual(self.store.get("styles", b["id"]), b)

    async def test_sprite_integrates_every_role_and_uses_its_own_framing_not_preview_templates(self):
        a, b = self.styles()
        pieces = [await self.save_tested_piece(a, kind) for kind in ("character", "outfit", "pose", "expression")]
        status, result = await self.request("POST", "/compose", self.integration(b, pieces,
            prompt="clean silhouette", integration_framing="full-body"))
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result["config"]["loras"]), 5)
        for kind in ("character", "outfit", "pose", "expression"):
            self.assertIn(f"own {kind}", result["prompt"])
        self.assertEqual(result["prompt"].count("finish B quality"), 1)
        self.assertIn("entire figure visible including feet", result["prompt"])
        for staging in ("neutral mannequin", "featureless", "design study", "front-facing portrait"):
            self.assertNotIn(staging, result["prompt"])
        self.assertEqual(result["composition"]["integration_framing"], "full-body")
        self.assertEqual(result["composition"]["integration_user_prompt"], "clean silhouette")
        record = self.store.save_generation({**result, "project_id": self.one["id"], "category": "characters",
            "seed": result["config"]["seed"]}, fixtures.png())
        frozen = routes.composition.frozen(record)
        self.assertEqual(frozen["prompt"], result["prompt"])
        self.assertEqual(frozen["composition"]["integration_user_prompt"], "clean silhouette")

    async def test_style_render_settings_are_protected_but_piece_loras_keep_zero_disabled_and_triggers(self):
        _, b = self.styles()
        piece = self.store.save("pieces", {"project_id": self.one["id"], "name": "Outfit",
            "kind": "outfit", "prompt": "green dress", "loras": [
                {"file": "pieces/outfit.safetensors", "strength": 0, "trigger": "dress-trigger"},
                {"file": "pieces/pose.safetensors", "strength": .9, "trigger": "disabled-trigger", "enabled": False}]})
        status, result = await self.request("POST", "/compose", self.integration(b, [piece]))
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["loras"][1], {"file": "pieces/outfit.safetensors", "strength": 0.0})
        self.assertIn("dress-trigger", result["prompt"])
        self.assertNotIn("disabled-trigger", result["prompt"])
        for field, value in {"model": "other", "text_encoder": "other", "vae": "other", "steps": 1,
                             "cfg": 20, "sampler": "other", "scheduler": "other", "loras": []}.items():
            payload = self.integration(b, [piece])
            payload["config"][field] = value
            status, error = await self.request("POST", "/compose", payload)
            self.assertEqual(status, 400, (field, error))
            self.assertIn("Estilos", error["detail"])
        for override in ("", "replace all negatives"):
            status, error = await self.request("POST", "/compose", self.integration(b, [piece], negative_override=override))
            self.assertEqual(status, 400, error)
            self.assertIn("negativo adicional", error["detail"])

    async def test_lora_duplicates_deduplicate_and_conflicting_weights_name_the_file(self):
        _, b = self.styles()
        a = self.store.save("pieces", {"project_id": self.one["id"], "name": "Outfit", "kind": "outfit",
            "loras": [{"file": "pieces/outfit.safetensors", "strength": .4}]})
        same = self.store.save("pieces", {"project_id": self.one["id"], "name": "Pose", "kind": "pose",
            "loras": [{"file": "pieces\\OUTFIT.safetensors", "strength": .4}]})
        status, result = await self.request("POST", "/compose", self.integration(b, [a, same]))
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result["config"]["loras"]), 2)
        changed = self.store.save("pieces", {"id": same["id"], "loras": [{"file": "pieces/outfit.safetensors", "strength": .7}]})
        status, result = await self.request("POST", "/compose", self.integration(b, [a, changed]))
        self.assertEqual(status, 400, result)
        self.assertIn("outfit.safetensors", result["detail"])
        self.assertIn("pesos distintos", result["detail"])
        self.assertEqual(self.store.get("styles", b["id"]), b)

    async def test_content_controls_contribute_without_character_outfit_or_icon_preview_staging(self):
        _, b = self.styles()
        definitions = {
            "character": {"framing": "portrait", "view": "back"},
            "outfit": {"view": "back"}, "pose": {"view": "profile"},
            "expression": {"intensity": "strong"},
            "background": {"light": "dusk", "weather": "rain", "space": "left"},
            "item": {"view": "threequarter", "presentation": "icon"},
        }
        pieces = [self.store.save("pieces", {"project_id": self.one["id"], "name": kind, "kind": kind,
            "prompt": f"own {kind}", "definition": {"presentation": values}}) for kind, values in definitions.items()]
        before = copy.deepcopy(pieces)
        status, result = await self.request("POST", "/compose", self.integration(b, pieces))
        self.assertEqual(status, 200, result)
        for fragment in ("Side profile view", "Strong clearly readable facial expression", "Warm dusk lighting",
                         "Rainy weather", "foreground space on the left", "Three-quarter view"):
            self.assertIn(fragment, result["prompt"])
        for fragment in ("Back view", "Close-up portrait", "Inventory icon composition", "mannequin"):
            self.assertNotIn(fragment, result["prompt"])
        self.assertEqual(result["snapshot"]["pieces"], before)
        self.assertEqual(pieces, before)

    async def test_editing_own_negative_does_not_add_trial_quality_or_mutate_revision_history(self):
        a, b = self.styles()
        piece = await self.save_tested_piece(a, "expression")
        old = copy.deepcopy(piece)
        changed = self.store.save("pieces", {"id": piece["id"], "negative": "frowning",
            "style_id": a["id"], "style_revision": a["revision"], "config": a["config"],
            "definition": {"presentation": {"intensity": "subtle"}, "trial_negative": {"style_id": a["id"]}}})
        for key in ("style_id", "style_revision", "config"):
            self.assertNotIn(key, changed)
        self.assertNotIn("trial_negative", changed["definition"])
        self.assertEqual(self.store.get("pieces", old["id"], old["revision"]), old)
        status, result = await self.request("POST", "/compose", self.integration(b, [changed]))
        self.assertEqual(status, 200, result)
        self.assertIn("finish B flaws", result["negative"])
        self.assertIn("frowning", result["negative"])
        self.assertNotIn("finish A", result["negative"])

    async def test_legacy_style_trial_references_do_not_block_deletion_but_real_recipes_do(self):
        a, b = self.styles()
        piece = self.store.save("pieces", {"project_id": self.one["id"], "name": "Old outfit", "kind": "outfit"})
        legacy = {**piece, "definition": {"trial_negative": {"style_id": a["id"], "text": "trial flaws"}}}
        self.store._write(self.store._path("pieces", f"{piece['id']}.json"), legacy)
        self.store._write(self.store._path("revisions", "pieces", piece["id"], "00000001.json"), legacy)
        self.assertTrue(self.store.delete("styles", a["id"])["deleted"])
        recipe = self.store.save("recipes", {"project_id": self.one["id"], "name": "Real integration",
            "style_id": b["id"], "style_revision": b["revision"], "piece_ids": [piece["id"]]})
        with self.assertRaisesRegex(routes.LibraryConflict, "utiliza"):
            self.store.delete("styles", b["id"])
        self.assertEqual(self.store.get("recipes", recipe["id"])["style_id"], b["id"])

    async def test_expression_replacement_changes_its_loras_only_and_frozen_style_cannot_be_changed(self):
        a, b = self.styles()
        hero = await self.save_tested_piece(a, "character")
        sad = await self.save_tested_piece(a, "expression")
        status, result = await self.request("POST", "/compose", self.integration(b, [hero, sad], integration_framing="waist-up"))
        self.assertEqual(status, 200, result)
        record = self.store.save_generation({**result, "project_id": self.one["id"], "category": "characters",
            "seed": result["config"]["seed"]}, fixtures.png())
        happy = self.store.save("pieces", {"project_id": self.one["id"], "name": "Happy", "kind": "expression",
            "prompt": "happy", "negative": "sad", "loras": [{"file": "pieces/new-expression.safetensors", "strength": .8, "trigger": "smile"}]})
        before = copy.deepcopy(record)
        status, frozen = await self.request("POST", "/compose", {"project_id": self.one["id"], "integration": True,
            "source_asset_id": record["id"], "style_id": b["id"], "style_revision": b["revision"],
            "expression_piece_id": happy["id"], "mode": "expression", "config": {"denoise": .4}})
        self.assertEqual(status, 200, frozen)
        files = {lora["file"] for lora in frozen["config"]["loras"]}
        self.assertEqual(files, {"anima/finish-b.safetensors", "pieces/character.safetensors", "pieces/new-expression.safetensors"})
        self.assertEqual(frozen["config"]["model"], b["config"]["model"])
        self.assertEqual(frozen["composition"]["integration_framing"], "waist-up")
        self.assertEqual(record, before)
        status, error = await self.request("POST", "/compose", {"project_id": self.one["id"], "integration": True,
            "source_asset_id": record["id"], "style_id": a["id"], "style_revision": a["revision"]})
        self.assertEqual(status, 400, error)

    async def test_integrated_generation_uses_selected_style_files_and_independent_piece_loras(self):
        a, b = self.styles()
        outfit = await self.save_tested_piece(a, "outfit")
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            status, job = await self.request("POST", "/generate", self.integration(b, [outfit],
                category="characters", mode="txt2img", purpose="production", integration_framing="portrait"))
            self.assertEqual(status, 200, job)
            record = await self.finish(job["job_id"])
            await asyncio.gather(*list(routes._tasks))
            await asyncio.sleep(0)
        self.assertEqual(record["config"]["model"], b["config"]["model"])
        self.assertEqual(record["config"]["steps"], b["config"]["steps"])
        self.assertNotIn("anima/paint.safetensors", [entry["file"] for entry in record["config"]["loras"]])
        self.assertIn("trigger-outfit", record["prompt"])
        self.assertEqual(self.store.get("pieces", outfit["id"]), outfit)

    async def test_integrator_flags_style_revisions_and_framing_are_validated(self):
        _, b = self.styles()
        for change in ({"integration": "true"}, {"style_id": ""}, {"style_revision": None},
                       {"style_revision": True}, {"style_revision": 0}, {"integration_framing": "panorama"},
                       {"integration_framing": []}, {"piece_test": {"kind": "character"}}):
            status, error = await self.request("POST", "/compose", self.integration(b, **change))
            self.assertEqual(status, 400, (change, error))


if __name__ == "__main__":
    unittest.main()
