from pathlib import Path
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_routing_service
from app.core.db import Base, make_session_factory
from app.main import app
from app.schemas.message import InboundMessage
from app.services.routing import RoutingService


client = TestClient(app)


class FakeDirectLLMService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def classify_turn(self, text: str, *, conversation_context: dict | None = None) -> dict:
        self.calls.append({
            "method": "classify_turn",
            "text": text,
            "conversation_context": conversation_context,
        })
        if text.strip().lower() in {"привет", "здравствуйте", "добрый день"}:
            return {
                "turn_type": "social_turn",
                "confidence": 0.98,
                "reason": "test_social_turn",
            }
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
    ) -> dict:
        self.calls.append({
            "method": "assess_request",
            "text": text,
            "conversation_context": conversation_context,
            "retrieval": retrieval,
            "tool_observations": tool_observations,
        })
        retrieval = retrieval or {"kb_status": "not_started", "kb_snippets": []}
        tool_observations = tool_observations or []
        lowered = text.lower()

        if retrieval.get("kb_status") == "not_started":
            return {
                "action": "read_kb",
                "scope_status": "uncertain",
                "confidence": 0.6,
                "reason": "test_read_kb_first",
            }
        if "марс" in lowered:
            return {
                "action": "out_of_scope",
                "scope_status": "out_of_scope",
                "confidence": 0.9,
                "reason": "test_out_of_scope",
            }
        if retrieval.get("kb_status") == "not_found":
            return {
                "action": "cannot_answer",
                "scope_status": "in_scope",
                "confidence": 0.55,
                "reason": "test_no_grounding",
            }
        if "27 июня" in lowered and not tool_observations:
            return {
                "action": "use_tool",
                "scope_status": "in_scope",
                "confidence": 0.9,
                "reason": "test_calendar_tool",
            }
        return {
            "action": "answer_from_kb",
            "scope_status": "in_scope",
            "confidence": 0.82,
            "reason": "test_answer_from_kb",
        }

    def answer(
        self,
        text: str,
        kb_hits: list[dict],
        *,
        allow_general_without_kb: bool = False,
        conversation_context: dict | None = None,
    ) -> dict:
        self.calls.append({
            "method": "answer",
            "text": text,
            "kb_hits": kb_hits,
            "allow_general_without_kb": allow_general_without_kb,
            "conversation_context": conversation_context,
        })
        if allow_general_without_kb:
            return {
                "direct_status": "ready",
                "response_text": "Рад вас слышать. Задайте вопрос, и я постараюсь помочь.",
                "used_kb_sources": [],
                "confidence": 0.82,
                "decision": "answer",
                "reason": "social_llm",
            }
        return {
            "direct_status": "insufficient_confidence",
            "response_text": "",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.4,
            "decision": "handoff",
            "reason": "test_stub",
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
        "# Business Hours\n"
        "We are open from 9 to 18 on weekdays.\n"
        "Tandem jumps happen on weekends.\n"
        "Jumps require preparation before the jump.\n",
        encoding="utf-8",
    )

    return RoutingService(
        session_factory=session_factory,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        direct_llm=FakeDirectLLMService(),
        summary_service=FakeSummaryService(),
    )


