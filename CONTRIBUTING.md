# Contributing to Pulse OS

Thanks for helping improve Pulse OS! This guide covers how to propose changes, the required standards, and how to run checks so your contribution lands cleanly.

## Ways to contribute
- File bugs and feature requests in [GitHub Issues](https://github.com/weirdtangent/pulse-os/issues) with clear repro steps and expected behavior.
- Improve docs or examples (README, docs/, samples).
- Submit pull requests for fixes or new functionality (small, focused PRs are preferred).
- Security reports go through the process in [`SECURITY.md`](SECURITY.md) instead of issues/PRs.

## Development setup
1) Use Python 3.13+.
2) Install [uv](https://docs.astral.sh/uv/) if you don't have it.
3) Install dependencies: `uv sync --all-extras --dev`.

## Required checks (run locally before opening a PR)
- `uv run ruff check .` (lint)
- `uv run black --check .` (format, 120 cols)
- `uv run pytest` (tests; add/adjust tests for your changes)
- If you touch release or packaging logic, ensure `release.config.js`, `CHANGELOG.md`, and version metadata stay coherent (semantic versioning).

CI reruns these checks (plus CodeQL and dependency auditing) on every PR and main-branch push.

## Coding standards
- Style is enforced by Black (120 columns) and Ruff; prefer type hints where practical.
- Keep log messages clear and actionable; avoid leaking secrets or personal data.
- Update docs/comments when behavior or interfaces change (config keys, MQTT topics, CLI flags, APIs).

## Testing expectations
- Add or update tests when fixing bugs or adding functionality.
- Prefer focused, deterministic tests; avoid network access in unit tests.
- For integration work, document any prerequisites in `TESTING.md`.

## Commit and PR hygiene
- Reference related issues in PR descriptions.
- Describe user-facing impact, risk, and test coverage in the PR body.
- Keep PRs small and reviewable; split large changes when possible.

## Security and privacy
- Follow the responsible disclosure flow in [`SECURITY.md`](SECURITY.md).
- Do not include secrets in code, tests, or git history; rotate immediately if something slips.
- Use HTTPS or SSH for cloning/fetching and verify hashes/signatures when applicable.

Thanks again for contributing!
