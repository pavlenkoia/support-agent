# Final answer contract

## Purpose

The support agent has one common customer-facing finalization boundary for normal substantive answers. That boundary turns the generic profile behavior contract plus question-relevant evidence into a natural customer reply. It must not repeat upstream planning or knowledge-navigation work.

## Canonical flow

```text
active SYSTEM_PROMPT.md
+ current customer question
+ bounded role-labelled dialogue from the current case
+ optional compact grounded KB evidence
+ optional normalized customer-relevant tool facts
→ customer-facing final model
→ one ready-to-send answer
```

There are two finalization modes:

1. `prompt_only`: no business fact is required, for example for a social response or a genuinely necessary clarification. The common final model may use the role/style contract and dialogue, but may not state a business fact.
2. `kb_grounded`: a business fact is required. The KB agent runs and only a compact grounded packet reaches the common final model.

The stage-4 candidate supplies a structurally validated `answer-evidence/v1` packet. Acquisition status and direct semantic coverage are separate: neither `ready` nor cited, related facts guarantee an answer. A typed `not_found` packet can retain coverage and related cited facts without being promoted to `ready`; missing legacy evidence becomes an empty typed packet. Unavailable or invalid evidence blocks the writer. Raw extraction rationale is not evidence. The writer answers a directly covered request, or writes a natural profile-backed non-answer when essential information is absent. It must not substitute a partial or adjacent answer for that missing information.

The mode is derived from structurally validated evidence availability. Response intent is the validated decision of the existing selector model, not an application-side semantic classifier. Only its enum value crosses the writer boundary; selector reasons, traces and drafts do not.

## Finalization input allowlist

The finalization contract is appended at system-message priority after the active profile prompt. The profile prompt contains role, safety, dialogue, and style rules but no business facts. The evidence boundary is authoritative for every factual claim.

The final model may receive only:

- the full active `SYSTEM_PROMPT.md` as a generic behavior contract, followed in the same system message by the application-owned generic finalization contract;
- `knowledge_mode` (`prompt_only` or `kb_grounded`), model-selected `response_intent` and application-computed `allowed_routes`;
- the current customer question;
- at most the last 10 valid current-case dialogue items as `{role, content}`, with roles restricted to `user|assistant`;
- typed `grounding_evidence`: schema version, literal question, context scope, acquisition status, facts with local IDs/text/source refs/conditions/modality, coverage (answered and missing parts, conflicts, unresolved constraints), candidate answer basis, calendar facts and sourced policy evidence;
- allowlisted `tool_facts`: calendar kind/summary/source ref and exact structured calendar values, or a profile no-answer option with summary and source ref; no arbitrary tool payloads.

The final model must not receive:

- `planner_action`, `planner_reason`, planner confidence, or planner trace;
- route reasons, loop trace, audit fields, case state, database identifiers, or session machinery;
- KB navigation, selected-page rationale, coverage-review rationale, extraction rationale, LLM trace, source-selection mechanics, or raw selected KB pages;
- raw tool requests/results/payloads or tool execution trace;
- duplicated representations of the dialogue such as both `recent_turns` and `recent_messages`;
- internal summaries whose provenance or relevance is not guaranteed.

The application constructs a new allowlisted finalization packet. It must never serialize a broad runtime `context` dictionary into the final-model request.

There is no legacy `answer(...)` bypass for KB-ready customer finalization. A configured finalizer must implement the common `respond(...)` contract; otherwise the runtime fails closed instead of sending broad context through an older path.

## Approved fixed fallback exception

The operator explicitly replaced model-written fallback for the current office policy with a fixed response: `Пожалуйста, позвоните в офис в рабочее время.` When validated output restrictions are exactly `['cannot_answer']` and the sourced `profile_no_answer_option` refers to `kb/entities/office-chelyabinsk.md`, `respond` returns that text without a model request. Routing preserves the literal text and does not record a generated final response. This exception supersedes the common-writer requirement for this branch only; technical unavailability remains textless `retry_pending`, and answerable requests retain model generation. It is not a global office fallback for other policy sources.

## Evidence semantics

- `SYSTEM_PROMPT.md` is authoritative only for role, scope, safety, dialogue behavior, and style. It is not a source of business facts.
- In `prompt_only`, empty KB evidence is expected and the final model may produce only non-factual social text, a necessary clarification or natural cannot_answer within allowed_routes.
- In `kb_grounded`, structural validity establishes packet shape and source membership, not semantic truth or direct coverage. Coverage is supplied by the existing extraction model and judged against the actual current question by the common writer; no extra verifier is added.
- `grounding_status=not_found` is a successful semantic no-direct-answer result, not technical unavailability. Typed coverage remains available; raw navigation/extraction rationale and pages are excluded. Absence of mention is not a negative business fact.
- `answer_basis` is a candidate synthesis, not an independent fact source. Typed facts define supported scope, conditions and modality. The writer may rephrase them naturally but must not add, strengthen, contradict or discard material conditions.

