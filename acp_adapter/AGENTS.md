# ACP Adapter — Development Guide

The `acp_adapter/` directory implements the Agent Communication Protocol server for IDE integration (VS Code, Zed, JetBrains).

## Architecture

| File | Purpose |
|------|---------|
| `server.py` (28KB) | Main ACP server — handles protocol lifecycle, message routing |
| `session.py` (18KB) | Session management — maps ACP sessions to AIAgent instances |
| `tools.py` (7KB) | Tool schema adaptation — exposes Hermes tools via ACP protocol |
| `events.py` (6KB) | Event type definitions |
| `permissions.py` (3KB) | Permission model for tool execution |
| `auth.py` (1KB) | Authentication handling |
| `entry.py` (2KB) | Entry point, server startup |
| `__main__.py` | Module entry (`python -m acp_adapter`) |

## Key Pattern

The ACP server wraps `AIAgent` instances:
1. Incoming ACP messages are translated to Hermes message format
2. `AIAgent.run_conversation()` processes the request
3. Tool calls go through the standard `handle_function_call()` pipeline
4. Responses are translated back to ACP protocol format
5. Permissions are checked before tool execution via `permissions.py`

The server runs as a separate process, started via `hermes acp serve` or `hermes-acp` entry point.

## Integration Points

- Tool schemas are adapted from `model_tools.py` format to ACP format in `tools.py`
- Sessions are independent from CLI/gateway sessions
- Config is read from the standard `~/.hermes/config.yaml`
