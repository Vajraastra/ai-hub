"""CPU regression tests for Anima selection and expression continuity."""
import asyncio
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiohttp import WSMsgType
from PIL import Image, ImageChops, ImageDraw
from anima_studio.core.catalog import Catalog
from anima_studio.core.comfy_client import ComfyClient, ComfyError
from anima_studio.core.expressions import compose, png_bytes, prepare
from anima_studio.core.pipeline import (GenParams, PipelineError, build_workflow,
                                       generate, resolve_backend_names, resolve_seed,
                                       validate_params)


def safetensors(path, header):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + b"\0" * 8)


class CatalogueChecks(unittest.TestCase):
    def test_checkpoint_architecture_and_loader_identity_do_not_depend_on_name(self):
        temp_root = Path(__file__).resolve().parents[3] / ".cache" / "anima-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            # The container used by WAI Anima: its encoder and VAE live in
            # separate files despite the model residing under checkpoints/.
            anima = {"model.diffusion_model.llm_adapter.out_proj.weight": {"shape": [1024, 1024]},
                     "model.diffusion_model.blocks.0.self_attn.q_proj.weight": {"shape": [2048, 2048]}}
            safetensors(root / "checkpoints" / "unrelated-name.safetensors", anima)
            safetensors(root / "diffusion_models" / "checkpoints" / "unrelated-name.safetensors", anima)
            safetensors(root / "checkpoints" / "waiANIMA-false.safetensors", {
                "model.diffusion_model.input_blocks.0.weight": {"shape": [320, 4, 3, 3]}})
            safetensors(root / "Diffusers" / "converted.sft", {
                "llm_adapter.out_proj.weight": {"shape": [1024, 1024]},
                "blocks.0.self_attn.q_proj.weight": {"shape": [2048, 2048]}})
            result = Catalog(root).scan()["models"]
            entries = {entry["file"]: entry for entry in result}
            self.assertEqual(set(entries), {"checkpoints::unrelated-name.safetensors",
                                           "checkpoints/unrelated-name.safetensors", "converted.sft"})
            checkpoint = entries["checkpoints::unrelated-name.safetensors"]
            self.assertEqual(checkpoint["folder"], "checkpoints")
            self.assertEqual(checkpoint["backend_file"], "unrelated-name.safetensors")
            self.assertEqual(checkpoint["relative_path"], "checkpoints/unrelated-name.safetensors")
            self.assertEqual(Catalog._select(result, checkpoint["file"], "model"), checkpoint)
            with self.assertRaisesRegex(ValueError, "Ruta"):
                Catalog._select(result, "checkpoints::../unrelated-name.safetensors", "model")

    def test_only_anima_signatures_and_reject_untrusted_paths(self):
        temp_root = Path(__file__).resolve().parents[3] / ".cache" / "anima-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            anima = {"net.llm_adapter.out_proj.weight": {"shape": [1024, 1024]},
                     "net.blocks.0.self_attn.q_proj.weight": {"shape": [2048, 2048]}}
            safetensors(root / "diffusion_models" / "plain-name.safetensors", anima)
            safetensors(root / "diffusion_models" / "ANIMA.safetensors",
                        {"model.diffusion_model.input_blocks.0.weight": {"shape": [64, 4, 3, 3]}})
            safetensors(root / "loras" / "real.safetensors", {
                "lora_unet_blocks_0_self_attn_q_proj.lora_down.weight": {"shape": [16, 2048]}})
            safetensors(root / "loras" / "style-lokr.safetensors", {
                "lora_unet_blocks_0_self_attn_q_proj.lokr_w1": {"shape": [4, 4]},
                "lora_unet_blocks_0_self_attn_q_proj.lokr_w2": {"shape": [512, 512]}})
            safetensors(root / "loras" / "fake-anima.safetensors", {
                "__metadata__": {"ss_base_model_version": "anima"},
                "lora_unet_input_blocks_0.lora_down.weight": {"shape": [16, 320]}})
            safetensors(root / "clip" / "shared.safetensors", {
                "model.embed_tokens.weight": {"shape": [151936, 1024]},
                "model.layers.0.mlp.down_proj.weight": {"shape": [1024, 3072]},
                "model.layers.27.self_attn.q_proj.weight": {"shape": [2048, 1024]}})
            safetensors(root / "clip" / "broken-header.safetensors",
                        {"model.embed_tokens.weight": 1})
            safetensors(root / "vae" / "shared.safetensors", {
                "conv2.weight": {"shape": [16, 16, 1, 1, 1]},
                "encoder.conv1.weight": {"shape": [96, 3, 3, 3, 3]},
                "decoder.head.2.weight": {"shape": [3, 96, 3, 3, 3]}})
            catalogue = Catalog(root)
            result = catalogue.scan()
            self.assertEqual([entry["file"] for entry in result["models"]], ["plain-name.safetensors"])
            self.assertEqual([entry["file"] for entry in result["loras"]],
                             ["real.safetensors", "style-lokr.safetensors"])
            params = GenParams(model="plain-name.safetensors", text_encoder="shared.safetensors",
                               vae="shared.safetensors", loras=[{"file": "real.safetensors", "strength": .7}])
            self.assertEqual(catalogue.validate(params)["loras"][0]["strength"], .7)
            params.model = "../plain-name.safetensors"
            with self.assertRaisesRegex(ValueError, "Ruta"):
                catalogue.validate(params)
            params.model = "ANIMA.safetensors"
            with self.assertRaisesRegex(ValueError, "catálogo Anima"):
                catalogue.validate(params)

    def test_maximum_seed_and_graph_links_are_lossless(self):
        seed = "18446744073709551615"
        self.assertEqual(resolve_seed(seed), seed)
        params = GenParams(seed=seed, prompt='literal ["1", 0] and {{model}}',
                           loras=[{"file": "Anima\\test.safetensors", "strength": .5}])
        graph = build_workflow(params)
        self.assertEqual(graph["7"]["inputs"]["seed"], int(seed))
        self.assertEqual(graph["4"]["inputs"]["text"], params.prompt)
        self.assertEqual(graph["100"]["inputs"]["model"], ["1", 0])
        self.assertEqual(graph["7"]["inputs"]["model"], ["100", 0])
        self.assertEqual(graph["100"]["inputs"]["lora_name"], "Anima\\test.safetensors")

    def test_mode_dimensions_and_effective_txt2img_denoise(self):
        params = validate_params(GenParams(prompt="A gem", denoise=.2))
        self.assertEqual(params.denoise, 1.0)
        self.assertEqual(build_workflow(params)["7"]["inputs"]["denoise"], params.denoise)
        params = validate_params(GenParams(prompt="A smile", mode="expression", width=1000,
                                          height=1500, denoise=.65))
        self.assertEqual((params.width, params.height, params.denoise), (1000, 1500, .65))
        with self.assertRaisesRegex(ValueError, "múltiplo"):
            validate_params(GenParams(prompt="A gem", width=1000, height=1500))
        with self.assertRaisesRegex(ValueError, "4096"):
            validate_params(GenParams(prompt="A smile", mode="expression", width=4097, height=100))


