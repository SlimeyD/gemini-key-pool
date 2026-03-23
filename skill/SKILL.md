---
name: gemini-delegator
description: Delegate work to Gemini agents to save Claude tokens and gain parallel throughput. Gemini agents can do far more than text generation — they have code execution (Python with pandas/numpy/matplotlib), Google Search with citations, URL reading (up to 20 pages), structured JSON output, image generation and understanding, and MCP tool access (Linear, Supabase, agent-browser, Playwright). Use this skill whenever you're doing medium-to-high complexity work — summarizing, reviewing, analyzing, data processing, chart generation, web research, code generation from specs, image work, structured extraction, or batch-processing. Also use for orchestration patterns like "council of experts", "parallel analysis", or any 2+ independent subtasks. Use during plan execution (each task is a delegation candidate), plan writing (research phases), and testing with agent-browser or Playwright. If a task has clear inputs and success criteria and doesn't need conversation context, delegate it.
---

# Gemini Delegator

You have a workforce of Gemini agents available via shell commands. Your job is to **orchestrate** — break work into delegable pieces, dispatch them, validate the results, and synthesize for the user.

This isn't something you do only when asked. Whenever you recognize work that Gemini handles well, route it there automatically. The user benefits from faster results and you preserve your context for the reasoning that matters.

## Should I Delegate This?

```
Is this task...
  ├─ Independent (doesn't need conversation context)? → DELEGATE fully
  ├─ Well-defined (clear input, clear success criteria)? → DELEGATE fully
  ├─ One of many similar subtasks? → DELEGATE in parallel
  ├─ A named pattern (council of experts, parallel review)? → DELEGATE
  ├─ A plan task with clear requirements? → DELEGATE
  ├─ A test/validation that can run headlessly? → DELEGATE
  ├─ Interactive but has a research component? → DELEGATE research, KEEP conversation
  └─ Purely trivial or needs live credentials? → KEEP
```

**Delegate when:**
- **Analysis & review** — summarize, compare, review code, audit, document
- **Code generation** — especially with specs, templates, or clear requirements
- **Data processing** — CSV analysis, calculations, chart generation (Gemini has pandas/numpy/matplotlib in its code execution sandbox)
- **Research** — web search, reading URLs, fact-checking, competitive analysis (via `--enable-tools`)
- **Image work** — generation, understanding, visual comparison, screenshot analysis
- **Batch processing** — many items, same operation, in parallel
- **Multi-perspective analysis** — council of experts, parallel review, compare approaches
- **Testing** — browser automation via agent-browser/Playwright, visual regression, accessibility audits
- **Plan execution** — each independent plan task is a natural delegation candidate
- **Plan writing** — research phases, requirements analysis, surveying approaches
- **Structured extraction** — pull data into JSON schemas, classify content, parse documents
- The task has clear inputs and success criteria and doesn't need your conversation history

**Keep in Claude (but delegate the research first):**

Most tasks have a delegable research/analysis phase even when the final conversation must stay in Claude. The pattern is: delegate research → read results → have an informed conversation with the user.

- **Interactive decisions** — delegate background research (benchmarks, best practices, tradeoffs), then discuss with user using the data
- **Final synthesis** — combining multiple delegated outputs into a coherent recommendation is your job
- **Security-sensitive operations** — anything involving credentials or secrets stays in Claude
- **Claude-specific tools** — tasks needing Read, Edit, Glob that Gemini can't access
- **Trivial tasks** — when delegation overhead exceeds the work itself

## How to Delegate

### Single Task

```bash
~/Code/ai-orchestration/scripts/.venv/bin/python3 \
  ~/Code/ai-orchestration/scripts/gemini_agent.py \
  --task "Your detailed prompt here" \
  --output /tmp/result.md
```

The model is auto-selected based on task complexity. Only specify `--model` when you need to override:

| Task Type | Auto-routes to | Override when |
|-----------|---------------|---------------|
| Format, list, summarize, translate | gemini-3-flash | Never — Flash is right |
| Review, analyze, document, compare | gemini-3-pro | Use `--model gemini-3-flash` for speed over depth |
| Architect, security audit, complex code gen | gemini-3-pro | Use `--quality production` or `research` for deeper thinking |
| Image generation | gemini-2.5-flash-image | Use `--model gemini-3-pro-image-preview` for high-fidelity |

### Useful Flags

