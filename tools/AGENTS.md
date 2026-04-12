# Tools — Development Guide

The `tools/` directory contains all tool implementations that the agent can invoke. Each tool registers itself at import time via the central registry.

## Tool Registry Pattern

`tools/registry.py` is the central hub. It exports a `registry` singleton. Every tool file calls `registry.register()` at module level during import. `model_tools.py` triggers discovery by importing all tool modules in `_discover_tools()`.

```python
from tools.registry import registry

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

All handlers MUST return a JSON string (`json.dumps({...})`).

## Adding a New Tool

Requires changes in **3 files**:

1. **Create `tools/your_tool.py`** — implement the tool function and `registry.register()` call. Include a `check_requirements()` function if the tool needs env vars or optional deps.

2. **Add import in `model_tools.py`** — add to the `_discover_tools()` import list.

3. **Add to `toolsets.py`** — either to `_HERMES_CORE_TOOLS` (all platforms) or create a new toolset entry.

**Path references in schemas**: Use `display_hermes_home()` from `hermes_constants` for user-facing paths. Schemas are generated at import time, after `_apply_profile_override()` sets `HERMES_HOME`.

**State files**: Use `get_hermes_home()` for persistent state — never `Path.home() / ".hermes"`.

**Agent-level tools** (todo, memory): intercepted by `run_agent.py` before `handle_function_call()`. See `todo_tool.py` for the pattern.

## Tool Categories

| Category | Key File(s) | Notes |
|----------|------------|-------|
| Terminal | `terminal_tool.py`, `process_registry.py` | Shell execution, background processes |
| File | `file_tools.py`, `file_operations.py`, `patch_parser.py` | Read, write, search, patch |
| Web | `web_tools.py` | Firecrawl search/extract |
| Browser | `browser_tool.py`, `browser_camofox.py` | Browserbase + Camofox automation |
| Code execution | `code_execution_tool.py` | `execute_code` sandbox |
| Delegation | `delegate_tool.py` | Subagent spawning |
| MCP | `mcp_tool.py`, `mcp_oauth.py` | Model Context Protocol client |
| Memory | `memory_tool.py` | Persistent memory read/write |
| Skills | `skills_tool.py`, `skills_hub.py`, `skill_manager_tool.py` | Skill CRUD and discovery |
| Cron | `cronjob_tools.py` | Scheduled job management |
| Image gen | `image_generation_tool.py` | Image generation |
| Vision | `vision_tools.py` | Image analysis |
| Voice/TTS | `voice_mode.py`, `tts_tool.py`, `transcription_tools.py` | Audio I/O |

**Environments subsystem**: Terminal backends (local, docker, ssh, modal, etc.) live in `tools/environments/`. See `tools/environments/AGENTS.md` for architecture.

**Browser providers**: `tools/browser_providers/` — `base.py` (ABC), `browserbase.py`, `browser_use.py`, `firecrawl.py`.

## Security

- **Redact secrets**: All tool output entering model context must go through `redact_sensitive_text()` from `agent/redact.py`. This prevents secrets read from disk (e.g. `open('~/.hermes/.env')`) from leaking to the LLM provider. See `.claude/skills/hermes-dev/references/pitfalls.md` for the full redaction audit checklist.
- **Dangerous command detection**: `tools/approval.py` — `detect_dangerous_command()` checks for chmod 777, tee to /etc/, sudo cp, etc. When adding terminal operations, check if they need approval patterns.
- **Path validation**: `file_tools.py` has `_check_sensitive_path()` for /etc/, /boot/, docker.sock. `_is_blocked_device()` blocks device files using the literal input path (not `realpath()` — symlinks defeat that).
- **Credential passthrough**: `credential_files.py` for remote backends, `env_passthrough.py` for env vars into sandboxes.

## Pitfalls

### `_last_resolved_tool_names` is a process-global in `model_tools.py`
`_run_single_child()` in `delegate_tool.py` saves and restores this global around subagent execution. If you add new code that reads this global, be aware it may be temporarily stale during child agent runs.

### DO NOT hardcode cross-tool references in schema descriptions
Tool schema descriptions must not mention tools from other toolsets by name (e.g., `browser_navigate` saying "prefer web_search"). Those tools may be unavailable. If a cross-reference is needed, add it dynamically in `get_tool_definitions()` in `model_tools.py` — see the `browser_navigate` / `execute_code` post-processing blocks for the pattern.
