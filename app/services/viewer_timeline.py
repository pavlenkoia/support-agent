"""Read-only VK timeline projection; never alter persisted dialogue history."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from app.models.message import Message
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.transport_event import TransportEvent


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def vk_timeline(
    messages: list[Message], events: list[TransportEvent], sends: list[OutboundTransportSend]
) -> list[tuple[Message, datetime]]:
    """Use uniquely correlated VK timestamps/IDs, otherwise keep stored time.

    Content alone is not an identity: inbound/operator rows also require the exact
    source timestamp; bot rows require a unique case/text ledger match and echo.
    Repeated/ambiguous candidates deliberately retain their historical fallback.
    """
    source_groups = defaultdict(list)
    echoes = defaultdict(list)
    for event in events:
        external_id = event.external_message_id
        if not external_id or not external_id.isdecimal():
            continue
        obj = (event.payload_json or {}).get("object")
        if not isinstance(obj, dict):
            continue
        body = obj.get("message", obj)
        if not isinstance(body, dict):
            continue
        stamp = body.get("date")
        if not isinstance(stamp, int) or isinstance(stamp, bool):
            continue
        try:
            at = datetime.fromtimestamp(stamp, UTC)
        except (ValueError, OverflowError, OSError):
            continue
        text = body.get("text")
        if not isinstance(text, str):
            continue
        item = (at, int(external_id), text)
        if event.event_type == "message_reply":
            echoes[external_id].append(item)
        role = "user" if event.event_type == "message_new" else "human"
        source_groups[(role, text, at)].append(item)

    send_groups = defaultdict(list)
    for send in sends:
        if send.send_status in {"sent", "reconciled"} and send.external_message_id:
            send_groups[(send.case_id, send.content_text)].append(send)
    message_groups = defaultdict(list)
    for message in messages:
        key = ((message.case_id, message.content) if message.role == "assistant"
               else (message.role, message.content, _utc(message.created_at)))
        message_groups[key].append(message)

    projected = []
    for message in messages:
        at, order = _utc(message.created_at), 0
        if message.role == "assistant":
            key = (message.case_id, message.content)
            candidates = send_groups[key]
            if len(message_groups[key]) == 1 and len(candidates) == 1:
                matches = echoes[candidates[0].external_message_id]
                if len(matches) == 1 and matches[0][2] == message.content:
                    at, order, _ = matches[0]
        elif message.role in {"user", "human"}:
            key = (message.role, message.content, at)
            matches = source_groups[key]
            if len(message_groups[key]) == 1 and len(matches) == 1:
                at, order, _ = matches[0]
        projected.append((message, at, order))
    projected.sort(key=lambda item: (item[1], item[2], item[0].id))
    return [(message, at) for message, at, _ in projected]
