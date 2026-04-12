# Provider Guide

Detailed provider implementation, debugging, and auth reference.

## Workflow: Adding a New Provider

Adding a new inference provider touches 6+ files. Follow this checklist:

### Files to modify (in order)

1. **`agent/<provider>_adapter.py`** (NEW) — Format conversion between OpenAI messages and the provider's native API. Isolate ALL provider-specific logic here: client construction, message/tool conversion, response normalization. Follow the pattern of `anthropic_adapter.py` or `codex_responses` in `run_agent.py`.

2. **`hermes_cli/auth.py`** — Add `ProviderConfig` to `PROVIDER_REGISTRY` with auth_type, env vars, base URL. Add aliases to `_PROVIDER_ALIASES`.

3. **`hermes_cli/models.py`** — Add to `_PROVIDER_MODELS` (static fallback), `_PROVIDER_LABELS`, `_PROVIDER_ALIASES`, `_PROVIDER_ORDER`. Add live model fetching function if the provider has a `/models` endpoint.

4. **`hermes_cli/runtime_provider.py`** — Add provider branch in `resolve_runtime_provider()` returning `api_mode`, `api_key`, `base_url`. Raise `AuthError` if no credentials found.

5. **`hermes_cli/main.py`** — Add to `--provider` CLI choices, `provider_labels` dict in `cmd_model()`, `providers` list, and create `_model_flow_<provider>()` function.

6. **`hermes_cli/setup.py`** — **NO CHANGES NEEDED for provider picker.** Since March 2026, `setup_model_provider()` delegates to `select_provider_and_model()` from main.py. Any provider added to `cmd_model()`'s picker automatically appears in the setup wizard. The old 800-line inline provider handling was deleted in the unification refactor (#4180). **Exception:** the credential pool step (same-provider fallback & rotation) still lives in setup.py after the `select_provider_and_model()` call — changes to pool UX go here.

7. **`run_agent.py`** — Add new `api_mode` string to all switch points:
   - `__init__` api_mode detection
   - Client initialization (may need custom SDK client)
   - `_build_api_kwargs()` — format conversion
   - `_call()` in `_api_call_with_interrupt()` — API dispatch
   - Response validation (shape checking)
   - `finish_reason` extraction
   - Token usage tracking
   - Response normalization
   - Cache metrics (if provider has its own format)
   - 401 credential refresh handler
   - Memory flush path (`_flush_memories`)
   - Summary generation path (iteration limit)
   - Client interrupt/rebuild
   - **Search for ALL `self.client.` calls** — any that aren't guarded for `self.client = None` will crash

8. **`agent/model_metadata.py`** — Add context lengths for provider's models.

9. **`agent/auxiliary_client.py`** — Add default aux model to `_API_KEY_PROVIDER_AUX_MODELS`.

10. **`pyproject.toml`** — Add SDK dependency if needed.

11. **Tests** — Create `tests/test_<provider>_adapter.py`, update `tests/test_run_agent.py`.

12. **Docs** — Update `website/docs/getting-started/quickstart.md`, `website/docs/user-guide/configuration.md`, `website/docs/reference/environment-variables.md`.

### OpenAI-compatible providers (simplified path)

For providers that expose an OpenAI-compatible `/v1/chat/completions` endpoint (e.g. Hugging Face, AI Gateway, Z.AI), most of the checklist above is unnecessary. The generic `api_key` path in `runtime_provider.py` (line ~392) handles them automatically when the ProviderConfig has `auth_type="api_key"`. You only need:

1. **`auth.py`** — ProviderConfig + aliases
2. **`models.py`** — Model list, labels, aliases, provider order
3. **`main.py`** — Provider labels, `--provider` choices, dispatch (add to the `_model_flow_api_key_provider` tuple), `_PROVIDER_MODELS` dict
4. **`config.py`** — Env var entries (TOKEN + optional BASE_URL)
5. **`model_metadata.py`** — Context window entries
6. **Tests + docs**

