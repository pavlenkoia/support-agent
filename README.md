# support-agent

Application-first skeleton for the **Агент поддержки** project.

## Included baseline
- FastAPI app with `/health` and normalized inbound message flow
- PostgreSQL service in Docker Compose
- polling transport workers for Telegram and VK
- Telegram gateway slice for bot polling delivery
- VK gateway slice via VK Bots Long Poll API for community direct messages
- read-only VK dialog viewer web UI on port `3002`, including installable online-first PWA shell
- internal probe session API for governor-driven live runtime checks without customer-channel traffic
- external knowledge-layer contract
- production `simple_llm_wiki` path: semantic catalog navigation -> selective full-page reads -> coverage review -> grounded extraction -> finalization
- legacy planner loop and `simple_full_corpus` remain non-production compatibility paths
- separate KB agent for Karpathy-style wiki navigation and selective page reading
- reviewed, versioned prompt/profile source compiled into a release artifact for the customer-facing agent and KB agent
- provider-aware LLM client with bounded retries/backoff for transient failures
- ordered multi-key failover for Mistral/openai-compatible roles via `*_API_KEYS` pools
- failover observability in LLM trace/usage summaries without exposing secrets
- fail-closed finalization: exhausted final-model recovery returns `retry_pending` with no customer message
- transport persistence for raw events, outbound-send reconciliation, and VK human-override state
- pytest smoke tests

## Project layout
- `app/` — application code
- `viewer-web/` — React + Tailwind VK dialog viewer
- `deploy/` — Dockerfiles and env templates
- `docs/` — local repo docs and specs
- `scripts/` — helper scripts
- `migrations/` — DB migrations placeholder

## Versioned profile and external runtime mount

Reviewed prompts and factual Wiki source live under `deploy/profile-source/`. `scripts/build_runtime_profile.py` validates and compiles that committed source into the ignored local artifact `deploy/runtime-profile/`. Production mounts an external runtime copy. `scripts/release.py` independently rebuilds the current source and refuses a release unless both the local artifact and external runtime tree match that rebuild exactly, including absence of stale extra files. Runtime paths, generated artifacts, and secrets remain local and are not committed.

Current runtime shape:
- generic `SYSTEM_PROMPT.md` for role and communication behavior only
- generic `KB_AGENT_PROMPT.md` for semantic wiki navigation and grounded extraction only
- curated `deploy/profile-source/kb/runtime-facts.v1.json` is the sole source for the one-call runtime KB; it contains semantic IDs, fact text, applicability conditions, and logical source references
- `scripts/build_runtime_profile.py` validates and promotes that registry as `kb/knowledge-base.v1.json`; it never infers customer facts by splitting Wiki Markdown
- optional runtime tools (for example weekday/date checks) before final answer generation
- provider key pools via `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS` (CSV, first key primary, later keys reserve/failover)

## Quick start

### Local quality checks
```bash
uv run ruff check .
uv run pytest -q
```

Ruff is a pinned development dependency. The project configuration intentionally ignores FastAPI's `B008` dependency-injection pattern and `RUF001` for legitimate Cyrillic text. Existing review-required warnings remain visible; do not run a repository-wide `ruff --fix` without an approved scope.

### Start with Docker Compose (local development only)
```bash
mkdir -p /home/tian/support-agent-runtime
cp deploy/env/app.env.example /home/tian/support-agent-runtime/app.env
# then edit /home/tian/support-agent-runtime/app.env locally:
# - set KNOWLEDGE_ROOT to your external KB path
# - set TELEGRAM_BOT_TOKEN if you want live Telegram polling
# - set VK_ENABLED=true, VK_GROUP_ID, and VK_ACCESS_TOKEN if you want live VK polling
# - optionally enable VIEWER_AUTH_ENABLED=true and set VIEWER_AUTH_KEY for viewer-web access control
docker compose up --build
```

### Production release

Do **not** deploy production by selecting individual Compose services. First build a fresh ignored artifact from the clean committed source, promote that exact tree to the external runtime profile through the reviewed profile-swap procedure, then run:

```bash
python3 scripts/release.py
```

