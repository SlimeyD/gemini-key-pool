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
- **Tiered cooldowns** — per-minute (90s), per-day (1h), and quota (2h) cooldowns based on the actual error message from Google's API
- **Circuit breaker** — after 3 consecutive 429s, pauses before burning through remaining keys
- **Automatic model fallback** — when all keys for a model are exhausted, falls back through a chain (e.g., gemini-3-flash → gemini-2.5-flash → gemini-3.1-flash-lite)
- **Cooldown-nearest selection** — when all keys are on cooldown, picks the one expiring soonest instead of failing

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

# Image generation
python3 -m gemini_key_pool.gemini_agent --task "A sunset over mountains" --image-output sunset.png

# Image understanding
python3 -m gemini_key_pool.gemini_agent --task "Describe this image" --image-file photo.jpg --output description.md

# With Google Search and code execution
python3 -m gemini_key_pool.gemini_agent --task "What are the latest Gemini API changes?" --enable-tools --output research.md

# Override model selection
python3 -m gemini_key_pool.gemini_agent --task "Quick format this" --model gemini-3.1-flash --output formatted.md
```

### As a Python Library

```python
from gemini_key_pool import KeyPoolManager, run_gemini_task, select_model_for_task

# Direct API key management
manager = KeyPoolManager()
key_id = manager.select_key("gemini")       # LRU selection
api_key = manager.get_api_key(key_id)        # Resolves env: prefix
manager.update_usage(key_id, {"requests": 1})

# On 429 error:
manager.mark_key_rate_limited(key_id, error_message=str(error))
# Automatically detects RPM vs RPD vs quota and applies correct cooldown

# High-level task execution (handles rotation, fallback, everything)
result = run_gemini_task(
    task="Analyze this code for security issues",
    quality_level="production",
    output_file="/tmp/analysis.md"
)

# Model routing
routing = select_model_for_task("Summarize this document")
print(routing["model"])      # e.g., "gemini-3.1-flash"
print(routing["rationale"])  # e.g., "Gemini 3.1 Flash selected for draft quality | free tier"
```

### As a Claude Code Skill

Copy `skill/SKILL.md` into your Claude Code skills directory. Set `GEMINI_KEY_POOL_ROOT` to point to this repo. The skill teaches Claude Code how to delegate work to Gemini agents.

## How It Works

### Key Selection (LRU)

```
Keys: [A, B, C]  (all fresh)
Request 1 → A  (least recently used — never used = timestamp 0)
Request 2 → B
Request 3 → C
Request 4 → A  (least recently used again)
```

### Tiered Cooldowns

When a key hits a 429, the cooldown duration depends on the error type:

| Error Type | Cooldown | Why |
|-----------|----------|-----|
| RPM (requests per minute) | 90s | Rolling 60s window, 90s is safe |
| TPM (tokens per minute) | 90s | Same rolling window |
| IPM (images per minute) | 90s | Same rolling window |
| RPD (requests per day) | 1 hour | Resets at midnight PT |
| Quota (billing/plan) | 2 hours | Won't resolve soon |
| Unknown | 5 minutes | Conservative default |

The error type is auto-detected from Google's error message.

### Circuit Breaker

After 3 consecutive 429s:
- **Per-minute errors**: Wait 90s for the rolling window to reset, then continue
- **Per-day/quota errors**: Stop trying this model, fall back to the next one

### Model Fallback Chain

```
gemini-3.1-pro → gemini-3-flash → gemini-2.5-flash → gemini-3.1-flash-lite → (stop)
gemini-3-flash → gemini-2.5-flash → gemini-3.1-flash-lite → (stop)
```

Only triggered when ALL keys for a model are exhausted. Not triggered on individual key failures.

## Configuration

### keys.json

```json
{
  "providers": {
    "gemini": {
      "keys": [
        {"id": "project-1", "api_key": "env:GEMINI_API_KEY_1"},
        {"id": "project-2", "api_key": "env:GEMINI_API_KEY_2"},
        {"id": "project-3", "api_key": "AIza...literal-key-here"}
      ]
    }
  }
}
```

Keys support two formats:
- `"env:VARIABLE_NAME"` — reads from environment variable (recommended)
- `"AIza..."` — literal API key string (simpler but less secure)

### Quality Levels

The `--quality` flag controls model selection and thinking depth:

| Quality | Model | Thinking | Use For |
|---------|-------|----------|---------|
| `draft` | gemini-3.1-flash | minimal | Quick formatting, simple transforms |
| `standard` | gemini-3-flash | medium | Everyday analysis, code review |
| `production` | gemini-3-flash | high | Customer-facing output, complex code gen |
| `research` | gemini-3-flash | high | Deep analysis, architecture decisions |

## Testing

```bash
pip install pytest
pytest tests/ -v
```

25 tests covering LRU selection, cooldown-nearest fallback, history pruning, rate limit classification, tiered cooldowns, and cooldown expiry.

## Free Tier Rate Limits (March 2026)

| Model | RPM | RPD | With 3 Keys | With 18 Keys |
|-------|-----|-----|-------------|--------------|
| gemini-3.1-flash | 15 | 500 | 45 RPM / 1,500 RPD | 270 RPM / 9,000 RPD |
| gemini-3-flash | 10 | 1,000 | 30 RPM / 3,000 RPD | 180 RPM / 18,000 RPD |
| gemini-2.5-flash | 15 | 1,000 | 45 RPM / 3,000 RPD | 270 RPM / 18,000 RPD |
| gemini-2.5-pro | 10 | 1,000 | 30 RPM / 3,000 RPD | 180 RPM / 18,000 RPD |

## License

MIT
