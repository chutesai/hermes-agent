---
name: hermes-dev
description: Development workflow for the hermes-agent codebase — PR review, bug fixing, cherry-pick salvage, code quality, architecture reference, and testing patterns. Use when working on hermes-agent itself, reviewing PRs, fixing bugs, triaging issues, or adding features.
---

# Hermes Agent Development Workflow

Use this skill when working on the hermes-agent codebase itself — fixing bugs, reviewing PRs, triaging issues, or making improvements.

## Quick Reference

| Task | Start here |
|------|-----------|
| PR review | [Workflow: Reviewing PRs](#workflow-reviewing-prs) |
| Bug fix | [Workflow: Fixing Bugs](#workflow-fixing-bugs) |
| Stale PR salvage | [Workflow: Cherry-Pick Salvage](#workflow-cherry-pick-salvage) |
| Architecture | [references/architecture.md](references/architecture.md) |
| Known pitfalls | [references/pitfalls.md](references/pitfalls.md) |
| Testing patterns | [references/testing-recipes.md](references/testing-recipes.md) |
| Adding providers | [references/provider-guide.md](references/provider-guide.md) |

## Default Posture: PRs Are Expected To Be Stale

Hermes-Agent moves quickly. Assume every external PR is stale against current `main`. This is normal. Do NOT spend time emphasizing staleness unless it creates a specific conflict or redundancy.

The default workflow is to **salvage** contributor work onto latest `main`, preserve authorship, add follow-up fixes, and merge the updated result. Direct merge is only for PRs that are already current and clean.

## Important Policies

### .env Is For Secrets Only

`~/.hermes/.env` is exclusively for API keys, tokens, and credentials. All non-secret configuration belongs in `config.yaml`. Reject any pattern that tells users to "set X in your .env" unless X is an API key.

### Prompt Caching Must Not Break

Do NOT implement changes that alter past context, change toolsets mid-conversation, or reload memories/rebuild system prompts during a conversation. Cache-breaking forces dramatically higher costs. The ONLY time context is altered is during compression.

**Exception:** `/model` intentionally invalidates cache because system prompt is model-dependent. This is the only acceptable cache break.

### Message Flow Invariants

1. No synthetic user/human injections during the agent loop
2. Role alternation: `System -> User -> Assistant -> User -> ...` with tool results between assistant turns
3. Never two assistant messages in a row, never two user messages in a row
4. Only `tool` role can have consecutive entries (parallel tool calls)

### No Lazy-Reading Escape Hatches

Do not add optional `offset`/`limit` parameters to tools that load instructional content (skills, prompts, config). Models will read page 1 and skip the rest. Full-content loading is the default and only option for instructional tools.

### Plugin PRs Must Not Touch Core Code

Plugin PRs should only modify their own plugin directory. Core files (`agent/`, `run_agent.py`, `model_tools.py`, `gateway/`, `hermes_cli/`) should not be touched by plugin PRs.

## Subagent Safety: Critical File Restrictions

**NEVER delegate file-editing tasks to subagents for these files:**
- `run_agent.py` (~7500 lines)
- `cli.py` (~7400 lines)
- `gateway/run.py` (~5800 lines)
- `hermes_cli/main.py` (~4200 lines)
- `hermes_cli/setup.py` (~3500 lines)

The patch tool's fuzzy matching creates dangerous ambiguous matches in files this size. Read-only operations are always safe. For edits to these files: do them directly, one patch at a time, with `py_compile.compile()` after each.

## Architecture Overview

See [references/architecture.md](references/architecture.md) for the full deep dive.

| Area | Key File(s) |
|------|------------|
| Agent core | `run_agent.py` — `AIAgent` class, conversation loop |
| Tool system | `model_tools.py` + `tools/registry.py` — discovery and dispatch |
| CLI | `cli.py` — `HermesCLI`, interactive orchestrator |
| Gateway | `gateway/run.py` — messaging platform handling |
| Config | `hermes_cli/config.py` — `DEFAULT_CONFIG`, migration |
| Auth | `hermes_cli/auth.py` — provider credential resolution |
| Skills | `agent/skill_commands.py` — skill loading and injection |

**File dependency chain:**
```
tools/registry.py  (no deps)
       ^
tools/*.py  (register at import)
       ^
model_tools.py  (triggers discovery)
       ^
run_agent.py, cli.py, batch_runner.py
```

## Workflow: Reviewing PRs

### 1. Read the PR

```bash
gh pr view <NUMBER>
gh pr diff <NUMBER>
```

### 2. Verify changes don't already exist on main

**Do this BEFORE evaluating merit.** Many PRs duplicate work already merged.

```bash
# Check if fix/feature already exists
git fetch origin main
search_files for key identifiers (function names, config keys)
git blame <file> -L <lines>
```

Check: code changes already on main, new features that exist elsewhere, bugs already fixed, equivalent files already present.

**Signals of redundancy:** PR author's commits already in `git blame`, PR's "before" code doesn't match current main, test count far below current (~3000+).

### 3. Check staleness and mergeability

```bash
git fetch origin pull/<NUMBER>/head:pr-<NUMBER>
git rev-list --count $(git merge-base main pr-<NUMBER>)..main
git merge --no-commit --no-ff pr-<NUMBER>  # conflicts?
git merge --abort
```

### 4. Audit sibling changes

Check for recent changes on main in the same area. Cross-reference conditionals against canonical registries (`PROVIDER_REGISTRY`, `DEFAULT_CONFIG`, `_PROVIDER_MODELS`).

### 5. Check for duplicate PRs

```bash
gh pr list --search "<ISSUE_NUMBER>" --state open
gh pr list --search "<keywords>" --state open
```

### 6. Decision

- **Close as redundant** if changes already exist on main
- **Salvage via cherry-pick** (default) if PR is good but stale
- **Approve & merge directly** if clean, correct, and current
- **Request changes** if the contribution itself is incorrect

### Merge Strategy

| PR type | Method | Why |
|---------|--------|-----|
| Salvage (cherry-picked contributor commits) | `--rebase` | Preserves per-commit authorship |
| All our commits | `--squash` | Clean single commit |
| Clean external PR | `--rebase` or `--merge` | Preserves contributor history |

## Workflow: Cherry-Pick Salvage

```bash
# 1. Fetch and check staleness
git fetch origin pull/<N>/head:pr-<N>
git rev-list --count $(git merge-base main pr-<N>)..main

# 2. Update worktree branch to current main
git fetch origin main
git merge --ff-only origin/main
# If ff-only fails: git reset --hard origin/main

# 3. Cherry-pick contributor commits
git cherry-pick <commit-sha>
# Or for inspection: git cherry-pick <commit-sha> --no-commit

# 4. Resolve conflicts - keep main + contributor's intended additions

# 5. Add follow-up fixes
git add <files>
git commit -m "fix: follow-up for salvaged PR #<N>"

# 6. E2E test (MANDATORY - see testing section)

# 7. Push, create PR, merge with --rebase, close original with credit
git push -f origin <worktree-branch>
gh pr create --title "<title>" --body "Salvaged from PR #<N> by @contributor"
gh pr merge <NEW> --rebase
gh pr close <N> --comment "Merged via PR #<NEW>. Commits cherry-picked with authorship preserved."
```

### One Change Per Branch Push

Never stack unreviewed changes. Push A -> merge -> reset to main -> apply B -> push. Force-pushing B before A is merged overwrites A.

### Post-Squash-Merge Verification

Squash merges from stale branches can silently revert recent fixes. After merging, run `git diff HEAD~1..HEAD` and scan for unexpected removals.

## Workflow: Fixing Bugs

### 1. Understand the issue

```bash
gh issue view <NUMBER>
```

### 2. Check for existing PRs

```bash
gh pr list --search "<ISSUE_NUMBER>" --state all
```

If PRs exist: merge clean ones directly, fix incomplete ones on top, cherry-pick stale ones.

### 3. Verify the claim against the codebase

Don't trust the issue description blindly. Use search and read to verify the reported code path exists and the bug is reproducible from reading the code.

For security PRs: understand WHY the vulnerable code exists before restricting it. The right fix preserves functionality while mitigating risk.

### 4. Fix and test

**Small fixes:** commit directly to main
**Larger changes:** create a PR branch

```bash
git checkout -b fix/short-description
git add <files>
git commit -m "fix: concise description"
git push -u origin fix/short-description
gh pr create --title "fix: concise description" --body "..."
```

### 5. Comment on the issue

```bash
gh issue comment <NUMBER> --body "Fixed in commit <SHA>."
gh issue close <NUMBER>
```

## E2E Testing After Changes (MANDATORY)

Unit tests alone are insufficient. Always E2E test before pushing.

```python
import sys, os, tempfile
worktree = "/path/to/worktree"
sys.path.insert(0, worktree)

test_home = tempfile.mkdtemp(prefix="hermes_e2e_")
os.environ["HERMES_HOME"] = os.path.join(test_home, ".hermes")
os.makedirs(os.environ["HERMES_HOME"])

# Call actual functions, assert behavior, clean up
```

**What to verify by change type:**

| Change | What to test |
|--------|-------------|
| Security fix | Real attack vectors + legitimate inputs still work |
| Tool change | Real function with real file I/O, not mocked |
| Config change | Real config.yaml -> real config loader -> value propagates |
| Display/UI | MANDATORY live PTY test via tmux (see [testing-recipes](references/testing-recipes.md)) |

See [references/testing-recipes.md](references/testing-recipes.md) for comprehensive testing patterns.

## Testing Commands

```bash
# Full suite (disable xdist parallel mode - causes hangs)
source .venv/bin/activate
python -m pytest tests/ -n0 -q          # ~2 minutes

# Without xdist installed
python3 -m pytest tests/ -o "addopts=" -q

# Specific areas
python -m pytest tests/test_model_tools.py -n0 -q    # toolset resolution
python -m pytest tests/test_cli_init.py -n0 -q        # CLI config
python -m pytest tests/gateway/ -n0 -q                 # gateway
python -m pytest tests/tools/ -n0 -q                   # tools
```

### CI vs Local Differences

- CI installs `.[all,dev]` — all optional deps present
- CI blanks API keys
- Local may have real keys but missing optional packages
- Always check `gh run view <ID> --log-failed` rather than assuming

## Contributor Credit

Prefer preserving contributor commits in history. Cherry-pick (not reimplement) their work, merge with `--rebase` to keep authorship. Credit matters more than cosmetics.

## CRITICAL: Always Fetch Before Investigating

```bash
git fetch origin main
git log --oneline origin/main -- <relevant files> | head -10
```

The local repo may be days behind origin. Always compare against `origin/main`, not just `HEAD`.

## Additional References

- **Architecture deep dive**: [references/architecture.md](references/architecture.md)
- **Known pitfalls (30+)**: [references/pitfalls.md](references/pitfalls.md)
- **Testing recipes**: [references/testing-recipes.md](references/testing-recipes.md)
- **Adding providers**: [references/provider-guide.md](references/provider-guide.md)
- **Skill config interface**: [references/skill-config-interface.md](references/skill-config-interface.md)
- **Anthropic thinking signatures**: [references/anthropic-thinking.md](references/anthropic-thinking.md)
- **Stale env var investigation**: [references/stale-env-vars.md](references/stale-env-vars.md)
