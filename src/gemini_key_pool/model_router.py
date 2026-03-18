#!/usr/bin/env python3
"""
Model Router - Intelligent model selection for Gemini API key pool.
Selects optimal Gemini model based on task complexity, type, quality requirements.

Loads configuration from config/model-capabilities.yaml to make routing decisions
based on quality tiers, thinking configurations, and model capabilities.
"""
import sys
import os
import json
import yaml
from pathlib import Path
from datetime import datetime

# Import location-aware path utilities
try:
    from .paths import get_model_capabilities
except ImportError:
    try:
        from paths import get_model_capabilities
    except ImportError:
        # Fallback if paths not available (standalone use)
        def get_model_capabilities():
            return Path(__file__).parent.parent.parent / "config" / "model-capabilities.yaml"

# Load model capabilities configuration
def load_model_config():
    """Load model capabilities from YAML configuration"""
    config_path = get_model_capabilities()
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    # Return minimal fallback config if file not found
    return {
        "models": {},
        "quality_tiers": {
            "draft": {"default_thinking": "minimal"},
            "standard": {"default_thinking": "medium"},
            "production": {"default_thinking": "high"},
            "research": {"default_thinking": "maximum"}
        },
        "routing_strategy": {
            "default_model": "gemini-3-flash",
            "quality_routing": {
                "draft": "gemini-3.1-flash",
                "standard": "gemini-3-flash",
                "production": "gemini-3-flash",
                "research": "gemini-3-flash"
            }
        }
    }

CONFIG = load_model_config()

# Path to the shared model performance matrix
MATRIX_FILE = Path(__file__).parent.parent.parent / "logs" / "model_matrix.json"


def check_model_matrix(task_type: str, complexity: str):
    """
    Check the model matrix for a data-driven model recommendation.

    Returns the matrix entry dict if confidence >= 0.7 and sample_size >= 5,
    otherwise None (fall back to static routing).
    """
    if not MATRIX_FILE.exists():
        return None

    try:
        with open(MATRIX_FILE, 'r') as f:
            matrix = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    key = f"{task_type}_{complexity}"
    entry = matrix.get(key)

    if entry and entry.get("confidence", 0) >= 0.7 and entry.get("sample_size", 0) >= 5:
        return entry

    return None


def update_model_matrix(task_type: str, complexity: str, model: str, score: float):
    """
    Update the model matrix with the performance score of a completed task.
    Recalculates running average, increments sample_size, updates confidence.
    """
    try:
        with open(MATRIX_FILE, 'r') as f:
            matrix = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        matrix = {}

    key = f"{task_type}_{complexity}"
    entry = matrix.get(key)

    if not entry:
        return

    current_avg = entry.get("avg_score", 0)
    current_size = entry.get("sample_size", 0)

    new_size = current_size + 1
    new_avg = ((current_avg * current_size) + score) / new_size

    entry["sample_size"] = new_size
    entry["avg_score"] = round(new_avg, 2)
    entry["confidence"] = min(new_size / 10.0, 1.0)
    entry["last_updated"] = datetime.utcnow().isoformat()

    matrix[key] = entry

    with open(MATRIX_FILE, 'w') as f:
        json.dump(matrix, f, indent=2)


def detect_task_type(task_description):
    """Detect specialized task types that require specific models"""
    task_lower = task_description.lower()

    # Intent Analysis / Routing - Force Flash for efficiency (Strategic Reflection Guardrail)
    intent_keywords = ["intent analysis", "route task", "classify intent", "determine model", "select model", "router"]
    if any(kw in task_lower for kw in intent_keywords):
        return "intent_analysis"

    # Image generation detection
    image_keywords = ["generate image", "create diagram", "design mockup", "draw", "visualize", "flowchart", "create visual"]
    if any(kw in task_lower for kw in image_keywords):
        # Professional quality vs speed
        quality_keywords = ["professional", "high-quality", "brand", "production", "marketing", "polished"]
        if any(kw in task_lower for kw in quality_keywords):
            return "image_generation_pro"
        return "image_generation_fast"

    # Embedding/semantic search detection
    embedding_keywords = ["semantic search", "find similar", "cluster", "embedding", "vector search", "rag", "retrieval"]
    if any(kw in task_lower for kw in embedding_keywords):
        return "embedding"

    # Research requiring current information
    research_keywords = ["latest", "recent", "current", "2026", "today", "news", "what happened"]
    if any(kw in task_lower for kw in research_keywords):
        return "research_current"

    # Micro-task detection (Gemma 3)
    micro_keywords = ["fix typo", "format this", "count words", "extract single", "is this correct", "convert to json"]
    if any(kw in task_lower for kw in micro_keywords) and len(task_description) < 500:
        return "micro_task"

    return "general"


