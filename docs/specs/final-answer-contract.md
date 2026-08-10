# Final answer contract

## Purpose

The support agent has one common customer-facing finalization boundary for normal substantive answers. That boundary turns approved profile policy plus question-relevant evidence into a natural customer reply. It must not repeat upstream planning or knowledge-navigation work.

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

There are two knowledge modes:

1. `prompt_only`: the active system prompt explicitly and completely answers the current factual question. The planner selects `answer_from_prompt`; retrieval and the KB agent are skipped; the common final model still writes the customer answer.
2. `kb_grounded`: the system prompt alone is not sufficient. Retrieval and the KB agent run; only a compact grounded packet reaches the common final model.

The mode is an application-owned control value. It is not derived by exposing planner output to the final model.

## Finalization input allowlist

The finalization contract is appended at system-message priority after the active profile prompt. This keeps approved business policy intact while making the evidence boundary authoritative over lower-priority prompt payload text. It is generic runtime policy: it contains no domain question, case number, keyword, or expected business answer.

The final model may receive only:

- the full active `SYSTEM_PROMPT.md` as approved profile policy, followed in the same system message by the application-owned generic finalization contract;
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

- `SYSTEM_PROMPT.md` is authoritative for role, scope, safety, approved policy, and facts explicitly stated there.
- In `prompt_only`, the final model answers from the system prompt and dialogue; empty KB evidence is expected.
- In `kb_grounded`, `grounding_status=ready` means the supplied KB evidence has already passed the KB boundary. The final model must not re-decide whether that evidence exists.
- `answer_basis` is the compact intended answer from the KB agent. `grounded_facts` define the supported factual scope and modality. The final model may rephrase them naturally but may not add, strengthen, contradict, or silently discard facts needed to answer the current question.
- Conditional profile fallbacks such as “if exact information is not confirmed” apply only when the corresponding fact is absent from the supplied grounded evidence. They must not override evidence that explicitly satisfies the condition.
- If `answer_basis` and `grounded_facts` conflict, the final model must stay within the exact grounded facts and avoid the unsupported part of the basis.
- Evidence is not mandatory prose. The final model selects only facts needed for the current question and must not mechanically concatenate all facts.
- A ready grounded answer must not be downgraded to `clarification_requested` or `cannot_answer` merely because the final model prefers to re-open the decision.

## Output contract

A normal final answer must:

1. answer the practical customer question first;
2. use natural customer-facing language;
3. include only relevant confirmed conditions or the next useful step;
4. preserve modality (`usually`, `may`, `depends`, conditional scope);
5. avoid mentioning KB, prompts, tools, traces, routes, evidence, reasoning, or internal decisions;
6. obey the common first-reply greeting boundary without duplicating a greeting.

No domain-specific question handler, keyword branch, canned FAQ shortcut, or mechanical fact renderer is part of the normal path.

## Terminal paths outside normal finalization

- `social_reply` may remain a short dedicated social response path without KB work.
- first-turn `out_of_scope` renders approved policy text directly.
- genuine missing grounding may render `cannot_answer`.
- provider recovery exhaustion remains `retry_pending` with no customer text.
- on final-model transport failure after ready grounding, the existing bounded grounded degradation may answer from the compact grounded packet; raw KB pages remain forbidden.

## Required auditability

Internal planner, KB, route, LLM, and tool traces remain available in `response_strategy` and workflow events for operators. They are stored outside the final-model input and outside customer text.

## Acceptance criteria

1. A captured final-model payload contains the allowlisted fields and no internal planner/route/KB/tool mechanics.
2. `planner_reason` and `planner_action` cannot appear anywhere in the serialized final-model user prompt.
3. `prompt_only` performs zero retrieval and zero KB-agent calls, while the common final model still runs.
4. `kb_grounded` uses ready evidence and the common final model, without mechanical fact joining.
5. Exact replay of case 594 answers the payment-method question from ready KB evidence instead of applying the “information not confirmed” fallback.
6. A prompt-sufficient regression still answers from `SYSTEM_PROMPT.md` without reading KB.
7. Targeted tests, the full suite, production image/hash checks, and literal internal production probes all pass before the change is reported complete.
