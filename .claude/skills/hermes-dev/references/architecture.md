# Architecture Deep Dive

Detailed architecture reference for hermes-agent internals.

## Architecture: Dual Context Compression System

Context management has TWO independent compression systems that can disagree:

### 1. Gateway Hygiene Pre-Compression (`gateway/run.py` ~line 1710)
- Runs BEFORE the agent starts, on each incoming message
- Hardcoded threshold: 85% of context length (ignores user's `compression.threshold`)
- Reads `model.context_length`, `model.provider`, `model.base_url` from config and resolves runtime provider for accurate context detection (fixed in b799bca7)
- Same 85% threshold for both actual and estimated token counts — no multiplier
- Rough estimates overestimate by 30-50% for code/JSON, so hygiene may fire a bit early (~57% actual usage) — this is safe and harmless

### 2. Agent Compressor (`agent/context_compressor.py`, wired in `run_agent.py` ~line 1028)
- Runs during the agent's tool loop, using real API-reported token counts
- Reads `model.context_length` from config (step 0 in resolution chain)
- Passes `provider` and `base_url` for accurate context detection
- Fires at the user-configured `compression.threshold` (default 50%)
- Also has preflight compression (`run_agent.py` ~line 5472) before the main loop

### Compression Persistence Pitfalls (Gateway Sessions)

When compression fires during `run_conversation()` in the gateway, two independent persistence paths must both be handled correctly or the compressed context is silently lost:

**Agent → SQLite (`_flush_messages_to_session_db`):** After `_compress_context()` creates a new session and resets `_last_flushed_db_idx = 0`, the `conversation_history` local variable in `run_conversation()` still has its original pre-compression length. The flush logic does `flush_from = max(len(conversation_history), _last_flushed_db_idx)` — if conversation_history is 200 but compressed messages is 30, `messages[200:]` is empty and nothing gets written. **Fix pattern:** Set `conversation_history = None` after every `_compress_context()` call.

**Gateway → JSONL (`history_offset`):** The gateway sets `history_offset = len(agent_history)` (pre-compression length). After compression shortened the message list, `agent_messages[200:]` is empty, causing fallback to just user/assistant pair. **Fix pattern:** Detect session splits (`agent.session_id != original_session_id`) and set `history_offset = 0`.

**E2E test pattern for compression:** Override `context_compressor.context_length` and `threshold_tokens` to small values, mock `call_llm` for summary generation and the OpenAI client for API responses, verify messages persist to SQLite and survive a round-trip through `get_messages_as_conversation()`.

### Context Length Resolution Chain (`agent/model_metadata.py`)
When debugging "wrong context length" issues, trace through these steps in order:
0. Explicit `model.context_length` in config.yaml (or `custom_providers` per-model)
1. Persistent cache (`~/.hermes/context_length_cache.yaml`)
2. Endpoint `/models` query (for non-OpenRouter custom endpoints)
3. Local server query (Ollama, LM Studio, vLLM, llama.cpp)
4. Anthropic `/v1/models` API
5. models.dev registry (provider-aware via `agent/models_dev.py`)
6. OpenRouter live API metadata
7. Hardcoded `DEFAULT_CONTEXT_LENGTHS` (fuzzy substring match)
8. Default fallback: 128K

**Critical pitfall**: OpenRouter may report a DIFFERENT context limit than the provider's native API (e.g. OpenRouter lists glm-5-turbo at 128K, z.ai's actual limit is 200K). When the user accesses a provider directly (not via OpenRouter), step 6 can return the wrong value. Always ensure `provider` and `base_url` are passed to `get_model_context_length()` so provider-aware resolution (steps 2-5) runs before the OpenRouter fallback.

**Debugging tip**: To see what Hermes detects, check the CLI startup line: `📊 Context limit: X tokens (compress at Y% = Z)`. If X is wrong, the user should set `model.context_length` explicitly in config.yaml.


## execute_code Remote Execution (April 2026)

`execute_code` (`tools/code_execution_tool.py`) spawns scripts via `subprocess.Popen([sys.executable, "script.py"])` — always on the host machine. When the user has a remote terminal backend (Docker, SSH, Modal, etc.), the script should run in the same environment.

**What already works correctly:**
- `terminal()` calls from within execute_code → RPC back to parent → `handle_function_call` → terminal_tool → uses configured backend ✓
- `read_file`, `write_file`, `search_files`, `patch` calls from within execute_code → RPC back to parent → `handle_function_call` → file_tools → already route through terminal backend via `ShellFileOperations` (file_tools.py line ~149, `_get_file_ops()` gets-or-creates the terminal environment) ✓

**What's broken:** The Python script itself runs locally. Any raw Python code (`open()`, `os.path`, etc.) operates on the host filesystem, not the remote environment.

### Why TCP RPC doesn't work (rejected approach)

The initial design proposed TCP callbacks (host binds TCP, remote script connects back). This works for Docker (`host.docker.internal`), SSH (reverse tunnel), and Singularity (shared network), but **fails for cloud backends**: Modal sandboxes and Daytona workspaces are in the cloud behind NAT — they cannot connect back to the host. Since the requirement is ALL backends, not just some, TCP was rejected.

### Correct architecture: File-based RPC through the terminal backend

The universal mechanism uses the one thing ALL backends share: `env.execute()`. Tool calls travel as files on the remote filesystem, polled by the host.

