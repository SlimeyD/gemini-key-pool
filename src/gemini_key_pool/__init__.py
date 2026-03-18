"""
gemini-key-pool: Smart API key rotation for the Gemini API.

Solves the "cascade failure" problem where naive key rotation burns through
all keys in seconds. Uses LRU selection, tiered cooldowns, circuit breakers,
and automatic model fallback.
"""

from .key_pool_manager import KeyPoolManager, parse_rate_limit_type, COOLDOWN_TIERS
from .model_router import select_model_for_task
from .gemini_agent import run_gemini_task

__all__ = [
    "KeyPoolManager",
    "parse_rate_limit_type",
    "COOLDOWN_TIERS",
    "select_model_for_task",
    "run_gemini_task",
]