This is the only supported production service-release command: it first rebuild-verifies `deploy/runtime-profile/` against the committed source and verifies exact external runtime-profile parity, then rebuilds and force-recreates all application-plane services (`app`, `worker`, `vk-worker`, `viewer-web`, `viewer-push-worker`) without recreating PostgreSQL. It writes a non-secret receipt only after version, source-manifest, health, worker-liveness, and controlled fail-closed finalizer checks pass. See [production release operations](docs/operations/production-releases.md).

By default the Compose stack reads runtime env from `${SUPPORT_AGENT_RUNTIME_ROOT_HOST:-/home/tian/support-agent-runtime}/app.env` rather than from a repo-local `.env` file.

### Health check
```bash
curl http://127.0.0.1:8000/health
```

### VK dialog viewer
After `docker compose up -d`, open:
- `http://127.0.0.1:3002` — React/Tailwind viewer UI
- `GET /api/viewer/auth/me` — current viewer auth state
- `POST /api/viewer/auth/login` — accepts `{ "key": "..." }`, sets a long-lived `HttpOnly` session cookie
- `POST /api/viewer/auth/logout` — clears the viewer session cookie
- `GET /api/viewer/dialogs?day=YYYY-MM-DD` — VK dialog list for the selected day (requires viewer auth when enabled)
- `GET /api/viewer/dialogs/{conversation_id}/messages?day=YYYY-MM-DD` — selected dialog messages for the selected day (requires viewer auth when enabled)
- `GET /api/viewer/push/config` — public VAPID key for an authenticated PWA client
- `POST` / `DELETE /api/viewer/push/subscriptions` — create/update or disable the current browser push subscription

Web Push is opt-in: after login, the operator presses the header bell and grants the browser permission. Enable it only on an HTTPS viewer origin. Runtime secrets stay in the external `app.env`: `VIEWER_PUSH_VAPID_PRIVATE_KEY` and `VIEWER_PUSH_VAPID_SUBJECT`; the public key is exposed only through the authenticated config endpoint. The `vk-worker` creates committed inbound-message outbox records and `viewer-push-worker` sends them, with a payload containing only conversation/case identifiers, never message text. Production defaults use high urgency, a 24-hour TTL, a 90-second stale-worker lease reclaim, and a persistent/vibrating native notification for backgrounded devices. When viewer is open, it refreshes its data silently because Android can suppress a system notification for a foreground PWA. Deploy Web Push changes to **both** worker services as well as `app` and `viewer-web`, then verify a newly created outbox event reaches `delivered`.

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

Current UI/runtime contract:
- viewer opens on a one-field login screen when viewer auth is enabled
- successful login is persisted with a long-lived `HttpOnly` cookie (configurable, current default 365 days)
- viewer-web is shipped as an installable online-first PWA with `manifest.webmanifest`, service worker registration, standalone display mode, and app icons
- the PWA intentionally caches only the static app shell/assets; dialog API payloads are not aggressively persisted for offline history in v1
- if the installed shell is opened without network before auth/data can load, the UI shows a clear offline state instead of a blank screen
- theme remains light by default, with header-right light/dark toggle after login
- theme toggle is icon-only; visible text is moved to tooltip/accessibility labels
- compact sticky top header for app identity + theme toggle
- left panel header contains the date picker plus an icon-only refresh button for reloading the currently selected date (no extra title/label/counter)
- on touch/mobile screens, a clear horizontal swipe in the dialog list switches days: left-to-right selects the previous day and right-to-left the next; the list follows the finger, snaps back on an incomplete gesture, and exits in the swipe direction before reloading without a separate loading card or incoming-list animation
- left panel items show `№<case_id> Имя пользователя` when `case_id` exists, otherwise `display_name/external_chat_id`
- inbound VK messages are labeled from cached `users.display_name` when available; the VK gateway populates missing names via `users.get` and falls back to id when lookup is unavailable
- dialog list time and message-pane time are both rendered from full timestamps in the browser's current timezone
- dialog list is sorted by earliest message time first for the selected day
- on narrow/mobile screens the viewer switches to a master-detail overlay flow: tapping a list item slides the selected chat in from right to left over the list, and swipe-back or the icon-only back button returns to the list
- the mobile detail header uses an icon-only back control plus the real current `case_id` label (`Обращение №...`) when available
- right panel has no separate desktop header above the messages; on mobile it keeps only that compact detail header
- left and right panels scroll independently, and the mobile chat pane must scroll to the last message without bottom clipping
- the backend viewer API is read-only but protected by the same auth layer when enabled
- an enabled PWA subscription receives a native notification when the viewer is closed; an already-open viewer refreshes dialogs/messages without a duplicate system banner

