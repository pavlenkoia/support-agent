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

Only `grounding_status=ready` may cross into the finalizer as `kb_grounded` evidence. A non-ready extraction can contain internal coverage, source, or availability observations; it is not customer evidence and its `answer_basis` and `grounded_facts` are replaced with an empty packet before finalization. The finalizer then owns a useful clarification or an honest `cannot_answer` using only separately allowlisted customer-safe policy evidence, if present. This is not a classifier, domain keyword branch, or KB answer source.

The mode and response intent are application-owned control values. They are not derived by exposing planner output to the final model.

## Finalization input allowlist

The finalization contract is appended at system-message priority after the active profile prompt. The profile prompt contains role, safety, dialogue, and style rules but no business facts. The evidence boundary is authoritative for every factual claim.

The final model may receive only:

- the full active `SYSTEM_PROMPT.md` as a generic behavior contract, followed in the same system message by the application-owned generic finalization contract;
- `knowledge_mode` (`prompt_only` or `kb_grounded`);
- the current customer question;
- at most the last 10 valid current-case dialogue items as `{role, content}`, with roles restricted to `user|assistant`;
- KB `answer_basis` and ordered `grounded_facts` when `knowledge_mode=kb_grounded`;
- normalized customer-relevant tool observations containing only their public kind and summary.

The final model must not receive:

- `planner_action`, `planner_reason`, planner confidence, or planner trace;
- route reasons, loop trace, audit fields, case state, database identifiers, or session machinery;
- KB navigation, selected-page rationale, coverage-review rationale, extraction rationale, LLM trace, source-selection mechanics, or raw selected KB pages;
- raw tool requests/results/payloads or tool execution trace;
- duplicated representations of the dialogue such as both `recent_turns` and `recent_messages`;
- internal summaries whose provenance or relevance is not guaranteed.

The application constructs a new allowlisted finalization packet. It must never serialize a broad runtime `context` dictionary into the final-model request.

There is no legacy `answer(...)` bypass for KB-ready customer finalization. A configured finalizer must implement the common `respond(...)` contract; otherwise the runtime fails closed instead of sending broad context through an older path.

## Evidence semantics

- `SYSTEM_PROMPT.md` is authoritative only for role, scope, safety, dialogue behavior, and style. It is not a source of business facts.
- In `prompt_only`, empty KB evidence is expected and the final model may produce only non-factual social text or a necessary clarification.
- In `kb_grounded`, `grounding_status=ready` means the supplied KB evidence has already passed the KB boundary. The final model must not re-decide whether that evidence exists.
- `grounding_status=not_found` is a semantic no-direct-answer result. Its extraction text, source-selection outcome, and any explanation of unavailable or dynamic facts remain operational telemetry and never enter the finalizer packet.
- `answer_basis` is the compact intended answer from the KB agent. `grounded_facts` define the supported factual scope and modality. The final model may rephrase them naturally but may not add, strengthen, contradict, or silently discard facts needed to answer the current question.

- If `answer_basis` and `grounded_facts` conflict, the final model must stay within the exact grounded facts and avoid the unsupported part of the basis.
- Evidence is not mandatory prose. The final model selects only facts needed for the current question and must not mechanically concatenate all facts.
- A ready grounded answer must not be downgraded to `clarification_requested` or `cannot_answer` merely because the final model prefers to re-open the decision.

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
5. A non-ready extraction with non-empty internal strings reaches the finalizer only as an empty evidence packet and cannot make the route `answer`.
6. Exact replay of case 594 answers the payment-method question from ready KB evidence instead of applying the “information not confirmed” fallback.
7. A non-factual social or clarification regression can use `SYSTEM_PROMPT.md` without reading KB; a substantive factual request cannot.
8. Targeted tests, the full suite, production image/hash checks, and literal internal production probes all pass before the change is reported complete.
9. Exact isolated replay of a historical multi-message failure preserves the unanswered follow-ups in chronological order, sends the combined newline-delimited turn through the common finalizer, keeps the customer's latest explicit constraint active, and produces exactly one practical answer before listing relevant conditions.
10. The finalizer treats combined customer fragments as one practical request and returns the customer result plus a confirmed next step without narrating evidence selection, internal sources, checks, contradictions, or reasoning.
