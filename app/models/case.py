from dataclasses import dataclass


@dataclass(slots=True)
class SupportCase:
    status: str = "open"
    route_mode: str = "direct_answer"
