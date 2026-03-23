import fcntl
import json
import os
import random
import sys
import time
from pathlib import Path

# Import package-aware path utilities
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
        # Use package-aware path resolution
        repo_root = get_root()

        # Load environment variables
        env_path = get_env_file()
        if env_path.exists():
            load_dotenv(env_path)
        else:
            # Try additional fallback locations
            for fallback in [Path(os.path.expanduser("~/.env")), 
                           Path(os.path.expanduser("~/SecondBrain/.env"))]:
                if fallback.exists():
                    load_dotenv(fallback)
                    break

        if not config_path:
            # Use package-aware keys config path
            config_path = get_keys_config()

        self.config_path = str(config_path)
        self.usage_path = str(get_logs_dir() / "key-usage.json")
        self.config = self._load_config()
        self.usage = self._load_usage()
        self._reserved_keys: set = set()

    def _load_config(self):
        with open(self.config_path, 'r') as f:
            return json.load(f)

    def _load_usage(self):
        if not os.path.exists(self.usage_path):
            return {}
        try:
            with open(self.usage_path, 'r') as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            return {}

    def _save_usage(self):
        os.makedirs(os.path.dirname(self.usage_path), exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        try:
            fd = os.open(self.usage_path, flags, 0o644)
            with os.fdopen(fd, 'r+') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    content = f.read()
                    disk_usage = json.loads(content) if content.strip() else {}
                    # Our in-memory state wins for keys we've touched
                    merged = {**disk_usage, **self.usage}
                    f.seek(0)
                    f.truncate()
                    json.dump(merged, f, indent=2)
                    self.usage = merged
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            print(f"⚠️  key-usage.json write failed: {e}", file=sys.stderr)

    def _last_used(self, key_id: str) -> float:
        """Return the timestamp of the most recent usage for a key (0 if never used)."""
        history = self.usage.get(key_id, {}).get("history", [])
        if not history:
            return 0
        return history[-1].get("timestamp", 0)

    def is_key_available(self, key_id: str, model: str = None) -> bool:
        """Return True if key is not on cooldown (globally or for the given model).

        Args:
            key_id: The key to check.
            model: If provided, also check for per-model cooldown on this model.
                   If None, only checks the global cooldown.
        """
        current_time = time.time()
        usage = self.usage.get(key_id, {})

        # Check global cooldown
        if current_time < usage.get("rate_limit_backoff", 0):
            return False

        # Check per-model cooldown
        if model:
            model_cooldowns = usage.get("model_cooldowns", {})
            if current_time < model_cooldowns.get(model, 0):
                return False

        return True

    def _is_available(self, key_id: str) -> bool:
        """Return True if key is not on cooldown (global) and not currently reserved."""
        if key_id in self._reserved_keys:
            return False
        return self.is_key_available(key_id)

    def select_key(self, provider):
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")

        provider_config = self.config["providers"][provider]
        keys = provider_config["keys"]

        if not keys:
            raise ValueError(f"No keys for {provider}")

        # Filter out keys on cooldown (rate limited)
        available_keys = [k for k in keys if self._is_available(k["id"])]

        # If all keys are on cooldown, pick the one nearest to cooldown expiry
        if not available_keys:
            print(f"⚠️  All {provider} keys are on cooldown, picking nearest to expiry")
            nearest = min(
                keys,
                key=lambda k: self.usage.get(k["id"], {}).get("rate_limit_backoff", 0),
            )
            return nearest["id"]

        # LRU selection: pick the key with the oldest last-usage timestamp
        # Keys never used (no history) get timestamp 0, so they're selected first
        selected = min(available_keys, key=lambda k: self._last_used(k["id"]))
        return selected["id"]

    def reserve_key(self, provider: str) -> str:
        """Atomically select a key and mark it as in-use.

        Parallel agents should call reserve_key() instead of select_key()
        to prevent multiple agents picking the same key simultaneously.
        Call release_key() when the request completes (success or failure).

        Raises RuntimeError if no keys are available (all reserved or on cooldown).
        """
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")

        all_keys = self.config["providers"][provider].get("keys", [])

        # Find available keys: not reserved and not on cooldown
        available = [k for k in all_keys if self._is_available(k["id"])]

        if not available:
            raise RuntimeError(
                f"No available keys for provider '{provider}' — all reserved or on cooldown"
            )

        # Apply LRU selection among available keys
        selected = min(available, key=lambda k: self._last_used(k["id"]))
        key_id = selected["id"]
        self._reserved_keys.add(key_id)
        return key_id

    def release_key(self, key_id: str) -> None:
        """Release a previously reserved key back to the pool."""
        self._reserved_keys.discard(key_id)

    def count_available(self, provider: str) -> int:
        """Return the number of keys not on cooldown and not currently reserved."""
        if provider not in self.config["providers"]:
            raise ValueError(f"Provider {provider} not found")

        return sum(
            1 for k in self.config["providers"][provider].get("keys", [])
            if self._is_available(k["id"])
        )

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

    def mark_key_rate_limited(self, key_id, cooldown_seconds=None, error_message=None, model=None):
        """Mark a key as rate limited, optionally scoped to a specific model.

        If *model* is provided, only that model is blocked on this key.
        If *model* is None, the key is blocked globally (all models).

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

        expiry = time.time() + cooldown_seconds
        limit_type = parse_rate_limit_type(error_message or "") if error_message else "unknown"

        if model:
            # Per-model cooldown
            if "model_cooldowns" not in self.usage[key_id]:
                self.usage[key_id]["model_cooldowns"] = {}
            self.usage[key_id]["model_cooldowns"][model] = expiry
        else:
            # Global cooldown (legacy + RPD where all models are affected)
            self.usage[key_id]["rate_limit_backoff"] = expiry

        self.usage[key_id]["rate_limit_type"] = limit_type
        self._save_usage()
        scope = f"model={model}" if model else "all models"
        print(f"🔒 Key {key_id} placed on cooldown for {cooldown_seconds}s ({scope})")

    def clear_expired_cooldowns(self):
        """Remove cooldowns that have already expired."""
        now = time.time()
        cleared = 0
        for key_id, data in self.usage.items():
            if data.get("rate_limit_backoff", 0) > 0 and now >= data["rate_limit_backoff"]:
                data["rate_limit_backoff"] = 0
                data.pop("rate_limit_type", None)
                cleared += 1

            # Also prune expired per-model cooldowns
            model_cooldowns = data.get("model_cooldowns", {})
            expired_models = [m for m, expiry in model_cooldowns.items() if now >= expiry]
            for m in expired_models:
                del model_cooldowns[m]
                cleared += 1
            if expired_models and not model_cooldowns:
                # Remove the empty dict to keep storage clean
                data.pop("model_cooldowns", None)
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
        elif command == "select-claude-key":
            print(manager.select_key("claude"))
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
