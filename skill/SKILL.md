---
name: gemini-delegator
description: Delegate work to Gemini agents to save Claude tokens and gain parallel throughput. Gemini agents have code execution (Python with pandas/numpy/matplotlib), Google Search with citations, URL reading, structured JSON output, and image generation/understanding. Use whenever you're doing medium-to-high complexity work — summarizing, reviewing, analyzing, data processing, web research, code generation from specs, image work, structured extraction, or batch-processing. Also use for orchestration patterns like "council of experts", "parallel analysis", or any 2+ independent subtasks.
---

# Gemini Delegator (Claude Code Skill)

You have a workforce of Gemini agents available via shell commands. Your job is to **orchestrate** — break work into delegable pieces, dispatch them, validate the results, and synthesize for the user.

## Quick Start

Set the `GEMINI_KEY_POOL_ROOT` environment variable to point to this repo, then:

```bash
python3 -m gemini_key_pool.gemini_agent \
  --task "Your detailed prompt here" \
  --output /tmp/result.md
```

## Should I Delegate This?

**Delegate when:**
- Analysis & review — summarize, compare, review code, audit, document
- Code generation — especially with specs, templates, or clear requirements
- Data processing — CSV analysis, calculations, chart generation
- Research — web search, reading URLs, fact-checking (`--enable-tools`)
- Image work — generation, understanding, visual comparison
- Batch processing — many items, same operation, in parallel
- Multi-perspective analysis — council of experts, parallel review
- Structured extraction — pull data into JSON schemas, classify content

**Keep in Claude:**
- Interactive decisions (delegate research first, then discuss)
- Security-sensitive operations (credentials, secrets)
- Claude-specific tools (Read, Edit, Glob, etc.)
- Trivial tasks (delegation overhead exceeds the work)

## Useful Flags

| Flag | Purpose |
|------|---------|
| `--quality draft\|standard\|production\|research` | Controls thinking depth |
| `--context-file path` | Feed additional context |
| `--image-file path` | Image understanding input |
| `--image-output path` | Image generation output |
| `--enable-tools` | Enable Google Search + code execution |
| `--json` | Output result as structured JSON |
| `--capture-thinking` | Return model's reasoning trace |

## Orchestration Patterns

### Parallel-Aggregate (most common)

Multiple agents analyze the same input from different angles, you synthesize.

```
Dispatch (in parallel via Bash with run_in_background):
  Agent 1: "Review for security vulnerabilities..." → /tmp/security.md
  Agent 2: "Review for performance bottlenecks..." → /tmp/perf.md
  Agent 3: "Review for code quality..." → /tmp/quality.md

Synthesize: Read all three, prioritize findings, present unified review.
```

### Batch Processing

Same operation across many items — dispatch N parallel agents.

### Council of Experts

5-7 domain experts in parallel, each writes findings, you consolidate by severity.

## Crafting Good Prompts

Gemini agents have no conversation context. Everything they need must be in the prompt.

**Bad:** `"Review the code"`

**Good:**
```
"Review src/auth/login.py for:
1. Security vulnerabilities (injection, XSS, auth bypass)
2. Error handling completeness
3. Edge cases not covered

Here is the file content:
[paste or use --context-file]

Output format:
- Issue description with line numbers
- Severity (critical/high/medium/low)
- Suggested fix"
```

## Validating Output

Always read the output file and cross-check key claims before presenting to the user. Gemini can hallucinate file paths, features, and code details.
