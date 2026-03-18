"""Tests for KeyPoolManager LRU selection and cooldown-nearest logic."""

import json
import sys
import time
from pathlib import Path

import pytest

# Ensure src/ is on the import path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gemini_key_pool.key_pool_manager import KeyPoolManager, parse_rate_limit_type, COOLDOWN_TIERS


@pytest.fixture
def pool_manager(tmp_path, monkeypatch):
    """Build a KeyPoolManager with a temporary keys.json and no .env loading."""
    keys_config = {
        "providers": {
            "gemini": {
                "keys": [
                    {"id": "key-a", "api_key": "env:GEMINI_KEY_A"},
                    {"id": "key-b", "api_key": "env:GEMINI_KEY_B"},
                    {"id": "key-c", "api_key": "env:GEMINI_KEY_C"},
                ]
            }
        }
    }
    config_file = tmp_path / "keys.json"
    config_file.write_text(json.dumps(keys_config))

    # Prevent .env loading side effects
    monkeypatch.setattr("gemini_key_pool.key_pool_manager.load_dotenv", lambda path=None: None)

    # Point usage file to tmp dir so nothing is written to real logs
    manager = KeyPoolManager(config_path=str(config_file))
    manager.usage_path = str(tmp_path / "key-usage.json")
    manager.usage = {}
    return manager


class TestLRUSelection:
    """LRU selection: all keys should be used before any repeats."""

    def test_all_keys_selected_before_repeat(self, pool_manager):
        """With no usage history, keys should be selected in a deterministic
        order and all keys visited before any key is reused. Usage is
        recorded between each selection (as the real caller does)."""
        seen = []
        for i in range(3):
            key = pool_manager.select_key("gemini")
            seen.append(key)
            # Simulate the caller recording usage after each selection
            pool_manager.usage.setdefault(key, {"total_requests": 0, "history": []})
            pool_manager.usage[key]["history"].append(
                {"timestamp": 1000 + i, "usage": {"requests": 1}}
            )
        # All three distinct keys should appear exactly once
        assert set(seen) == {"key-a", "key-b", "key-c"}

    def test_least_recently_used_preferred(self, pool_manager):
        """After recording usage, the least-recently-used key should be
        selected next."""
        # Simulate usage: key-c used most recently, then key-a, key-b longest ago
        pool_manager.usage = {
            "key-b": {
                "total_requests": 1,
                "history": [{"timestamp": 1000, "usage": {"requests": 1}}],
            },
            "key-a": {
                "total_requests": 1,
                "history": [{"timestamp": 2000, "usage": {"requests": 1}}],
            },
            "key-c": {
                "total_requests": 1,
                "history": [{"timestamp": 3000, "usage": {"requests": 1}}],
            },
        }
        selected = pool_manager.select_key("gemini")
        assert selected == "key-b"  # oldest timestamp -> selected first

    def test_never_used_key_selected_first(self, pool_manager):
        """A key with no usage history should be preferred over used keys."""
        pool_manager.usage = {
            "key-a": {
                "total_requests": 1,
                "history": [{"timestamp": 5000, "usage": {"requests": 1}}],
            },
            "key-c": {
                "total_requests": 1,
                "history": [{"timestamp": 6000, "usage": {"requests": 1}}],
            },
            # key-b has no entry -> never used
        }
        selected = pool_manager.select_key("gemini")
        assert selected == "key-b"


class TestCooldownNearest:
    """When all keys are on cooldown, select the one expiring soonest."""

    def test_nearest_cooldown_selected(self, pool_manager):
        """The key whose cooldown expires earliest should be picked."""
        now = time.time()
        pool_manager.usage = {
            "key-a": {
                "total_requests": 1,
                "history": [],
                "rate_limit_backoff": now + 300,  # 5 min from now
            },
            "key-b": {
                "total_requests": 1,
                "history": [],
                "rate_limit_backoff": now + 60,  # 1 min from now (soonest)
            },
            "key-c": {
                "total_requests": 1,
                "history": [],
                "rate_limit_backoff": now + 600,  # 10 min from now
            },
        }
        selected = pool_manager.select_key("gemini")
        assert selected == "key-b"

    def test_cooldown_not_used_when_keys_available(self, pool_manager):
        """If at least one key is off cooldown, cooldown logic should not
        be triggered - the available key is selected normally."""
        now = time.time()
        pool_manager.usage = {
            "key-a": {
                "total_requests": 1,
                "history": [{"timestamp": 1000, "usage": {"requests": 1}}],
                "rate_limit_backoff": now + 300,
            },
            "key-c": {
                "total_requests": 1,
                "history": [{"timestamp": 2000, "usage": {"requests": 1}}],
                "rate_limit_backoff": now + 600,
            },
            # key-b: no cooldown, no history -> available and never used
        }
        selected = pool_manager.select_key("gemini")
        assert selected == "key-b"


class TestHistoryPruning:
    """Usage history should be pruned to prevent unbounded growth."""

    def test_history_capped_at_100(self, pool_manager):
        """After 120 updates, history should contain at most 100 entries."""
        for i in range(120):
            pool_manager.update_usage("key-a", {"requests": 1})
        assert len(pool_manager.usage["key-a"]["history"]) <= 100

    def test_oldest_entries_pruned(self, pool_manager):
        """Pruning should keep the newest entries."""
        for i in range(120):
            pool_manager.update_usage("key-a", {"requests": 1, "index": i})
        history = pool_manager.usage["key-a"]["history"]
        # Oldest surviving entry should be index 20 (first 20 pruned)
        assert history[0]["usage"]["index"] == 20
        assert history[-1]["usage"]["index"] == 119


