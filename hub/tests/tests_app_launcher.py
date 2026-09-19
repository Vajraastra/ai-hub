import os
import json
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.app_launcher import _sanitize_config_path_lists, _serialize_commandline_args


class AppLauncherTests(unittest.TestCase):
    def test_commandline_args_preserve_windows_paths_and_spaces(self):
        args = [
            "--ckpt-dirs",
            r"E:\Models\checkpoints",
            "--lora-dirs",
            r"E:\Model Store\loras",
        ]

        serialized = _serialize_commandline_args(args)

        self.assertEqual(shlex.split(serialized), args)

    def test_sanitize_config_path_lists_removes_only_missing_paths(self):
        with tempfile.TemporaryDirectory() as app_dir:
            valid_path = os.path.join(app_dir, "models")
            os.mkdir(valid_path)
            config_path = os.path.join(app_dir, "config.json")
            original = {
                "forge_additional_modules": [valid_path, r"Z:\missing\checkpoint.safetensors"],
                "forge_additional_modules_sd": [],
                "unrelated": [r"Z:\leave\this\alone"],
            }
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(original, f)

            _sanitize_config_path_lists(
                {"config_path_lists": ["forge_additional_modules", "forge_additional_modules_sd"]},
                app_dir,
            )

            with open(config_path, "r", encoding="utf-8") as f:
                sanitized = json.load(f)
            with open(config_path + ".aihub.bak", "r", encoding="utf-8") as f:
                backup = json.load(f)

            self.assertEqual(sanitized["forge_additional_modules"], [valid_path])
            self.assertEqual(sanitized["unrelated"], original["unrelated"])
            self.assertEqual(backup, original)


if __name__ == "__main__":
    unittest.main()
