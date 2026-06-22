# Architecture

How burnstop works, in depth. (README is the quick start; this is the reference.)

## What it is (and isn't)

burnstop is a **Claude Code plugin**: hooks (the enforcement) + a `/budget` slash command + a small Python engine (`meter.py` / `hook.py` / `cli.py` / `dispatch.py`). Not a skill, not an MCP server — only a hook can halt the agent loop.

Four hooks do the work:

| Event | Script | Role |
|---|---|---|
| `Stop` | `hook.py` | the halt — top-level `continue:false` (beats `/goal`) |
| `PreToolUse` | `hook.py` | permission `deny` to freeze spending mid-turn (exempts burnstop's own CLI) |
| `SubagentStop` | `hook.py` | reconcile subagent spend into the session total |
| `UserPromptExpansion` | `dispatch.py` | auto-arm $50 when a `/goal` starts |

The `/budget` commands are **not** hooks — each runs the CLI directly via Bash (see [The /budget commands](#the-budget-commands-and-goal-auto-arm)).

Install is a plugin (`/plugin install burnstop`, see [Packaging](#packaging-as-a-plugin)) or `python cli.py install` for the local/Windows path. Both register the four hooks plus the `/budget` commands; neither needs a manual settings edit beyond running the installer. Per-session budget state lives in `~/.claude/burnstop/<session_id>.json` (written by `arm`).

## Data flow

```
~/.claude/projects/<project>/<session_id>.jsonl   ─┐
~/.claude/projects/<project>/subagents/*.jsonl    ─┤→ meter.session_spend()  (read-only, deduped, priced)
                                                    │           │
PreToolUse / Stop / SubagentStop ── hook.py ───────┘           ↓
        │            resolve_budget() (env or arm file)   evaluate() -> halt | warn | allow
        ↓                                                       │
   render(verdict, event)  ←────────────────────────────────────┘
        │
   Stop: {"continue": false}  |  PreToolUse: permission deny  |  warn: systemMessage  |  allow: nothing
```

There is **no ledger to maintain**. Spend is recomputed from Claude Code's own transcripts on every hook call, which (a) can't go stale and (b) avoids any read-modify-write race when the parent and several subagents fire hooks at once — every hook only reads.

## How spend is calculated

### Tokens

Each assistant turn records `message.usage` with four billable fields: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`. A token budget is compared against their **sum**.

Two invariants (verified against real transcripts, June 2026):
- **Dedupe by `message.id`, keep last.** Claude Code writes several records per API response; only the last carries final tallies. Summing across records that share a message id double-counts.
- **Subagents count.** Sidechain turns carry `isSidechain: true` and the **same parent `sessionId`**, inline in the parent transcript and/or a sibling `subagents/*.jsonl`. `session_spend` reads both and dedupes, so the union is safe.

### Dollars (model-aware)

A turn's cost depends on its model: Opus output is 5x Haiku's. So cost is computed **per turn, by each turn's own model** (`meter.turn_cost`) and summed — aggregating tokens first and applying one price would be wrong for a mixed-model session. Per-MTok USD list prices (`meter.PRICING`, Anthropic, June 2026):

| family | input | output | cache-read | cache-write |
|---|---|---|---|---|
| opus | $5 | $25 | $0.50 | $6.25 |
| sonnet | $3 | $15 | $0.30 | $3.75 |
| haiku | $1 | $5 | $0.10 | $1.25 |
| fable / mythos | $10 | $50 | $1.00 | $12.50 |

`get_pricing` matches the family keyword as a substring of the model id (handles date suffixes like `claude-opus-4-8-20260601`). Cache-read is 0.1x input, cache-write (5m creation) is 1.25x input. **Unknown / local / third-party models resolve to $0**, so they never trip a dollar budget (and aren't billed at Anthropic rates). Keep `PRICING` current with list prices.

## Setting and resetting constraints

A budget is `(amount, unit)` where unit is `usd` (`$1`, `$0.10`) or `tok` (`200k`, `1.5m`, integer). Both forms work everywhere.

| Action | Command (plugin / engine) | Effect |
|---|---|---|
| Cap the whole session, at launch | `BURNSTOP_BUDGET=$1 claude` | baseline 0, every token/dollar counts |
| Cap from now (mid-session) | `/budget arm $1` · `python cli.py arm $1` | baseline = spend at arm time; everything *after* counts |
| Fresh allowance, keep the cap | `/budget reset` | spend-since back to 0 (re-baseline to now) |
| Remove the cap | `/budget disarm` | hook stops interfering |
| Inspect | `/budget status` | spend (tokens **and** $) vs cap |
| Set the `/goal` default | `/budget default $100` | persists to `~/.claude/burnstop/config.json` |

**Resolution precedence** (most specific wins): `BURNSTOP_BUDGET` env var > arm file > unarmed (the hook exits 0 and never interferes). The `/goal` auto-arm default resolves separately: `BURNSTOP_GOAL_DEFAULT` env > config file `goal_default` > built-in `$50`.

**Two UX properties of `/budget`:** bad/missing arguments return the CLI's usage; and because the `PreToolUse` hook **exempts burnstop's own CLI**, `reset`/`disarm` **work even after a budget halt** — the fuse can never trap you from clearing it.

### Per-goal / per-loop budgets

There is no separate per-goal budget store, because Claude Code doesn't expose a goal/loop id to hooks. You scope a budget to a goal or loop by **arming when you start it** — `arm` baselines from "now," so `arm $1` right after `/goal ...` caps that goal's run at $1. Use `reset` to grant the next goal a fresh allowance without retyping the amount. Budgets are per-session; "per goal" = "from this point on."

## How it halts (and why it beats `/goal`)

The halt is event-specific (`hook.render`):

- **`Stop` / `SubagentStop`** -> top-level `{"continue": false}`. This is the reliable session stop. `/goal` is itself a Stop hook that returns `decision: block` ("keep going") while its condition is unmet; a top-level `continue: false` from another hook **takes precedence over any event-specific decision**, and if *any* hook returns `continue:false` the session stops. So burnstop's Stop hook **overrides an active `/goal`** — the goal can't resurrect the loop. (There is no hook API to run `/goal clear`, so `continue:false` is the only route, and it's the right one.)
- **`PreToolUse`** -> a permission **`deny`** so the agent can't keep calling tools mid-turn, plus `continue:false` as a hedge. It **exempts Bash calls running burnstop's own `cli.py`** (`hook._is_burnstop_cli`), so `/budget` commands work even when over budget.

`install` registers Stop, PreToolUse, and SubagentStop for the fuse (plus UserPromptExpansion for auto-arm). **Do not reduce the fuse to PreToolUse-only** — under a goal that may not stop the session.

## The `/budget` commands and `/goal` auto-arm

**`/budget` commands run the CLI directly.** Each `commands/budget-*.md` instructs the agent to run `python .../cli.py <action>` with Bash and show the output. This costs ~1 turn but is reliable. An earlier "zero-turn" design (a `__BURNSTOP__` sentinel that a `UserPromptSubmit` hook blocked) **does not work in current Claude Code**: `UserPromptSubmit` fires on the raw `/budget-x` text *before* expansion and does not honor a hook blocking a slash command — confirmed from a real session transcript (see [spikes.md](spikes.md) §4b). Because the `PreToolUse` hook exempts burnstop's own CLI, these commands run even when the session is over budget; `cli.py` reads `CLAUDE_CODE_SESSION_ID` from the Bash env to target the right session.

**`/goal` auto-arm** (`dispatch.py` on `UserPromptExpansion`). That event fires *before* expansion with `command_name == "goal"`. If the session isn't already armed (no `BURNSTOP_BUDGET`, no arm file), `dispatch.py` auto-arms `BURNSTOP_GOAL_DEFAULT` (**$50**), forwarding to `cli.main` with `--session <id>` from the payload. An explicit budget is never overridden.

> Verify interactively: hooks don't fire in headless `claude -p` (see Limitations), so `/goal` auto-arm is confirmed by unit tests (`test_dispatch.py`) and a live in-session run, not by an automated `-p` test.

## Loop detection

Independent of the budget, the hook trips on a **structural loop**: it reads recent `tool_use` blocks from the transcript, hashes each `(tool_name, input)`, and halts if the last N (default 5, `BURNSTOP_LOOP`) are byte-identical — the signature of a stuck agent repeating the same failing call, which is what causes the classic overnight burn. This is a heuristic: it catches identical repetition reliably, not subtle semantic spinning. The budget cap is the deterministic backstop.

## Mid-session arming: how the CLI knows the session

A plain shell can't tell which Claude session it belongs to. The unlock: **Claude Code exports `CLAUDE_CODE_SESSION_ID` to every child process**, and the transcript is `~/.claude/projects/<project>/<session_id>.jsonl`. So `cli.py arm`, run from inside the session (a `!` command, the slash command, any shell-out), reads that env var and writes the arm file to the right per-session path. The hook gets the same id on its stdin payload. The arm file is keyed by `session_id` (concurrent sessions never collide) and lives under `~/.claude`, never in a repo.

## Packaging (as a plugin)

The repo is its own Claude Code plugin **and** marketplace, so installing is two commands and zero settings edits:

```
/plugin marketplace add phuryn/burnstop     # reads .claude-plugin/marketplace.json
/plugin install burnstop                     # reads .claude-plugin/plugin.json
```

- `.claude-plugin/plugin.json` declares the `commands` (the six `/budget*` files) and `hooks: "./hooks/hooks.json"`. On install, the four hooks and the `/budget` commands register automatically.
- `hooks/hooks.json` references the bundled scripts via `${CLAUDE_PLUGIN_ROOT}` (e.g. `python3 "${CLAUDE_PLUGIN_ROOT}/hook.py"`), and the command files run `python3 "${CLAUDE_PLUGIN_ROOT}/cli.py" <action>`, so paths resolve wherever the plugin lands.
- `.claude-plugin/marketplace.json` lists the single plugin at `source: "./"`, making the repo self-hosting as a marketplace.

Both hooks and commands invoke `python3`. On Windows (where `python3` may be a broken Store alias, **and** where Claude Code runs hooks through bash which eats backslashes) use the local installer instead — `python cli.py install` writes the four hooks and generates the commands using `sys.executable` with forward-slash paths. Use one path or the other, not both (double-registered hooks would run twice).

## Honest limitations

- **Bounded overshoot, not surgical.** Spend is read from completed turns, so the fuse is ~one turn behind, and a subagent already mid-run can't be killed (only the parent halts and new spawns are blocked). Set the cap with headroom and bound subagents with `maxTurns`. The guarantee is "won't run *far* past the cap," not "stops at the exact token/dollar."
- **Subscription unit.** On Pro/Max there's no per-token dollar bill; a dollar budget is computed from list prices as a proxy for usage burn. It can't read "messages left in your 5-hour window."
- **Headless `claude -p`.** Hooks loaded via `--settings` or project `.claude/settings.json` do **not** fire in `-p` mode (only user-scope settings are trusted headlessly). burnstop targets interactive sessions, where `/goal` and long agents actually run. See [tests.md](tests.md) for the live test caveat.
- **Why not a proxy** (which could be exact): routing a Pro/Max subscription through a third-party proxy violates Anthropic's ToS (Feb 2026). The native hook is the ToS-safe path. See [spikes.md](spikes.md).
