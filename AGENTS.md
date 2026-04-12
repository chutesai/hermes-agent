# Hermes Agent - Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.

## Development Environment

```bash
source venv/bin/activate  # ALWAYS activate before running Python
```

**Development skill:** Detailed workflows for PR review, bug fixing, cherry-pick salvage, and E2E testing are in `.claude/skills/hermes-dev/SKILL.md` with deep-dive references in `.claude/skills/hermes-dev/references/`.

---

## Python Coding Standards

This codebase does not yet have automated linting (ruff, black, flake8) or type checking (mypy, pyright). These standards must be followed manually until tooling is added. Ruff and type checks are planned for the future.

### Style

- **Line length:** 120 characters max. Exceptions for long strings, URLs, or data.
- **Indentation:** 4 spaces. Never tabs.
- **Quotes:** Double quotes (`"string"`) for all strings. Single quotes only inside f-strings or to avoid escaping.
- **Trailing commas:** Always use trailing commas in multi-line data structures (lists, dicts, function args, imports). This makes diffs cleaner.
- **Blank lines:** 2 blank lines before top-level definitions (classes, functions). 1 blank line between methods. No trailing whitespace.

### Imports

Organize imports in three groups separated by blank lines:

```python
# 1. Standard library
import json
import os
from pathlib import Path

# 2. Third-party packages
import openai
from rich.console import Console

# 3. Local/project imports
from hermes_constants import get_hermes_home
from tools.registry import registry
```

**Rules:**
- Absolute imports only — no relative imports (`from . import foo`)
- No wildcard imports (`from module import *`)
- One import per line for `from X import Y` when importing multiple names
- Group `from` imports after `import` imports within each section
- Stdlib `from __future__ import annotations` goes at the very top if used

### Type Hints

- **Required** on all function/method signatures (parameters and return types)
- **Optional** on local variables — use when it improves clarity
- Use `str | None` syntax (Python 3.10+), not `Optional[str]`
- Use `list[str]`, `dict[str, int]` lowercase generics (Python 3.9+)
- For complex types, define type aliases at module level

```python
# Good
def resolve_provider(name: str, fallback: str | None = None) -> dict[str, str]:
    ...

# Bad — missing hints
def resolve_provider(name, fallback=None):
    ...
```

### Naming Conventions

| Entity | Convention | Example |
|--------|-----------|---------|
| Functions, methods, variables | `snake_case` | `resolve_provider()`, `api_key` |
| Classes | `PascalCase` | `AIAgent`, `SessionDB` |
| Constants | `UPPER_SNAKE_CASE` | `DEFAULT_CONFIG`, `MAX_ITERATIONS` |
| Private/internal | `_leading_underscore` | `_build_api_kwargs()`, `_cache` |
| Modules, packages | `snake_case` | `model_tools.py`, `hermes_cli/` |
| Test functions | `test_<what>_<scenario>` | `test_resolve_provider_missing_key` |

### Error Handling and Defensive Programming

**The #1 rule: don't write defensive code.** This codebase values crashes over silent misbehavior. A crash produces a traceback that points directly at the problem. Defensive fallbacks hide bugs behind "reasonable defaults" that make the system silently do the wrong thing for days.

**Let errors propagate.** Only catch an exception when you have a *specific, concrete recovery action* — not just to log and re-raise, not just to wrap in a friendlier message, not just to "be safe."

**Enforce conventions, don't guess around them:**

```python
# Good — fail immediately on bad input
if "__" not in tool_name:
    raise ValueError(f"Tool name must be 'server__tool' format, got: {tool_name}")
server, tool = tool_name.split("__", 1)

# Bad — silently guesses, hides the real problem
if "__" in tool_name:
    server, tool = tool_name.split("__", 1)
else:
    for srv in servers:  # silent fallback search
        if tool_name in srv:
            server = srv
            break
```

**Catch specific exceptions, keep handlers minimal:**

```python
# Good — one handler, one message
try:
    subprocess.run(cmd, check=True, timeout=300)
except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
    raise RuntimeError(f"Failed to run command: {e}") from e

# Bad — each exception gets a custom essay
try:
    subprocess.run(cmd)
except FileNotFoundError:
    raise RuntimeError("command not found. Install via: brew install ...")
except subprocess.TimeoutExpired:
    raise RuntimeError("timed out. Check your internet...")
except Exception as e:
    raise RuntimeError(f"unexpected error: {e}")
```

