# Support Agent Architecture Overview

## Repository contract — deployed minimal stabilization

The engine runs as `ANSWER_ENGINE_MODE=agent_tool_loop`. Production release `aaa9b51219b00875764bf497f40f8ca280c3ae95` preserves the two verified one-Wiki-result paths: complete validated evidence reaches the common finalizer directly; a successful Wiki `not_found` reaches the sourced fixed fallback directly. It also bounds the grounded-evidence packet to the facts explicitly linked from `coverage.answered_parts`, preventing incidental surplus extraction facts from turning a directly covered answer into a technical `evidence_too_large` failure. Multi-tool/calendar ordering remains a separately unaccepted scope and is not release evidence for this stabilization.

```text
Telegram / VK inbound event
  → raw-event journal + channel queue + stale/override guards
  → RoutingService: case resolution + bounded dialogue context
  → DirectLLMService agent (native tools, tool_choice=auto)
      → tool-free pure social reply returns directly through the output boundary
      → 0–2 sequential tools, Wiki/calendar each at most once
      → all assistant.tool_calls / role=tool pairs retain native IDs during execution
      → after the second successful tool, one terminal selection projection is built from the checked native history
      → action=finalize + response_intent, no customer draft
  → RoutingService allowlisted evidence + structural allowed_routes + common respond exactly once
  → shared output validator + PolicyService + channel limit
  → durable message persistence + transport delivery
```

Technical selector/tool failures take a textless `retry_pending` path without invoking the writer. A semantic `not_found` result can proceed to finalization.

The common writer system contract uses a compact allowlisted evidence packet; allowed_routes restrict outcomes after collection; non-answerable facts/basis are excluded from writer input. A ready partial Wiki result with answerable facts is projected to only the fact IDs linked by `coverage.answered_parts`: the writer sees neither missing parts, coverage diagnostics, nor a candidate basis that could contain an omitted detail. If no answerable facts remain, or evidence is conflicting/unavailable, the current office policy emits an exact sourced fallback without a writer call; this is the only fixed-text exception. The verified direct-fact path has no post-Wiki selector call, so a complete packet cannot be lost in a second provider protocol exchange.

## Core boundaries

- **LLM chooses the tool action.** Application code does not classify business topics, use keyword routes, or unconditionally query Wiki.
- **Wiki is the only business-fact source.** The system prompt is generic and contains no profile facts. For a substantive turn, the customer model must call `wiki_lookup` before returning customer facts, procedures, contacts, promises, or domain-specific clarification/out-of-scope text.
- **Social exception is narrow.** A short pure social message without a request or continuation of a customer situation may return a non-factual `social_reply` without Wiki and without a second finalizer call.
- **Runtime tools are factual sources for their own domain only.** The date tool supplies calendar facts; it does not infer business schedules.
- **Evidence acquisition and direct coverage are separate.** The stage-4 candidate validates `answer-evidence/v1` (up to 16384 bytes): literal question/scope, acquisition status, cited fact IDs/text/conditions/modality, coverage/missing/conflicts, candidate basis and separate calendar/policy facts. The packet allows at most eight facts; surplus extraction facts are removed only when they are not referenced by `coverage.answered_parts`. A ready partial packet may still reach the writer, but only after a customer-safe projection keeps those linked facts and removes every missing/conflicting diagnostic and candidate basis. A packet with no linked answerable facts, a conflict, or an unresolved constraint fails closed. `ready` is not semantic proof; a typed `not_found` is not upgraded because related facts exist. Unavailable/invalid evidence blocks the writer. Raw extraction rationale and pages are excluded.
- **Customer output is validated at the boundary.** The shared application validator requires an object, an exact supported route and non-empty string `response_text`; missing/null `confidence` stays unknown (`None`), otherwise only finite numbers in `[0, 1]` are valid. Invalid output is textless `retry_pending`, with reason distinct from outcome. After the idempotent greeting policy, Telegram's 4096-character / VK's 9000-character plain-text limits are checked: overflow is `output_too_long`, never a silent cut. Newlines and complete conditions survive; existing outer trim and `**`/`__` marker removal remain. See [the structural output contract](../specs/final-answer-contract.md#structural-output-validation-stage-1).
- **Technical LLM/Wiki failure is fail-closed.** It creates `retry_pending` with empty customer text and is retried by the transport worker. It is never converted into a guessed domain answer.
- **The first-reply greeting is deterministic.** `PolicyService.finalize_simple_customer_text()` adds exactly one `Здравствуйте!` to the first non-empty customer-visible reply. It adds none to later replies and does not duplicate a model-provided greeting.

## Typed native tool contract

`wiki_lookup` and `calendar_lookup` are typed native tools. The agent chooses one with `tool_choice=auto`; the runtime validates the exact name, JSON arguments and nonempty native ID before execution. A tool-free direct response is accepted only as `social_reply` and then passes the same deterministic output boundary. The first continuation retains native `assistant.tool_calls` → `role=tool` messages. After both tools complete, the same next selector call uses exactly system + user messages containing `selector-terminal/v1`, the original question/dialogue and two checked `tool_exchanges` (ID/name/arguments/observation). Native pairs remain internal execution records; both pairs must match the recorded requests/results before projection. Terminal JSON is parsed strictly without XML repair or a synthesized finalize. The selector may choose the other tool or finish collection with `action=finalize`, `response_intent` and a short internal `reason`; it must not create customer text. Both Wiki→calendar and calendar→Wiki are supported by the target contract. Repeat/third/unknown/parallel calls and invalid ID links fail closed before the forbidden action. One common `respond` call owns customer text after tool collection, including calendar-only replies; a validated tool-free social reply bypasses that second call.

`wiki_lookup` requires:

```json
{
  "query": "focused semantic Wiki query",
  "context_scope": "subject or constraint selected in the dialogue",
  "needed_fact": "specific fact needed for the current turn"
}
```

The OpenAI-compatible adapter dispatches only an exact registered function name with JSON arguments that validate against that tool schema; malformed names, malformed JSON, missing fields, and unknown tools fail closed as `retry_pending`. It never guesses a tool from a structural call object or rewrites an argument into a business decision. `parallel_tool_calls=false` bounds the customer action to one call. The runtime passes the model-selected request to catalog navigation, coverage review, and grounded extraction. For a contextual follow-up, `context_scope` preserves the last explicit customer-selected subject; it must not be widened to a neighboring variant unless the customer asks to compare or change it.

`calendar_lookup` requires exactly `date_expression` and `requested_calendar_fact`. It supplies calendar facts only and never infers a business schedule. A ready calendar observation is retained separately from Wiki business facts and passed to the common finalizer after collection.

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
