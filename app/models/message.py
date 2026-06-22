from dataclasses import dataclass


@dataclass(slots=True)
class Message:
    role: str
    content: str
