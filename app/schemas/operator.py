from pydantic import BaseModel


class OperatorEscalation(BaseModel):
    case_status: str
    note: str