def estimate_task_cost(task_description, model, model_config):
    """
    Estimate cost based on task length and model pricing.

    Returns estimated cost in dollars.
    """
    # Text model - estimate tokens
    # Rough estimate: 4 characters per token
    input_tokens = len(task_description) / 4

    # Assume 2x output tokens for most tasks
    output_tokens = input_tokens * 2

    input_cost = (input_tokens / 1000) * model_config.get("cost_per_1k_input", 0)
    output_cost = (output_tokens / 1000) * model_config.get("cost_per_1k_output", 0)

    return input_cost + output_cost

def assess_task_complexity(task_description, metadata=None):
    """
    Assess task complexity to route to appropriate model.
    Uses keyword matching from model config where available.

    Returns: "low", "medium", "high", or "research"
    """
    task_lower = task_description.lower()
    task_len = len(task_description)

    # RESEARCH complexity - novel/strategic tasks
    research_keywords = [
        "strategic", "novel", "creative", "groundbreaking", "innovative",
        "complex synthesis", "multi-document", "deep analysis", "research",
        "critical", "high-stakes"
    ]
    if any(kw in task_lower for kw in research_keywords):
        return "research"

    # HIGH complexity indicators
    high_keywords = [
        "refactor", "architect", "design system", "security audit",
        "optimize algorithm", "tradeoff analysis",
        "vulnerability", "threat model", "comprehensive", "in-depth",
        "multi-step", "complex", "analyze thoroughly", "review code",
        "debug", "investigate", "plan", "implement feature"
    ]
    if any(kw in task_lower for kw in high_keywords):
        return "high"

    # MEDIUM complexity indicators
    medium_keywords = [
        "review", "analyze", "explain", "compare", "document",
        "test", "validate", "check", "evaluate", "assess"
    ]
    if any(kw in task_lower for kw in medium_keywords):
        return "medium"

    # LOW complexity - simple, well-defined tasks
    low_keywords = [
        "format", "list", "summarize", "typo", "fix simple", "count", "extract",
        "what is", "how many", "hello", "say ", "translate", "convert",
        "define", "explain briefly", "in one sentence", "quick",
        "draft", "brainstorm", "iterate", "prototype"
    ]
    if any(kw in task_lower for kw in low_keywords):
        return "low"

    # Length-based heuristic for tasks without clear keywords
    if task_len < 50:
        return "low"

    # Default to medium for unclear tasks (prefer quality)
    return "medium"

def get_thinking_config(model_name, quality_level):
    """
    Get thinking configuration for a model at a given quality level.
    Returns the appropriate thinking_level or thinking_budget.
    """
    models = CONFIG.get("models", {})
    model_config = models.get(model_name, {})

    thinking = model_config.get("thinking", {})
    if not thinking.get("supported", False):
        return None

    quality_tiers = model_config.get("quality_tiers", {})
    tier_config = quality_tiers.get(quality_level, {})

    thinking_type = thinking.get("type")

    if thinking_type == "thinking_level":
        level = tier_config.get("thinking_level", thinking.get("default", "high"))
        return {"type": "thinking_level", "level": level}

    elif thinking_type == "thinking_budget":
        budget = tier_config.get("thinking_budget", thinking.get("default", -1))
        return {"type": "thinking_budget", "budget": budget}

    return None

def select_tools_for_task(task_description):
    """Recommend Gemini built-in tools based on task requirements"""
    task_lower = task_description.lower()
    tools = []

    if any(kw in task_lower for kw in ["search", "current", "recent", "latest", "fact check"]):
        tools.append("google_search")

    if any(kw in task_lower for kw in ["calculate", "math", "equation", "benchmark", "analyze data"]):
        tools.append("code_execution")

    if any(kw in task_lower for kw in ["location", "directions", "map", "place", "route"]):
        tools.append("google_maps")

    if "url" in task_lower or "website" in task_lower or "webpage" in task_lower:
        tools.append("url_context")

    return tools

