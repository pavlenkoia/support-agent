# Production Release Gate Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make every production support-agent deployment reproducible, release-identifiable, and rejected when any app-plane container runs a different release.

**Architecture:** `scripts/release.py` owns the production operation. It derives an immutable Git SHA release ID, passes it into Compose image builds, force-recreates all five application-plane services while excluding PostgreSQL, verifies runtime evidence, and writes a non-secret receipt under the external runtime root. Pure verification helpers are testable without Docker; Docker integration is exercised against the running stack.

**Tech Stack:** Python 3.11 stdlib, Docker Compose v2, OCI image labels, pytest.

---

### Task 1: Define tested release contract

**Objective:** Introduce a pure, test-first deployment contract that identifies the required service set and rejects mismatched evidence.

**Files:**
- Create: `tests/test_release.py`
- Create: `scripts/release.py`

**Steps:** Write failing tests for required services, release-label mismatch, stale start time, runtime-manifest mismatch, and receipt success criteria. Implement only pure helpers until those tests pass.

### Task 2: Add immutable image metadata

**Objective:** Ensure each application image carries the Git release ID it was built from.

**Files:**
- Modify: `docker-compose.yml`
- Modify: `deploy/compose/app.Dockerfile`
- Modify: `deploy/compose/worker.Dockerfile`
- Modify: `deploy/compose/viewer-web.Dockerfile`
- Extend: `tests/test_release.py`

**Steps:** Add `SUPPORT_AGENT_RELEASE_ID` build args, deterministic release-tagged image names, and `org.opencontainers.image.revision` labels. Run the release tests and build all images.

### Task 3: Implement release and verification command

**Objective:** Build/recreate all runtime services and hard-fail on any incomplete deployment.

**Files:**
- Extend: `scripts/release.py`
- Extend: `tests/test_release.py`

**Steps:** Implement clean-checkout preflight, Compose validation, all-service build, force-recreate without DB, container/image/manifest/health/log/fallback checks, and external JSON receipts. Include a rollback command that accepts a known receipt/release ID and invokes the same gate.

### Task 4: Document the operator contract

**Objective:** Prevent a return to manual partial Compose rollouts.

**Files:**
- Modify: `README.md`
- Create/modify: `docs/operations/production-releases.md`
- Update: sysadmin wiki runbook `runbooks/support-agent-production-release-gate.md`

**Steps:** Document the only supported release and rollback commands, the exclusion of DB, receipt location, failure semantics, and evidence required before stating that production is updated.

### Task 5: Prove failure detection and production rollout

**Objective:** Demonstrate that the gate catches release skew and then completes a real, uniform rollout.

**Files:**
- No source changes expected.

**Steps:** Run unit tests; use a controlled mismatched-evidence probe that must fail; run the release command on the current committed SHA; inspect the receipt, image labels, start times, source manifests, health, and worker logs; retain the receipt as operational evidence.
