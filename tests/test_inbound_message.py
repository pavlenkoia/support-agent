from pathlib import Path
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_routing_service
from app.core.db import Base, make_session_factory
from app.main import app
from app.schemas.message import InboundMessage
from app.services.direct_llm import DirectLLMService
from app.services.policy import PolicyService
from app.services.routing import RoutingService
from app.services.system_prompt import SystemPromptService


client = TestClient(app)


TEST_PROMPT = """## Обязательный ответ при отсутствии информации
Я не могу точно ответить по этому вопросу. Пожалуйста, позвоните в офис в будние дни по телефону +7 (351) 214-30-30.
"""


class FakeKBAgentService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def read(self, text: str, kb_hits: list[dict], *, conversation_context: dict | None = None) -> dict:
        packet = {
            "kb_status": "found" if kb_hits else "not_found",
            "kb_mode": "test_stub",
            "grounding_status": "ready" if kb_hits else "not_found",
            "answer_context": kb_hits,
            "grounded_facts": [str(hit.get("text") or "").strip() for hit in kb_hits[:3]],
            "answer_basis": " ".join(str(hit.get("text") or "").strip() for hit in kb_hits[:2]).strip(),
            "source_refs": [hit.get("source_ref") for hit in kb_hits if hit.get("source_ref")],
            "trace": {"reason": "fake_kb_agent"},
        }
        self.calls.append(
            {
                "method": "read",
                "text": text,
                "kb_hits": kb_hits,
                "conversation_context": conversation_context,
                "packet": packet,
            }
        )
        return packet


class FakeDirectLLMService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def respond(
        self,
        text: str,
        kb_result,
        *,
        conversation_context: dict | None = None,
        tool_observations: list[dict] | None = None,
        first_reply_in_dialogue: bool = False,
    ) -> dict:
        tool_observations = tool_observations or []
        kb_packet = kb_result if isinstance(kb_result, dict) else {"answer_context": kb_result}
        answer_context = kb_packet.get("answer_context", [])
        self.calls.append(
            {
                "method": "respond",
                "text": text,
                "kb_result": kb_packet,
                "conversation_context": conversation_context,
                "tool_observations": tool_observations,
                "first_reply_in_dialogue": first_reply_in_dialogue,
            }
        )
        override = getattr(self, "answer", None)
        if callable(override):
            legacy = override(text, answer_context, allow_general_without_kb=False, conversation_context=conversation_context)
            return {
                "route": "answer" if legacy.get("decision") == "answer" else "cannot_answer",
                "response_text": legacy.get("response_text", ""),
                "confidence": legacy.get("confidence", 0.0),
                "reason": legacy.get("reason", "legacy_answer_override"),
            }

        lowered = text.lower()
        if any(token in lowered for token in ("цен", "стоим", "сколько")):
            reply = "Здравствуйте! Все цены доступны по ссылке https://vk.cc/cYzS5j." if first_reply_in_dialogue else "Все цены доступны по ссылке https://vk.cc/cYzS5j."
            return {"route": "answer", "response_text": reply, "confidence": 0.93, "reason": "pricing_rule"}
        if any(token in lowered for token in ("суп", "рецепт", "марс", "погод")):
            return {
                "route": "out_of_scope",
                "response_text": "Я помогаю только по вопросам прыжков, сертификатов и связанных услуг в Челябинске.",
                "confidence": 0.9,
                "reason": "out_of_scope_rule",
            }
        if "июня" in lowered or "25" in lowered or "27" in lowered:
            assert tool_observations, "date questions must include tool observations"
            return {
                "route": "answer",
                "response_text": "Здравствуйте! 25 июня — это будний день, а прыжки обычно проходят по выходным." if first_reply_in_dialogue else "25 июня — это будний день, а прыжки обычно проходят по выходным.",
                "confidence": 0.9,
                "reason": "date_with_kb_rule",
            }
        if not answer_context:
            return {
                "route": "cannot_answer",
                "response_text": "Я не могу точно ответить по этому вопросу. Пожалуйста, позвоните в офис в будние дни по телефону +7 (351) 214-30-30.",
                "confidence": 0.0,
                "reason": "no_grounding",
            }
        snippet = str(answer_context[0].get("text") or "").split(".\n")[0].split(".")[0].strip()
        if first_reply_in_dialogue:
            snippet = f"Здравствуйте! {snippet}."
        else:
            snippet = f"{snippet}."
        return {"route": "answer", "response_text": snippet, "confidence": 0.82, "reason": "grounded_kb_reply"}