class ExpressionChecks(unittest.TestCase):
    def test_full_rgba_unchanged_outside_final_mask_and_alpha_everywhere(self):
        master = Image.new("RGBA", (96, 128))
        master.putdata([(x % 256, y % 256, (x + y) % 256, (x * 3 + y) % 256)
                        for y in range(128) for x in range(96)])
        mask = Image.new("RGBA", master.size)
        ImageDraw.Draw(mask).ellipse((32, 16, 65, 50), fill="white")
        crop = prepare(png_bytes(master), png_bytes(mask), padding=12, feather=5)
        generated = Image.new("RGB", crop.render_size, (230, 30, 40))
        output = compose(crop, png_bytes(generated))
        result = Image.open(io.BytesIO(output["image"]))
        actual_mask = Image.open(io.BytesIO(output["mask"]))
        changed = 0
        for original, edited, weight in zip(master.getdata(), result.getdata(), actual_mask.getdata()):
            self.assertEqual(original[3], edited[3])
            if weight == 0:
                self.assertEqual(original, edited)
            elif original != edited:
                changed += 1
        self.assertGreater(changed, 100)
        self.assertTrue(output["details"]["outside_mask_identical"])
        self.assertEqual(result.size, master.size)

    def test_empty_or_wrong_size_mask_rejected_before_generation(self):
        master = png_bytes(Image.new("RGBA", (128, 128)))
        with self.assertRaisesRegex(ValueError, "tamaño"):
            prepare(master, png_bytes(Image.new("L", (64, 64))))
        with self.assertRaisesRegex(ValueError, "zona facial"):
            prepare(master, png_bytes(Image.new("L", (128, 128))))


