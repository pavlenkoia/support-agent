# Support Agent Architecture Overview

## Core runtime

This repository now targets a **bounded support-agent loop** rather than a linear `classify -> KB -> escalation` pipeline.

Runtime layers:
- **FastAPI app** for inbound API and health checks
- **Telegram gateway + polling worker** for chat delivery
- **PostgreSQL** as system of record for cases, messages, and workflow events
- **Bounded orchestrator loop** for per-turn decision making
- **External compiled knowledge base** outside the repository
- **Optional runtime tools** for current-date / calculation / environment-aware checks
- **Provider-aware LLM client layer** with bounded retry / timeout handling for transient failures

## Per-turn orchestration contract

Each inbound turn is processed as a bounded loop with explicit actions and outcomes.

States:
1. `intake`
2. `assess`
3. `gather`
4. `decide`
5. `finalize`
6. `stop`

Allowed actions inside the loop:
- `read_kb`
- `use_tool`
- `ask_clarification`
- `answer_from_kb`
- `out_of_scope`
- `cannot_answer`
- `social_reply`

Hard limits:
- max 3 loop iterations per turn
- max 1 tool-gather step per iteration
- no hidden escalations
- mandatory final outcome or explicit clarification request

## Architectural boundaries

### 1. Orchestrator
Chooses the next action and stops the loop within bounded limits.

### 2. Knowledge + tool layer
Provides grounded facts from:
- compiled KB pages
- runtime tools (for example calendar/day-of-week checks)

### 3. Policy layer
Renders final user-facing fallback wording without hard-coding domain-specific channels into core state names.

Examples:
- core outcome: `cannot_answer`
- policy decides whether the text says:
  - ask a contact channel
  - ask to write elsewhere
  - provide a generic refusal
  - simply state that the answer is unavailable

## Final outcomes

The application-level outcomes are now:
- `answer`
- `cannot_answer`
- `out_of_scope`
- `clarification_requested`

`human_escalation` and `hermes_escalation` are no longer first-class runtime outcomes for the main support flow.

## Reliability and degradation rules

1. **Transient provider failures are retried** with bounded timeout/backoff settings at the LLM client layer.
2. **Planner over-clarification is constrained**:
   - if KB has not been read yet and the opener is broad but clearly in-domain, prefer `read_kb`
   - if KB is already found and a short safe overview is possible, prefer `answer_from_kb`
3. **Final-answer LLM failure does not erase grounded knowledge**. If KB facts were already gathered and the final answer-generation step fails, the runtime must return a short grounded fallback answer synthesized from the retrieved facts.
4. **Deterministic fallbacks must stay generic**. The core must not hardcode one business question as the only fallback path; the degradation path must work across in-domain topics such as certificates, schedules, and rules.

## History and context

Conversation context is built from persisted `messages` rows and must include:
- inbound user messages
- outbound assistant replies after successful delivery

This preserves follow-up reasoning over `user -> assistant -> user` turns instead of user-only history.
