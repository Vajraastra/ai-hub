"""CPU checks for replayable piece assembly and expression-only substitutions."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from anima_studio.core import composition


class CompositionChecks(unittest.TestCase):
    def setUp(self):
        self.project = {"id": "game", "name": "Game"}
        self.style = {"id": "paint", "revision": 2, "name": "Paint", "prompt": "painted",
                      "negative": "photo", "config": {"model": "base", "seed": "1"}}
        self.hero = {"id": "hero", "kind": "character", "revision": 3, "name": "Hero", "prompt": "blue eyes",
                     "loras": [{"file": "hero.safetensors", "strength": .7, "trigger": "myhero"}]}
        self.outfit = {"id": "dress", "kind": "outfit", "revision": 1, "name": "Dress", "prompt": "green dress"}
        self.sad = {"id": "sad", "kind": "expression", "revision": 1, "name": "Sad", "prompt": "sad",
                    "negative": "smiling"}

    def test_snapshot_is_independent_and_records_each_prompt_block(self):
        assembled = composition.assemble(self.project, self.style, [self.hero, self.outfit],
                                         {"model": "base"}, "full body", "cropped")
        self.hero["prompt"] = "red eyes"
        self.assertIn("blue eyes", assembled["prompt"])
        self.assertIn("myhero", assembled["prompt"])
        self.assertEqual(assembled["composition"]["blocks"][1]["revision"], 3)
        self.assertEqual(assembled["config"]["loras"], [{"file": "hero.safetensors", "strength": .7}])

    def test_same_lora_is_applied_once_and_conflicts_are_explicit(self):
        matching = [{"file": "HERO.safetensors", "strength": .7}]
        self.assertEqual(len(composition.merge_loras(matching, [self.hero])), 1)
        with self.assertRaisesRegex(ValueError, "pesos distintos"):
            composition.merge_loras([{"file": "hero.safetensors", "strength": .8}], [self.hero])
        self.hero["loras"][0]["enabled"] = False
        self.assertEqual(composition.merge_loras([], [self.hero]), [])

    def test_expression_replay_uses_saved_inputs_and_replaces_both_blocks(self):
        assembled = composition.assemble(self.project, self.style, [self.hero, self.outfit, self.sad],
            {"model": "base", "seed": "18446744073709551615", "steps": 31}, "full body", "cropped")
        source = {**assembled, "id": "master", "seed": "18446744073709551615"}
        happy = {"id": "happy", "revision": 4, "kind": "expression", "name": "Happy", "prompt": "happy",
                 "negative": "frowning"}
        before = copy.deepcopy(source)
        result = composition.frozen(source, expression=happy)
        self.assertEqual(result["config"]["seed"], "18446744073709551615")
        self.assertEqual(result["config"]["steps"], 31)
        self.assertNotIn("sad", result["prompt"])
        self.assertNotIn("smiling", result["negative"])
        self.assertIn("frowning", result["negative"])
        self.assertEqual(source, before)
        fixed_before = [b for b in source["blocks"] if b["kind"] != "expression"]
        fixed_after = [b for b in result["blocks"] if b["kind"] != "expression"]
        self.assertEqual(fixed_before, fixed_after)

    def test_legacy_generation_suffix_is_recovered_without_rewriting(self):
        source = composition.assemble(self.project, self.style, [self.outfit], {"model": "base"}, "full body")
        source.pop("composition")
        source.update(id="old", seed="99")
        result = composition.frozen(source, expression_prompt="happy")
        self.assertTrue(result["prompt"].endswith("happy\nfull body"))
        source["prompt"] = "unrecoverable edit"
        with self.assertRaisesRegex(ValueError, "recuperables"):
            composition.frozen(source, expression_prompt="happy")

    def test_outfit_test_is_an_explicit_mannequin_presentation(self):
        result = composition.assemble(self.project, self.style, [self.outfit], {"model": "base"},
                                      piece_test={"kind": "outfit", "id": "dress"})
        self.assertIn("neutral mannequin", result["prompt"])
        self.assertNotIn("blue eyes", result["prompt"])
        self.assertEqual(self.outfit["prompt"], "green dress")


if __name__ == "__main__":
    unittest.main()
