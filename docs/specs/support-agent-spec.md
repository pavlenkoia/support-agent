# Support Agent Runtime Spec

## Current target behavior

> Production mode is `ANSWER_ENGINE_MODE=agent_tool_loop`: an optional Wiki lookup is selected by the model, then the common finalizer receives bounded dialogue and ready evidence. The legacy fixed retrieval pipeline and `simple_full_corpus` are not production paths. A KB-model outage is technical `retry_pending`, never a customer-facing assertion that the Wiki lacks information.

The support agent is a **bounded, policy-driven conversational agent**.

It is not a ticket router and not an escalation-first bot.

## Main invariants

1. The agent must stay within the domain represented by the active factual Wiki.
2. The system prompt is a generic behavior contract and is never a business-fact source; substantive answers must use grounded KB and runtime-tool evidence.
3. Substantive KB reasoning must pass through a dedicated KB agent that reads the compiled wiki selectively rather than treating lexical snippets as the final reasoning surface.
4. The customer-facing support agent and the KB agent must use separate external prompt files.
5. The agent must not fabricate facts when KB/tool evidence is missing.
6. A planner-approved `social_reply` (greeting, acknowledgement, thanks) is not a substantive request: it must finish immediately without retrieval or KB-agent work and always return a short polite `answer`, including when its model call fails.
7. A planner-approved `out_of_scope` is terminal only on the first customer turn. For a follow-up after an assistant reply, it must first attempt KB reading so a contextual post-service request cannot be discarded as unrelated.
8. Every customer-visible terminal route passes through one output boundary: first replies begin with exactly one `Здравствуйте!`, and texts containing internal mechanics (`profile`, KB/tool/route terms, traces, JSON/brackets) are replaced with the approved customer fallback.
9. If a substantive answer cannot be grounded, the final outcome must be `cannot_answer`.
10. If the first request is outside the domain, the final outcome must be `out_of_scope`.
11. If a critical ambiguity blocks a safe answer, the final outcome must be `clarification_requested`.
12. The main runtime flow must not silently switch into human or Hermes escalation.
13. Exhausted LLM-provider recovery must produce `retry_pending` with empty customer text; application code must not synthesize a domain answer.
14. Broad and contextual language is interpreted semantically from dialogue plus the complete compact Wiki catalog, not by application keyword lists or question-specific branches.
15. When the KB agent returns `grounding_status=ready`, its compact `answer_basis` and grounded facts are evidence for the customer-facing final model. The finalizer is an evidence editor, not an independent decision maker: topical relevance is not proof that evidence answers a question, and it must not derive a new relation, procedure, requirement, or result by combining facts. A substantive `answer` is allowed only when evidence directly covers the factual part of the current turn.
16. The customer-facing final model receives a newly constructed allowlisted packet, never the broad runtime context: no planner action/reason, route/loop/audit metadata, KB navigation/review reasons, raw KB pages, raw tool payloads, case identifiers, duplicate dialogue forms, or dialogue roles outside `user|assistant` may enter its request. The dialogue projection is capped at the last 10 valid items. `knowledge_mode` is an explicit application control, not a value inferred from planner reasoning exposed to the model. A legacy finalizer without `respond(...)` fails closed rather than bypassing this boundary through `answer(...)`.
17. In `kb_grounded` mode, the final model selects relevant ready evidence and writes natural customer prose; it does not mechanically concatenate facts.
18. Channel-specific transport workers must reuse the same application runtime rather than creating a second support agent.
19. VK transport-level manual-admin intervention must silence auto-replies for 1 hour from the last unmatched `message_reply`.
20. Transport-level override must be re-checked immediately before a VK reply is sent.
21. Planner actions are capability-bound: `use_tool` is valid only for an explicitly advertised runtime capability. A request outside that set is rejected before tool execution as internal `planner_action_rejected`, never customer-facing `cannot_answer` by itself.
22. A defensive runtime capability mismatch (`tool_unavailable`) must immediately advance to KB/finalization; neither it nor a rejected planner action may repeat `use_tool` or consume another bounded-loop iteration.
23. The production simple runtime must never load the whole compiled corpus into the answering prompt. It must start from the compact OKF catalog and pass only selected full pages to the KB agent.
24. `simple_llm_wiki` must require coverage review at its call boundary; a stale `KB_AGENT_SKIP_COVERAGE_REVIEW=true` setting must not weaken this production invariant.
25. The first customer-visible response, including `cannot_answer`, must begin with exactly one `Здравствуйте!`.

## Decision loop

Per inbound turn:
1. classify social vs substantive turn
2. finalize a planner-approved `social_reply` without KB lookup or final answer-model generation; only a first-turn `out_of_scope` may finalize directly
3. force a KB read before accepting `out_of_scope` on a contextual follow-up
4. for substantive in-domain turns, assess the next action
5. gather runtime tool observations when needed and run semantic Wiki navigation for substantive domain facts
6. never answer a substantive business question from the system prompt
7. run the dedicated KB agent over the compiled wiki material already gathered when KB is needed
8. finalize through the customer-output boundary with only the relevant evidence
9. emit one of the allowed outcomes

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

- Every OpenAI-compatible call (direct, KB-agent, and summary) must use one bounded shared recovery contour: transient network/upstream/ordinary-`429` error → `Retry-After` or exponential backoff retry on the active slot → cooldown after that slot's retry budget is exhausted → failover to the next eligible slot.
- Mistral/openai-compatible roles support ordered multi-key pools via `DIRECT_LLM_API_KEYS`, `KB_AGENT_API_KEYS`, and `SUMMARY_LLM_API_KEYS` with backward-compatible single-key aliases. Auth and explicit quota/credit/billing exhaustion can fail over immediately; all key transitions are non-secret trace events.
- After complete recovery exhaustion, the customer-facing runtime must record `retry_pending` with empty text and perform no outbound delivery. It must never expose a provider exception, raw KB material, or `cannot_answer` caused by LLM API failure. `cannot_answer` remains reserved for genuinely insufficient grounding.
- The runtime must expose non-secret failover observability: warning logs on key-slot switches plus `api_key_index` / `used_failover` / `failover_count` / `failover_events` (reason class and `Retry-After` where supplied) in LLM trace metadata.
- The KB agent is the stronger reasoning step and should be allowed to use the stronger model tier than the final answering step.
- If final answer generation fails after provider recovery, return `retry_pending` with empty customer text and preserve gathered evidence only in trace state for retry.
- Deterministic application code must not generate business answers from retrieved facts, question words, or one-off branches.
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
- provider `retry_pending` outcomes are persisted on the raw transport event with bounded retry metadata; the worker recovers due retries after restart without duplicating the customer turn or outbound delivery

### VK
- worker polls VK Bots Long Poll API
- `message_new` is the inbound customer message event
- `message_reply` is the observed outgoing community activity event
- direct messages to the community are in scope for v1
- group chats / besedy and history backfill are out of scope for v1
- unmatched `message_reply` is persisted in the dialog as a human/operator outbound message and activates a 1-hour human override window; bot sends must first commit an outbound reconciliation row in `pending` state using the generated `random_id`, so an early bot echo is matched rather than misclassified as an operator reply
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

## Versioned prompt contract

The runtime contract is built from reviewed source under `deploy/profile-source/` and compiled into `deploy/runtime-profile/`:
- `SYSTEM_PROMPT.md` — generic customer-facing role, evidence boundary, dialogue behavior, and output rules; no business facts or scenarios
- `KB_AGENT_PROMPT.md` — generic semantic page selection, coverage review, and grounded extraction rules; no business-topic special cases
- `kb/` — the only customer-facing business-fact source

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
