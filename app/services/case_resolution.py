from app.schemas.message import InboundMessage


def resolve_case(payload: InboundMessage) -> dict:
    channel_user = f"{payload.channel}:{payload.external_user_id}"
    channel_chat = f"{payload.channel}:{payload.external_chat_id}"

    return {
        "user_id": channel_user,
        "channel_account_id": channel_chat,
        "conversation_id": channel_chat,
        "case_id": f"{channel_chat}:{payload.external_user_id}",
        "case_status": "open",
    }