```
Host (parent process)                    Remote env (same container/sandbox)
======================                   ===================================
                                         /tmp/hermes_exec_XXXX/
_rpc_poll_loop():                          hermes_tools.py (file-based stubs)
  env.execute_oneshot("ls rpc/req_*")      script.py (user code)
  env.execute_oneshot("cat rpc/req_0001")  rpc/
  handle_function_call(...)                  req_0001 ← script writes request (atomic rename)
  env.execute_oneshot("... > rpc/res_0001")  res_0001 ← parent writes response (base64)

Thread A: env.execute("python3 script.py")  → blocks until script finishes
Thread B: _rpc_poll_loop()                  → concurrent short commands via execute_oneshot()
```

**Script-side (hermes_tools.py file-based stubs):**
- Script writes tool call request to `rpc/req_NNNN` (atomic: write to `.tmp`, then `os.rename`)
- Script polls for `rpc/res_NNNN` file (~50ms interval, 5min timeout)
- Script reads response, cleans up both files, continues

**Parent-side (polling thread):**
- Polls for request files via `env.execute_oneshot("ls rpc/req_* 2>/dev/null || true")`
- Reads request via `env.execute_oneshot("cat rpc/req_NNNN")`
- Processes through `handle_function_call()` (standard tool dispatch)
- Writes response via `env.execute_oneshot("echo 'base64...' | base64 -d > rpc/res_NNNN")`
- Poll interval ~100ms, adaptive backoff

**Local backend:** Keeps UDS (current behavior, unchanged). Only remote backends use file-based RPC.

### Backend concurrency analysis (CRITICAL)

The design requires two concurrent `env.execute()` calls: Thread A runs the script (long), Thread B polls for RPC requests (short, repeated). This was validated per-backend:

| Backend | Concurrent execute() safe? | Mechanism |
|---------|---------------------------|-----------|
| local | N/A (keeps UDS) | No change |
| docker | **Yes** | Each `docker exec` is an independent process in the container |
| ssh (persistent=False) | **Yes** | ControlMaster multiplexes concurrent SSH sessions |
| ssh (persistent=True) | **No** — `_shell_lock` serializes | Must use `execute_oneshot()` bypass |
| modal (direct) | **Yes** | Each `sandbox.exec()` is independent |
| modal (managed) | **Yes** | Independent SDK calls |
| daytona | **Yes** | Lock only held briefly for `_ensure_sandbox_ready()`, released before exec |
| singularity | **Yes** | Each `singularity exec` is independent |

**The SSH persistent mode problem:** `PersistentShellMixin._execute_persistent()` acquires `self._shell_lock` for the entire command duration. Thread A holds it for the full script run, blocking Thread B's polling calls indefinitely → deadlock.

**The fix: `execute_oneshot()` public method on BaseEnvironment:**
- `BaseEnvironment.execute_oneshot()` — default delegates to `execute()` (safe for all non-persistent backends)
- `PersistentShellMixin.execute_oneshot()` — overrides to call `_execute_oneshot()` directly, bypassing `_shell_lock`
- SSH's `_execute_oneshot()` fires a separate SSH command through ControlMaster (which natively multiplexes), fully concurrent with the persistent shell
- The RPC polling thread always uses `execute_oneshot()`, never `execute()`

### Implementation plan

**Files to modify (3 files):**
1. `tools/environments/base.py` — Add `execute_oneshot()` default method
2. `tools/environments/persistent_shell.py` — Override `execute_oneshot()` to bypass shell lock
3. `tools/code_execution_tool.py` — Major refactor:
   - Extract current local path into `_execute_local()` (preserve UDS entirely)
   - Add `_execute_remote()` with file-based RPC
   - `generate_hermes_tools_module()` gets `transport` parameter: `"uds"` vs `"file"`
   - `_rpc_poll_loop()` — polls remote filesystem, dispatches through `handle_function_call`
   - File shipping to remote via base64 through `env.execute_oneshot()`
   - Reuses existing terminal environment from `_active_environments` (same container/sandbox)
   - Same env var filtering (no API keys), 50KB stdout cap, ANSI stripping, secret redaction

**RPC latency per tool call (overhead):**
- Docker: ~100ms (docker exec round-trip)
- SSH: ~150ms (ControlMaster oneshot)
- Singularity: ~100ms
- Modal: ~400-600ms (cloud API round-trip)
- Daytona: ~300-500ms (cloud API round-trip)

Acceptable for typical 10-30 tool calls per script (adds 1-15s total, dominated by actual tool execution time).


## Debugging Gateway Errors

**CRITICAL: Check gateway logs FIRST.** When a user reports a gateway feature stopped working (tool progress, typing indicators, streaming, etc.), grep the gateway log for send errors BEFORE tracing code paths:

```bash
grep -i "failed to send\|send.*error\|edit.*error\|MarkdownV2 parse failed" ~/.hermes/logs/gateway.log | tail -30
```

This single command would have saved 20+ tool calls in the March 2026 tool progress investigation. The root cause (252 "Message thread not found" errors) was immediately visible in the logs, but wasn't checked until deep into the code trace.

### python-telegram-bot exception hierarchy gotcha

