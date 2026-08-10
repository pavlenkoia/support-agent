# Prompt-first final answer implementation plan

> Implement with tests before source changes and verify through the internal live probe.

**Goal:** Make the final customer-facing model use `SYSTEM_PROMPT.md`, the current question, and optional relevant KB/tool evidence; allow prompt-sufficient answers without KB; eliminate mechanical fact joining.

**Architecture:** Extend the bounded-loop planner with `answer_from_prompt`. Route that action directly to `DirectLLMService.respond()` with an explicit no-KB packet. For KB-ready turns, always call the same final response path instead of `_render_ready_grounding()`. Keep grounded fallback only for transport failure and make `answer_basis` its primary input.

---

### Task 1: Add failing contract tests

**Files:**
- Modify: `tests/test_tool_runtime.py`
- Modify: `tests/test_inbound_message.py`

1. Test that the planner accepts `answer_from_prompt` and advertises when it is allowed.
2. Test that ready KB evidence is sent to the final model and unrelated facts are not automatically emitted.
3. Test that an orchestrator `answer_from_prompt` turn performs zero retrieval/KB-agent calls.
4. Run targeted tests and confirm RED.

### Task 2: Implement prompt-first routing

**Files:**
- Modify: `app/services/direct_llm.py`
- Modify: `app/services/orchestrator.py`

1. Add `answer_from_prompt` to planner schema, validation, and rules.
2. Exempt that action from mandatory KB retrieval.
3. Add a prompt-only finalization branch with no KB-agent invocation.
4. Remove the ready-grounding early renderer; use the existing final-model JSON contract.
5. Include `answer_basis`, grounded facts, and tool observations as evidence.
6. Prefer `answer_basis` in transport fallback; keep non-invention boundaries.
7. Run targeted tests until GREEN.

### Task 3: Update old renderer tests and regression suite

**Files:**
- Modify tests that encoded `ready_grounding_rendered` as the normal path.

1. Preserve direct renderer unit coverage only as emergency fallback coverage.
2. Update normal-path expectations to final-model execution.
3. Run the full test suite.
4. Run `graphify update .`.

### Task 4: Release and live verification

1. Commit the scoped implementation.
2. Build and recreate `app`, `worker`, and `vk-worker` only.
3. Verify `/health`, container image state, and mounted profile hash.
4. Replay the exact approved geography question through the internal probe.
5. Require a direct city answer with no certificate-printing detail before reporting success.
