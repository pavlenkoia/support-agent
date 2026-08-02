# Support Agent Runtime Spec

## Current target behavior

The support agent is a **bounded, policy-driven conversational agent**.

It is not a ticket router and not an escalation-first bot.

## Main invariants

1. The agent must stay within an explicit domain scope from the external profile.
2. The agent must prefer grounded answers from KB and runtime tools for substantive requests.
3. Substantive KB reasoning must pass through a dedicated KB agent that reads the compiled wiki selectively rather than treating lexical snippets as the final reasoning surface.
4. The customer-facing support agent and the KB agent must use separate external prompt files.
5. The agent must not fabricate facts when KB/tool evidence is missing.
6. A planner-approved `social_reply` (greeting, acknowledgement, thanks) is not a substantive request: it must finish immediately without retrieval or KB-agent work and always return a short polite `answer`, including when its model call fails.
7. A planner-approved `out_of_scope` is also terminal: it must render the policy-defined out-of-scope text directly, without retrieval, KB-agent work, or final answer-model generation.
8. If a substantive answer cannot be grounded, the final outcome must be `cannot_answer`.
9. If the request is outside the domain, the final outcome must be `out_of_scope`.
10. If a critical ambiguity blocks a safe answer, the final outcome must be `clarification_requested`.
11. The main runtime flow must not silently switch into human or Hermes escalation.
12. A transient LLM-provider failure must not discard already gathered KB facts when a short grounded fallback answer is still possible.
13. Broad but clearly in-domain openers should prefer a safe overview answer over unnecessary clarification.
14. When the KB agent returns `grounding_status=ready`, final-answer generation must preserve the contextual customer answer, but cannot downgrade the turn to `clarification_requested`; if it does, the runtime emits a grounded fallback from curated `grounded_facts` or `answer_basis`.
15. Channel-specific transport workers must reuse the same application runtime rather than creating a second support agent.
16. VK transport-level manual-admin intervention must silence auto-replies for 1 hour from the last unmatched `message_reply`.
17. Transport-level override must be re-checked immediately before a VK reply is sent.
18. The first customer-facing reply in a dialogue must begin with exactly one standard `Здравствуйте!`. The final-answer model is instructed not to add a greeting itself; the runtime prepends the standard greeting only when `first_reply_in_dialogue` is true and the reply does not already begin with a recognised greeting. Later replies are not changed by the runtime.

## Decision loop

Per inbound turn:
1. classify social vs substantive turn
2. for a planner-approved `social_reply` or `out_of_scope`, finalize the policy-defined terminal response without KB lookup or final answer-model generation
3. for substantive in-domain turns, assess the next action
4. optionally gather runtime tool observations
5. optionally gather KB facts
6. run the dedicated KB agent over the compiled wiki material already gathered
7. re-assess / finalize
8. emit one of the allowed outcomes

## Tool-aware reasoning

The loop must support runtime facts that are not reliable from model memory alone.

Example class of task:
- user asks about a calendar date
- relative-date words such as `сегодня`, `завтра`, and `послезавтра` are resolved from the runtime current date for that request
- KB contains a rule such as weekend-only availability
- tool computes the weekday for the relevant date using the current year if the year is omitted
- day-only parsing must not reinterpret clock-time phrases like `к 15:00` as day-of-month input
- once the tool result exists, the loop must advance to KB / answer generation instead of repeating `use_tool`
- final answer combines KB rule + tool result
- weekend-only schedule rules must stay scoped to jump/schedule intent rather than unrelated office/certificate questions

## Provider failure handling

- LLM clients must support bounded timeout / retry / backoff settings for transient network or upstream-provider failures, including an incomplete HTTP response body (`IncompleteRead`).
- Mistral/openai-compatible roles must also support ordered multi-key pools via `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS` with backward-compatible single-key aliases.
- Transient/network/5xx failures should retry on the current key; auth/quota/key-specific failures should fail over to the next key when available.
- The runtime must expose non-secret failover observability: warning logs on key-slot switches plus `api_key_index` / `used_failover` / `failover_count` / `failover_events` in LLM trace metadata.
- The KB agent is the stronger reasoning step and should be allowed to use the stronger model tier than the final answering step.
- If the planner or final answer step fails after KB retrieval succeeded, the runtime should prefer a grounded degradation path over a template refusal whenever the KB agent's extracted `grounded_facts` or `answer_basis` support a safe short answer. It must not select sentences from arbitrary loaded KB pages.
- Deterministic degradations must be based on retrieved facts generically, not on one-off question-specific hardcodes.
- The KB agent runtime must support feature-flagged hardening controls for structured-output reliability:
  - deterministic navigation over wiki page cards
  - optional coverage-review bypass when page selection is already sufficiently narrow
  - minimal extraction schema for models that struggle with larger JSON envelopes
  - tolerant JSON recovery from fenced or wrapped objects before surfacing a parsing failure
