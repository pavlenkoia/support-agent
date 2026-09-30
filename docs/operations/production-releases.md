# Production releases

## Supported command

Run production releases only from a clean, committed checkout:

```bash
python3 scripts/release.py
```

`deploy/profile/` is generated from the sole authored authority `deploy/profile-source/` using `scripts/build_runtime_profile.py`. Containers mount it read-only at `/data/profile`. Do not independently edit generated KB. Both source and the already-tracked runtime bundle are synchronized in Git during landing.

## KB-only maintenance (no application rollout)

Production currently uses `jev_selected_fact`: Jev selects one fact from the generated corpus; the answer writer receives that fact, its conditions and bounded dialogue. The former `simple_full_corpus_natural` mode remains available but is not selected in production. Nine compact fact pages cover certificates, equipment, flights, jumps, location-office, payments, photo-video, schedule and shared-restrictions. Preserve subjects and conditions. `raw/fact-view-provenance/coverage-ledger.json` is an immutable migration record, not a second editable knowledge authority.

The external governor uses `support_kb_bundle`:
1. `list` returns current pages; `read` returns content and `content_sha256`.
2. `apply` requires `expected_sha256`, checked under the publication lock. Stale edits and overwrites of existing raw documents with different content are rejected.
3. `dry_run=true` compiles/validates without publishing; this is not a completed correction.
4. Normal apply updates the authored page and atomically exchanges only live `kb/` using Linux `renameat2(RENAME_EXCHANGE)`. The old KB is retained as backup. Receipt failure rolls back live KB and authored changes. No non-atomic fallback is used.
5. Read back the page and run internal production no-send probes; inspect literal answers.

This lane never replaces the profile root, restarts customer executors, edits prompts or runs Git. Governor changes remain working-tree changes until a separate scoped landing. Operational backups, locks and receipts must not be committed.

Publisher and governor plugin/SOUL are external infrastructure files, not contained in this application repository. Their operational runbook and backup are maintained by sysadmin. Changed Hermes instructions/tool schemas require a new governor session (`/reset`).

Compact knowledge is an incremental improvement, not a semantic guarantee. Price/flight assumptions, missing conditions and calendar/address confusion remain separate known limitations; do not hide them with canned answers.

The application release procedure below is for code/image changes, not routine KB-only maintenance.

When shipping thinking-safety code, keep `DIRECT_LLM_THINK_ENABLED=false` in production until the proxy/backend is proven to honor the configured completion budget. `DIRECT_LLM_THINK_MAX_OUTPUT_TOKENS` defaults to 1024 and only affects thinking calls (and their single bounded non-thinking fallback after an explicit `length`). A client-side timeout does not prove inference cancellation; do not run unbounded thinking replays through the shared single-slot provider as a release smoke test.

The command verifies the mounted profile against this same tracked directory, obtains the full Git SHA, builds release-tagged images for `app`, `worker`, `vk-worker`, `viewer-web`, and `viewer-push-worker`, and force-recreates exactly those services. PostgreSQL is not recreated. The polling-liveness gate allows up to 150 seconds for an initial Telegram Long Poll plus startup. Check the effective `ANSWER_ENGINE_MODE` in each executor after recreation; the code default alone does not establish production mode.

A release is successful only when the command exits with `RELEASE SUCCESS` and writes a JSON receipt beneath `${SUPPORT_AGENT_RUNTIME_ROOT_HOST:-/home/tian/support-agent-runtime}/releases/`.

## Hard verification gate

The command fails when the single tracked/mounted profile tree is empty or inconsistent, or when any application-plane service is missing, not running, was not recreated for the operation, has a mismatched OCI revision label, or (for Python runtime services) has a mismatched complete `app/**/*.py` source manifest. It additionally requires backend health and Telegram/VK polling liveness. The current release script does **not** run a semantic no-send probe; run it separately through the production internal probe and inspect the persisted workflow events with zero customer transport sends.

`docker compose ps`, a successful build, and a standalone `/health` response are insufficient evidence of a completed production release.

The Jev code release `a9e0fa7` used an exceptional image build based on the preceding production image because the normal build could not fetch an unchanged build dependency. No dependencies or migrations changed: the released images include the committed `app/` tree and the same installed environment; the successful receipt is under the external runtime root. This is a historical build note, **not** a blanket replacement for the normal command. Do not use the base-image method if dependencies, migrations or entry points have changed.

## Rollback

Use a previous receipt only when its `status` is `success`:

```bash
python3 scripts/release.py --rollback-receipt /external/runtime/root/releases/<successful-receipt>.json
```

Rollback selects that immutable image release ID, force-recreates the same application-plane services, and applies the same verification gate. It does not modify PostgreSQL, volumes, or runtime secrets.

## Manual Compose boundary

`docker compose` remains suitable for local development and diagnosis. It is not a supported production deployment interface because selecting an individual service can create release skew between the HTTP app and traffic-serving workers.
