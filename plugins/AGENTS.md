# Plugins & Extensions — Development Guide

Hermes has **three separate extension subsystems**. They share nothing — different discovery, different loading, different APIs. Do not confuse them.

| System | Discovery | Registration | Runtime |
|--------|-----------|-------------|---------|
| General Plugins | `~/.hermes/plugins/`, pip entry points | `register(ctx)` in `__init__.py` | `hermes_cli/plugins.py` |
| Memory Providers | `plugins/memory/<name>/` | `register(ctx)` or class discovery | `plugins/memory/__init__.py` |
| Skills | `~/.hermes/skills/` scan | YAML frontmatter in `SKILL.md` | `agent/skill_commands.py` |

---

## General Plugin System

**Implementation**: `hermes_cli/plugins.py` — `PluginManager` singleton.

### Discovery (three sources, in priority order)

1. **User plugins**: `~/.hermes/plugins/<name>/` — always scanned
2. **Project plugins**: `./.hermes/plugins/<name>/` — only when `HERMES_ENABLE_PROJECT_PLUGINS` is set
3. **Pip entry points**: packages exposing `hermes_agent.plugins` group

Plugins listed in `config.yaml plugins.disabled` are skipped before loading.

### Plugin Structure

```
~/.hermes/plugins/my_plugin/
├── plugin.yaml      # Manifest (NOT manifest.yaml — scanner looks for plugin.yaml/yml)
└── __init__.py      # MUST contain register(ctx) — scanner imports __init__.py, not plugin.py
```

**Common silent failures:**
- `manifest.yaml` instead of `plugin.yaml` → silently skipped
- `register()` in `plugin.py` instead of `__init__.py` → "has no register() function" warning
- Missing `__init__.py` → "No __init__.py" error

### Plugin Capabilities

The `register(ctx)` function receives a `PluginContext` facade with:

| Method | What it does |
|--------|-------------|
| `ctx.register_tool(name, toolset, schema, handler, ...)` | Delegates to `tools.registry.register()` |
| `ctx.register_hook(hook_name, callback)` | Adds callback to hook dispatch |
| `ctx.register_cli_command(name, help, setup_fn, handler_fn)` | Wires into argparse via `hermes_cli/main.py` |
| `ctx.register_context_engine(engine)` | Sets the context engine (only one allowed) |
| `ctx.inject_message(content, role)` | Pushes into CLI interrupt/input queue |

### Lifecycle Hooks

10 hooks, invoked via `invoke_hook(name, **kwargs)`:

`pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `pre_api_request`, `post_api_request`, `on_session_start`, `on_session_end`, `on_session_finalize`, `on_session_reset`

- `invoke_hook()` wraps each callback in try/except — a broken plugin cannot crash the core loop
- `pre_llm_call` callbacks may return context strings injected into the user turn (never system prompt — preserves prompt cache)
- Wired in: `model_tools.py` (pre/post tool), `run_agent.py` (lifecycle hooks)

### Discovery Timing Pitfall

`discover_plugins()` only runs as a side effect of importing `model_tools.py`. `get_plugin_manager()` creates the singleton but does NOT call `discover_and_load()`. Any code reading plugin state (`get_plugin_toolsets()`, `_get_plugin_toolset_keys()`) in a process that hasn't imported `model_tools.py` will see zero plugins. Call `discover_plugins()` (idempotent) before reading plugin state.

### Policy

Plugin PRs must only modify their own plugin directory. Core files (`agent/`, `run_agent.py`, `model_tools.py`, `gateway/`, `hermes_cli/`) must not be touched. If a plugin needs a richer interface, that's a feature request for the core.

---

## Memory Providers

**Implementation**: `plugins/memory/__init__.py` — separate discovery system from general plugins.

- Selected via `config.yaml memory.provider`
- Only ONE active at a time
- Discovery: `discover_memory_providers()` returns `(name, desc, is_available)` tuples
- Loading: `load_memory_provider(name)` tries `register(ctx)` pattern first, falls back to `MemoryProvider` subclass discovery

### Available Backends

byterover, hindsight, holographic, honcho, mem0, openviking, retaindb, supermemory

Each backend lives in `plugins/memory/<name>/` with `__init__.py` + `plugin.yaml`. Backends may also have a `cli.py` with `register_cli(subparser)` for CLI command registration (loaded via `discover_plugin_cli_commands()`).

---

## Context Engine

**Implementation**: `plugins/context_engine/__init__.py`

Registered via the general plugin system's `ctx.register_context_engine(engine)`. Only one context engine may be active. Second registration attempt warns and is rejected.

---

## Skills (Brief)

Skills are NOT Python plugins — they are markdown files with YAML frontmatter. They do not call `register()`.

- Location: `~/.hermes/skills/` (user), `skills/` (bundled), `optional-skills/` (opt-in)
- Loaded by: `agent/skill_commands.py` scanning for `SKILL.md` files
- Injected as: user messages (not system prompt) to preserve prompt caching
- Config interface: `metadata.hermes.config` in frontmatter → `skills.config.*` in config.yaml

See `.claude/skills/hermes-dev/references/skill-config-interface.md` for config details.

---

## Testing Plugins

Use an isolated `HERMES_HOME` to avoid polluting real config. See `.claude/skills/hermes-dev/references/testing-recipes.md` for the full pattern including plugin.yaml format, `__init__.py` register pattern, and common silent failure modes.
