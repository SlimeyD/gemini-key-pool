#!/usr/bin/env python3
"""
Gemini Agent - Executes tasks via Gemini API with key pool rotation.
Supports automatic model selection, image generation, image understanding, and usage tracking.

Usage:
    # Text tasks
    python3 -m gemini_key_pool.gemini_agent --task "Your task here" --output result.md
    python3 -m gemini_key_pool.gemini_agent --task "Complex analysis" --model gemini-2.5-pro --context-file context.md

    # Image generation
    python3 -m gemini_key_pool.gemini_agent --task "Generate an image of a sunset" --image-output sunset.png

    # Image understanding
    python3 -m gemini_key_pool.gemini_agent --task "Describe this image" --image-file photo.jpg --output description.md
"""
import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path
from datetime import datetime

# Add package directory for local imports
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

try:
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types as genai_types
    from google.genai import errors as genai_errors
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install google-genai python-dotenv pyyaml")
    sys.exit(1)

try:
    from .key_pool_manager import KeyPoolManager, parse_rate_limit_type, COOLDOWN_TIERS
    from .model_router import select_model_for_task
    from .paths import get_logs_dir, get_root
except ImportError:
    from key_pool_manager import KeyPoolManager, parse_rate_limit_type, COOLDOWN_TIERS
    from model_router import select_model_for_task
    from paths import get_logs_dir, get_root

# Circuit breaker: stop burning keys after this many consecutive 429s
CIRCUIT_BREAKER_THRESHOLD = 3
# Seconds to wait between key retries (with jitter) to avoid thundering herd
KEY_RETRY_DELAY_BASE = 1.0
KEY_RETRY_DELAY_JITTER = 1.0  # random 0-1s added

# Execution logging - uses location-aware path resolution
LOG_DIR = get_logs_dir()
EXECUTION_LOG = LOG_DIR / "executions.jsonl"

# Load environment once at module level
_env_loaded = False

