# Support Agent Architecture Overview

## Repository contract — deployed minimal stabilization

The engine runs as `ANSWER_ENGINE_MODE=agent_tool_loop`. Production uses one semantically owned answer path: the model selects zero to two native Wiki/calendar calls, then the common finalizer writes customer text from the literal turn, bounded dialogue, and compact verified facts. Retrieval query/scope, candidate answer basis, coverage verdicts, raw pages, and internal reasons are audit mechanics and never finalizer input.

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
  → RoutingService allowlisted compact facts + common respond exactly once
  → shared output validator + PolicyService + channel limit
  → durable message persistence + transport delivery
```

Technical selector/tool failures take a textless `retry_pending` path without invoking the writer. A semantic `not_found` result can proceed to finalization.

The common writer receives a compact allowlisted packet: literal current message, bounded dialogue, cited Wiki facts, deterministic calendar facts, and sourced policy options. The writer, not coverage or retrieval, selects only facts directly relevant to the question and writes the outcome. A confirmed current source for a requested dynamic value is a direct answer; the writer gives the source without adding merely related conditions, internal process, contact, payment, or explanation of an absent value. There is no fixed customer-text fallback: even a sourced no-answer option is presented to the common writer as policy evidence. Technical failures remain textless `retry_pending`.

## Core boundaries

- **LLM chooses the tool action.** Application code does not classify business topics, use keyword routes, or unconditionally query Wiki.
- **Wiki is the only business-fact source.** The system prompt is generic and contains no profile facts. For a substantive turn, the customer model must call `wiki_lookup` before returning customer facts, procedures, contacts, promises, or domain-specific clarification/out-of-scope text.
- **Social exception is narrow.** A short pure social message without a request or continuation of a customer situation may return a non-factual `social_reply` without Wiki and without a second finalizer call.
- **Runtime tools are factual sources for their own domain only.** The date tool supplies calendar facts; it does not infer business schedules.
- **Evidence acquisition and direct coverage are separate.** The runtime validates typed evidence and retains compact cited fact text, source references, conditions, and modality. `ready` is not semantic proof: retrieval requests, answer basis, coverage/missing/conflict verdicts, and related but unasked facts do not authorize an answer. The finalizer evaluates direct relevance against the literal question. Raw extraction rationale and pages are excluded.
- **Customer output is validated at the boundary.** The shared application validator requires an object, an exact supported route and non-empty string `response_text`; missing/null `confidence` stays unknown (`None`), otherwise only finite numbers in `[0, 1]` are valid. Invalid output is textless `retry_pending`, with reason distinct from outcome. After the idempotent greeting policy, Telegram's 4096-character / VK's 9000-character plain-text limits are checked: overflow is `output_too_long`, never a silent cut. Newlines and complete conditions survive; existing outer trim and `**`/`__` marker removal remain. See [the structural output contract](../specs/final-answer-contract.md#structural-output-validation-stage-1).
- **Technical LLM/Wiki failure is fail-closed.** It creates `retry_pending` with empty customer text and is retried by the transport worker. It is never converted into a guessed domain answer.
- **The first-reply greeting is deterministic.** `PolicyService.finalize_simple_customer_text()` adds exactly one `Здравствуйте!` to the first non-empty customer-visible reply. It adds none to later replies and does not duplicate a model-provided greeting.

## Typed native tool contract

`wiki_lookup` and `calendar_lookup` are typed native tools. The agent chooses one with `tool_choice=auto`; the runtime validates the exact name, JSON arguments and nonempty native ID before execution. A tool-free direct response is accepted only as `social_reply` and then passes the same deterministic output boundary. The first continuation retains native `assistant.tool_calls` → `role=tool` messages. After both tools complete, the same next selector call uses exactly system + user messages containing `selector-terminal/v1`, the original question/dialogue and two checked `tool_exchanges` (ID/name/arguments/observation). The terminal packet carries only its `action=finalize` JSON schema (`response_intent` and `reason`); pre-tool social/finalize schemas are removed rather than inherited. Native pairs remain internal execution records; both pairs must match the recorded requests/results before projection. Terminal JSON is parsed strictly without XML repair or a synthesized finalize. The selector may choose the other tool or finish collection with `action=finalize`, `response_intent` and a short internal `reason`; it must not create customer text. Both Wiki→calendar and calendar→Wiki are supported by the target contract. Repeat/third/unknown/parallel calls and invalid ID links fail closed before the forbidden action. One common `respond` call owns customer text after tool collection, including calendar-only replies; a validated tool-free social reply bypasses that second call.

`wiki_lookup` requires:

```json
{
  "query": "focused semantic Wiki query",
  "context_scope": "subject or constraint selected in the dialogue",
  "needed_fact": "specific fact needed for the current turn"
}
```

The OpenAI-compatible adapter normalizes the supported provider variants—`_native_tool_calls`, standard OpenAI `tool_calls`, legacy `function_call`, a one-item legacy map list such as `[{"wiki_lookup": {...}}]`, and the exact one-item Wiki action-object list `[{"action":"wiki_lookup","query":"...","context_scope":"...","needed_fact":"..."}]`—then validates one exact registered function name and its JSON arguments against the tool schema. The action-object form is translated losslessly to one synthetic native call; it cannot add capability or infer missing arguments. Unknown tools, multiple calls, malformed JSON, missing fields, and invalid schemas fail closed as `retry_pending`; normalization never invents a tool or rewrites an argument into a business decision. `parallel_tool_calls=false` bounds the customer action to one call. The runtime passes the model-selected request to catalog navigation and grounded extraction. Extraction returns both fact-bound `coverage` and cited evidence, so the former separate `coverage_review` LLM pass is disabled in production (`KB_AGENT_MERGE_COVERAGE_EXTRACTION=true`). For a contextual follow-up, `context_scope` preserves the last explicit customer-selected subject; it must not be widened to a neighboring variant unless the customer asks to compare or change it.

`calendar_lookup` requires exactly `date_expressions` and `requested_calendar_fact`; `date_expressions` is a list of one to eight explicit date fragments. It resolves every date against the turn reference time and returns deterministic ISO date, weekday, and weekend facts. It supplies calendar facts only and never infers a business schedule. A ready calendar observation is retained separately from Wiki business facts and passed to the common finalizer after collection.

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
