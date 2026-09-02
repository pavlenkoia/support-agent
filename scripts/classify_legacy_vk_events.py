from __future__ import annotations

"""Classify legacy nonterminal VK inbound events without sending any message.

Default mode is read-only JSON output. `--apply` only writes an explicit audit
marker; it never invokes routing, a sender, or a VK API client.
"""

import argparse
import json
from collections import Counter

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.transport_event import TransportEvent

TERMINAL_STATUSES = {"processed", "suppressed", "failed"}


def classify(event: TransportEvent) -> str:
    reason = str(event.error_text or "")
    if event.status == "waiting_human" and reason in {
        "kb_agent_retry_exhausted",
        "received_timeout_without_finalization",
    }:
        return "requires_operator_review"
    if event.status in TERMINAL_STATUSES:
        return "terminal"
    return "unknown_nonterminal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write only a classification audit marker; never replay or send VK messages.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        events = list(
            session.scalars(
                select(TransportEvent)
                .where(
                    TransportEvent.platform == "vk",
                    TransportEvent.event_type == "message_new",
                    TransportEvent.status.not_in(TERMINAL_STATUSES),
                )
                .order_by(TransportEvent.id)
            )
        )
        rows = [
            {
                "event_id": event.id,
                "status": event.status,
                "reason": str(event.error_text or ""),
                "classification": classify(event),
            }
            for event in events
        ]
        if args.apply:
            for event, row in zip(events, rows, strict=True):
                event.error_text = f"legacy_classified:{row['classification']}:{row['reason']}"
            session.commit()

    print(json.dumps({"count": len(rows), "by_classification": Counter(row["classification"] for row in rows), "events": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
