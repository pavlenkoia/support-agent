# support-agent

Application-first skeleton for the **Агент поддержки** project.

## Included baseline
- FastAPI app with `/health`, inbound message flow, and Telegram gateway slice
- PostgreSQL service in Docker Compose
- polling worker for Telegram updates
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

This repository only defines the application-side contract to an external knowledge source.
Deployment-specific paths and environment values must be provided locally and are **not** documented here with machine-specific absolute paths.

## Quick start

### Local test run
```bash
uv run pytest -q
```

### Start with Docker Compose
```bash
cp deploy/env/app.env.example deploy/env/app.env
# then edit deploy/env/app.env locally:
# - set KNOWLEDGE_ROOT to your external KB path
# - set TELEGRAM_BOT_TOKEN if you want live Telegram polling
docker compose up --build
```

### Health check
```bash
curl http://127.0.0.1:8000/health
```

