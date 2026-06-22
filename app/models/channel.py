from dataclasses import dataclass


@dataclass(slots=True)
class ChannelAccount:
    channel: str
    external_chat_id: str
