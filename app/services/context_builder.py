from app.schemas.message import InboundMessage


def build_context(payload: InboundMessage) -> dict:
    return {
        "recent_turns": [payload.text],
        "summary": "Initial placeholder summary",
        "case_meta": {"channel": payload.channel},
    }