def test_inbound_message_returns_db_backed_cannot_answer_payload(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    app.dependency_overrides[get_routing_service] = lambda: routing
    try:
        response = client.post('/api/v1/messages/inbound', json={
            "channel": "telegram",
            "external_user_id": "u1",
            "external_chat_id": "c1",
            "text": "What are your business hours?",
        })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()

    assert payload["case"]["case_status"] == "resolved"
    assert isinstance(payload["case"]["user_id"], int)
    assert isinstance(payload["case"]["channel_account_id"], int)
    assert isinstance(payload["case"]["conversation_id"], int)
    assert isinstance(payload["case"]["case_id"], int)
    assert payload["route"]["route"] == "cannot_answer"
    assert payload["route"]["route_reason"] == "test_stub"
    assert payload["outcome"]["outcome_type"] == "cannot_answer"
    assert payload["outcome"]["outcome_status"] == "completed"
    assert payload["audit"]["route"] == "cannot_answer"
    assert payload["audit"]["outcome_status"] == "completed"
    assert payload["audit"]["turn_classifier"]["turn_type"] == "knowledge_request"
    assert payload["audit"]["response_strategy"]["loop_mode"] == "bounded_agent_loop"
    assert payload["retrieval"]["kb_status"] == "found"
    assert payload["context"]["user_message"] == "What are your business hours?"
    assert payload["context"]["recent_turns"] == ["What are your business hours?"]
    assert payload["context"]["recent_messages"] == [{"role": "user", "content": "What are your business hours?"}]
    assert payload["context"]["session_summary"] == "What are your business hours?"


def test_routing_service_persists_entities_and_reuses_open_case(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)

    first = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u2",
        external_chat_id="c2",
        text="Первое сообщение",
    ))
    second = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u2",
        external_chat_id="c2",
        text="Второе сообщение",
    ))

    assert second["case"]["user_id"] == first["case"]["user_id"]
    assert second["case"]["channel_account_id"] == first["case"]["channel_account_id"]
    assert second["case"]["conversation_id"] == first["case"]["conversation_id"]
    assert second["case"]["case_id"] == first["case"]["case_id"]
    assert second["case"]["case_status"] == "resolved"
    assert second["context"]["recent_turns"] == ["Первое сообщение", "Второе сообщение"]
    assert second["context"]["recent_messages"] == [
        {"role": "user", "content": "Первое сообщение"},
        {"role": "user", "content": "Второе сообщение"},
    ]
    assert second["context"]["session_summary"] == "Первое сообщение | Второе сообщение"

    with routing.session_factory() as session:
        assert session.execute(text("select count(*) from users")).scalar_one() == 1
        assert session.execute(text("select count(*) from channel_accounts")).scalar_one() == 1
        assert session.execute(text("select count(*) from conversations")).scalar_one() == 1
        assert session.execute(text("select count(*) from support_cases")).scalar_one() == 1
        assert session.execute(text("select count(*) from messages")).scalar_one() == 2
        assert session.execute(text("select count(*) from workflow_events")).scalar_one() == 6
        event_types = session.execute(text("select event_type from workflow_events order by id")).scalars().all()
        assert event_types == [
            "turn_classified",
            "response_strategy_selected",
            "inbound_processed",
            "turn_classified",
            "response_strategy_selected",
            "inbound_processed",
        ]
        payload = session.execute(text("select payload from workflow_events order by id desc limit 1")).scalar_one()
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["turn_classifier"]["turn_type"] == "knowledge_request"
        assert payload["response_strategy"]["loop_mode"] == "bounded_agent_loop"


def test_routing_service_returns_answer_contract_when_confidence_is_high(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u3",
        external_chat_id="c3",
        text="What are your business hours?",
    )

    routing.direct_llm.answer = lambda text, kb_hits, *, allow_general_without_kb=False, conversation_context=None: {
        "direct_status": "ready",
        "response_text": "We are open from 9 to 18.",
        "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
        "confidence": 0.91,
        "decision": "answer",
        "reason": "sufficient_kb",
    }

    result = routing.handle_inbound(payload)

    assert result["route"]["route"] == "answer"
    assert result["route"]["route_confidence"] == 0.91
    assert result["outcome"]["outcome_type"] == "answer"
    assert result["outcome"]["outcome_status"] == "completed"
    assert result["outcome"]["outcome_payload"]["response_text"] == "We are open from 9 to 18."
    assert result["audit"]["route_reason"] == "sufficient_kb"


def test_routing_service_uses_model_for_social_turn_without_kb(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u4",
        external_chat_id="c4",
        text="привет",
    )

    result = routing.handle_inbound(payload)

    answer_calls = [call for call in routing.direct_llm.calls if call["method"] == "answer"]
    assert answer_calls == [{
        "method": "answer",
        "text": "привет",
        "kb_hits": [],
        "allow_general_without_kb": True,
        "conversation_context": result["context"],
    }]
    assert result["route"]["route"] == "answer"
    assert result["route"]["route_confidence"] == 0.82
    assert result["outcome"]["outcome_type"] == "answer"
    assert result["outcome"]["outcome_status"] == "completed"
    assert result["outcome"]["outcome_payload"]["reason"] == "social_llm"
    assert result["outcome"]["outcome_payload"]["response_text"] == "Рад вас слышать. Задайте вопрос, и я постараюсь помочь."
    assert result["retrieval"]["kb_status"] == "skipped_social_turn"
    assert result["retrieval"]["kb_skip_reason"] == "model_classified_social_turn"
    assert result["audit"]["turn_classifier"]["turn_type"] == "social_turn"
    assert result["audit"]["response_strategy"]["final_action"] == "social_reply"
    assert result["audit"]["response_strategy"]["classifier_path"] == "llm_turn_classifier"


