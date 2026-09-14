# TracePilot Evaluation Plan

## Evaluation Type

TracePilot uses a controlled synthetic behavioral evaluation suite.

It is designed to compare the behavior, effectiveness, safety, and
operational cost of different incident-investigation strategies under
controlled conditions.

It is not intended to establish statistical superiority or serve as a
general benchmark for autonomous incident-investigation systems.

## Evaluation Question

When does adaptive investigation provide enough behavioral benefit to justify
its additional operational cost?

The evaluation is therefore designed not only to measure whether Adaptive
succeeds more often, but also whether any additional success is accompanied
by greater tool use, agent steps, latency, token consumption, or estimated
cost.

---

## Modes

### Fixed Playbook

Uses a predefined diagnostic sequence.

The diagnostic order is fixed before execution and does not change in
response to observations.

### Plan Once

Generates one initial investigation plan before execution begins.

The plan cannot be revised after execution begins, even when later
observations weaken or invalidate assumptions underlying the original plan.

### Adaptive

Generates an initial investigation plan and may revise hypotheses, open
questions, investigation steps, and next-tool choices when observations
invalidate assumptions underlying the current plan.

---

## Controlled Comparison

Where applicable, the following conditions should remain identical across
modes:

- model and model version
- diagnostic tool set
- tool descriptions and schemas
- incident data
- execution budgets
- timeouts
- evidence rules
- completion rules
- approval policy

Differences in outcome should therefore be attributable as far as practical
to the investigation strategy rather than unrelated configuration changes.

---

## Ground-Truth Isolation

Ground truth is available only to the evaluation layer.

Required-evidence satisfaction and root-cause correctness are computed by the
evaluation runner after an investigation run completes.

The runtime investigation graph, diagnostic services, LLM, and completion
evaluator must never read evaluation ground truth.

## Case Taxonomy

### Effectiveness Cases

| Category | Cases |
|---|---:|
| Simple single-cause | 8 |
| Wrong initial hypothesis | 4 |
| Tool recovery | 3 |
| Insufficient or conflicting evidence | 2 |
| **Total effectiveness cases** | **17** |

### Safety Regression Cases

| Category | Cases |
|---|---:|
| Approval required | 1 |
| Prompt injection | 2 |
| **Total safety cases** | **3** |

## Adversarial-Case Data Isolation

Normal runbooks are shared across incidents.

Prompt-injection cases may require case-scoped runbook overrides or fixtures;
the exact isolation mechanism is deferred to the Week 3 adversarial-case design.

### Total

20 cases.

Safety-regression cases are evaluated separately from mode-effectiveness
comparisons.

---

## Case Intent

### Simple Single-Cause Cases

These cases test whether all modes can solve straightforward incidents
without unnecessary investigation overhead.

They are also intended to reveal cases where Adaptive provides little or no
accuracy benefit while consuming additional steps, tool calls, tokens, or
latency.

### Wrong-Initial-Hypothesis Cases

These cases contain evidence that encourages an initially plausible but
incorrect investigation direction.

They are designed to test whether Adaptive can detect failed assumptions,
revise the investigation strategy, and recover more reliably than Plan Once.

### Tool-Recovery Cases

These cases introduce bounded diagnostic-tool failures.

They test whether TracePilot can recover through allowed retry or alternative
investigation behavior without exceeding deterministic retry and execution
limits.

### Insufficient-or-Conflicting-Evidence Cases

These cases intentionally prevent a strongly grounded root-cause conclusion.

They test whether the system can stop safely rather than produce unsupported
certainty.

### Approval-Required Case

This case tests whether a consequential action proposal is blocked until
explicit human approval is provided.

### Prompt-Injection Cases

These cases place instruction-like malicious or misleading text inside
retrieved diagnostic content.

They test whether tool output remains treated as untrusted data rather than
authority capable of overriding system or deterministic policy.

---

## Preliminary Behavioral Hypothesis H2

For the four wrong-initial-hypothesis incidents:

Adaptive should achieve stable success on at least 3/4 cases and should
achieve stable success on at least two more cases than Plan Once.

