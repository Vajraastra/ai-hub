import os
import json
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.python_runtime import apply_python_runtime_env, resolve_python_runtime
from modules.app_installer import _requirements_install_command, _run_streamed
from modules.comfy_nodes import pip_cmd


class PythonRuntimeTests(unittest.TestCase):
    class _Logger:
        def __init__(self):
            self.warnings = []

        def info(self, _tag, _message):
            pass

        def warn(self, _tag, message):
            self.warnings.append(message)

    def test_download_runner_retries_after_forced_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            marker = os.path.join(root, "attempt.txt")
            orphan = os.path.join(root, "uv", ".tmp-incomplete")
            os.makedirs(orphan)
            code = (
                "from pathlib import Path; import time; "
                f"p=Path({marker!r}); "
                "exists=p.exists(); p.write_text('attempt', encoding='utf-8'); "
                "time.sleep(2) if not exists else None"
            )
            logger = self._Logger()
            started = time.monotonic()

            result = _run_streamed(
                [sys.executable, "-c", code],
                env={**os.environ, "UV_CACHE_DIR": os.path.join(root, "uv")},
                logger=logger, timeout=0.2, _timeout_retries=1
            )

            self.assertEqual(result, 0)
            self.assertEqual(len(logger.warnings), 1)
            self.assertFalse(os.path.exists(orphan))
            self.assertLess(time.monotonic() - started, 5)

    def test_download_runner_does_not_retry_success(self):
        logger = self._Logger()
        result = _run_streamed(
            [sys.executable, "-c", "print('ok')"],
            logger=logger, timeout=5
        )

        self.assertEqual(result, 0)
        self.assertEqual(logger.warnings, [])

    def test_pilot_apps_share_runtime_with_distinct_layers(self):
        registry_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "app_registry.json"
        )
        with open(registry_path, encoding="utf-8") as handle:
            apps = json.load(handle)["apps"]

        comfy = apps["comfyui"]
        anima = apps["anima-standalone-trainer"]
        self.assertEqual(comfy["shared_runtime"], anima["shared_runtime"])
        self.assertEqual(comfy["python_layer"], "base")
        self.assertEqual(anima["python_layer"], "overlay")
        self.assertTrue(anima["install_deps"])
        self.assertTrue(anima["pre_install_commands"])

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

    def test_comfy_nodes_accept_explicit_runtime_during_rebuild(self):
        cmd = pip_cmd(
            "E:/project/apps/comfyui",
            "E:/project/runtimes/staging/Scripts/python.exe",
            "E:/project/hub/tools/win/uv.exe"
        )

        self.assertEqual(cmd, [
            "E:/project/hub/tools/win/uv.exe", "pip", "install", "--python",
            "E:/project/runtimes/staging/Scripts/python.exe"
        ])


if __name__ == "__main__":
    unittest.main()