Skip: adapter file, `runtime_provider.py` branch, `run_agent.py` changes, `pyproject.toml` deps, `auxiliary_client.py`, **setup.py** (auto-inherits from main.py since March 2026 unification).

### setup.py provider handling — UNIFIED (March 2026)

`setup_model_provider()` no longer has inline provider handling. It calls `select_provider_and_model()` (extracted from `cmd_model()` in main.py), then syncs the wizard's config dict from disk. The old 800-line `elif provider_idx == N:` chain was deleted. New providers only need to be added to main.py's `_model_flow_*` dispatch.

**Key architecture:** `cmd_model(args)` is a thin wrapper: TTY check → `select_provider_and_model()`. The setup wizard calls `select_provider_and_model()` directly (no TTY check needed since setup is always interactive). After the call, setup.py re-reads config from disk and derives `selected_provider` for downstream vision setup.

### Docs completeness audit for new providers

After adding a provider, search ALL docs pages that mention other providers and verify HF is included. The systematic approach:
```bash
search_files "kilocode|minimax|kimi-coding" path="website/docs" output_mode="files_only"
search_files "your-new-provider" path="website/docs" output_mode="files_only"
# The difference = pages you need to update
```
Commonly missed: `reference/cli-commands.md` (`--provider` choices), `reference/environment-variables.md` (`HERMES_INFERENCE_PROVIDER` list).

### Model list consistency

Provider model lists appear in TWO places that must stay in sync (setup.py was eliminated in the March 2026 unification):
- `models.py` → `_PROVIDER_MODELS` (static fallback for `hermes model` picker)
- `main.py` → `_PROVIDER_MODELS` (curated list for `hermes model` flow)

Add tests that assert both are consistent (see `TestHuggingFaceModels` in `test_api_key_providers.py` for the pattern).

### Model curation: only agentic OpenRouter analogues

When a provider has a large catalog (100+ models on their live `/models` endpoint), do NOT show all of them in the `hermes model` picker. Instead, curate a list of **only agentic models that map to OpenRouter defaults** — models that support tool calling and work well as agent backends. Non-agentic models (Gemma, base Llama, small distills, vision-only models) should NOT be in the curated list even if they're available on the provider.

The curated list should map 1:1 to models users already know from our OpenRouter default list (`OPENROUTER_MODELS` in `models.py`). Closed-source models (Claude, GPT, Gemini, Grok) obviously have no open equivalent and are excluded. Example mapping for HF:

| OpenRouter default | HF equivalent |
|---|---|
| `qwen/qwen3.5-plus` | `Qwen/Qwen3.5-397B-A17B` |
| `deepseek/deepseek-chat` | `deepseek-ai/DeepSeek-V3.2` |
| `minimax/minimax-m2.5` | `MiniMaxAI/MiniMax-M2.5` |
| `z-ai/glm-5` | `zai-org/GLM-5` |

Users who want a model outside the curated list can always pick "Enter custom model name."

Providers with 8+ curated models in `_PROVIDER_MODELS` skip the live `/models` probe entirely (`_model_flow_api_key_provider` in main.py). This keeps the picker fast and focused.

