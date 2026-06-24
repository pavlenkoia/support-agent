# API Contracts

## Endpoints
- `GET /health`
- `POST /api/v1/messages/inbound`
- `POST /telegram/webhook`

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
