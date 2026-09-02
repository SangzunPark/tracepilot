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




### Issue
Ground-truth leakage check scope (Week 2, Session 2.1)

### Human-owned decision
Which agent-visible data must be free of root-cause phrasing.

### Initial proposal
Extend the verbatim alias check to cover shared runbooks as well as
case fixtures, on the grounds that runbooks are agent-visible.

### Human correction
Reverted. Shared runbooks are general operational knowledge and must be
able to name failure modes to be useful at all. Naming a failure mode is
not the same as revealing which incident it caused. Split the two checks:
forbidden ground-truth keys are banned everywhere agent-visible; verbatim
root-cause aliases are checked only against case-specific data.

### Lessons learned
"Agent-visible" is not one category. Case-scoped observable data and
shared organizational knowledge have different leakage semantics.