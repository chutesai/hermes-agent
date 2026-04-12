# CLI & Configuration — Development Guide

The `hermes_cli/` directory contains the CLI entry point, all `hermes` subcommands, configuration management, provider auth, setup wizard, and the skin/theme engine.

## CLI Architecture

- **`cli.py`** (root) — `HermesCLI` class, interactive CLI orchestrator
- **Rich** for banner/panels, **prompt_toolkit** for input with autocomplete
- **KawaiiSpinner** (`agent/display.py`) — animated faces during API calls, `┊` activity feed for tool results
- `load_cli_config()` in `cli.py` merges hardcoded defaults + user config YAML
- `process_command()` dispatches on canonical command name via `resolve_command()` from the central registry
- Skill slash commands: `agent/skill_commands.py` scans `~/.hermes/skills/`, injects as **user message** (not system prompt) to preserve prompt caching

## Slash Command Registry (`hermes_cli/commands.py`)

All slash commands are defined in `COMMAND_REGISTRY` — a list of `CommandDef` objects. Every downstream consumer derives from this automatically:

- **CLI** — `process_command()` dispatches via `resolve_command()`
- **Gateway** — `GATEWAY_KNOWN_COMMANDS` frozenset + `resolve_command()` for dispatch
- **Telegram** — `telegram_bot_commands()` generates BotCommand menu
- **Slack** — `slack_subcommand_map()` generates `/hermes` subcommand routing
- **Autocomplete** — `COMMANDS` flat dict feeds `SlashCommandCompleter`

### CommandDef Fields

| Field | Purpose |
|-------|---------|
| `name` | Canonical name without slash (e.g. `"background"`) |
| `description` | Human-readable |
| `category` | `"Session"`, `"Configuration"`, `"Tools & Skills"`, `"Info"`, `"Exit"` |
| `aliases` | Tuple of alternatives (e.g. `("bg",)`) |
| `args_hint` | Argument placeholder (e.g. `"<prompt>"`) |
| `cli_only` | Only in interactive CLI |
| `gateway_only` | Only in messaging platforms |
| `gateway_config_gate` | Config dotpath; cli_only command becomes gateway-available if truthy |

### Adding a Slash Command

1. Add `CommandDef` to `COMMAND_REGISTRY` in `commands.py`
2. Add handler in `HermesCLI.process_command()` in `cli.py`
3. If gateway-available, add handler in `gateway/run.py`
4. For persistent settings, use `save_config_value()` in `cli.py`

Adding an alias requires only adding to the `aliases` tuple — all consumers update automatically.

---

## Provider System

### PROVIDER_REGISTRY (`auth.py`)

20+ entries of `ProviderConfig` dataclass: `id`, `name`, `auth_type`, `portal_base_url`, `inference_base_url`, `client_id`, `scope`, `api_key_env_vars`, `base_url_env_var`.

**Auth types:**
- `"api_key"` — env var based (most providers)
- `"oauth_device_code"` — Nous Portal (device code flow + key minting)
- `"oauth_external"` — Codex, Qwen (externally-managed OAuth)
- `"external_process"` — Copilot ACP (subprocess-based)

**Alias system**: `_PROVIDER_ALIASES` inside `resolve_provider()` maps ~40 friendly names to canonical IDs (e.g. `"claude"` → `"anthropic"`, `"google"` → `"gemini"`, `"ollama"` → `"custom"`).

### Resolution Chain

1. `resolve_provider()` (auth.py) — normalizes name through aliases, checks registry, auto-detects credentials
2. `resolve_runtime_provider()` (runtime_provider.py) — returns dict with `provider`, `api_mode`, `base_url`, `api_key`, `source`, plus extras (`credential_pool`, `expires_at`, etc.)

**`api_mode` values** (three possible):
- `"chat_completions"` — OpenAI-compatible (most providers, OpenRouter)
- `"codex_responses"` — OpenAI Responses API (GPT-5.x via Codex)
- `"anthropic_messages"` — Anthropic Messages API (direct Anthropic, MiniMax /anthropic endpoints)

Auto-detected from URL suffix (`/anthropic` → `anthropic_messages`) or from config `model.api_mode`.

