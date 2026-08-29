# Support Agent Architecture Overview

## Core runtime

> Production uses the simple `simple_llm_wiki` runtime. It removes the legacy planner loop but preserves the mandatory OKF knowledge path: compact catalog, semantic LLM navigation, selective full-page reads, LLM coverage review, grounded extraction, and customer finalization. `simple_full_corpus` is a deprecated regression path and must not be deployed.

The production target is the direct `simple_llm_wiki` path. The bounded planner loop remains only as a non-production compatibility path.

Runtime layers:
- **FastAPI app** for inbound API, health checks, the internal probe-session API, and the read-only VK dialog viewer API
- **Telegram gateway + polling worker** for chat delivery
- **VK gateway + VK Bots Long Poll worker** for VK community direct messages
- **React + Tailwind viewer-web** published separately on port `3002` for read-only dialog browsing, with optional shared-key login, long-lived `HttpOnly` cookie sessions, installable online-first PWA shell, theme toggle, mobile master-detail overlay navigation with slide-in / swipe-back detail transitions, and touch-driven day navigation in the dialog list
- **Viewer Push worker** for opt-in Web Push delivery from a transactional PostgreSQL outbox to authenticated viewer PWA installations
- **PostgreSQL** as system of record for cases, messages, workflow events, and transport state
- **Simple LLM-wiki answer path** for production turns, without the legacy planner loop
- **Separate KB agent** for Karpathy-style wiki navigation, selective page reads, and grounded fact extraction
- **Versioned factual Wiki source and generic prompts** under `deploy/profile-source/`
- **Compiled runtime profile artifact** under `deploy/runtime-profile/`, copied to the external production mount only after validation
- **Optional runtime tools** for current-date / calculation / environment-aware checks
- **Provider-aware LLM client layer** with bounded retry / timeout handling for transient failures and ordered multi-key failover for Mistral/openai-compatible roles

## Production per-turn contract

The production path is:

1. build bounded dialogue context;
2. load only the compact OKF index/page-card catalog;
3. let the KB agent choose at most three starting pages semantically;
4. read those pages in full;
5. run coverage review and add at most two pages when required;
6. extract grounded facts and a compact answer basis;
7. finalize one customer answer from the allowlisted evidence packet.

A successful navigation result with no information need and no selected pages is a normal `no_information_need` outcome, not a transport failure. It bypasses page reading, coverage review, and extraction; the finalizer receives an empty fact packet and may produce a non-factual dialogue response from the active system prompt and dialogue history. A genuine navigation/provider/structured-output failure remains `retry_pending` and does not emit customer text.

The runtime must not send every full KB page to a model as a substitute for wiki navigation. The `simple_llm_wiki` caller requires coverage review when pages are selected, even if a stale global skip flag is present.

## Legacy bounded-loop contract

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

### 3. Prompt and profile layer
`SYSTEM_PROMPT.md` defines only the role, evidence boundary, dialogue behavior, and output style. `KB_AGENT_PROMPT.md` defines only semantic page navigation, coverage review, and grounded extraction. Business facts, contacts, services, prices, and operating rules live only in the factual Wiki pages. The reviewed source and compiled runtime artifact are versioned; production releases reject an external mount that differs from that artifact.

## Final outcomes

The application-level outcomes are now:
- `answer`
- `cannot_answer`
- `out_of_scope`
- `clarification_requested`

`human_escalation` and `hermes_escalation` are no longer first-class runtime outcomes for the main support flow.

## Reliability and degradation rules

1. **Every OpenAI-compatible LLM call uses one bounded recovery contour.** On a transient transport/`408`/`409`/`425`/ordinary `429`/`5xx` failure, the client retries the active slot using `Retry-After` when present or exponential jittered backoff, within the role deadline. The shared deadline must accommodate every configured attempt plus its backoff; it must never equal the nominal timeout of one attempt. After that budget is exhausted, it cools down the failing slot and tries the next eligible key. Auth and explicit quota/credit/billing exhaustion still enter immediate slot disable/cooldown and failover. The contour covers direct, KB-agent, and summary roles.
2. **Full LLM-recovery exhaustion has one safe terminal outcome.** The runtime records `retry_pending` with an empty payload and does not send a customer message. It never exposes a provider exception, raw KB text, or an LLM-caused `cannot_answer`. Honest missing-grounding remains `cannot_answer`; it is not conflated with provider unavailability. KB navigation, coverage review, and grounded-extraction traces are retained in the routing audit, including attempt count, duration, and non-secret error class.
3. **Customer-visible terminal responses use one output boundary.** `social_reply` finalizes without retrieval/KB work; a social-model failure returns a neutral polite answer. A first reply receives exactly one standard greeting, while internal mechanics (profile/scope, KB/tool/route/trace terms, and structured-output delimiters) are rejected before transport. `out_of_scope` may finalize directly only for the first customer turn: an out-of-scope decision after an assistant reply must first perform KB processing so contextual post-service follow-ups are not discarded.
4. **Key-pool continuity is observable without secrets**:
   - `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS` accept ordered CSV key pools; legacy single-key vars remain valid as the primary slot
   - `last_call_info` / LLM trace preserve `api_key_index`, `used_failover`, `failover_count`, and failover events including non-secret reason class and `Retry-After`
   - warning logs and LLM-usage summaries show reserve-key use