`telegram.error.BadRequest` inherits from `telegram.error.NetworkError`. Any `except NetworkError` catch block will also catch `BadRequest` — which is a PERMANENT error, not a transient network issue. This caused the Telegram adapter's retry loop to retry "Message thread not found" 3 times instead of failing fast, silently killing progress messages. Always check `isinstance(err, BadRequest)` inside `NetworkError` handlers and handle permanent vs transient errors differently.

### Shared module extraction: update test mock targets

When extracting duplicated logic into a shared module (e.g. `agent/skill_utils.py`), existing tests that mock the old location (`patch("tools.skills_tool.sys")`) will silently stop working — the mock patches the OLD module's reference while the real code now reads from the NEW module. Tests pass but the mock has no effect, causing false positives or flaky failures depending on the host's actual `sys.platform`. Always grep for ALL `patch("old_module.thing")` references in tests and update them to `patch("new_module.thing")`.

### Setup wizard test pitfall: _stub_tts overwrites prompt_choice

`_stub_tts(monkeypatch)` in `test_setup_model_provider.py` patches `hermes_cli.setup.prompt_choice` with a generic lambda. If your test needs a custom `prompt_choice` mock (e.g., to return a specific strategy index), call `_stub_tts` BEFORE your custom mock — otherwise it silently overwrites your mock and everything returns `default`. This caused a multi-hour debugging loop in March 2026 where pool strategy tests returned `fill_first` instead of `round_robin`.

Also: setup wizard tests must pre-write `model.provider` to config.yaml via `_write_model_config()` and mock `select_provider_and_model` as a no-op, since the wizard now delegates to that unified flow. The pool step only runs if `selected_provider` is derived from the persisted config after the select call.

### Common silent-failure patterns in gateway sends

When messages "stop showing" in Telegram but responses still arrive, check for:
- **Invalid `message_thread_id`:** `source.thread_id` can be set from Telegram's `message.message_thread_id` for DMs with topics. If the thread doesn't exist, sends fail with BadRequest but the error gets swallowed by retry logic or broad exception handlers. The streaming consumer and base adapter may use different error paths, so responses may still arrive while progress/typing fails.
- **MarkdownV2 formatting failures:** Progress messages with `[](){}` chars (common in verbose mode tool args) can trigger parse errors. The adapter falls back to plain text, but check if both attempts fail.

When a user reports an error from Telegram/Discord/etc., trace the issue through session files and gateway logs:

### 1. Find the session file

Session files live in `~/.hermes/sessions/`. Gateway sessions have `"platform": "telegram"` (or discord, slack, etc.). Use grep to narrow down:

```bash
# Search by error text, user input, or keywords
grep -rl "specific error text" ~/.hermes/sessions/ | head
# Then check platform and timestamp
python3 -c "import json; d=json.load(open('sessions/session_XXXX.json')); print(d.get('platform'), d.get('session_id'), len(d['messages']))"
```

**Session file format:** JSON dict with keys: `session_id`, `model`, `base_url`, `platform`, `session_start`, `last_updated`, `system_prompt`, `tools`, `message_count`, `messages` (list of OpenAI-format message dicts).

**Gotcha:** Many CLI sessions with `browser_` in them are subagent sessions spawned BY the gateway — the gateway runs `AIAgent` in a ThreadPoolExecutor thread, so the actual agent session may show `platform=cli` even though it originated from Telegram. Search by content (error text, user inputs, tool names) rather than platform alone.

### 2. Cross-reference with gateway logs

Gateway logs: `~/.hermes/logs/gateway.log` (plus rotated `.1`, `.2`, `.3`). Error logs: `~/.hermes/logs/errors.log`.

```bash
# Find the traceback by error text
grep -n "specific error" ~/.hermes/logs/gateway.log
# Then read surrounding context (timestamps, session IDs)
sed -n '<start>,<end>p' ~/.hermes/logs/gateway.log
```

The gateway log includes full Python tracebacks with file paths and line numbers. Match timestamps between the log and the session messages to pinpoint exactly when the error occurred.

### 3. Check if the fix already exists but wasn't deployed

A common pattern: the fix was already committed to `main` but the gateway wasn't restarted. Check `git log` for the fix commit time vs. the error time vs. the last gateway restart (search for "Starting Hermes Gateway" in the gateway log).


## Gateway: Crash Forensics & Restart Rules

The gateway is managed by a background service (`hermes-gateway`). Agents should always use `systemctl --user restart hermes-gateway` to restart it — NEVER `kill <PID>` followed by `gateway run &disown`. Starting the gateway outside the service manager breaks automatic restarts, which is how a 7-hour outage happened in March 2026. A dangerous command pattern in `tools/approval.py` now blocks `gateway run` with backgrounding operators (`&`, `disown`, `nohup`, `setsid`).

### Forensic workflow: "Gateway died, why?"

1. Check process: `ps aux | grep gateway`, then `systemctl --user status hermes-gateway` (PID, exit code, duration)
2. Check `gateway.log`: search for `"Stopping gateway..."` — clean shutdown means SIGTERM was received. Check what happened just before
3. Check `~/.hermes/gateway_state.json` — has PID, argv (service vs rogue script?), platform states, exit reason
4. Check for OOM: `dmesg | grep -i oom`, memory watchdog log
5. Trace agent actions in session files (`~/.hermes/sessions/`) — search for `gateway`, `kill`, `systemctl` in tool call arguments to find if an agent killed/restarted the gateway
6. If `gateway_state.json` shows unexpected argv (e.g. `tmp_*.py`), an agent likely started the gateway outside the service


