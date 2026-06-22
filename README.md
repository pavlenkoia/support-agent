# support-agent

Application-first skeleton for the **Агент поддержки** project.

## Included baseline
- FastAPI app with `/health` and inbound message stub
- PostgreSQL service in Docker Compose
- background worker placeholder
- external knowledge-layer contract
- pytest smoke tests

## Project layout
- `app/` — application code
- `deploy/` — Dockerfiles and env templates
- `docs/` — local repo docs and specs
- `scripts/` — helper scripts
- `migrations/` — DB migrations placeholder

## External knowledge layer

The support knowledge base is intentionally stored **outside** this repository.

Current local development path:
- `/home/tian/support-agent-kb`

The application points to it through:
- `KNOWLEDGE_BACKEND=filesystem`
- `KNOWLEDGE_ROOT=/home/tian/support-agent-kb`

This keeps the application repo separate from the durable support KB and keeps the KB available to Hermes/backend flows as an external knowledge layer.

## Quick start

### Local test run
```bash
cd /home/tian/support-agent
uv run pytest -q
```

### Start with Docker Compose
```bash
cp deploy/env/app.env.example deploy/env/app.env
docker compose up --build
```

### Health check
```bash
curl http://127.0.0.1:8000/health
```

## Agent tooling for work with the repository

`Graphify` is initialized for **agent/developer work with the repo**, not as part of the application runtime contract.

Current state:
- project-scoped Codex integration installed into `.codex/` and `AGENTS.md`;
- git hooks installed via `graphify hook install`;
- initial code graph generated into `graphify-out/` from `app/`;
- `graphify query`, `graphify path`, and `graphify explain` can already be used against `graphify-out/graph.json`.

Useful commands:
```bash
graphify query "routing service" --graph graphify-out/graph.json
graphify path "RoutingService" "HermesBackendService" --graph graphify-out/graph.json
graphify update app
```

See also:
- `docs/developer/agent-tooling.md`
