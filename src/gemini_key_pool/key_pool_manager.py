import json
import os
import random
import time
from pathlib import Path

# Import location-aware path utilities
try:
    from .paths import get_root, get_env_file, get_keys_config, get_logs_dir
except ImportError:
    try:
        from paths import get_root, get_env_file, get_keys_config, get_logs_dir
    except ImportError:
        # Fallback if paths not available (standalone use)
        def get_root():
            return Path(__file__).parent.parent.parent
        def get_env_file():
            return get_root() / ".env"
        def get_keys_config():
            return get_root() / "keys.json"
        def get_logs_dir():
            return get_root() / "logs"

try:
    from dotenv import load_dotenv as _load_dotenv
    def load_dotenv(path=None):
        if path:
            _load_dotenv(path)
        else:
            _load_dotenv()
except ImportError:
    # Allow running without dotenv if package missing (fallback)
    def load_dotenv(path=None): pass

MAX_HISTORY_ENTRIES = 100

# Tiered cooldown durations based on rate limit type
COOLDOWN_TIERS = {
    "rpm": 90,      # Per-minute limit — rolling 60s window, 90s is safe
    "tpm": 90,      # Tokens per minute — same rolling window
    "ipm": 90,      # Images per minute — same rolling window
    "rpd": 3600,    # Per-day limit — resets at midnight Pacific, 1h is reasonable
    "quota": 7200,  # Billing/plan quota — won't resolve soon, 2h
    "unknown": 300,  # Can't determine type — conservative 5 min default
}


def parse_rate_limit_type(error_message: str) -> str:
    """Classify a 429 error into a rate limit tier based on the error message.

    Returns one of: "rpm", "tpm", "ipm", "rpd", "quota", "unknown".
    """
    if not error_message:
        return "unknown"
    msg = error_message.lower()

    # Check for explicit per-day indicators
    if "requestsperday" in msg or "per_day" in msg or "rpd" in msg:
        return "rpd"

    # Check for tokens-per-minute
    if "tokensperminute" in msg or "tpm" in msg or "tokens_per_minute" in msg:
        return "tpm"

    # Check for images-per-minute
    if "imagesperminute" in msg or "ipm" in msg or "images_per_minute" in msg:
        return "ipm"

    # Check for explicit per-minute indicators
    if "requestsperminute" in msg or "rpm" in msg or "per_minute" in msg:
        return "rpm"

    # Generic "exceeded your current quota" — treat as RPD since free-tier
    # daily limits are the most common cause of this message
    if "exceeded" in msg and "quota" in msg:
        return "rpd"

    # Check for billing/plan quota exhaustion (not a time-based reset)
    # This must come after the generic quota check above, since the common
    # "check your plan and billing details" message is a daily quota, not
    # a permanent billing block.
    if "upgrade" in msg or ("billing" in msg and "enable" in msg):
        return "quota"

    return "unknown"