- These hardening controls must be runtime-configurable so production can stay on the current model while isolated test contours experiment with alternative KB-agent models.

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
- unmatched `message_reply` is persisted in the dialog as a human/operator outbound message and activates a 1-hour human override window
- inbound messages during override are still persisted, but no automatic reply is sent
- transient long-poll transport failures reported as `transport_error:*` must not crash the worker loop; they should degrade to an empty poll / retryable condition
- the `vk-worker` deployment must use automatic container restart (`restart: unless-stopped` or an equivalent supervisor policy)

## VK dialog viewer contract

The repository includes a read-only VK dialog viewer web slice.

Access/auth requirements:
- the viewer may be protected by one shared access key
- when enabled, the web UI opens on a one-field login screen before showing dialogs
- successful login is persisted with a long-lived signed `HttpOnly` cookie (current default 365 days)
- the same auth gate protects the underlying `/api/viewer/*` read-only routes, not just the React screen

UI requirements:
- React + Tailwind frontend served separately on port `3002`
- compact sticky top header
- header-right light/dark theme toggle
- header-right opt-in Web Push bell; it requests browser permission only after an explicit operator click
- icon-only theme button with tooltip/accessibility text instead of visible label
- left panel header contains the date picker plus an icon-only refresh button for reloading the current selected day
- on touch/mobile screens, swiping the dialog list left-to-right selects the previous day and right-to-left selects the next day; the current list follows the finger, snaps back if the gesture is incomplete, and completes its exit before the new day reloads
- the day reload must not render a separate `Загружаю диалоги…` panel or animate the newly loaded list into view
- no `Диалоги` heading and no `Диалогов за день N` counter block
- left panel items show `№<case_id> Имя пользователя` when `case_id` exists, otherwise `name/id`, plus time and `message_count`
- inbound viewer messages prefer cached `users.display_name` for the author label and fall back to VK id when profile enrichment is unavailable
- no dialog preview text in the list
- dialog list ordering is earliest-first within the selected day
- both the list time and the message time must be rendered from full timestamps in the browser's current timezone
- right panel renders the selected dialog as a chat view for the selected day only
- no separate desktop right-panel header above the messages; on mobile the detail view uses only a compact header with an icon-only back control and the real current `case_id` label when available
- on narrow/mobile screens the viewer switches to a master-detail overlay flow with explicit return to the list
- selecting a dialog on mobile opens the detail pane with a right-to-left slide transition, and swipe-back or the icon-only back control returns left-to-right to the list
- left and right panels must scroll independently, and the mobile detail pane must be able to scroll to the final message without clipping

Backend requirements:
- viewer API lives inside the existing FastAPI application
- viewer endpoints are read-only and day-scoped
- selected-day filtering applies both to dialog summaries and to message history
- auth endpoints exist under `/api/viewer/auth/...` for `me`, `login`, and `logout`
- runtime viewer auth is controlled by env flags/key material (`VIEWER_AUTH_ENABLED`, `VIEWER_AUTH_KEY`, cookie name/TTL/secure settings)
- viewer-web is delivered as an installable online-first PWA under the same origin, with manifest, service worker, app icons, and standalone display mode
- the PWA caches only the static shell/assets in v1; `/api/viewer/*` data is intentionally not treated as an offline history cache
- when the shell opens without network, it must show a clear offline/unavailable state instead of a white screen
- when Web Push is configured on HTTPS, a closed PWA receives a native notification for a new VK user message; an open viewer refreshes the affected data without a duplicate native banner
- push payloads must not contain customer message text; they may contain only transport-safe conversation/case identifiers

## Internal probe session slice

The repository also ships an internal probe session slice for operator/governor-driven runtime checks.

Requirements:
- probe sessions reuse the real routing/runtime path but do not publish to customer-facing transports
- probe sessions are persisted as explicit test sessions on both `conversations` and `support_cases`
- marker fields include `is_test`, `source`, `session_type`, `scenario_name`, and `requested_by`
- the shipped HTTP surface includes create/list/get/send/wait/messages/trace/close operations under `/api/internal/probe/...`
- the normalized probe channel reported by the API is `internal_test`

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
