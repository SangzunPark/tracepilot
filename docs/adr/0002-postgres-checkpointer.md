# ADR 0002 — PostgreSQL-backed LangGraph Checkpointing

## Context

TracePilot must survive process termination while an investigation is in
progress or waiting for approval.

## Observation

The Week 1 spike demonstrated that an interrupted graph could be resumed from
a new Python process by reusing the same thread ID and PostgreSQL checkpoint.

The node containing `interrupt()` was re-entered from its beginning during
resume.

## Decision

Use PostgreSQL-backed LangGraph checkpointing for durable execution.

## Consequences

- local development requires PostgreSQL
- checkpoint migrations must be initialized
- code before `interrupt()` must be replay-safe
- consequential side effects should be isolated from approval-gate logic
The spike validated the approve path only. Edit and reject resume
semantics remain unverified as of Week 1.