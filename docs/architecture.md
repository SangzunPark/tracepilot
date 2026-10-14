# TracePilot Architecture

## High-Level Architecture

User
  ↓
FastAPI
  ↓
LangGraph Investigation Runtime
  │
  ├── LLM
  │
  ├── Deterministic Policy Layer
  │
  ├── MCP Client
  │      ↓
  │   Incident Diagnostic MCP Server
  │      ↓
  │   Synthetic Domain Services
  │
  └── PostgreSQL
         ├── LangGraph checkpoints
         ├── investigations
         ├── evidence
         ├── tool runs
         ├── plan revisions
         └── action records

## Component Responsibilities

### FastAPI
Exposes the investigation lifecycle to external clients.

### LangGraph
Manages workflow state transitions, checkpointed execution, interrupts,
and resumption.

### LLM
Proposes:
- initial plans
- open questions
- hypotheses
- next tools and parameters
- observation interpretations
- plan revisions
- report drafts

### Deterministic Policy Layer
Enforces:
- tool allowlists
- schema validation
- retry limits
- duplicate prevention
- step budgets
- evidence validation
- completion rules
- approval rules
- idempotency
- safe-stop behavior

### MCP Client
Discovers and invokes diagnostic capabilities exposed through MCP.

### Incident Diagnostic MCP Server
Provides the bounded diagnostic tool interface.

### PostgreSQL
Stores:
1. LangGraph checkpoints.
2. Durable TracePilot application records.

## Investigation Modes

TracePilot exposes three investigation control policies over the same diagnostic domain and deterministic safety layer.

### Fixed Playbook
Uses a predefined investigation sequence.

- One global deterministic playbook, not one playbook per incident category.
- No LLM planning or replanning.
- Operates from the same `InitialInvestigationContext` as the other modes, and
  may use the same catalog/dependency information available to them.
- May deterministically expand service scope using the approved service catalog.
- Must not use hidden category or root-cause information, and must not invent a
  global fixed time window.
- Receives no hard-coded answer.

### Plan Once
Uses an LLM-generated initial plan, but plan revision is disabled after execution begins.

- The provider creates exactly one initial plan.
- The plan contains fully specified tool names and arguments before execution.
- Observations do not change the plan or later tool arguments.

### Adaptive
Uses the same underlying tool set and safety policies, but may revise the
current plan when observations invalidate assumptions.

- The provider creates an initial plan.
- After each fresh executed observation the provider reviews the plan: it may
  KEEP the current plan or REPLACE it (next tools, arguments, plan), and in
  either case replaces the lightweight working hypothesis and open questions.
- The deterministic runtime still permits or blocks every proposal.

Adaptive control flow must not allow a review-only loop:

    executed action → fresh observation
        ↓
    exactly one plan review (KEEP or REPLACE)
        ↓
    concrete investigation action, or structural termination
        ↓ (if the action is blocked by policy)
    next current-plan action (no review)

A plan-review opportunity is created only by a fresh observation from a
policy-permitted investigation action that reaches execution (an executed MCP
error counts). A policy-blocked attempt is not an observation and creates no
review. Both KEEP and REPLACE consume the opportunity, so the same observation
can never trigger a second review; `review → review` and
`review → blocked action → review` are impossible. Provider calls do not
consume the step budget (see Security Model), so this rule is what bounds
reviews.

### Plan Cursor and Routing

A concrete plan step is consumed once its action has been attempted, including
when deterministic policy blocks it. A blocked step therefore advances
`current_step_index` to the next existing step of the current plan:

- Fixed Playbook: the next playbook step; no replanning.
- Plan Once: the next initial-plan step; no replanning.
- Adaptive: the next step of the current plan. Policy feedback may be recorded,
  but no ad-hoc replacement action is created and no plan review occurs,
  because a blocked attempt produced no observation.

If there is no next step and no plan review is pending, the plan is exhausted
normally (`plan_exhausted`).

Cursor movement and plan-review routing belong to the mode loop. The Week 4
executor enforces only structural termination (for example, the step budget).

### Adaptive Plan Review (Week 4)

Approved rule: when a fresh executed observation occurs and budget remains, a
plan review is mandatory before the runner proceeds or allows plan exhaustion
to become terminal. `review_plan` is called exactly once per such
observation, whether or not the current plan has steps left; the decision
itself may be KEEP or REPLACE.

`plan_decision_at_step` is the latest agent step whose fresh executed
observation has already been consumed by a KEEP/REPLACE decision. A review is
pending exactly when an executed observation is newer than it. It stays 0 for
Fixed Playbook and Plan Once.

- KEEP: plan, `plan_version`, and `current_step_index` are unchanged;
  working hypothesis and open questions are replaced by the decision values;
  `plan_decision_at_step = step_count`. KEEP is valid even when the plan is
  exhausted; the next structural check then ends `plan_exhausted`.
- REPLACE: `plan` = the proposal's plan (future actions only),
  `plan_version += 1`, `current_step_index = 0`,
  `plan_decision_at_step = step_count`, and working hypothesis and open
  questions are replaced. An empty plan is valid and leads to
  `plan_exhausted`. Replacement actions are not sanitized, reordered,
  truncated, or rewritten; deterministic policy handles every action.

