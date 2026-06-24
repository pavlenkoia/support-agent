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

## Internal outcome contract

Expected `outcome.outcome_type` values:
- `answer`
- `cannot_answer`
- `out_of_scope`
- `clarification_requested`

All user-visible final text is carried in `outcome.outcome_payload.response_text`.

For degraded-but-grounded answers, `outcome.outcome_payload.reason` should preserve the failure lineage (for example `llm_fallback:RuntimeError`) while `response_text` remains customer-facing and free of runtime internals.
