from pathlib import Path
from typing import Any

import pytest

from app.core.db import Base, make_session_factory
from app.schemas.message import InboundMessage
from app.services.outcome import OutcomeService
from app.services.routing import RoutingService


class RecordingOutcomeService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, dict, dict, str]] = []

    def execute(
        self,
        route: dict,
        case: dict,
        context: dict,
        retrieval: dict,
        user_message: str,
    ) -> dict:
        self.calls.append((route, case.copy(), context.copy(), retrieval.copy(), user_message))
        case["case_status"] = "resolved"
        context["case_state"]["case_status"] = "resolved"
        return {
            "outcome_type": route["route"],
            "outcome_status": "completed",
            "outcome_payload": {"response_text": "delegated"},
        }


class FakeDirectLLMService:
    def classify_turn(self, text: str, *, conversation_context: dict | None = None) -> dict:
        return {
            "turn_type": "knowledge_request",
            "confidence": 0.0,
            "reason": "test_default",
        }

    def assess_request(
        self,
        text: str,
        *,
        conversation_context: dict | None = None,
        retrieval: dict | None = None,
        tool_observations: list[dict] | None = None,
        runtime_capabilities: list[dict[str, str]] | None = None,
    ) -> dict:
        _ = runtime_capabilities
        retrieval = retrieval or {"kb_status": "not_started", "kb_snippets": []}
        if retrieval.get("kb_status") == "not_started":
            return {
                "action": "read_kb",
                "scope_status": "uncertain",
                "confidence": 0.6,
                "reason": "test_read_kb_first",
            }
        return {
            "action": "answer_from_kb",
            "scope_status": "in_scope",
            "confidence": 0.8,
            "reason": "test_answer_from_grounding",
        }

    def answer(
        self,
        text: str,
        kb_hits: list[dict],
        *,
        allow_general_without_kb: bool = False,
        conversation_context: dict | None = None,
    ) -> dict:
        return {
            "direct_status": "insufficient_confidence",
            "response_text": "",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.4,
            "decision": "handoff",
            "reason": "test_stub",
        }

    def respond(
        self,
        text: str,
        kb_result: dict,
        *,
        knowledge_mode: str,
        conversation_context: dict | None = None,
        tool_observations: list[dict] | None = None,
        first_reply_in_dialogue: bool = False,
    ) -> dict:
        _ = (knowledge_mode, tool_observations, first_reply_in_dialogue)
        legacy = self.answer(
            text,
            kb_result.get("answer_context", []),
            allow_general_without_kb=False,
            conversation_context=conversation_context,
        )
        return {
            "route": "answer" if legacy.get("decision") == "answer" else "cannot_answer",
            "response_text": legacy.get("response_text", ""),
            "confidence": legacy.get("confidence", 0.0),
            "reason": legacy.get("reason", "test_stub"),
        }


class FakeSummaryService:
    def summarize_case(self, messages: list[str]) -> str | None:
        cleaned = [message for message in messages if message]
        if not cleaned:
            return None
        return " | ".join(cleaned[:3])


def make_test_routing_service(tmp_path: Path) -> RoutingService:
    db_path = tmp_path / "test.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    kb_root = tmp_path / "kb"
    (kb_root / "concepts").mkdir(parents=True)
    (kb_root / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (kb_root / "concepts" / "hours.md").write_text(
        "# Business Hours\nWe are open from 9 to 18 on weekdays. Tandem jumps happen on weekends.\n",
        encoding="utf-8",
    )

    return RoutingService(
        session_factory=session_factory,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        direct_llm=FakeDirectLLMService(),
        summary_service=FakeSummaryService(),
    )






def test_outcome_service_builds_cannot_answer_outcome() -> None:
    service = OutcomeService()
    case = {
        "case_id": "case-1",
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "case_status": "open",
    }
    context = {
        "user_message": "Need help",
        "recent_turns": ["Need help"],
        "session_summary": "Need help",
        "case_state": {"case_id": "case-1", "case_status": "open", "conversation_id": "conv-1"},
    }
    retrieval = {
        "kb_status": "found",
        "kb_snippets": [{"text": "x", "source_type": "kb_article", "source_ref": "kb://1"}],
    }
    route = {
        "route": "cannot_answer",
        "route_reason": "low_grounding",
        "route_confidence": 0.4,
        "reply": {"response_text": "Сейчас не могу дать точный ответ на этот вопрос."},
    }

    outcome = service.execute(route, case, context, retrieval, "Need help")

    assert outcome["outcome_type"] == "cannot_answer"
    assert outcome["outcome_status"] == "completed"
    assert outcome["outcome_payload"]["response_text"] == "Сейчас не могу дать точный ответ на этот вопрос."
    assert case["case_status"] == "resolved"
    assert context["case_state"]["case_status"] == "resolved"
