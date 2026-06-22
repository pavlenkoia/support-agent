# Support Agent Architecture Overview

This repository follows an application-first baseline:
- FastAPI application as the core system
- PostgreSQL as system of record
- direct LLM path by default
- Hermes as optional advanced backend

Agent tooling such as Graphify is intentionally treated as a **developer/agent workflow layer outside the application runtime** and is documented separately in `docs/developer/agent-tooling.md`.