Routing rules for the Adaptive runner:

1. Budget exhausted: `budget_exhausted`, before any further review.
2. Plan exhausted but a review is pending and budget remains: do not
   terminate; run the review once first.
3. After the review: KEEP with an exhausted plan, or REPLACE with an empty
   plan, ends `plan_exhausted`; REPLACE with a non-empty plan continues.
4. Final plan step blocked: no observation, no review; an exhausted plan ends
   `plan_exhausted`.

Termination (assuming awaited provider and executor calls return or raise):
every concrete action attempt, blocked or not, consumes one agent step;
provider calls consume none; each fresh executed observation yields at most
one review; both KEEP and REPLACE set `plan_decision_at_step = step_count`,
so the next progress event after a review is a concrete action attempt or
structural termination. The shared step budget therefore bounds execution,
and reviews are bounded by executed actions.

All Adaptive provider calls (initial planning, every plan review, synthesis)
use `ProviderView.from_state(state, incident, executor.max_steps)`.

### Adaptive Decision Trace and Provider Accounting (planned)

Revision history is not stored in `InvestigationState`. The Adaptive runner
will keep an append-only plan-decision trace outside the current state;
`AdaptiveRunResult` will contain the final state, the synthesis, the
`initial_plan`, and `decisions`, a sequence of `PlanDecisionRecord`s (KEEP
decisions are recorded too). Each record preserves at least: `after_step`;
`trigger` (`mid_plan` or `plan_exhausted`, describing whether the old plan
still had unconsumed steps); `decision` (`keep` or `replace`);
`previous_plan_version` and `new_plan_version`; `previous_plan` and
`previous_cursor`; `replacement_plan` (or none); previous and replacement
working hypothesis; previous and replacement open questions. Later
persistence maps these to durable plan-revision records. This trace is what
makes wrong-hypothesis and replanning evaluation possible.

Cross-mode provider-call accounting will use a shared `MeteredProvider`
instrumentation wrapper owned by the evaluation harness, measuring per
operation: calls, failures, latency, and token usage where available.
Expected completed-run provider operations:

| Mode | initial planning | plan review | synthesis |
|---|---|---|---|
| Fixed Playbook | 0 | 0 | 1 |
| Plan Once | 1 | 0 | 1 |
| Adaptive | 1 | D | 1 |

D is the number of fresh executed observations reviewed before termination,
and D ≤ executed actions ≤ the step budget. Adaptive decision metrics (review
decisions, KEEP decisions, REPLACE decisions) come from the decision trace.
Nothing is added to `InvestigationState` for provider-cost accounting.

### Execution Error Boundary (Week 4)

- Policy block: MCP is not called; no observation.
- MCP tool error returned as `CallToolResult(is_error=True)`: executed; error
  observation; no retry.
- MCP read timeout (`MCPError` with code `REQUEST_TIMEOUT`): executed; error
  observation; no retry. The session remains usable.
- Any other `MCPError` or client exception: a transport/session failure, not
  incident evidence. The executor raises `ToolExecutionError` with the original
  cause, records nothing, and the Week 4 investigation aborts outside
  `InvestigationState`. No failure termination reason exists; Week 6 adds
  recovery/runtime-failure semantics.

The per-call timeout default (10 seconds) is a provisional engineering default.
The evaluation timeout is recorded during preregistration/final-run
configuration. At setup, the MCP server must expose exactly the approved tool
set; missing or extra tools fail setup, and the allowlist is never widened from
discovery.

### Shared LangGraph Runtime (Week 4)

LangGraph is the application-level dispatch and runtime boundary. The public
entry point is `run_investigation(case_id, mode, incident, context, executor,
provider)` in `tracepilot.investigation.graph`; it returns the mode runner's
own result (`FixedRunResult`, `PlanOnceRunResult`, or `AdaptiveRunResult`)
unchanged.

    START -> route by InvestigationMode -> fixed_playbook | plan_once | adaptive -> END

- Mode selection is deterministic: the requested `InvestigationMode` alone
  picks the node. The router makes no provider call, consumes no agent step,
  and reads no data. An invalid mode is rejected with `ValueError` before the
  graph is invoked. The provider never chooses or sees the mode.
- The existing mode runners (`run_fixed_playbook`, `run_plan_once`,
  `run_adaptive`) remain the semantic execution kernels and stay directly
  callable. Each mode node awaits its runner unchanged; the graph does not
  reimplement plan execution, plan review, or termination.
- `ToolExecutor` and `Provider` arrive per invocation through LangGraph's
  runtime context, never through graph state, input, or output.
- Graph state holds only the request (case ID, mode, incident brief, initial
  context) and the runner's result. It does not duplicate `InvestigationState`
  fields, so there is a single source of truth for plan, cursor, tool calls,
  hypothesis, and termination.
- Runner exceptions propagate unchanged; the graph adds no retry policy,
  catch-and-convert behavior, or partial result.
- In Week 4 the graph is compiled without a checkpointer. Week 6 may refactor
  toward finer-grained graph transitions if mid-run durable resume requires
  it, using parity tests against the direct runners as the regression guard.

