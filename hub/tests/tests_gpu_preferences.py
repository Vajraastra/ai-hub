import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HUB_DIR = Path(__file__).resolve().parents[1]
if str(HUB_DIR) not in sys.path:
    sys.path.insert(0, str(HUB_DIR))

from modules import gpu_detector
from modules.app_installer import (
    apply_post_install_patches,
    revert_post_install_patches,
)


class _Completed:
    returncode = 0
    stdout = (
        "0, NVIDIA GeForce RTX 3060, GPU-old, 00000000:04:00.0, 12288, 610.88\n"
        "1, NVIDIA GeForce RTX 5060 Ti, GPU-best, 00000000:08:00.0, 16311, 610.88\n"
    )


class GpuPreferenceTests(unittest.TestCase):
    @patch("modules.gpu_detector.subprocess.run", return_value=_Completed())
    def test_lists_all_gpus_with_stable_identity(self, _run):
        gpus = gpu_detector.list_nvidia_gpus()
        self.assertEqual([gpu["uuid"] for gpu in gpus], ["GPU-old", "GPU-best"])
        self.assertEqual(gpus[1]["index"], 1)
        self.assertEqual(gpus[1]["pci_bus_id"], "00000000:08:00.0")

    @patch("modules.gpu_detector.subprocess.run", return_value=_Completed())
    def test_general_profile_chooses_highest_vram_gpu(self, _run):
        selected = gpu_detector._parse_nvidia_smi()
        self.assertEqual(selected["gpu_name"], "NVIDIA GeForce RTX 5060 Ti")
        self.assertEqual(selected["vram_mb"], 16311)

    def test_source_patch_is_idempotent_and_reversible_for_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "training-ui" / "server.js"
            target.parent.mkdir()
            init_original = "    let accelerateFlags = '';"
            init_replacement = "    let accelerateFlags = `--num_processes 1 --mixed_precision ${mixedPrec}`;"
            original = "            accelerateFlags = `--mixed_precision ${mixedPrec}`;"
            replacement = "            accelerateFlags = `--num_processes 1 --mixed_precision ${mixedPrec}`;"
            target.write_text(init_original + "\n" + original, encoding="utf-8")
            config = {"post_install_patches": [{
                "file": "training-ui/server.js",
                "find": init_original,
                "replace": init_replacement,
                "critical": True,
            }, {
                "file": "training-ui/server.js",
                "find": original,
                "replace": replacement,
                "critical": True,
            }]}

            first = apply_post_install_patches(tmp, config)
            second = apply_post_install_patches(tmp, config)
            self.assertEqual(first["applied"], ["training-ui/server.js"] * 2)
            self.assertEqual(second["already_applied"], ["training-ui/server.js"] * 2)
            self.assertEqual(target.read_text(encoding="utf-8"), init_replacement + "\n" + replacement)

            self.assertEqual(revert_post_install_patches(tmp, config), ["training-ui/server.js"] * 2)
            self.assertEqual(target.read_text(encoding="utf-8"), init_original + "\n" + original)

    def test_critical_patch_fails_closed_if_upstream_context_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "server.js"
            target.write_text("upstream changed", encoding="utf-8")
            config = {"post_install_patches": [{
                "file": "server.js", "find": "old", "replace": "new", "critical": True,
            }]}
            result = apply_post_install_patches(tmp, config)
            self.assertTrue(result["failed"])


if __name__ == "__main__":
    unittest.main()