| Flag | Purpose | Example |
|------|---------|---------|
| `--quality draft\|standard\|production\|research` | Controls thinking depth | `--quality production` for customer-facing output |
| `--context-file path` | Feed additional context | `--context-file /tmp/code-summary.md` |
| `--image-file path` | Image understanding input | `--image-file screenshot.png` |
| `--image-output path` | Image generation output | `--image-output /tmp/logo.png` |
| `--enable-tools` | Enable Google Search + code execution | Research tasks, data analysis, chart generation |
| `--enable-mcp` | Enable MCP tools (Linear, Supabase, browser) | `--enable-mcp --mcp-servers linear-ratemyflat` |
| `--json` | Output result as structured JSON | When you need machine-parseable output |
| `--capture-thinking` | Return model's reasoning trace | Useful for debugging unexpected outputs |

### Shell Shortcuts

If the shell integration is sourced (`source ~/Code/ai-orchestration/meta/shell/orchestration.zsh`):

```bash
gemini-agent --task "..." --output /tmp/result.md    # Full control
delegate "Quick summary of this error log"            # Auto-output shortcut
which-model "Refactor the auth system"                # Check routing
```

## Orchestration Patterns

These patterns are your playbook for multi-agent work. When the user's task matches one, use it.

### Parallel-Aggregate (most common)

Multiple agents analyze the same input from different angles, you synthesize.

```
User: "Review this codebase thoroughly"

You dispatch (in parallel via Bash with run_in_background):
  Agent 1: "Review src/auth/ for security vulnerabilities..." → /tmp/security.md
  Agent 2: "Review src/auth/ for performance bottlenecks..." → /tmp/perf.md
  Agent 3: "Review src/auth/ for code quality and maintainability..." → /tmp/quality.md

You synthesize: Read all three, prioritize findings, present unified review.
```

### Council of Experts

A specialized form of Parallel-Aggregate where each agent adopts an expert persona.

```
User: "Analyze our landing page"

Dispatch 5-7 domain experts in parallel:
  UX Expert, Visual Design Expert, Performance Expert,
  SEO Expert, Accessibility Expert, Copywriting Expert

Each writes findings to /tmp/expert-{domain}.md
You consolidate by severity, cross-referencing where experts agree.
```

### Sequential Pipeline

Tasks in order where each depends on the previous output.

```
Agent 1 (Flash): Gather raw data → /tmp/raw.md
Agent 2 (Pro): Analyze and structure → /tmp/analysis.md
You: Final synthesis and presentation to user
```

### Batch Processing

Same operation across many items.

```
User: "Document all 8 API endpoints"

Dispatch 8 parallel agents, one per endpoint:
  gemini-agent --task "Document the /api/users endpoint..." --output /tmp/doc-users.md
  gemini-agent --task "Document the /api/auth endpoint..." --output /tmp/doc-auth.md
  ...

You: Review, ensure consistency, compile into single doc.
```

For more patterns (Hierarchical, Double-Diamond, Handoff Chain, Structured Debate), see [references/patterns.md](references/patterns.md).

## Crafting Good Delegation Prompts

Gemini agents have no conversation context. Everything they need must be in the prompt.

**Context feeding:** Summarize the key code sections in the prompt rather than passing huge files. Gemini Pro handles detailed summaries better than raw code dumps.

**Bad:**
```
"Review the code"
```

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

## Validating Gemini Output

