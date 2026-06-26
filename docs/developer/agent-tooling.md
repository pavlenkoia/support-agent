# Agent tooling: Graphify and repo navigation

## Purpose

This document covers **agent/developer tooling around the repository**. It is not part of the application runtime spec.

## Graphify status

Graphify is installed and initialized for this repository.

Completed initialization:
- `uv tool install graphifyy`
- `graphify install --project --platform codex`
- `graphify hook install`
- `graphify install --platform hermes`
- initial graph extraction over `app/`

Created artifacts:
- `AGENTS.md`
- `.codex/hooks.json`
- `.codex/skills/graphify/`
- `graphify-out/graph.json`
- `graphify-out/GRAPH_REPORT.md`

## What works now

The current initialized graph is a working **code graph** for the application source tree.

Examples:
```bash
graphify query "routing service" --graph graphify-out/graph.json
graphify explain "RoutingService" --graph graphify-out/graph.json
graphify path "RoutingService" "HermesBackendService" --graph graphify-out/graph.json
```

To refresh the code graph after edits:
```bash
graphify update .
```

## Scope rule for this project

- Graphify is a **must-have companion tool for agent work with the repo**.
- The support KB is intentionally external to this repository.
- The application itself does **not** depend on Graphify at runtime.
