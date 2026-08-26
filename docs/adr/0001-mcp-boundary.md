# ADR 0001 — MCP Diagnostic Boundary

## Context

TracePilot requires diagnostic capabilities to be exposed through MCP.

## Observation

The Week 1 spike validated:

- client/server communication
- tool discovery
- generated input/output schemas
- structured results
- domain-error propagation

using the MCP Python SDK's in-memory client/server test path.

## Decision

Use MCP as the diagnostic capability boundary.

The production transport remains deferred until the Week 3 MCP integration.

## Consequence

Week 3 must explicitly choose and document the runtime transport before the
production diagnostic server is finalized.