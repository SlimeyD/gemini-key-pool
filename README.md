# Gemini Key Pool

**Gemini Key Pool** is a high-throughput management system for the Google Gemini API. It allows you to pool multiple API keys from different GCP projects to make the most of free-tier rate limits and provide a resilient, high-availability AI service.

---

## What It Does

Google's Gemini Free Tier is powerful but strictly limited (e.g., 500 requests per day for 3.1 Flash Lite). To scale beyond this, you need multiple projects and keys. **Gemini Key Pool** automates the orchestration of these keys by providing:

*   **Smart Key Rotation**: Uses Least-Recently-Used (LRU) selection to distribute load evenly across your pool.
*   **Fail-Safe Rate Limiting**: Automatically detects `429 RESOURCE_EXHAUSTED` errors and puts individual keys on tiered cooldowns (RPM, TPM, RPD).
*   **Model Fallback**: If your best model (e.g., Flash 3.0) is completely exhausted across all keys, the system automatically falls back to a high-quota alternative (e.g., Flash 3.1 Lite).
*   **Concurrency Safety**: Built for parallel execution. Atomic reservations (`reserve_key`) prevent multiple agents from "thundering herd" on the same key.
*   **Persistent Usage Tracking**: Remembers rate-limit states across restarts using a file-locked JSON database.

---

## Setup Guide

### 1. Prerequisites
*   Python 3.10+
*   Multiple Gemini API keys (create them at [Google AI Studio](https://aistudio.google.com/app/apikey))
  *    Please note, limits apply per project - you can create up to 8 projects with their own gemini API keys which can be added with their own names to your .env file. 

### 2. Installation
```bash
git clone https://github.com/SlimeyD/gemini-key-pool.git
cd gemini-key-pool
pip install -r requirements.txt
```

### 3. Configuration

#### `keys.json`
Define your keys in a `keys.json` file in the root directory. You can use literal keys or reference environment variables.

```json
{
  "providers": {
    "gemini": {
      "keys": [
        {"id": "account-1", "api_key": "env:GEMINI_KEY_1"},
        {"id": "account-2", "api_key": "env:GEMINI_KEY_2"},
        {"id": "account-3", "api_key": "AIzaSy...literal-key..."}
      ]
    }
  }
}
```

#### `.env` (Optional but Recommended)
If you used `env:` in your `keys.json`, add your keys to a `.env` file:
```bash
GEMINI_KEY_1=AIzaSy...
GEMINI_KEY_2=AIzaSy...
```

---

## Usage

### As a CLI Tool
The included `gemini_agent.py` is a powerful CLI for executing tasks:

```bash
# Basic text generation
python3 -m gemini_key_pool.gemini_agent --task "Summarize this log" --output result.md

# Image generation (uses 2.5 Flash Image)
python3 -m gemini_key_pool.gemini_agent --task "A blueprint of a spaceship" --image-output ship.png

# High-quality research (uses Pro features via Flash if on free tier)
python3 -m gemini_key_pool.gemini_agent --task "Analyze market trends" --quality research --enable-tools
```

### As a Python Library
Integrate the pool into your own applications:

```python
from gemini_key_pool import KeyPoolManager, run_gemini_task

# 1. Direct key management
manager = KeyPoolManager()
key_id = manager.reserve_key("gemini") # Atomic reservation for thread-safety
try:
    api_key = manager.get_api_key(key_id)
    # ... your logic here ...
    manager.update_usage(key_id, {"requests": 1})
except Exception as e:
    # If it was a rate limit error, block the key
    manager.mark_key_rate_limited(key_id, error_message=str(e))
finally:
    manager.release_key(key_id)

# 2. High-level execution (handles rotation and retries automatically)
result = run_gemini_task(
    task="Write a blog post about AI safety",
    quality_level="production"
)
print(result["output"])
```

---

## How It Works

### Tiered Cooldowns
The system parses Google's error messages to determine exactly how long to block a key:
*   **RPM (Per-Minute)**: 90 second cooldown.
*   **RPD (Per-Day)**: 1 hour cooldown (checked against Pacific Time resets).
*   **Quota (Billing)**: 2 hour cooldown.

### Model Fallback Chain
When a model is requested, the system attempts to fulfill it using the best available key. If the pool is empty for that model, it falls back:
`Gemini 3.1 Pro` → `Gemini 3 Flash` → `Gemini 2.5 Flash` → `Gemini 3.1 Flash Lite` → `Stop`

---

## March 2026 Free Tier Reference
The system is pre-configured with the latest verified limits:

| Model | RPM | TPM | RPD | Scaled (18 Keys) |
| :--- | :--- | :--- | :--- | :--- |
| **Gemini 3.1 Flash Lite** | 15 | 250K | 500 | 9,000 RPD |
| **Gemini 3 Flash** | 5 | 250K | 20 | 360 RPD |
| **Gemma 3 (1B-27B)** | 30 | 15K | 14.4K | 259K RPD |
| **Gemini 3.1 Pro** | 0* | 0* | 0* | Requires Paid Plan |

---

## Testing
Run the suite of 42 tests to verify rotation, cooldowns, and locking logic:
```bash
pytest tests/ -v
```

## License
MIT
