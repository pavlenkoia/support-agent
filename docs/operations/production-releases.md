# Production releases

## Supported command

Run production releases only from a clean, committed checkout:

```bash
python3 scripts/release.py
```

`deploy/profile/` is generated from the sole authored authority `deploy/profile-source/` using `scripts/build_runtime_profile.py`. Containers mount it read-only at `/data/profile`. Do not independently edit generated KB. Both source and the already-tracked runtime bundle are synchronized in Git during landing.

## KB-only maintenance (no application rollout)

The active one-call `simple_full_corpus_natural` engine uses the whole compiled corpus and `SIMPLE_ANSWER_PROMPT.md`. Nine compact fact pages cover certificates, equipment, flights, jumps, location-office, payments, photo-video, schedule and shared-restrictions. Preserve subjects and conditions. `raw/fact-view-provenance/coverage-ledger.json` is an immutable migration record, not a second editable knowledge authority.

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

The command verifies the mounted profile against this same tracked directory, obtains the full Git SHA, builds release-tagged images for `app`, `worker`, `vk-worker`, `viewer-web`, and `viewer-push-worker`, and force-recreates exactly those services. PostgreSQL is not recreated. The polling-liveness gate allows up to 150 seconds for an initial Telegram Long Poll plus startup.

A release is successful only when the command exits with `RELEASE SUCCESS` and writes a JSON receipt beneath `${SUPPORT_AGENT_RUNTIME_ROOT_HOST:-/home/tian/support-agent-runtime}/releases/`.

## Hard verification gate

The command fails when the single tracked/mounted profile tree is empty or inconsistent, or when any application-plane service is missing, not running, was not recreated for the operation, has a mismatched OCI revision label, or (for Python runtime services) has a mismatched complete `app/**/*.py` source manifest. It additionally requires backend health, Telegram/VK polling liveness, and a controlled final-model failure probe in `app`, `worker`, and `vk-worker`; that probe must return `retry_pending` with empty customer text.

`docker compose ps`, a successful build, and a standalone `/health` response are insufficient evidence of a completed production release.

## Rollback

Use a previous receipt only when its `status` is `success`:

```bash
python3 scripts/release.py --rollback-receipt /external/runtime/root/releases/<successful-receipt>.json
```

Rollback selects that immutable image release ID, force-recreates the same application-plane services, and applies the same verification gate. It does not modify PostgreSQL, volumes, or runtime secrets.

## Manual Compose boundary

`docker compose` remains suitable for local development and diagnosis. It is not a supported production deployment interface because selecting an individual service can create release skew between the HTTP app and traffic-serving workers.
