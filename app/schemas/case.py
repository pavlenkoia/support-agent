from pydantic import BaseModel


class CaseState(BaseModel):
    status: str
    route_mode: str
