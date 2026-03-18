"""
Key Pool Path Utilities

Provides location-aware path resolution for gemini-key-pool.
Supports both in-repo use and exported skill use via environment variable.
"""
import os
from pathlib import Path


def get_root() -> Path:
    """
    Get the gemini-key-pool root directory.

    Supports three modes:
    1. Environment variable GEMINI_KEY_POOL_ROOT (for installed/exported use)
    2. Skill folder mode (SKILL.md in parent directory)
    3. Repo mode (src/ is a child of repo root)

    Returns:
        Path to root directory
    """
    # 1. Check environment variable
    if env_root := os.environ.get("GEMINI_KEY_POOL_ROOT"):
        return Path(env_root)

    # 2. Check if running from skill folder (SKILL.md in parent)
    script_dir = Path(__file__).parent
    if (script_dir.parent / "SKILL.md").exists():
        return script_dir.parent

    # 3. Default: assume running from src/gemini_key_pool/ inside repo
    return script_dir.parent.parent


def get_logs_dir() -> Path:
    """Get the logs directory (creates if needed)."""
    logs_dir = get_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_env_file() -> Path:
    """
    Get the .env file path.

    Checks multiple locations in order:
    1. Root directory
    2. Home directory
    """
    root = get_root()

    candidates = [
        root / ".env",
        Path(os.path.expanduser("~/.env")),
    ]

    for path in candidates:
        if path.exists():
            return path

    # Return default even if doesn't exist
    return root / ".env"


def get_keys_config() -> Path:
    """Get the keys.json configuration file path."""
    root = get_root()

    # Check multiple locations
    candidates = [
        root / "keys.json",
        root / "config" / "keys.json",
        root / "keys.example.json",
    ]

    for path in candidates:
        if path.exists():
            return path

    # Return default
    return root / "keys.json"


def get_model_capabilities() -> Path:
    """Get the model-capabilities.yaml file path."""
    root = get_root()

    candidates = [
        root / "config" / "model-capabilities.yaml",
    ]

    for path in candidates:
        if path.exists():
            return path

    return root / "config" / "model-capabilities.yaml"
