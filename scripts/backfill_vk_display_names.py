from __future__ import annotations

import argparse
from collections.abc import Iterable

from sqlalchemy import select

from app.core.db import SessionLocal
from app.integrations.vk.client import VKAPIClient
from app.models.user import User


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def compose_display_name(profile: dict) -> str | None:
    first_name = str(profile.get("first_name") or "").strip()
    last_name = str(profile.get("last_name") or "").strip()
    display_name = " ".join(part for part in (first_name, last_name) if part).strip()
    return display_name or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill VK display names into users.display_name")
    parser.add_argument("--batch-size", type=int, default=100, help="How many VK ids to resolve per users.get call")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of users to process")
    parser.add_argument("--dry-run", action="store_true", help="Resolve names and print summary without writing to DB")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = VKAPIClient()

    with SessionLocal() as session:
        stmt = (
            select(User)
            .where(User.external_id.like("vk:%"))
            .where((User.display_name.is_(None)) | (User.display_name == ""))
            .order_by(User.id.asc())
        )
        users = list(session.scalars(stmt))
        if args.limit > 0:
            users = users[: args.limit]

        if not users:
            print("No VK users with empty display_name found.")
            return 0

        external_ids = [str(user.external_id).split(":", 1)[1] for user in users if ":" in str(user.external_id)]
        by_external_id = {str(user.external_id): user for user in users}

        resolved = 0
        updated = 0
        skipped = 0

        for batch in chunked(external_ids, max(args.batch_size, 1)):
            response = client.get_users([user_id for user_id in batch])
            if not response.get("ok"):
                print(f"VK users.get failed for batch starting with {batch[0]}: {response.get('reason')}")
                skipped += len(batch)
                continue

            profiles = response.get("response")
            if not isinstance(profiles, list):
                print(f"VK users.get returned unexpected response shape for batch starting with {batch[0]}")
                skipped += len(batch)
                continue

            for profile in profiles:
                if not isinstance(profile, dict):
                    skipped += 1
                    continue
                user_id = str(profile.get("id") or "").strip()
                if not user_id:
                    skipped += 1
                    continue
                display_name = compose_display_name(profile)
                if not display_name:
                    skipped += 1
                    continue
                resolved += 1
                user = by_external_id.get(f"vk:{user_id}")
                if user is None:
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"DRY RUN: would set {user.external_id} -> {display_name}")
                    continue
                user.display_name = display_name
                updated += 1

        if args.dry_run:
            print(
                f"Dry run complete: candidates={len(users)} resolved={resolved} would_update={resolved} skipped={skipped}"
            )
            session.rollback()
            return 0

        session.commit()
        print(f"Backfill complete: candidates={len(users)} resolved={resolved} updated={updated} skipped={skipped}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
