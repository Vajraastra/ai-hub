import os
import sys
import tempfile
import unittest


HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

from modules.app_updater import _add_pip_overrides


class AppUpdaterTests(unittest.TestCase):
    def test_update_uses_registry_pip_overrides(self):
        with tempfile.TemporaryDirectory() as app_dir:
            cmd = ["uv", "pip", "install", "-r", "requirements.txt"]
            path = _add_pip_overrides(
                cmd,
                app_dir,
                {"pip_overrides": [
                    "numpy==2.4.4",
                    "torch=={torch_version}",
                ]},
                {"torch_version": "2.10.0"},
            )

            self.assertEqual(cmd[-2:], ["--override", path])
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(
                    handle.read(),
                    "numpy==2.4.4\ntorch==2.10.0\n",
                )


if __name__ == "__main__":
    unittest.main()
