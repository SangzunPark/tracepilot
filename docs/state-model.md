# TracePilot State Model

## InvestigationState

Graph state contains the minimal structured information needed to continue an investigation.

Candidate fields:

- task_id
- thread_id
- user_goal
- current_plan
- plan_version
- plan_revision_ids
- open_questions
- hypothesis_ids
- evidence_ids
- recent_tool_run_ids
- retry_counts
- remaining_step_budget
- pending_action_id
- approval_status
- completion_status

## Week 4 Interim State Contract

The Week 4 Three Modes runtime uses a compact, serializable interim state:

- case_id
- mode
- initial_context
- plan
- plan_version
- current_step_index
- bounded tool-call / observation records
- step_count
- open_questions
- lightweight working_hypothesis
- termination_reason

The candidate fields above remain the longer-term design.

Tool-call / observation records are kept inline, compact, and bounded only until
durable tool-run storage exists. The principle that large raw tool results stay
out of graph state still applies.

Explicitly deferred to Week 5/6, and not designed here:

- full Evidence model/provenance
- durable Hypothesis records
- PlanRevision durable records
- PostgreSQL tool-run/history storage
- retry counts
- approval state
- deterministic completion status
- recovery/injection instrumentation

## State Storage Principle

Active working data may remain inline when it is small and required for the next graph decision; historical or audit data should live in durable records and be referenced by ID.

Large raw tool results should not be stored directly in graph state.

For example, `current_plan`, `open_questions`, and `user_goal` may remain directly in graph state because they are small and frequently needed during execution.

By contrast, full evidence, tool-run history, plan revisions, and action records should remain in durable storage.

Example:

InvestigationState:

    evidence_ids = ["ev_001", "ev_004"]
    recent_tool_run_ids = ["tool_010", "tool_011"]

Application database:

    ev_001 -> full structured evidence record
    ev_004 -> full structured evidence record

    tool_010 -> full ToolRun record
    tool_011 -> full ToolRun record

## Identifiers

### task_id

Application-level public identifier for an investigation.

### thread_id

LangGraph persistence identifier used to locate checkpoint history.

The v1 implementation may map them one-to-one, but they serve conceptually different roles.

## Domain Entities

- Investigation
- Plan
- PlanRevision
- OpenQuestion
- Hypothesis
- Evidence
- ToolRun
- ActionProposal
- ActionExecution
- CompletionResult