# AGENTS.md

Guidance for any coding agent (Codex, Claude Code, etc.) working on this repository.

> **Naming note.** burnstop *instruments* Claude Code, so "Claude Code" below refers to that product (the source of the JSONL transcripts and the host of the hooks) — not to the agent reading this file. The agent working on the codebase is "the coding agent" or "you".

## What this is

A **native, ToS-safe token budget for Claude Code.** Arm a cap on a session and burnstop halts the run cleanly when spend (parent **+ subagents**) crosses it — no proxy, no API key, no OAuth forwarding. It's a `PreToolUse` hook plus a tiny CLI. See [README.md](README.md) for the user-facing pitch and [docs/spikes.md](docs/spikes.md) for why it's a hook and not a proxy.

## Project shape

Three Python files, **stdlib only**, no `pip install` step. Python 3.8+.

- [meter.py](meter.py) — read-only accounting. Sums `message.usage` across a session's JSONL transcripts (parent + subagents), dedupes by `message.id`, **prices each turn by its own model** (`PRICING` / `get_pricing` / `turn_cost`), and surfaces recent tool-call signatures for loop detection. Holds `VERSION`.
- [hook.py](hook.py) — the fuse: `PreToolUse` / `Stop` / `SubagentStop` entrypoint. Resolves the budget (tokens or dollars), recomputes spend, and a pure `evaluate()` returns a verdict (`halt` / `warn` / `allow`) that `render()` turns into the right output for the event.
- [dispatch.py](dispatch.py) — `UserPromptExpansion` only: auto-arms `$50` on `/goal` (forwards to `cli.main`). The `/budget` commands do NOT go through here — they run the CLI directly via Bash (current Claude Code won't let a hook block a slash command). See [docs/spikes.md](docs/spikes.md) §4b.
- [cli.py](cli.py) — `arm` / `reset` / `disarm` / `status` / `default` / `install`. `install` generates the `/budget-*` command files (direct-run) and registers the four hooks with forward-slash paths.

Ships as a Claude Code plugin: `.claude-plugin/plugin.json` + `marketplace.json`, `hooks/hooks.json`, `commands/budget*.md`. Full design: [docs/architecture.md](docs/architecture.md).

Use `python` on Windows, `python3` on macOS/Linux.

## Common commands

```
python cli.py install                 # register all 5 hooks + the /budget command into ~/.claude/settings.json
python cli.py install --scope project # ...or into ./.claude/settings.json
python cli.py arm $1                    # cap at $1 of model spend from now (incl. subagents) — run INSIDE a session
python cli.py arm 200k                 # ...or cap by tokens
python cli.py reset                    # fresh allowance, keep the cap
python cli.py status                   # spend (tokens and $) vs cap for the current session
python cli.py disarm                   # remove the cap
python cli.py --version

# end users install as a plugin instead:  /plugin marketplace add phuryn/burnstop  &&  /plugin install burnstop
# and use /budget arm $1 ... in-session (auto-arms $50 on /goal)

python -m unittest discover -s tests -v                    # full suite (CI runs this)
python -m unittest tests.test_meter -v                     # one file
python -m unittest tests.test_hook.TestDecide.test_trip_on_budget   # one test
```

You can also arm at launch with no file at all: `BURNSTOP_BUDGET=200k claude`.

## Architecture

The full design — data flow, transcript schema, the per-model cost model, budget resolution, the Stop-vs-`/goal` precedence, the `/budget` dispatcher and `/goal` auto-arm, subagent handling, the plugin manifest, and the bounded limitations — lives in **[docs/architecture.md](docs/architecture.md)**. Read it before changing `meter.py` / `hook.py` / `dispatch.py`.

One-line shape: five hooks (`hook.py` on PreToolUse/Stop/SubagentStop; `dispatch.py` on UserPromptSubmit/UserPromptExpansion) call `meter.session_spend()`, which recomputes spend from Claude Code's own transcripts on every call — **no ledger, nothing written during a run**, so there's no staleness and no read-modify-write race across concurrent subagent hooks. The halt is `Stop` returning `{"continue": false}`, which beats `/goal`. Two load-bearing invariants to preserve: dedupe transcript records by `message.id` (keep last), and price each turn by its own model then sum (never aggregate tokens first).

## Testing notes

- Tests use a temp projects root (`meter.session_spend(..., root=tmp)`) and `unittest.mock` for env/arm-file/`cli.main` — they never read the user's real `~/.claude`.
- The pure functions (`hook.evaluate` / `hook.render`, `dispatch.handle_*`) carry the logic with no I/O, so trip/warn/allow, the `/budget` sentinel, and `/goal` auto-arm are all unit-testable without a live session.
- `tests/test_version.py` enforces `meter.VERSION ==` the top CHANGELOG heading **and** `.claude-plugin/plugin.json` version. Bump them together.

## Versioning and releases

[SemVer](https://semver.org/). **`CHANGELOG.md` is the canonical version reference**; tags and GitHub Releases are automatic projections of it. Adapted from phuryn/claude-usage (minus the `.vsix` build — burnstop has no extension, so the release just tags and publishes the CHANGELOG section as notes).

Release flow:
1. Work accumulates under a `## vX.Y.Z — TBD` heading at the top of `CHANGELOG.md`. Bump `meter.VERSION` to match when you write the heading (the parity test enforces it).
2. To release: change `TBD` → today's date, merge to `main`, push.
3. [`.github/workflows/tag-on-merge.yml`](.github/workflows/tag-on-merge.yml) sees the new `## vX.Y.Z` heading in the push diff, creates the lightweight tag, and publishes a GitHub Release with that CHANGELOG section as the notes. Idempotent; no-ops on pushes that don't add a heading.

### CHANGELOG conventions

The workflow trusts the format. Every release entry:

```
## vX.Y.Z — TBD

### <Area>

- One bullet per change, past tense, link a PR/issue with #N, credit contributors with `thanks @login`.
```

| Field | Required form | Why |
|---|---|---|
| Heading | `## vX.Y.Z` — exactly two `#`, `v` prefix, three numeric parts | The workflow regex `^## v[0-9]+\.[0-9]+\.[0-9]+` ignores anything else. |
| Date | `TBD` while accumulating; real `YYYY-MM-DD` at merge-to-`main` | A `TBD` shipped to main reads unfinished forever. |
| Bump | Patch by default; minor for a user-visible feature; major only for a break | No automation picks the bump; you do, when writing the heading. |

The `TBD → date` swap is the one thing a human must remember at release time.

## House style

- **Keep user-facing CLI output and code string literals ASCII** (no em dashes / fancy punctuation) — a Windows console is cp1252 and renders them as junk (`�`). Markdown prose can use em dashes.
- Match the surrounding code: stdlib only, type-free Python, docstrings that explain the *non-obvious* invariant rather than restating the signature.