def get_model_for_quality(quality_level, task_type="general"):
    """Get the recommended model for a quality level from routing strategy"""
    routing = CONFIG.get("routing_strategy", {})
    quality_routing = routing.get("quality_routing", {})

    # Handle special task types first
    if task_type == "micro_task":
        return "gemma-3-1b"
    if task_type == "image_generation_pro":
        return "gemini-3-pro-image-preview"
    if task_type == "image_generation_fast":
        return "gemini-3.1-flash-image"
    if task_type == "embedding":
        return "gemini-embedding-001"

    # Dynamic overrides for 2026 free tier (Pro models have 0 RPD on free tier)
    if quality_level == "draft":
        return "gemini-3.1-flash"  # Flash Lite (500 RPD) — high quota for drafts
    if quality_level == "standard":
        return "gemini-3-flash"    # Most capable free Flash (20 RPD)
    if quality_level == "production":
        return "gemini-3-flash"    # Most capable free Flash (Pro unavailable)

    # Default quality routing
    return quality_routing.get(quality_level, routing.get("default_model", "gemini-3.1-flash"))

def check_model_suitability(model_name, quality_level):
    """Check if a model is suitable for a quality level"""
    models = CONFIG.get("models", {})
    model_config = models.get(model_name, {})

    quality_tiers = model_config.get("quality_tiers", {})
    tier_config = quality_tiers.get(quality_level, {})

    return tier_config.get("suitable", True)

def get_fallback_model(model_name):
    """Get fallback model if primary model is unsuitable or unavailable"""
    routing = CONFIG.get("routing_strategy", {})
    fallback_chain = routing.get("fallback_chain", {})
    return fallback_chain.get(model_name)

