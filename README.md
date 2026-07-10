# or-cli — OpenRouter Unified CLI Router

Routes AI CLI tools (codex, gemini, claude, cline, opencode) through OpenRouter with transparent LLM usage monitoring.

All traffic passes through a monitoring proxy that logs every call — model, tokens, cost, duration — into a one-JSON-object-per-line file. No config changes to the upstream CLIs; everything is transparent.

## Prerequisites

- **OpenRouter API key** — set as `OPENROUTER_API_KEY` in your environment
- One or more of the supported AI CLIs installed:
  - [Codex CLI](https://github.com/openai/codex) (`codex`)
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini`)
  - [Claude CLI](https://github.com/anthropics/claude-code) (`claude`)
  - [Cline](https://github.com/cline/cline) (`cline`)
  - [OpenCode](https://github.com/opencode-ai/opencode) (`opencode`)
- Python 3.9+ with `httpx` (`pip install httpx`)

## Install

```bash
# Clone the repo
git clone https://github.com/droxey/or-cli.git
cd or-cli

# Install or-cli to your PATH
cp or-cli ~/.local/bin/or-cli
chmod +x ~/.local/bin/or-cli

# Verify
or-cli --help
```

## Monitoring Proxy

Two transparent proxy scripts capture every LLM API call:

| Proxy | Port | Forwards to | Log file |
|---|---|---|---|
| `llm-monitor-proxy.py` | 9090 | Nebula proxy | `nebula-llm-usage.jsonl` |
| `llm-monitor-openrouter.py` | 9091 | OpenRouter API | `or-llm-usage.jsonl` |

### Start the proxies

```bash
# OpenRouter monitor (all or-cli calls go through this)
LLM_PROXY_PORT=9091 LLM_PROXY_LOG=~/or-llm-usage.jsonl \
  python3 llm-monitor-openrouter.py &

# Nebula proxy monitor (for direct Nebula API calls)
LLM_PROXY_PORT=9090 LLM_PROXY_LOG=~/nebula-llm-usage.jsonl \
  python3 llm-monitor-proxy.py &
```

To run persistently, use supervisord or systemd:

```ini
# supervisor: /etc/supervisor/conf.d/or-monitor-proxy.conf
[program:or-monitor-proxy]
command=python3 /path/to/or-cli/llm-monitor-openrouter.py
environment=LLM_PROXY_PORT=9091,LLM_PROXY_LOG=/home/nebula/or-llm-usage.jsonl
directory=/home/nebula
```

## Usage

```bash
# Auto-detect tier from prompt wording
or-cli exec --tool codex --prompt "Write fibonacci in Python"

# Reasoning tier (deepseek-r1)
or-cli exec --tool claude --tier reasoning --prompt "Design a cache invalidation strategy"

# Fast tier (haiku)
or-cli exec --tool gemini --tier fast --prompt "Summarize this changelog"

# Explicit model (any OpenRouter model string)
or-cli exec --tool gemini --model "anthropic/claude-sonnet-4-6" --prompt "Review this code"
```

### Auto-detected tiers

| Prompt keywords | Tier | Model |
|---|---|---|
| plan, analyze, reason, design, debug, review, compare, explain why… | **reasoning** | `deepseek/deepseek-r1` |
| summarize, quick, simple, short, brief, list, tldr… | **fast** | `anthropic/claude-haiku-4-6` |
| (everything else) | **coding** | `openai/gpt-5.5` |

## Checking Usage

Each proxy logs one JSON object per call:

```json
{"timestamp": "2026-07-09T13:19:57+00:00", "model": "openai/gpt-4o-mini",
 "provider": "openai", "prompt_tokens": 11, "completion_tokens": 4,
 "total_tokens": 15, "cost": 4.05e-06, "duration_ms": 864, "status": "ok"}
```

```bash
# Last 10 calls
cat ~/or-llm-usage.jsonl | jq -r '[.timestamp[0:16], .model, .total_tokens, .cost] | @tsv' | tail -10

# Monthly spend by model
cat ~/or-llm-usage.jsonl ~/nebula-llm-usage.jsonl \
  | jq -s 'group_by(.model) | map({model: .[0].model, calls: length, tokens: map(.total_tokens)|add, cost_est: map(.cost)|add})'

# Calls in the last 7 days
cat ~/or-llm-usage.jsonl | jq -r \
  --arg cutoff "$(date -d '7 days ago' -Iseconds)" \
  'select(.timestamp >= $cutoff) | [.timestamp[0:10], .model, .total_tokens, .cost] | @tsv'
```

## How It Works

`or-cli` sets the upstream CLI's API key and base URL to point at the monitoring proxy:

```
your prompt → or-cli → codex/claude/etc → OPENAI_BASE_URL=:9091 → monitor proxy → OpenRouter
                                                                         ↓
                                                                   or-llm-usage.jsonl
```

Each CLI tool sees normal OpenAI-compatible endpoints; the proxy intercepts, logs the request/response, and forwards to OpenRouter. No changes to the individual CLI tools.

## What Gemini Can't Do

The Gemini CLI (`gemini`) talks to Google's native API, not OpenAI-compatible endpoints. It can't route through the OpenRouter proxy and will use Google's own models instead. Use codex, claude, cline, or opencode when you want OpenRouter routing.
