# API Contracts

## Endpoints
- `GET /health`
- `POST /api/v1/messages/inbound`
- `POST /api/internal/probe/sessions`
- `GET /api/internal/probe/sessions`
- `GET /api/internal/probe/sessions/{session_id}`
- `POST /api/internal/probe/sessions/{session_id}/messages`
- `POST /api/internal/probe/sessions/{session_id}/wait-reply`
- `GET /api/internal/probe/sessions/{session_id}/messages`
- `GET /api/internal/probe/sessions/{session_id}/trace`
- `POST /api/internal/probe/sessions/{session_id}/close`
- `GET /api/viewer/auth/me`
- `POST /api/viewer/auth/login`
- `POST /api/viewer/auth/logout`
- `GET /api/viewer/dialogs?day=YYYY-MM-DD`
- `GET /api/viewer/dialogs/{conversation_id}/messages?day=YYYY-MM-DD`
- `POST /telegram/webhook`

## VK viewer auth contract

The viewer can be protected by a single shared access key.

Endpoints:
- `GET /api/viewer/auth/me` — returns `{ "authenticated": true|false }`
- `POST /api/viewer/auth/login` — accepts `{ "key": "..." }`; on success returns `204 No Content` and sets a signed long-lived `HttpOnly` cookie
- `POST /api/viewer/auth/logout` — returns `204 No Content` and clears the same cookie

Runtime/env controls:
- `VIEWER_AUTH_ENABLED`
- `VIEWER_AUTH_KEY`
- `VIEWER_AUTH_COOKIE_NAME`
- `VIEWER_AUTH_SESSION_DAYS`
- `VIEWER_AUTH_COOKIE_SECURE`

Behavior rules:
- when `VIEWER_AUTH_ENABLED=false`, the viewer API behaves as open read-only access
- when `VIEWER_AUTH_ENABLED=true`, both `GET /api/viewer/dialogs...` routes require a valid viewer auth cookie and otherwise return `401`
- the shipped frontend uses the auth endpoints before loading dialogs so the UI and the API stay under the same access gate
- the shipped frontend is also an installable online-first PWA; its service worker may cache static shell/assets, but `/api/viewer/*` data is not the offline source of truth in v1

## VK viewer response contract

`GET /api/viewer/dialogs?day=YYYY-MM-DD` returns a JSON array of dialog summaries with:
- `conversation_id`
- `case_id`
- `display_name`
- `external_chat_id`
- `last_message_at`
- `message_count`

`last_message_at` is a full timestamp (UTC in transport JSON) so the frontend can render the list in the browser's current timezone using the same formatter as the message pane.

Ordering contract:
- dialogs are sorted by the selected day's message time in ascending order (earliest first)

`GET /api/viewer/dialogs/{conversation_id}/messages?day=YYYY-MM-DD` returns the selected dialog scoped to the requested day only.

## Viewer Web Push contract

All push routes require the same viewer-auth cookie as the dialog routes.

- `GET /api/viewer/push/config` returns `{ "public_key": "<VAPID public key>" }` only when `VIEWER_PUSH_ENABLED=true` and the public key is configured; otherwise it returns `503`.
- `POST /api/viewer/push/subscriptions` accepts the browser `PushSubscription.toJSON()` shape (`endpoint`, `keys.p256dh`, `keys.auth`, optional `expirationTime`) and creates or re-enables the endpoint.
- `DELETE /api/viewer/push/subscriptions` disables the endpoint without deleting audit/delivery history.

For each committed inbound VK `Message`, the same DB transaction creates one `viewer_user_message_received` outbox event. `viewer-push-worker` delivers events asynchronously. The push payload contains only `type`, `conversation_id`, and `case_id`; it must not contain customer message text. Delivery uses configurable Web Push TTL/urgency; a stale `processing` event is reclaimed after its configured lease, while transient provider errors are retried with bounded backoff.

## Shared inbound message contract

The normalized application inbound contract is transport-neutral and currently accepts these fields:
- `channel`
- `external_user_id`
- `external_chat_id`
- `text`
- optional `external_message_id`
- optional `external_event_type`
- optional `external_event_id`
- optional `received_at`
- optional `raw_event`
- optional `metadata`

## Inbound response envelope

`POST /api/v1/messages/inbound` returns:
- `case`
- `context`
- `retrieval`
- `route`
- `outcome`
- `audit`

`audit.response_strategy` is the canonical per-turn trace for the bounded loop. It records the final action and, when applicable, the loop step sequence / tool trace that led to the final outcome.