- If `answer_basis` and `grounded_facts` conflict, the final model must stay within the exact grounded facts and avoid the unsupported part of the basis.
- Evidence is not mandatory prose. The final model selects only facts needed for the current question and must not mechanically concatenate all facts.
- The typed evidence packet permits at most eight facts. Extraction is instructed to return the minimum direct set, and the runtime removes surplus facts only when they are not referenced by `coverage.answered_parts`. If the coverage mapping itself needs more than eight fact IDs, the packet remains invalid and fails closed; the runtime does not choose facts by topic, text similarity, or a domain-specific rule.
- Directly supported information must be answered without unnecessary refusal. Partial, none or conflicting coverage, or nonempty missing parts, conflicts or unresolved constraints, do not permit `answer`; they require a natural profile-backed fallback after collection. Clarification is for ambiguity the customer can resolve, not for a clear unknown question.
- A sourced option to take an action does not establish that action's success or a particular result. The writer preserves this uncertainty, does not add intentions or conditions when restating the customer's request, and keeps internal sources and decision grounds in `reason`, not customer prose.

## Output contract

A normal final answer must:

1. answer the practical customer question first;
2. use natural customer-facing language;
3. keep the most recent explicit customer constraint or preference active across short follow-ups, unless the customer changes it or asks for a comparison;
4. acknowledge a newly stated or narrowed constraint in the first sentence before applying grounded facts;
5. include only relevant confirmed conditions or the next useful step;
6. when the customer asks whether other requirements exist, avoid an exhaustive “none” claim if grounded evidence contains relevant conditions or is not exhaustive;
7. preserve modality (`usually`, `may`, `depends`, conditional scope);
8. avoid mentioning KB, prompts, tools, traces, routes, evidence, reasoning, or internal decisions;
9. obey the common first-reply greeting boundary without duplicating a greeting.

No domain-specific question handler, keyword branch, canned FAQ shortcut, or mechanical fact renderer is part of the normal path.

## Structural output validation (stage 1)

`app/services/final_response_validation.py` owns the shared application-side schema, independent of provider-native JSON Schema support. In stage 3, `respond` is the only customer-output model boundary. `begin_turn` and `continue_after_tool` validate selector decisions separately: registered native requests or `action=finalize` without customer text. Routing accepts only technical failures from the selector, never precomputed customer replies. Routing repeats the same validation before policy and after formatting with the actual channel; this small routing change is necessary for channel-specific limits and textless technical outcomes, not a redesign of tool selection or audit telemetry.

