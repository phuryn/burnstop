# Contributing

burnstop is maintained by [Paweł Huryn](https://www.productcompass.pm) (pawel@productcompass.pm). Contributions are welcome — a bug fix, a typo, or a new idea.

## How to contribute

- **Bugs and small fixes** — open a PR directly.
- **Larger changes** — open an issue first so we can discuss the approach.

## Guidelines

- Keep PRs focused — one change per PR.
- **Stdlib only, Python 3.8+** — no third-party dependencies.
- **Run the tests before submitting:** `python -m unittest discover -s tests -v` (the live test in `tests/test_live_goal.py` is skipped unless `BURNSTOP_LIVE=1`).
- Keep the budget logic in **pure functions** (`hook.evaluate` / `hook.render`, `meter`, `dispatch.handle_*`) so it stays unit-testable without a live session. See [AGENTS.md](AGENTS.md) and [docs/architecture.md](docs/architecture.md).
- **User-facing CLI output stays ASCII** — Claude Code runs hooks through bash and a Windows console is cp1252; fancy punctuation renders as junk.
- `meter.VERSION`, the top `CHANGELOG.md` heading, and `.claude-plugin/plugin.json` version must match (a parity test enforces it). Bump them together.
- Every contributor will be listed publicly.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