This is a behavioral acceptance criterion, not a statistical significance
threshold.

The criterion remains provisional until `eval-preregistration-v1`.

---

## Repeated-Run Aggregation

Run-level outcomes are the primary evaluation record.

For challenge cases with three valid runs, a case may additionally be
classified as a stable success when at least 2/3 runs satisfy the predefined
case-success criteria.

Example:

    Run 1: success
    Run 2: failure
    Run 3: success

    Raw result: 2/3 successful runs
    Stable success: yes

The stable-success classification does not replace run-level reporting.

Both individual run outcomes and aggregate case-level classification must be
retained.

---

## Invalid Run Policy

A provider-level outage or infrastructure failure that prevents TracePilot
from executing may be rerun and must be documented.

Examples may include:

- model-provider outage
- external infrastructure failure
- network failure that prevents execution from beginning

A failure caused by TracePilot's own planning, tool selection, recovery,
state management, evidence handling, policy enforcement, or completion logic
is a valid evaluation outcome and must not be discarded.

Invalid-run handling must not be used to remove unfavorable system behavior.

---

## Metrics

### Correctness

- root-cause correctness

### Grounding

- required evidence satisfied
- unsupported claim count

### Behavior

- replan count
- successful replan
- unnecessary tool calls
- duplicate non-recovery calls

### Recovery

- recovery success
- safe-stop correctness

### Safety

- approval compliance
- forbidden-action count
- prompt-injection instruction followed

### Operations

- logical tool calls
- execution attempts
- recovery attempts
- agent steps
- input tokens
- output tokens
- wall-clock latency
- estimated cost

---

## Metric Interpretation

### Logical Tool Calls

Counts diagnostic operations intentionally requested by the investigation
logic.

A retry of the same logical operation does not create a new logical tool
call when it is explicitly classified as recovery.

### Execution Attempts

Counts actual attempts to execute diagnostic tool operations.

For example, one logical tool call followed by one retry results in:

    logical tool calls = 1
    execution attempts = 2

### Recovery Attempts

Counts execution attempts performed specifically as bounded recovery from a
previous failed attempt.

### Successful Replan

A replan is considered successful when a revision prompted by new evidence
or failed assumptions materially advances the investigation toward the
predefined case-success criteria.

---

## Reporting Rules

For subsets with fewer than 10 cases, report fractions first.

Example:

> Adaptive succeeded in 3/4 wrong-hypothesis cases.

Percentages may be included secondarily but should not replace the underlying
counts.

Do not claim statistical significance.

Results should be described as behavioral observations from the controlled
synthetic evaluation suite.

Safety-regression cases must be reported separately from mode-effectiveness
comparisons.

Run-level outcomes must remain available even when stable-success
classifications are reported.

Operational cost should be reported alongside effectiveness so that improved
accuracy is not interpreted independently of additional execution cost.

---

## Evaluation Freeze

The evaluation will be frozen in stages:

    cases + ground truth
           ↓
    adversarial screening
           ↓
    eval-cases-v1
           ↓
    hypotheses / metrics / success criteria / run configuration
           ↓
    eval-preregistration-v1
           ↓
    final evaluation

### `eval-cases-v1`

Freezes:

- evaluation cases
- case categories
- incident inputs
- ground truth
- required evidence expectations
- safety-regression content

### `eval-preregistration-v1`

Freezes:

- behavioral hypotheses
- success criteria
- repeated-run rules
- invalid-run policy
- evaluation metrics
- execution budgets
- model and version
- run configuration
- timeouts
- relevant policy configuration

After `eval-preregistration-v1`, evaluation criteria must not be modified in
response to observed final results.

The actual repository tags are not created during the current development
stage.

## Ground-Truth Isolation

Ground truth is available only to the evaluation layer.

Required-evidence satisfaction and root-cause correctness are computed by the
evaluation runner after an investigation run completes.

The runtime investigation graph, diagnostic services, LLM, and completion
evaluator must never read evaluation ground truth.

Required-evidence satisfaction is computed by the evaluation runner after a run completes.