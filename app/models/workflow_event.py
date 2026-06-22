from dataclasses import dataclass


@dataclass(slots=True)
class WorkflowEvent:
    event_type: str
    actor: str