class TransportChecks(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_names_and_external_encoder_vae_in_both_modes(self):
        class Client:
            async def get_models(self, folder):
                return {"checkpoints": ["community\\wai.safetensors"],
                        "text_encoders": ["anima\\encoder.safetensors"],
                        "vae": ["qwen\\vae.safetensors"], "loras": ["Anima\\style.safetensors"]}[folder]

        for mode in ("txt2img", "expression"):
            with self.subTest(mode=mode):
                params = await resolve_backend_names(Client(), GenParams(
                    model="checkpoints::community/wai.safetensors", text_encoder="anima/encoder.safetensors",
                    vae="qwen/vae.safetensors", mode=mode, loras=[{"file": "Anima/style.safetensors"}]))
                graph = build_workflow(params, image_name="master.png", mask_name="mask.png")
                self.assertEqual(params.model, "checkpoints::community\\wai.safetensors")
                self.assertEqual(graph["1"], {"class_type": "CheckpointLoaderSimple", "inputs": {
                    "ckpt_name": "community\\wai.safetensors"}})
                self.assertEqual(graph["100"]["inputs"]["model"], ["1", 0])
                self.assertEqual(graph["4"]["inputs"]["clip"], ["2", 0])
                self.assertEqual(graph["8"]["inputs"]["vae"], ["3", 0])
                self.assertEqual(graph["2"]["inputs"]["clip_name"], "anima\\encoder.safetensors")
                self.assertEqual(graph["3"]["inputs"]["vae_name"], "qwen\\vae.safetensors")
                if mode == "expression":
                    self.assertEqual(graph["13"]["inputs"]["vae"], ["3", 0])

    async def test_expression_keeps_free_canvas_and_metadata_without_resizing_master(self):
        class Client:
            def __init__(self):
                self.uploads = []

            async def get_models(self, folder):
                return {"diffusion_models": ["base.safetensors"],
                        "text_encoders": ["encoder.safetensors"],
                        "vae": ["vae.safetensors"], "loras": []}[folder]

            async def upload_image(self, data):
                self.uploads.append(data)
                return f"upload-{len(self.uploads)}.png"

            async def run_workflow(self, workflow, values, callback):
                with Image.open(io.BytesIO(self.uploads[0])) as crop:
                    self.render_size = crop.size
                return png_bytes(Image.new("RGB", self.render_size, "orange"))

        master = Image.new("RGBA", (1000, 1500), (20, 40, 60, 180))
        mask = Image.new("L", master.size)
        ImageDraw.Draw(mask).ellipse((400, 100, 600, 300), fill=255)
        # A caller's selected format must not replace an imported master's canvas.
        params = GenParams(model="base.safetensors", text_encoder="encoder.safetensors",
                           vae="vae.safetensors", mode="expression", width=1024, height=1536,
                           prompt="A smile", seed="42", denoise=.65)
        client = Client()
        result = await generate(client, params, master_bytes=png_bytes(master),
                                mask_bytes=png_bytes(mask))
        output = Image.open(io.BytesIO(result["image"]))
        self.assertEqual(output.size, master.size)
        self.assertEqual((result["metadata"]["params"]["width"],
                          result["metadata"]["params"]["height"]), master.size)
        self.assertTrue(all(dimension % 16 == 0 for dimension in client.render_size))
        self.assertEqual(output.getpixel((0, 0)), master.getpixel((0, 0)))
        self.assertEqual(result["metadata"]["params"]["denoise"], .65)

    async def test_ws_open_before_queue_and_cached_result_recovered(self):
        events = []
        image = png_bytes(Image.new("RGB", (128, 128), "green"))
        client = ComfyClient()

        class FakeWS:
            async def __aenter__(self):
                events.append("ws")
                return self

            async def __aexit__(self, *args):
                pass

            async def __aiter__(self):
                for kind, data in [("executed", {"prompt_id": "cached", "output": None}),
                                   ("execution_success", {"prompt_id": "cached"})]:
                    yield type("Message", (), {"type": WSMsgType.TEXT,
                                               "data": json.dumps({"type": kind, "data": data})})()

        class FakeSession:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def ws_connect(self, *args, **kwargs):
                return FakeWS()

        async def queue(workflow, client_id):
            self.assertEqual(events, ["ws"])
            events.append("queue")
            self.assertEqual(workflow["1"]["inputs"]["unet_name"], "anima\\base.safetensors")
            self.assertEqual(workflow["2"]["inputs"]["clip_name"], "anima\\qwen.safetensors")
            self.assertEqual(workflow["3"]["inputs"]["vae_name"], "shared\\qwen_vae.safetensors")
            self.assertEqual(workflow["100"]["inputs"]["lora_name"], "Anima\\style.safetensors")
            return "cached", client_id

        async def history(prompt_id):
            events.append("history")
            return {"images": [{"filename": "test.png"}]}

        async def download(*args):
            return image

        async def models(folder):
            return {"diffusion_models": ["anima\\base.safetensors"],
                    "text_encoders": ["anima\\qwen.safetensors"],
                    "vae": ["shared\\qwen_vae.safetensors"],
                    "loras": ["Anima\\style.safetensors"]}[folder]

        client.queue_prompt = queue
        client.history_output = history
        client.get_image_bytes = download
        client.get_models = models
        params = GenParams(model="anima/base.safetensors", text_encoder="anima/qwen.safetensors",
                           vae="shared/qwen_vae.safetensors", prompt="A green gem", width=128,
                           height=128, seed="18446744073709551615",
                           loras=[{"file": "Anima/style.safetensors", "strength": .5}])
        with patch("anima_studio.core.comfy_client.aiohttp.ClientSession", FakeSession):
            result = await generate(client, params)
        self.assertEqual(events, ["ws", "queue", "history"])
        self.assertEqual(result["image"], image)
        self.assertEqual(result["metadata"]["seed"], params.seed)
        self.assertEqual(result["metadata"]["params"]["model"], "anima\\base.safetensors")
        self.assertEqual(result["metadata"]["params"]["loras"][0]["file"], "Anima\\style.safetensors")

    async def test_stale_backend_filename_fails_before_queue(self):
        class Client:
            async def get_models(self, folder):
                return []

        with self.assertRaisesRegex(PipelineError, "ComfyUI no ofrece"):
            await resolve_backend_names(Client(), GenParams(model="missing.safetensors"))

    async def test_validation_error_retains_server_body(self):
        class Response:
            status = 400

            async def text(self):
                return '{"node_errors":{"1":"wrong model"}}'

        with self.assertRaisesRegex(ComfyError, "wrong model"):
            await ComfyClient._json(Response())


if __name__ == "__main__":
    unittest.main(verbosity=2)