def make_test_routing_service(tmp_path: Path, direct_llm=None, kb_agent=None) -> RoutingService:
    db_path = tmp_path / "test.db"
    session_factory = make_session_factory(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=session_factory.kw["bind"])

    profile_root = tmp_path / "profile"
    kb_root = profile_root / "kb"
    (kb_root / "concepts").mkdir(parents=True)
    (kb_root / "entities").mkdir(parents=True)
    (kb_root / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (profile_root / "SYSTEM_PROMPT.md").write_text(TEST_PROMPT, encoding="utf-8")
    (kb_root / "concepts" / "pricing.md").write_text(
        "# Pricing\nВсе цены доступны по ссылке https://vk.cc/cYzS5j.\n",
        encoding="utf-8",
    )
    (kb_root / "concepts" / "schedule.md").write_text(
        "# Schedule\nПрыжки обычно проходят по выходным.\nДля групп около 20 человек возможна договоренность на другой день.\n",
        encoding="utf-8",
    )
    (kb_root / "entities" / "office.md").write_text(
        "# Office\nОфис находится в Челябинске.\n",
        encoding="utf-8",
    )

    prompt_service = SystemPromptService(str(profile_root / "SYSTEM_PROMPT.md"))
    policy = PolicyService(profile_root=str(profile_root), prompt_service=prompt_service)

    return RoutingService(
        session_factory=session_factory,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        kb_agent=kb_agent or FakeKBAgentService(),
        direct_llm=direct_llm or FakeDirectLLMService(),
        policy=policy,
    )


def test_inbound_message_uses_prompt_driven_runtime_and_mandatory_kb_lookup(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    app.dependency_overrides[get_routing_service] = lambda: routing
    try:
        response = client.post(
            "/api/v1/messages/inbound",
            json={
                "channel": "telegram",
                "external_user_id": "u1",
                "external_chat_id": "c1",
                "text": "Сколько стоят прыжки?",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["route"] == "answer"
    assert payload["outcome"]["outcome_payload"]["response_text"] == "Здравствуйте! Все цены доступны по ссылке https://vk.cc/cYzS5j."
    assert payload["retrieval"]["kb_status"] == "found"
    assert payload["audit"]["turn_classifier"]["turn_type"] == "prompt_driven_dialogue"
    assert payload["audit"]["response_strategy"]["loop_mode"] == "agentic_bounded_loop_with_kb_agent"
    respond_calls = [call for call in routing.direct_llm.calls if call["method"] == "respond"]
    assert respond_calls[-1]["first_reply_in_dialogue"] is True
    assert respond_calls[-1]["kb_result"]["answer_context"]


def test_routing_service_marks_out_of_scope_request(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    result = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="u2",
            external_chat_id="c2",
            text="Напиши рецепт горохового супа",
        )
    )

    assert result["route"]["route"] == "out_of_scope"
    assert result["outcome"]["outcome_type"] == "out_of_scope"
    assert "Челябинске" in result["outcome"]["outcome_payload"]["response_text"]


def test_routing_service_uses_calendar_tool_then_kb_for_date_question(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    result = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="u3",
            external_chat_id="c3",
            text="25 июня прыжки будут?",
        )
    )

    assert result["route"]["route"] == "answer"
    assert "будний день" in result["outcome"]["outcome_payload"]["response_text"]
    actions = [item["action"] for item in result["audit"]["response_strategy"]["loop_trace"]]
    assert actions[0] == "use_tool"
    assert "read_kb" in actions
    tool_observations = result["outcome"]["outcome_payload"]["tool_observations"]
    assert any(item["kind"] == "calendar_weekday" for item in tool_observations)


def test_routing_service_persists_outbound_message_for_followup_context(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    first = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="u4",
            external_chat_id="c4",
            text="Сколько стоят прыжки?",
        )
    )
    routing.record_outbound_message(first["case"]["case_id"], first["outcome"]["outcome_payload"]["response_text"])
    second = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="u4",
            external_chat_id="c4",
            text="А где это?",
        )
    )

    assert second["case"]["case_id"] == first["case"]["case_id"]
    assert second["context"]["recent_messages"] == [
        {"role": "user", "content": "Сколько стоят прыжки?"},
        {"role": "assistant", "content": "Здравствуйте! Все цены доступны по ссылке https://vk.cc/cYzS5j."},
        {"role": "user", "content": "А где это?"},
    ]
    respond_calls = [call for call in routing.direct_llm.calls if call["method"] == "respond"]
    assert respond_calls[-1]["first_reply_in_dialogue"] is False


