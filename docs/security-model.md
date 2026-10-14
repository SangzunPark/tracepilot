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

## Case ID Ownership

The investigation runtime owns and pins `case_id`. The LLM/provider must not
choose or change the case being investigated.

For case-scoped MCP tools, the runtime injects the current `case_id`
deterministically. The provider's proposed tool arguments do not control
`case_id`. `search_runbooks` is the exception, because that tool has no
`case_id`.

Reason: cross-case access would invalidate evaluation isolation.

## Deterministic Controls

- runtime-pinned `case_id`
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

## Duplicate-Call Identity

A tool call's identity is the tool name plus its normalized semantic arguments.
Normalization:

- omitted defaults and explicitly supplied defaults are the same call where the
  tool schema defines those defaults
- omitted and `None` values are normalized as appropriate to the tool contract
- datetime values are normalized to a single representation
- argument-key ordering does not affect identity

Week 4: identical duplicate calls are blocked. Week 4 defines no recovery
exceptions. The Week 6 deterministic recovery policy may later allow a retry
(see Recovery Classification).

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

One investigation/agent step is one control-policy action attempt. A step is
consumed when the mode attempts an investigation action, including:

- a permitted tool request
- a duplicate request blocked by policy
- an invalid/forbidden request blocked by policy

`agent_steps`, `logical_tool_calls`, and `execution_attempts` are separate
counts (see Evaluation Plan):

- A blocked invalid, forbidden, or duplicate action proposal consumes one agent
  step but does not increment logical tool calls or execution attempts. It may
  return deterministic policy feedback, and the mode must then propose another
  concrete investigation action. A blocked attempt is not a new observation and
  does not create an Adaptive plan-review opportunity.
- A policy-permitted tool action that proceeds to MCP execution consumes one
  agent step and increments both logical tool calls and execution attempts.

The maximum step budget applies to investigation action attempts. Planning,
plan-review, and final synthesis provider calls do not themselves consume it.
Adaptive review-only loops are prevented by the Adaptive plan-review rule
(see Architecture, Adaptive Plan Review).

## Secrets

Do not persist API keys, credentials, or raw secret values.

Avoid storing full prompts or sensitive raw payloads in logs.

## Week 1 Spike Limitation

The durable-execution spike validates the approval/resume path only.

Edit and reject semantics, including edited-action revalidation, are
deferred to the Week 6 approval workflow implementation.