# AI-Assisted Development Log

TracePilot uses a human-led, agent-assisted development workflow.

This document records meaningful uses of coding assistants, especially where
the assistant's proposal required human correction or verification.

## Foundation repository review

### Issue

Review the initial TracePilot repository and development workflow before application development begins.

### Human-owned decision

The repository architecture, quality gates, dependency policy, and coding-agent constraints remain human-owned.

### Coding-agent contribution

The coding agent performed a read-only review of the uv project configuration, CI workflow, smoke test, `.gitignore`, and `AGENTS.md`.

### Initial findings

The agent found that:

- the uv project and Python 3.12 configuration were internally consistent,
- local quality checks matched CI,
- no dependency or packaging problems were present,
- the root-level `main.py` was unused,
- `AGENTS.md` did not explicitly protect CI configuration, agent-governance files, or test strength.

### Human correction

The human developer accepted the removal of the unused `main.py` and strengthened `AGENTS.md` to:

- explicitly require both Ruff lint and format checks,
- protect CI and quality-gate configuration,
- prevent autonomous weakening of agent-governance instructions,
- prevent tests or assertions from being weakened merely to pass CI.

### Verification

The following checks were run after the changes:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy src tests`
- `uv run pytest`

### Lesson learned

A coding assistant can identify workflow and governance gaps without being given permission to modify the repository. Review-first usage keeps architectural and process decisions under human control.