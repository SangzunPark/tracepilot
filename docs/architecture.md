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

### Plan Once
Uses an LLM-generated initial plan, but plan revision is disabled after execution begins.

### Adaptive
Uses the same underlying tool set and safety policies, but may revise the
current plan when observations invalidate assumptions.

The implementation mechanism is intentionally deferred until Week 4.
The modes may share one graph with policy-controlled routing or use
mode-specific graph composition, provided the evaluation keeps all
non-control-policy conditions equivalent.

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