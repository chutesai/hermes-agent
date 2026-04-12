# Testing Recipes

Detailed live testing patterns for hermes-agent.

## Live Testing the Agent

Beyond unit tests, always verify changes by actually running the agent. This catches issues that mocks miss — prompt rendering, tool wiring, config loading, UX regressions. You have full access to run the CLI interactively.

### Quick single-query smoke test

```bash
cd ~/.hermes/hermes-agent
source .venv/bin/activate
python -m hermes_cli.main chat -q "What tools do you have available?"
```

The `-q` flag sends one message and exits. Use this to quickly verify:
- The agent starts without import errors after your changes
- Tool discovery and schema generation work
- System prompt renders correctly
- Model/provider connectivity is intact

### Full interactive CLI session

```bash
cd ~/.hermes/hermes-agent
source .venv/bin/activate
python -m hermes_cli.main
```

This launches the full interactive CLI with prompt_toolkit, the banner, spinner, and all keybindings. Use this to verify:
- **Slash commands** work: `/help`, `/context`, `/thinkon`, `/thinkoff`, `/compact`, `/reset`
- **Tool calls** execute correctly (ask it to read a file, run a terminal command, search the web)
- **Display rendering** looks right (response boxes, tool progress feed, think blocks, KawaiiSpinner)
- **Session resume** works: exit, note the session ID printed on exit, then `python -m hermes_cli.main --resume <ID>`
- **Session continue** works: `python -m hermes_cli.main -c` (most recent) or `-c "session name"`
- **Worktree mode** works: `python -m hermes_cli.main -w` (from inside a git repo)

### Testing with a specific model or provider

```bash
python -m hermes_cli.main chat -m "anthropic/claude-sonnet-4"
python -m hermes_cli.main chat --provider openrouter
```

### Testing gateway/messaging changes

For gateway changes, run the gateway locally:

```bash
python -m hermes_cli.main gateway run
```

Then send messages via Telegram/Discord/etc. to verify platform-specific behavior. Use `/restart` in the chat to reload gateway changes without killing the process.

### Testing config changes

Verify config changes are picked up by both loaders:

```bash
# Check hermes_cli config loader (used by hermes tools, hermes setup)
python -c "from hermes_cli.config import load_config; import json; print(json.dumps(load_config(), indent=2))" | head -30

# Check CLI config loader (used by interactive CLI)
python -c "from cli import load_cli_config; c = load_cli_config(); print(c.get('your_new_key'))"
```

Remember: CLI reads config once at startup, gateway reads it per-message.

### Multi-provider live regression testing

When a PR changes core agent behavior (reasoning extraction, error handling, response processing), test across all available providers to catch provider-specific regressions. Use `execute_code` to run multiple providers in sequence:

```python
from run_agent import AIAgent
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.hermes/.env"))

# Discover available providers
# Check: OPENROUTER_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.

models = [
    ("OpenRouter — Claude", "anthropic/claude-sonnet-4"),
    ("Anthropic direct", "claude-sonnet-4-20250514"),  # api_mode="anthropic_messages"
    ("OpenRouter — GPT-4.1", "openai/gpt-4.1"),
    ("OpenRouter — DeepSeek R1", "deepseek/deepseek-r1"),  # produces structured reasoning
    ("OpenRouter — Qwen3", "qwen/qwen3-235b-a22b"),  # may produce inline reasoning
]

for label, model in models:
    agent = AIAgent(model=model, quiet_mode=True, skip_context_files=True,
                    skip_memory=True, enabled_toolsets=[])
    result = agent.run_conversation("What is 2+2? Answer in one word.")
    print(f"{label}: completed={result['completed']} response={result['final_response'][:100]}")
```

Also test with tools enabled (e.g. `enabled_toolsets=["terminal"]`) to verify tool calling still works. Provider-specific failures often only surface with tool schemas attached.

### Live E2E testing with Codex Responses API

The Codex path uses a different API mode, auth flow, and model naming than standard providers. Standard AIAgent instantiation won't route to Codex — you must configure it explicitly:

```python
from hermes_cli.auth import resolve_codex_runtime_credentials
from run_agent import AIAgent
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.hermes/.env"))

creds = resolve_codex_runtime_credentials()
# creds = {"api_key": "...", "base_url": "https://chatgpt.com/backend-api/codex"}

agent = AIAgent(
    model="gpt-5.3-codex",       # NOT "codex-mini" or "o4-mini" — those fail with ChatGPT auth
    quiet_mode=True,
    skip_context_files=True,
    skip_memory=True,
    enabled_toolsets=[],          # or ["terminal"] for tool-call testing
    provider="openai-codex",
)
# Must set these explicitly — provider="openai-codex" alone doesn't wire everything
agent.api_mode = "codex_responses"
agent.api_key = creds["api_key"]
agent.base_url = creds["base_url"]
agent.client = None              # force client rebuild with new creds

result = agent.run_conversation("What is 2+2? Answer in one word.")
print(f"completed={result.get('completed')}, response={result.get('final_response')}")
```

**Model names for ChatGPT-backed Codex** (from `_PROVIDER_MODELS["openai-codex"]` in models.py):
`gpt-5.3-codex`, `gpt-5.2-codex`, `gpt-5.1-codex-mini`, `gpt-5.1-codex-max`

**Common mistakes:**
- `codex-mini`, `o4-mini` → 400 "not supported when using Codex with a ChatGPT account"
- Forgetting `agent.client = None` → stale client uses wrong base_url
- Not calling `resolve_codex_runtime_credentials()` → no auth

**Test both paths when changing Codex code:**
1. No tools (`enabled_toolsets=[]`) — exercises `_preflight_codex_api_kwargs` with `tools=None`
2. With tools (`enabled_toolsets=["terminal"]`) — exercises tool schema serialization

### What to verify for common change types

| Change type | What to test live |
|-------------|-------------------|
| New tool | Ask the agent to use it — check schema in tool list, execution, result rendering |
| Tool fix | Reproduce the original bug scenario interactively, confirm it's fixed |
| Display/UI change | **MANDATORY live PTY test via tmux** (see below). Previous ANSI fixes caused regressions — visual verification is not optional. |
| New config option | Set the value via hermes config set, start a session, verify behavior |
| Gateway/platform | Send a real message from the platform, check response and delivery |
| Prompt changes | Start a session, ask the agent to describe its instructions |
| Session/state | Create a session, exit, resume with --resume or -c, verify history |
| Skill changes | Load the skill with /skill name, verify it injects correctly |
| Context compression | Fill up context with a long conversation, verify compression triggers |

### MANDATORY: Live PTY testing for display/ANSI/rendering changes

Any PR touching `display.py`, `cli.py` display output, `skin_engine.py`, `_safe_print`, `_cprint`, `_vprint`, spinner rendering, response box rendering, or ANSI escape codes MUST be live tested via tmux before merging. Previous ANSI fixes caused regressions that weren't caught by unit tests.

```bash
# 1. Launch hermes in a tmux PTY
tmux kill-session -t display-test 2>/dev/null
tmux new-session -d -s display-test -x 140 -y 40
tmux send-keys -t display-test "cd /path/to/worktree && source .venv/bin/activate && python -m hermes_cli.main" Enter
sleep 5

# 2. Capture and verify the banner rendered cleanly
tmux capture-pane -t display-test -p | tail -20

# 3. Test /verbose cycling (all 4 modes)
tmux send-keys -t display-test "/verbose" Enter && sleep 1
tmux capture-pane -t display-test -p | tail -5
# Repeat 3x to cycle through OFF → NEW → ALL → VERBOSE

# 4. Test a tool call (verifies tool progress, reasoning, response box)
tmux send-keys -t display-test "What is 2+2? Use execute_code." Enter
sleep 15
tmux capture-pane -t display-test -p | tail -20

# 5. Test a second tool call in same session (catches state bugs)
tmux send-keys -t display-test "Now read the first line of README.md" Enter
sleep 12
tmux capture-pane -t display-test -p | tail -15

# 6. Check for garbled output: ?[33m, ?[0m, ?[K, literal ESC chars
# If ANY captured output contains "?[" followed by digits, the fix is broken.

# 7. Clean up
tmux send-keys -t display-test "/exit" Enter && sleep 2
tmux kill-session -t display-test
```

**What to look for:**
- No `?[33m` or `?[0m` garbled sequences (the `?` replaces ESC when `patch_stdout` mangles output)
- Tool progress lines render with correct formatting
- Reasoning blocks have proper box borders
- Response boxes render cleanly
- `/verbose` mode labels show colored text, not raw escape codes

**Key pitfall:** `patch_stdout`'s `StdoutProxy` intercepts raw `print()` and `Console.print()` calls during prompt_toolkit's event loop. Any code that writes ANSI directly to stdout (bypassing `_cprint`/`print_formatted_text(ANSI(...))`) will get mangled. The fix is always to route through `_cprint` or the agent's pluggable `_print_fn`.

