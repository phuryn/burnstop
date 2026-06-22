# Feasibility spikes

The design decisions behind burnstop, and the dead ends that were ruled out. Recorded so a future contributor doesn't re-walk them.

## 1. Why a hook, not a proxy

The obvious design for a token budget is a proxy in front of the model API that counts tokens and refuses the next completion when the cap is hit. It was **rejected**:

- **It breaks Anthropic's Terms of Service on a subscription.** As of Feb 2026 Anthropic prohibits forwarding consumer (Pro/Max) OAuth tokens through any third-party tool, proxy, or gateway; accounts have been banned for it. A proxy only stays in-ToS with a Console **API key**, which is a different (and smaller) audience and separate billing.
- A transparent intercept (point `api.anthropic.com` at a local proxy via the hosts file) doesn't dodge this — it still forwards a subscription token to third-party code, and needs a local CA install. There is no cert pinning to defeat, so it's *technically* possible, but it's the same ToS violation done covertly.

So burnstop is a **native `PreToolUse` hook**. Nothing leaves the machine, no token is forwarded, and it works on a subscription within ToS.

## 2. Which event actually stops a run? (corrected)

First cut used a `PreToolUse` hook returning `{"continue": false}`. Follow-up research disagreed on whether `PreToolUse` even honors `continue:false` (one pass said it halts the whole session, another said `PreToolUse` only supports permission decisions). Rather than bet on the ambiguity, burnstop registers on **both** events and lets each do what it's definitely good at:

- **`Stop` (and `SubagentStop`)** → top-level `{"continue": false}`. This is the reliable session stop (every source agrees Stop supports it). Stop fires after each turn, so the fuse trips at the next turn boundary.
- **`PreToolUse`** → a permission **`deny`** (which is universally supported) so the agent can't keep calling tools mid-turn, *plus* `continue:false` as a belt-and-suspenders in case the running build honors it here.

`PreToolUse` is still useful because it fires before **every** tool (Bash, Edit/Write, all MCP tools) — a universal choke point that freezes spending fast; `Stop` is what actually ends the session.

Metering needs no proxy: the hook gets `transcript_path`, and the transcript JSONL records `message.usage` per turn. The hook sums it. Caveat: the in-flight turn isn't in the transcript yet when the hook fires, so the fuse is **~one turn behind** (set the cap with headroom).

## 2b. Does it beat `/goal`? (the make-or-break)

`/goal` keeps a session running until a condition is met — exactly the feature that can burn unbounded spend. The question: does a fuse halt actually stop a goal-driven loop, or does the goal resurrect it?

**Finding (per docs research):** `/goal` is itself implemented as a **Stop hook** that returns "continue" while its condition is unmet. A top-level **`continue: false`** from another hook **takes precedence over any event-specific "continue" decision** — and if *any* Stop hook returns `continue:false`, the session stops. So burnstop's `Stop` hook returning `continue:false` **wins over an active `/goal`**; the goal cannot resurrect it. There is **no** way for a hook to programmatically run `/goal clear`, so winning via `continue:false` (not `decision: block`) is the only route — which is exactly what burnstop does.

This is also why a `PreToolUse`-only design (the first cut) was a latent bug: under a goal it might not have stopped at all. Registering the `Stop` hook is what makes the fuse real.

> Residual verification worth doing live: install the `Stop` hook with a `$0.01` cap in a throwaway session under a `/goal` and confirm it halts. The logic and precedence are confirmed by docs; a live smoke test would close the loop.

## 3. Mid-session arming — the session-identity unlock

Open question during design: a separate `burnstop arm` process can't know *which* live session it belongs to, so how do you arm a cap mid-session without guessing?

**Spike result (resolved):** Claude Code exports **`CLAUDE_CODE_SESSION_ID`** to every child process. Verified in a live session:

```
CLAUDE_CODE_SESSION_ID = 05c0311f-d596-4645-84b9-768dfe123c42
transcript            = ~/.claude/projects/<project>/05c0311f-...jsonl
```

