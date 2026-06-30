# API Contracts

## Endpoints
- `GET /health`
- `POST /api/v1/messages/inbound`
- `GET /api/viewer/dialogs?day=YYYY-MM-DD`
- `GET /api/viewer/dialogs/{conversation_id}/messages?day=YYYY-MM-DD`
- `POST /telegram/webhook`

## VK viewer response contract

`GET /api/viewer/dialogs?day=YYYY-MM-DD` returns a JSON array of dialog summaries with:
- `conversation_id`
- `display_name`
- `external_chat_id`
- `last_message_time`
- `message_count`

Ordering contract:
- dialogs are sorted by the selected day's message time in ascending order (earliest first)

`GET /api/viewer/dialogs/{conversation_id}/messages?day=YYYY-MM-DD` returns the selected dialog scoped to the requested day only.

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
- `audit.response_strategy.tool_trace` — structured runtime-tool trace for date/math/live-fact checks

Transport workers may also keep transport-level journal state outside the API response envelope; for VK this includes raw event processing, send reconciliation, and override suppression state.

## Internal route contract

`route` is an orchestration decision, not a human-handoff router.

Expected `route.route` values:
- `answer`
- `cannot_answer`
- `out_of_scope`
- `clarification_requested`

Notes:
- `route_reason` may carry either a normal planner/policy reason or a degradation marker such as `llm_fallback:RuntimeError` when the final LLM answer step failed but the runtime still returned a grounded KB-based answer.
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
- VK `message_reply` is classified as either bot-originated (reconciled) or admin-originated (override activation)