5. **The KB agent resolves broad and contextual language semantically.** Core application code does not contain business-topic dictionaries, FAQ branches, or prompt-only factual shortcuts.
6. **The KB agent is the heavy reasoning step**. The stronger model is allocated to page selection / selective reading / coverage review / grounded extraction.
7. **Ready grounding is final-answer evidence, not a phrase list.** The customer-facing final model receives only the current question, bounded `user|assistant` history, compact `answer_basis`, grounded facts, and normalized tool summaries. It must answer the main question first and omit irrelevant evidence.
8. **The customer finalizer receives no execution reasoning.** Planner/route metadata, case state, full pages, navigation traces, raw tool payloads, and audit fields remain operator telemetry. If final-model recovery is exhausted, the runtime returns `retry_pending` with empty text; application code does not render a business answer from keywords or KB prose.
9. **Date/tool paths must advance after the tool result is gathered**. A live runtime must not repeat a calendar calculation for the same turn once the relevant tool result is already present.
10. **Relative-date resolution is runtime-anchored**. Words such as `сегодня`, `завтра`, and `послезавтра` resolve from the active request's runtime date, and clock-time phrases like `к 15:00` must not be misread as a calendar day. The calendar tool contains no business schedule heuristics.
11. **Production knowledge navigation remains semantic.** Keyword dictionaries, deterministic business-topic routing, coverage-review bypasses, and whole-corpus prompting are not production paths.
12. **Failover is observable without leaking secrets**:
   - warning logs are emitted when the client switches from one key slot to another
   - `last_call_info` / LLM trace preserve `api_key_index`, `used_failover`, `failover_count`, and structured `failover_events`
   - LLM usage summaries aggregate `failover_count` and `failover_calls` so operators can tell when reserve keys are in active use

See also: `docs/architecture/kb-agent-hardening.md`.

## Inbound coalescing and transport-state persistence

Telegram and VK place accepted text events into one channel-independent, in-memory `InboundQueue` before `RoutingService.handle_inbound()`. The queue is keyed by `(channel, external_chat_id)`: it combines texts received within a 5-second quiet window (with a 15-second maximum wait) into one newline-delimited customer turn, while different dialogs continue independently. Polling acknowledges the transport event after validation, dedupe, raw journaling, and enqueueing; it never waits for LLM work or delivery.

Each package has a monotonically increasing revision. A new source event or human override supersedes the active revision. Routing happens outside the polling flow. For VK, the combined inbound retains the newest source external message ID; immediately before journaling and sending a generated reply, the worker compares it with the durable latest-inbound marker for that conversation. Thus an inbound that is already journaled but whose `queue.submit()` is waiting behind the old generation's final boundary suppresses that stale reply instead of producing two bot messages. The VK worker journals the current Long Poll updates before recovery/replay. A provider `retry_pending` result is stored against every raw `transport_event` in that unanswered package with a 5-second next-attempt time and monotonically increasing attempt count. Durable replay groups all `retry_pending` `message_new` events for one conversation in `(received_at, id)` order, atomically claims the complete group as `processing`, and rebuilds one newline-delimited customer turn; it never drops an older unanswered message merely because a newer unanswered message arrived. The newest unanswered event owns the group's next-attempt schedule, so an older event is deliberately not answered alone while a newer part of the same customer turn is still waiting. A failed or partial compare-and-set claim is rolled back and contributes zero to the worker's `processed` count. Expired claims are recovered. Before routing and again at the pre-send transaction, the worker compares the group's newest external message ID with the latest inbound ID for that conversation; an inbound outside the claimed group or active human override suppresses the historical retry without delivery. The combined inbound turn and a `pending` outbound intent are committed atomically before `messages.send`. An ambiguous send failure is retried from that journaled text with the same stable `random_id`, without running the LLM again. Coalescing batch state itself remains deliberately process-local; PostgreSQL `transport_events` are the durable source for replay after a worker restart.

The repository keeps transport-level persistence separate from business dialogue content:
- `transport_events` — raw inbound/outbound transport-event journal with dedupe key and processing status
- `outbound_transport_sends` — bot-send reconciliation records used to match later transport-side echo/activity events; a VK bot delivery creates and commits a `pending` row with its generated `random_id` before calling `messages.send`, then finalizes it to `sent` or `failed`
- `conversation_transport_states` — per-conversation transport state such as last bot reply, last admin reply, and active human-override window

Raw source events remain individually auditable; after an actual delivery, `messages` contains exactly one combined `user` turn and one `assistant` turn for the package. This transport layer exists around the current `RoutingService`; it does not replace the existing case/message runtime.

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
