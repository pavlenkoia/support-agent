# Prompt-first final answer

## Goal
The customer-facing answer must be written from the active `SYSTEM_PROMPT.md`, the current conversation, and only the relevant knowledge/tool evidence needed for the question.

## Runtime contract

The canonical packet and evidence contract is defined in `docs/specs/final-answer-contract.md`.

1. The bounded-loop planner may choose `answer_from_prompt` when the active system prompt already contains sufficient authoritative information.
2. `answer_from_prompt` must not trigger KB retrieval.
3. When KB is required, KB-agent output is evidence for the final customer-facing model, not customer-ready prose.
4. The final customer-facing model receives only an allowlisted packet: active system prompt, explicit `knowledge_mode`, current user message, role-labelled current-case dialogue, compact grounded evidence, and normalized customer-relevant tool facts.
5. Planner actions/reasons, routing metadata, traces, raw KB pages, raw tool payloads, case state, and duplicated context representations must never enter the final-model request.
6. The final response must answer the main customer question first and include only facts needed for that answer.
7. A ready KB packet must not be converted to customer text by mechanically joining `grounded_facts`.
8. On final-model transport failure, the runtime may use a grounded fallback, preferring compact ready grounding over unrelated raw context. It must not invent information.

## Session and safety boundaries
- The existing new-day / more-than-two-hour case boundary remains unchanged.
- No city, service, certificate, schedule, or other domain-specific branch may be hardcoded in application code.
- `SYSTEM_PROMPT.md` remains hot-editable and authoritative for profile policy.
- KB remains the source for facts not explicitly established by the system prompt.

## Acceptance criteria
- A prompt-sufficient request can finish through `answer_from_prompt` with zero KB calls.
- A KB-dependent request reads KB and is finalized by the customer-facing model.
- The final-model payload contains `answer_basis` plus grounded evidence but no internal trace.
- A question about one fact does not receive unrelated facts from the same KB packet.
- The approved geography replay answers the named-city question directly and does not mention certificate printing.
- Existing retry, tool, greeting, cannot-answer, and out-of-scope behavior remains covered.