**Live probe fallback (PR #3856, March 2026):** When the live probe returns FEWER models than the curated list, the curated list is used instead. This handles providers whose Anthropic-compatible endpoints don't list all models (e.g. MiniMax's `/anthropic` endpoint didn't list M2.7). The check: `if live_models and len(live_models) >= len(curated):`.

### Key design patterns

- **api_mode abstraction**: Each provider gets a unique string (`chat_completions`, `codex_responses`, `anthropic_messages`). ALL branching in `run_agent.py` is on this string.
- **Adapter isolation**: Provider-specific logic stays in the adapter file. `run_agent.py` only calls adapter functions, never constructs provider-specific payloads inline.
- **Auth resolution chain**: env vars → credential files → auto-discovery. Always provide clear error messages when no credentials found.
- **Live model fetching**: Try the provider's API first, fall back to static `_PROVIDER_MODELS` catalog.

### Deep scan after implementation

After initial implementation, do a thorough audit:
1. **Search `self.client.`** in run_agent.py — every call must handle `self.client = None` for providers that don't use OpenAI client
2. **Cross-reference other implementations** (clawdbot, OpenCode, etc.) for subtle API quirks: empty content rejection, tool ID format requirements, required headers, token type detection
3. **Test interactively** via tmux for flows requiring PTY (like `hermes model`):
   ```bash
   tmux new-session -d -s test -x 120 -y 40
   tmux send-keys -t test "python -m hermes_cli.main model" Enter
   sleep 3 && tmux capture-pane -t test -p  # read output
   tmux send-keys -t test "4" Enter          # select option
   ```
4. **Live smoke test** with real credentials:
   ```bash
   python -m hermes_cli.main chat -q "Say hello" --provider <name> --model <model>
   ```


## Debugging Provider Endpoint / Routing Issues

When a provider returns 404 or unexpected errors, **test with the actual SDK, not just raw HTTP**. Raw HTTP requests construct URLs manually, but the OpenAI/Anthropic SDKs append paths to `base_url` — a working `curl` test does NOT prove the SDK will work.

### Systematic endpoint diagnosis pattern

```python
# 1. Raw HTTP: isolates network/auth from SDK path construction
#    Tests: does the server respond? Is the key valid?
urllib.request.Request(f"{base_url}/chat/completions", ...)

# 2. OpenAI SDK: reveals actual URL construction bugs
#    The SDK appends /chat/completions to base_url — if base_url
#    is an Anthropic endpoint (/apps/anthropic), the SDK hits
#    /apps/anthropic/chat/completions → 404
client = openai.OpenAI(api_key=key, base_url=base_url)
client.chat.completions.create(model=model, ...)

# 3. Anthropic SDK: for /apps/anthropic endpoints
#    Appends /v1/messages to base_url
client = anthropic.Anthropic(api_key=key, base_url=base_url)
client.messages.create(model=model, ...)

# 4. Always test tool calling — some endpoints accept chat but
#    reject tool schemas or return malformed tool_calls
client.chat.completions.create(model=model, tools=[...], ...)
```

### Auto-detection in runtime_provider.py (line ~408)

```python
elif base_url.rstrip("/").endswith("/anthropic"):
    api_mode = "anthropic_messages"
```

URLs ending in `/anthropic` auto-route to the Anthropic adapter. URLs ending in `/v1` stay as `chat_completions`. This means the SAME provider can work with EITHER endpoint — the base_url suffix determines the SDK used. When diagnosing 404s, always check which api_mode resolved and whether that matches the endpoint's expected format.

### Config `api_mode` overrides URL auto-detection (PR #3857, March 2026)

`runtime_provider.py` has a precedence chain for `api_mode`:
```python
configured_mode = _parse_api_mode(model_cfg.get("api_mode"))
if configured_mode:
    api_mode = configured_mode          # ← config wins if present
elif base_url.rstrip("/").endswith("/anthropic"):
    api_mode = "anthropic_messages"     # ← URL detection only runs if config absent
```

**Trap:** Any `_model_flow_*` function that writes `model["api_mode"] = "chat_completions"` to config.yaml prevents the URL-based detection from ever running. This breaks providers whose `inference_base_url` ends in `/anthropic` (MiniMax, MiniMax-CN, Alibaba) — the OpenAI SDK appends `/chat/completions` to an Anthropic endpoint → 404.

**Fix pattern:** In flows where the api_mode depends on the provider's URL format (custom endpoint, api_key_provider, Kimi), use `model.pop("api_mode", None)` instead of hardcoding. This clears any stale value from a previous provider and lets runtime auto-detection work. Flows where the mode is always known (OpenRouter → always `chat_completions`, Copilot ACP → always `chat_completions`) can keep the explicit value.

**E2E test pattern for provider switches:**
```python
# Simulate the config state after switching providers
m = {"provider": "minimax", "base_url": "https://api.minimax.io/anthropic"}
m.pop("api_mode", None)  # the fix

# Run the same resolution logic as runtime_provider.py
base_url = m.get("base_url", "")
configured = m.get("api_mode")
if configured:
    api_mode = configured
elif base_url.rstrip("/").endswith("/anthropic"):
    api_mode = "anthropic_messages"
else:
    api_mode = "chat_completions"
assert api_mode == "anthropic_messages"

# Also test with actual resolve_runtime_provider():
from hermes_cli.runtime_provider import resolve_runtime_provider
result = resolve_runtime_provider(requested="minimax")
assert result["api_mode"] == "anthropic_messages"
```

Test ALL provider combinations: Codex→MiniMax, Codex→Z.AI, Codex→custom/anthropic, OpenRouter→MiniMax, etc. The key assertion: `/anthropic` URLs get `anthropic_messages`, all others get `chat_completions`, and no stale `codex_responses` survives.

### E2E Testing Provider Resolution (Isolated HERMES_HOME)

When testing provider fallback/resolution changes end-to-end, create a fully isolated environment. The first-run setup wizard blocks non-TTY sessions, so include `_setup_done: true` and `_config_version` in the config:

```bash
# Create clean test env
mkdir -p /tmp/hermes-e2e-test
# Write config with proper YAML (use execute_code, not heredoc — avoids encoding issues)
# Must include: _config_version: 10, _setup_done: true, toolsets: [hermes-cli]
touch /tmp/hermes-e2e-test/.env

# Run in tmux PTY (input() and prompt_toolkit need a TTY)
tmux new-session -d -s e2e-test -x 120 -y 30
tmux send-keys -t e2e-test "HERMES_HOME=/tmp/hermes-e2e-test OPENROUTER_API_KEY='' OPENAI_API_KEY='' python -m hermes_cli.main chat -q 'hello'" Enter
sleep 10 && tmux capture-pane -t e2e-test -p | tail -15
```

**Pitfalls discovered:**
- `env -i` strips PATH/VIRTUAL_ENV — the command can't find Python. Use explicit env var clearing instead.
- First-run setup blocks if `_setup_done` or `_config_version` is missing from config.yaml.
- `os.environ` from the parent shell leaks API keys even with `HERMES_HOME` override — explicitly clear each key.
- YAML write errors (heredoc encoding) cause config parse failures at lines that don't exist. Use `execute_code` to write configs programmatically.

### file_tools.py — Path Blocking and Size Guard Pitfalls

**os.path.realpath defeats path-based blocklists.** On Linux, symlink chains resolve
fully: `/dev/stdin` → `/proc/self/fd/0` → `/dev/pts/0`. A blocklist containing
`/dev/stdin` won't match the resolved path `/dev/pts/0`. When blocking paths by name
(device files, sensitive dirs), use the literal input path — `os.path.expanduser()` only,
no `os.path.realpath()`. (Fixed March 2026 in `_is_blocked_device()`.)

**Line-number formatting inflates content beyond raw file size.** `read_file` prefixes
each line with `     N|` (~8 chars). A 12-byte file produces ~26 chars of output. Never
use `min(file_size, char_limit)` as the effective guard — the formatted output always
exceeds raw bytes. Use the character limit alone against the formatted content.

### Security: Tool Output Redaction Checklist

ALL tool output that enters the model context MUST go through `redact_sensitive_text()` from `agent/redact.py`. This prevents secrets read from disk (e.g. `open('~/.hermes/.env')`) from leaking to the LLM provider. The sandbox env-var filter only blocks `os.environ` access — file reads bypass it entirely.

**Currently redacted tools (as of March 2026):**
- `terminal_tool.py` line ~1168: `output = redact_sensitive_text(output.strip())`
- `file_tools.py` line ~223: `result.content = redact_sensitive_text(result.content)` (read_file)
- `file_tools.py` line ~423: `m.content = redact_sensitive_text(m.content)` (search_files)
- `code_execution_tool.py` line ~600: `stdout_text = redact_sensitive_text(stdout_text)` (added PR #4360)

**When adding new tools:** If the tool produces output that could contain user data or file contents, add `redact_sensitive_text()` before returning. The function is safe to call on any string — non-matching text passes through unchanged, and it respects the `HERMES_REDACT_SECRETS` config flag.

**Audit pattern:** Search for tools returning `json.dumps({"output": ...})` without a redact call:
```bash
search_files "json.dumps.*output" path="tools/" file_glob="*.py"
# Cross-reference with:
search_files "redact_sensitive_text" path="tools/" file_glob="*.py" output_mode="files_only"
# Any tool in the first list but not the second is potentially leaking secrets.
```

### Security Audit: Dangerous Command Detection

When auditing `tools/approval.py` patterns, systematically test all attack vectors via `detect_dangerous_command()`:

```python
from tools.approval import detect_dangerous_command
commands = [
    ("echo '...' | sudo tee /etc/docker/daemon.json", True),
    ("chmod 666 /var/run/docker.sock", True),
    ("sudo cp file /etc/docker/daemon.json", True),
    ("sudo sed -i 's/.../.../' /etc/docker/daemon.json", True),
    ("chmod 755 /home/user/script.sh", False),  # should NOT trigger
]
for cmd, should_block in commands:
    is_dangerous, key, desc = detect_dangerous_command(cmd)
    assert bool(is_dangerous) == should_block, f"FAIL: {cmd}"
```

Also audit **file_tools.py** — `write_file` and `patch` have their own `_check_sensitive_path()` guard for `/etc/`, `/boot/`, `/usr/lib/systemd/`, and docker.sock (added March 2026 after a security incident where an agent exposed Docker's Remote API). The guard checks both the raw path AND `os.path.realpath()` to catch symlinks like `/var/run` → `/run`. V4A multi-file patches extract all target paths from the patch header and check each one.

**Security patterns in approval.py:**
- `chmod` catches 777, 666, and symbolic modes (`o+w`, `a+w`, `o+rw`, `a+rw`)
- `cp`/`mv`/`install` targeting `/etc/` are detected
- `sed -i`/`--in-place` targeting `/etc/` are detected
- `tee` and `>` redirect to `/etc/` were already covered

**WhatsApp bridge gotcha:** Some Hermes features live in JavaScript (`scripts/whatsapp-bridge/bridge.js`), not Python. The reply prefix (`⚕ *Hermes Agent*\n────────────\n`) is prepended in JS, not the Python adapter. Search `.js` files when tracing WhatsApp-specific behavior that isn't in `gateway/platforms/whatsapp.py`.

### Config.yaml is single source of truth for endpoint URLs (PR #4165, March 2026)

`OPENAI_BASE_URL` env var is no longer consulted for endpoint resolution. `config.yaml` `model.base_url` is the authoritative source. Custom endpoint API keys are saved to `model.api_key` in config (PR #4202). Tests that set `OPENAI_BASE_URL` to simulate custom endpoints need updating to use `_get_model_config` mocks with `base_url` and `api_key` fields instead.

### Key finding (Alibaba, March 2026)

`coding-intl.dashscope.aliyuncs.com` and `dashscope-intl.aliyuncs.com` are TWO separate services with different keys, different model catalogs, and different endpoint paths. The `sk-sp-*` keys only work on `coding-intl`. Both have `/v1` (OpenAI-compat) and `/apps/anthropic` (Anthropic-compat) paths. The auto-detection handles both correctly.

---


## Debugging Provider Auth / Fallback Issues

When users report "auth fails" or "wrong model after fallback," trace through this chain:

### Provider Resolution Chain (`auth.py` → `runtime_provider.py`)

`resolve_provider()` (auth.py line ~659) determines which provider to use:
1. Check `active_provider` in `~/.hermes/auth.json` → call `get_auth_status(provider)` → if `logged_in: True`, use it
2. Explicit CLI `api_key`/`base_url` → `"openrouter"`
3. `OPENAI_API_KEY` or `OPENROUTER_API_KEY` env vars → `"openrouter"`
4. Auto-detect API-key providers (z.ai, kimi, minimax, kilocode, huggingface) by env vars
5. Fallback: raises `AuthError("No inference provider configured")` — NO silent OpenRouter default

**Key insight:** When an OAuth provider (Codex, Nous) has `active_provider` set but auth check fails, the code falls through to step 2+. The `active_provider` persists in auth.json even after token expiry — it doesn't get cleared on failure.

### Local Server Aliases (auth.py `_PROVIDER_ALIASES`)

Common local server names are aliased to `"custom"` so users don't get "Unknown provider" errors:
- `lmstudio`, `lm-studio`, `lm_studio` → `"custom"`
- `ollama`, `vllm`, `llamacpp`, `llama.cpp`, `llama-cpp` → `"custom"`

**Pitfall with `"local"` alias:** Do NOT add `"local"` to aliases — it conflicts with `_get_named_custom_provider()` in `runtime_provider.py`. That function calls `resolve_provider(name)` to check if a name is a built-in provider BEFORE looking up user-defined custom providers. If `"local"` resolves as a built-in (via alias), it skips the user's saved custom provider named "Local". Discovered March 2026 when adding aliases broke `test_named_custom_provider_uses_saved_credentials`.

### Provider Alias Pitfall: `"local"` conflicts with named custom providers

Do NOT add `"local"` to `_PROVIDER_ALIASES` in auth.py. It conflicts with `_get_named_custom_provider()` in runtime_provider.py, which calls `resolve_provider(name)` to check if a name is a built-in BEFORE looking up user-defined custom providers in the `custom_providers` config list. If `"local"` resolves as a built-in (via alias → "custom"), the user's saved custom provider named "Local" gets skipped silently. Discovered March 2026 when adding local server aliases broke `test_named_custom_provider_uses_saved_credentials`.

Safe aliases: `lmstudio`, `ollama`, `vllm`, `llamacpp` — these are specific enough to never collide with user-defined custom provider names.

### Config Convention: .env is Secrets-Only (March 2026 refactor)

**`.env` is for API keys (secrets) ONLY.** All other configuration — model names, base URLs, provider selection — lives exclusively in `config.yaml`. This was enforced in a 27-site cleanup that removed all `save_env_value("OPENAI_BASE_URL", ...)` calls and all `LLM_MODEL`/`HERMES_MODEL` env var reads.

**What this means for new code:**
- NEVER write non-secrets to `.env` via `save_env_value()` — use `save_config()` instead
- NEVER read `OPENAI_BASE_URL` from `os.getenv()` — read from `config.yaml` `model.base_url`
- NEVER read `LLM_MODEL`/`HERMES_MODEL` from env — read from `config.yaml` `model.default`
- `OPENAI_API_KEY` in `.env` is correct (it's a secret)
- The runtime resolver (`runtime_provider.py`) reads base_url exclusively from config

**Setup wizard config dict lifecycle (CRITICAL):** `setup_model_provider(config)` receives a `config` dict that it saves to disk at the end via `save_config(config)`. Any function called during the wizard that loads its own `cfg = load_config()`, modifies `cfg`, and saves it — WITHOUT also updating the passed `config` dict — will have its changes silently overwritten by the wizard's final save.

**March 2026 incident (#4172):** `_model_flow_custom(config)` loaded its own `cfg = load_config()`, wrote `model.provider: "custom"` and `model.base_url` to disk, but never updated the passed `config` dict. The wizard's final `save_config(config)` overwrote it with the stale default string model value. Users lost their custom endpoint after setup — stale `OPENROUTER_API_KEY` in `.env` then hijacked resolution. Fix: mutate the caller's `config` dict in both branches (model_name and no-model_name) after saving to disk. Every other provider in the wizard uses `_set_model_provider(config, ...)` which correctly updates the wizard's dict.

**Pattern to enforce:** Any function called from `setup_model_provider()` that writes config MUST also mutate the passed `config` dict. Compare: `_set_model_provider()` (correct — updates `config["model"]` in-place) vs the old `_model_flow_custom()` (broken — used its own dict).

**Test pattern:** Tests for setup wizard flows MUST call `save_config(config)` after `setup_model_provider(config)` to match the real wizard lifecycle. The existing `test_custom_setup_clears_active_oauth_provider` omitted this step, which is why the bug went undetected.

**Custom endpoint API keys live in `model.api_key` in config.yaml (March 2026, #4182).** Each custom endpoint (Together.ai, RunPod, Groq, local Ollama, etc.) stores its own API key in the `model` section of config.yaml — NOT in `OPENAI_API_KEY` env var. The runtime resolver reads `model.api_key` at `runtime_provider.py` line 224-228 (`cfg_api_key` from `model_cfg.get("api_key")`). All three custom endpoint code paths in `main.py` must save `model["api_key"] = effective_key`: `_model_flow_custom` (both branches) and `_model_flow_named_custom`. This allows unlimited custom endpoints without fighting over a shared env var — switching endpoints switches the key automatically.

**Do NOT clear stale API keys when switching providers.** When a user switches from OpenRouter to a custom local endpoint, do NOT clear `OPENROUTER_API_KEY` from `.env`. Users commonly keep an OpenRouter key for auxiliary tasks (vision via Gemini Flash, compression summaries, etc.) even when their main model runs locally. The config-based resolution (`model.provider: "custom"` wins over env var scanning) makes the stale key harmless for primary inference. Clearing it breaks auxiliary model access.

**Docker user confusion pattern:** Users see `OPENAI_BASE_URL` in `.env` and assume that's where all config lives, then set `LLM_MODEL` in `.env` expecting it to work. The CLI ignores both. Direct them to `config.yaml` for model/provider/URL config.

### Codex OAuth Specific Flow

Tokens stored in `~/.hermes/auth.json` under `openai-codex` state (NOT `~/.codex/`). Migration from `~/.codex/` happens once automatically.

`resolve_codex_runtime_credentials()` (auth.py line ~988):
1. Read tokens from auth store → `_read_codex_tokens()`
2. Check JWT expiry: if `exp < now + 300s` (5 min skew), refresh
3. Refresh via `POST https://auth.openai.com/oauth/token` with `grant_type=refresh_token`
4. Save refreshed tokens back to auth store

**Common failure modes:**
- `codex_auth_missing` — no tokens at all (never logged in, or auth.json corrupted)
- `codex_auth_missing_refresh_token` — refresh token gone from store
- `codex_refresh_failed` / `invalid_grant` — OpenAI rejected refresh (token revoked, password changed, session invalidated)
- Network timeout on refresh (20s default, configurable: `HERMES_CODEX_REFRESH_TIMEOUT_SECONDS`)

**Diagnostic commands:**
```bash
hermes doctor                    # Shows Codex auth status, last refresh, auth file path
hermes status                    # Shows logged_in state per provider
HERMES_OAUTH_TRACE=1 hermes chat # Detailed trace of token read/refresh cycle
hermes model                     # Re-authenticate by selecting a provider (triggers device code flow for OAuth providers)
```

### Model Normalization Gap During Fallback

`_normalize_model_for_provider()` (cli.py line ~1417) only works in ONE direction:
- **Forward (→ Codex):** Strips `openai/` prefix, swaps default model to `gpt-5.3-codex`
- **Reverse (Codex → OpenRouter fallback):** NO normalization happens

When Codex auth fails → falls back to OpenRouter → the model in config.yaml is still `gpt-5.3-codex` (a Codex-specific model name). OpenRouter may not recognize it, or route it unexpectedly.

**Designed solution:** The `fallback_model` config option (cli.py line ~1169):
```yaml
fallback_model:
  provider: openrouter
  model: openai/gpt-5.3-codex  # OpenRouter-compatible name
```

**Also check:** `smart_model_routing` config — this feature swaps models for "simple" turns, which users may perceive as "not using my preferred model." Ask if they have it enabled when they report the symptom.
