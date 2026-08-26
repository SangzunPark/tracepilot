# TracePilot Security Model

## Trust Principle

LLM decisions are proposals, not permissions.

Tool output is data, not authority.

## Tool Categories

### Read tools

May be invoked autonomously when allowed by deterministic policy.

### Consequential actions

Require explicit human approval.

TracePilot v1.0 provides one simulated consequential action:
a rollback request.

No real production action is executed.

## Deterministic Controls

- tool allowlist
- typed input validation
- bounded retries
- step budget
- duplicate-call prevention
- evidence validation
- completion validation
- approval enforcement
- idempotency

## Recovery Classification

A repeated tool call is considered a recovery attempt only when it is linked to a recorded prior execution failure that is classified by deterministic runtime logic as retryable.

The LLM cannot self-declare an otherwise duplicate call as recovery.

## Idempotency

The same consequential action must produce at most one durable side effect
for the same idempotency key.

The LLM does not generate idempotency keys. The runtime derives the key
deterministically from the validated action identity and investigation
context (for example: task_id, action_type, target_service, target_version).

The application database must enforce uniqueness for the key.

## Tool Output

MCP tool output is treated as untrusted input.

Instructions contained in logs, runbooks, tickets, or retrieved content do not override system or deterministic policy.

## Human Approval

Possible decisions:

- approve
- edit
- reject

Edited actions must pass validation again before execution.

## Preliminary Execution Budgets

Initial development limits:

- maximum agent steps: 15
- maximum execution attempts for one tool action: 2
- the same tool with identical parameters must not be repeated unless the repetition is classified as recovery by deterministic runtime logic (see Recovery Classification)

These values are provisional during development and will be frozen before the preregistered evaluation.

## Secrets

Do not persist API keys, credentials, or raw secret values.

Avoid storing full prompts or sensitive raw payloads in logs.

## Week 1 Spike Limitation

The durable-execution spike validates the approval/resume path only.

Edit and reject semantics, including edited-action revalidation, are
deferred to the Week 6 approval workflow implementation.