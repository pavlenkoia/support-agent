import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

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


class RetryPendingKBAgentService:
    def read(self, text: str, kb_hits: list[dict], *, conversation_context: dict | None = None) -> dict:
        _ = (text, kb_hits, conversation_context)
        return {
            "kb_status": "found",
            "kb_mode": "test_stub",
            "grounding_status": "retry_pending",
            "answer_context": [],
            "grounded_facts": [],
            "answer_basis": "",
            "source_refs": [],
            "trace": {"extraction": {"reason": "grounding_error:IncompleteRead"}},
        }


class ExhaustedRecoveryKBAgentService:
    def read(self, text: str, kb_hits: list[dict], *, conversation_context: dict | None = None) -> dict:
        _ = (text, kb_hits, conversation_context)
        return {
            "kb_status": "found",
            "kb_mode": "test_stub",
            "grounding_status": "llm_unavailable",
            "answer_context": [],
            "grounded_facts": [],
            "answer_basis": "",
            "source_refs": [],
            "reason": "llm_recovery_exhausted:rate_limited",
            "trace": {"extraction": {"reason": "llm_recovery_exhausted:rate_limited"}},
        }


class NotGroundedKBAgentService:
    def read(self, text: str, kb_hits: list[dict], *, conversation_context: dict | None = None) -> dict:
        _ = (text, conversation_context)
        return {
            "kb_status": "found",
            "kb_mode": "test_stub",
            "grounding_status": "not_found",
            "answer_context": kb_hits,
            "grounded_facts": [],
            "answer_basis": "",
            "source_refs": [hit.get("source_ref") for hit in kb_hits if hit.get("source_ref")],
            "trace": {"extraction": {"reason": "missing_confirmed_fact"}},
        }


class FakeDirectLLMService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def begin_turn(self, *, text: str, context: dict) -> dict:
        self.calls.append({"method": "begin_turn", "text": text, "context": context})
        return {"kind": "wiki_lookup", "tool_call_id": "wiki-1", "tool_request": {"query": text, "context_scope": "current customer subject", "needed_fact": "confirmed answer"}, "llm_trace": []}

    def continue_after_tool(self, *, text: str, context: dict, tool_name: str, **kwargs) -> dict:
        if tool_name == "wiki_lookup" and any(token in text.lower() for token in ("июня", "25", "27")):
            return {"kind": "calendar_lookup", "tool_call_id": "calendar-1", "tool_request": {"date_expression": "25 июня", "requested_calendar_fact": "день недели"}, "llm_trace": []}
        return {"kind": "finalization_requested", "response_intent": "answer", "reason": "fixture_ready", "llm_trace": []}

    def respond(
        self,
        text: str,
        kb_result,
        *,
        knowledge_mode: str = "kb_grounded",
        conversation_context: dict | None = None,
        tool_observations: list[dict] | None = None,
        first_reply_in_dialogue: bool = False,
        response_intent: str = "answer",
    ) -> dict:
        _ = response_intent
        tool_observations = tool_observations or []
        kb_packet = kb_result if isinstance(kb_result, dict) else {"answer_context": kb_result}
        answer_context = [{"text": fact} for fact in kb_packet.get("grounded_facts", [])]
        self.calls.append(
            {
                "method": "respond",
                "text": text,
                "kb_result": kb_packet,
                "knowledge_mode": knowledge_mode,
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
        answer_engine_mode="agent_tool_loop",
    )










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


def test_routing_starts_new_case_when_local_calendar_day_changes(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    yekaterinburg = ZoneInfo("Asia/Yekaterinburg")
    first = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="boundary-day",
            external_chat_id="boundary-day",
            text="Сколько стоят прыжки?",
            received_at=datetime(2026, 8, 10, 23, 30, tzinfo=yekaterinburg),
        )
    )
    second = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="boundary-day",
            external_chat_id="boundary-day",
            text="А сертификат действует?",
            received_at=datetime(2026, 8, 11, 0, 15, tzinfo=yekaterinburg),
        )
    )

    assert second["case"]["case_id"] != first["case"]["case_id"]
    assert second["context"]["recent_messages"] == [{"role": "user", "content": "А сертификат действует?"}]