class KeyPoolManager:
    def __init__(self, config_path=None):
        # Use location-aware path resolution
        repo_root = get_root()

        # Load environment variables
        env_path = get_env_file()
        if env_path.exists():
            load_dotenv(env_path)

        if not config_path:
            # Use location-aware keys config path
            config_path = get_keys_config()

        self.config_path = str(config_path)
        self.usage_path = str(get_logs_dir() / "key-usage.json")
        self.config = self._load_config()
        self.usage = self._load_usage()

    def _load_config(self):
        with open(self.config_path, 'r') as f:
            return json.load(f)

    def _load_usage(self):
        if os.path.exists(self.usage_path):
            try:
                with open(self.usage_path, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_usage(self):
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.usage_path), exist_ok=True)
        with open(self.usage_path, 'w') as f:
            json.dump(self.usage, f, indent=2)

    def select_key(self, provider):
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")

        provider_config = self.config["providers"][provider]
        keys = provider_config["keys"]

        if not keys:
            raise ValueError(f"No keys for {provider}")

        # Filter out keys on cooldown (rate limited)
        current_time = time.time()
        available_keys = []
        for k in keys:
            key_id = k["id"]
            cooldown_until = self.usage.get(key_id, {}).get("rate_limit_backoff", 0)
            if current_time >= cooldown_until:
                available_keys.append(k)

        # If all keys are on cooldown, pick the one nearest to cooldown expiry
        if not available_keys:
            print(f"Warning: All {provider} keys are on cooldown, picking nearest to expiry")
            nearest = min(
                keys,
                key=lambda k: self.usage.get(k["id"], {}).get("rate_limit_backoff", 0),
            )
            return nearest["id"]

        # LRU selection: pick the key with the oldest last-usage timestamp
        # Keys never used (no history) get timestamp 0, so they're selected first
        def last_used_timestamp(k):
            history = self.usage.get(k["id"], {}).get("history", [])
            if not history:
                return 0
            return history[-1].get("timestamp", 0)

        selected = min(available_keys, key=last_used_timestamp)
        return selected["id"]

    def get_api_key(self, key_id):
        # Flatten keys from all providers to find the matching ID
        all_keys = []
        for provider in self.config["providers"].values():
            all_keys.extend(provider["keys"])

        found_key = next((k for k in all_keys if k["id"] == key_id), None)
        if not found_key:
            raise ValueError(f"Key ID {key_id} not found")

        api_key_val = found_key["api_key"]
        if api_key_val.startswith("env:"):
            env_var = api_key_val.split(":", 1)[1]
            return os.environ.get(env_var, "")
        return api_key_val

    def mark_key_rate_limited(self, key_id, cooldown_seconds=None, error_message=None):
        """Mark a key as rate limited.

        If *error_message* is provided and *cooldown_seconds* is not, the
        cooldown duration is auto-detected from the error text using
        ``parse_rate_limit_type``.  When both are provided, *cooldown_seconds*
        takes precedence (backward compatible).
        """
        if cooldown_seconds is None:
            limit_type = parse_rate_limit_type(error_message or "")
            cooldown_seconds = COOLDOWN_TIERS[limit_type]

        if key_id not in self.usage:
            self.usage[key_id] = {"total_requests": 0, "history": []}

        self.usage[key_id]["rate_limit_backoff"] = time.time() + cooldown_seconds
        self.usage[key_id]["rate_limit_type"] = parse_rate_limit_type(error_message or "") if error_message else "unknown"
        self._save_usage()
        print(f"Key {key_id} placed on cooldown for {cooldown_seconds}s")

    def clear_expired_cooldowns(self):
        """Remove cooldowns that have already expired."""
        now = time.time()
        cleared = 0
        for key_id, data in self.usage.items():
            if data.get("rate_limit_backoff", 0) > 0 and now >= data["rate_limit_backoff"]:
                data["rate_limit_backoff"] = 0
                data.pop("rate_limit_type", None)
                cleared += 1
        if cleared:
            self._save_usage()
        return cleared

    def update_usage(self, key_id, usage_data):
        if key_id not in self.usage:
            self.usage[key_id] = {"total_requests": 0, "history": []}

        self.usage[key_id]["total_requests"] += usage_data.get("requests", 0)
        self.usage[key_id]["history"].append({
            "timestamp": time.time(),
            "usage": usage_data
        })

        # Prune old history entries to prevent unbounded growth
        if len(self.usage[key_id]["history"]) > MAX_HISTORY_ENTRIES:
            self.usage[key_id]["history"] = self.usage[key_id]["history"][-MAX_HISTORY_ENTRIES:]

        self._save_usage()

if __name__ == "__main__":
    import sys
    # Example usage: python3 key_pool_manager.py select-gemini-key
    manager = KeyPoolManager()
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "select-gemini-key":
            print(manager.select_key("gemini"))
        elif command == "get-api-key":
            # Usage: python3 key_pool_manager.py get-api-key <key_id>
            if len(sys.argv) < 3:
                print("Error: Missing key_id", file=sys.stderr)
                sys.exit(1)
            print(manager.get_api_key(sys.argv[2]))
        elif command == "update-usage":
            # Usage: python3 key_pool_manager.py update-usage <key_id> <requests_count>
            if len(sys.argv) < 3:
                print("Error: Missing key_id for update-usage", file=sys.stderr)
                sys.exit(1)
            key_id = sys.argv[2]
            requests = int(sys.argv[3]) if len(sys.argv) > 3 else 1
            manager.update_usage(key_id, {"requests": requests})
