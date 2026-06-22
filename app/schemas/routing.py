from pydantic import BaseModel


class RoutingDecision(BaseModel):
    mode: str
    reason: str
