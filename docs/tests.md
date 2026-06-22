# Tests

Stdlib `unittest`, no dependencies. Run the full (fast, offline) suite:

```bash
python -m unittest discover -s tests -v
```

## Unit suite (runs on CI)

| File | Covers |
|---|---|
| `test_meter.py` | token sums; dedupe by `message.id` (keep last); subagents inline + in a separate `subagents/*.jsonl`; cross-file dedupe; loop detection (`is_looping`, `recent_tool_signatures`) |
| `test_pricing.py` | per-model price resolution (incl. date-suffixed ids and unknown=$0); per-turn cost (opus is 5x haiku; cache rates); **mixed-model** session cost; subagent cost; the `$0.10` and `$1` boundaries |
| `test_hook.py` | budget parsing (dollars + tokens); resolution precedence (env vs arm file, with unit); the pure `evaluate()` verdict (halt/warn/allow in $ and tok, loop); per-event `render()` (Stop = `continue:false`; PreToolUse = permission `deny`) |
| `test_cli.py` | `arm` (tokens + dollars) sets baseline; `reset` re-baselines and keeps the cap; `disarm` removes the file; error paths. Real `meter`, temp projects dir, patched `arm_path` (never touches real `~/.claude`) |
| `test_dispatch.py` | `/budget` sentinel parsing → CLI args + `decision:block` output (incl. default-to-`status`, fallback reason); `/goal` auto-arm of `$50` when unarmed, and **not** clobbering an explicit budget. `cli.main` mocked, so offline |
| `test_version.py` | `meter.VERSION` equals the top CHANGELOG heading **and** `.claude-plugin/plugin.json` version |

All of these are pure/offline and isolated via temp dirs and mocks. CI (`.github/workflows/tests.yml`) runs them on Python 3.9 / 3.11 / 3.12.

## Live test (NOT on CI)

`test_live_goal.py` makes **real `claude -p` calls** (real cost) to prove what unit tests can't: that burnstop's `Stop`-hook `continue:false` actually halts a session **and overrides a `/goal`-style block**. It is `@skipUnless` gated:

```bash
BURNSTOP_LIVE=1 python -m unittest tests.test_live_goal -v
```

Without `BURNSTOP_LIVE=1` (and a `claude` CLI on PATH) it skips, so GitHub never runs it.

**What it does, and why it's safe.** Hooks loaded via `--settings` or a project `.claude/settings.json` do **not** fire in `claude -p` (only user-scope settings are trusted headlessly — an empirical finding from building this). So the test temporarily injects hooks into `~/.claude/settings.json` and restores it in `tearDown`. Every injected hook is a **no-op unless `BURNSTOP_TEST=1`**, which only the child `claude -p` run sets — so it can never trap your own session. The fixture `tests/fixtures/_gated_block.py` simulates an unmet `/goal` by returning `decision: block`; `always_block.py` is the un-gated variant kept for reference.

**The assertion.** It runs two headless sessions under the goal-block: one where burnstop can't trip (`$999` cap) — the block forces the full `--max-turns` leash — and one with a `$0.30` cap, which should stop *before* the leash. `test_turns < control_turns` proves `continue:false` overrode the block. (The control's `> 1` guard also surfaces the case where headless hooks don't fire at all.)

> Note: this is the live verification flagged in [spikes.md](spikes.md) §2b. Running it requires temporarily editing user settings, which an agent's safety classifier will (correctly) block — so run it yourself.
