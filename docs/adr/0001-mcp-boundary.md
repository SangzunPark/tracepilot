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

The production transport was deferred to the Week 3 MCP integration. Week 3
selected and implemented stdio as the production MCP process boundary: the
diagnostic server runs as a subprocess via `python -m tracepilot.mcp`, and
`src/tracepilot/mcp/__main__.py` is the only production location that starts
the server.

## Consequence

The Week 3 transport decision is recorded above.