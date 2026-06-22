# Changelog

## v1.0.0 — 2026-06-22

First public release. A native budget fuse for Claude Code.

### Fuse (enforcement)

- Caps a session in **tokens or dollars** and halts the run when spend crosses the cap (parent **+ subagents**), recomputed live from Claude Code's own JSONL transcripts — no proxy, no API key, no OAuth forwarding, fully within Anthropic's ToS.
- Halts via a `Stop` hook returning `{"continue": false}`, which **overrides an active `/goal`** so a goal-driven runaway can't resurrect. `PreToolUse` adds a permission `deny` to freeze spending mid-turn, and **exempts burnstop's own CLI** so `/budget` commands keep working even when over budget.
- **Per-model dollar pricing:** each turn is priced by its own model (`meter.PRICING` for opus/sonnet/haiku/fable/mythos; cache-read and cache-write priced; unknown/local models cost $0) and summed — correct for mixed-model sessions.
- **Structural loop detection:** trips when the same tool call repeats N times (the overnight-burn signature).
- Dedupes transcript records by `message.id` (keep last) so streaming re-writes and subagent files aren't double-counted.

### Commands & onboarding

- **`/budget-*` slash commands**, discoverable (type `/budget`): `/budget-arm`, `/budget-status`, `/budget-reset`, `/budget-disarm`, `/budget-default`. Each runs the CLI and shows the result.
- **Auto-arm on `/goal`:** a `UserPromptExpansion` hook arms a **$50** default when a goal starts and the session isn't already armed. Configurable via `/budget-default $100` or `BURNSTOP_GOAL_DEFAULT` (resolution: env > config > built-in $50). Explicit budgets are never overridden.
- **Forgiving budget input:** dollars (`$1`, `1$`, `$ 1`, `$0.10`) or tokens (`200k`, `200K`, `5M`, `5 m`, `1.5m`, `200000`). Compact display: `200k`, `1.50M`.

### Packaging

- Ships as a **Claude Code plugin** (`.claude-plugin/plugin.json` + `marketplace.json`): `/plugin marketplace add phuryn/burnstop` then `/plugin install burnstop`. Or `python cli.py install` locally (recommended on Windows).
- Hook commands use forward-slash paths — Claude Code runs hooks through bash, which strips backslashes on Windows. `cli.py install` self-heals on re-install.

### Quality

- Stdlib-only Python 3.8+, **88 tests** (meter / pricing / hook / cli / dispatch / version), CI on Python 3.9 / 3.11 / 3.12, plus a gated live `/goal` test. Docs: `README.md`, `docs/architecture.md`, `docs/spikes.md`, `docs/tests.md`, `AGENTS.md`.
