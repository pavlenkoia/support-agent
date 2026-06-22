from app.schemas.message import InboundMessage


def normalize_message(payload: InboundMessage) -> InboundMessage:
    return payload
