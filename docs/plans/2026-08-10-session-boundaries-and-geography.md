# Session boundary and geography prompt implementation plan

> **For Hermes:** implement task-by-task with tests before runtime code.

**Goal:** Start a fresh support case on a local-day change or a two-hour inactivity gap, and clarify the geography instruction in the external system prompt.

**Architecture:** Case selection remains in `app/services/case_resolution.py`. It will compare the latest persisted message timestamp in the latest case with the new inbound timestamp in `Asia/Yekaterinburg`; if either boundary is crossed, it creates a new case. Geography remains external policy in `SYSTEM_PROMPT.md`; no city is hardcoded into application code.

**Tech stack:** Python 3.11, SQLAlchemy, pytest, Docker Compose.

---

### Task 1: Define and test case-boundary behavior

**Files:**
- Modify: `tests/test_inbound_message.py`
- Modify: `app/services/case_resolution.py`

1. Add tests for next-local-day, more-than-two-hour, and within-two-hours handling through `RoutingService`.
2. Run the three tests and confirm they fail under current last-case reuse behavior.
3. Add a small case-selection helper that reads the latest message time and creates a fresh case when either approved boundary is crossed.
4. Re-run the targeted tests.

### Task 2: Clarify the external geography policy

**Files:**
- Modify: `/home/tian/support-agent-profiles/parachute/SYSTEM_PROMPT.md`
- Test: `tests/test_inbound_message.py`

1. Add a prompt-content regression assertion for the approved concise rule.
2. Run it and confirm it fails before the prompt change.
3. Amend the existing geography section: distinguish the service-delivery location from the customer's city; do not infer an additional service city from customer travel. State the one authorized service location without enumerating other cities.
4. Re-run the assertion.

### Task 3: Verify target runtime

**Files:** no further source changes.

1. Run targeted tests, then the relevant broader test module.
2. Build the changed application images and recreate the application-plane services only.
3. Verify `/health`, image/container state, and an internal probe whose same-chat second message is separated by the session boundary.
4. Record the exact evidence in the final report.
