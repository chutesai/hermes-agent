# Gateway — Development Guide

The `gateway/` directory implements the messaging platform gateway — Telegram, Discord, Slack, WhatsApp, Signal, Matrix, and 10+ other platforms behind a unified adapter interface.

## Architecture

`GatewayRunner` in `run.py` (~8800 lines) is the main orchestrator. It:
- Starts platform adapters based on config
- Dispatches incoming messages to `AIAgent` instances
- Manages sessions, slash commands, hooks, and streaming delivery
- Runs one `AIAgent` per conversation in a `ThreadPoolExecutor`

The gateway reads config per-message (not at startup), so config changes take effect immediately without restart.

## Key Files

| File | Purpose |
|------|---------|
| `run.py` | Main loop, slash command dispatch, message handling, agent orchestration |
| `session.py` | `SessionStore` — JSONL + SQLite conversation persistence |
| `config.py` | `Platform` enum, `PlatformConfig`, full platform configuration schema |
| `stream_consumer.py` | Streaming response consumption and delivery |
| `hooks.py` | Gateway hook dispatch |
| `delivery.py` | Message delivery logic |
| `status.py` | Status tracking, scoped locks, PID management |
| `channel_directory.py` | Channel routing |
| `pairing.py` | Device/user pairing |
| `mirror.py` | Cross-session message mirroring |
| `session_context.py` | Context tracking |

## Platform Adapter Interface

`BasePlatformAdapter` in `platforms/base.py` (79KB) defines the interface. Each platform adapter inherits from it and implements platform-specific message handling.

Key types: `MessageEvent` (incoming message data), `MessageType` (text, image, audio, etc.)

For the full checklist on adding a new platform, see `gateway/platforms/ADDING_A_PLATFORM.md`.

### Platform Adapters

Telegram (121KB), Discord (128KB), Slack (67KB), Feishu (153KB), Matrix (81KB), API Server (77KB), WhatsApp, Signal, Mattermost, Weixin, WeCom, BlueBubbles, HomeAssistant, DingTalk, SMS.

Each adapter is fully self-contained — implements `BasePlatformAdapter` methods for its platform's API.

## Debugging

### Check Logs FIRST

```bash
grep -i "failed to send\|send.*error\|edit.*error\|MarkdownV2 parse failed" ~/.hermes/logs/gateway.log | tail -30
```

This single command often reveals the root cause immediately. A common mistake is tracing code paths for 20+ tool calls before checking logs.

### Crash Forensics

1. `systemctl --user status hermes-gateway` — PID, exit code, duration
2. `gateway.log` — search for `"Stopping gateway..."` (clean shutdown = SIGTERM)
3. `~/.hermes/gateway_state.json` — PID, argv, platform states, exit reason
4. `dmesg | grep -i oom` — OOM killer check
5. Session files in `~/.hermes/sessions/` — search for `gateway`, `kill`, `systemctl` in tool args

### Restart Rules

Always use `systemctl --user restart hermes-gateway`. NEVER `kill <PID>` + `gateway run &disown`. Starting outside the service manager breaks automatic restarts. A dangerous command pattern in `tools/approval.py` blocks `gateway run` with backgrounding operators.

## Pitfalls

**Dual persistence on compression.** When compression fires during `run_conversation()`, two persistence paths must both handle it: agent → SQLite (`_flush_messages_to_session_db`) and gateway → JSONL (`history_offset`). If either misses the session split, compressed context is silently lost. See `.claude/skills/hermes-dev/references/architecture.md` for the full treatment.

**`python-telegram-bot` exception hierarchy.** `BadRequest` inherits from `NetworkError`. Any `except NetworkError` handler catches both — but `BadRequest` is permanent, not transient. This caused retry loops on "Message thread not found" errors. Always check `isinstance(err, BadRequest)` inside `NetworkError` handlers.

**Shared module extraction.** When extracting duplicated logic into shared modules, tests that mock the old location (`patch("tools.skills_tool.sys")`) silently stop working. Always grep for ALL `patch("old_module.thing")` references and update.
