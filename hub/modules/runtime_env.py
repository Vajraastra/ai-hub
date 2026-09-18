"""Shared runtime environment for the Hub and every managed application."""

import os


def project_root() -> str:
    """Return the AI Hub root from this module's stable location."""
    hub_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(hub_dir)


def build_runtime_env(base_env: dict | None = None,
                      root_dir: str | None = None,
                      create_dirs: bool = True) -> dict:
    """Build a portable environment whose caches live beside the project."""
    env = dict(os.environ if base_env is None else base_env)
    root_dir = os.path.abspath(root_dir or project_root())
    cache_dir = os.path.abspath(
        env.get("AI_HUB_CACHE_DIR") or os.path.join(root_dir, ".cache")
    )

    paths = {
        "AI_HUB_CACHE_DIR": cache_dir,
        "UV_CACHE_DIR": os.path.join(cache_dir, "uv"),
        "UV_PYTHON_INSTALL_DIR": os.path.join(root_dir, "hub", "tools", "python"),
        "PIP_CACHE_DIR": os.path.join(cache_dir, "pip"),
        "HF_HOME": os.path.join(cache_dir, "huggingface"),
        "HF_HUB_CACHE": os.path.join(cache_dir, "huggingface", "hub"),
        "HUGGINGFACE_HUB_CACHE": os.path.join(cache_dir, "huggingface", "hub"),
        "HF_DATASETS_CACHE": os.path.join(cache_dir, "huggingface", "datasets"),
        "HF_ASSETS_CACHE": os.path.join(cache_dir, "huggingface", "assets"),
        "TORCH_HOME": os.path.join(cache_dir, "torch"),
        # Ultralytics reads YOLO_CONFIG_DIR, despite the product name.
        "YOLO_CONFIG_DIR": os.path.join(cache_dir, "ultralytics"),
        "XDG_CACHE_HOME": cache_dir,
    }
    env.update(paths)
    env["UV_LINK_MODE"] = "hardlink"
    env["UV_PYTHON_PREFERENCE"] = "only-managed"

    if create_dirs:
        for path in dict.fromkeys(paths.values()):
            os.makedirs(path, exist_ok=True)

    return env
