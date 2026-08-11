# Simple Answer Engine Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Follow strict RED-GREEN, two-stage review (spec compliance, then code quality), Docker-first verification, and do not touch production without Igor's explicit approval.

**Goal:** Replace the active planner/KB-agent/finalizer chain with one full-corpus, KB-grounded answer engine while preserving transports, persistence, human override and operator observability.

**Architecture:** A new `SimpleAnswerEngine` receives a minimal external prompt, bounded role-labelled history, the complete compiled profile KB, and optional deterministic DateTime observations. It performs one logical provider call and returns a validated `{kind, response_text, source_refs}` envelope. A temporary rollout flag keeps legacy runtime available only until isolated parity proof and an explicitly approved cutover.

**Canonical spec:** `docs/specs/simple-answer-engine.md`

**Tech stack:** Python 3.11, FastAPI service layer, SQLAlchemy/PostgreSQL, OpenAI-compatible client, pytest, Docker Compose, internal probe API, Graphify.

---

## Safety and scope gates

- Do not modify the mounted production profile or production env during Tasks 1–8.
- Use fixture/copy profile data in tests and the isolated production-parity contour.
- Do not deploy, restart or recreate production services without a separate explicit approval from Igor.
- Do not delete legacy files in the first rollout slice.
- Do not add vector search, another agent, another model role, domain keyword routing, or business facts in Python.
- Keep changes uncommitted until focused and full test gates pass; commit before any approved release.

### Task 1: Lock the new contract with failing tests

**Objective:** Express the one-call and strict-grounding contract before implementation.

**Files:**
- Create: `tests/test_simple_answer_engine.py`
- Create: `tests/fixtures/simple_answer_profile/SYSTEM_PROMPT.md`
- Create: `tests/fixtures/simple_answer_profile/kb/index/catalog.json`
- Create: minimal compiled fixture pages under `tests/fixtures/simple_answer_profile/kb/compiled/`

**Steps:**

1. Add a capturing fake LLM client.
2. Add RED tests for:
   - factual answer with valid `source_ref`;
   - missing/unknown source rejection;
   - greeting;
   - greeting plus factual question;
   - clarification, cannot-answer and out-of-scope envelopes;
   - poisoned internal-context exclusion;
   - corpus-size overflow;
   - provider exhaustion → `retry_pending`.
3. Assert exactly one `generate()` call per completed test turn.
4. Run:

```bash
docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/support-agent-venv \
  -v "$PWD:/workspace" -w /workspace \
  support-agent-app:dev \
  uv run --group dev pytest -q tests/test_simple_answer_engine.py
```

**Expected:** RED because `SimpleAnswerEngine` does not exist.

### Task 2: Implement corpus loading and bounded input projection

**Objective:** Build a deterministic, all-or-nothing input packet from the external profile.

**Files:**
- Create: `app/services/simple_answer_engine.py`
- Modify: `tests/test_simple_answer_engine.py`

**Steps:**

1. Implement a small compiled-corpus loader that reads catalog plus every declared compiled page.
2. Preserve stable `source_ref`, title/summary where present, and complete page text.
3. Validate:
   - every catalog page exists;
   - duplicate refs are rejected;
   - total chars are counted before the call;
   - over-budget corpus fails closed without truncation.
4. Project at most 10 non-empty `user|assistant` history items.
5. Ensure the current user message is separate and not duplicated.
6. Run focused tests until corpus/projection tests are GREEN.
7. Commit after the task is reviewed:

```bash
git add app/services/simple_answer_engine.py tests/test_simple_answer_engine.py tests/fixtures/simple_answer_profile
git commit -m "feat: add bounded simple answer input contract"
```

### Task 3: Implement one-call response and envelope validation

**Objective:** Produce and validate the final customer outcome without downstream re-decision.

**Files:**
- Modify: `app/services/simple_answer_engine.py`
- Modify: `tests/test_simple_answer_engine.py`

**Steps:**

1. Call the existing injected OpenAI-compatible client exactly once per logical turn.
2. Send only:
   - active minimal prompt;
   - current question;
   - projected dialogue;
   - complete compiled corpus;
   - normalized public tool observations.