### Shared Conditions

All three modes share:

- the same incident data
- the same available MCP tools
- the same model/provider when applicable
- the same budgets
- the same timeout policy
- the same deterministic safety policy, including runtime-pinned `case_id`
  (see Security Model)
- the same future completion policy
- the same future approval policy

Mode difference is control policy. Investigation data access differs only
because of each mode's control policy, not because any mode receives privileged
information.

The implementation mechanism is a Week 4 implementation choice.
The modes may share one graph with policy-controlled routing or use
mode-specific graph composition, provided the evaluation keeps all
non-control-policy conditions equivalent.

### Initial Investigation Context

The runtime accepts a typed `InitialInvestigationContext`. It may contain shared
case-level starting information such as focus services, an initial question or
manifest summary as appropriate, and optional tool-specific initial constraints.
For the same case, Fixed Playbook, Plan Once, and Adaptive receive the identical
context.

Initial constraints are tool-specific, not one global investigation time range.
For example:

- INC-013: the initial 13:50–14:15 range applies to the initial
  deployment-history search, not automatically to every metrics, log, or status
  query.
- INC-015: the initial context is based on focus services and an initial
  question, not a global time range.

The final Python representation is not designed here.

Case-specific initial context is evaluation/preregistration configuration. It is
not hidden ground truth and not an `IncidentManifest` field.

There is no single default initial time window applied to all cases, and no
global window such as incident start minus N minutes. Cases without a
preregistered time constraint have no case-level time-window restriction from
`InitialInvestigationContext`. Exact evaluation defaults will be finalized in
preregistration where needed.

The preregistered contexts live in
`evaluation/preregistration/eval-preregistration-v1.yaml` (see Evaluation Plan,
Evaluation Inputs and Preregistration). The evaluation harness loads them and
passes the same context to every mode; runners never read the file.

### LLM Provider Interface

Week 4 defines a provider Protocol/interface and uses a deterministic scripted
provider in tests. No provider SDK (OpenAI, Anthropic, or other) is added in
Week 4. A real provider dependency requires a later explicit human decision.

The provider has three operations, none of which consumes the step budget:

- `propose_initial_plan(view) -> InvestigationPlan` (Plan Once, Adaptive)
- `review_plan(view) -> KeepPlanDecision | RevisionProposal` (Adaptive only;
  discriminated by `decision`): KEEP carries replacement working hypothesis
  and open questions only; REPLACE (`RevisionProposal`) also carries a
  replacement plan of future actions only
- `synthesize(view) -> SynthesisProposal`: shared final synthesis, not a
  completion decision

There is no next-action operation; concrete actions come from the current plan.

Providers receive only a `ProviderView`: runtime-visible investigation state
plus an `IncidentBrief` (incident start time, affected services, symptom) that
the runner supplies. The view excludes `case_id`, the manifest title, and all
evaluation data (category, ground truth, required/acceptable evidence,
root-cause answer keys). The provider module has no repository access.

The view also does not expose the investigation mode, directly or through a
replacement field. Plan Once and Adaptive share `propose_initial_plan`, and
modes differ only in control policy, so a provider must not condition its
planning or synthesis on the experimental condition.

Provider failures are raised to the caller and stay outside
`InvestigationState` in Week 4; there is no failure termination reason.

### Structural Termination and Shared Synthesis

Week 4 structural termination reasons are limited to control-flow conditions:

- `plan_exhausted`
- `budget_exhausted`

There is no provider-controlled "conclude" authority. The LLM has no final
completion authority in Week 4.

After structural termination, all three modes use the same final synthesis
interface. The synthesis result can carry:

- a root-cause claim
- cited observable evidence IDs
- a concise explanation

Final synthesis occurs outside the investigation action-step budget.

This shared synthesis step is not the deterministic completion evaluator. The
Week 5 deterministic completion evaluator will later decide whether a diagnosis
is sufficiently supported to count as completed.

## LLM / Deterministic Boundary

| LLM proposes | Deterministic code enforces |
|---|---|
| Initial plan | Tool allowlist |
| Open-question proposal | Schema validation |
| Hypothesis | Step budget |
| Next-tool proposal | Retry limit |
| Tool arguments | Duplicate prevention |
| Observation interpretation | Evidence-ID validation |
| Replanning proposal | Completion gate |
| Report draft | Approval enforcement |
| — | Idempotency |

## Completion Validation

The deterministic completion layer does not independently determine the semantic truth of a root-cause hypothesis.

It validates structural completion conditions such as:

- required investigation questions are resolved,
- required evidence categories are present,
- referenced evidence records exist,
- critical unresolved questions are absent,
- budget and approval conditions are satisfied,
- confirmed claims satisfy predefined evidence requirements.

Semantic interpretation of observations and hypothesis formation remain LLM responsibilities.

## Approval Boundary

Consequential action proposal, approval waiting, and side-effect execution
must be separated into distinct workflow stages.

    propose_action
        ↓
    approval_gate
        ↓
    execute_action

The approval gate must not perform non-idempotent side effects before `interrupt()`.