### Model Catalog (`models.py`)

`_PROVIDER_MODELS` maps provider IDs to curated model lists (used in `hermes setup`/`hermes model` menus). OpenRouter uses separate `OPENROUTER_MODELS` with live catalog fallback. Providers with 8+ curated models skip live probe.

For full provider implementation guide, see `.claude/skills/hermes-dev/references/provider-guide.md`.

---

## Config System

### Two Loaders

| Loader | Used by | Location |
|--------|---------|----------|
| `load_cli_config()` | CLI mode | `cli.py` — merges hardcoded defaults + user YAML |
| `load_config()` | `hermes tools`, `hermes setup` | `hermes_cli/config.py` — merges `DEFAULT_CONFIG` + user YAML |
| Direct YAML load | Gateway | `gateway/run.py` — reads raw |

### Adding Config Options

**config.yaml**: Add to `DEFAULT_CONFIG` in `hermes_cli/config.py`. Bump `_config_version` only when you need to actively migrate existing values (renaming keys, changing structure). New keys in existing sections get defaults via `_deep_merge()` automatically.

**.env variables**: Add to `OPTIONAL_ENV_VARS` in `hermes_cli/config.py`:
```python
"NEW_API_KEY": {
    "description": "What it's for",
    "prompt": "Display name",
    "url": "https://...",
    "password": True,
    "category": "tool",  # provider, tool, messaging, setting
},
```

**Policy**: `.env` is for secrets (API keys, tokens) ONLY. All non-secret configuration belongs in `config.yaml`.

---

## Skin/Theme System (`hermes_cli/skin_engine.py`)

Data-driven CLI visual customization. Skins are **pure data** — no code changes needed.

### Architecture

- `init_skin_from_config()` — called at CLI startup, reads `display.skin` from config
- `get_active_skin()` — returns cached `SkinConfig`
- `set_active_skin(name)` — switches at runtime (`/skin` command)
- `load_skin(name)` — user skins first, then built-ins, falls back to default
- Missing values inherit from `default` skin automatically

### What Skins Customize

| Element | Skin Key | Used By |
|---------|----------|---------|
| Banner border/title/accent/dim/text | `colors.*` | `banner.py` |
| Response box border | `colors.response_border` | `cli.py` |
| Spinner faces (waiting/thinking) | `spinner.waiting_faces`, `spinner.thinking_faces` | `display.py` |
| Spinner verbs/wings | `spinner.thinking_verbs`, `spinner.wings` | `display.py` |
| Tool output prefix | `tool_prefix` | `display.py` |
| Per-tool emojis | `tool_emojis` | `display.py` |
| Agent name/welcome/response label/prompt | `branding.*` | `banner.py`, `cli.py` |

### Built-in Skins

`default` (gold/kawaii), `ares` (crimson/bronze), `mono` (grayscale), `slate` (cool blue)

### Adding a Built-in Skin

Add to `_BUILTIN_SKINS` dict in `skin_engine.py`. For user skins: `~/.hermes/skins/<name>.yaml`, activate with `/skin <name>`.

---

## Profiles: Multi-Instance Support

Each profile gets its own `HERMES_HOME` directory. `_apply_profile_override()` in `main.py` sets `HERMES_HOME` before any module imports. All 119+ references to `get_hermes_home()` automatically scope to the active profile.

### Rules for Profile-Safe Code

1. **Use `get_hermes_home()`** for all paths. Never hardcode `~/.hermes` or `Path.home() / ".hermes"`.
2. **Use `display_hermes_home()`** for user-facing messages (returns `~/.hermes` for default, `~/.hermes/profiles/<name>` for profiles).
3. **Module-level constants are fine** — they cache `get_hermes_home()` after `_apply_profile_override()`.
4. **Tests must also set `HERMES_HOME`** when mocking `Path.home()`.
5. **Gateway adapters should use token locks** — `acquire_scoped_lock()` prevents two profiles from using the same credential.
6. **Profile operations are HOME-anchored** — `_get_profiles_root()` returns `Path.home() / ".hermes" / "profiles"`, not `get_hermes_home() / "profiles"`.