3. Parse provider-neutral JSON, including safe fenced/wrapped-object recovery already proven elsewhere in the repo.
4. Validate `kind`, non-empty customer text and source refs.
5. Reject a `grounded_answer` whose refs are empty or absent from the supplied corpus.
6. Return normalized telemetry including logical call count, provider attempts, corpus size, history count and non-secret trace metadata.
7. Do not implement semantic keyword checks or a second verifier model.
8. Run focused tests and confirm one captured call.
9. Commit after review:

```bash
git add app/services/simple_answer_engine.py tests/test_simple_answer_engine.py
git commit -m "feat: implement one-call grounded answer engine"
```

### Task 4: Reuse deterministic DateTime observations without an agent loop

**Objective:** Preserve current-date correctness while removing model-selected tool iterations.

**Files:**
- Modify: `app/services/tool_runtime.py` only if a narrow public precompute API is missing
- Modify: `app/services/simple_answer_engine.py`
- Modify: `tests/test_tool_runtime.py`
- Modify: `tests/test_simple_answer_engine.py`

**Steps:**

1. Add RED tests for a concrete date, relative date, month/range and a non-date question.
2. Reuse existing deterministic parsers/calculators; do not duplicate date rules.
3. Produce only approved customer-relevant observations before the model call.
4. Confirm no tool observation is produced for unrelated questions.
5. Confirm exact dates remain hidden when the existing product contract prohibits them.
6. Run:

```bash
uv run pytest -q tests/test_tool_runtime.py tests/test_simple_answer_engine.py
```

7. Commit after review.

### Task 5: Add the temporary application wiring flag

**Objective:** Route a turn through either legacy or simple core without changing transport contracts.

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/services/routing.py`
- Modify: `tests/test_routing.py`
- Modify: `tests/test_orchestrator_llm_trace_summary.py` as required by the new mode boundary

**Steps:**

1. Add `ANSWER_ENGINE_MODE=legacy|simple_full_corpus`, default `legacy`.
2. Inject `SimpleAnswerEngine` at the same application boundary where `RoutingService` currently invokes orchestration.
3. In simple mode:
   - resolve case and bounded history;
   - precompute DateTime observations;
   - invoke `SimpleAnswerEngine` once;
   - normalize to the existing outcome persistence shape;
   - emit explicit `answer_engine=simple_full_corpus` telemetry.
4. Prove with spies that simple mode does not call:
   - `OrchestratorService.run`;
   - `KBAgentService.read`;
   - `SummaryService.summarize_case`;
   - legacy `DirectLLMService.assess_request/respond/answer`.
5. Keep legacy mode behavior unchanged.
6. Run focused routing tests and commit after review.

### Task 6: Reduce policy to sanitization and greeting in simple mode

**Objective:** Prevent downstream route/fact mutation while preserving customer-output safety.

**Files:**
- Modify: `app/services/policy.py`
- Modify: `app/services/outcome.py` or `app/services/outcome_service.py` only where needed
- Modify: existing policy/outcome tests
- Modify: `tests/test_simple_answer_engine.py`

**Steps:**

1. Add RED tests proving simple-mode policy cannot change `kind` or replace supported business content.
2. Preserve only:
   - internal-marker sanitization;
   - empty/invalid text fail-closed behavior;
   - exactly one first-reply greeting;
   - existing customer-safe terminal fallback wording.
3. Keep legacy policy path untouched until cutover.
4. Run focused policy/outcome tests and commit after review.

### Task 7: Prove transport and probe parity

**Objective:** Ensure every ingress reaches the same simple application core.

**Files:**
- Modify: `tests/test_telegram_gateway.py`
- Modify: `tests/test_vk_gateway.py`
- Modify: probe tests (`tests/test_probe_service.py` or current equivalent)
- Modify: `tests/test_inbound_queue.py` only if assertions need the new outcome metadata

**Steps:**

1. Inject a recording `SimpleAnswerEngine` into Telegram, VK, HTTP inbound and probe paths.
2. Assert equal normalized application input for the same dialogue: current turn, history, KB identity and tool observations.
3. Preserve coalescing, stale-generation suppression, human override and outbound persistence tests.
4. Assert the current user turn is not duplicated in probe history.
5. Assert one combined user and one assistant message are stored only for an actual current delivery.
6. Run the focused transport/probe suite and commit after review.

### Task 8: Build a minimal candidate profile prompt and isolated replay suite

**Objective:** Prove the architecture against the real profile corpus without changing production files.

**Files:**
- Create: `tests/fixtures/simple_answer_profile/README.md` documenting fixture intent
- Create or modify: isolated replay helper under `tools/` only if existing probe tooling cannot capture the required fields
- Do not modify: `/home/tian/support-agent-profiles/parachute/SYSTEM_PROMPT.md`

**Steps:**

1. Create a temporary candidate copy of the external profile outside the repository/runtime production root.
2. Replace only the candidate `SYSTEM_PROMPT.md` with the minimal contract from the spec.
3. Keep candidate compiled KB byte-identical to the active corpus.
4. Switch only the isolated production-parity runtime to `ANSWER_ENGINE_MODE=simple_full_corpus` and the candidate profile root.
5. Verify effective non-secret provider/model settings match production.
6. Run the literal replay matrix from the spec through `/api/internal/probe` with no customer transport.
7. For every scenario capture:
   - literal user input and answer;
   - `kind` and `source_refs`;
   - `logical_llm_call_count`;
   - provider attempts/failover;
   - absence of planner/navigation/extraction/finalizer steps.
8. Stop and report the first unmet acceptance criterion; do not tune production prompt or KB.

### Task 9: Full checkout verification and documentation synchronization

**Objective:** Make the candidate implementation reviewable and reproducible before any rollout decision.

**Files:**
- Modify: `docs/specs/support-agent-spec.md`
- Modify: `docs/architecture/overview.md`
- Modify: `README.md`
- Keep canonical: `docs/specs/simple-answer-engine.md`
- Keep plan: `docs/plans/2026-08-11-simple-answer-engine.md`

**Steps:**

1. Update shipped docs to distinguish current legacy default from candidate simple mode.
2. Run focused tests.
3. Run the full suite in Docker:

```bash
docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/support-agent-venv \
  -v "$PWD:/workspace" -w /workspace \
  support-agent-app:dev \
  uv run --group dev pytest -q
