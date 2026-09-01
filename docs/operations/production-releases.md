# Production releases

## Supported command

Run production releases only from a clean, committed checkout:

```bash
python3 scripts/release.py
```

Before the command, build a fresh `deploy/runtime-profile/` from the current clean checkout and promote that exact tree to the external runtime profile using the reviewed atomic profile-swap procedure. The generated directory is ignored by Git.

A failed candidate preparation is **not** a live-profile rollback event. It must not delete, recreate, or copy the external profile path after containers may have mounted it: Docker bind mounts retain the old directory inode and can otherwise lose access to prompts and Wiki files. Candidate rollback is limited to candidate/authored artifacts; only the atomic promotion function may replace the external profile, and it must run before this release command recreates the application plane.

The command independently rebuilds `deploy/profile-source/` in a temporary directory and verifies that `deploy/runtime-profile/` matches it exactly. It then verifies that the external runtime profile has the same exact file set and bytes, obtains the full Git SHA, builds release-tagged images for `app`, `worker`, `vk-worker`, `viewer-web`, and `viewer-push-worker`, and force-recreates exactly those services. It never recreates `db` or its volume.

A release is successful only when the command exits with `RELEASE SUCCESS` and writes a JSON receipt beneath `${SUPPORT_AGENT_RUNTIME_ROOT_HOST:-/home/tian/support-agent-runtime}/releases/`.

## Hard verification gate

The command fails when the generated artifact is stale relative to the committed source, when the external runtime profile differs or contains stale extra files, or when any application-plane service is missing, not running, was not recreated for the operation, has a mismatched OCI revision label, or (for Python runtime services) has a mismatched complete `app/**/*.py` source manifest. It additionally requires backend health, Telegram/VK polling liveness, and a controlled final-model failure probe in `app`, `worker`, and `vk-worker`; that probe must return `retry_pending` with empty customer text.

`docker compose ps`, a successful build, and a standalone `/health` response are insufficient evidence of a completed production release.

## Rollback

Use a previous receipt only when its `status` is `success`:

```bash
python3 scripts/release.py --rollback-receipt /external/runtime/root/releases/<successful-receipt>.json
```

Rollback selects that immutable image release ID, force-recreates the same application-plane services, and applies the same verification gate. It does not modify PostgreSQL, volumes, or runtime secrets.

## Manual Compose boundary

`docker compose` remains suitable for local development and diagnosis. It is not a supported production deployment interface because selecting an individual service can create release skew between the HTTP app and traffic-serving workers.
