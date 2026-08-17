# Prompt-first final answer

## Goal
The customer-facing answer must follow the active generic `SYSTEM_PROMPT.md`, use the current conversation, and derive every business fact only from relevant Wiki/tool evidence.

## Runtime contract

The canonical packet and evidence contract is defined in `docs/specs/final-answer-contract.md`.

1. The production simple runtime uses the prompt without KB only for non-factual social text or a genuinely necessary clarification.
2. A substantive business question must trigger the KB path; the system prompt is not a factual shortcut.
3. When KB is required, KB-agent output is evidence for the final customer-facing model, not customer-ready prose.
4. The final customer-facing model receives only an allowlisted packet: active system prompt, explicit `knowledge_mode`, current user message, role-labelled current-case dialogue, compact grounded evidence, and normalized customer-relevant tool facts.
5. Planner actions/reasons, routing metadata, traces, raw KB pages, raw tool payloads, case state, and duplicated context representations must never enter the final-model request.
6. The final response must answer the main customer question first and include only facts needed for that answer.
7. A ready KB packet must not be converted to customer text by mechanically joining `grounded_facts`.
8. On final-model transport failure, the runtime fails closed as `retry_pending` with empty customer text. Application code must not compose a customer answer from KB prose.

## Session and safety boundaries
- The existing new-day / more-than-two-hour case boundary remains unchanged.
- No city, service, certificate, schedule, or other domain-specific branch may be hardcoded in application code.
- `SYSTEM_PROMPT.md` is authoritative only for role, safety, dialogue behavior, and style.
- The versioned Wiki is the sole source of business facts.

## Acceptance criteria
- A non-factual social/clarification request can finish with zero KB calls and no business claim.
- A KB-dependent request reads KB and is finalized by the customer-facing model.
- The final-model payload contains `answer_basis` plus grounded evidence but no internal trace.
- A question about one fact does not receive unrelated facts from the same KB packet.
- The approved geography replay answers the named-city question directly and does not mention certificate printing.
- Existing retry, tool, greeting, cannot-answer, and out-of-scope behavior remains covered.
