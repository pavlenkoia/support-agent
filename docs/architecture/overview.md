# Support Agent Architecture Overview

## Core runtime

This repository now targets a **bounded support-agent loop** rather than a linear `classify -> KB -> escalation` pipeline.

Runtime layers:
- **FastAPI app** for inbound API, health checks, the internal probe-session API, and the read-only VK dialog viewer API
- **Telegram gateway + polling worker** for chat delivery
- **VK gateway + VK Bots Long Poll worker** for VK community direct messages
- **React + Tailwind viewer-web** published separately on port `3002` for read-only dialog browsing, with optional shared-key login, long-lived `HttpOnly` cookie sessions, installable online-first PWA shell, theme toggle, mobile master-detail overlay navigation with slide-in / swipe-back detail transitions, and touch-driven day navigation in the dialog list
- **Viewer Push worker** for opt-in Web Push delivery from a transactional PostgreSQL outbox to authenticated viewer PWA installations
- **PostgreSQL** as system of record for cases, messages, workflow events, and transport state
- **Bounded orchestrator loop** for per-turn decision making
- **Separate KB agent** for Karpathy-style wiki navigation, selective page reads, and grounded fact extraction
- **External compiled knowledge base** outside the repository
- **External prompt files** (`SYSTEM_PROMPT.md`, `KB_AGENT_PROMPT.md`) outside the repository
- **Optional runtime tools** for current-date / calculation / environment-aware checks
- **Provider-aware LLM client layer** with bounded retry / timeout handling for transient failures and ordered multi-key failover for Mistral/openai-compatible roles

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
- `answer_from_prompt`
- `read_kb`
- `use_tool`
- `ask_clarification`
- `answer_from_kb`
- `out_of_scope`
- `cannot_answer`
- `social_reply`

Operational rule:
- planner-visible runtime capabilities are a closed, explicit set; `use_tool` is legal only when a listed capability applies
- once `tool_observations` already contains the needed runtime fact, the planner must advance to `read_kb` or `answer_from_kb` instead of repeating `use_tool`
- a planner `use_tool` request outside the advertised capability set is rejected before tool execution, recorded as internal `planner_action_rejected`, then deterministically advances to KB/finalization in the same iteration; it cannot repeat or become a customer refusal solely because the requested capability does not exist
- `tool_unavailable` remains a defensive invariant-violation path for a capability/runtime mismatch and uses the same terminal safe transition

Hard limits:
- max 3 loop iterations per turn
- max 1 tool-gather step per iteration
- no hidden escalations
- mandatory final outcome or explicit clarification request

## Architectural boundaries

### Viewer Push boundary

Viewer Push is a transport adjunct around persisted VK inbound messages, not a second support workflow. The inbound persistence transaction creates a durable outbox event only for `channel=vk`; a separate `viewer-push-worker` delivers it after commit. The browser receives only `conversation_id` / `case_id`, fetches current data through the existing viewer API, and never treats a push payload as message history. VAPID private-key material remains in the external runtime environment; browser subscription registration is protected by viewer auth.

### 1. Orchestrator
Chooses the next action and stops the loop within bounded limits.

### 2. Knowledge + tool layer
Provides grounded facts from:
- compiled KB pages
- a dedicated KB agent that plans wiki navigation, selectively reads full pages, and returns grounded facts / answer basis
- runtime tools (for example calendar/day-of-week checks)

### 3. Prompt layer
Behavior is controlled by external hot-editable prompt files:
- `SYSTEM_PROMPT.md` for the customer-facing support agent
- `KB_AGENT_PROMPT.md` for the KB wiki-reader agent

### 4. Policy layer
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

1. **Every OpenAI-compatible LLM call uses one bounded recovery contour.** On a transient transport/`408`/`409`/`425`/ordinary `429`/`5xx` failure, the client retries the active slot using `Retry-After` when present or exponential jittered backoff, within the role deadline. After that budget is exhausted, it cools down the failing slot and tries the next eligible key. Auth and explicit quota/credit/billing exhaustion still enter immediate slot disable/cooldown and failover. The contour covers direct, KB-agent, and summary roles.
2. **Full LLM-recovery exhaustion has one safe terminal outcome.** The runtime records `retry_pending` with an empty payload and does not send a customer message. It never exposes a provider exception, raw KB text, or an LLM-caused `cannot_answer`. Honest missing-grounding remains `cannot_answer`; it is not conflated with provider unavailability.
3. **Customer-visible terminal responses use one output boundary.** `social_reply` finalizes without retrieval/KB work; a social-model failure returns a neutral polite answer. A first reply receives exactly one standard greeting, while internal mechanics (profile/scope, KB/tool/route/trace terms, and structured-output delimiters) are rejected before transport. `out_of_scope` may finalize directly only for the first customer turn: an out-of-scope decision after an assistant reply must first perform KB processing so contextual post-service follow-ups are not discarded.
4. **Key-pool continuity is observable without secrets**:
   - `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS` accept ordered CSV key pools; legacy single-key vars remain valid as the primary slot
   - `last_call_info` / LLM trace preserve `api_key_index`, `used_failover`, `failover_count`, and failover events including non-secret reason class and `Retry-After`
   - warning logs and LLM-usage summaries show reserve-key use
