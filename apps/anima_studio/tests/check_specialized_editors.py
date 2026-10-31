"""Fixed calibrated styles and editable effective negatives in typed editors."""
import asyncio
import copy
import unittest
from unittest.mock import patch

import check_api as fixtures

routes = fixtures.routes


class SpecializedEditorApiChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiChecks.asyncSetUp
    asyncTearDown = fixtures.ApiChecks.asyncTearDown
    request = fixtures.ApiChecks.request
    payload = fixtures.ApiChecks.payload
    finish = fixtures.ApiChecks.finish
    render = staticmethod(fixtures.ApiChecks.render)

    def style(self):
        return self.store.save("styles", {"project_id": self.one["id"], "name": "Calibrated ink",
            "prompt": "masterpiece, ink finish", "negative": "photo, grain, blurry",
            "config": {**self.payload()["config"], "steps": 19, "cfg": 0, "sampler": "euler",
                       "scheduler": "karras", "loras": [{"file": "anima/paint.safetensors", "strength": 0}]}})

    def specialized(self, style, kind="character", **values):
        return {"project_id": self.one["id"], "purpose": "piece_test", "mode": "txt2img",
            "style_id": style["id"], "style_revision": style["revision"],
            "config": {"seed": "18446744073709551615", "width": 128, "height": 256},
            "piece_ids": [], "piece_test": {"specialized": True, "kind": kind,
                "draft": {"kind": kind, "prompt": "explore a design"}, "token": "specialized-trial",
                "reuse_master_settings": False}, **values}

    async def test_all_typed_editors_generate_from_the_fixed_style_without_saving_a_piece(self):
        style = self.style()
        before = copy.deepcopy(style)
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            for kind in routes.composition.TEST_TEMPLATES:
                with self.subTest(kind=kind):
                    payload = self.specialized(style, kind)
                    status, job = await self.request("POST", "/generate", payload)
                    self.assertEqual(status, 200, job)
                    record = await self.finish(job["job_id"])
                    await asyncio.gather(*list(routes._tasks))
                    await asyncio.sleep(0)
                    for key in ("model", "text_encoder", "vae", "steps", "cfg", "sampler", "scheduler", "loras"):
                        self.assertEqual(record["config"][key], style["config"][key], key)
                    self.assertEqual(record["config"]["seed"], "18446744073709551615")
                    self.assertEqual((record["config"]["width"], record["config"]["height"]), (128, 256))
                    self.assertEqual(record["snapshot"]["style"], before)
                    self.assertIn("masterpiece, ink finish", record["prompt"])
                    self.assertIn("explore a design", record["prompt"])
                    self.assertEqual(self.store.list("pieces"), [])
        self.assertEqual(self.store.get("styles", style["id"]), before)

    async def test_model_sampling_and_finish_loras_cannot_be_overridden_from_a_typed_editor(self):
        style = self.style()
        for key, value in {"model": "other", "text_encoder": "other", "vae": "other", "steps": 4,
                           "cfg": 8, "sampler": "other", "scheduler": "other", "loras": [],
                           "denoise": .5, "unexpected": True}.items():
            with self.subTest(key=key):
                payload = self.specialized(style)
                payload["config"][key] = value
                status, result = await self.request("POST", "/compose", payload)
                self.assertEqual(status, 400, result)
                self.assertIn("Estilos", result["detail"])
        self.assertEqual(self.store.list("pieces"), [])
        self.assertEqual(self.store.get("styles", style["id"]), style)

    async def test_style_and_explicit_revision_are_required_but_open_draft_needs_no_inventory(self):
        style = self.style()
        for patch_values in ({"style_id": ""}, {"style_id": None}, {"style_revision": None},
                             {"style_revision": True}, {"style_revision": 0}, {"style_revision": "1"}):
            payload = {**self.specialized(style), **patch_values}
            status, result = await self.request("POST", "/compose", payload)
            self.assertEqual(status, 400, result)
        payload = self.specialized(style)
        payload["piece_test"]["specialized"] = "true"
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 400, result)
        self.assertEqual(self.store.list("pieces"), [])

    async def test_historical_style_revision_stays_exact_even_when_master_has_other_replay_settings(self):
        style = self.style()
        self.store.save("styles", {"id": style["id"], "prompt": "changed finish",
            "config": {**style["config"], "cfg": 17, "steps": 5}})
        payload = self.specialized(style)
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["snapshot"]["style"], style)
        self.assertEqual(result["config"]["cfg"], 0)
        self.assertEqual(result["config"]["steps"], 19)
        self.assertNotIn("changed finish", result["prompt"])

    async def test_identity_lora_is_added_without_replacing_finish_loras_or_their_zero_weight(self):
        self.catalog.DATA = copy.deepcopy(self.catalog.DATA)
        self.catalog.DATA["loras"].append({"file": "anima/identity.safetensors", "name": "Identity"})
        style = self.style()
        payload = self.specialized(style)
        payload["piece_test"]["draft"]["loras"] = [
            {"file": "anima/identity.safetensors", "strength": .65, "trigger": "lookalike"}]
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["loras"], [
            {"file": "anima/paint.safetensors", "strength": 0.0},
            {"file": "anima/identity.safetensors", "strength": .65}])
        self.assertIn("lookalike", result["prompt"])
        self.assertEqual(result["snapshot"]["style"], style)

    async def test_deleted_style_replays_only_from_real_sample_or_master_and_trial_seed_wins(self):
        style = self.style()
        status, original = await self.request("POST", "/compose", self.specialized(style))
        self.assertEqual(status, 200, original)
        sample = self.store.save_generation({**original, "project_id": self.one["id"], "category": "characters",
            "seed": original["config"]["seed"]}, fixtures.png())
        # Generation snapshots are self-contained; deletion removes both current
        # metadata and revisions, while a later real sample can still prove them.
        self.store.delete("styles", style["id"])
        payload = self.specialized(style)
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 404, result)
        payload["piece_test"]["draft"]["sample_asset_id"] = sample["id"]
        payload["config"]["seed"] = "73"
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["snapshot"]["style"], style)
        self.assertEqual(result["config"]["seed"], "73")
        self.assertEqual(result["snapshot"]["pieces"][0]["seed"], "73")
        status, master = await self.request("POST", "/pieces", {"project_id": self.one["id"],
            "kind": "character", "name": "Master", "prompt": "identity", "sample_asset_id": sample["id"]})
        self.assertEqual(status, 200, master)
        payload["piece_test"]["draft"] = {"master_id": master["id"], "master_revision": master["revision"]}
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["snapshot"]["style"], style)
        payload["style_revision"] += 1
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 404, result)
        payload["style_revision"] = style["revision"]
        payload["piece_test"]["draft"] = {"generation_snapshot": sample}
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 400, result)

    async def test_negative_override_replaces_style_piece_and_template_negatives_including_empty(self):
        style = self.style()
        for text in ("grain", "", "  exact user text\nline two  "):
            with self.subTest(text=text):
                payload = self.specialized(style, negative_override=text)
                payload["piece_test"]["draft"]["negative"] = "old identity exclusion"
                payload["piece_test"]["context_negative"] = "template exclusion"
                status, result = await self.request("POST", "/compose", payload)
                self.assertEqual(status, 200, result)
                self.assertEqual(result["negative"], text)
                self.assertEqual(result["composition"]["negative_override"], text)
                self.assertEqual(result["snapshot"]["style"]["negative"], style["negative"])
                self.assertEqual(result["snapshot"]["pieces"][0]["negative"], "old identity exclusion")
                self.assertEqual(self.store.get("styles", style["id"]), style)
        for value in (None, False, [], {}):
            status, result = await self.request("POST", "/compose", self.specialized(style, negative_override=value))
            self.assertEqual(status, 400, result)

    async def test_existing_piece_without_preview_proves_its_deleted_style_without_changing_its_revision(self):
        style = self.style()
        status, original = await self.request("POST", "/compose", self.specialized(style))
        self.assertEqual(status, 200, original)
        sample = self.store.save_generation({**original, "project_id": self.one["id"], "category": "characters",
            "seed": original["config"]["seed"]}, fixtures.png())
        self.store.delete("styles", style["id"])
        for scope in (self.one["id"], None):
            with self.subTest(scope=scope):
                source = self.store.save("pieces", {"project_id": scope, "kind": "character",
                    "name": "Saved identity", "prompt": "old identity", "sample_asset_id": sample["id"]})
                source = self.store.save("pieces", {"id": source["id"], "sample_asset_id": None})
                self.assertNotIn("sample_asset_id", source)
                before = copy.deepcopy(source)
                payload = self.specialized(style)
                payload["config"]["seed"] = "91"
                payload["piece_test"].update(source_piece_id=source["id"], source_piece_revision=source["revision"])
                payload["piece_test"]["draft"].update(prompt="edited identity", sample_asset_id=None)
                status, result = await self.request("POST", "/compose", payload)
                self.assertEqual(status, 200, result)
                self.assertEqual(result["snapshot"]["style"], style)
                self.assertEqual(result["config"]["seed"], "91")
                self.assertEqual(result["snapshot"]["pieces"][0]["prompt"], "edited identity")
                self.assertEqual(self.store.get("pieces", source["id"]), before)
                payload["style_revision"] += 1
                status, result = await self.request("POST", "/compose", payload)
                self.assertEqual(status, 404, result)
        self.assertEqual(len(self.store.list("pieces")), 2)

    async def test_source_piece_reference_checks_scope_kind_and_pinned_revision(self):
        style = self.style()
        source = self.store.save("pieces", {"project_id": self.one["id"], "kind": "character", "name": "Source"})
        foreign = self.store.save("pieces", {"project_id": self.two["id"], "kind": "character", "name": "Foreign"})
        outfit = self.store.save("pieces", {"project_id": self.one["id"], "kind": "outfit", "name": "Outfit"})
        bad = [
            ({"source_piece_id": source["id"]}, 400),
            ({"source_piece_revision": 1}, 400),
            ({"source_piece_id": [], "source_piece_revision": 1}, 400),
            ({"source_piece_id": source["id"], "source_piece_revision": True}, 400),
            ({"source_piece_id": source["id"], "source_piece_revision": 0}, 400),
            ({"source_piece_id": source["id"], "source_piece_revision": "1"}, 400),
            ({"source_piece_id": "missing", "source_piece_revision": 1}, 404),
            ({"source_piece_id": source["id"], "source_piece_revision": 999}, 404),
            ({"source_piece_id": foreign["id"], "source_piece_revision": foreign["revision"]}, 400),
            ({"source_piece_id": outfit["id"], "source_piece_revision": outfit["revision"]}, 400),
        ]
        for values, expected in bad:
            with self.subTest(values=values):
                payload = self.specialized(style)
                payload["piece_test"].update(values)
                status, result = await self.request("POST", "/compose", payload)
                self.assertEqual(status, expected, result)
        payload = self.specialized(style)
        payload["piece_test"].update(source_piece_id=source["id"], source_piece_revision=source["revision"])
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.store.get("pieces", source["id"]), source)

    async def test_saved_sample_and_frozen_expression_keep_the_effective_negative_without_restoring_removed_tags(self):
        style = self.style()
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            status, job = await self.request("POST", "/generate", self.specialized(style, negative_override="grain"))
            self.assertEqual(status, 200, job)
            record = await self.finish(job["job_id"])
        self.assertEqual(record["negative"], "grain")
        status, piece = await self.request("POST", "/pieces", {"project_id": self.one["id"],
            "kind": "character", "name": "Chosen identity", "prompt": "identity only", "negative": "",
            "sample_asset_id": record["id"]})
        self.assertEqual(status, 200, piece)
        self.assertEqual(piece["negative"], "")
        self.assertEqual(piece["generation_snapshot"]["negative"], "grain")
        self.assertEqual(piece["generation_snapshot"]["composition"]["negative_override"], "grain")
        self.assertEqual(piece["generation_snapshot"]["snapshot"]["style"], style)
        frozen = routes.composition.frozen(record)
        self.assertEqual(frozen["negative"], "grain")
        self.assertEqual(frozen["composition"]["negative_override"], "grain")
        edited = routes.composition.frozen(record, expression_prompt="happy", expression_negative="frowning")
        self.assertEqual(edited["negative"], "grain\nfrowning")
        self.assertNotIn("photo", edited["negative"])
        self.assertEqual(routes.composition.frozen(record, expression_prompt="happy", negative_override="")["negative"], "")
        self.assertEqual(self.store.get_generation(record["id"])["negative"], "grain")

    async def test_normal_compositions_support_effective_negative_override_and_keep_legacy_additive_mode(self):
        style = self.style()
        base = {"project_id": self.one["id"], "style_id": style["id"], "prompt": "a forest", "negative": "darkness"}
        status, legacy = await self.request("POST", "/compose", base)
        self.assertEqual(status, 200, legacy)
        self.assertEqual(legacy["negative"], "photo, grain, blurry\ndarkness")
        status, result = await self.request("POST", "/compose", {**base, "negative_override": "only fog"})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["negative"], "only fog")
        record = self.store.save_generation({**result, "project_id": self.one["id"], "category": "characters",
            "seed": result["config"]["seed"]}, fixtures.png())
        status, restored = await self.request("POST", "/compose", {"project_id": self.one["id"],
            "source_asset_id": record["id"], "expression_prompt": "happy", "negative_override": ""})
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["negative"], "")
        self.assertEqual(restored["composition"]["negative_override"], "")


if __name__ == "__main__":
    unittest.main()
