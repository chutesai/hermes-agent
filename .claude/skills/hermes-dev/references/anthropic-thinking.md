# Anthropic Thinking Block Signatures & Provider Switching

## How Signatures Work

Anthropic signs thinking blocks against the full preceding conversation context. The signature is attached to each thinking/redacted_thinking block in the assistant message's content array.

## Key Pitfalls

### 1. Signatures are Anthropic-proprietary
Third-party Anthropic-compatible endpoints (MiniMax, Azure AI Foundry, self-hosted proxies) CANNOT validate them → HTTP 400 "Invalid signature in thinking block".

### 2. Context mutations invalidate signatures
Context compression, budget warnings, session truncation, or message merging can make the signature on the latest assistant's thinking block stale → Anthropic rejects it.

### 3. Fallback chain cascading failure
When primary Anthropic fails and fallback activates to a third-party Anthropic-compat endpoint, the session carries Anthropic-signed thinking blocks. Without base_url-aware stripping, the fallback also gets 400 — both primary and fallback error out.

## Architecture (as of PR #6289)

### Proactive fix: base_url-aware stripping
`convert_messages_to_anthropic(base_url=...)` checks `_is_third_party_anthropic_endpoint()`:
- **Third-party endpoint** → strip ALL thinking/redacted_thinking blocks from ALL assistant messages
- **Direct Anthropic** → preserve signed blocks only in the latest assistant message, strip from older messages
- **base_url=None** → treated as direct Anthropic (default behavior)

Threaded: `_build_api_kwargs()` → `build_anthropic_kwargs(base_url=self._anthropic_base_url)` → `convert_messages_to_anthropic(base_url=...)`

### Reactive recovery: signature error retry
`_api_call_with_interrupt()` (~line 7884) has a one-shot recovery that strips `reasoning_details` from all messages when it detects "signature" + "thinking" in a 400 error message. This is a safety net — the proactive fix should prevent most occurrences.

### httpx.URL normalization
Some client objects expose `base_url` as `httpx.URL`, not `str`. All auth/provider detection helpers use `_normalize_base_url_text()` to coerce to str before calling `.rstrip()` or `.startswith()`. **Always normalize first** when adding new base_url inspection code.

## Files Involved

- `agent/anthropic_adapter.py` — `_normalize_base_url_text()`, `_is_third_party_anthropic_endpoint()`, signature management in `convert_messages_to_anthropic()`, `build_anthropic_kwargs(base_url=...)`
- `run_agent.py` — `_try_activate_fallback()` (stores `fb_base_url` as str, not raw httpx.URL), `_build_api_kwargs()` (passes `_anthropic_base_url`), signature recovery in `_api_call_with_interrupt()`

## Debugging

User-visible error pattern:
```
⚠️ Non-retryable error (HTTP 400) — trying fallback...
❌ Non-retryable error (HTTP 400): HTTP 400: messages.11.content.0: Invalid signature in thinking block
```

If the rstrip crash is also present:
```
Failed to activate fallback MiniMax-M2.7: 'URL' object has no attribute 'rstrip'
```

The combination means: signature error → fallback attempt → fallback also crashes → both errors shown.
