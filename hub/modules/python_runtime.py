"""Resolve portable Python runtimes shared by managed applications."""

from dataclasses import dataclass
import os
import re

from modules.runtime_env import project_root


_RUNTIME_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HIDDEN_PACKAGE_STUB = (
    "raise ModuleNotFoundError(\n"
    "    \"No module named '{package}' (hidden by AI Hub runtime isolation)\"\n"
    ")\n"
)


@dataclass(frozen=True)
class PythonRuntime:
    env_dir: str
    python: str
    bin_dir: str
    overlay_dir: str | None
    hidden_dir: str | None
    hidden_packages: tuple[str, ...]


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
    hidden_dir = None
    hidden_packages = tuple(app_config.get("python_hide_packages", []))

    if any(not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name)
           for name in hidden_packages):
        raise ValueError(f"Invalid hidden Python package list: {hidden_packages!r}")

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
        if hidden_packages:
            hidden_dir = os.path.join(os.path.abspath(app_dir), ".python-hidden")
    else:
        env_dir = os.path.join(os.path.abspath(app_dir), "venv")

    python, bin_dir = _python_paths(env_dir)
    return PythonRuntime(
        env_dir, python, bin_dir, overlay_dir, hidden_dir, hidden_packages
    )


def apply_python_runtime_env(env: dict, runtime: PythonRuntime) -> dict:
    """Point an app and all of its child processes at the resolved runtime."""
    env["VIRTUAL_ENV"] = runtime.env_dir
    env["PATH"] = runtime.bin_dir + os.pathsep + env.get("PATH", "")
    python_paths = []
    if runtime.hidden_dir:
        os.makedirs(runtime.hidden_dir, exist_ok=True)
        for package in runtime.hidden_packages:
            package_dir = os.path.join(runtime.hidden_dir, package)
            os.makedirs(package_dir, exist_ok=True)
            stub = os.path.join(package_dir, "__init__.py")
            content = _HIDDEN_PACKAGE_STUB.format(package=package)
            current_content = None
            if os.path.isfile(stub):
                with open(stub, encoding="utf-8") as handle:
                    current_content = handle.read()
            if current_content != content:
                with open(stub, "w", encoding="utf-8") as handle:
                    handle.write(content)
        python_paths.append(runtime.hidden_dir)
    if runtime.overlay_dir:
        python_paths.append(runtime.overlay_dir)
    current = env.get("PYTHONPATH", "")
    if current:
        python_paths.append(current)
    if python_paths:
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return env
