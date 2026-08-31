# Support Agent Architecture Overview

## Current production contract

Production runs with `ANSWER_ENGINE_MODE=agent_tool_loop`. It is one LLM-managed customer turn with an optional bounded Wiki tool, not a keyword router, a separate LLM classifier, or an application-forced retrieval path.

```text
Telegram / VK inbound event
  → raw-event journal + channel queue + stale/override guards
  → RoutingService: case resolution + bounded dialogue context + runtime date facts
  → DirectLLMService customer turn (native wiki_lookup available, tool_choice=auto)
      ├─ pure social turn → direct non-factual response
      └─ substantive turn → LLM calls wiki_lookup
           → compact Wiki catalog → KB agent semantic navigation / selective reads / coverage review
           → grounding_status=ready + bounded evidence
           → common finalizer
  → PolicyService output boundary
  → durable message persistence + transport delivery
```

## Core boundaries

- **LLM chooses the tool action.** Application code does not classify business topics, use keyword routes, or unconditionally query Wiki.
- **Wiki is the only business-fact source.** The system prompt is generic and contains no profile facts. For a substantive turn, the customer model must call `wiki_lookup` before returning customer facts, procedures, contacts, promises, or domain-specific clarification/out-of-scope text.
- **Social exception is narrow.** A short pure social message without a request or continuation of a customer situation may return a non-factual `social_reply` without Wiki.
- **Runtime tools are factual sources for their own domain only.** The date tool supplies calendar facts; it does not infer business schedules.
- **Only ready grounding crosses the finalizer seam.** `grounding_status=ready` may provide `answer_basis` and grounded facts. `not_found` text, page-selection rationale, source availability, and other non-ready extraction text are operator telemetry, not customer evidence.
- **Technical LLM/Wiki failure is fail-closed.** It creates `retry_pending` with empty customer text and is retried by the transport worker. It is never converted into a guessed domain answer.
- **The first-reply greeting is deterministic.** `PolicyService.finalize_simple_customer_text()` adds exactly one `Здравствуйте!` to the first non-empty customer-visible reply. It adds none to later replies and does not duplicate a model-provided greeting.

## Native tool compatibility

The provider-facing adapter treats every structurally recognizable native function-call object as `wiki_lookup` during the one-tool customer turn. This is deliberately schema-based rather than string-pattern-based: only `wiki_lookup` is registered for that turn, so a native call is the model's tool decision even if an OpenAI-compatible provider corrupts the name, arguments, or appends serialized answer text. Regular JSON envelopes are parsed from their first valid object when a provider appends junk. This protocol normalization does not choose whether Wiki is needed and does not synthesize business facts.

## Main runtime components

- **`TelegramGatewayService` / polling worker** — normalizes Bot API updates, journals events, coalesces turns, retries durable `retry_pending` events, and sends replies.
- **`VKGatewayService` / VK worker** — equivalent shared runtime integration plus outbound reconciliation and a one-hour manual-operator override.
- **`RoutingService`** — case resolution, context construction, common evidence boundary, finalization, outcomes, and audit events.
- **`UnifiedTurnService`** — executes the LLM-selected action: direct non-factual final result or bounded Wiki lookup.
- **`WikiLookupTool` + `KBAgentService`** — compact-catalog navigation, selective page reads, coverage review, and grounded extraction.
- **`DirectLLMService`** — customer tool decision and evidence-bounded final response using separate calls; records non-secret LLM traces.
- **`PolicyService`** — profile-backed no-answer policy evidence and the deterministic output boundary.
- **PostgreSQL** — system of record for conversations, cases, messages, workflow events, transport events, transport state, and VK outbound reconciliation.
- **Internal probe API** — invokes the real routing path with `is_test` records and never publishes to Telegram or VK; use it for literal no-send replays.

## Outcomes

- `answer` — grounded facts and/or runtime tool facts support a response.
- `cannot_answer` — in-domain but insufficient confirmed evidence; the common finalizer may use allowlisted profile policy evidence as a natural next step.
- `out_of_scope` — outside the active profile domain.
- `clarification_requested` — a short clarification is required before a grounded answer.
- `retry_pending` — technical failure only; empty customer text and durable transport retry.

## Release boundary

`deploy/profile-source/` is reviewed source. `scripts/build_runtime_profile.py` validates it into `deploy/runtime-profile/`; the exact generated tree is atomically promoted to the external runtime profile before `python3 scripts/release.py` recreates the full application plane (`app`, `worker`, `vk-worker`, `viewer-web`, `viewer-push-worker`). PostgreSQL is not recreated.

The release script verifies profile parity, image revision labels, Python source manifests, health/liveness, and controlled fail-closed probes. A release is complete only after `RELEASE SUCCESS` and a non-secret receipt under the runtime root.

See also:
- `docs/specs/support-agent-spec.md` — detailed behavior contract.
- `docs/operations/production-releases.md` — release and rollback procedure.
