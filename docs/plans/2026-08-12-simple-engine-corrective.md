# Simple Answer Engine — Corrective Plan

> **SUPERSEDED — DO NOT EXECUTE.** This plan only hardened the rejected `simple_full_corpus` candidate and is retained for incident history. The production correction is commit `b086edaa681cdd94bad1740d013d7e46761d6f54`: `simple_llm_wiki` with semantic OKF navigation, selective full-page reads, mandatory coverage review, grounded extraction, and finalization. Canonical contracts are `docs/architecture/llm-wiki-knowledge-contract.md`, `docs/architecture/overview.md`, and `docs/specs/support-agent-spec.md`.

> **Execution rule:** one task per separate implementation session. Never execute the next task automatically. Production, active profile, runtime env and running containers are frozen.

**Goal:** make the existing `simple_full_corpus` candidate fail-closed and auditable without reopening the architecture or patching production in place.

**Base:** `main` at `311e00cfc4bbedfab20572a9904d383636a07025`.

**Out of scope:** deployment, production restart, prompt/KB edits, legacy deletion, retrieval redesign, model change, lint cleanup, UI, viewer, new agents.

## Non-negotiable stop rules

1. Work only on a new candidate branch/worktree from the base commit.
2. Do not edit `/home/tian/support-agent-profiles/parachute`, `/home/tian/support-agent-runtime`, production env or Compose services.
3. Start every task with the named RED test and show its expected failure.
4. End every task with focused tests, full Docker pytest, changed-file Ruff and `git diff --check`.
5. Commit only that task. Stop and report the commit and literal test output.
6. Any failed acceptance check means **stop**. Do not compensate with prompt tuning, another model call, domain keywords or a production experiment.
7. No production cutover task exists in this plan.

---

## Task 1 — Make factual output extractively grounded

**Problem:** current validation proves only that `source_ref` exists; the candidate already answered that night jumps do not occur although the KB contains no such fact.

**Files:**
- Modify: `app/services/simple_answer_engine.py`
- Modify: `tests/test_simple_answer_engine.py`
- Modify: `tests/fixtures/simple_answer_profile/SYSTEM_PROMPT.md`

**RED tests:**

1. A `grounded_answer` with a valid page ref but an invented statement is not delivered.
2. Every evidence item must be `{source_ref, quote}` where `quote` is a non-empty exact substring of that page.
3. Grounded customer text is rendered only from validated evidence quotes; arbitrary model `response_text` cannot add facts.
4. Unknown ref, absent quote, altered quote and more than three evidence items fail to `cannot_answer`.
5. `social_reply`, `clarification_requested`, `cannot_answer` and `out_of_scope` cannot carry evidence.
6. The model is still called exactly once.

**Minimal implementation:**

Extend only the provider-neutral envelope:

```json
{
  "kind": "grounded_answer|social_reply|clarification_requested|cannot_answer|out_of_scope",
  "response_text": "model wording for non-factual outcomes",
  "source_refs": ["compiled/path.md"],
  "evidence": [
    {"source_ref": "compiled/path.md", "quote": "exact text from that page"}
  ]
}
```

For `grounded_answer`, ignore factual prose from model `response_text`; build customer text deterministically from at most three validated exact quotes. Do not add a semantic verifier, keyword classifier or second LLM call.

**Focused gate:**

```bash
uv run pytest -q tests/test_simple_answer_engine.py
```

**Commit:**

```text
fix: require extractive evidence for simple grounded answers
```

**STOP after this commit.**

---

## Task 2 — Remove two deterministic output failures

**Problems:** simple sanitizer can inject the legacy office contact; period observations expose exact dates that the engine later rejects.

**Files:**
- Modify: `app/services/policy.py`
- Modify: `app/services/routing.py`
- Modify: `app/services/simple_answer_engine.py`
- Modify: `app/services/tool_runtime.py` only for a narrow public period projection
- Modify: `tests/test_simple_routing.py`
- Modify: `tests/test_simple_answer_engine.py`
- Modify: `tests/test_tool_runtime.py`

**RED tests:**

