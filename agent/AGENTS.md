# Agent Internals — Development Guide

The `agent/` directory contains the LLM client core — prompt building, context management, API adapters, credential handling, and display rendering. The `AIAgent` class itself lives in root `run_agent.py`, not in this directory.

## AIAgent Class (`run_agent.py`)

```python
class AIAgent:
    def __init__(self,
        model: str = "anthropic/claude-opus-4.6",
        max_iterations: int = 90,
        enabled_toolsets: list = None,
        disabled_toolsets: list = None,
        quiet_mode: bool = False,
        save_trajectories: bool = False,
        platform: str = None,           # "cli", "telegram", etc.
        session_id: str = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        # ... plus provider, api_mode, callbacks, routing params
    ): ...

    def chat(self, message: str) -> str:
        """Simple interface — returns final response string."""

    def run_conversation(self, user_message: str, ...) -> dict:
        """Full interface — returns dict with final_response + messages."""
```

### Core Loop

Inside `run_conversation()` — entirely synchronous:

```python
while api_call_count < self.max_iterations and self.iteration_budget.remaining > 0:
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

Messages follow OpenAI format: `{"role": "system/user/assistant/tool", ...}`. Reasoning content stored in `assistant_msg["reasoning"]`.

## Prompt Builder (`prompt_builder.py`)

Assembles the system prompt from: base instructions, platform hints (`PLATFORM_HINTS`), tool guidance, model-specific adjustments, memory context, and skill injections. The system prompt is built once per session and cached — prompt caching integrity is critical.

## Context Compression

**Dual system** — two independent compressors that can disagree:

1. **Gateway hygiene pre-compression** (`gateway/run.py`): runs BEFORE the agent starts, hardcoded 85% threshold, rough token estimates
2. **Agent compressor** (`context_compressor.py`): runs during the agent loop, uses real API-reported token counts, fires at user-configured threshold (default 50%)

**Context length resolution chain** (8 steps): explicit config → persistent cache → endpoint `/models` → local server query → Anthropic API → models.dev → OpenRouter metadata → hardcoded defaults → 128K fallback.

For compression persistence pitfalls (dual path handling), see `.claude/skills/hermes-dev/references/architecture.md`.

## Anthropic Adapter (`anthropic_adapter.py`)

Message format conversion between Hermes's OpenAI-style messages and Anthropic Messages API. Key responsibilities:
- `build_anthropic_kwargs()` — full request construction with beta headers
- `convert_messages_to_anthropic()` — message format translation
- Thinking block signature management (base_url-aware stripping for third-party endpoints)
- Per-model output limits (`_ANTHROPIC_OUTPUT_LIMITS` lookup table)
- Adaptive thinking support detection

See `.claude/skills/hermes-dev/references/anthropic-thinking.md` for signature handling details.

## Credential Pool (`credential_pool.py`)

Multi-key rotation for providers with multiple API keys. `CredentialPool` manages round-robin selection, auto-seeding from env vars and OAuth tokens. The `load_pool()` function returns a pool when `config.yaml credential_pool` has entries.

## Display System (`display.py`)

`KawaiiSpinner` for animated waiting indicators, tool preview formatting, `_safe_print`/`_cprint` routing for prompt_toolkit compatibility. All output that goes through prompt_toolkit's `patch_stdout` must use `_cprint` or `print_formatted_text(ANSI(...))` — raw `print()` during the event loop produces garbled escape sequences.

## Other Modules

| Module | Purpose |
|--------|---------|
| `auxiliary_client.py` | Secondary LLM client for vision, summarization, compression |
| `model_metadata.py` | Context length resolution, `DEFAULT_CONTEXT_LENGTHS`, token estimation |
| `models_dev.py` | models.dev registry integration (provider-aware context lookup) |
| `smart_model_routing.py` | Automatic model switching for simple vs complex turns |
| `error_classifier.py` | Classifies API errors for retry/fallback decisions |
| `rate_limit_tracker.py` | Tracks provider rate limits |
| `redact.py` | `redact_sensitive_text()` for stripping secrets from tool output |
| `skill_commands.py` | Skill slash command handling, skill injection as user messages |
| `skill_utils.py` | Skill metadata parsing, frontmatter, config var resolution |
| `usage_pricing.py` | Token usage cost tracking |
| `trajectory.py` | Trajectory saving for RL training |
| `insights.py` | Session analytics and insights |
| `prompt_caching.py` | Anthropic prompt caching helpers |
| `retry_utils.py` | Retry logic with exponential backoff |
