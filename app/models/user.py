from dataclasses import dataclass


@dataclass(slots=True)
class User:
    external_id: str
    display_name: str | None = None
