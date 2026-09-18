import os
import json
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.python_runtime import apply_python_runtime_env, resolve_python_runtime
from modules.app_installer import _requirements_install_command
from modules.comfy_nodes import pip_cmd


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

    def test_overlay_install_excludes_shared_cuda_packages(self):
        with tempfile.TemporaryDirectory() as root:
            app_dir = os.path.join(root, "apps", "anima")
            runtime = resolve_python_runtime(
                {"shared_runtime": "py312-cu130", "python_layer": "overlay"},
                app_dir, root
            )
            cmd = _requirements_install_command(
                "uv", runtime, "requirements.txt", "shared.txt"
            )

            self.assertEqual(cmd[:5], [
                "uv", "pip", "install", "--python", runtime.python
            ])
            self.assertEqual(cmd[cmd.index("--target") + 1], runtime.overlay_dir)
            self.assertIn("--excludes", cmd)
            self.assertIn("unsafe-best-match", cmd)

    def test_base_install_targets_shared_venv_without_overlay_flags(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = resolve_python_runtime(
                {"shared_runtime": "py312-cu130", "python_layer": "base"},
                os.path.join(root, "apps", "comfyui"), root
            )
            cmd = _requirements_install_command(
                "uv", runtime, "requirements.txt", "shared.txt"
            )

            self.assertNotIn("--target", cmd)
            self.assertNotIn("--excludes", cmd)

    @unittest.skipUnless(os.name == "nt", "Windows-first path layout")
    def test_comfy_nodes_resolve_registered_shared_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            app_dir = os.path.join(root, "apps", "comfyui")
            config_dir = os.path.join(root, "hub", "config")
            runtime_python = os.path.join(
                root, "runtimes", "py312-cu130", "Scripts", "python.exe"
            )
            uv = os.path.join(root, "hub", "tools", "win", "uv.exe")
            os.makedirs(config_dir)
            os.makedirs(os.path.dirname(runtime_python))
            os.makedirs(os.path.dirname(uv))
            os.makedirs(app_dir)
            open(runtime_python, "wb").close()
            open(uv, "wb").close()
            with open(os.path.join(config_dir, "app_registry.json"), "w") as handle:
                json.dump({"apps": {"comfyui": {
                    "shared_runtime": "py312-cu130", "python_layer": "base"
                }}}, handle)

            self.assertEqual(pip_cmd(app_dir), [
                uv, "pip", "install", "--python", runtime_python
            ])


if __name__ == "__main__":
    unittest.main()