So a command run from inside the session (a `!` command, a slash command, any shell-out) inherits the id and writes the arm file to the correct per-session path. Mid-session arming is therefore clean, not a hack. The arm file is keyed by `session_id` (concurrent sessions never collide) and lives under `~/.claude`, never in a repo.

## 4. Subagents — what's feasible

Requirement: one budget covers the parent **and** its subagents.

- **Accounting: feasible.** All subagents share the parent's `session_id`, and `PreToolUse` fires inside subagents too. Subagent turns carry `isSidechain: true` and the same `sessionId`, inline in the parent transcript and/or `subagents/*.jsonl`. `meter.session_spend` sums both and dedupes by `message.id`, so total spend includes subagents.
- **Real-time halt of a running subagent: not feasible.** There's no "kill this subagent" in the hook API; `{"continue": false}` halts the parent loop. You can block *new* subagent spawns (the spawn is itself a `PreToolUse`), but a subagent already mid-run finishes its current work.

**Consequence (documented, not papered over):** the guarantee is "the session won't run *far* past the cap," not "stops at the exact token." Bound the overshoot with a conservative cap plus a per-subagent `maxTurns`.

## 4b. The `/budget` commands — a sentinel design that DIDN'T work, and the fix

First attempt (clever, wrong): `/budget-x` would expand to a `__BURNSTOP__ x` sentinel, and a `UserPromptSubmit` hook would match it, run the action, and `{"decision":"block"}` the prompt for a zero-turn command. Doc research claimed `UserPromptSubmit` sees the *expanded* text.

**A live session transcript disproved it.** Typing `/budget-status` produced: (line 5) the raw `/budget-status`, (line 6) the expanded `__BURNSTOP__ status` sent **to the model**, then the agent investigating it. So in current Claude Code, `UserPromptSubmit` fires on the **raw** slash text *before* expansion (the sentinel never matches), and a hook does **not** block a slash command from running. The zero-turn interception is not achievable here.

**Fix (reliable):** each `/budget-x` command instructs the agent to run `python .../cli.py <action>` with Bash and show the output. ~1 turn, but it works. Two supporting pieces:
- The `PreToolUse` hook **exempts Bash calls running burnstop's own `cli.py`** (`hook._is_burnstop_cli`), so `/budget` works even when the session is over budget (otherwise the fuse would deny the very command that clears it).
- `/goal` auto-arm still rides `UserPromptExpansion` (fires *before* expansion with `command_name`), which works as a side-effect (it doesn't need to block). `dispatch.py` now only handles that event.

## 4b-bis. Windows: hooks run through bash, which eats backslashes

`cli.py install` first wrote hook commands as `C:\Python313\python.exe ...`. Claude Code runs hooks through bash even on Windows, and bash strips the backslashes → `C:Python313python.exe: command not found`. Every hook errored. **Fix:** write hook commands (and the generated command files) with **forward slashes** (`C:/Python313/python.exe`) — bash accepts them and it's a no-op on POSIX. `sys.executable.replace("\\", "/")`.

## 4c. Packaging as a plugin

A single `.claude-plugin/plugin.json` registers the four hooks + the `/budget` commands on `/plugin install`, with hook commands referencing bundled scripts via `${CLAUDE_PLUGIN_ROOT}` — no manual `settings.json` edit. The repo doubles as its own marketplace (`.claude-plugin/marketplace.json`, `source: "./"`). This is the same distribution model as `phuryn/pm-skills`, and the friendliest for a hooks+commands tool. (Windows users use `cli.py install` instead: `python3` and `${CLAUDE_PLUGIN_ROOT}` backslash-paths both trip the bash issue above.)

## 5. Schema facts the meter relies on

Verified against real transcripts (June 2026):

- `message.usage` keys: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`. The cap is compared against their sum (cache tokens are billable and can dominate a long session — in a real test, 12.66M tokens over 66 turns was mostly cache reads).
- Dedupe by `message.id`, keep last: Claude Code writes several records per response and only the last carries final tallies.
