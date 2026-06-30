# Support Agent Runtime Spec

## Current target behavior

The support agent is a **bounded, policy-driven conversational agent**.

It is not a ticket router and not an escalation-first bot.

## Main invariants

1. The agent must stay within an explicit domain scope from the external profile.
2. The agent must prefer grounded answers from KB and runtime tools.
3. Substantive KB reasoning must pass through a dedicated KB agent that reads the compiled wiki selectively rather than treating lexical snippets as the final reasoning surface.
4. The customer-facing support agent and the KB agent must use separate external prompt files.
5. The agent must not fabricate facts when KB/tool evidence is missing.
6. If the answer cannot be grounded, the final outcome must be `cannot_answer`.
7. If the request is outside the domain, the final outcome must be `out_of_scope`.
8. If a critical ambiguity blocks a safe answer, the final outcome must be `clarification_requested`.
9. The main runtime flow must not silently switch into human or Hermes escalation.
10. A transient LLM-provider failure must not discard already gathered KB facts when a short grounded fallback answer is still possible.
11. Broad but clearly in-domain openers should prefer a safe overview answer over unnecessary clarification.
12. Channel-specific transport workers must reuse the same application runtime rather than creating a second support agent.
13. VK transport-level manual-admin intervention must silence auto-replies for 1 hour from the last unmatched `message_reply`.
14. Transport-level override must be re-checked immediately before a VK reply is sent.

## Decision loop

Per inbound turn:
1. classify social vs substantive turn
2. for substantive turns, assess the next action
3. optionally gather runtime tool observations
4. optionally gather KB facts
5. run the dedicated KB agent over the compiled wiki material already gathered
6. re-assess / finalize
7. emit one of the allowed outcomes

## Tool-aware reasoning

The loop must support runtime facts that are not reliable from model memory alone.

Example class of task:
- user asks about a calendar date
- KB contains a rule such as weekend-only availability
- tool computes the weekday for the relevant date using the current year if the year is omitted
- once the tool result exists, the loop must advance to KB / answer generation instead of repeating `use_tool`
- final answer combines KB rule + tool result

## Provider failure handling

- LLM clients must support bounded timeout / retry / backoff settings for transient network or upstream-provider failures.
- The KB agent is the stronger reasoning step and should be allowed to use the stronger model tier than the final answering step.
- If the planner or final answer step fails after KB retrieval succeeded, the runtime should prefer a grounded degradation path over a template refusal whenever the retrieved KB still supports a safe short answer.
- Deterministic degradations must be based on retrieved facts generically, not on one-off question-specific hardcodes.

## Channel contract notes

### Telegram
- worker polls Bot API updates
- gateway normalizes inbound updates into the shared runtime contract

### VK
- worker polls VK Bots Long Poll API
- `message_new` is the inbound customer message event
- `message_reply` is the observed outgoing community activity event
- direct messages to the community are in scope for v1
- group chats / besedy and history backfill are out of scope for v1
- unmatched `message_reply` activates a 1-hour human override window
- inbound messages during override are still persisted, but no automatic reply is sent
- transient long-poll transport failures reported as `transport_error:*` must not crash the worker loop; they should degrade to an empty poll / retryable condition
- the `vk-worker` deployment must use automatic container restart (`restart: unless-stopped` or an equivalent supervisor policy)

## External prompt contract

The runtime contract depends on hot-editable external prompt files:
- `SYSTEM_PROMPT.md` — customer-facing support agent behavior, tool obligations, and output format rules
- `KB_AGENT_PROMPT.md` — KB wiki-reader behavior, page-selection logic, and grounded fact extraction rules

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