1. Internal markers, brackets or empty text in simple mode produce only `render_simple_cannot_answer()`; the result contains no contact, URL, price or prompt-derived fallback.
2. Sanitization never changes `kind`.
3. A month/range/season observation sent to the model contains normalized period metadata but no `weekend_dates` and no exact date list.
4. `Можно прыгнуть в августе?` can return a grounded general schedule answer without `forbidden_exact_dates_for_period`.
5. Concrete single-date weekday observation remains supported.

**Minimal implementation:** introduce a simple-specific finalize method or explicit fallback argument; do not alter legacy behavior. Project period observations to public fields before the one model call instead of sending the raw legacy structure.

**Focused gate:**

```bash
uv run pytest -q tests/test_simple_routing.py tests/test_simple_answer_engine.py tests/test_tool_runtime.py
```

**Commit:**

```text
fix: keep simple output and period observations fail-closed
```

**STOP after this commit.**

---

## Task 3 — Restore Telegram durability and grounding observability

**Problems:** Telegram `retry_pending` has no durable retry owner; persisted simple traces omit `outcome_kind` and `source_refs`.

**Files:**
- Modify: `app/services/telegram_gateway.py`
- Modify: `app/workers/main.py`
- Modify: `app/services/routing.py`
- Modify: `app/services/audit.py`
- Modify: `tests/test_telegram_gateway.py`
- Modify: `tests/test_simple_routing.py`
- Modify: `tests/test_probe_service.py`

**RED tests:**

1. Telegram provider failure persists the raw event as `retry_pending` with bounded `available_at` and attempt count.
2. Worker retry succeeds without a second user message and stores exactly one combined user message plus one assistant message.
3. Restart simulation reconstructs the retry from `transport_events`; no process-local queue state is required.
4. Exhaustion reaches an explicit terminal status and does not spin.
5. A newer inbound revision or human override suppresses stale retry delivery.
6. `response_strategy_selected` and `inbound_processed` persist `outcome_kind`, validated `source_refs`, logical call count and provider attempts.
7. Probe exposes the same fields.

**Minimal implementation:** reuse the existing `TransportEvent` retry fields and the proven VK retry semantics; do not create another queue or generic framework.

**Focused gate:**

```bash
uv run pytest -q tests/test_telegram_gateway.py tests/test_inbound_queue.py tests/test_simple_routing.py tests/test_probe_service.py
```

**Commit:**

```text
fix: persist Telegram retries and simple grounding trace
```

**STOP after this commit.**

---

## Task 4 — Isolated acceptance only

**Files:**
- Create: `docs/reviews/simple-engine-corrective-evidence.md`
- Do not modify application code, production profile, production env or production containers.

**Required gates:**

```bash
docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/support-agent-venv \
  -v "$PWD:/workspace" -w /workspace \
  support-agent-app:dev \
  uv run --group dev pytest -q

docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/support-agent-venv \
  -v "$PWD:/workspace" -w /workspace \
  support-agent-app:dev \
  uv run --group dev ruff check app/services/simple_answer_engine.py app/services/policy.py app/services/routing.py app/services/audit.py app/services/telegram_gateway.py app/workers/main.py tests/test_simple_answer_engine.py tests/test_simple_routing.py tests/test_telegram_gateway.py tests/test_probe_service.py tests/test_tool_runtime.py
git diff --check
graphify update .
```

Run only in the isolated simple candidate contour, with customer transports disabled, the exact matrix:

1. greeting;
2. greeting + price;
3. certificate;
4. payment;
5. broad in-domain opener;
6. contextual follow-up;
7. operational schedule: `Есть ли у вас ночные прыжки?` — must preserve the approved customer response: `Здравствуйте! Прыжки обычно проходят только по выходным. Ночные прыжки не проводятся.`;
8. out-of-scope;
9. `Можно прыгнуть в августе?` — must not fail because of exact-date leakage;
10. forced provider failure followed by durable Telegram retry simulation.

For every turn record literal input/output, `kind`, evidence quotes, `source_refs`, logical call count, provider attempts and persisted trace. Any mismatch fails the task.

**Commit:**

```text
test: record isolated simple engine corrective evidence
```

**STOP. Do not deploy.** Production decision requires a separate Igor request after review of this evidence.