## Transport notes

### Supported channels in the current repository
- `telegram` — polling worker + gateway adapter
- `vk` — VK Bots Long Poll worker + gateway adapter for community direct messages

### VK first-version scope
- inbound customer message: `message_new`
- outgoing community activity event: `message_reply`
- target surface: direct messages to the VK community
- inbound `message_new` handling should opportunistically enrich missing `users.display_name` via `users.get` and cache it for the viewer/runtime
- VK Market attachments on inbound `message_new` are normalized into the routing-only agent text so references such as "данная услуга" carry the attached service title, description, price text, and card identifiers into Wiki/finalization. Viewer history still stores the customer's original message text without this synthetic attachment context.
- out of scope for v1: VK chats / besedy and history backfill

### VK manual-admin override rule
- if a `message_reply` matches a recent bot send, it is reconciled as `sent_by=bot`
- if a `message_reply` does not match a recent bot send, it is treated as a live admin reply, persisted in dialog history as `human`, and shown in viewer-web as `Оператор VK`
- a live admin reply activates silence for 1 hour from the last such reply
- inbound messages during that window are still stored, but the bot does not answer
- the worker re-checks override state immediately before `messages.send` to avoid stale auto-replies racing a human admin

### VK worker recovery behavior
- transient VK long-poll transport failures (`transport_error:*`, including read timeouts and `connection reset by peer`) are treated as empty polls so the worker keeps running
- the Docker Compose `vk-worker` service uses `restart: unless-stopped` so the container auto-recovers if the process exits unexpectedly

## Reliability notes

- Direct-answer, KB-agent, and summary LLM clients use configurable timeout / retry settings for transient provider failures.
- Mistral/openai-compatible roles can also use ordered CSV key pools (`*_API_KEYS`): transient/network/5xx failures retry on the same key, while auth/quota/key-specific failures fail over to the next key.
- Production must configure at least two distinct slots for every customer-answer role: `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS`. After an env change, recreate `app`, `worker`, and `vk-worker`, then verify each process sees the intended non-secret slot count; one healthy direct/final-answer key without a reserve is not an acceptable production configuration.
- LLM traces now preserve non-secret failover metadata (`api_key_index`, `used_failover`, `failover_count`, `failover_events`) and aggregate failover counters in usage summaries so operators can detect reserve-key usage.
- VK long-poll transport failures that surface as transient network errors must not terminate the worker loop.
- The stronger model should be allocated to the KB agent / wiki-reading step; the cheaper model can be used for the final customer-facing answer step.
- If the final answer-generation call exhausts provider recovery, the runtime returns `retry_pending` with empty text. Application code never synthesizes a business answer from keywords or retrieved prose.
- Broad or ambiguous wording is interpreted by the LLM from dialogue context and the semantic Wiki catalog; application code contains no topic dictionary or business FAQ branch for it.
- Date/tool paths must not loop on repeated `use_tool` after the tool result is already present; the next step must advance to KB or answer generation.
- Relative-date words (`сегодня`, `завтра`, `послезавтра`) must resolve from the runtime current date for the active request, not from a guessed calendar day extracted elsewhere in the sentence.
- Day-only date parsing must not mistake clock-time phrases such as `к 15:00` / `к 15 часам` for a calendar day-of-month.
- Weekend-only schedule checks must stay scoped to jump/schedule questions and must not hijack office/certificate answers just because KB snippets also mention weekends.
- The KB agent now has an optional hardened runtime mode with deterministic navigation, optional coverage-review skip, minimal extraction schema, and tolerant JSON recovery for structured-output drift.

For rollout notes and the production/test split, see `docs/architecture/kb-agent-hardening.md`.