def select_model_for_task(task_description, metadata=None, quality_level=None):
    """
    Select optimal Gemini model based on task characteristics and quality requirements.

    Args:
        task_description: Description of the task
        metadata: Optional metadata dict
        quality_level: Explicit quality level ("draft", "standard", "production", "research")
                      If not provided, inferred from task complexity

    Returns:
        Dict with provider, model, task_type, complexity, quality_level, thinking_config, tools, rationale
    """

    # 1. Detect specialized task types
    task_type = detect_task_type(task_description)

    # 2. Handle specialized types immediately

    # Strategic Guardrail: Enforce Flash for routing/intent analysis
    if task_type == "intent_analysis":
        model = "gemini-3-flash"
        return {
            "provider": "gemini",
            "model": model,
            "api_name": CONFIG.get("models", {}).get(model, {}).get("api_name", model),
            "task_type": "intent_analysis",
            "quality_level": "standard",
            "thinking_config": None,
            "tools": [],
            "requires_confirmation": False,
            "rationale": "Strategic Guardrail: Enforced Flash for high-efficiency intent analysis"
        }

    if task_type == "image_generation_pro":
        model = "gemini-3.1-flash-image"
        return {
            "provider": "gemini",
            "model": model,
            "api_name": CONFIG.get("models", {}).get(model, {}).get("api_name", model),
            "task_type": "image_generation",
            "quality_level": quality_level or "production",
            "thinking_config": get_thinking_config(model, quality_level or "production"),
            "tools": [],
            "requires_confirmation": False,
            "rationale": "Flash image generation (Pro image unavailable on free tier)"
        }

    if task_type == "image_generation_fast":
        model = "gemini-3.1-flash-image"
        return {
            "provider": "gemini",
            "model": model,
            "api_name": CONFIG.get("models", {}).get(model, {}).get("api_name", model),
            "task_type": "image_generation",
            "quality_level": quality_level or "draft",
            "thinking_config": get_thinking_config(model, quality_level or "draft"),
            "tools": [],
            "requires_confirmation": False,
            "rationale": "Fast image generation (3.1 Flash Image)"
        }

    if task_type == "embedding":
        model = "gemini-embedding-001"
        return {
            "provider": "gemini",
            "model": model,
            "api_name": CONFIG.get("models", {}).get(model, {}).get("api_name", model),
            "task_type": "embedding",
            "quality_level": "standard",
            "output_dimensions": 768,
            "thinking_config": None,
            "tools": [],
            "requires_confirmation": False,
            "rationale": "Semantic search and clustering via embeddings"
        }

    if task_type == "micro_task":
        model = "gemma-3-1b"
        return {
            "provider": "gemini",
            "model": model,
            "api_name": CONFIG.get("models", {}).get(model, {}).get("api_name", model),
            "task_type": "micro_task",
            "complexity": "low",
            "quality_level": "draft",
            "thinking_config": None,
            "tools": [],
            "requires_confirmation": False,
            "rationale": "Gemma 3 1B: Optimized for high-volume, ultra-low complexity micro-tasks"
        }

    # 3. Assess complexity if quality_level not provided
    complexity = assess_task_complexity(task_description, metadata)

    if quality_level is None:
        # Map complexity to quality level
        complexity_to_quality = {
            "low": "draft",
            "medium": "standard",
            "high": "production",
            "research": "research"
        }
        quality_level = complexity_to_quality.get(complexity, "standard")

    # 3.5 Check model performance matrix for data-driven override
    matrix_entry = check_model_matrix(task_type, complexity)
    if matrix_entry:
        model = matrix_entry["best_model"]
        model_config = CONFIG.get("models", {}).get(model, {})
        provider = model_config.get("provider", "gemini")
        api_name = model_config.get("api_name", model)
        thinking_config = get_thinking_config(model, quality_level)
        recommended_tools = select_tools_for_task(task_description)
        return {
            "provider": provider,
            "model": model,
            "api_name": api_name,
            "task_type": task_type,
            "complexity": complexity,
            "quality_level": quality_level,
            "thinking_config": thinking_config,
            "tools": recommended_tools,
            "requires_confirmation": model_config.get("requires_confirmation", False),
            "rationale": f"Data-driven selection from model matrix (confidence={matrix_entry['confidence']}, n={matrix_entry['sample_size']})"
        }

    # 4. Get model for quality level
    model = get_model_for_quality(quality_level, task_type)

    # 5. Check suitability and get fallback if needed
    if not check_model_suitability(model, quality_level):
        fallback = get_fallback_model(model)
        if fallback:
            model = fallback

    # 6. Get model config
    model_config = CONFIG.get("models", {}).get(model, {})
    provider = model_config.get("provider", "gemini")
    api_name = model_config.get("api_name", model)

    # 7. Get thinking configuration
    thinking_config = get_thinking_config(model, quality_level)

    # 8. Select appropriate tools
    recommended_tools = select_tools_for_task(task_description)

    # 9. Generate rationale
    rationale = _generate_rationale(model, quality_level, complexity, thinking_config)

    # 10. Check if confirmation required and estimate cost
    requires_confirmation = model_config.get("requires_confirmation", False)
    estimated_cost = None
    cost_warning = None

    if requires_confirmation:
        estimated_cost = estimate_task_cost(task_description, model, model_config)
        cost_warning = f"${estimated_cost:.2f}"

    return {
        "provider": provider,
        "model": model,
        "api_name": api_name,
        "task_type": task_type,
        "complexity": complexity,
        "quality_level": quality_level,
        "thinking_config": thinking_config,
        "tools": recommended_tools,
        "requires_confirmation": requires_confirmation,
        "estimated_cost": estimated_cost,
        "cost_warning": cost_warning,
        "rationale": rationale
    }

def _generate_rationale(model, quality_level, complexity, thinking_config):
    """Generate human-readable rationale for model selection"""
    model_config = CONFIG.get("models", {}).get(model, {})
    model_name = model_config.get("full_name", model)

    parts = [f"{model_name} selected for {quality_level} quality"]

    if thinking_config:
        if thinking_config.get("type") == "thinking_level":
            parts.append(f"thinking level: {thinking_config.get('level')}")
        elif thinking_config.get("type") in ["thinking_budget", "extended_thinking"]:
            budget = thinking_config.get("budget")
            if budget == -1:
                parts.append("dynamic thinking budget")
            else:
                parts.append(f"thinking budget: {budget}")

    cost_tier = model_config.get("cost_tier", "unknown")
    if cost_tier == "free":
        parts.append("free tier")
    elif cost_tier in ["standard", "premium"]:
        parts.append(f"{cost_tier} pricing")

    return " | ".join(parts)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Route tasks to optimal Gemini models based on complexity, type, and quality requirements"
    )
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--quality", choices=["draft", "standard", "production", "research"],
                       help="Explicit quality level (default: inferred from task)")
    parser.add_argument("--metadata", help="Optional metadata JSON")

    args = parser.parse_args()

    metadata = json.loads(args.metadata) if args.metadata else None
    result = select_model_for_task(args.task, metadata, args.quality)

    print(json.dumps(result, indent=2))