**Anti-patterns to avoid:**

| Pattern | Why it's wrong | Do this instead |
|---------|---------------|----------------|
| `except Exception: pass` | Swallows every error silently | Remove the try/except entirely |
| `value = x if x else default` on non-optional params | Hides caller bugs | Let the `None` crash downstream |
| `try: ... except: return None` | Caller gets `None`, crashes later with no context | Let the original exception propagate |
| `if result is None: result = []` as a guard | Masks upstream bug that returned `None` | Fix upstream to always return a list |
| `getattr(obj, "method", lambda: None)()` | Silently does nothing when method is missing | Let `AttributeError` fire |
| Wrapping every function body in `try/except` | Turns tracebacks into useless "something went wrong" | Only catch where you have a recovery strategy |

**When catching IS appropriate:**
- Network retries with backoff (transient failures that genuinely resolve on retry)
- Resource cleanup in `finally` blocks
- Boundary handlers (API endpoints, CLI entry points) that must return a response
- `KeyboardInterrupt` handling for graceful shutdown

**Never** use bare `except:` or `except Exception:`. Never catch-and-log without re-raising. Never add a try/except "just in case."

### Docstrings

- Required on all public functions, classes, and modules
- Google-style format
- First line is a concise summary (imperative mood: "Return", "Create", not "Returns", "Creates")
- Skip docstrings on obvious one-liners, test functions, and private helpers

```python
def resolve_runtime_provider(requested: str) -> dict[str, str]:
    """Resolve a provider name to api_mode, api_key, and base_url.

    Args:
        requested: Provider name or alias (e.g. "openrouter", "anthropic").

    Returns:
        Dict with keys: api_mode, api_key, base_url.

    Raises:
        AuthError: If no credentials found for the provider.
    """
    ...
```

### Code Organization

- **One concept per file** where practical — don't create 5000-line files for new code
- **Helper functions at module level**, not nested inside other functions
- **Constants at top of module** after imports
- **Registry pattern:** New tools follow `tools/registry.py` — register at import time
- **State files:** Use `get_hermes_home()` for paths, never `Path.home() / ".hermes"`
- **User-facing paths:** Use `display_hermes_home()` for print/log messages

### Function Design

- **Keep functions short** — under 50 lines for new code. Existing long functions are legacy.
- **Single responsibility** — each function does one thing
- **No side effects in constructors** — `__init__` should store params, not do heavy work
- **Dependency injection** — pass dependencies as constructor params, create them in factory functions

### JSON Returns from Tools

All tool handlers MUST return a JSON string:

```python
def my_tool(param: str, task_id: str = None) -> str:
    result = do_work(param)
    return json.dumps({"success": True, "data": result})
```

### Security

- **Redact secrets:** All tool output that enters model context must go through `redact_sensitive_text()` from `agent/redact.py`
- **No API keys in code:** Use `OPTIONAL_ENV_VARS` in `hermes_cli/config.py`
- **Path validation:** Use `get_hermes_home()`, never hardcode `~/.hermes`
- **Dangerous commands:** Check `tools/approval.py` patterns when adding terminal operations

### Testing Standards

- **Every new feature needs tests** — no exceptions
- **Unit tests are the minimum, not the goal** — E2E verification catches what mocks miss (see Testing section below)
- **Use pytest**, not unittest
- **Mock external services** — never make real API calls in unit tests
- **Don't over-mock** — if you're mocking 5 things to test 1 function, the test is fragile and tells you nothing. Test at a higher level or restructure the code.
- **Fixtures in conftest.py** — share setup across test files
- **Isolated HERMES_HOME** — the `_isolate_hermes_home` autouse fixture handles this
- **Never write to `~/.hermes/`** in tests — use `tmp_path`
- **Test names:** `test_<function>_<scenario>` — be descriptive
- **No defensive assertions** — don't assert things that can't possibly fail. Assert the behavior you actually care about.

```python
# Good — tests the actual behavior
def test_resolve_provider_returns_openrouter_for_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    result = resolve_provider()
    assert result == "openrouter"

# Bad — tests nothing useful, will never fail
def test_resolve_provider_exists():
    assert resolve_provider is not None
    assert callable(resolve_provider)
```

---

## Project Structure