class TestParseRateLimitType:
    """parse_rate_limit_type should classify 429 errors into tiers."""

    def test_rpm_detection(self):
        msg = "429 RESOURCE_EXHAUSTED: RequestsPerMinute limit exceeded"
        assert parse_rate_limit_type(msg) == "rpm"

    def test_rpd_detection(self):
        msg = "429 RESOURCE_EXHAUSTED: RequestsPerDay limit exceeded"
        assert parse_rate_limit_type(msg) == "rpd"

    def test_tpm_detection(self):
        msg = "429 RESOURCE_EXHAUSTED: TokensPerMinute limit exceeded"
        assert parse_rate_limit_type(msg) == "tpm"

    def test_ipm_detection(self):
        msg = "429 RESOURCE_EXHAUSTED: ImagesPerMinute limit exceeded"
        assert parse_rate_limit_type(msg) == "ipm"

    def test_generic_quota_classified_as_rpd(self):
        """The common generic error message should be classified as rpd."""
        msg = "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details.'}}"
        assert parse_rate_limit_type(msg) == "rpd"

    def test_upgrade_classified_as_quota(self):
        msg = "429 RESOURCE_EXHAUSTED: Please upgrade your plan to continue."
        assert parse_rate_limit_type(msg) == "quota"

    def test_billing_enable_classified_as_quota(self):
        msg = "429 RESOURCE_EXHAUSTED: Please enable billing on your project."
        assert parse_rate_limit_type(msg) == "quota"

    def test_empty_message(self):
        assert parse_rate_limit_type("") == "unknown"

    def test_none_message(self):
        assert parse_rate_limit_type(None) == "unknown"

    def test_unrecognized_message(self):
        msg = "429 Something completely different happened"
        assert parse_rate_limit_type(msg) == "unknown"

    def test_case_insensitive(self):
        assert parse_rate_limit_type("requestsperday exceeded") == "rpd"
        assert parse_rate_limit_type("TOKENSPERMINUTE exceeded") == "tpm"


class TestTieredCooldowns:
    """mark_key_rate_limited should auto-detect cooldown from error message."""

    def test_auto_detect_rpm_cooldown(self, pool_manager):
        error = "429 RESOURCE_EXHAUSTED: RequestsPerMinute limit exceeded"
        pool_manager.mark_key_rate_limited("key-a", error_message=error)
        backoff = pool_manager.usage["key-a"]["rate_limit_backoff"]
        expected = time.time() + COOLDOWN_TIERS["rpm"]
        assert abs(backoff - expected) < 2  # within 2s tolerance

    def test_auto_detect_rpd_cooldown(self, pool_manager):
        error = "429 RESOURCE_EXHAUSTED: RequestsPerDay limit exceeded"
        pool_manager.mark_key_rate_limited("key-a", error_message=error)
        backoff = pool_manager.usage["key-a"]["rate_limit_backoff"]
        expected = time.time() + COOLDOWN_TIERS["rpd"]
        assert abs(backoff - expected) < 2

    def test_explicit_cooldown_overrides_auto(self, pool_manager):
        """When cooldown_seconds is passed explicitly, it takes precedence."""
        error = "429 RESOURCE_EXHAUSTED: RequestsPerMinute limit exceeded"
        pool_manager.mark_key_rate_limited("key-a", cooldown_seconds=999, error_message=error)
        backoff = pool_manager.usage["key-a"]["rate_limit_backoff"]
        expected = time.time() + 999
        assert abs(backoff - expected) < 2

    def test_no_args_defaults_to_unknown(self, pool_manager):
        """Calling with no error_message and no cooldown_seconds uses unknown tier."""
        pool_manager.mark_key_rate_limited("key-a")
        backoff = pool_manager.usage["key-a"]["rate_limit_backoff"]
        expected = time.time() + COOLDOWN_TIERS["unknown"]
        assert abs(backoff - expected) < 2

    def test_rate_limit_type_stored(self, pool_manager):
        error = "429 RESOURCE_EXHAUSTED: RequestsPerDay limit exceeded"
        pool_manager.mark_key_rate_limited("key-a", error_message=error)
        assert pool_manager.usage["key-a"]["rate_limit_type"] == "rpd"


class TestClearExpiredCooldowns:
    """clear_expired_cooldowns should remove stale backoffs."""

    def test_clears_expired(self, pool_manager):
        pool_manager.usage = {
            "key-a": {
                "total_requests": 1,
                "history": [],
                "rate_limit_backoff": time.time() - 10,  # expired 10s ago
                "rate_limit_type": "rpm",
            },
            "key-b": {
                "total_requests": 1,
                "history": [],
                "rate_limit_backoff": time.time() + 300,  # still active
            },
        }
        cleared = pool_manager.clear_expired_cooldowns()
        assert cleared == 1
        assert pool_manager.usage["key-a"]["rate_limit_backoff"] == 0
        assert "rate_limit_type" not in pool_manager.usage["key-a"]
        assert pool_manager.usage["key-b"]["rate_limit_backoff"] > 0

    def test_returns_zero_when_none_expired(self, pool_manager):
        pool_manager.usage = {
            "key-a": {
                "total_requests": 1,
                "history": [],
                "rate_limit_backoff": time.time() + 300,
            },
        }
        assert pool_manager.clear_expired_cooldowns() == 0
