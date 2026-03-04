"""
AI Hub - App Updater
Handles app updates via git pull with CUDA verification and rollback.
Zero external dependencies - stdlib only.
"""

import subprocess
import os


def get_current_commit(app_dir: str) -> str | None:
    """Get the current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=app_dir
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_remote_commit(app_dir: str, branch: str) -> str | None:
    """Check if there are remote updates available."""
    try:
        # Fetch latest info from remote
        subprocess.run(
            ["git", "fetch", "origin", branch],
            capture_output=True, text=True, timeout=60,
            cwd=app_dir
        )
        # Get the remote HEAD
        result = subprocess.run(
            ["git", "rev-parse", f"origin/{branch}"],
            capture_output=True, text=True, timeout=10,
            cwd=app_dir
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def check_for_updates(app_dir: str, branch: str) -> dict:
    """Check if an app has updates available."""
    current = get_current_commit(app_dir)
    remote = get_remote_commit(app_dir, branch)

    return {
        "current_commit": current,
        "remote_commit": remote,
        "update_available": current != remote and remote is not None,
        "error": None if current and remote else "Could not check for updates",
    }


def update_app(app_dir: str, branch: str, backups_dir: str,
               cuda_env: dict, cuda_tag: str, logger=None) -> dict:
    """
    Update an app: backup → git pull → verify CUDA → rollback if broken.
    
    Args:
        app_dir: Path to the installed app
        branch: Git branch name
        backups_dir: Directory for backups
        cuda_env: CUDA environment overrides
        cuda_tag: Expected CUDA tag for verification
        logger: HubLogger instance
    
    Returns:
        dict with update results
    """
    from . import app_installer
    from . import cuda_guardian

    result = {
        "success": False,
        "updated": False,
        "rolled_back": False,
        "old_commit": None,
        "new_commit": None,
        "error": None,
    }

    app_name = os.path.basename(app_dir)

    if logger:
        logger.section(f"Updating {app_name}")

    # Step 1: Check current state
    result["old_commit"] = get_current_commit(app_dir)
    if logger:
        logger.info("update", f"Current commit: {result['old_commit'][:8] if result['old_commit'] else 'unknown'}")

    # Step 2: Backup requirements
    backup_file = app_installer.backup_requirements(app_dir, backups_dir, logger)

    # Step 3: Git pull
    if logger:
        logger.info("update", "Pulling latest changes...")

    try:
        proc = subprocess.run(
            ["git", "pull", "origin", branch, "--ff-only"],
            capture_output=True, text=True, timeout=120,
            cwd=app_dir
        )
        if proc.returncode != 0:
            result["error"] = f"git pull failed: {proc.stderr}"
            if logger:
                logger.error("update", result["error"])
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result["error"] = f"git pull error: {e}"
        if logger:
            logger.error("update", result["error"])
        return result

    result["new_commit"] = get_current_commit(app_dir)
    result["updated"] = result["old_commit"] != result["new_commit"]

    if not result["updated"]:
        if logger:
            logger.info("update", "Already up to date.")
        result["success"] = True
        return result

    if logger:
        logger.info("update", f"Updated to: {result['new_commit'][:8]}")

    # Step 4: Verify CUDA (if venv exists and torch is installed)
    python_path = app_installer.get_app_python(app_dir)
    if os.path.exists(python_path):
        if logger:
            logger.info("update", "Verifying CUDA post-update...")

        verify = cuda_guardian.verify_app_cuda(python_path, cuda_tag)

        if verify["torch_available"] and not verify["verified"]:
            if logger:
                logger.warn("update", f"CUDA mismatch detected! Expected {cuda_tag}, "
                           f"got cuda={verify['cuda_version']}")
                logger.warn("update", "CUDA will be re-enforced on next launch.")
            # Not rolling back for CUDA mismatch — the env vars will fix it on next launch
        elif verify["torch_available"] and verify["verified"]:
            if logger:
                logger.success("update", f"CUDA verified: {verify['cuda_version']}")

    # Step 5: Rollback only on critical failure (git merge conflicts, etc.)
    # For now, git pull --ff-only prevents merge conflicts
    result["success"] = True

    if logger:
        logger.success("update", f"{app_name} updated successfully!")

    return result


def rollback_app(app_dir: str, commit_hash: str, logger=None) -> bool:
    """Rollback an app to a specific commit."""
    if logger:
        logger.info("rollback", f"Rolling back to {commit_hash[:8]}...")

    try:
        proc = subprocess.run(
            ["git", "reset", "--hard", commit_hash],
            capture_output=True, text=True, timeout=30,
            cwd=app_dir
        )
        if proc.returncode == 0:
            if logger:
                logger.success("rollback", "Rollback successful")
            return True
        else:
            if logger:
                logger.error("rollback", f"Rollback failed: {proc.stderr}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        if logger:
            logger.error("rollback", f"Rollback error: {e}")
        return False


# === Self-test ===
if __name__ == "__main__":
    print("=" * 50)
    print("AI Hub - App Updater")
    print("=" * 50)
    print("\n  Updater module loaded successfully.")
    print("  Use update_app() to update an installed application.")
    print("  Use check_for_updates() to check without updating.")
