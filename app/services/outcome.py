from app.services.escalation import EscalationService
from app.services.hermes_backend import HermesBackendService


class OutcomeService:
    def __init__(
        self,
        hermes: HermesBackendService | None = None,
        escalation: EscalationService | None = None,
    ) -> None:
        self.hermes = hermes or HermesBackendService()
        self.escalation = escalation or EscalationService()

    def execute(
        self,
        route: dict,
        case: dict,
        context: dict,
        retrieval: dict,
        user_message: str,
    ) -> dict:
        route_name = route["route"]

        if route_name == "direct_answer":
            case["case_status"] = "resolved"
            context["case_state"]["case_status"] = "resolved"
            return {
                "outcome_type": "direct_answer",
                "outcome_status": "completed",
                "outcome_payload": route["direct_result"],
            }

        if route_name == "hermes_escalation":
            case["case_status"] = "waiting_hermes"
            context["case_state"]["case_status"] = "waiting_hermes"
            hermes_result = self.hermes.analyze(user_message, context, retrieval)
            return {
                "outcome_type": "hermes_escalation",
                "outcome_status": "pending",
                "outcome_payload": hermes_result,
            }

        if route_name == "human_escalation":
            case["case_status"] = "waiting_human"
            context["case_state"]["case_status"] = "waiting_human"
            handoff = self.escalation.to_human(route["route_reason"], case, context, retrieval)
            return {
                "outcome_type": "human_escalation",
                "outcome_status": "waiting_human",
                "outcome_payload": handoff,
            }

        raise ValueError(f"Unsupported route: {route_name}")
