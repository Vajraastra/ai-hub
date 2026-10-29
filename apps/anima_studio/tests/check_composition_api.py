"""Exercise composition contracts through HTTP without a GPU/backend."""
import base64
import json
import unittest
from unittest.mock import patch

import check_api as fixtures

routes = fixtures.routes


class CompositionApiChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiChecks.asyncSetUp
    asyncTearDown = fixtures.ApiChecks.asyncTearDown
    request = fixtures.ApiChecks.request
    payload = fixtures.ApiChecks.payload
    finish = fixtures.ApiChecks.finish
    render = staticmethod(fixtures.ApiChecks.render)

    def pieces(self):
        style = self.store.save("styles", {"name": "Paint", "project_id": self.one["id"], "prompt": "painted",
            "config": {**self.payload()["config"], "cfg": 4}})
        hero = self.store.save("pieces", {"name": "Hero", "kind": "character", "project_id": self.one["id"],
            "prompt": "blue eyes", "seed": "18446744073709551615"})
        outfit = self.store.save("pieces", {"name": "Outfit", "kind": "outfit", "project_id": self.one["id"],
            "prompt": "green dress", "seed": "12"})
        sad = self.store.save("pieces", {"name": "Sad", "kind": "expression", "project_id": self.one["id"],
            "prompt": "sad", "negative": "smiling"})
        return style, hero, outfit, sad

    async def master(self):
        style, hero, outfit, sad = self.pieces()
        payload = {"project_id": self.one["id"], "style_id": style["id"],
                   "piece_ids": [hero["id"], outfit["id"], sad["id"]], "prompt": "full body"}
        status, assembled = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, assembled)
        return self.store.save_generation({**assembled, "project_id": self.one["id"], "category": "characters",
            "seed": assembled["config"]["seed"]}, fixtures.png())

    async def test_first_composition_uses_character_seed_and_outfit_test_uses_its_own_seed(self):
        style, hero, outfit, _ = self.pieces()
        payload = {"project_id": self.one["id"], "style_id": style["id"], "piece_ids": [outfit["id"], hero["id"]]}
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["config"]["seed"], hero["seed"])
        payload["piece_test"] = {"kind": "outfit", "id": outfit["id"]}
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["config"]["seed"], "12")
        self.assertNotIn("blue eyes", result["prompt"])
        self.assertIn("neutral mannequin", result["prompt"])

    async def test_frozen_master_ignores_live_style_and_form_seed(self):
        master = await self.master()
        style = master["snapshot"]["style"]
        self.store.save("styles", {"id": style["id"], "prompt": "CHANGED", "config": {"seed": "3", "model": "OTHER"}})
        status, result = await self.request("POST", "/compose", {"project_id": self.one["id"],
            "source_asset_id": master["id"], "expression_prompt": "happy", "expression_negative": "frowning",
            "config": {"seed": "1", "model": "spoof"}, "prompt": "UNWANTED"})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["seed"], master["seed"])
        self.assertEqual(result["config"]["model"], "anima/base.safetensors")
        self.assertNotIn("sad", result["prompt"])
        self.assertNotIn("CHANGED", result["prompt"])
        self.assertNotIn("UNWANTED", result["prompt"])
        self.assertNotIn("smiling", result["negative"])

    async def test_expression_job_reuses_real_master_seed_and_stores_block_provenance(self):
        master = await self.master()
        captured = []

        async def render(client, params, on_progress=None, **kwargs):
            captured.append(params)
            return await self.render(client, params, on_progress, **kwargs)

        with patch.object(routes.pipeline, "generate", side_effect=render):
            status, job = await self.request("POST", "/generate", {"project_id": self.one["id"],
                "source_asset_id": master["id"], "mode": "expression", "expression_prompt": "surprised",
                "config": {"seed": "1", "model": "spoof", "denoise": .4},
                "mask_base64": base64.b64encode(fixtures.png("white")).decode()})
            self.assertEqual(status, 200, job)
            record = await self.finish(job["job_id"])
        self.assertEqual(captured[0].seed, master["seed"])
        self.assertEqual(captured[0].model, master["config"]["model"])
        self.assertEqual(captured[0].denoise, .4)
        self.assertEqual(record["composition"]["source_asset_id"], master["id"])
        self.assertEqual(record["master_asset_id"], master["id"])

    async def test_source_scope_and_wrong_expression_kind_are_rejected(self):
        master = await self.master()
        status, _ = await self.request("POST", "/compose", {"project_id": self.two["id"], "source_asset_id": master["id"]})
        self.assertEqual(status, 400)
        hero = master["snapshot"]["pieces"][0]
        status, _ = await self.request("POST", "/compose", {"project_id": self.one["id"],
            "source_asset_id": master["id"], "expression_piece_id": hero["id"]})
        self.assertEqual(status, 400)

    async def test_legacy_master_parameter_also_freezes_replay(self):
        master = await self.master()
        status, result = await self.request("POST", "/compose", {"project_id": self.one["id"],
            "master_asset_id": master["id"], "mode": "expression", "prompt": "happy",
            "config": {"model": "spoof", "seed": "1"}})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["seed"], master["seed"])
        self.assertEqual(result["config"]["model"], master["config"]["model"])
        self.assertIn("happy", result["prompt"])
        self.assertNotIn("sad", result["prompt"])

    async def test_two_pose_roles_are_rejected_instead_of_ignoring_one(self):
        poses = [self.store.save("pieces", {"name": name, "kind": "pose", "prompt": name,
                                          "project_id": self.one["id"]}) for name in ("Standing", "Sitting")]
        status, _ = await self.request("POST", "/compose", {"project_id": self.one["id"],
            "piece_ids": [p["id"] for p in poses]})
        self.assertEqual(status, 400)

    async def test_variant_replays_sampling_without_retaining_old_character_lora(self):
        master = await self.master()
        hero = master["snapshot"]["pieces"][0]
        piece = self.store.save("pieces", {"id": hero["id"], "kind": "character", "sample_asset_id": master["id"]})
        child = self.store.save("pieces", {"kind": "character", "name": "Variant", "master_id": piece["id"],
            "project_id": self.one["id"], "loras": [{"file": "anima/paint.safetensors", "strength": .5}]})
        style = master["snapshot"]["style"]
        self.store.save("styles", {"id": style["id"], "config": {"model": "CHANGED", "cfg": 17}})
        status, result = await self.request("POST", "/compose", {"project_id": self.one["id"],
            "style_id": style["id"], "piece_test": {"kind": "character", "id": child["id"]}})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["model"], "anima/base.safetensors")
        self.assertEqual(result["config"]["cfg"], master["config"].get("cfg", 4))
        self.assertEqual(result["config"]["loras"], [{"file": "anima/paint.safetensors", "strength": .5}])
        status, bundle = await self.request("GET", f'/projects/{self.one["id"]}/export')
        status, project = await self.request("POST", "/projects/import", bundle)
        self.assertEqual(status, 200, project)
        imported = next(p for p in self.store.list("pieces", project["id"]) if p["name"] == "Variant")
        replay_style = imported["generation_snapshot"]["snapshot"]["style"]
        status, replay = await self.request("POST", "/compose", {"project_id": project["id"],
            "style_id": replay_style["id"], "piece_test": {"kind": "character", "id": imported["id"]}})
        self.assertEqual(status, 200, replay)
        self.assertEqual(replay["config"]["model"], "anima/base.safetensors")
    async def test_profile_import_orders_variants_before_remapping_their_pinned_master(self):
        master = self.store.save("pieces", {"id": "z-master", "kind": "character", "name": "Master",
            "project_id": self.one["id"], "prompt": "blue eyes", "seed": "18446744073709551615"})
        child = self.store.save("pieces", {"id": "a-variant", "kind": "character", "name": "Variant",
            "project_id": self.one["id"], "master_id": master["id"], "master_revision": 1, "prompt": "green eyes"})
        self.store.save("pieces", {"id": master["id"], "prompt": "brown eyes"})
        status, bundle = await self.request("GET", f'/projects/{self.one["id"]}/export')
        self.assertEqual(status, 200)
        status, project = await self.request("POST", "/projects/import", bundle)
        self.assertEqual(status, 200, project)
        imported = self.store.list("pieces", project["id"])
        new_child = next(p for p in imported if p["name"] == "Variant")
        new_master = self.store.get("pieces", new_child["master_id"], new_child["master_revision"])
        self.assertEqual(new_master["prompt"], "blue eyes")
        self.assertEqual(new_child["prompt"], "green eyes")
        self.assertEqual(new_child["seed"], "18446744073709551615")


if __name__ == "__main__":
    unittest.main()
