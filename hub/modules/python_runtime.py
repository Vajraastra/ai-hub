"""Resolve portable Python runtimes shared by managed applications."""

from dataclasses import dataclass
import os
import re

from modules.runtime_env import project_root


_RUNTIME_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class PythonRuntime:
    env_dir: str
    python: str
    bin_dir: str
    overlay_dir: str | None


def _python_paths(env_dir: str) -> tuple[str, str]:
    if os.name == "nt":
        bin_dir = os.path.join(env_dir, "Scripts")
        return os.path.join(bin_dir, "python.exe"), bin_dir
    bin_dir = os.path.join(env_dir, "bin")
    return os.path.join(bin_dir, "python"), bin_dir


def resolve_python_runtime(app_config: dict, app_dir: str,
                           root_dir: str | None = None) -> PythonRuntime:
    """Resolve the legacy app venv or a named project-wide runtime."""
    runtime_id = app_config.get("shared_runtime")
    overlay_dir = None

    if runtime_id:
        if not isinstance(runtime_id, str) or not _RUNTIME_ID.fullmatch(runtime_id):
            raise ValueError(f"Invalid shared_runtime id: {runtime_id!r}")
        root = os.path.abspath(root_dir or project_root())
        env_dir = os.path.join(root, "runtimes", runtime_id)
        layer = app_config.get("python_layer", "base")
        if layer not in {"base", "overlay"}:
            raise ValueError(f"Invalid python_layer: {layer!r}")
        if layer == "overlay":
            overlay_dir = os.path.join(os.path.abspath(app_dir), ".python-packages")
    else:
        env_dir = os.path.join(os.path.abspath(app_dir), "venv")

    python, bin_dir = _python_paths(env_dir)
    return PythonRuntime(env_dir, python, bin_dir, overlay_dir)


def apply_python_runtime_env(env: dict, runtime: PythonRuntime) -> dict:
    """Point an app and all of its child processes at the resolved runtime."""
    env["VIRTUAL_ENV"] = runtime.env_dir
    env["PATH"] = runtime.bin_dir + os.pathsep + env.get("PATH", "")
    if runtime.overlay_dir:
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            runtime.overlay_dir + (os.pathsep + current if current else "")
        )
    return env
