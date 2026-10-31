"""Explore typed pieces before saving their identity or inventory configuration."""
import asyncio
import base64
import copy
import unittest
from unittest.mock import patch

import check_api as fixtures

routes = fixtures.routes


class PieceDraftApiChecks(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiChecks.asyncSetUp
    asyncTearDown = fixtures.ApiChecks.asyncTearDown
    request = fixtures.ApiChecks.request
    payload = fixtures.ApiChecks.payload
    finish = fixtures.ApiChecks.finish
    render = staticmethod(fixtures.ApiChecks.render)

    def draft_payload(self, kind="character", **values):
        return self.payload(prompt="", category="not-configured", purpose="piece_test", piece_ids=[],
                            piece_test={"kind": kind, "draft": {}, "token": "workbench-1",
                                        "reuse_master_settings": False, **values})

    async def test_every_piece_type_can_generate_an_unnamed_blank_trial_without_inventory_or_styles(self):
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            for kind, template in routes.composition.TEST_TEMPLATES.items():
                with self.subTest(kind=kind):
                    payload = self.draft_payload(kind)
                    status, assembled = await self.request("POST", "/compose", payload)
                    self.assertEqual(status, 200, assembled)
                    self.assertEqual(assembled["snapshot"]["style"], None)
                    snapshot = assembled["snapshot"]["pieces"][0]
                    self.assertEqual(snapshot["kind"], kind)
                    self.assertTrue(snapshot["draft"])
                    self.assertTrue(snapshot["id"].startswith("draft-"))
                    self.assertEqual(snapshot["revision"], 0)
                    self.assertEqual(assembled["prompt"], template[0])
                    self.assertEqual(assembled["negative"], template[1])
                    status, job = await self.request("POST", "/generate", payload)
                    self.assertEqual(status, 200, job)
                    result = await self.finish(job["job_id"])
                    await asyncio.gather(*list(routes._tasks))
                    await asyncio.sleep(0)  # coordinator release is a task done callback
                    self.assertEqual(result["category"], self.one["categories"][0])
                    self.assertEqual(result["piece_test"]["token"], "workbench-1")
                    self.assertEqual(result["snapshot"]["pieces"][0]["kind"], kind)
                    self.assertEqual(self.store.list("pieces"), [])
                    self.assertEqual(self.store.list("styles"), [])
        self.assertFalse((self.store.root / "revisions" / "pieces").exists())

    async def test_draft_uses_validated_character_lora_and_all_seed_bits_without_modifying_inputs(self):
        payload = self.draft_payload(draft={"kind": "character", "prompt": "blue hair", "negative": "red hair",
            "seed": "18446744073709551615", "loras": [{"file": "anima\\paint.safetensors", "strength": 0}]})
        payload["config"].pop("seed")
        before = copy.deepcopy(payload)
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["config"]["seed"], "18446744073709551615")
        self.assertEqual(result["config"]["loras"], [{"file": "anima/paint.safetensors", "strength": 0.0}])
        self.assertIn("blue hair", result["prompt"])
        self.assertIn("red hair", result["negative"])
        self.assertEqual(payload, before)

    async def test_selected_trial_can_be_saved_later_with_its_effective_config_and_context(self):
        payload = self.draft_payload("outfit", draft={"prompt": "green dress", "negative": "red fabric"},
            context_prompt="one mannequin, profile view", context_negative="hat")
        payload["config"].update(steps=7, cfg=2.5)
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            status, job = await self.request("POST", "/generate", payload)
            self.assertEqual(status, 200, job)
            result = await self.finish(job["job_id"])
        self.assertEqual(self.store.list("pieces"), [])
        payload["piece_test"]["draft"]["prompt"] = "later editor contents"
        payload["config"]["cfg"] = 8
        chosen = result["snapshot"]["pieces"][0]
        status, saved = await self.request("POST", "/pieces", {"project_id": self.one["id"], "kind": "outfit",
            "name": "Green dress", "prompt": chosen["prompt"], "negative": chosen["negative"],
            "sample_asset_id": result["id"]})
        self.assertEqual(status, 200, saved)
        self.assertFalse(saved["id"].startswith("draft-"))
        self.assertEqual(saved["prompt"], "green dress")
        self.assertEqual(saved["generation_snapshot"]["config"]["cfg"], 2.5)
        self.assertEqual(saved["generation_snapshot"]["config"]["steps"], 7)
        self.assertIn("one mannequin, profile view", saved["generation_snapshot"]["prompt"])
        self.assertNotIn("later editor contents", saved["generation_snapshot"]["prompt"])

    async def test_editable_context_replaces_defaults_and_can_be_cleared_explicitly(self):
        payload = self.draft_payload("outfit", draft={"prompt": "green dress", "negative": "red fabric"},
            context_prompt="folded clothes on a table", context_negative="mannequin")
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["prompt"], "green dress\nfolded clothes on a table")
        self.assertEqual(result["negative"], "red fabric\nmannequin")
        payload["piece_test"].update(context_prompt="", context_negative="")
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["prompt"], "green dress")
        self.assertEqual(result["negative"], "red fabric")
        payload["piece_test"]["draft"] = {}
        status, result = await self.request("POST", "/generate", payload)
        self.assertEqual(status, 400, result)
        self.assertIn("prompt", result["detail"])

    async def test_unnamed_trial_with_empty_identity_can_be_saved_without_turning_its_context_into_identity(self):
        payload = self.draft_payload("character", draft={"prompt": "", "negative": ""},
            context_prompt="one standing figure, full body", context_negative="collage")
        payload["prompt"] = "ink finish"
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            status, job = await self.request("POST", "/generate", payload)
            self.assertEqual(status, 200, job)
            result = await self.finish(job["job_id"])
        status, saved = await self.request("POST", "/pieces", {"project_id": self.one["id"], "kind": "character",
            "name": "Chosen figure", "prompt": "", "negative": "", "sample_asset_id": result["id"]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["prompt"], "")
        self.assertEqual(saved["negative"], "")
        replay = saved["generation_snapshot"]
        self.assertIsNone(replay["snapshot"]["style"])
        self.assertEqual(replay["config"], result["config"])
        self.assertEqual(replay["composition"]["piece_test"]["context_prompt"], "one standing figure, full body")
        self.assertEqual(replay["composition"]["piece_test"]["context_negative"], "collage")
        self.assertEqual(replay["composition"]["piece_test"]["draft"]["prompt"], "")
        self.assertIn("ink finish", replay["prompt"])
        self.assertIn("one standing figure, full body", replay["prompt"])

    async def test_composition_without_saved_style_can_be_recovered_and_edited_from_its_own_master(self):
        with patch.object(routes.pipeline, "generate", side_effect=self.render):
            status, job = await self.request("POST", "/generate", self.payload(prompt="one mage, painted finish"))
            self.assertEqual(status, 200, job)
            master = await self.finish(job["job_id"])
            await asyncio.gather(*list(routes._tasks))
            await asyncio.sleep(0)
            self.assertIsNone(master["snapshot"]["style"])
            status, restored = await self.request("POST", "/compose", {"project_id": self.one["id"],
                "source_asset_id": master["id"], "prompt": "unrelated form", "config": {"model": "forged", "seed": "1"}})
            self.assertEqual(status, 200, restored)
            self.assertEqual(restored["prompt"], master["prompt"])
            self.assertEqual(restored["config"], master["config"])
            self.assertIsNone(restored["snapshot"]["style"])
            status, edited_job = await self.request("POST", "/generate", {"project_id": self.one["id"],
                "source_asset_id": master["id"], "mode": "expression", "expression_prompt": "happy eyes",
                "config": {"model": "forged", "seed": "1", "denoise": .4},
                "mask_base64": base64.b64encode(fixtures.png("white")).decode()})
            self.assertEqual(status, 200, edited_job)
            edited = await self.finish(edited_job["job_id"])
        self.assertIsNone(edited["snapshot"]["style"])
        self.assertEqual(edited["config"]["model"], master["config"]["model"])
        self.assertEqual(edited["config"]["seed"], master["config"]["seed"])
        self.assertEqual(edited["config"]["denoise"], .4)
        self.assertIn("one mage, painted finish", edited["prompt"])
        self.assertIn("happy eyes", edited["prompt"])
        self.assertEqual(self.store.get_generation(master["id"])["prompt"], master["prompt"])

    async def test_bootstrap_publishes_context_defaults_from_the_actual_templates(self):
        status, result = await self.request("GET", "/bootstrap")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["piece_test_templates"],
                         {kind: list(template) for kind, template in routes.composition.TEST_TEMPLATES.items()})

    async def test_draft_rejects_spoofed_snapshots_mixed_identity_and_malformed_controls(self):
        bad_tests = [
            {"draft": []}, {"draft": None}, {"draft": {"id": "forged"}},
            {"draft": {"generation_snapshot": {"config": {"model": "forged"}}}},
            {"draft": {"revision": 7}}, {"draft": {"kind": "outfit"}},
            {"draft": {"name": []}}, {"draft": {"prompt": []}},
            {"draft": {"negative": None}}, {"draft": {"seed": True}},
            {"draft": {"loras": [{"file": "../outside.safetensors"}]}},
            {"draft": {"loras": [{"file": "anima/paint.safetensors", "strength": 4}]}},
            {"draft": {"pose_control": {"enabled": True}}},
            {"draft": {"sample_asset_id": "missing"}},
            {"draft": {"master_revision": 1}},
            {"draft": {}, "id": "saved"}, {"context_prompt": []}, {"context_negative": None},
            {"token": []}, {"token": "x" * 121}, {"reuse_master_settings": "false"},
        ]
        for values in bad_tests:
            with self.subTest(values=values):
                status, result = await self.request("POST", "/compose", self.draft_payload(**values))
                self.assertEqual(status, 400, result)
        for values in ({"piece_ids": ["saved"]}, {"piece_revisions": {"saved": 1}},
                       {"source_asset_id": "saved"}):
            status, result = await self.request("POST", "/compose", {**self.draft_payload(), **values})
            self.assertEqual(status, 400, result)
        self.assertEqual(self.store.list("pieces"), [])

    async def test_master_and_sample_references_are_read_from_real_library_with_scope_and_revision_checks(self):
        master = self.store.save("pieces", {"kind": "character", "name": "Master", "prompt": "blue hair",
            "project_id": self.one["id"], "seed": "7"})
        self.store.save("pieces", {"id": master["id"], "prompt": "red hair"})
        payload = self.draft_payload(draft={"master_id": master["id"], "master_revision": 1})
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 200, result)
        self.assertIn("blue hair", result["prompt"])
        self.assertNotIn("red hair", result["prompt"])
        self.assertEqual(result["snapshot"]["pieces"][0]["master_revision"], 1)
        foreign = self.store.save("pieces", {"kind": "character", "name": "Foreign", "project_id": self.two["id"]})
        payload["piece_test"]["draft"] = {"master_id": foreign["id"]}
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 400, result)
        sample = self.store.save_generation({"project_id": self.two["id"], "category": "characters", "seed": "4"}, fixtures.png())
        payload["piece_test"]["draft"] = {"sample_asset_id": sample["id"]}
        status, result = await self.request("POST", "/compose", payload)
        self.assertEqual(status, 400, result)
        self.assertEqual(len(self.store.list("pieces")), 2)


if __name__ == "__main__":
    unittest.main()
