[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](CONTRIBUTING.md)
[![claude-code](https://img.shields.io/badge/claude--code-black?style=flat-square)](https://claude.ai/code)
[![Companion: pm-skills](https://img.shields.io/badge/companion-pm--skills-blue?style=flat-square)](https://github.com/phuryn/pm-skills)
[![Companion: claude-usage](https://img.shields.io/badge/companion-claude--usage-blue?style=flat-square)](https://github.com/phuryn/claude-usage)

# burnstop — a budget fuse for Claude Code

> Cap a Claude Code session in **tokens or dollars**. When spend crosses the line — your subagents counted — the run **halts cleanly**. It auto-arms **$50** the moment you start a `/goal`, and trips when an agent gets stuck in a loop.

Native hook. No proxy, no API key, nothing leaves your machine, fully within Anthropic's Terms of Service.

**Created by:** [The Product Compass Newsletter](https://www.productcompass.pm)

---

## The problem

Claude Code now runs autonomously for long stretches — `/goal` loops until a condition is met, agents retry, subagents fan out. That's powerful, and it's exactly how a session quietly burns through your budget: a stuck loop or a goal that never converges can run up your tokens (or, on API billing, real dollars) while you're away from the keyboard.

Claude Code shows usage *after the fact* (`/usage`) but it won't **stop**. burnstop is the missing circuit breaker: set a cap, and when the session crosses it, the run halts — **even mid-`/goal`**.

## Install

**As a plugin (recommended):**

```
/plugin marketplace add phuryn/burnstop
/plugin install burnstop
```

**Or locally** — and the recommended path on **Windows** (where `python3` is awkward and Claude Code runs hooks through bash):

```bash
git clone https://github.com/phuryn/burnstop
cd burnstop
python cli.py install      # registers the hooks + /budget commands with your own python
```

Use one path or the other, not both. **After installing, restart Claude Code (or open a new session)** — commands hot-reload into a running session, but hooks load at session start.

## Use

**Just start a goal — it auto-arms.** `/goal ...` caps the goal at **$50** automatically (change with `/budget-default $100`; an explicit budget is never overridden). A runaway goal halts at the cap.

**Set a budget yourself** — type `/budget` to discover them all:

```
/budget-arm $1          cap from now ($ or tokens), incl. subagents
/budget-status          spend vs cap
/budget-reset           fresh allowance, keep the cap
/budget-disarm          remove the cap
/budget-default $100    set the budget auto-armed on /goal
```

Budgets accept **dollars** (`$1`, `1$`, `$0.10`) or **tokens** (`200k`, `5M`, `1.5m`, `200000`). When the cap is hit:

```
burnstop: budget exhausted: $1.04 / $1.00 this session (incl. subagents). Halting.
```

`/budget-reset` and `/budget-disarm` work **even after a halt** — the fuse exempts burnstop's own commands, so it can't block you from clearing it. (You can also cap a whole session at launch: `BURNSTOP_BUDGET=$1 claude`.)

## How it works

A `Stop` hook recomputes spend from Claude Code's own local transcripts each turn (no ledger, nothing written during a run), prices each turn by its model for dollar budgets, and returns `continue:false` to halt — which **overrides `/goal`** so a runaway can't resurrect. It also trips on a structural loop (the same tool call repeated). Full design, pricing table, loop detection, subagent handling, and limitations: **[docs/architecture.md](docs/architecture.md)**. Why a hook and not a proxy: **[docs/spikes.md](docs/spikes.md)**.

## Requirements

- **Python 3.8+**, standard library only — no `pip install`, no build step. (Anyone running Claude Code already has Python.)

## Limitations (honest)

Bounded overshoot, not surgical: the fuse is ~one turn behind, and a subagent already mid-run can't be killed (only the parent halts and new spawns are blocked) — set the cap with headroom and bound subagents with `maxTurns`. Loop detection is a heuristic; the budget cap is the deterministic backstop. On a subscription there's no per-token dollar bill, so a dollar budget is computed from list prices as a proxy for usage burn.

## Companion projects

- **[claude-usage](https://github.com/phuryn/claude-usage)** — a local dashboard for Claude Code token usage and cost. burnstop *caps* your spend; claude-usage *shows* it.
- **[pm-skills](https://github.com/phuryn/pm-skills)** — the AI operating system for better product decisions (Claude Code plugins).

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Agent/contributor guide: [AGENTS.md](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE).
