# Creating Multiple GCP Projects for API Key Rotation

Each Gemini API key is tied to a GCP project. Free-tier rate limits are per-project, so more projects = more throughput. This guide walks through creating multiple projects and API keys.

## Why Multiple Projects?

Gemini's free tier has per-project rate limits:

| Model | RPM (per key) | RPD (per key) |
|-------|---------------|---------------|
| gemini-3.1-flash | 15 | 500 |
| gemini-3-flash | 10 | 1000 |
| gemini-2.5-flash | 15 | 1000 |
| gemini-2.5-pro | 10 | 1000 |

With 3 keys, you get 3x these limits. With 18 keys, you get 18x.

## Step-by-Step Setup

### 1. Create a GCP Project

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Click "Create API Key"
3. Select "Create API Key in new project" (or use an existing project)
4. Copy the generated key

### 2. Repeat for More Keys

Each API key needs its own GCP project for independent rate limits. Create as many projects as you need:

- **3 keys**: Good starting point, 3x throughput
- **6 keys**: Comfortable for moderate parallel workloads
- **18 keys**: Maximum throughput for heavy automation

### 3. Configure keys.json

Copy `keys.example.json` to `keys.json` and add your keys:

```json
{
  "providers": {
    "gemini": {
      "keys": [
        {
          "id": "project-1",
          "api_key": "env:GEMINI_API_KEY_1"
        },
        {
          "id": "project-2",
          "api_key": "env:GEMINI_API_KEY_2"
        },
        {
          "id": "project-3",
          "api_key": "env:GEMINI_API_KEY_3"
        }
      ]
    }
  }
}
```

### 4. Add Keys to .env

```bash
cp .env.example .env
# Edit .env with your actual keys:
GEMINI_API_KEY_1=AIza...
GEMINI_API_KEY_2=AIza...
GEMINI_API_KEY_3=AIza...
```

### 5. Verify Setup

```bash
python3 -m gemini_key_pool.gemini_agent --task "Hello, what model are you?" --output /tmp/test.md
```

## Security Notes

- **Never commit `.env` or `keys.json`** — both are in `.gitignore`
- The `env:VARIABLE_NAME` pattern in `keys.json` keeps actual keys out of the config file
- You can also use literal API keys directly in `keys.json` if you prefer (less secure but simpler for local use)

## Rate Limit Reset Schedule

- **Per-minute limits** (RPM, TPM, IPM): Rolling 60-second window
- **Per-day limits** (RPD): Resets at midnight Pacific Time
- **Quota limits**: Tied to billing — won't auto-reset

The key pool manager handles all of this automatically with tiered cooldowns.
