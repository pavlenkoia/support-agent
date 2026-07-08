# support-agent

Application-first skeleton for the **Агент поддержки** project.

## Included baseline
- FastAPI app with `/health` and normalized inbound message flow
- PostgreSQL service in Docker Compose
- polling transport workers for Telegram and VK
- Telegram gateway slice for bot polling delivery
- VK gateway slice via VK Bots Long Poll API for community direct messages
- read-only VK dialog viewer web UI on port `3002`
- internal probe session API for governor-driven live runtime checks without customer-channel traffic
- external knowledge-layer contract
- bounded support-agent loop with planner -> tool/KB gathering -> finalize
- separate KB agent for Karpathy-style wiki navigation and selective page reading
- external hot-editable prompt files for the customer-facing agent and the KB agent
- provider-aware LLM client with bounded retries/backoff for transient failures
- grounded fallback answers when KB is present but the final LLM answer step fails
- transport persistence for raw events, outbound-send reconciliation, and VK human-override state
- pytest smoke tests

## Project layout
- `app/` — application code
- `viewer-web/` — React + Tailwind VK dialog viewer
- `deploy/` — Dockerfiles and env templates
- `docs/` — local repo docs and specs
- `scripts/` — helper scripts
- `migrations/` — DB migrations placeholder

## External knowledge layer

The support knowledge base is intentionally stored **outside** this repository.

This repository only defines the application-side contract to an external knowledge source.
Deployment-specific paths and environment values must be provided locally and are **not** documented here with machine-specific absolute paths.

Current runtime shape:
- external `SYSTEM_PROMPT.md` for the customer-facing support agent
- external `KB_AGENT_PROMPT.md` for the wiki-reading KB agent
- compiled external KB pages used as the factual source of truth
- optional runtime tools (for example weekday/date checks) before final answer generation

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
# - set VK_ENABLED=true, VK_GROUP_ID, and VK_ACCESS_TOKEN if you want live VK polling
docker compose up --build
```

### Health check
```bash
curl http://127.0.0.1:8000/health
```

### VK dialog viewer
After `docker compose up -d`, open:
- `http://127.0.0.1:3002` — React/Tailwind viewer UI
- `GET /api/viewer/dialogs?day=YYYY-MM-DD` — VK dialog list for the selected day
- `GET /api/viewer/dialogs/{conversation_id}/messages?day=YYYY-MM-DD` — selected dialog messages for the selected day

### Internal probe sessions
The repository also exposes an operator/internal-test probe slice that reuses the real support runtime without sending traffic into customer channels:
- `POST /api/internal/probe/sessions`
- `GET /api/internal/probe/sessions`
- `GET /api/internal/probe/sessions/{session_id}`
- `POST /api/internal/probe/sessions/{session_id}/messages`
- `POST /api/internal/probe/sessions/{session_id}/wait-reply`
- `GET /api/internal/probe/sessions/{session_id}/messages`
- `GET /api/internal/probe/sessions/{session_id}/trace`
- `POST /api/internal/probe/sessions/{session_id}/close`

Current UI contract:
- light theme by default, with header-right light/dark toggle
- theme toggle is icon-only; visible text is moved to tooltip/accessibility labels
- compact sticky top header for app identity + theme toggle
- left panel header contains the date picker only (no extra title/label/counter)
- left panel items show `case_id` when available, otherwise `display_name/external_chat_id`
- dialog list time and message-pane time are both rendered from full timestamps in the browser's current timezone
- dialog list is sorted by earliest message time first for the selected day
- on narrow/mobile screens the viewer switches to a master-detail flow: list -> selected chat -> `← К списку`
- right panel has no separate desktop header above the messages; on mobile it may show only the compact back control
- left and right panels scroll independently, and the mobile chat pane must scroll to the last message without bottom clipping

## Transport notes

### Supported channels in the current repository
- `telegram` — polling worker + gateway adapter
- `vk` — VK Bots Long Poll worker + gateway adapter for community direct messages

### VK first-version scope
- inbound customer message: `message_new`
- outgoing community activity event: `message_reply`
- target surface: direct messages to the VK community
- out of scope for v1: VK chats / besedy and history backfill

### VK manual-admin override rule
- if a `message_reply` matches a recent bot send, it is reconciled as `sent_by=bot`
- if a `message_reply` does not match a recent bot send, it is treated as a live admin reply
- a live admin reply activates silence for 1 hour from the last such reply
- inbound messages during that window are still stored, but the bot does not answer
- the worker re-checks override state immediately before `messages.send` to avoid stale auto-replies racing a human admin

### VK worker recovery behavior
- transient VK long-poll transport failures (`transport_error:*`, including read timeouts and `connection reset by peer`) are treated as empty polls so the worker keeps running
- the Docker Compose `vk-worker` service uses `restart: unless-stopped` so the container auto-recovers if the process exits unexpectedly

## Reliability notes

- Direct-answer, KB-agent, and summary LLM clients use configurable timeout / retry settings for transient provider failures.
- VK long-poll transport failures that surface as transient network errors must not terminate the worker loop.
- The stronger model should be allocated to the KB agent / wiki-reading step; the cheaper model can be used for the final customer-facing answer step.
- If KB facts were already gathered and the final answer-generation call fails, the runtime degrades to a short grounded answer synthesized from the retrieved KB instead of a template refusal.
- Broad but clearly in-domain openers (for example `Подскажите пожалуйста по прыжкам`) should prefer a short KB overview before asking clarification.
- Date/tool paths must not loop on repeated `use_tool` after the tool result is already present; the next step must advance to KB or answer generation.
- The KB agent now has an optional hardened runtime mode with deterministic navigation, optional coverage-review skip, minimal extraction schema, and tolerant JSON recovery for structured-output drift.

For rollout notes and the production/test split, see `docs/architecture/kb-agent-hardening.md`.

