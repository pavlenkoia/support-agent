# support-agent

Application-first skeleton for the **Агент поддержки** project.

## Included baseline
- FastAPI app with `/health`, inbound message flow, and Telegram gateway slice
- PostgreSQL service in Docker Compose
- polling worker for Telegram updates
- external knowledge-layer contract
- bounded support-agent loop with KB + tool gathering
- provider-aware LLM client with bounded retries/backoff for transient failures
- grounded fallback answers when KB is present but the final LLM answer step fails
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

## Reliability notes

- Direct-answer and summary LLM clients use configurable timeout / retry settings for transient provider failures.
- If KB facts were already gathered and the final answer-generation call fails, the runtime degrades to a short grounded answer synthesized from the retrieved KB instead of a template refusal.
- Broad but clearly in-domain openers (for example `Подскажите пожалуйста по прыжкам`) should prefer a short KB overview before asking clarification.

