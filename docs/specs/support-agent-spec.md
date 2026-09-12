# Support Agent Runtime Spec

## Current runtime — one-call full-corpus mode

> Current production configuration is `ANSWER_ENGINE_MODE=simple_full_corpus_natural`. One model call receives the literal customer turn, up to ten prior `user|assistant` messages, deterministic calendar observations where applicable, and the complete mounted compiled Wiki. It returns the customer text directly; no selector, native Wiki call, KB navigation/extraction, or second finalizer is in the active path.

Customer language is controlled by the external `SIMPLE_ANSWER_PROMPT.md`; factual language is controlled by authored KB pages compiled into the mounted Wiki. The output boundary adds the first-response greeting and removes provider metadata (`source_refs`, `evidence`, tool fields) and Markdown code delimiters. It never composes business content.

The OpenAI-compatible payload explicitly sets `think: false`. This keeps the one-call path bounded and avoids provider-default reasoning latency. JSON replies retain source/evidence validation; provider failure remains a textless `retry_pending`, never `cannot_answer`.

The active profile has been checked by a ten-question transport-free corpus replay: every turn returned customer text in one short provider call without calendar misclassification, empty replies, `retry_pending`, or customer-channel delivery. The remaining sections that describe native tools, selective Wiki navigation, coverage, and a common finalizer are historical/rollback contracts and do not govern this active mode.

It is not a ticket router and not an escalation-first bot.

## Main invariants

1. The agent must stay within the domain represented by the active factual Wiki.
2. The system prompt is a generic behavior contract and is never a business-fact source; substantive answers must use grounded KB and runtime-tool evidence.
3. Substantive KB reasoning must pass through a dedicated KB agent that reads the compiled wiki selectively rather than treating lexical snippets as the final reasoning surface.
4. The customer-facing support agent and the KB agent must use separate external prompt files.
5. The agent must not fabricate facts when KB/tool evidence is missing.
6. The existing customer model selects 0–2 sequential native tools by meaning; Wiki and calendar are each allowed at most once. A selector finalization decision contains no customer draft. One common writer handles Wiki, calendar, their combination and pure social replies. There is no separate classifier, keyword router or forced retrieval path. Wiki is the only business-fact source.
7. Only the common writer creates customer outcomes. Selector intent, a retrieval query, coverage, and dialogue do not prove business facts or determine that the literal customer question has been answered.
8. Every customer-visible terminal route passes through one output boundary: first replies begin with exactly one `Здравствуйте!`. The boundary normalizes formatting but never replaces a model-written customer reply with an application template; an invalid or missing final-model payload is `retry_pending` with empty customer text.
9. Acquisition success and semantic coverage are distinct. Clear unknown questions lead to natural `cannot_answer`, and clarification is limited to genuine customer-resolvable ambiguity. `not_found` is not a technical retry; related facts, candidate basis, coverage verdicts, or a retrieval query do not automatically promote it to `ready` or justify an answer.
10. If the first request is outside the domain, the final outcome must be `out_of_scope`.
11. If a critical ambiguity blocks a safe answer, the final outcome must be `clarification_requested`.
12. The main runtime flow must not silently switch into human or Hermes escalation.
13. Exhausted LLM-provider recovery must produce `retry_pending` with empty customer text; application code must not synthesize a domain answer.
14. Broad and contextual language is interpreted semantically from dialogue plus the complete compact Wiki catalog, not by application keyword lists or question-specific branches.
15. Acquisition ready does not prove direct coverage. Runtime validates factual packets and passes only compact cited facts to the finalizer; the finalizer alone selects facts directly relevant to the literal question and writes the customer outcome. Candidate basis, coverage verdicts, retrieval query/scope, raw pages, and internal reasons remain audit-only.
16. The customer-facing final model receives a newly constructed allowlisted packet, never the broad runtime context: no planner action/reason, route/loop/audit metadata, KB navigation/review reasons, raw KB pages, raw tool payloads, case identifiers, duplicate dialogue forms, or dialogue roles outside `user|assistant` may enter its request. The dialogue projection is capped at the last 10 valid items. `knowledge_mode` is an explicit application control, not a value inferred from planner reasoning exposed to the model. A legacy finalizer without `respond(...)` fails closed rather than bypassing this boundary through `answer(...)`.
17. In `kb_grounded` mode, the final model selects relevant ready evidence and writes natural customer prose; it does not mechanically concatenate facts. When a confirmed current source is itself the direct answer to a requested dynamic value, the writer treats that source as an `answer`, gives only the source-needed response, and does not add related service, payment, contact, publication-process, or lack-of-value assertions unless they directly answer the literal question.
18. Channel-specific transport workers must reuse the same application runtime rather than creating a second support agent.
19. VK transport-level manual-admin intervention must silence auto-replies for 1 hour from the last unmatched `message_reply` and terminally suppress unfinished earlier `message_new` transport events for that peer. A historical inbound event must not remain in `received` after an operator reply has intentionally taken over the conversation.
20. Every accepted VK `message_new` must be durably stored as its own customer message before queueing. Queue coalescing may combine only the routing input; manual override or stale-generation suppression must never remove the original inbound messages from Viewer. VK Market attachments may enrich only the routing input: the agent receives a normalized attachment context with market title, description, price text, and stable card identifiers, while persisted Viewer/customer history retains the literal customer text. Immediately before a VK reply is journaled and sent, the worker must re-check both transport-level override and whether the combined generation's newest external message ID still equals the durable latest-inbound marker for that conversation. A newer persisted inbound suppresses the obsolete generation.
21. Native selector actions are capability-bound. Unregistered names, invalid arguments and invalid native IDs are technical failures before tool execution, never customer-facing `cannot_answer` by themselves.
22. Tool unavailability stops collection with a textless technical outcome; it does not advance to a customer answer or request the failing tool again in the same turn.
23. The production simple runtime must never load the whole compiled corpus into the answering prompt. It must start from the compact OKF catalog and pass only selected full pages to the KB agent.
24. `simple_llm_wiki` must require coverage review at its call boundary; a stale `KB_AGENT_SKIP_COVERAGE_REVIEW=true` setting must not weaken this production invariant.
25. The first customer-visible response, including `cannot_answer`, must begin with exactly one `Здравствуйте!`.

