class OutcomeService:
    def execute(
        self,
        route: dict,
        case: dict,
        context: dict,
        retrieval: dict,
        user_message: str,
    ) -> dict:
        _ = (retrieval, user_message)
        route_name = route["route"]
        reply = route.get("reply", {})

        if route_name == "answer":
            case["case_status"] = "resolved"
            context["case_state"]["case_status"] = "resolved"
            return {
                "outcome_type": "answer",
                "outcome_status": "completed",
                "outcome_payload": reply,
            }

        if route_name == "cannot_answer":
            case["case_status"] = "resolved"
            context["case_state"]["case_status"] = "resolved"
            return {
                "outcome_type": "cannot_answer",
                "outcome_status": "completed",
                "outcome_payload": reply,
            }

        if route_name == "retry_pending":
            case["case_status"] = "retry_pending"
            context["case_state"]["case_status"] = "retry_pending"
            return {
                "outcome_type": "retry_pending",
                "outcome_status": "retry_pending",
                "outcome_payload": reply,
            }

        if route_name == "out_of_scope":
            case["case_status"] = "resolved"
            context["case_state"]["case_status"] = "resolved"
            return {
                "outcome_type": "out_of_scope",
                "outcome_status": "completed",
                "outcome_payload": reply,
            }

        if route_name == "clarification_requested":
            case["case_status"] = "waiting_user"
            context["case_state"]["case_status"] = "waiting_user"
            return {
                "outcome_type": "clarification_requested",
                "outcome_status": "waiting_user",
                "outcome_payload": reply,
            }

        raise ValueError(f"Unsupported route: {route_name}")
