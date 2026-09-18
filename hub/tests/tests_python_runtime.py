import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.python_runtime import apply_python_runtime_env, resolve_python_runtime


class PythonRuntimeTests(unittest.TestCase):
    def test_legacy_app_keeps_its_own_venv(self):
        with tempfile.TemporaryDirectory() as root:
            app_dir = os.path.join(root, "apps", "legacy")
            runtime = resolve_python_runtime({}, app_dir, root)

            self.assertEqual(runtime.env_dir, os.path.join(app_dir, "venv"))
            self.assertIsNone(runtime.overlay_dir)

    def test_shared_base_uses_project_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            app_dir = os.path.join(root, "apps", "comfyui")
            config = {"shared_runtime": "py312-cu130", "python_layer": "base"}
            runtime = resolve_python_runtime(config, app_dir, root)

            self.assertEqual(
                runtime.env_dir, os.path.join(root, "runtimes", "py312-cu130")
            )
            self.assertIsNone(runtime.overlay_dir)

    def test_overlay_is_prepended_for_child_processes(self):
        with tempfile.TemporaryDirectory() as root:
            app_dir = os.path.join(root, "apps", "anima")
            config = {"shared_runtime": "py312-cu130", "python_layer": "overlay"}
            runtime = resolve_python_runtime(config, app_dir, root)
            env = apply_python_runtime_env({"PATH": "old", "PYTHONPATH": "existing"}, runtime)

            self.assertEqual(runtime.overlay_dir,
                             os.path.join(app_dir, ".python-packages"))
            self.assertEqual(env["VIRTUAL_ENV"], runtime.env_dir)
            self.assertTrue(env["PATH"].startswith(runtime.bin_dir + os.pathsep))
            self.assertEqual(
                env["PYTHONPATH"], runtime.overlay_dir + os.pathsep + "existing"
            )

    def test_runtime_id_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                resolve_python_runtime({"shared_runtime": "../outside"}, root, root)


if __name__ == "__main__":
    unittest.main()
