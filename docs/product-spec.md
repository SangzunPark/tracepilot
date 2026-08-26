# TracePilot Product Specification

## 1. Product Definition

TracePilot is a bounded autonomous incident investigation agent that plans
diagnostic work, selects tools dynamically, gathers grounded evidence,
revises its investigation strategy when assumptions fail, survives execution
interruptions, and requires human approval before consequential actions.

## 2. Problem

Fixed diagnostic workflows are effective when the investigation path is
known in advance, but they become brittle when early assumptions are wrong,
expected evidence is unavailable, or observations require a different
investigation path.

TracePilot explores whether bounded adaptive investigation can handle these
situations while maintaining deterministic safety, evidence, and execution
controls.

## 3. Target User

The primary user is a software or incident-response engineer who initiates an
investigation, reviews evidence and reasoning, and approves or rejects
consequential action proposals.

## 4. Core Use Cases

- investigate a synthetic software incident
- collect diagnostic evidence across multiple sources
- revise an investigation when early assumptions fail
- recover from bounded diagnostic-tool failures
- stop safely when evidence is insufficient
- pause for human approval
- resume an investigation after process restart
- compare Fixed, Plan Once, and Adaptive investigation strategies

## 5. Core Capabilities

- initial investigation planning
- dynamic diagnostic-tool selection
- structured evidence collection
- hypothesis tracking
- observation-based replanning
- bounded failure recovery
- persistent execution state
- interrupt and resume
- deterministic completion evaluation
- approval-gated consequential action
- idempotent simulated action execution
- behavioral evaluation

## 6. Non-goals for v1.0

- real production rollback
- multi-agent orchestration
- multiple MCP servers
- pgvector
- OpenTelemetry
- Prometheus
- Grafana
- cloud deployment
- second LLM provider
- streaming UI
- autonomous production remediation

## 7. OpsPilot vs TracePilot

OpsPilot:
The graph largely determines the execution path.

TracePilot:
The agent selects the investigation path, while graph structure and
deterministic policies constrain its allowed execution space.

## 8. v1.0 Success Criteria

TracePilot v1.0 must demonstrate:

1. MCP-based diagnostic-tool discovery and invocation.
2. Dynamic investigation planning and tool selection.
3. Evidence-linked hypotheses.
4. Meaningful replanning when investigation assumptions fail.
5. Bounded recovery from selected tool failures.
6. Persistence across a real process restart.
7. Human approval before a simulated consequential action.
8. Idempotent action execution.
9. Controlled comparison of Fixed, Plan Once, and Adaptive modes.
10. Transparent accuracy-versus-cost analysis.