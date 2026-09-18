import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.runtime_env import build_runtime_env


class RuntimeEnvTests(unittest.TestCase):
    def test_defaults_keep_shared_paths_inside_project(self):
        with tempfile.TemporaryDirectory() as root:
            env = build_runtime_env({"UV_CACHE_DIR": "C:/foreign"}, root)

            cache = os.path.join(os.path.abspath(root), ".cache")
            self.assertEqual(env["UV_CACHE_DIR"], os.path.join(cache, "uv"))
            self.assertEqual(env["HF_HOME"], os.path.join(cache, "huggingface"))
            self.assertEqual(env["TORCH_HOME"], os.path.join(cache, "torch"))
            self.assertEqual(env["YOLO_CONFIG_DIR"], os.path.join(cache, "ultralytics"))
            self.assertEqual(env["UV_LINK_MODE"], "hardlink")
            self.assertTrue(os.path.isdir(env["HF_HUB_CACHE"]))

    def test_explicit_cache_root_is_honored(self):
        with tempfile.TemporaryDirectory() as root:
            custom = os.path.join(root, "shared-cache")
            env = build_runtime_env({"AI_HUB_CACHE_DIR": custom}, root)

            self.assertEqual(env["AI_HUB_CACHE_DIR"], os.path.abspath(custom))
            self.assertEqual(env["PIP_CACHE_DIR"], os.path.join(custom, "pip"))


if __name__ == "__main__":
    unittest.main()
