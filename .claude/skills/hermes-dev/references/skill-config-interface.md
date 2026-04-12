# Skill Config Interface (April 2026)

Skills can declare `config.yaml` settings via `metadata.hermes.config` in their SKILL.md frontmatter. This is for non-secret settings (paths, preferences) — secrets still use `required_environment_variables` and `.env`.

## Architecture
- **Frontmatter schema:** `metadata.hermes.config` — list of `{key, description, default, prompt}`
- **Storage:** All values under `skills.config.*` in config.yaml (e.g., `skills.config.wiki.path`)
- **`agent/skill_utils.py`:** `extract_skill_config_vars()`, `discover_all_skill_config_vars()`, `resolve_skill_config_values()`, `SKILL_CONFIG_PREFIX`
- **`agent/skill_commands.py`:** `_inject_skill_config()` appends `[Skill config: ...]` block to skill messages at load time
- **`hermes_cli/config.py`:** `get_missing_skill_config_vars()`, prompting in `migrate_config()`, display in `show_config()`

## Key design decisions
- Skills declare logical keys (`wiki.path`), system auto-prefixes `skills.config.` for storage — no namespace collision with core config
- Disabled/platform-incompatible skills are excluded from discovery
- Agent sees logical keys in the injected block, not storage paths
- `_inject_skill_config` is try/except wrapped — non-critical, skill still loads if config resolution fails
- Path values (`~`, `${VAR}`) are expanded via `os.path.expanduser` + `os.path.expandvars`

## Adding config to a skill
```yaml
metadata:
  hermes:
    config:
      - key: wiki.path
        description: Path to the LLM Wiki directory
        default: "~/wiki"
        prompt: Wiki directory path
```

Users configure via `hermes config migrate` (prompted) or `hermes config set skills.config.wiki.path ~/my-wiki` (manual).

## First consumer
`skills/research/llm-wiki/SKILL.md` — declares `wiki.path` with default `~/wiki`.
