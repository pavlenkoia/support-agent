from dataclasses import dataclass


@dataclass(slots=True)
class Conversation:
    external_id: str
    status: str = "active"