```
hermes-agent/
├── run_agent.py          # AIAgent class — core conversation loop
├── model_tools.py        # Tool orchestration, _discover_tools(), handle_function_call()
├── toolsets.py           # Toolset definitions, _HERMES_CORE_TOOLS list
├── cli.py                # HermesCLI class — interactive CLI orchestrator
├── hermes_state.py       # SessionDB — SQLite session store (FTS5 search)
├── agent/                # Agent internals → See agent/AGENTS.md
├── hermes_cli/           # CLI, config, providers, skins → See hermes_cli/AGENTS.md
├── tools/                # Tool implementations → See tools/AGENTS.md
│   └── environments/     # Terminal backends → See tools/environments/AGENTS.md
├── gateway/              # Messaging gateway → See gateway/AGENTS.md
├── acp_adapter/          # ACP server (IDE integration) → See acp_adapter/AGENTS.md
├── plugins/              # Plugins, memory providers → See plugins/AGENTS.md
├── cron/                 # Scheduler (jobs.py, scheduler.py)
├── environments/         # RL training environments (Atropos)
├── tests/                # Pytest suite (~3000 tests)
└── batch_runner.py       # Parallel batch processing
```

**User config:** `~/.hermes/config.yaml` (settings), `~/.hermes/.env` (API keys)

### Directory Documentation

Each major subsystem has its own `AGENTS.md` with architecture, patterns, and pitfalls:

| Directory | Key topics |
|-----------|------------|
| `agent/` | AIAgent class, prompt builder, context compression, Anthropic adapter, display system |
| `hermes_cli/` | Slash commands, provider system, config loaders, skin engine, profiles |
| `tools/` | Tool registry, adding tools, tool categories, security |
| `tools/environments/` | Terminal backends, BaseEnvironment ABC, stdin modes, FileSyncManager |
| `gateway/` | GatewayRunner, platform adapters, debugging, session persistence |
| `acp_adapter/` | ACP protocol server, IDE integration |
| `plugins/` | General plugins, memory providers, skills, lifecycle hooks |

## File Dependency Chain

```
tools/registry.py  (no deps — imported by all tool files)
       ↑
tools/*.py  (each calls registry.register() at import time)
       ↑
model_tools.py  (imports tools/registry + triggers tool discovery)
       ↑
run_agent.py, cli.py, batch_runner.py, environments/
```

---

## Important Policies

### Prompt Caching Must Not Break

Hermes-Agent ensures caching remains valid throughout a conversation. **Do NOT implement changes that would:**
- Alter past context mid-conversation
- Change toolsets mid-conversation
- Reload memories or rebuild system prompts mid-conversation

Cache-breaking forces dramatically higher costs. The ONLY time we alter context is during context compression.

### Working Directory Behavior
- **CLI**: Uses current directory (`.` → `os.getcwd()`)
- **Messaging**: Uses `MESSAGING_CWD` env var (default: home directory)

### Background Process Notifications (Gateway)

When `terminal(background=true, notify_on_complete=true)` is used, the gateway runs a watcher that detects process completion and triggers a new agent turn. Control verbosity with `display.background_process_notifications` in config.yaml (or `HERMES_BACKGROUND_NOTIFICATIONS` env var): `all` (default), `result`, `error`, `off`.

---

## Known Pitfalls

### DO NOT hardcode `~/.hermes` paths
Use `get_hermes_home()` from `hermes_constants` for code paths, `display_hermes_home()` for user-facing messages. Hardcoding breaks profiles. See `hermes_cli/AGENTS.md` → Profiles for the full rules.

### DO NOT use `simple_term_menu` for interactive menus
Rendering bugs in tmux/iTerm2 — ghosting on scroll. Use `curses` (stdlib) instead. See `hermes_cli/tools_config.py`.

### DO NOT use `\033[K` (ANSI erase-to-EOL) in spinner/display code
Leaks as literal `?[K` text under `prompt_toolkit`'s `patch_stdout`. Use space-padding: `f"\r{line}{' ' * pad}"`.

### Tests must not write to `~/.hermes/`
The `_isolate_hermes_home` autouse fixture in `tests/conftest.py` redirects `HERMES_HOME` to a temp dir. See profile test pattern in `hermes_cli/AGENTS.md`.

See each directory's `AGENTS.md` for subsystem-specific pitfalls.

---

## Testing

### Running Tests