### Live testing plugins (HERMES_HOME isolation)

When testing plugin hooks or plugin loading, use an isolated `HERMES_HOME` to avoid polluting real config:

```bash
# 1. Create isolated plugin home with symlinked credentials
mkdir -p /tmp/hermes-plugin-test/plugins/my_test_plugin
ln -sf ~/.hermes/.env /tmp/hermes-plugin-test/.env
ln -sf ~/.hermes/config.yaml /tmp/hermes-plugin-test/config.yaml

# 2. Plugin directory structure (ALL THREE files required):
#    plugins/my_test_plugin/
#    ├── plugin.yaml          # NOT manifest.yaml — scanner looks for plugin.yaml or plugin.yml
#    ├── __init__.py           # MUST contain register(ctx) — scanner imports __init__.py, not plugin.py
#    └── (optional other files)

# 3. plugin.yaml — minimal manifest:
cat > /tmp/hermes-plugin-test/plugins/my_test_plugin/plugin.yaml << 'EOF'
name: my_test_plugin
version: 0.1.0
description: Test plugin
EOF

# 4. __init__.py — register() function is the entry point:
cat > /tmp/hermes-plugin-test/plugins/my_test_plugin/__init__.py << 'EOF'
def on_session_start(**kwargs):
    print(f"HOOK FIRED: on_session_start {kwargs}")

def register(ctx):
    ctx.register_hook("on_session_start", on_session_start)
    # Available hooks: pre_tool_call, post_tool_call, pre_llm_call,
    # post_llm_call, on_session_start, on_session_end
EOF

# 5. Run with isolated home:
HERMES_HOME=/tmp/hermes-plugin-test python -m hermes_cli.main chat -q "test message" --model anthropic/claude-sonnet-4
```

**Common mistakes that produce silent failures:**
- `manifest.yaml` instead of `plugin.yaml` → plugin silently skipped ("no plugin.yaml" debug log)
- `register()` in `plugin.py` instead of `__init__.py` → "has no register() function" warning
- Missing `__init__.py` → "No __init__.py" error
- Missing `.env` symlink → "Provider resolver returned an empty API key"

Check the first few lines of CLI output for plugin load errors — they print before the banner.

### Testing in worktree mode (recommended for larger changes)

When testing changes that might break things, use worktree mode so your changes are isolated:

```bash
cd ~/.hermes/hermes-agent
python -m hermes_cli.main -w
```

This creates a disposable git worktree with its own branch. Changes here won't affect the main working tree. The worktree is cleaned up on exit.


## MCP Server Testing (mcp_serve.py)

When testing the MCP server (`hermes mcp serve`), use a three-layer approach:

### Layer 1: Unit tests with test fixtures
Standard pytest with mock SessionDB and sessions.json in tmp_path. Tests helpers, EventBridge queue mechanics, content extraction.

### Layer 2: E2E via FastMCP's internal tool manager
FastMCP exposes `server._tool_manager.call_tool(name, args)` — async, returns the tool's return value directly. This calls the REAL tool function through the MCP server without needing stdio transport:

```python
from mcp_serve import create_mcp_server, EventBridge
bridge = EventBridge()
server = create_mcp_server(event_bridge=bridge)

# Call tools through the server
import asyncio
result = asyncio.run(server._tool_manager.call_tool("conversations_list", {"limit": 5}))
data = json.loads(result)  # Tools return JSON strings
```

Create a real SQLite DB with `_create_test_db()` for messages_read/attachments_fetch tests. Wire it via `monkeypatch.setattr(mcp_serve, "_get_session_db", lambda: test_db)`.

### Layer 3: Full stdio protocol test
Spawn the server as a subprocess, connect with the MCP client SDK:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(command=sys.executable, args=["-c", "...server code..."])
async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("conversations_list", {"limit": 5})
```

### Key pitfall: SessionDB timestamps are Unix floats
Real SessionDB stores timestamps as `float` (e.g. `1774809502.59`), NOT ISO strings. The EventBridge poll loop must normalize with a `_ts_float()` helper that handles both formats. Discovered during live E2E testing against real `~/.hermes` data — unit tests with string timestamps passed but live tests crashed with `TypeError: '>' not supported between instances of 'float' and 'str'`.

### mtime-optimized polling
The EventBridge polls at 200ms using `os.stat()` mtime checks (~1μs each) on sessions.json and state.db. When neither file has changed, the poll cycle skips entirely — makes fast polling essentially free. Test this with a call-counting mock DB to verify the second poll doesn't hit the DB.