def test_routing_service_reset_session_prepares_new_case(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    first = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u5",
        external_chat_id="c5",
        text="Нужна помощь",
    ))

    reset = routing.reset_session(InboundMessage(
        channel="telegram",
        external_user_id="u5",
        external_chat_id="c5",
        text="/new",
    ))

    second = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u5",
        external_chat_id="c5",
        text="Начинаем заново",
    ))

    assert reset["closed_case_count"] == 0
    assert reset["closed_case_ids"] == []
    assert second["case"]["case_id"] == reset["case_id"]
    assert second["case"]["case_id"] != first["case"]["case_id"]

    with routing.session_factory() as session:
        statuses = session.execute(text("select id, status, route_mode from support_cases order by id")).all()
        assert statuses == [
            (first["case"]["case_id"], "resolved", "cannot_answer"),
            (reset["case_id"], "resolved", "cannot_answer"),
        ]
        reset_event = session.execute(text("select event_type, payload from workflow_events where event_type = 'session_reset' order by id desc limit 1")).one()
        payload = reset_event[1]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert reset_event[0] == "session_reset"
        assert payload["command"] == "/new"
        assert payload["new_case_id"] == reset["case_id"]


def test_routing_service_reuses_same_case_and_context_for_follow_up_after_direct_answer(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    recorded_queries: list[str] = []

    original_retrieve = routing.retrieval.retrieve

    def recording_retrieve(query: str, backend: str, root: str) -> dict:
        recorded_queries.append(query)
        return original_retrieve(query, backend, root)

    routing.retrieval.retrieve = recording_retrieve
    recorded_answer_contexts: list[dict] = []

    def direct_answer(text, kb_hits, *, allow_general_without_kb=False, conversation_context=None):
        recorded_answer_contexts.append(conversation_context)
        return {
            "direct_status": "ready",
            "response_text": "Подготовка обязательна даже для первого прыжка.",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.9,
            "decision": "answer",
            "reason": "grounded_answer",
        }

    routing.direct_llm.answer = direct_answer

    first = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u6",
        external_chat_id="c6",
        text="А без подготовки можно?",
    ))
    routing.record_outbound_message(first["case"]["case_id"], first["outcome"]["outcome_payload"]["response_text"])
    second = routing.handle_inbound(InboundMessage(
        channel="telegram",
        external_user_id="u6",
        external_chat_id="c6",
        text="Я офицер вдв",
    ))

    assert second["case"]["case_id"] == first["case"]["case_id"]
    assert second["context"]["recent_turns"] == [
        "А без подготовки можно?",
        "Подготовка обязательна даже для первого прыжка.",
        "Я офицер вдв",
    ]
    assert second["context"]["recent_messages"] == [
        {"role": "user", "content": "А без подготовки можно?"},
        {"role": "assistant", "content": "Подготовка обязательна даже для первого прыжка."},
        {"role": "user", "content": "Я офицер вдв"},
    ]
    assert "Recent conversation context:" in recorded_queries[-1]
    assert "assistant: Подготовка обязательна даже для первого прыжка." in recorded_queries[-1]

    assert recorded_answer_contexts[-1]["recent_turns"] == [
        "А без подготовки можно?",
        "Подготовка обязательна даже для первого прыжка.",
        "Я офицер вдв",
    ]
    assess_calls = [call for call in routing.direct_llm.calls if call["method"] == "assess_request"]
    assert assess_calls[-1]["text"].startswith("Current user message: Я офицер вдв")
    assert "assistant: Подготовка обязательна даже для первого прыжка." in assess_calls[-1]["text"]


def test_routing_service_uses_calendar_tool_before_answering(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u7",
        external_chat_id="c7",
        text="27 июня будут прыжки?",
    )

    def answer(text, kb_hits, *, allow_general_without_kb=False, conversation_context=None):
        tool_obs = (conversation_context or {}).get("tool_observations", [])
        assert tool_obs, "tool observations must be present for date-based question"
        joined = "\n".join(item["summary"] for item in tool_obs)
        assert "Дата" in joined
        return {
            "direct_status": "ready",
            "response_text": "Проверил дату по календарю и правилу выходных: смотрите результат по конкретному дню.",
            "used_kb_sources": [hit["source_ref"] for hit in kb_hits],
            "confidence": 0.93,
            "decision": "answer",
            "reason": "kb_plus_calendar_tool",
        }

    routing.direct_llm.answer = answer

    result = routing.handle_inbound(payload)

    assert result["route"]["route"] == "answer"
    assert result["outcome"]["outcome_payload"]["reason"] == "kb_plus_calendar_tool"
    assert result["audit"]["response_strategy"]["loop_trace"][1]["action"] == "use_tool"


