"""
AI Hub - CUDA Guardian
Maps GPU driver CUDA version to optimal PyTorch wheel selection.
Builds environment variable overrides for app installs/launches.
Verifies post-install CUDA correctness.
Zero external dependencies - stdlib only.
"""

import subprocess
import os


# PyTorch CUDA wheel mapping table
# Key: minimum driver CUDA version required
# Value: wheel tag, torch version, torchvision version, index URL
CUDA_WHEEL_MAP = [
    {
        "min_cuda": "13.0",
        "tag": "cu130",
        "torch": "2.10.0",
        "torchvision": "0.25.0",
        "torchaudio": "2.10.0",
        "index_url": "https://download.pytorch.org/whl/cu130",
    },
    {
        "min_cuda": "12.8",
        "tag": "cu128",
        "torch": "2.10.0",
        "torchvision": "0.25.0",
        "torchaudio": "2.10.0",
        "index_url": "https://download.pytorch.org/whl/cu128",
    },
    {
        "min_cuda": "12.6",
        "tag": "cu126",
        "torch": "2.6.0",
        "torchvision": "0.21.0",
        "torchaudio": "2.6.0",
        "index_url": "https://download.pytorch.org/whl/cu126",
    },
    {
        "min_cuda": "12.4",
        "tag": "cu124",
        "torch": "2.5.1",
        "torchvision": "0.20.1",
        "torchaudio": "2.5.1",
        "index_url": "https://download.pytorch.org/whl/cu124",
    },
    {
        "min_cuda": "12.1",
        "tag": "cu121",
        "torch": "2.5.1",
        "torchvision": "0.20.1",
        "torchaudio": "2.5.1",
        "index_url": "https://download.pytorch.org/whl/cu121",
    },
    {
        "min_cuda": "11.8",
        "tag": "cu118",
        "torch": "2.5.1",
        "torchvision": "0.20.1",
        "torchaudio": "2.5.1",
        "index_url": "https://download.pytorch.org/whl/cu118",
    },
]


def _version_gte(version_a: str, version_b: str) -> bool:
    """Compare two version strings (e.g., '13.1' >= '13.0')."""
    def parse(v):
        return tuple(int(x) for x in v.split("."))
    try:
        return parse(version_a) >= parse(version_b)
    except (ValueError, AttributeError):
        return False


def get_optimal_cuda(max_cuda: str) -> dict | None:
    """
    Given the driver's max CUDA version, return the best PyTorch wheel config.
    Returns None if no compatible version found.
    """
    if not max_cuda:
        return None

    for entry in CUDA_WHEEL_MAP:
        if _version_gte(max_cuda, entry["min_cuda"]):
            return {
                "tag": entry["tag"],
                "torch_version": entry["torch"],
                "torchvision_version": entry["torchvision"],
                "torchaudio_version": entry["torchaudio"],
                "index_url": entry["index_url"],
            }

    return None


def build_env_overrides(cuda_config: dict) -> dict:
    """
    Build environment variable overrides that force apps to use the correct
    CUDA version when installing PyTorch.
    """
    if not cuda_config:
        return {}

    tag = cuda_config["tag"]
    torch_ver = cuda_config["torch_version"]
    tv_ver = cuda_config["torchvision_version"]
    ta_ver = cuda_config.get("torchaudio_version", torch_ver)
    index_url = cuda_config["index_url"]

    torch_command = (
        f"pip install torch=={torch_ver}+{tag} "
        f"torchvision=={tv_ver}+{tag} "
        f"torchaudio=={ta_ver}+{tag} "
        f"--extra-index-url {index_url}"
    )

    return {
        "TORCH_INDEX_URL": index_url,
        "TORCH_COMMAND": torch_command,
        "PIP_EXTRA_INDEX_URL": index_url,
    }


def verify_app_cuda(python_path: str, expected_tag: str) -> dict:
    """
    Verify that an installed app's PyTorch has the correct CUDA version.
    
    Args:
        python_path: Path to the app's venv Python binary
        expected_tag: Expected CUDA tag (e.g., 'cu130')
    
    Returns:
        dict with verification results
    """
    result = {
        "verified": False,
        "torch_available": False,
        "cuda_available": False,
        "cuda_version": None,
        "torch_version": None,
        "expected_tag": expected_tag,
        "match": False,
        "error": None,
    }

    check_script = (
        "import torch; "
        "print(f'torch={torch.__version__}'); "
        "print(f'cuda_available={torch.cuda.is_available()}'); "
        "print(f'cuda_version={torch.version.cuda}')"
    )

    try:
        proc = subprocess.run(
            [python_path, "-c", check_script],
            capture_output=True, text=True, timeout=30
        )

        if proc.returncode != 0:
            result["error"] = proc.stderr.strip()
            return result

        result["torch_available"] = True

        for line in proc.stdout.strip().split("\n"):
            if line.startswith("torch="):
                result["torch_version"] = line.split("=", 1)[1]
            elif line.startswith("cuda_available="):
                result["cuda_available"] = line.split("=", 1)[1] == "True"
            elif line.startswith("cuda_version="):
                result["cuda_version"] = line.split("=", 1)[1]

        # Verify CUDA version matches expected tag
        if result["cuda_version"]:
            # e.g., cuda_version="13.0" should match tag="cu130"
            cuda_parts = result["cuda_version"].replace(".", "")
            # Handle cases like "13.0" -> "130" and tag "cu130"
            expected_cuda = expected_tag.replace("cu", "")
            result["match"] = cuda_parts.startswith(expected_cuda) or expected_cuda.startswith(cuda_parts)

        result["verified"] = result["cuda_available"] and result["match"]

    except FileNotFoundError:
        result["error"] = f"Python not found at: {python_path}"
    except subprocess.TimeoutExpired:
        result["error"] = "Verification timed out"

    return result


def get_cpu_fallback() -> dict:
    """Return CPU-only PyTorch configuration (no CUDA)."""
    return {
        "tag": "cpu",
        "torch_version": "2.10.0",
        "torchvision_version": "0.25.0",
        "torchaudio_version": "2.10.0",
        "index_url": "https://download.pytorch.org/whl/cpu",
    }


# === Self-test ===
if __name__ == "__main__":
    import json

    print("=" * 50)
    print("AI Hub - CUDA Guardian")
    print("=" * 50)

    # Test with various CUDA versions
    test_versions = ["13.1", "13.0", "12.8", "12.6", "12.4", "12.1", "11.8", "11.0", None]

    for ver in test_versions:
        config = get_optimal_cuda(ver)
        if config:
            print(f"\n  CUDA {ver} → {config['tag']} (torch {config['torch_version']})")
            env = build_env_overrides(config)
            print(f"    TORCH_INDEX_URL = {env['TORCH_INDEX_URL']}")
        else:
            tag = "cpu (fallback)" if ver else "no GPU"
            print(f"\n  CUDA {ver} → {tag}")

    # Try to detect from current system
    try:
        from gpu_detector import detect_gpu
        gpu = detect_gpu()
        if gpu["gpu_available"]:
            print(f"\n--- Current System ---")
            print(f"  GPU: {gpu['gpu_name']}")
            print(f"  Max CUDA: {gpu['max_cuda']}")
            config = get_optimal_cuda(gpu["max_cuda"])
            if config:
                print(f"  Optimal: {config['tag']} (torch {config['torch_version']})")
    except ImportError:
        pass