def test_routing_service_persists_entities_and_workflow_events(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="u5", external_chat_id="c5", text="Сколько стоят прыжки?"))
    routing.handle_inbound(InboundMessage(channel="telegram", external_user_id="u5", external_chat_id="c5", text="25 июня прыжки будут?"))

    with routing.session_factory() as session:
        assert session.execute(text("select count(*) from users")).scalar_one() == 1
        assert session.execute(text("select count(*) from channel_accounts")).scalar_one() == 1
        assert session.execute(text("select count(*) from conversations")).scalar_one() == 1
        assert session.execute(text("select count(*) from support_cases")).scalar_one() == 1
        assert session.execute(text("select count(*) from messages")).scalar_one() == 2
        assert session.execute(text("select count(*) from workflow_events")).scalar_one() == 6
        payload = session.execute(text("select payload from workflow_events order by id desc limit 1")).scalar_one()
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["response_strategy"]["loop_mode"] == "agentic_bounded_loop_with_kb_agent"


def test_prompt_runtime_falls_back_when_model_leaks_service_markers(tmp_path: Path) -> None:
    class LeakClient:
        def generate(self, **kwargs):
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "KnowledgeBase Result: [служебные данные]",
                    "confidence": 0.9,
                    "reason": "bad_output",
                },
                ensure_ascii=False,
            )

    prompt_path = tmp_path / "SYSTEM_PROMPT.md"
    prompt_path.write_text(TEST_PROMPT, encoding="utf-8")
    service = DirectLLMService(client=LeakClient(), prompt_service=SystemPromptService(str(prompt_path)))
    result = service.respond("Сколько стоят прыжки?", [{"text": "Все цены доступны по ссылке https://vk.cc/cYzS5j.", "source_ref": "kb/pricing.md"}])

    assert result["route"] == "cannot_answer"
    assert result["response_text"] == "Я не могу точно ответить по этому вопросу. Пожалуйста, позвоните в офис в будние дни по телефону +7 (351) 214-30-30."


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


def test_direct_llm_promotes_repeat_use_tool_to_read_kb_after_tool_result() -> None:
    class RepeatToolClient:
        def generate(self, **kwargs):
            return json.dumps({
                "action": "use_tool",
                "scope_status": "in_scope",
                "confidence": 0.95,
                "reason": "repeat_use_tool",
                "clarification_question": "",
            }, ensure_ascii=False)

    service = DirectLLMService(client=RepeatToolClient())
    result = service.assess_request(
        "25 июня прыжки будут?",
        conversation_context={"user_message": "25 июня прыжки будут?", "recent_messages": []},
        retrieval={"kb_status": "not_started", "kb_snippets": []},
        tool_observations=[
            {
                "kind": "calendar_weekday",
                "summary": "Дата 2026-06-25 приходится на четверг.",
                "structured": {"weekday_ru": "четверг", "is_weekend": False},
            }
        ],
    )

    assert result["action"] == "read_kb"
    assert result["reason"] == "repeat_use_tool"


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