def _ensure_env():
    global _env_loaded
    if _env_loaded:
        return
    env_paths = [
        get_root() / ".env",
        Path(os.path.expanduser("~/.env")),
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
    _env_loaded = True


def log_execution(task_summary: str, result: dict, task_type: str = "text",
                  requested_model: str = None, context_file: str = None,
                  quality_level: str = None):
    """Log execution details to JSONL file for auditing."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "task_summary": task_summary[:200] if task_summary else None,
            "task_type": task_type,
            "requested_model": requested_model,
            "model_used": result.get("model_used"),
            "key_id": result.get("key_id"),
            "quality_level": quality_level,
            "thinking_config": result.get("thinking_config"),
            "tools_used": result.get("tools_used", []),
            "system_prompt_used": result.get("system_prompt_used", False),
            "tokens_in": result.get("tokens_in"),
            "tokens_out": result.get("tokens_out"),
            "duration_ms": result.get("duration_ms"),
            "backend": result.get("backend", "python-api"),
            "thinking_tokens": result.get("thinking_tokens"),
            "success": result.get("success", False),
            "error": result.get("error"),
            "context_file": context_file,
            "output_file": result.get("output_file"),
            "image_path": result.get("image_path"),
        }

        with open(EXECUTION_LOG, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    except Exception as e:
        print(f"Warning: Failed to log execution: {e}")


def parse_context_template(template_path: str) -> dict:
    """
    Parse a YAML context template and extract all relevant information.

    Returns a dict with:
        - task: str (task description)
        - quality_level: str
        - model: str (if specified in routing)
        - thinking_config: dict (if specified)
        - tools_enabled: list
        - system_prompt: str (built from context)
        - context_content: str (built from files, decisions, etc.)
        - enable_tools: bool
    """
    with open(template_path, 'r') as f:
        ctx = yaml.safe_load(f)

    result = {
        "task": ctx.get("task", {}).get("description", ""),
        "quality_level": ctx.get("task", {}).get("quality_level", ""),
        "model": ctx.get("routing", {}).get("model", ""),
        "api_name": ctx.get("routing", {}).get("api_name", ""),
        "thinking_config": ctx.get("routing", {}).get("thinking_config", {}),
        "tools_enabled": ctx.get("routing", {}).get("tools_enabled", []),
        "enable_tools": bool(ctx.get("routing", {}).get("tools_enabled", [])),
    }

    # Build system prompt from quality level and task type
    task_type = ctx.get("task", {}).get("type", "")
    quality_level = result["quality_level"]

    system_parts = []
    if task_type:
        system_parts.append(f"You are performing a {task_type.replace('_', ' ')} task.")
    if quality_level:
        quality_guidance = {
            "draft": "Provide a quick, working response. Prioritize speed over perfection.",
            "standard": "Provide a thorough, well-reasoned response suitable for internal use.",
            "production": "Provide a polished, high-quality response suitable for customer-facing use. Consider edge cases.",
            "research": "Provide deep analysis with careful reasoning. Consider multiple perspectives and trade-offs."
        }
        system_parts.append(quality_guidance.get(quality_level, ""))

    # Add quality requirements
    requirements = ctx.get("quality", {}).get("specific_requirements", [])
    if requirements:
        system_parts.append("Requirements:")
        for req in requirements:
            system_parts.append(f"- {req}")

    # Add anti-patterns
    anti_patterns = ctx.get("quality", {}).get("anti_patterns", [])
    if anti_patterns:
        system_parts.append("Avoid:")
        for ap in anti_patterns:
            system_parts.append(f"- {ap}")

    result["system_prompt"] = "\n".join(system_parts)

    # Build context content from various sources
    context_parts = []

    # Add session decisions
    decisions = ctx.get("session", {}).get("decisions", [])
    if decisions:
        context_parts.append("## Previous Decisions\n")
        for d in decisions:
            context_parts.append(f"- **{d.get('decision', '')}**: {d.get('rationale', '')}")
        context_parts.append("")

    # Add constraints
    constraints = ctx.get("session", {}).get("constraints", [])
    if constraints:
        context_parts.append("## Constraints\n")
        for c in constraints:
            context_parts.append(f"- **{c.get('constraint', '')}**: {c.get('reason', '')}")
        context_parts.append("")

    # Add assumptions
    assumptions = ctx.get("session", {}).get("assumptions", [])
    if assumptions:
        context_parts.append("## Assumptions\n")
        for a in assumptions:
            validated = "Y" if a.get("validated", False) else "?"
            context_parts.append(f"- [{validated}] {a.get('assumption', '')}")
        context_parts.append("")

    # Add file contents (read primary files)
    primary_files = ctx.get("files", {}).get("primary", [])
    for f in primary_files:
        file_path = f.get("path", "")
        if file_path and os.path.exists(file_path) and f.get("read_full", True):
            context_parts.append(f"## File: {file_path}\n")
            if f.get("relevance"):
                context_parts.append(f"*Relevance: {f.get('relevance')}*\n")
            try:
                with open(file_path, 'r') as fp:
                    content = fp.read()
                ext = Path(file_path).suffix.lstrip('.')
                lang_map = {'rb': 'ruby', 'py': 'python', 'js': 'javascript', 'ts': 'typescript'}
                lang = lang_map.get(ext, ext)
                context_parts.append(f"```{lang}\n{content}\n```\n")
            except Exception as e:
                context_parts.append(f"*Could not read file: {e}*\n")

    # Add code snippets
    code_context = ctx.get("code_context", [])
    for snippet in code_context:
        desc = snippet.get("description", "Code snippet")
        lang = snippet.get("language", "")
        content = snippet.get("content", "")
        context_parts.append(f"## {desc}\n")
        context_parts.append(f"```{lang}\n{content}\n```\n")

    # Add documentation links
    docs = ctx.get("documentation", [])
    if docs:
        context_parts.append("## Documentation References\n")
        for doc in docs:
            context_parts.append(f"- [{doc.get('description', doc.get('url', ''))}]({doc.get('url', '')})")
        context_parts.append("")

    result["context_content"] = "\n".join(context_parts)

    return result


# Model name mappings (router names -> API names)
# Reference: https://ai.google.dev/gemini-api/docs/models
MODEL_MAP = {
    # Gemini 3.1 Family (Latest)
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3.1-pro-customtools": "gemini-3.1-pro-preview-customtools",
    "gemini-3.1-flash": "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash-image": "gemini-3.1-flash-image-preview",

    # Gemini 3 Family (Standard Flash/Pro)
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3-pro-image-preview": "gemini-3-pro-image-preview",

    # Gemini 2.5 Family (Stable)
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash-image": "gemini-2.5-flash-image",

    # Gemma 3 Family (Micro-tasks)
    "gemma-3-1b": "gemma-3-1b",
    "gemma-3-4b": "gemma-3-4b",
    "gemma-3-12b": "gemma-3-12b",
    "gemma-3-27b": "gemma-3-27b",

    # Embedding model
    "gemini-embedding-001": "gemini-embedding-001",
}

# Models that support image generation
IMAGE_GENERATION_MODELS = {
    "gemini-2.5-flash-image",
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
}

# Models available on free tier (Pro models have 0 RPD as of March 2026)
FREE_TIER_MODELS = {
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash",
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
}

# Fallback chain when all keys for a model are quota-exhausted.
# Skips Pro models (0 RPD on free tier). Flash -> Flash only.
# Only triggered when ALL keys return 429/RESOURCE_EXHAUSTED.
MODEL_FALLBACK = {
    # Pro models fall through to Flash immediately
    "gemini-3.1-pro-preview": "gemini-3-flash-preview",
    "gemini-3-pro-preview": "gemini-3-flash-preview",
    # Flash chain: most capable -> highest quota
    "gemini-3-flash-preview": "gemini-2.5-flash",
    "gemini-2.5-flash": "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash-lite-preview": None,
    "gemma-3-1b": "gemma-3-4b",
    "gemma-3-4b": "gemma-3-12b",
    "gemma-3-12b": "gemma-3-27b",
    "gemma-3-27b": "gemini-3.1-flash-lite-preview",
}

# Supported image MIME types for understanding
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".gif": "image/gif",
}


def run_gemini_task(task: str, model: str = None, context_file: str = None,
                    output_file: str = None, image_file: str = None,
                    image_output: str = None, max_retries: int = None,
                    quality_level: str = None, system_prompt: str = None,
                    enable_tools: bool = False,
                    capture_thinking: bool = False) -> dict:
    """
    Execute a task using Gemini API with key pool rotation and model fallback.
    Supports text generation, image understanding, and image generation.

    Args:
        task: The task/prompt to execute
        model: Model name (optional, auto-selected if not provided)
        context_file: Path to file with additional context (text)
        output_file: Path to write text result
        image_file: Path to image file for understanding tasks
        image_output: Path to save generated image
        max_retries: Number of keys to try before giving up
        quality_level: Quality tier (draft, standard, production, research)
        system_prompt: System instructions for the model
        enable_tools: Enable recommended Gemini tools (google_search, code_execution)
        capture_thinking: Capture and return the model's thinking trace

    Returns:
        dict with 'success', 'output', 'model_used', 'key_id', 'error', 'image_path', 'thinking_config', 'tools_used'
    """
    _ensure_env()

    # Initialize key pool
    key_manager = KeyPoolManager()

    # Determine task type
    is_image_generation = image_output is not None
    is_image_understanding = image_file is not None

    # Select model and get thinking configuration + tools
    thinking_config = None
    recommended_tools = []
    task_complexity = None

    if not model:
        routing = select_model_for_task(task, quality_level=quality_level)
        model = routing.get("model", "gemini-3-flash")
        thinking_config = routing.get("thinking_config")
        recommended_tools = routing.get("tools", [])
        task_complexity = routing.get("complexity")

        # Override for image generation tasks
        if is_image_generation:
            model = "gemini-2.5-flash-image"
            thinking_config = None
            recommended_tools = []
            print(f"Image generation task, using {model}")
        else:
            print(f"Auto-selected model: {model} (reason: {routing.get('rationale', 'default')})")
            if thinking_config:
                print(f"Thinking config: {thinking_config}")
            if recommended_tools and enable_tools:
                print(f"Recommended tools: {recommended_tools}")

    # Map model name to API model name
    api_model = MODEL_MAP.get(model, model)

    # Build content parts
    content_parts = []

    # Add image for understanding tasks
    if is_image_understanding and os.path.exists(image_file):
        image_path = Path(image_file)
        mime_type = IMAGE_MIME_TYPES.get(image_path.suffix.lower(), "image/jpeg")
        with open(image_file, 'rb') as f:
            image_bytes = f.read()
        content_parts.append(genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        print(f"Loaded image: {image_file} ({mime_type})")

    # Build structured prompt with context
    prompt_sections = []

    # Add context if provided
    if context_file and os.path.exists(context_file):
        with open(context_file, 'r') as f:
            context = f.read()
        prompt_sections.append(f"## Context\n\n{context}")

    # Add the main task
    prompt_sections.append(f"## Task\n\n{task}")

    # Add quality guidance based on quality level
    if quality_level:
        quality_guidance = {
            "draft": "Provide a quick, working response. Prioritize speed over perfection.",
            "standard": "Provide a thorough, well-reasoned response suitable for internal use.",
            "production": "Provide a polished, high-quality response suitable for customer-facing use. Consider edge cases.",
            "research": "Provide deep analysis with careful reasoning. Consider multiple perspectives and trade-offs."
        }
        if quality_level in quality_guidance:
            prompt_sections.append(f"## Quality Expectation\n\n{quality_guidance[quality_level]}")

    full_prompt = "\n\n---\n\n".join(prompt_sections)
    content_parts.append(full_prompt)

    # Build system instruction if provided or load default from config
    effective_system_prompt = system_prompt
    if not effective_system_prompt:
        # Try loading default system prompt from config
        default_prompt_path = get_root() / "config" / "system-prompt.md"
        if default_prompt_path.exists():
            try:
                effective_system_prompt = default_prompt_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
    if not effective_system_prompt and task_complexity:
        # Fallback: generate minimal system prompt based on complexity
        complexity_prompts = {
            "low": "You are a helpful assistant. Provide concise, direct answers.",
            "medium": "You are a knowledgeable assistant. Explain your reasoning clearly.",
            "high": "You are an expert assistant. Analyze thoroughly, consider edge cases, and provide detailed explanations.",
            "research": "You are a strategic advisor. Think deeply, consider multiple perspectives, and provide nuanced analysis with trade-offs."
        }
        effective_system_prompt = complexity_prompts.get(task_complexity)

    # Build full fallback chain (primary -> flash -> 2.5-flash -> ...)
    # Only traversed when ALL keys for a model are quota-exhausted.
    models_to_try = []
    _current = api_model
    while _current and _current not in models_to_try:
        models_to_try.append(_current)
        _current = MODEL_FALLBACK.get(_current)

    last_error = None

    for current_model in models_to_try:
        if current_model != api_model:
            print(f"Quota exhausted on all keys - falling back to {current_model}...")

        # Try keys until one works or we exhaust the pool
        tried_keys = set()
        attempt = 0
        consecutive_skips = 0
        consecutive_429s = 0  # Circuit breaker counter
        quota_hit = False
        max_consecutive_skips = 20

        # Get total number of keys available
        try:
            provider_config = key_manager.config.get("providers", {}).get("gemini", {})
            total_keys = len(provider_config.get("keys", []))
        except Exception:
            total_keys = 18  # Safe fallback

        # Try all keys unless explicitly capped
        effective_max_retries = max_retries if max_retries is not None else total_keys

        while attempt < effective_max_retries:
            # If we've tried all available keys, break
            if total_keys > 0 and len(tried_keys) >= total_keys:
                break

            # Select a key we haven't tried
            key_id = key_manager.select_key("gemini")
            if key_id in tried_keys:
                consecutive_skips += 1
                if consecutive_skips >= max_consecutive_skips:
                    break
                continue
            tried_keys.add(key_id)
            attempt += 1
            consecutive_skips = 0

            api_key = key_manager.get_api_key(key_id)
            if not api_key:
                print(f"Warning: Could not resolve key {key_id}")
                continue

            print(f"Attempt {attempt}: Using key {key_id} with {current_model}")

            try:
                client = genai.Client(api_key=api_key)

                # Build generation config with all options
                config_kwargs = {}

                # Add system instruction if available
                if effective_system_prompt:
                    config_kwargs["system_instruction"] = effective_system_prompt

                # Add tools if enabled and recommended
                tools_used = []
                if enable_tools and recommended_tools and not is_image_generation:
                    tool_mapping = {
                        "google_search": genai_types.Tool(google_search=genai_types.GoogleSearch()),
                        "code_execution": genai_types.Tool(code_execution=genai_types.ToolCodeExecution()),
                    }
                    for tool_name in recommended_tools:
                        if tool_name in tool_mapping:
                            if "tools" not in config_kwargs:
                                config_kwargs["tools"] = []
                            config_kwargs["tools"].append(tool_mapping[tool_name])
                            tools_used.append(tool_name)

                # Configure response modalities for image generation
                if is_image_generation:
                    config_kwargs["response_modalities"] = ["IMAGE", "TEXT"]

                # Configure thinking based on type
                if thinking_config and not is_image_generation:
                    thinking_type = thinking_config.get("type")
                    if thinking_type == "thinking_level":
                        level = thinking_config.get("level", "high")
                        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                            thinking_budget=-1 if level == "high" else
                                           8000 if level == "medium" else
                                           2000 if level == "low" else 0
                        )
                    elif thinking_type == "thinking_budget":
                        budget = thinking_config.get("budget", -1)
                        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                            thinking_budget=budget
                        )

                # Create config if we have any options
                generate_config = genai_types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

                _call_start = time.time()
                response = client.models.generate_content(
                    model=current_model,
                    contents=content_parts,
                    config=generate_config
                )

                output_text = None
                generated_image_path = None

                # Process response parts (guard against None candidates/parts)
                _candidates = response.candidates or []
                _parts = (_candidates[0].content.parts if _candidates and _candidates[0].content else None) or []
                for part in _parts:
                    if hasattr(part, 'text') and part.text:
                        output_text = part.text
                    elif hasattr(part, 'inline_data') and part.inline_data:
                        # Save generated image
                        if image_output:
                            img_path = Path(image_output)
                            img_path.parent.mkdir(parents=True, exist_ok=True)

                            raw_data = part.inline_data.data

                            if isinstance(raw_data, bytes):
                                img_data = raw_data
                            else:
                                try:
                                    img_data = base64.b64decode(raw_data)
                                except Exception as e:
                                    img_data = raw_data.encode('utf-8')

                            with open(img_path, 'wb') as f:
                                f.write(img_data)
                            generated_image_path = str(img_path)
                            print(f"Image saved to {generated_image_path}")

                # Fallback to response.text if no text part found
                if output_text is None:
                    try:
                        output_text = response.text
                    except Exception:
                        output_text = "Image generated successfully" if generated_image_path else "No text output"

                # Write text output if requested
                if output_file and output_text:
                    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_file, 'w') as f:
                        f.write(output_text)
                    print(f"Output written to {output_file}")

                # Extract token usage from response
                _tokens_in = None
                _tokens_out = None
                try:
                    if hasattr(response, 'usage_metadata') and response.usage_metadata:
                        _tokens_in = getattr(response.usage_metadata, 'prompt_token_count', None)
                        _tokens_out = getattr(response.usage_metadata, 'candidates_token_count', None)
                except Exception:
                    pass
                _duration_ms = int((time.time() - _call_start) * 1000)

                # Extract thinking trace if requested
                _thinking_trace = None
                _thinking_tokens = 0
                if capture_thinking and response.candidates and response.candidates[0].content.parts:
                    thinking_parts = []
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, 'thought') and part.thought:
                            thinking_parts.append(part.text)
                    if thinking_parts:
                        _thinking_trace = "\n".join(thinking_parts)
                        _thinking_tokens = getattr(response.usage_metadata, 'thoughts_token_count', 0) if response.usage_metadata else 0

                # Update usage
                key_manager.update_usage(key_id, {"requests": 1, "model": current_model})

                result = {
                    "success": True,
                    "output": output_text,
                    "model_used": current_model,
                    "key_id": key_id,
                    "error": None,
                    "image_path": generated_image_path,
                    "thinking_config": thinking_config,
                    "tools_used": tools_used,
                    "system_prompt_used": bool(effective_system_prompt),
                    "tokens_in": _tokens_in,
                    "tokens_out": _tokens_out,
                    "duration_ms": _duration_ms,
                    "backend": "python-api",
                }
                if _thinking_trace:
                    result["thinking_trace"] = _thinking_trace
                    result["thinking_tokens"] = _thinking_tokens
                return result

            except genai_errors.ClientError as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    # Auto-detect cooldown tier from error message
                    key_manager.mark_key_rate_limited(key_id, error_message=error_str)
                    limit_type = parse_rate_limit_type(error_str)

                    print(f"Key {key_id} rate-limited ({limit_type}), trying next key...")
                    last_error = f"Rate limited: {key_id}"
                    quota_hit = True
                    consecutive_429s += 1

                    # Circuit breaker: if N consecutive keys fail, likely a burst
                    if consecutive_429s >= CIRCUIT_BREAKER_THRESHOLD:
                        if limit_type in ("rpm", "tpm", "ipm", "unknown"):
                            wait = COOLDOWN_TIERS["rpm"]
                            print(f"Circuit breaker: {consecutive_429s} consecutive 429s. "
                                  f"Waiting {wait}s for per-minute limits to reset...")
                            time.sleep(wait)
                            consecutive_429s = 0
                        else:
                            print(f"Circuit breaker: {consecutive_429s} consecutive daily/quota 429s. "
                                  f"Falling back to next model.")
                            break

                    # Small delay + jitter between retries to avoid thundering herd
                    delay = KEY_RETRY_DELAY_BASE + random.uniform(0, KEY_RETRY_DELAY_JITTER)
                    time.sleep(delay)
                    continue
                else:
                    last_error = error_str
                    print(f"API error (no fallback): {error_str[:100]}")
                    break
            except Exception as e:
                last_error = str(e)
                print(f"Unexpected error (trying fallback): {last_error[:100]}")

        # Proceed to next model in fallback chain

    return {
        "success": False,
        "output": None,
        "model_used": api_model,
        "key_id": None,
        "error": last_error or "All keys and models exhausted",
        "image_path": None,
        "thinking_config": thinking_config,
        "tools_used": [],
        "system_prompt_used": False
    }


def main():
    parser = argparse.ArgumentParser(
        description="Execute tasks via Gemini API with key pool rotation. Supports text, image generation, and image understanding."
    )
    parser.add_argument("--task", required=True, help="Task/prompt to execute")
    parser.add_argument("--model", help="Model to use (auto-selected if not specified)")
    parser.add_argument("--context-file", help="File with additional text context")
    parser.add_argument("--output", help="Output file path for text results")
    parser.add_argument("--image-file", help="Input image file for understanding tasks")
    parser.add_argument("--image-output", help="Output path for generated images")
    parser.add_argument("--quality", choices=["draft", "standard", "production", "research"],
                       help="Quality level (affects model selection and thinking config)")
    parser.add_argument("--system-prompt", help="System instructions for the model")
    parser.add_argument("--enable-tools", action="store_true",
                       help="Enable recommended Gemini tools (google_search, code_execution)")
    parser.add_argument("--context-template", help="YAML context template file")
    parser.add_argument("--capture-thinking", action="store_true",
                       help="Capture and return the model's thinking trace if available")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")

    args = parser.parse_args()

    # Determine task type for display
    task_type = "text"
    if args.image_output:
        task_type = "image generation"
    elif args.image_file:
        task_type = "image understanding"

    # Handle context template if provided
    template_config = None
    if args.context_template:
        print(f"Loading context template: {args.context_template}")
        template_config = parse_context_template(args.context_template)

        task = args.task or template_config.get("task", "")
        model = args.model or template_config.get("model", None)
        quality = args.quality or template_config.get("quality_level", None)
        system_prompt = args.system_prompt or template_config.get("system_prompt", None)
        enable_tools = args.enable_tools or template_config.get("enable_tools", False)

        context_content = template_config.get("context_content", "")
        if context_content:
            task = f"{context_content}\n\n---\n\n## Task\n\n{task}"
            print(f"Context loaded: {len(context_content)} chars from template")
    else:
        task = args.task
        model = args.model
        quality = args.quality
        system_prompt = args.system_prompt
        enable_tools = args.enable_tools

    print(f"Gemini Agent starting ({task_type})...")
    print(f"Task: {task[:100]}{'...' if len(task) > 100 else ''}")
    if quality:
        print(f"Quality level: {quality}")
    if enable_tools:
        print(f"Gemini tools enabled: auto-detect from task")

    result = run_gemini_task(
        task=task,
        model=model,
        context_file=args.context_file,
        output_file=args.output,
        image_file=args.image_file,
        image_output=args.image_output,
        quality_level=quality,
        system_prompt=system_prompt,
        enable_tools=enable_tools,
        capture_thinking=args.capture_thinking
    )

    # Log execution for auditing
    log_execution(
        task_summary=args.task,
        result=result,
        task_type=task_type,
        requested_model=args.model,
        context_file=args.context_file,
        quality_level=args.quality
    )

    if args.json:
        print(json.dumps(result, indent=2))
    elif result["success"]:
        print(f"\nTask completed using {result['model_used']} (key: {result['key_id']})")
        if result.get("image_path"):
            print(f"Generated image: {result['image_path']}")
        if not args.output and not args.image_output:
            print(f"\n--- Output ---\n{result['output']}\n")
    else:
        print(f"\nTask failed: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
