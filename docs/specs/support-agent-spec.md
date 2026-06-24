# Support Agent Runtime Spec

## Current target behavior

The support agent is a **bounded, policy-driven conversational agent**.

It is not a ticket router and not an escalation-first bot.

## Main invariants

1. The agent must stay within an explicit domain scope from the external profile.
2. The agent must prefer grounded answers from KB and runtime tools.
3. The agent must not fabricate facts when KB/tool evidence is missing.
4. If the answer cannot be grounded, the final outcome must be `cannot_answer`.
5. If the request is outside the domain, the final outcome must be `out_of_scope`.
6. If a critical ambiguity blocks a safe answer, the final outcome must be `clarification_requested`.
7. The main runtime flow must not silently switch into human or Hermes escalation.
8. A transient LLM-provider failure must not discard already gathered KB facts when a short grounded fallback answer is still possible.
9. Broad but clearly in-domain openers should prefer a safe overview answer over unnecessary clarification.

## Decision loop

Per inbound turn:
1. classify social vs substantive turn
2. for substantive turns, assess the next action
3. optionally gather KB facts
4. optionally gather tool observations
5. re-assess
6. finalize with one of the allowed outcomes

## Tool-aware reasoning

The loop must support runtime facts that are not reliable from model memory alone.

Example class of task:
- user asks about a calendar date
- KB contains a rule such as weekend-only availability
- tool computes the weekday for the relevant date using the current year if the year is omitted
- final answer combines KB rule + tool result

## Provider failure handling

- LLM clients must support bounded timeout / retry / backoff settings for transient network or upstream-provider failures.
- If the planner or final answer step fails after KB retrieval succeeded, the runtime should prefer a grounded degradation path over a template refusal whenever the retrieved KB still supports a safe short answer.
- Deterministic degradations must be based on retrieved facts generically, not on one-off question-specific hardcodes.

## Outcome semantics

### `answer`
Grounded answer is available from KB and/or tool observations.

### `cannot_answer`
The agent remains in-domain but lacks enough reliable information to answer.

### `out_of_scope`
The user asks about something outside the profile domain.

### `clarification_requested`
A short follow-up question is required before any grounded answer is possible.

Clarification is not the default for broad in-domain requests when the KB already supports a safe overview answer.
