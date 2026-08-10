# Finalization Boundary Implementation Plan

> **For Hermes:** Execute task-by-task with strict RED-GREEN verification and production-backed completion.

**Goal:** Remove all internal planning/reasoning leakage from the customer-facing final model and implement the final allowlisted `prompt_only` / `kb_grounded` contract without removing natural model finalization.

**Architecture:** `OrchestratorService` owns routing and passes an explicit application-only `knowledge_mode`. `DirectLLMService` constructs a fresh allowlisted finalization packet from role-labelled current-case dialogue, compact grounded evidence, and normalized tool summaries. Audit traces remain in workflow telemetry but never enter the final-model request.

**Tech stack:** Python 3.11, FastAPI service layer, pytest, Docker Compose, internal probe API.

---

### Task 1: Lock the payload boundary with failing tests

**Files:**
- Modify: `tests/test_direct_llm_grounding_boundary.py`
- Modify: `tests/test_tool_runtime.py`

1. Add a capturing final client regression with deliberately poisoned runtime context containing `planner_action`, `planner_reason`, route/trace/case/session fields, duplicate dialogue and raw tool data.
2. Assert the serialized final payload contains only current question, projected `{role, content}` dialogue, compact grounding evidence, normalized tool summaries, and explicit `knowledge_mode`.
3. Assert no internal marker or poisoned text appears anywhere in the serialized payload.
4. Update the prompt-only test to call `respond(..., knowledge_mode="prompt_only")` without planner metadata.
5. Run the exact tests and confirm RED because the existing implementation serializes broad `conversation_context` and infers the mode from `planner_action`.

### Task 2: Introduce explicit mode and allowlisted projection

**Files:**
- Modify: `app/services/direct_llm.py`
- Modify: `app/services/orchestrator.py`

1. Add explicit keyword-only `knowledge_mode` to the final response API with validated values `prompt_only|kb_grounded`.
2. Add a small projector for role-labelled dialogue that retains only non-empty `role` and `content` from bounded `recent_messages`, and admits only `user|assistant` roles.
3. Add a small projector for tool observations that retains only customer-relevant `kind` and non-empty `summary`.
4. Build the final-model payload from those projections and compact grounding evidence; never serialize the broad runtime context.
5. Remove planner action/reason from every finalization call. Keep them only in planner/loop telemetry.
6. Pass `knowledge_mode="prompt_only"` on `answer_from_prompt` and `knowledge_mode="kb_grounded"` after ready KB grounding.
7. Run the focused boundary tests until GREEN.

### Task 3: Lock semantic behavior and case 594

**Files:**
- Modify: `tests/test_direct_llm_grounding_boundary.py`
- Modify: `tests/test_tool_runtime.py`

1. Add a provider-independent finalizer test for case 594: ready payment evidence plus the conditional fallback wording in the active system prompt must produce the payment-method answer, not “уточнят при записи”.
2. Assert the finalization rules state that ready evidence satisfies conditional “if not confirmed” fallbacks and must answer the current question first.
3. Preserve regressions that a ready answer cannot be downgraded to clarification/cannot-answer and that unrelated facts are not mechanically emitted.
4. Preserve `prompt_only` zero-KB orchestration coverage.
5. Run focused tests after each slice.

### Task 4: Synchronize specifications and architecture docs

**Files:**
- Create: `docs/specs/final-answer-contract.md`
- Create: `docs/plans/2026-08-10-finalization-boundary.md`
- Modify: `docs/specs/prompt-first-final-answer.md`
- Modify: `docs/specs/support-agent-spec.md`
- Modify: `docs/architecture/overview.md`

1. Make the new final-answer contract canonical.
2. State the allowlist and forbidden internal fields explicitly.
3. Keep prompt-first routing, common final model, optional KB, no keyword hardcodes, and no mechanical fact renderer.
4. Remove or amend wording that permits broad runtime context or internal reasons to reach finalization.

### Task 5: Verify the checkout

1. Run focused tests for direct finalization and orchestration.
2. Run the complete pytest suite in the project Docker test contour.
3. Run `graphify update .`.
4. Review `git diff --check`, changed-file scope, and secret exposure.
5. Confirm no migration/schema/dependency files changed.

### Task 6: Commit, release and prove the production path

1. Run `docker compose config` and complete isolated production-like verification.
2. Commit the scoped code/docs/tests so production images can be tagged with the immutable commit SHA.
3. Build every executor that can run the changed path: `app`, `worker`, `vk-worker`; use the repository release procedure where available.
4. Force-recreate the same application-plane services without restarting PostgreSQL.
5. Verify `/health`, service status, recent error logs, image IDs/start times, and repository/container hashes for every changed runtime file in all three executors.
6. Replay exact case 594 through `/api/internal/probe`, display the literal answer, inspect `knowledge_path`, `grounding_status`, final-model trace, and close the probe session.
7. Replay one prompt-sufficient question and prove `answer_from_prompt` with no KB-agent call; close the session.
8. Push the intended branch only after the production proof succeeds.