This is non-negotiable. Gemini agents are fast but can hallucinate — especially on:
- File paths and directory structures (may invent paths that don't exist)
- Feature descriptions (may describe features the product doesn't have)
- Specific code details (verify against actual source)

Always read the output file and cross-check key claims before presenting to the user. For code suggestions, verify they compile/run. For factual claims, spot-check against the source material.

## Parallel Dispatch from Claude Code

Use the Bash tool with `run_in_background` for parallel execution:

```bash
# Each runs as a separate background task
~/Code/ai-orchestration/scripts/.venv/bin/python3 \
  ~/Code/ai-orchestration/scripts/gemini_agent.py \
  --task "Security review of auth module..." \
  --output /tmp/security-review.md

# Launch multiple in the same message for true parallelism
```

After all complete, read the output files and synthesize.

**Throughput:** 18 API keys with LRU rotation. Effective parallel capacity: ~4 simultaneous agents comfortably, more with Flash.

**Caveat:** API key cooldowns are currently model-agnostic — a key rate-limited on one model gets blocked for all models. If you're hitting rate limits with heavy parallel dispatch, stagger launches slightly.

## Gemini Agent Capabilities

Gemini agents are more capable than a simple text-in/text-out API. Understanding what they can do unlocks better delegation decisions.

### Built-in Tools (`--enable-tools`)

| Tool | What It Does | Great For |
|------|-------------|-----------|
| **Google Search** | Real-time web search with citations and source URIs | Research, fact-checking, finding current info, competitor analysis |
| **Code Execution** | Python sandbox with 40+ libraries (pandas, numpy, sklearn, TensorFlow, opencv, matplotlib) | Data analysis, chart generation, calculations, CSV processing, regex validation |
| **URL Context** | Read and analyze up to 20 web pages per request (34MB each) | Deep page analysis, content extraction, comparing multiple URLs |

Code execution is particularly powerful — Gemini can write Python, run it, see the output, iterate up to 5 times if errors occur, and generate matplotlib charts. Delegate "analyze this data" or "generate a chart from these numbers" tasks here.

### Multimodal Input/Output

| Direction | Supported Types |
|-----------|----------------|
| **Input** | Text, images (PNG/JPEG/WebP), audio, video, PDFs, code files |
| **Output** | Text, images (via image models), structured JSON (schema-enforced), matplotlib charts (via code execution) |

### Structured Output

Gemini can enforce JSON schema on responses — guaranteed valid JSON matching your Pydantic/Zod schema. Use `response_mime_type="application/json"` with a schema for:
- Data extraction into structured formats
- Classification with enum constraints
- API response generation
- Any task where you need machine-parseable output

### MCP Tools (`--enable-mcp`)

When enabled, Gemini agents can call MCP tools (Linear, Supabase, Playwright, local-shell/agent-browser). This means they can:
- Create/update Linear issues
- Query Supabase databases
- Drive browser automation via agent-browser or Playwright
- Execute shell commands (within allowlist)

### Computer Use (Preview)

Gemini 3 Flash supports vision-based browser automation — it sees screenshots and generates UI actions (click, type, scroll, drag). Different from Playwright (vision-based vs DOM-based) but useful for testing dynamic interfaces. Not yet integrated into `gemini_agent.py` but available via the API.

### File Search (RAG)

Full RAG pipeline via the API: upload documents (150+ file types including PDF, Word, Excel, code), auto-chunk and embed, then query semantically. Useful for "search through these documents" tasks. Not yet exposed via `gemini_agent.py` — use the API directly if needed.

### Thinking Levels

The `--quality` flag maps to Gemini's thinking configuration:

| Quality | Thinking Level | Use For |
|---------|---------------|---------|
| `draft` | minimal | Quick formatting, simple transforms |
| `standard` | medium | Everyday analysis, code review |
| `production` | high | Customer-facing output, complex code gen |
| `research` | high + Pro model | Deep analysis, architecture, security audits |

### What Gemini Agents Cannot Do

- Access your conversation history (everything must be in the prompt)
- Interact with the user mid-task
- Maintain state between separate calls
- Use Claude Code tools (Read, Edit, Glob, etc.) — use `--context-file` to pass file contents
- Install custom Python packages (only the 40+ pre-installed ones)
- Access localhost URLs via URL Context (use MCP + agent-browser instead)

## Works With Other Skills

This skill is a force multiplier when combined with other workflows:

### Plan Execution
When executing an implementation plan (via `executing-plans` or `subagent-driven-development`), each independent task is a delegation candidate. Instead of Claude doing every task sequentially, dispatch plan tasks to Gemini agents in parallel. Claude orchestrates the plan, validates outputs, and handles the synthesis.

### Plan Writing
During the research and analysis phases of writing a plan, delegate the groundwork:
- "Survey 3 approaches to implementing X" → parallel Gemini agents
- "Analyze the codebase for all uses of Y" → Gemini with `--context-file`
- "Research best practices for Z" → Gemini with `--enable-tools` (Google Search)

Claude then synthesizes the research into the actual plan.

### Testing with agent-browser / Playwright
Gemini agents with `--enable-mcp` can drive browser automation:
```bash
gemini-agent \
  --task "Open https://localhost:3000, take a screenshot, check for accessibility violations" \
  --enable-mcp --mcp-servers local-shell \
  --output /tmp/test-results.md
```

Use this for: visual regression testing, screenshot comparisons across viewports, accessibility audits, smoke testing deployed pages. Dispatch multiple agents in parallel to test different pages or viewports simultaneously.

## Reference Files

| File | When to read |
|------|-------------|
| [references/patterns.md](references/patterns.md) | Need advanced patterns beyond the basics above |
| [references/model-capabilities.md](references/model-capabilities.md) | Need detailed model specs, costs, or fallback chains |
| [references/claude-integration.md](references/claude-integration.md) | Using Claude Code subagents alongside Gemini delegation |
| [references/key-configuration.md](references/key-configuration.md) | Setting up API keys for export/new environments |