## Submodules & External Dependencies

### `__pycache__` Bytecode Pitfall in Update Flow

The `hermes update` command (both git and ZIP paths) must clear `__pycache__` directories
after pulling new code. Stale `.pyc` files cause `ImportError` when updated source references
names that didn't exist in the old bytecode (e.g. `get_hermes_home` added to `hermes_constants`).

**March 2026 incident:** Three separate user reports of gateway crashing on restart with
`ImportError: cannot import name 'get_hermes_home' from 'hermes_constants'`. Root causes:
1. Git update path: zero `__pycache__` cleanup after `git pull`
2. ZIP update path: `__pycache__` was explicitly in the `preserve` set — stale bytecode intentionally kept

Fixed by `_clear_bytecode_cache(PROJECT_ROOT)` in both paths (PR #3819). The function walks
the project root, removes all `__pycache__` dirs (skipping venv/node_modules/.git/.worktrees).
Python recompiles from `.py` source on next import.

**If you touch the update flow:** ensure `__pycache__` clearing happens after code update and
before `pip install -e .` in both git and ZIP paths.

### Tool Integration UX Convention (CRITICAL)

When adding new tool backends (browser backends, API integrations, etc.), they MUST be wired
through the full setup flow — not just an env var. Teknium's explicit requirement:

1. **`package.json`** — Auto-installed with `npm install` during setup (for Node.js deps)
2. **`hermes_cli/tools_config.py`** — Shows in `hermes tools` curses UI as a selectable
   provider with `post_setup` hook for auto-installation
3. **`hermes_cli/setup.py`** — Shows in `hermes setup` tool status display
4. **`hermes_cli/config.py`** — `OPTIONAL_ENV_VARS` entry for the env var

Users should be able to select and configure new backends through `hermes tools` without
touching env files manually. The post_setup hook handles npm install, and the tools_config
provider entry handles URL/key prompting with defaults.

**Anti-pattern:** Adding `SOME_URL` to OPTIONAL_ENV_VARS and telling users to set it manually.
This was rejected twice during Camofox browser backend review (PR #4008, March 2026).

### Browser Tool SSRF & Camofox E2E Testing

**SSRF bypass for local backends (PR #4292, March 2026):**
`browser_navigate()` has SSRF protection that blocks private/internal URLs. This is only meaningful for cloud backends (Browserbase, BrowserUse) — local backends (Camofox, headless Chromium without cloud provider) skip SSRF via `_is_local_backend()`. Both pre-navigation and post-redirect checks use this helper. The `browser.allow_private_urls` config option remains as an explicit opt-out for cloud mode.

**E2E testing Camofox with Docker:**

1. **Build from source** — no pre-built Docker Hub image exists:
   ```bash
   git clone https://github.com/jo-inc/camofox-browser /tmp/camofox-browser
   cd /tmp/camofox-browser && docker build -t camofox-browser .
   ```

2. **Use `--network host`** — the Dockerfile defaults to port 3000, and the browser inside Docker needs to reach host localhost services. Port mapping (`-p 9377:3000`) makes `127.0.0.1` inside Docker point to the container's own loopback, not the host:
   ```bash
   docker run -d --name camofox-test --network host -e CAMOFOX_PORT=9377 camofox-browser
   curl -s http://127.0.0.1:9377/health  # verify
   ```

3. **Reset cached module state** when testing browser_tool in `execute_code`:
   ```python
   browser_tool._cloud_provider_resolved = False
   browser_tool._cached_cloud_provider = None
   browser_tool._allow_private_urls_resolved = False
   browser_tool._cached_allow_private_urls = None
   ```

4. **Test pattern**: start a local HTTP server on a different port, navigate to it via Camofox, snapshot to confirm content, then simulate cloud mode (`_is_local_backend = lambda: False`) to verify SSRF would block the same URL.

### mini-swe-agent — FULLY REMOVED (March 2026)
mini-swe-agent was removed as a dependency in PR #2804 and all references cleaned in follow-up commits. The Docker backend now runs `docker run -d` directly (~20 lines inline in `tools/environments/docker.py`). The Modal backend imports `swe-rex`'s `ModalDeployment` directly with a built-in `_AsyncWorker` for Atropos async-safety (already in `[modal]` extras). `mini_swe_runner.py` was updated to use hermes-agent's own backends — it no longer imports from minisweagent at all. No part of the codebase depends on mini-swe-agent anymore. The `MSWEA_*` env vars, logger suppressions, and doctor health checks were all removed.

**Two install scripts exist** that duplicate dep installation logic and must stay in sync:
- `scripts/install.sh` (1140 lines) — curl-pipe installer for new users, clones repo from scratch
- `setup-hermes.sh` (305 lines) — lightweight script for devs who already cloned manually

### Remote Backend Credential Architecture

Two passthrough systems exist for getting credentials into remote sandboxes:

1. **Env passthrough** (`tools/env_passthrough.py`): Skills declare `required_environment_variables`, which get registered and forwarded to local terminal, execute_code, AND Docker (merged into `forward_env` at exec time). Modal env passthrough is not yet implemented.

2. **Credential files** (`tools/credential_files.py`): Skills declare `required_credential_files` (paths relative to `HERMES_HOME`). Files are mounted into Docker (read-only bind mounts) and Modal (mount at creation + sync via exec before each command with mtime+size caching). User can also list files in `terminal.credential_files` config.

**Key gap found (March 2026):** Docker's `forward_env` was completely disconnected from `env_passthrough` — skills that registered vars weren't forwarded to Docker containers. Fixed by merging `get_all_passthrough()` into the Docker exec `-e` flags.

**Modal sync design:** Modal mounts are set at sandbox creation and can't be updated. For mid-session OAuth setup, `_sync_credential_files()` pushes file content via `Sandbox.exec()` + base64. Cached by `(mtime, size)` — ~13μs overhead per command in the no-op case.

### Testing container terminal backends (Docker, Modal)

The terminal backend config has a multi-layer resolution chain that causes common testing mistakes:

1. Config key precedence: `terminal.backend` takes priority over `terminal.env_type` (legacy key). The CLI normalizes backend to env_type at load time. The config.yaml may have BOTH keys — always change `backend`, not `env_type`.
2. Config overrides env vars: `load_cli_config()` writes config values to TERMINAL_ENV in the process environment. Config.yaml is authoritative when a terminal section exists, so setting TERMINAL_ENV on the command line does NOT work — the config loader overwrites it.
3. Relative cwd breaks containers: Values like "." in TERMINAL_CWD pass through to `docker run -d -w .` which is invalid. Fixed in March 2026 — `_get_env_config()` now catches relative paths alongside host paths for container backends.
4. The default nikolaik/python-nodejs image is Ubuntu-based: it shows Ubuntu in /etc/os-release, making it look like commands are running on the host. Use `hostname` (returns container ID) or python:3.11-slim (shows Debian) to distinguish.

To test Docker through the CLI: change `backend: docker` in config.yaml (and optionally docker_image to a cached small image). Do not just set env vars — they get overwritten.

To test the Docker backend directly (unit-test style), call `terminal_tool()` after setting the env vars in Python. The hostname output will be a container ID, confirming container execution.

Always verify container creation with `docker ps -a --filter "name=hermes"` after testing. If no new container appeared, the backend silently fell back to local (check config precedence above).

### tinker-atropos (git submodule)
Optional RL training backend at `tinker-atropos/`. Not initialized by default — users opt in with `git submodule update --init tinker-atropos`.


## Competitor Codebase Reference for Model Behavior Issues

When debugging model behavioral issues (not sending tools, describing actions instead of acting, etc.), check how competing agent codebases handle the same models. Reference codebases live at `~/agent-codebases/`:

### OpenCode (`~/agent-codebases/opencode/`)
- **Model-specific system prompts** in `packages/opencode/src/session/prompt/`: 6 variants selected by model ID in `session/system.ts`
  - `beast.txt` (GPT-4/o1/o3): Extremely aggressive tool-use nudging — "when you say you are going to make a tool call, make sure you ACTUALLY make the tool call, instead of ending your turn"
  - `codex.txt` (GPT-5+): Calmer, more structured — they trust GPT-5 models more
  - `anthropic.txt`, `gemini.txt`, `default.txt`, `trinity.txt` for other families
- **Responses API** for OpenAI (not Chat Completions via OpenRouter)
- **textVerbosity: "low"** for GPT-5 models (reduces chattiness)
- **reasoningEffort: "medium"** default for GPT-5, with reasoningSummary: "auto"
- Provider-specific transform logic in `provider/transform.ts`

### Cline (`~/agent-codebases/cline/`)
- **Model family enum** in `src/shared/prompts.ts`: GENERIC, NEXT_GEN, GPT_5, NATIVE_GPT_5, NATIVE_GPT_5_1, etc.
- **tool_choice: "any"** for Anthropic (FORCES tool use) vs **"auto"** for OpenAI — key behavioral difference
- **parallel_tool_calls: false** by default for OpenAI
- **Developer role** (`role: "developer"`) instead of `system` for OpenAI reasoning models (o1/o3/o4/gpt-5)
- **GPT-5.1 variant**: Explicit "Tool-Calling Convention and Preambles" — "You always respond using tools"
- **Simplified rules** for GPT-5 (3 rules vs 20+ for generic) — they trust it more with fewer constraints
- Tool specs per model family in `src/core/prompts/system-prompt/tools/`

### Key Pattern: GPT "Promise Instead of Act"
Both codebases independently discovered that GPT models describe intended actions as text instead of making tool calls. OpenCode's fix: aggressive repetitive instructions. Cline's fix: model-family-specific prompt variants with tool-use mandates. We added `GPT_TOOL_USE_GUIDANCE` in PR #3479.

### Budget Warning History Poisoning (PR #3479)
Budget pressure warnings (`[BUDGET WARNING: ... No more tool calls]`) injected into tool result content persisted in conversation history across turns. When replayed via the gateway, models (especially GPT) complied with the stale instruction and avoided tools in ALL subsequent turns. Fixed with `_strip_budget_warnings_from_history()` in run_conversation().


## HERMES_HOME Scoping: Profile-Readiness Rules

119+ files reference `get_hermes_home()` from `hermes_constants.py`. The profile system
(planned) makes each agent a separate HERMES_HOME directory. All path references MUST
use `get_hermes_home()`, never hardcoded `~/.hermes` or `Path.home() / ".hermes"`.

**Known pitfall patterns (caught and fixed in PR #3575):**
- `Path.home() / ".hermes" / "config.yaml"` — WRONG, bypasses HERMES_HOME
- `_pathlib.Path("~/.hermes").expanduser()` — WRONG, ignores env var
- `os.environ.get("HERMES_HOME", Path.home() / ".hermes")` — WORKS but inconsistent with `get_hermes_home()`

**35 module-level constants** cache `get_hermes_home()` at import time (SKILLS_DIR,
MEMORY_DIR, CRON_DIR, DEFAULT_DB_PATH, etc.). These are safe because
`_apply_profile_override()` runs BEFORE any hermes module imports in main.py.
If you add new module-level path constants, use `get_hermes_home()` not `Path.home()`.

### Consolidated Directory Layout (`get_hermes_dir()`)

New subsystems should use `get_hermes_dir()` from `hermes_constants.py` instead of
`get_hermes_home() / "some_dir"`. This helper provides backward-compatible directory
consolidation:

```python
from hermes_constants import get_hermes_dir

# New installs get: ~/.hermes/cache/images/
# Existing installs keep: ~/.hermes/image_cache/ (if it exists on disk)
IMAGE_CACHE_DIR = get_hermes_dir("cache/images", "image_cache")
```

**Current consolidated layout (new installs):**
```
~/.hermes/
├── cache/
│   ├── images/        (was image_cache/)
│   ├── audio/         (was audio_cache/)
│   ├── documents/     (was document_cache/)
│   └── screenshots/   (was browser_screenshots/)
├── platforms/
│   ├── whatsapp/session/  (was whatsapp/session/)
│   ├── matrix/store/      (was matrix/store/)
│   └── pairing/           (was pairing/)
```

**Convention for new directories:** Use `cache/` for transient data (safe to delete),
`platforms/` for platform runtime state (auth, sessions). The helper checks `old_name`
path existence at import time — if the old path exists, it returns that; otherwise the
new consolidated path. Zero migration needed.

**Service naming** uses `_profile_suffix()` in `hermes_cli/gateway.py`:
- Default `~/.hermes` → `hermes-gateway` / `ai.hermes.gateway` (backward compat)
- Profile `~/.hermes/profiles/coder` → `hermes-gateway-coder` / `ai.hermes.gateway-coder`
- Custom path → hash suffix fallback

**Gateway stop/restart is profile-scoped** (April 2026, commit ad4feeaf):
- `hermes gateway stop` → only kills the current profile's gateway (via PID file)
- `hermes gateway stop --all` → kills every gateway process globally (old behavior)
- `hermes gateway restart` (manual fallback) → profile-scoped via `stop_profile_gateway()`
- `hermes update` → discovers and restarts ALL profile gateways (systemctl list-units hermes-gateway*) since git pull updates shared code
- The old `find_gateway_pids()` scans `ps aux` globally — NEVER use it for profile-scoped operations. Use `get_running_pid()` from `gateway/status.py` (reads profile-scoped PID file) instead.
- `stop_profile_gateway()` in `hermes_cli/gateway.py` is the profile-safe stop function.

**External skill directories** (April 2026, commit ad4feeaf):
Config key `skills.external_dirs` (list of paths) was already defined but not wired into all discovery paths. Now fully supported across: `_get_category_from_path()`, `skills_categories()`, `skill_manager_tool._find_skill()`, `credential_files.get_skills_directory_mount()` (returns list of mounts), `iter_skills_files()`, gateway `_check_unavailable_skill()`, and all remote backends (Docker, Singularity, SSH). External dirs mount at `external_skills/<idx>` inside containers. When updating discovery functions that use the module-level `SKILLS_DIR`, prefer `[SKILLS_DIR] + get_external_skills_dirs()` over `get_all_skills_dirs()` to respect test monkeypatching of `SKILLS_DIR`.

**Test pattern:** Tests that mock `Path.home()` for path redirection must ALSO set
`HERMES_HOME` env var via `patch.dict(os.environ, {"HERMES_HOME": str(tmp_path / ".hermes")})`,
since code now uses `get_hermes_home()` not `Path.home()/.hermes`.

**Deep scan methodology** for finding hardcoded paths:
```bash
# Module-level constants caching HERMES_HOME
search_files "^[A-Z_]*\s*=.*get_hermes_home|^[A-Z_]*\s*=.*HERMES_HOME" file_glob="*.py"
# Hardcoded ~/.hermes paths (potential leaks)  
search_files 'Path\.home\(\).*\.hermes|"~/.hermes"|expanduser.*\.hermes' file_glob="*.py"
# Hardcoded launchd/systemd labels
search_files '"ai\.hermes\.gateway"' path="hermes_cli"
```

**sync_skills() module-level caching gotcha:** `tools/skills_sync.py` caches `SKILLS_DIR`
and `MANIFEST_FILE` at module level. Once imported, changing `os.environ["HERMES_HOME"]`
has NO effect. For cross-profile skill operations (profile create, hermes update syncing
all profiles), use subprocess with `env={"HERMES_HOME": str(target_dir)}` to get fresh
module-level constants in a new Python process.

**HERMES_HOME env propagation through subprocesses:**
- Terminal tool (local): ✓ passes through (`os.environ`, not in provider blocklist)
- Terminal tool (docker): ✗ by design (container isolation)
- execute_code sandbox: ✗ dropped by whitelist filter (hermes_tools uses RPC back to parent)
- delegate_task: ✓ same process, same os.environ
- MCP servers: ✗ dropped by whitelist (don't need hermes state)
- Cron jobs: ✓ gateway process env cached at module level
- Background processes: ✓ spawned by terminal_tool, inherits env

**Profiles implementation (PR #3681, merged):**
Pre-work PRs: #3575 (code paths → get_hermes_home), #3623 (display paths → display_hermes_home).
Core module: `hermes_cli/profiles.py` (~900 lines). Tests: `tests/hermes_cli/test_profiles.py` (71 tests).
Docs: `website/docs/user-guide/profiles.md`, `website/docs/reference/profile-commands.md`.
AGENTS.md: "Profiles: Multi-Instance Support" section (6 rules for profile-safe code).
Contributing docs: profile-safe paths bullet in Code Style section.

Token lock pattern for gateway adapters: ALWAYS store lock identity at acquire time on
`self._token_lock_identity`, use that stored value for release, clear after release.
Do NOT re-read from env vars or config at disconnect time — the value may differ from
what was used to acquire. Community reviewer caught this for Slack (re-read os.getenv),
fixed in follow-up commit. Telegram had the correct pattern from the start.

Clone bug pattern: when `create_profile()` accepts both `clone_from` (source name) and
`clone_config` (bool flag), the condition to resolve source_dir must check ALL three
triggers: `if clone_from is not None or clone_all or clone_config`. Originally missed
`clone_config`, causing `--clone` without `--clone-from` to skip file copying. Found
during live E2E testing.

Key implementation decisions:
- `_apply_profile_override()` in main.py runs before ANY module imports, sets HERMES_HOME from
  `-p` flag → `active_profile` file → default. All exceptions caught — never prevents startup.
- `sync_skills()` via subprocess for cross-profile operations (module-level SKILLS_DIR caching)
- `_get_profiles_root()` anchored to `Path.home()` not HERMES_HOME (prevents nesting)
- Auto-stop gateway on profile delete: disable service → stop process → remove service file
- Wrapper scripts at `~/.local/bin/<name>` with collision detection against subcommands/binaries
- Three clone levels: blank, `--clone` (config), `--clone-all` (full copytree minus runtime files)
- Export/import via tar.gz, rename with full service/alias/active_profile cleanup
- Tab completion: `hermes completion bash/zsh`
- Doctor health: profile section + orphan alias detection
- Token locks for Discord/Slack/WhatsApp/Signal (extends Telegram pattern)
- Port conflict detection for API Server/Webhook (socket probe before bind)

AGENTS.md updated with "Profiles: Multi-Instance Support" section (6 rules for profile-safe code)
and "DO NOT hardcode ~/.hermes paths" pitfall. Contributing docs updated with profile-safe paths
bullet in Code Style section.


## Bulk Code Extraction from Critical Files

When removing hundreds of lines of deeply integrated code from large files (run_agent.py
at 8400+ lines), individual patches are too risky — fuzzy matching can delete wrong blocks.

### Pattern: programmatic replacement via execute_code

```python
# Read the file directly (not via read_file which adds line prefixes)
with open(path) as f:
    content = f.read()

# Use regex to remove entire method blocks
pattern = re.compile(
    rf'    def {method_name}\(.*?(?=\n    def |\n    @|\nclass )',
    re.DOTALL
)
content = pattern.sub('', content)

# Write and compile-check after each chunk
with open(path, 'w') as f:
    f.write(content)
terminal(f"python -m py_compile {path}")
```

### Pitfall: adjacent collateral damage
Regex-based method removal can sweep up constants/variables defined BETWEEN methods.
In PR #4154, removing `_inject_honcho_turn_context()` accidentally deleted `_SURROGATE_RE`
and `_BUDGET_WARNING_RE` constants that were defined between the removed function and the
next one. **Always grep for NameError after bulk removal** — compile success doesn't catch
runtime missing names.

### Chunk strategy
1. Constants/sets at file top (safe, few dependencies)
2. __init__ parameter removal (check all callers — gateway, tests, CLI)
3. Method blocks (regex, one at a time, compile between)
4. Call site cleanup (replace with no-ops or remove lines)
5. Test file cleanup (remove tests for removed methods)
6. Cross-file cleanup (gateway, model_tools, toolsets)

After each chunk: `py_compile`, then `pytest` for the affected test files before moving on.
Always do a final full-suite run at the end.


## Memory Provider Plugin System (PR #4154)

### Architecture

```
agent/memory_provider.py         — MemoryProvider ABC
agent/memory_manager.py          — Orchestrator (builtin + ONE external)
agent/builtin_memory_provider.py — Wraps MEMORY.md/USER.md
plugins/memory/<name>/           — Plugin directories (7 shipped)
plugins/memory/__init__.py       — Discovery: discover_memory_providers(), load_memory_provider()
hermes_cli/memory_setup.py       — CLI wizard: hermes memory setup/status/off
```

### Key design decisions

- **One external provider at a time** — `MemoryManager.add_provider()` rejects a second non-builtin provider. Prevents tool schema bloat.
- **Dedicated discovery** — `plugins/memory/__init__.py` scans the directory directly, NOT through the general `~/.hermes/plugins/` system. Memory plugins ship with the repo.
- **Native config** — Each provider writes config to its own format via `save_config()`. Only `memory.provider` goes in config.yaml. Don't fight upstream SDK config conventions.
- **`initialize()` receives `hermes_home` kwarg** — MemoryManager injects it so plugins resolve profile-scoped paths.
- **All sync_turn must be non-blocking** — threaded, with previous-sync join before starting new one.
- **`hermes memory setup` auto-installs pip deps** — reads `pip_dependencies` from `plugin.yaml`, checks via import, installs missing.

### Plugin directory structure

```
plugins/memory/<name>/
├── __init__.py      — MemoryProvider + register(ctx) entry point
├── plugin.yaml      — name, description, pip_dependencies, hooks
├── README.md        — setup, config, tools reference
└── (optional)       — supporting modules (client.py, session.py, etc.)
```

### Required methods for new plugins

- `name` (property), `is_available()`, `initialize()`, `get_tool_schemas()`, `handle_tool_call()`
- `get_config_schema()` — fields for the setup wizard (REQUIRED)
- `save_config(values, hermes_home)` — write native config (REQUIRED unless env-var-only)

### run_agent.py integration (8 additive hooks)

Init → tool injection → system prompt → tool routing → memory write bridge → pre-compress → prefetch (into user message) → turn end sync/shutdown. All wrapped in try/except.

### Feature extraction pattern (Honcho case study)

When extracting a deeply integrated feature into a plugin:
1. Create the plugin adapter first (thin wrapper over existing code)
2. Wire the new path (MemoryManager) alongside the old path
3. Remove the old path from run_agent.py, gateway, CLI, toolsets
4. Move supporting modules (client.py, session.py) INTO the plugin directory
5. Update all imports (production + tests)
6. Remove the old package from pyproject.toml
7. Auto-migration: detect old config (check `enabled AND credentials`, not just file existence), auto-set new config, persist once

### Pitfalls discovered

- **pip name ≠ import name**: `honcho-ai→honcho`, `mem0ai→mem0`. Need explicit mapping in dep checker.
- **Module-level regex constants near removed functions**: Regex removals can accidentally eat adjacent constants (`_SURROGATE_RE`, `_BUDGET_WARNING_RE`). Always compile-check after each removal.
- **Gateway has separate Honcho managers**: Gateway shared session managers across messages. Plugin creates per-AIAgent instances. Functionally correct (idempotent) but less efficient.
- **Dead argparse subparsers**: When replacing a CLI command with a redirect, strip the old subparsers too — they waste 80+ lines.
- **`getattr(agent, '_honcho', None)` scattered in cli.py**: After removing agent attributes, grep ALL files, not just run_agent.py. cli.py and gateway had crash-risk references.


## Credential Pool System (PR #4188)

Same-provider credential pooling lives in `agent/credential_pool.py`. Key architecture:

- `CredentialPool` manages per-provider lists of `PooledCredential` entries
- Strategies: `fill_first` (default), `round_robin`, `random`, `least_used`
- Stored in `auth.json` under `credential_pool` key
- Auto-seeds from env vars (`_seed_from_env`) and file-backed OAuth (`_seed_from_singletons`) on every `load_pool()` call
- CLI: `hermes auth add/list/remove/reset`
- Config: `credential_pool_strategies` in config.yaml
- Thread safety: `threading.Lock` on `select()`, `mark_exhausted_and_rotate()`, `try_refresh_current()`
- Internal `_*_unlocked()` variants prevent deadlock when locked methods call each other

### Credential Pool Test Pitfall (CRITICAL)

`load_pool()` auto-seeds from host env vars and file-backed OAuth credentials on every call. This means:

- Tests that mock `resolve_anthropic_token` or `resolve_codex_runtime_credentials` can be **silently bypassed** if the pool path runs first (`_select_pool_entry` returns a pool entry instead of falling through to the mocked function)
- Tests that write specific entries to auth.json may get **extra entries** injected by auto-seeding if host env vars like `ANTHROPIC_API_KEY` or file-backed OAuth tokens exist

**Fix patterns:**
```python
# For tests that don't want pool behavior:
patch("agent.auxiliary_client._select_pool_entry", return_value=(False, None))

# For tests that need isolated pool state:
monkeypatch.setattr("agent.credential_pool._seed_from_singletons", lambda p, e: (False, set()))
monkeypatch.setattr("agent.credential_pool._seed_from_env", lambda p, e: (False, set()))
monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
```

This caused 3 test failures when salvaging PR #2647 — the PR author's CI had no Anthropic credentials, so the auto-seeding was invisible to them.

### E2E Testing Credential Pools

Test the full lifecycle from a script with `PYTHONPATH=.`:
```bash
cd <worktree> && source .venv/bin/activate && PYTHONPATH=. python3 /tmp/test_pool.py
```
The script must call `dotenv.load_dotenv(HERMES_HOME/.env)` to match real CLI behavior — without it, env var seeding won't find any keys.

Key assertions: `load_pool()` returns entries, `select()` picks one, `mark_used()` increments `request_count`, `mark_exhausted_and_rotate(status_code=429)` picks a different key, exhausting all keys returns `None`, `resolve_runtime_provider()` returns `credential_pool` in its result dict.