Current audit fields of interest:
- `audit.response_strategy.loop_mode` — expected runtime family identifier (for the current architecture: `agentic_bounded_loop_with_kb_agent`)
- `audit.response_strategy.steps` — compact step sequence such as `read_kb -> kb_agent_read -> answer`
- `audit.response_strategy.loop_trace` — per-iteration planner/action trace
- `audit.response_strategy.tool_trace` — structured runtime-tool trace for date/math/live-fact checks; for relative-date requests this trace must reflect the runtime-resolved `iso_date` (`сегодня`/`завтра`/`послезавтра`) rather than a clock-time number accidentally parsed from the same sentence
- `audit.response_strategy.llm_trace` — per-role LLM call metadata including provider/model/duration/attempts/usage and failover fields such as `api_key_index`, `used_failover`, `failover_count`, and `failover_events`

Transport workers may also keep transport-level journal state outside the API response envelope; for VK this includes raw event processing, send reconciliation, and override suppression state.

## Internal route contract

`route` is an orchestration decision, not a human-handoff router.

Internal probe query/read contract:
- `POST /api/internal/probe/sessions` creates a new probe session and returns `session_id`, `case_id`, `created_at`, `channel=internal_test`, `is_test=true`, and optional `scenario_name` / `requested_by`
- `GET /api/internal/probe/sessions` lists internal test sessions filtered by explicit DB markers (`status`, `scenario_name`, `requested_by`, `source`, `session_type`) with pagination (`limit`, `offset`)
- `GET /api/internal/probe/sessions/{session_id}` returns the latest persisted case snapshot for a concrete probe session, including `message_count`, `last_user_message`, `last_assistant_message`, and `last_activity_at`
- `POST /api/internal/probe/sessions/{session_id}/messages` runs the real inbound runtime for that probe session and returns the final answer plus route/outcome envelopes
- `POST /api/internal/probe/sessions/{session_id}/wait-reply` returns the latest assistant reply snapshot for the probe session if available
- `GET /api/internal/probe/sessions/{session_id}/messages` returns persisted multi-turn message history
- `GET /api/internal/probe/sessions/{session_id}/trace` returns persisted workflow events for the case
- `POST /api/internal/probe/sessions/{session_id}/close` resolves the probe case and records a close event

Expected `route.route` values:
- `answer`
- `cannot_answer`
- `out_of_scope`
- `clarification_requested`

Notes:
- `route_reason` may carry either a normal planner/policy reason, a grounded-degradation marker such as `llm_fallback:RuntimeError`, or `llm_recovery_exhausted` when every bounded provider retry/key slot was exhausted. The last case still emits one neutral customer `answer`; it never becomes a blank/deferred transport reply or a provider-caused `cannot_answer`.
- `route_confidence` reflects the final emitted answer path, including grounded fallback paths.
- date/tool-assisted answers may still surface `answer` as the final route even when the step trace includes both tool usage and KB-agent reading.

## Internal outcome contract

Expected `outcome.outcome_type` values:
- `answer`
- `cannot_answer`
- `out_of_scope`
- `clarification_requested`

All user-visible final text is carried in `outcome.outcome_payload.response_text`.

Additional outcome payload fields may include grounded runtime context such as:
- `tool_observations` for date / calendar / other live-fact checks
- `reason` preserving degraded-but-grounded lineage for internal debugging

For degraded-but-grounded answers, `outcome.outcome_payload.reason` should preserve the failure lineage (for example `llm_fallback:RuntimeError`) while `response_text` remains customer-facing and free of runtime internals.

## Transport persistence side-contracts

These are internal persistence contracts rather than public HTTP endpoints, but they are part of the shipped repository behavior:
- raw transport events are journaled with a dedupe key and processing status
- outbound transport sends are stored for later reconciliation against transport-side activity events
- VK conversation transport state stores `last_bot_reply_at`, `last_admin_reply_at`, and `human_override_until`
- VK `message_reply` is classified as either bot-originated (reconciled) or admin-originated (persisted as a `human` dialog message plus override activation)
- viewer message items render `human` messages as outbound with `author_name: "Оператор VK"`
- internal probe/test sessions are marked explicitly on both `conversations` and `support_cases` with:
  - `is_test`
  - `source`
  - `session_type`
  - `scenario_name`
  - `requested_by`

## LLM observability side-contracts

These are internal observability contracts, but they are part of the shipped repository behavior:
- Mistral/openai-compatible roles may be configured with ordered CSV key pools via `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS`
- successful LLM calls preserve `api_key_index`, `used_failover`, `failover_count`, and `failover_events` in `last_call_info` / `llm_trace`
- failover warning logs identify only provider/model/key-slot transition/status/reason class and must not print raw API keys
- aggregated LLM usage summaries also expose `failover_count` and `failover_calls` per role and overall
