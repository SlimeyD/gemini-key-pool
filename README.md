# gemini-key-pool

Smart API key rotation for the Gemini API. Solves the "cascade failure" problem where naive key rotation burns through all your free-tier keys in seconds.

## The Problem

Gemini's free tier has strict per-project rate limits (e.g., 15 RPM, 500 RPD). The obvious solution is multiple GCP projects with multiple API keys. But naive rotation (round-robin, random) fails catastrophically:

1. Key A hits 429 → rotate to Key B
2. Key B hits 429 → rotate to Key C
3. Key C hits 429 → rotate back to Key A (still on cooldown)
4. **All keys burned in seconds**

Existing tools use basic round-robin with no circuit breaker or tiered cooldowns. This project does it properly.

## The Solution

- **LRU key selection** — least-recently-used key is always tried first, ensuring even distribution
- **Atomic reservations** — `reserve_key()` and `release_key()` prevent multiple parallel agents from picking the same key simultaneously
- **Tiered cooldowns** — per-minute (90s), per-day (1h), and quota (2h) cooldowns based on the actual error message from Google's API
- **Per-model cooldowns** — blocks a key for a specific model (e.g., Flash) while keeping it available for others (e.g., Lite)
- **Circuit breaker** — after 3 consecutive 429s, pauses before burning through remaining keys
- **Automatic model fallback** — when all keys for a model are exhausted, falls back through a chain (e.g., gemini-3-flash → gemini-2.5-flash → gemini-3.1-flash-lite)
- **Cooldown-nearest selection** — when all keys are on cooldown, picks the one expiring soonest instead of failing
- **File-level locking** — uses `fcntl` to ensure `key-usage.json` integrity during high-concurrency parallel tasks

## Quick Start

### 1. Install

```bash
git clone https://github.com/SlimeyD/gemini-key-pool.git
cd gemini-key-pool
pip install -r requirements.txt
```

### 2. Add Your Keys

```bash
cp .env.example .env
cp keys.example.json keys.json
# Edit both files with your Gemini API keys
# See docs/creating-gcp-projects.md for how to create multiple keys
```

### 3. Run

```bash
# CLI
python3 -m gemini_key_pool.gemini_agent --task "Explain quantum computing in one paragraph" --output /tmp/result.md

# Python library
python3 -c "
from gemini_key_pool import KeyPoolManager
manager = KeyPoolManager()
key_id = manager.select_key('gemini')
api_key = manager.get_api_key(key_id)
print(f'Selected: {key_id}')
"
```

## Usage

### As a CLI Tool

```bash
# Simple text task
python3 -m gemini_key_pool.gemini_agent --task "Your prompt" --output result.md

# With quality level (affects model selection and thinking depth)
python3 -m gemini_key_pool.gemini_agent --task "Analyze this code" --quality production --context-file code.py --output analysis.md

# Image generation (uses gemini-2.5-flash-image)
python3 -m gemini_key_pool.gemini_agent --task "A sunset over mountains" --image-output sunset.png

# Image understanding
python3 -m gemini_key_pool.gemini_agent --task "Describe this image" --image-file photo.jpg --output description.md

# With Google Search and code execution
python3 -m gemini_key_pool.gemini_agent --task "What are the latest Gemini API changes?" --enable-tools --output research.md
```

### As a Python Library

```python
from gemini_key_pool import KeyPoolManager, run_gemini_task

# Direct API key management
manager = KeyPoolManager()

# Atomic reservation (best for parallel tasks)
key_id = manager.reserve_key("gemini")
try:
    api_key = manager.get_api_key(key_id)
    # ... use key ...
    manager.update_usage(key_id, {"requests": 1})
finally:
    manager.release_key(key_id)

# On 429 error (per-model cooldown):
manager.mark_key_rate_limited(key_id, error_message=str(error), model="gemini-3-flash")
```

## How It Works

### Atomic Reservations

In parallel environments, `select_key()` can lead to multiple agents picking the same key. `reserve_key()` marks the key as in-use in-memory, ensuring absolute distribution even when multiple requests fire at the same millisecond.

### Per-Model Cooldowns

A 429 on `gemini-3-pro` shouldn't block you from using `gemini-3-flash` on the same key. The pool tracks cooldowns per model-key pair, only applying global cooldowns when the entire project quota is hit (RPD).

### Tiered Cooldowns

| Error Type | Cooldown | Why |
|-----------|----------|-----|
| RPM (requests per minute) | 90s | Rolling 60s window, 90s is safe |
| TPM (tokens per minute) | 90s | Same rolling window |
| IPM (images per minute) | 90s | Same rolling window |
| RPD (requests per day) | 1 hour | Resets at midnight PT |
| Quota (billing/plan) | 2 hours | Won't resolve soon |
| Unknown | 5 minutes | Conservative default |

## Configuration

### Quality Levels

The `--quality` flag controls model selection and thinking depth:

| Quality | Model | Thinking | Use For |
|---------|-------|----------|---------|
| `draft` | gemini-3.1-flash | minimal | Quick formatting, simple transforms |
| `standard` | gemini-3-flash | medium | Everyday analysis, code review |
| `production` | gemini-3-flash | high | Customer-facing output, complex code gen |
| `research` | gemini-3-flash | high | Deep analysis, architecture decisions |

*Note: In 2026 free tier, Gemini Pro models have 0 RPD quota. The router automatically uses the most capable Flash model for production/research tasks.*

## Testing

```bash
pip install pytest
pytest tests/ -v
```

42 tests covering atomic reservations, per-model cooldowns, LRU selection, cooldown-nearest fallback, history pruning, rate limit classification, and file-level locking.

## Free Tier Rate Limits (March 2026)

Verified from Google AI Studio dashboard (2026-03-24):

| Model | RPM | TPM | RPD | With 3 Keys | With 18 Keys |
|-------|-----|-----|-----|-------------|--------------|
| **Gemini 3.1 Flash Lite** | 15 | 250K | 500 | 45 RPM / 1,500 RPD | 270 RPM / 9,000 RPD |
| **Gemini 3 Flash** | 5 | 250K | 20 | 15 RPM / 60 RPD | 90 RPM / 360 RPD |
| **Gemini 2.5 Flash** | 5 | 250K | 20 | 15 RPM / 60 RPD | 90 RPM / 360 RPD |
| **Gemini 2.5 Flash Lite** | 10 | 250K | 20 | 30 RPM / 60 RPD | 180 RPM / 360 RPD |
| **Gemma 3 (1B/4B/12B/27B)** | 30 | 15K | 14.4K | 90 RPM / 43.2K RPD | 540 RPM / 259K RPD |
| **Gemini 3.1 Pro** | 0* | 0* | 0* | 0 RPM / 0 RPD | 0 RPM / 0 RPD |

*\*Pro models require a pay-as-you-go plan or billing upgrade in 2026.*

## License

MIT