- Top level must be an object. Model routes are exact `answer|social_reply|cannot_answer|out_of_scope|clarification_requested`; whitespace/case variants and unknown routes are invalid, not semantic refusals.
- `response_text` must be a non-empty string after existing formatting cleanup. Objects/lists/numbers are never converted to customer text. Existing internal-envelope guards remain.
- Missing or null `confidence` is valid unknown (`None`). Otherwise only finite JSON numbers in `[0, 1]` are accepted. Booleans, strings, NaN/Infinity and out-of-range values (including very large integers) are invalid. Confidence does not prove factual truth.
- Schema violations become `retry_pending`, `reason=invalid_finalizer_output`, empty text. Application-produced technical results are also always textless. The exact reason is preserved separately from `outcome_kind`; provider/parse failures remain technical, not business refusals.
- Formatting removes outer whitespace and the existing `**`/`__` emphasis markers only. It never collapses internal newlines or slices at 1000 characters. Greeting policy remains idempotent and runs only for customer outcomes.
- Length is checked **after greeting/formatting**, using Unicode character count: [Telegram `sendMessage`](https://core.telegram.org/bots/api#sendmessage) allows 1–4096 characters after entity parsing; our adapter sends plain text without `parse_mode`, and its formatter is identity. [VK `messages.send`](https://dev.vk.com/ru/method/messages.send) documents maximum `message` length 9000; our adapter passes text directly. Limits were checked against these official documents, not by sending live boundary-test messages.
- Exact-limit replies pass whole, including trailing conditions. A reply exceeding its channel limit becomes textless `retry_pending` with `reason=output_too_long`. No automatic rewrite, splitting or multipart sending is added. Channels without an active external adapter (such as internal probes) have no imposed transport limit.

`tests/test_final_response_validation.py` exercises malformed output at each model boundary, nullable confidence, policy idempotence, and the actual Telegram gateway/queue and VK durable-turn gateway through real routing with injected model/tool clients and recording senders. Rejected results create neither assistant messages nor outbound sends. Telegram recovery owns raw transport events; current VK recovery owns `VkTurn` rows, so their retry assertions intentionally target different tables.

Stage 1 does **not** change prompts, Wiki, tool ordering, grounding semantics, retry/backoff, models, or production. Schema confidence remains nullable, but the old routing audit confidence/counter projections are a separate stage-2 limitation. Semantic replay, unified tool-loop work and production release remain separately gated; these deterministic tests do not prove live-model factual correctness.

## Deployed one-Wiki-result stabilization

Production release `aaa9b51219b00875764bf497f40f8ca280c3ae95` preserves two bounded outcomes: after a successful `wiki_lookup`, complete validated `answer-evidence/v1` bypasses a second selector call and invokes the common writer with `response_intent=answer`; a successful `not_found` bypasses that selector call with `response_intent=missing_grounding`, so the sourced fixed office fallback is returned without a writer request. The release also fixes the evidence-budget boundary: an extraction with more than eight facts no longer discards a directly covered answer when coverage references a smaller subset. This avoids treating provider-native continuation formatting or incidental surplus facts as a prerequisite for a fact already fully covered or for a confirmed lack of direct Wiki knowledge.

The broader stage-4 candidate protocol—native continuation, terminal selection after two tools, and calendar/Wiki ordering—remains unaccepted. It requires its own frozen-candidate tests and literal no-send replay before it can be claimed as deployed behavior.

## Terminal paths outside normal finalization

- `social_reply` skips KB work but still reaches the common finalizer with a `social_reply` intent. The finalizer writes the short non-factual text; an invalid or failed finalization is `retry_pending` with empty customer text.
- first-turn `out_of_scope` may render only generic non-business policy text.
- genuine missing grounding may render `cannot_answer`.
- provider recovery exhaustion remains `retry_pending` with no customer text.
- on final-model transport failure after ready grounding, the runtime fails closed as `retry_pending` with empty customer text; application code must not render a business answer from KB prose.

## Required auditability

Internal planner, KB, route, LLM, and tool traces remain available in `response_strategy` and workflow events for operators. They are stored outside the final-model input and outside customer text.

## Acceptance criteria

1. A captured final-model payload contains the allowlisted fields and no internal planner/route/KB/tool mechanics.
2. `planner_reason` and `planner_action` cannot appear anywhere in the serialized final-model user prompt.
3. `prompt_only` performs zero retrieval and zero KB-agent calls and cannot emit a business fact.
4. `kb_grounded` uses ready evidence and the common final model, without mechanical fact joining.
5. Legacy non-ready internal strings never become evidence. Typed acquisition/coverage is preserved without status promotion; unavailable or malformed evidence never invokes the writer. Related facts alone do not justify `answer`.
6. Exact replay of case 594 answers the payment-method question from ready KB evidence instead of applying the “information not confirmed” fallback.
7. A non-factual social or clarification regression can use `SYSTEM_PROMPT.md` without reading KB; a substantive factual request cannot.
8. Candidate acceptance requires targeted/full Docker tests, isolated PostgreSQL/HTTP and both channel boundaries, three independent literal no-send runs per scenario, and independent reviews of the same frozen tree. Production changes or sends are not authorized by candidate acceptance.
9. Exact isolated replay of a historical multi-message failure preserves the unanswered follow-ups in chronological order, sends the combined newline-delimited turn through the common finalizer, keeps the customer's latest explicit constraint active, and produces exactly one practical answer before listing relevant conditions.
10. The finalizer treats combined customer fragments as one practical request and returns the customer result plus a confirmed next step without narrating evidence selection, internal sources, checks, contradictions, or reasoning.


## Evidence → allowed outcome

`allowed_answer_routes` in `answer_evidence.py` is a pure structural function called only after model-selected collection ends. It does not select/finalize tools or interpret question keywords. `full` requires ready acquisition, facts and linked answered parts, with no missing/conflicting/unresolved data; inconsistent full is `evidence_coverage_inconsistent`. All referenced fact IDs must exist. False semantic full is still a model defect, not something this code can prove away.

For executed Wiki, partial/none/conflicting or nonempty missing/conflicts/unresolved allow only cannot_answer. Ambiguous coverage without these blockers allows clarification_requested or cannot_answer; the model must distinguish real customer-resolvable ambiguity from absent knowledge. Full permits supported customer outcomes, still constrained by direct evidence. Calendar-only without Wiki remains answerable from executed calendar evidence. Calendar plus missing business evidence does not bypass Wiki restrictions. Pure social intent allows only social_reply and excludes policy evidence. A model answer cannot be relabelled to bypass this restriction. Outer acquisition status must match the typed packet; otherwise finalization fails with evidence_acquisition_mismatch.

In a non-answerable factual writer input, facts, candidate basis, answered-part IDs, conflict fact IDs and calendar data are removed; no duplicate business facts or basis enter tool_facts. Original tool evidence is retained only in bounded audit. The original customer context and sourced profile_no_answer_option remain. This option is factual next-step information for the LLM, not canned customer wording; contacts/hours/actions absent from it must not be invented. Social replies receive no policy option.

The existing single writer receives allowed_routes. A forbidden output is rejected with `final_response_route_not_allowed`, empty retry_pending, no assistant persistence or send. It is not relabelled as cannot_answer. Successful model-written cannot_answer is preserved subject only to existing formatting. Technical collection errors bypass the writer. Both Telegram and VK use this shared boundary. Audit captures the actual restricted writer input including allowed_routes, not a reconstructed original packet.

HTTP attempts use configured per-attempt timeout without a deadline, otherwise min(configured_timeout, remaining_deadline). No division among future attempts. An exhausted deadline sends no HTTP and increments no attempt. Backoff stays within remaining budget; key selection, endpoints, model and retry counts are unchanged. Client metadata records actual attempt timeouts and error types without raw private payloads.