5. **Planner over-clarification is constrained**:
   - if the active `SYSTEM_PROMPT.md` explicitly and completely answers a factual request, the planner chooses `answer_from_prompt`; no KB read is performed merely to reconfirm it
   - if KB has not been read yet and the opener is broad but clearly in-domain, prefer `read_kb`
   - if KB is already found and a short safe overview is possible, prefer `answer_from_kb`
4. **The KB agent is the heavy reasoning step**. The stronger model is allocated to page selection / selective reading / coverage review / grounded extraction.
5. **Ready grounding is final-answer evidence, not a phrase list.** When the KB agent returns `grounding_status=ready`, the customer-facing final model receives the active system prompt, current dialogue, compact `answer_basis`, grounded facts, and relevant tool observations. It must answer the main question first and omit evidence that is not needed for that answer.
6. **The customer finalizer receives no KB-agent audit.** It excludes full selected pages, planner/navigation/review traces, route reasons, raw tool output, and unbounded dialogue reasoning. On final-model transport failure only, a generic grounded fallback may use `answer_basis` first, then curated facts; it must not invent information.
7. **Deterministic fallbacks must stay generic**. The core must not hardcode one business question as the only fallback path; the degradation path must work across in-domain topics such as certificates, schedules, and rules.
8. **Date/tool paths must advance after the tool result is gathered**. A live runtime must not repeat `use_tool` for the same turn once the relevant tool result is already present.
9. **Relative-date resolution is runtime-anchored**. Words such as `сегодня`, `завтра`, and `послезавтра` must resolve from the active request's runtime date, and clock-time phrases like `к 15:00` must not be misread as a calendar day.
10. **Weekend schedule heuristics are intent-scoped**. Weekend-only availability checks are valid for jump/schedule questions, not for office/certificate questions that happen to coexist with weekend wording in KB snippets.
11. **The KB agent now supports a hardened selective-read mode** for less JSON-disciplined models and for safer prod operation on the current Mistral tier:
   - deterministic lexical navigation can replace free-form LLM navigation when enabled
   - coverage review can be skipped when the selected page set is already narrowly scoped
   - grounded extraction can request a minimal JSON schema instead of a larger object with optional fields
   - JSON parsing for KB-agent steps accepts fenced or wrapped objects before failing hard
   - rollout is controlled entirely by runtime flags so prod/test contours can diverge without forking code
9. **Failover is observable without leaking secrets**:
   - warning logs are emitted when the client switches from one key slot to another
   - `last_call_info` / LLM trace preserve `api_key_index`, `used_failover`, `failover_count`, and structured `failover_events`
   - LLM usage summaries aggregate `failover_count` and `failover_calls` so operators can tell when reserve keys are in active use

See also: `docs/architecture/kb-agent-hardening.md`.

## Transport-state persistence

The repository now includes transport-level persistence separate from business dialogue content:
- `transport_events` — raw inbound/outbound transport-event journal with dedupe key and processing status
- `outbound_transport_sends` — bot-send reconciliation records used to match later transport-side echo/activity events
- `conversation_transport_states` — per-conversation transport state such as last bot reply, last admin reply, and active human-override window

This transport layer exists around the current `RoutingService`; it does not replace the existing case/message runtime.

## VK channel behavior

Current VK slice boundaries:
- inbound customer traffic enters through `message_new`
- outgoing community activity is observed through `message_reply`
- supported surface is VK community direct messages, not group chats / besedy
- inbound `message_new` handling may enrich missing `users.display_name` via `users.get` and cache it for the viewer/runtime
- no history backfill is required for v1

Manual-admin override rule:
- unmatched `message_reply` is treated as a live admin reply, persisted as a `human` message, and rendered in viewer-web with the author label `Оператор VK`
- that reply activates silence for 1 hour from the last admin message
- inbound user messages are still persisted during silence
- before a bot reply is actually sent, the worker re-checks override state to prevent a stale race with a human admin reply

Worker recovery rule:
- transient long-poll transport failures returned as `transport_error:*` (including timeouts and `connection reset by peer`) are treated as empty polls, not fatal worker crashes
- the `vk-worker` Compose service is expected to run with `restart: unless-stopped` so container-level recovery exists even if the process exits unexpectedly

## Internal probe / test-session slice

The repository now includes an internal probe API that exercises the real support runtime without using customer-facing transports.

Architectural rules:
- probe traffic enters through a dedicated internal channel and never publishes outbound customer-channel messages
- probe conversations and support cases are explicitly marked with `is_test`, `source`, `session_type`, `scenario_name`, and `requested_by`
- listing/filtering probe sessions reads those persisted markers rather than inferring test state from ad-hoc naming
- the probe API is session-based (`start/list/get/send/wait/messages/trace/close`) so multi-turn runtime verification uses the same persisted history model as normal conversations

## History and context

Conversation context is built from persisted `messages` rows and must include:
- inbound user messages
- outbound assistant replies after successful delivery

This preserves follow-up reasoning over `user -> assistant -> user` turns instead of user-only history.