```

4. Run lint and structural checks:

```bash
docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/support-agent-venv \
  -v "$PWD:/workspace" -w /workspace \
  support-agent-app:dev \
  uv run --group dev ruff check .
git diff --check
graphify update .
```

5. Review changed-file scope and confirm no secret/runtime env values are present.
6. Commit the clean candidate implementation and docs.
7. Do not push or deploy until Igor approves the isolated evidence.

### Task 10: Approval-gated production cutover

**Objective:** Activate the proven simple core with a consistent immutable release.

**Prerequisite:** explicit Igor approval after reviewing Task 8 evidence.

**Steps:**

1. Back up production env and active `SYSTEM_PROMPT.md` to restricted external runtime storage.
2. Install the reviewed minimal prompt in the external production profile; do not alter compiled KB content.
3. Set `ANSWER_ENGINE_MODE=simple_full_corpus` in production env.
4. Use the repository production release gate to build/recreate every application-plane executor from one clean commit; do not recreate PostgreSQL.
5. Verify:
   - one immutable release ID across `app`, `worker`, `vk-worker` and other application-plane executors;
   - `/health`;
   - migrations/schema state;
   - worker polling/long-poll liveness;
   - effective answer-engine mode;
   - runtime hashes/manifest.
6. Run a short transport-free production probe set: greeting, factual KB answer, date/tool answer, absent fact and out-of-scope.
7. Display literal transcripts and traces.
8. Roll back prompt, env and application release together if any acceptance gate fails.
9. Push only the intended reviewed branch after successful proof and explicit request.

### Task 11: Separate cleanup proposal after stability window

**Objective:** Prevent permanent dual architecture without unsafe deletion during cutover.

**Not part of the first rollout.**

1. After an agreed stability window, inventory unreachable legacy owners:
   - `OrchestratorService` planner loop;
   - KB-agent runtime path;
   - summary LLM path;
   - legacy DirectLLM planner/wiki/finalizer helpers;
   - prompt-only route;
   - simple-mode policy compatibility branches.
2. Prepare an exact deletion list and dependency graph.
3. Obtain explicit Igor confirmation for file/code deletion scope.
4. Remove legacy mode and temporary feature flag with RED-GREEN tests.
5. Re-run full Docker tests, Graphify, isolated replay and the production release gate.
