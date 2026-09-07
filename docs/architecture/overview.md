# Support Agent Architecture Overview

## Current production contract

Production runs with `ANSWER_ENGINE_MODE=agent_tool_loop`. It is one LLM-managed customer turn with an optional bounded Wiki tool, not a keyword router, a separate LLM classifier, or an application-forced retrieval path.

```text
Telegram / VK inbound event
  → raw-event journal + channel queue + stale/override guards
  → RoutingService: case resolution + bounded dialogue context
  → DirectLLMService native customer turn (`wiki_lookup` / `calendar_lookup`, tool_choice=auto)
      ├─ pure social turn → direct non-factual response
      └─ model-selected tool call
           → runtime executes the typed request
           → tool result is linked to the originating tool_call_id as role=tool
           → same native conversation returns the customer JSON result
  → PolicyService output boundary
  → durable message persistence + transport delivery
```

## Core boundaries

- **LLM chooses the tool action.** Application code does not classify business topics, use keyword routes, or unconditionally query Wiki.
- **Wiki is the only business-fact source.** The system prompt is generic and contains no profile facts. For a substantive turn, the customer model must call `wiki_lookup` before returning customer facts, procedures, contacts, promises, or domain-specific clarification/out-of-scope text.
- **Social exception is narrow.** A short pure social message without a request or continuation of a customer situation may return a non-factual `social_reply` without Wiki.
- **Runtime tools are factual sources for their own domain only.** The date tool supplies calendar facts; it does not infer business schedules.
- **Only ready grounding crosses the finalizer seam.** `grounding_status=ready` may provide `answer_basis` and grounded facts. `not_found` text, page-selection rationale, source availability, and other non-ready extraction text are operator telemetry, not customer evidence.
- **Customer output is validated at the boundary.** The shared application validator requires an object, an exact supported route and non-empty string `response_text`; missing/null `confidence` stays unknown (`None`), otherwise only finite numbers in `[0, 1]` are valid. Invalid output is textless `retry_pending`, with reason distinct from outcome. After the idempotent greeting policy, Telegram's 4096-character / VK's 9000-character plain-text limits are checked: overflow is `output_too_long`, never a silent cut. Newlines and complete conditions survive; existing outer trim and `**`/`__` marker removal remain. See [the structural output contract](../specs/final-answer-contract.md#structural-output-validation-stage-1).
- **Technical LLM/Wiki failure is fail-closed.** It creates `retry_pending` with empty customer text and is retried by the transport worker. It is never converted into a guessed domain answer.
- **The first-reply greeting is deterministic.** `PolicyService.finalize_simple_customer_text()` adds exactly one `Здравствуйте!` to the first non-empty customer-visible reply. It adds none to later replies and does not duplicate a model-provided greeting.

## Typed native tool contract

`wiki_lookup` and `calendar_lookup` are typed native tools. The customer model selects one with `tool_choice=auto`; the runtime validates the exact name and JSON arguments, executes only that request, then continues the same OpenAI-compatible conversation with an `assistant.tool_calls` message followed by a linked `role=tool` result using the original `tool_call_id`. The model then returns the final customer JSON; the application does not substitute a second standalone finalizer prompt for the tool continuation.

`wiki_lookup` requires:

```json
{
  "query": "focused semantic Wiki query",
  "context_scope": "subject or constraint selected in the dialogue",
  "needed_fact": "specific fact needed for the current turn"
}
```

The OpenAI-compatible adapter dispatches only an exact registered function name with JSON arguments that validate against that tool schema; malformed names, malformed JSON, missing fields, and unknown tools fail closed as `retry_pending`. It never guesses a tool from a structural call object or rewrites an argument into a business decision. `parallel_tool_calls=false` bounds the customer action to one call. The runtime passes the model-selected request to catalog navigation, coverage review, and grounded extraction. For a contextual follow-up, `context_scope` preserves the last explicit customer-selected subject; it must not be widened to a neighboring variant unless the customer asks to compare or change it.

`calendar_lookup` requires exactly `date_expression` and `requested_calendar_fact`. It supplies calendar facts only and never infers a business schedule. A ready tool result is customer evidence only through the linked native continuation.

## Main runtime components

- **`TelegramGatewayService` / polling worker** — normalizes Bot API updates, journals events, coalesces turns, retries durable `retry_pending` events, and sends replies.
- **`VKGatewayService` / VK worker** — equivalent shared runtime integration plus outbound reconciliation and a one-hour manual-operator override.
- **`RoutingService`** — case resolution, context construction, common evidence boundary, finalization, outcomes, and audit events.
- **`UnifiedTurnService`** — executes the LLM-selected typed tool and continues the same native conversation with the linked tool result.
- **`WikiLookupTool` / `CalendarLookupTool`** — factual adapters for Wiki grounding and calendar calculations respectively.
- **`DirectLLMService`** — emits the customer tool decision and resumes an OpenAI-compatible tool conversation; records non-secret LLM traces.
- **`PolicyService`** — profile-backed no-answer policy evidence and the deterministic output boundary.
- **PostgreSQL** — system of record for conversations, cases, messages, workflow events, transport events, transport state, and VK outbound reconciliation.
- **Internal probe API** — invokes the real routing path with `is_test` records and never publishes to Telegram or VK; use it for literal no-send replays.

## Outcomes

- `answer` — grounded facts and/or runtime tool facts support a response.
- `cannot_answer` — in-domain but insufficient confirmed evidence; the common finalizer may use allowlisted profile policy evidence as a natural next step.
- `out_of_scope` — outside the active profile domain.
- `clarification_requested` — a short clarification is required before a grounded answer.
- `retry_pending` — technical failure only; empty customer text and durable transport retry.

When LLM Wiki catalog navigation succeeds but selects no source pages, the KB result is `not_found`, not a technical failure. The routing layer must finalize it as `cannot_answer` with an empty evidence packet instead of returning `retry_pending`/empty customer text.

When an unmatched VK `message_reply` activates manual override, the VK gateway suppresses unfinished `message_new` source events for the same peer (`received`, `processing`, or `retry_pending`) so historical customer questions do not remain indefinitely in intake state after an operator has answered.

## Release boundary

`deploy/profile-source/` is reviewed source. `scripts/build_runtime_profile.py` validates it into `deploy/runtime-profile/`; the exact generated tree is atomically promoted to the external runtime profile before `python3 scripts/release.py` recreates the full application plane (`app`, `worker`, `vk-worker`, `viewer-web`, `viewer-push-worker`). PostgreSQL is not recreated.

The release script verifies profile parity, image revision labels, Python source manifests, health/liveness, and controlled fail-closed probes. A release is complete only after `RELEASE SUCCESS` and a non-secret receipt under the runtime root.

See also:
- `docs/specs/support-agent-spec.md` — detailed behavior contract.
- `docs/operations/production-releases.md` — release and rollback procedure.
