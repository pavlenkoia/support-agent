# Production releases

## Supported command

Run production releases only from a clean, committed checkout:

```bash
python3 scripts/release.py
```

`deploy/profile/` is generated from the sole authored authority `deploy/profile-source/` using `scripts/build_runtime_profile.py`. Containers mount it read-only at `/data/profile`. Do not independently edit generated KB. Both source and the already-tracked runtime bundle are synchronized in Git during landing.

## KB-only maintenance (no application rollout)

Production uses `jev_selected_fact`: Jev evaluates all published facts in one typed batch and passes the selected set with conditions to the answer writer. The answer writer returns plain customer text, not JSON; `no_answer` uses the profile fallback without a writer call. The former `simple_full_corpus_natural` mode remains available but is not selected in production. Nine compact fact pages cover certificates, equipment, flights, jumps, location-office, payments, photo-video, schedule and shared-restrictions. Preserve subjects and conditions. `raw/fact-view-provenance/coverage-ledger.json` is an immutable migration record, not a second editable knowledge authority.

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

For a provider that expects `reasoning_effort` instead of `think`, set `DIRECT_LLM_REASONING_EFFORT=medium` alongside `DIRECT_LLM_THINK_ENABLED=true` in the external protected runtime env. `DIRECT_LLM_JSON_OBJECT_PROMPT_ONLY=true` remains available for structured direct-writer calls through proxies that reject `response_format=json_object`, but the factual Jev writer now requests plain customer text and does not use JSON mode. This changes only the direct answer writer, not Jev or summary; an unset effort preserves the legacy `think` request shape. Back up the prior runtime env and retain the old successful receipt. Rollback of an external provider switch requires restoring the prior env as well as the image when applicable; never print or commit either key.

## Hard verification gate

The command fails when the single tracked/mounted profile tree is empty or inconsistent, or when any application-plane service is missing, not running, was not recreated for the operation, has a mismatched OCI revision label, or (for Python runtime services) has a mismatched complete `app/**/*.py` source manifest. It additionally requires backend health and Telegram/VK polling liveness. The current release script does **not** run a semantic no-send probe; run it separately through the production internal probe and inspect the persisted workflow events with zero customer transport sends.

`docker compose ps`, a successful build, and a standalone `/health` response are insufficient evidence of a completed production release.

The Jev code release `a9e0fa7` used an exceptional image build based on the preceding production image because the normal build could not fetch an unchanged build dependency. No dependencies or migrations changed: the released images include the committed `app/` tree and the same installed environment; the successful receipt is under the external runtime root. This is a historical build note, **not** a blanket replacement for the normal command. Do not use the base-image method if dependencies, migrations or entry points have changed.

## Viewer chronology release

Release `c9e66d0` fixes Viewer ordering when a VK reply and a customer follow-up share the same second. The read-only projection uses uniquely correlated VK timestamps and numeric external IDs; missing or ambiguous evidence retains stored-time fallback. See `docs/api/contracts.md` for correlation rules.

Verification: 247 tests passed against the released application image. The served nginx API rejected anonymous access, accepted the existing login flow, and returned the incident dialogue in VK order. A read-only PostgreSQL check confirmed unchanged history and outbound-send count. No customer replay, model call, prompt change, schema change, or KB promotion was needed. The separate `18d248d` commit only reconciles previously published KB facts into Git; rebuilt core artifacts matched the active profile byte-for-byte.

The successful receipt is `20261019T222504Z-c9e66d02c48e.json` under the external runtime release directory. All five application-plane services run that code release from the canonical `/home/tian/support-agent` Compose checkout; PostgreSQL was not recreated. A later documentation-only landing commit does not require another runtime rollout.

## Selected-fact writer: customer/internal boundary

`JEV_ANSWER_PROMPT.md` requires customer-facing wording about the request, not about internal sources, fact selection, system structure or information processing. Missing information is not proof that a condition or restriction does not exist. When a part cannot be answered, the writer must say so briefly without internal explanations or assumptions. These are model instructions, not a deterministic output filter or a new routing branch.

The approved prompt was checked with 248 passing tests and a nine-turn isolated replay (the incident plus an existing historical scenario set, not a complete maintained questionnaire). The sampled incident answer omitted internal-selection wording but still stated that some requirements were unspecified. A separate greeting/follow-up conflict from a selected KB fact was disclosed and accepted outside this change; KB, selector, social prompt, provider and reasoning settings remain unchanged.

Production release `7745f37` passed the normal five-service release gate, receipt `20261020T160128Z-7745f37bd734.json`. The released application image passed all 248 tests. One closed internal no-send session (`probe_sess_d6f65fdf2e7d`, case 1568) persisted an answer without internal-selection wording; PostgreSQL confirmed `is_test=true` and zero outbound transport sends. Existing telemetry correlates the selected-fact snapshot, processed turn and stored reply, but does not persist the literal writer request. All executor prompt hashes match the approved artifact; KB bytes and writer model/reasoning configuration are unchanged.

For this prompt change, rollback must also restore the previous authored and mounted `JEV_ANSWER_PROMPT.md` before selecting a prior successful image receipt; image rollback alone does not revert the bind-mounted prompt. The previous literal and runtime evidence are retained under the private `checks/case1567-approved-prompt/production-before/` directory beneath the external runtime root. Do not restore a database backup for a prompt rollback.

## Intentional silence for acknowledgements

Release `92eb871` splits the existing Jev turn-type choice into `social_reply` and `social_no_reply` without an extra classifier request. Gratitude, acknowledgement and closing without a new request complete without a writer call, assistant message or transport send. Greetings/emotional reactions retain the dialogue writer; mixed factual requests retain fact selection. Technical errors remain retryable. VK marks no-reply turns terminal with reason `completed_no_reply`, without creating an outbound intent; Telegram marks source updates processed.

The released image passed 256 tests. The normal release receipt `20261021T120541Z-92eb8713dde8.json` confirms all five services, matching source manifests, health and both polling markers; PostgreSQL was not recreated. A six-turn production internal no-send session `probe_sess_6c30411b52b7` (case 1572) confirmed greeting, factual reply, silence for thanks and acknowledgement, emotional reply and a factual question after thanks. The closed session is test-marked, with six user messages, four assistant messages and zero outbound transport sends. Literal results and trace are retained under the external runtime root at `checks/social-no-reply-production/`. KB, mounted prompts and model/reasoning settings are unchanged. Roll back code/images with the previous successful `7745f37` receipt; no database or prompt restore is needed for this code-only change.

## Rollback

Use a previous receipt only when its `status` is `success`:

```bash
python3 scripts/release.py --rollback-receipt /external/runtime/root/releases/<successful-receipt>.json
```

Rollback selects that immutable image release ID, force-recreates the same application-plane services, and applies the same verification gate. It does not modify PostgreSQL, volumes, or runtime secrets.

## Manual Compose boundary

`docker compose` remains suitable for local development and diagnosis. It is not a supported production deployment interface because selecting an individual service can create release skew between the HTTP app and traffic-serving workers.