**CRITICAL: Disable pytest-xdist parallel mode.** The `-n auto` in pyproject.toml causes hangs.

```bash
source .venv/bin/activate

# Full suite
python -m pytest tests/ -n0 -q          # ~2 minutes

# Without xdist installed
python3 -m pytest tests/ -o "addopts=" -q

# Specific areas
python -m pytest tests/test_model_tools.py -n0 -q
python -m pytest tests/gateway/ -n0 -q
python -m pytest tests/tools/ -n0 -q
```

### CI vs Local

- CI installs `.[all,dev]` — ALL optional deps, blanked API keys
- Local may have real keys but missing optional packages — different failures than CI
- Always check actual CI logs: `gh run view <ID> --log-failed`

### Testing Philosophy

**Core principle: test the actual code path, not a mock of it.**

Unit tests catch logic errors but miss integration issues — env var loading, config resolution, module caching, symlink handling, Docker networking. Every change must be verified at the appropriate level from this stack:

### Verification Stack

| Level | When | How |
|-------|------|-----|
| Inline Python | After any code change | Real imports in isolated `HERMES_HOME`, call the function, assert |
| Smoke test | Before any push | `python -m hermes_cli.main chat -q "test query"` |
| Sub-agent tmux | Display/UI/CLI changes | Spin up hermes in tmux pane, send commands, capture output |
| Interactive CLI | Feature changes | Full interactive session, exercise the changed feature |
| Multi-provider | Agent core changes | Test across providers with real API calls |
| Full pytest suite | Before push | `python -m pytest tests/ -n0 -q` |

### Sub-Agent tmux Testing

For display, spinner, banner, ANSI, or prompt_toolkit changes — **always test in a real PTY**:

```bash
tmux new-session -d -s test -x 140 -y 40
tmux send-keys -t test "cd /path/to/worktree && source .venv/bin/activate && python -m hermes_cli.main" Enter
sleep 5
tmux capture-pane -t test -p | tail -20    # Check banner rendered cleanly
tmux send-keys -t test "What is 2+2? Use execute_code." Enter
sleep 15
tmux capture-pane -t test -p | tail -20    # Check tool output
# Any "?[" followed by digits = broken ANSI handling
tmux kill-session -t test
```

### Inline Python with Real Imports

For code changes — verify with real imports, not mocks:

```python
import sys, os, tempfile
for mod in list(sys.modules.keys()):
    if mod.startswith("tools") or mod.startswith("hermes"):
        del sys.modules[mod]
sys.path.insert(0, "/path/to/worktree")
os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="hermes_test_")
# Now import and call actual functions — assert behavior
```

**When mocks ARE appropriate:** external API calls, network services, time-dependent operations. Everything else runs real — real config files through real loaders, real tool handlers with real arguments.

### What to Test by Change Type

| Change type | Required verification |
|-------------|----------------------|
| New tool | Ask the agent to use it — check schema, execution, result rendering |
| Tool fix | Reproduce the original bug scenario, confirm fix |
| Display/UI change | **MANDATORY live PTY test via tmux** (see above) |
| Config option | Set via `hermes config set`, start session, verify behavior |
| Gateway/platform | Send a real message from the platform, check response |
| Prompt changes | Start session, ask agent to describe its instructions |
| Provider changes | Test across multiple providers (see below) |

### Multi-Provider Regression

When changes touch core agent behavior, test across providers:

```python
from run_agent import AIAgent
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.hermes/.env"))

for label, model in [("Claude", "anthropic/claude-sonnet-4"), ("GPT-4.1", "openai/gpt-4.1")]:
    agent = AIAgent(model=model, quiet_mode=True, skip_context_files=True, skip_memory=True)
    result = agent.run_conversation("What is 2+2? Answer in one word.")
    print(f"{label}: {result['final_response'][:100]}")
```

### CI Triage

```bash
python3 -m pytest tests/hermes_cli/ -o "addopts=" --tb=line -q  # ~5s
python3 -m pytest tests/tools/ -o "addopts=" --tb=line -q       # ~25s
python3 -m pytest tests/gateway/ -o "addopts=" --tb=line -q     # ~40s

gh run list --workflow tests.yml --branch main --limit 3
gh run view <RUN_ID> --log-failed 2>&1 | grep "FAILED\|ERROR"
```

Always run the full suite before pushing changes.
