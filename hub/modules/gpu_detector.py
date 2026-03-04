"""
AI Hub - GPU & Platform Detector
Detects GPU info, CUDA capability, OS platform, and container environment.
Zero external dependencies - stdlib only.
"""

import subprocess
import platform
import os
import re
import json


def detect_platform() -> dict:
    """Detect OS, container type, and filesystem mutability."""
    info = {
        "os": platform.system().lower(),  # 'linux', 'windows', 'darwin'
        "os_version": platform.version(),
        "arch": platform.machine(),
        "container": None,
        "mutable": True,
    }

    # Distrobox detection
    container_id = os.environ.get("CONTAINER_ID", "")
    if container_id:
        info["container"] = "distrobox"
        info["container_name"] = container_id
    elif os.path.exists("/.dockerenv"):
        info["container"] = "docker"
    elif os.path.exists("/run/.containerenv"):
        info["container"] = "podman"

    # Immutable filesystem detection (Linux only)
    if info["os"] == "linux":
        try:
            with open("/proc/mounts", "r") as f:
                mounts = f.read()
                # Check if /usr is read-only (immutable distros like Bazzite, NixOS)
                for line in mounts.splitlines():
                    parts = line.split()
                    if len(parts) >= 4 and parts[1] == "/usr":
                        info["mutable"] = "ro" not in parts[3].split(",")
                        break
        except (IOError, OSError):
            pass

    return info


def _parse_nvidia_smi() -> dict | None:
    """Parse nvidia-smi output for GPU information."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None

        line = result.stdout.strip().split("\n")[0]  # First GPU
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return None

        return {
            "gpu_name": parts[0],
            "vram_mb": int(float(parts[1])),
            "driver_version": parts[2],
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def _get_cuda_version_from_smi() -> str | None:
    """Get max CUDA version supported by the driver."""
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None

        # Parse "CUDA Version: 13.1" from nvidia-smi output
        match = re.search(r"CUDA Version:\s*(\d+\.\d+)", result.stdout)
        if match:
            return match.group(1)
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _detect_gpu_architecture(gpu_name: str) -> str:
    """Map GPU name to NVIDIA architecture."""
    name_upper = gpu_name.upper()

    # Architecture detection based on GPU naming patterns
    arch_patterns = {
        "blackwell": ["RTX 50", "RTX PRO 50", "GB"],
        "ada": ["RTX 40", "RTX PRO 40", "RTX 60", "AD", "L40", "L4 "],
        "hopper": ["H100", "H200", "GH"],
        "ampere": ["RTX 30", "RTX A", "A100", "A40", "A30", "A16", "A10", "GA"],
        "turing": ["RTX 20", "GTX 16", "TITAN RTX", "T4", "TU"],
        "volta": ["TITAN V", "V100", "GV"],
        "pascal": ["GTX 10", "GP", "P100", "P40", "TITAN X"],
    }

    for arch, patterns in arch_patterns.items():
        for pattern in patterns:
            if pattern in name_upper:
                return arch

    return "unknown"


def detect_gpu() -> dict:
    """
    Full GPU detection: GPU info + CUDA capability + platform.
    Returns a comprehensive dict with all system info needed by the hub.
    """
    platform_info = detect_platform()

    gpu_info = _parse_nvidia_smi()
    max_cuda = _get_cuda_version_from_smi()

    if gpu_info is None:
        return {
            "platform": platform_info,
            "gpu_available": False,
            "gpu_name": None,
            "vram_mb": 0,
            "driver_version": None,
            "max_cuda": None,
            "gpu_arch": None,
        }

    gpu_arch = _detect_gpu_architecture(gpu_info["gpu_name"])

    return {
        "platform": platform_info,
        "gpu_available": True,
        "gpu_name": gpu_info["gpu_name"],
        "vram_mb": gpu_info["vram_mb"],
        "driver_version": gpu_info["driver_version"],
        "max_cuda": max_cuda,
        "gpu_arch": gpu_arch,
    }


def check_prerequisites() -> dict:
    """Check that all required tools are available."""
    checks = {
        "python": {"available": True, "version": platform.python_version()},
        "git": {"available": False, "version": None},
        "nvidia_smi": {"available": False, "version": None},
        "uv": {"available": False, "version": None},
    }

    # Check git
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            checks["git"]["available"] = True
            checks["git"]["version"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            checks["nvidia_smi"]["available"] = True
            checks["nvidia_smi"]["version"] = result.stdout.strip().split("\n")[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check uv
    try:
        result = subprocess.run(
            ["uv", "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            checks["uv"]["available"] = True
            checks["uv"]["version"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return checks


# === Self-test ===
if __name__ == "__main__":
    print("=" * 50)
    print("AI Hub - GPU & Platform Detector")
    print("=" * 50)

    print("\n--- Prerequisites ---")
    prereqs = check_prerequisites()
    for tool, info in prereqs.items():
        status = "✓" if info["available"] else "✗"
        ver = f" ({info['version']})" if info["version"] else ""
        print(f"  {status} {tool}{ver}")

    print("\n--- System Info ---")
    gpu = detect_gpu()
    print(json.dumps(gpu, indent=2))