def test_routing_service_marks_offtopic_request_as_out_of_scope(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    payload = InboundMessage(
        channel="telegram",
        external_user_id="u8",
        external_chat_id="c8",
        text="Какая погода на Марсе?",
    )

    result = routing.handle_inbound(payload)

    assert result["route"]["route"] == "out_of_scope"
    assert result["outcome"]["outcome_type"] == "out_of_scope"
    assert "Я отвечаю только" in result["outcome"]["outcome_payload"]["response_text"]


def test_direct_llm_promotes_repeat_read_kb_to_answer_from_kb() -> None:
    class RepeatReadClient:
        def generate(self, **kwargs):
            return json.dumps({
                "action": "read_kb",
                "scope_status": "in_scope",
                "confidence": 0.95,
                "reason": "repeat_read_kb",
                "clarification_question": "",
            }, ensure_ascii=False)

    from app.services.direct_llm import DirectLLMService

    service = DirectLLMService(client=RepeatReadClient())
    result = service.assess_request(
        "как проходят",
        conversation_context={
            "user_message": "как проходят",
            "recent_turns": ["Подскажите пожалуйста по прыжкам", "как проходят"],
            "recent_messages": [
                {"role": "user", "content": "Подскажите пожалуйста по прыжкам"},
                {"role": "assistant", "content": "Что именно по прыжкам вас интересует?"},
                {"role": "user", "content": "как проходят"},
            ],
            "session_summary": "Пользователь спрашивает, как проходят прыжки.",
        },
        retrieval={"kb_status": "found", "kb_snippets": [{"source_ref": "kb/concepts/skydiving-services.md", "text": "Подготовка обязательна."}]},
        tool_observations=[],
    )

    assert result["action"] == "answer_from_kb"
    assert result["reason"] == "repeat_read_kb"


def test_direct_llm_prefers_read_kb_over_clarification_for_broad_in_domain_openers() -> None:
    class ClarifyClient:
        def generate(self, **kwargs):
            return json.dumps({
                "action": "ask_clarification",
                "scope_status": "in_scope",
                "confidence": 0.8,
                "reason": "too_broad",
                "clarification_question": "Что именно вас интересует?",
            }, ensure_ascii=False)

    from app.services.direct_llm import DirectLLMService

    service = DirectLLMService(client=ClarifyClient())
    result = service.assess_request(
        "Подскажите пожалуйста по прыжкам",
        conversation_context={"user_message": "Подскажите пожалуйста по прыжкам", "recent_messages": []},
        retrieval={"kb_status": "not_started", "kb_snippets": []},
        tool_observations=[],
    )

    assert result["action"] == "read_kb"
    assert result["reason"] == "too_broad"


def test_direct_llm_prefers_answer_from_kb_over_clarification_when_kb_is_already_found() -> None:
    class ClarifyClient:
        def generate(self, **kwargs):
            return json.dumps({
                "action": "ask_clarification",
                "scope_status": "in_scope",
                "confidence": 0.8,
                "reason": "too_broad_after_kb",
                "clarification_question": "Что именно вас интересует?",
            }, ensure_ascii=False)

    from app.services.direct_llm import DirectLLMService

    service = DirectLLMService(client=ClarifyClient())
    result = service.assess_request(
        "Подскажите пожалуйста по прыжкам",
        conversation_context={"user_message": "Подскажите пожалуйста по прыжкам", "recent_messages": []},
        retrieval={
            "kb_status": "found",
            "kb_snippets": [{
                "source_ref": "kb/concepts/skydiving-services.md",
                "text": "Прыжки проходят на аэродроме Калачево по выходным. Тандем-прыжок выполняется с высоты 2500 м после инструктажа 15–30 минут. Самостоятельный прыжок выполняется с высоты 800–900 м после подготовки 3–4 часа.",
            }],
        },
        tool_observations=[],
    )

    assert result["action"] == "answer_from_kb"
    assert result["reason"] == "too_broad_after_kb"