def test_routing_starts_new_case_after_two_hours_of_inactivity(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    yekaterinburg = ZoneInfo("Asia/Yekaterinburg")
    first_at = datetime(2026, 8, 10, 10, 0, tzinfo=yekaterinburg)
    first = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="boundary-gap",
            external_chat_id="boundary-gap",
            text="Сколько стоят прыжки?",
            received_at=first_at,
        )
    )
    second = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="boundary-gap",
            external_chat_id="boundary-gap",
            text="А сертификат действует?",
            received_at=first_at + timedelta(hours=2, minutes=1),
        )
    )

    assert second["case"]["case_id"] != first["case"]["case_id"]
    assert second["context"]["recent_messages"] == [{"role": "user", "content": "А сертификат действует?"}]


def test_routing_keeps_same_case_within_two_hours_on_same_local_day(tmp_path: Path) -> None:
    routing = make_test_routing_service(tmp_path)
    yekaterinburg = ZoneInfo("Asia/Yekaterinburg")
    first_at = datetime(2026, 8, 10, 10, 0, tzinfo=yekaterinburg)
    first = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="boundary-continue",
            external_chat_id="boundary-continue",
            text="Сколько стоят прыжки?",
            received_at=first_at,
        )
    )
    second = routing.handle_inbound(
        InboundMessage(
            channel="telegram",
            external_user_id="boundary-continue",
            external_chat_id="boundary-continue",
            text="А сертификат действует?",
            received_at=first_at + timedelta(hours=1, minutes=59),
        )
    )

    assert second["case"]["case_id"] == first["case"]["case_id"]
    assert second["context"]["recent_messages"][-1] == {"role": "user", "content": "А сертификат действует?"}




def test_direct_llm_leaves_first_reply_greeting_to_output_boundary(tmp_path: Path) -> None:
    class BodyOnlyClient:
        def generate(self, **kwargs):
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j.",
                    "confidence": 0.9,
                    "reason": "grounded_pricing",
                },
                ensure_ascii=False,
            )

    prompt_path = tmp_path / "SYSTEM_PROMPT.md"
    prompt_path.write_text(TEST_PROMPT, encoding="utf-8")
    service = DirectLLMService(client=BodyOnlyClient(), prompt_service=SystemPromptService(str(prompt_path)))

    result = service.respond(
        "Сколько стоит прыжок в тандеме?",
        {"kb_status": "found", "grounding_status": "ready", "answer_context": []},
        first_reply_in_dialogue=True,
    )

    assert result["response_text"] == "Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j."


def test_direct_llm_does_not_duplicate_recognised_model_greeting_on_first_reply(tmp_path: Path) -> None:
    class GreetingClient:
        def generate(self, **kwargs):
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Добрый день! Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j.",
                    "confidence": 0.9,
                    "reason": "grounded_pricing",
                },
                ensure_ascii=False,
            )

    prompt_path = tmp_path / "SYSTEM_PROMPT.md"
    prompt_path.write_text(TEST_PROMPT, encoding="utf-8")
    service = DirectLLMService(client=GreetingClient(), prompt_service=SystemPromptService(str(prompt_path)))

    result = service.respond(
        "Сколько стоит прыжок в тандеме?",
        {"kb_status": "found", "grounding_status": "ready", "answer_context": []},
        first_reply_in_dialogue=True,
    )

    assert result["response_text"] == "Добрый день! Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j."


def test_direct_llm_keeps_model_answer_without_forced_greeting_in_any_dialogue_turn(tmp_path: Path) -> None:
    class BodyOnlyClient:
        def generate(self, **kwargs):
            return json.dumps(
                {
                    "route": "answer",
                    "response_text": "Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j.",
                    "confidence": 0.9,
                    "reason": "grounded_pricing",
                },
                ensure_ascii=False,
            )

    prompt_path = tmp_path / "SYSTEM_PROMPT.md"
    prompt_path.write_text(TEST_PROMPT, encoding="utf-8")
    service = DirectLLMService(client=BodyOnlyClient(), prompt_service=SystemPromptService(str(prompt_path)))

    first = service.respond(
        "Сколько стоит прыжок в тандеме?",
        {"kb_status": "found", "grounding_status": "ready", "answer_context": []},
        first_reply_in_dialogue=True,
    )
    followup = service.respond(
        "А можно подарить сертификат?",
        {"kb_status": "found", "grounding_status": "ready", "answer_context": []},
        conversation_context={"recent_messages": [{"role": "assistant", "content": first["response_text"]}]},
        first_reply_in_dialogue=False,
    )

    assert first["response_text"] == "Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j."
    assert followup["response_text"] == "Все актуальные цены доступны по ссылке https://vk.cc/cYzS5j."






def test_prompt_runtime_suppresses_internal_envelope_without_canned_fallback(tmp_path: Path) -> None:
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

    assert result["route"] == "retry_pending"
    assert result["response_text"] == ""
