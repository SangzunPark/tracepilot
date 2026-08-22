# TracePilot Agent Development Guidelines

## Project principle

TracePilot is developed using a human-led, agent-assisted workflow.

The human developer owns:
- project scope
- architecture
- state design
- LLM/deterministic boundaries
- tool permissions
- evidence sufficiency rules
- completion criteria
- evaluation ground truth
- evaluation metrics
- interpretation of results

Coding agents assist with implementation, testing, debugging, refactoring,
repository analysis, and repetitive engineering work.

## Before modifying code

Before making changes:

1. Read the relevant files.
2. Explain the current behavior.
3. Identify the files that would be affected.
4. Propose a small implementation plan.
5. Identify technical and safety risks.
6. Describe the tests required.
7. Wait for approval before modifying files.

## Scope rules

Do not:

- change architecture without explicit approval
- add dependencies without explicit approval
- expand the task beyond the current issue
- weaken tool permissions or approval requirements
- silently change public schemas
- modify evaluation ground truth to improve results
- modify preregistered metrics or success criteria after evaluation begins
- remove failing tests merely to make CI pass
- modify Git configuration, branch strategy, or repository history without explicit approval
- modify CI/CD workflows or quality-gate definitions without explicit approval

## Implementation rules

Prefer:

- small, reviewable changes
- typed interfaces
- deterministic validation around LLM outputs
- explicit error handling
- tests for new behavior
- minimal dependencies

After implementation:

1. Run `uv run ruff check .`.
2. Run `uv run ruff format --check .`.
3. Run `uv run mypy src tests`.
4. Run `uv run pytest`.
5. Summarize changed files.
6. Explain any remaining risks or assumptions.