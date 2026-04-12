# Stale Env Var Investigation (Ghost Configs)

When a user reports an env var overriding config or causing confusion:

1. **Search for readers** — `os.getenv("VAR")` / `os.environ.get("VAR")` in production code (not tests)
2. **Search for writers** — `save_env_value("VAR", ...)` in current code
3. **If both are zero, check git history** — `git log --all -S 'VAR' -- '*.py'` to find who USED to write it. Old `hermes setup` flows or removed code may have written the var to `~/.hermes/.env` in past versions.
4. **The trap:** `load_dotenv(override=True)` in `env_loader.py` faithfully loads every key from `.env` into `os.environ` every session — even dead vars that nothing reads anymore. Users see the var in `.env`, the docs may still reference it, and they blame it for unrelated issues.
5. **Fix pattern:** Add a config migration (bump `_config_version` in `hermes_cli/config.py`, add `if current_ver < N:` block) to clear the dead var. Follow the ANTHROPIC_TOKEN cleanup pattern (version 8→9). Also purge from docs.

## Real example: LLM_MODEL (March 2026)

- Old setup wizard had 12 `save_env_value('LLM_MODEL', ...)` calls across provider flows
- Commit `9302690e` removed the writes but never added a migration to clean existing `.env` files
- Result: every pre-March install had `LLM_MODEL=<model>` sitting in `.env`, doing nothing but causing confusion
- Docs still said "the gateway reads it as a fallback" (false — zero references in gateway/)
- Fix: Config version 12→13 migration clears `LLM_MODEL` and `OPENAI_MODEL` from `.env`