## Decision loop

Per inbound turn (stage 4 candidate, not an implicit production release):
1. The existing model boundary selects native `wiki_lookup(query, context_scope, needed_fact)` or `calendar_lookup(date_expressions, requested_calendar_fact)`, or returns only `action=finalize`, a validated `response_intent`, and a short internal reason. `date_expressions` is an ordered list of every explicit date fragment in the current turn.
2. Collection does not create a customer draft. Pure social turns can finalize without a tool.
3. Validate each exact request and linked native ID before execution. Allow at most two sequential tool actions, each registered tool at most once; support both orders and either single-tool path.
4. Preserve native pairs internally and keep the first continuation native. After the second successful tool, validate both pairs against the execution records and replace only that selector request with system + user `selector-terminal/v1` data. The typed `tool_exchanges` retain exact IDs, names, arguments and observations. No extra model request or synthesized finalize is permitted; malformed history fails before HTTP, malformed terminal JSON/XML is textless retry.
5. On finalize, routing constructs an allowlisted packet and invokes the existing common `respond` exactly once. The finalizer receives the literal current message, bounded `user|assistant` dialogue, compact cited Wiki facts, and separate deterministic calendar facts. Business facts remain Wiki-only; navigation reasons, raw pages, retrieval requests, candidate basis, coverage, and audit metadata are not finalizer evidence.
6. Invalid selector output, tool budget/schema/ID violations and unavailable tools produce textless `retry_pending` without finalization, assistant persistence or send. Successful `not_found` remains a semantic outcome for the writer.
7. Validate/format the writer output through the shared output boundary and preserve the exact reason. Audit records whether the writer actually ran and associates tool actions with their native selector decisions.
8. Acceptance requires isolated real-provider replay of both native orders on the same effective provider/model/retry configuration as production, in addition to bounded regression and full-suite tests. Stub success or HTTP availability alone is not acceptance. No production deploy is implied.

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
- provider `retry_pending` outcomes remain durable raw transport events and are retried every 5 seconds until successful processing, a newer inbound suppresses them, or a human explicitly takes over; restart recovery must not duplicate the customer turn or outbound delivery

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
