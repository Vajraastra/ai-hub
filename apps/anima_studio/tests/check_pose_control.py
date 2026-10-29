"""CPU checks for pose inheritance, safe maps, native graphs and installation."""
import asyncio
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from anima_studio.core import pose_control as pose
from anima_studio.core.pipeline import GenParams, build_workflow, generate, validate_params


def png(size=(128, 192), color="black"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def control(**kwargs):
    return {"enabled": True, "model_patch": "anima/anima-lllite-pose-1.safetensors",
            "image_base64": "data:image/png;base64," + base64.b64encode(png()).decode(),
            "strength": .8, "start_percent": .1, "end_percent": .85,
            "representation": "openpose", **kwargs}


def model_bytes():
    header = {"__metadata__": {"lllite.version": "2"},
              "lllite_conditioning1.conv1.weight": {"shape": [32, 3, 4, 4]},
              "lllite_dit_blocks_0_self_attn_q_proj.down.weight": {"shape": [32, 2048]}}
    encoded = json.dumps(header).encode()
    return struct.pack("<Q", len(encoded)) + encoded + b"\0" * 8


class PoseChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        models = copy.deepcopy(pose.MODELS)
        models["kohya-pose"].update(size_bytes=len(model_bytes()), sha256=hashlib.sha256(model_bytes()).hexdigest())
        self.models_patch = patch.object(pose, "MODELS", models)
        self.models_patch.start()

    def tearDown(self):
        self.models_patch.stop()
        self.directory.cleanup()

    def test_validation_preserves_exact_map_and_rejects_paths_and_bad_numbers(self):
        value = control()
        resolved = pose.validate_config(value)
        self.assertEqual(pose.image_bytes(resolved["image_base64"]), png())
        self.assertEqual(value, control())
        for filename in ("../escape.safetensors", "C:/escape.safetensors", "/escape.safetensors", "x//y.safetensors"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                pose.validate_config(control(model_patch=filename))
        for fields in ({"strength": float("nan")}, {"start_percent": .9, "end_percent": .1},
                       {"strength": True}, {"image_base64": "data:image/jpeg;base64,AAAA"},
                       {"image_base64": base64.b64encode(b"not png").decode()}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                pose.validate_config(control(**fields))
        self.assertIsNone(pose.validate_config({"enabled": False}))

    def test_model_header_and_representation_are_checked_before_queuing(self):
        target = self.root / "model_patches/anima/anima-lllite-pose-1.safetensors"
        target.parent.mkdir(parents=True)
        target.write_bytes(model_bytes())
        self.assertTrue(pose.catalog(self.root)["models"][0]["installed"])
        self.assertEqual(pose.validate_model_patch(self.root, control())["size_bytes"], len(model_bytes()))
        with self.assertRaisesRegex(ValueError, "OpenPose"):
            pose.validate_model_patch(self.root, control(representation="render"))
        target.write_bytes(b"not a model")
        self.assertFalse(pose.catalog(self.root)["models"][0]["installed"])

    def test_native_graph_preserves_lora_order_and_integer_seed(self):
        params = validate_params(GenParams(prompt="Person", seed="18446744073709551615",
            loras=[{"file": "style.safetensors", "strength": .5}, {"file": "hero.safetensors", "strength": .7}],
            pose_control=control()))
        graph = build_workflow(params, pose_image_name="pose.png")
        self.assertEqual(graph["101"]["inputs"]["model"], ["100", 0])
        self.assertEqual(graph["1021"]["inputs"]["model"], ["101", 0])
        self.assertEqual(graph["1021"]["inputs"]["model_patch"], ["1020", 0])
        self.assertEqual(graph["7"]["inputs"]["model"], ["1021", 0])
        self.assertEqual(graph["7"]["inputs"]["seed"], 18446744073709551615)
        self.assertNotIn("image_base64", json.dumps(graph))

    def test_raw_map_is_padded_without_cropping_and_joints_target_destination(self):
        imported = control(image_base64=base64.b64encode(png((200, 50), "red")).decode())
        image = Image.open(io.BytesIO(pose.render_map(imported, 128, 192)))
        self.assertEqual(image.size, (128, 192))
        self.assertEqual(image.getpixel((64, 0)), (0, 0, 0))
        self.assertEqual(image.getpixel((64, 96)), (255, 0, 0))
        skeleton = {"format": "openpose18", "points": [[.5, .5] for _ in range(18)]}
        derived = pose.validate_config(control(skeleton=skeleton))
        skeleton["points"][0] = [2, 2]
        image = Image.open(io.BytesIO(pose.render_map(derived, 256, 384)))
        self.assertNotEqual(image.getpixel((128, 192)), (0, 0, 0))

    def test_install_is_bounded_idempotent_and_cleans_own_partial_file(self):
        data = model_bytes()
        class Content:
            async def iter_chunked(self, size):
                yield data
        class Response:
            content_length = len(data)
            content = Content()
            def raise_for_status(self): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Session:
            def __init__(self, **kwargs):
                self.timeout = kwargs["timeout"]
                assert self.timeout.total == 180 and self.timeout.sock_read == 30
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, url): return Response()
        with patch.object(pose.aiohttp, "ClientSession", Session):
            installed = asyncio.run(pose.install(self.root, "kohya-pose"))
            self.assertEqual(installed["size_bytes"], len(data))
        with patch.object(pose.aiohttp, "ClientSession", side_effect=AssertionError("network")):
            self.assertTrue(asyncio.run(pose.install(self.root, "kohya-pose"))["existing"])
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_failed_download_does_not_publish_or_keep_a_partial_model(self):
        class Content:
            async def iter_chunked(self, size): yield b"truncated"
        class Response:
            content_length = 9
            content = Content()
            def raise_for_status(self): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Session:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, url): return Response()
        with patch.object(pose.aiohttp, "ClientSession", Session), self.assertRaisesRegex(ValueError, "no coincide"):
            asyncio.run(pose.install(self.root, "kohya-pose"))
        self.assertFalse(list(self.root.rglob("*.safetensors")))
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_generation_uploads_target_map_but_facial_crop_does_not_apply_body_pose(self):
        class Client:
            def __init__(self): self.uploads = []; self.graph = None
            async def get_models(self, folder):
                return {"diffusion_models": ["anima.safetensors"], "text_encoders": ["encoder.safetensors"],
                        "vae": ["vae.safetensors"], "loras": [],
                        "model_patches": ["anima\\anima-lllite-pose-1.safetensors"]}[folder]
            async def upload_image(self, data, name, **kwargs):
                self.uploads.append((name, data)); return name
            async def run_workflow(self, graph, values, on_progress, **kwargs):
                self.graph = graph
                if any(name == "master.png" for name, data in self.uploads):
                    return next(data for name, data in self.uploads if name == "master.png")
                return png((128, 192))
        params = GenParams(model="anima.safetensors", text_encoder="encoder.safetensors", vae="vae.safetensors",
                           prompt="Person", seed="42", width=128, height=192, pose_control=control())
        client = Client()
        result = asyncio.run(generate(client, params))
        self.assertEqual([name for name, _ in client.uploads], ["pose.png"])
        self.assertTrue(result["metadata"]["pose_control_applied"])
        self.assertEqual(client.graph["1020"]["inputs"]["name"], "anima\\anima-lllite-pose-1.safetensors")
        client = Client()
        params.mode = "expression"
        mask = Image.new("RGB", (128, 192), "black")
        ImageDraw.Draw(mask).rectangle((40, 20, 80, 50), fill="white")
        stream = io.BytesIO(); mask.save(stream, format="PNG")
        result = asyncio.run(generate(client, params, master_bytes=png(), mask_bytes=stream.getvalue()))
        self.assertEqual([name for name, _ in client.uploads], ["master.png", "mask.png"])
        self.assertFalse(result["metadata"]["pose_control_applied"])
        self.assertNotIn("1021", client.graph)
        self.assertTrue(result["metadata"]["params"]["pose_control"])


if __name__ == "__main__":
    unittest.main()
