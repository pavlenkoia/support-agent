"""Synthetic output faults through real model, routing and delivery boundaries."""
from __future__ import annotations
from tests.evidence_fixtures import migrate_fixture

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db import Base, make_session_factory
from app.models.message import Message
from app.models.outbound_transport_send import OutboundTransportSend
from app.models.transport_event import TransportEvent
from app.models.vk_turn import VkTurn
from app.services.direct_llm import DirectLLMService
from app.services.inbound_queue import InboundQueue
from app.services.policy import PolicyService
from app.services.routing import RoutingService
from app.services.telegram_gateway import TelegramGatewayService
from app.services.vk_gateway import VKGatewayService


class PromptService:
    def load_system_prompt(self):
        return "Нейтральный тестовый профиль."


class JSONClient:
    def __init__(self, value):
        self.raw = json.dumps(value, ensure_ascii=False)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            return json.dumps({"action": "finalize", "response_intent": "social_reply", "reason": "selection_complete"})
        return self.raw


def reply(**changes):
    return {"route": "social_reply", "response_text": "Спасибо!", "reason": "model_reason", **changes}


def call_boundary(value, boundary):
    # These are three paths into the one writer, not three text-generating
    # selectors. Keep every malformed/nullable final JSON case on each path.
    direct = DirectLLMService(client=JSONClient(value), prompt_service=PromptService())
    observations = []
    if boundary == "begin_turn":
        selection = direct.begin_turn(text="Реплика", context={})
        assert selection["kind"] == "finalization_requested"
    elif boundary == "continue_after_tool":
        observation = {"tool": "calendar_lookup", "status": "ready", "summary": "Календарный факт"}
        selection = direct.continue_after_tool(
            text="Реплика", context={}, tool_name="calendar_lookup", tool_call_id="synthetic-call",
            tool_request={"date_expression": "2026-01-01", "requested_calendar_fact": "weekday"}, observation=observation,
        )
        assert selection["kind"] == "finalization_requested"
        observations = [observation]
    return direct.respond("Вопрос", migrate_fixture({"grounding_status": "ready", "grounded_facts": ["Факт"]}), tool_observations=observations)


INVALID = [
    [], None, 42, True,
    reply(response_text=None), reply(response_text={"text": "x"}), reply(response_text=["x"]),
    reply(response_text=12), reply(response_text=""), reply(response_text=" \n "),
    reply(route="unknown"), reply(route=None), reply(route=["answer"]), reply(route="answer "),
    reply(confidence=True), reply(confidence=False), reply(confidence="0.7"),
    reply(confidence="unknown"), reply(confidence=float("nan")), reply(confidence=float("inf")),
    reply(confidence=float("-inf")), reply(confidence=-0.1), reply(confidence=1.1),
    reply(confidence=10**400), reply(response_text="KnowledgeBase result: internal"),
]


@pytest.mark.parametrize("boundary", ["begin_turn", "continue_after_tool", "respond"])
@pytest.mark.parametrize("value", INVALID)
def test_invalid_model_output_is_textless_with_validation_reason(value, boundary):
    result = call_boundary(value, boundary)
    assert result["route"] == "retry_pending"
    assert result["response_text"] == ""
    assert result["reason"] == "invalid_finalizer_output"


@pytest.mark.parametrize("boundary", ["begin_turn", "continue_after_tool", "respond"])
@pytest.mark.parametrize("confidence", ["absent", None, 0, 1, 0.5])
def test_nullable_confidence_is_not_a_retry(confidence, boundary):
    value = reply()
    if confidence != "absent":
        value["confidence"] = confidence
    result = call_boundary(value, boundary)
    assert result["route"] == "social_reply"
    assert result["response_text"] == "Спасибо!"
    assert result["confidence"] == (None if confidence == "absent" else confidence)
    assert result["reason"] == "model_reason"


@pytest.mark.parametrize("route", ["answer", "social_reply", "cannot_answer", "out_of_scope", "clarification_requested"])
def test_supported_client_routes_are_not_renamed(route):
    assert call_boundary(reply(route=route), "respond")["route"] == route


@pytest.mark.parametrize("method", ["finalize_customer_text", "finalize_simple_customer_text"])
def test_policy_preserves_long_multiline_text_and_is_idempotent(method):
    policy = PolicyService()
    finalize = getattr(policy, method)
    body = "Описание. " * 150 + "\n\nТолько при выполнении условия."
    first = finalize(body, first_reply_in_dialogue=True)
    assert first == "Здравствуйте! " + body
    assert finalize(first, first_reply_in_dialogue=True) == first
    assert finalize(body, first_reply_in_dialogue=False) == body


@pytest.mark.parametrize("value", [None, {}, ["text"], 123])
@pytest.mark.parametrize("method", ["finalize_customer_text", "finalize_simple_customer_text"])
def test_policy_never_stringifies_non_text(value, method):
    assert getattr(PolicyService(), method)(value, first_reply_in_dialogue=True) == ""


class ReadyWikiTurn:
    """Only tool acquisition is fake; real routing calls the real finalizer."""
    def run(self, **kwargs):
        return {
            "finalization_requested": {"response_intent": "answer", "reason": "selection_complete"},
            "kb_result": migrate_fixture({"grounding_status": "ready", "grounded_facts": ["Факт"], "source_refs": []}),
            "tool_observations": [], "trace": {"actions": []}, "llm_trace": [],
        }


class RecordingRouting(RoutingService):
    def handle_inbound(self, payload, **kwargs):
        result = super().handle_inbound(payload, **kwargs)
        self.last_result = result
        return result


class NoNetworkVKClient:
    def get_users(self, *args, **kwargs):
        return {"ok": True, "response": []}

    def get_history(self, *args, **kwargs):
        return {"ok": True, "response": {"items": []}}


class RecordingSender:
    def __init__(self):
        self.messages = []

    def send_chat_action(self, *args):
        return {"ok": True}

    def send_message(self, chat_id=None, text="", *, peer_id=None, random_id=None):
        self.messages.append(text)
        return {"ok": True, "sent": True, "external_message_id": "1001"}


def run_delivery(tmp_path, channel, value, *, wiki=False):
    factory = make_session_factory(f"sqlite+pysqlite:///{tmp_path / 'isolated.db'}")
    Base.metadata.create_all(bind=factory.kw["bind"])
    client = JSONClient(value)
    routing = RecordingRouting(
        session_factory=factory, answer_engine_mode="agent_tool_loop",
        direct_llm=DirectLLMService(client=client, prompt_service=PromptService()),
        **({"turn_service": ReadyWikiTurn()} if wiki else {}),
    )
    sender = RecordingSender()
    now = datetime.now(UTC)
    if channel == "telegram":
        queue = InboundQueue(lambda *args: None, quiet_seconds=0, max_wait_seconds=0, autostart=False)
        gateway = TelegramGatewayService(routing=routing, sender=sender, queue=queue, allowed_chat_ids=set())
        queue.processor = gateway._process_generation
        result = gateway.handle_update({"update_id": 1, "message": {
            "message_id": 1, "text": "Синтетический вопрос", "chat": {"id": 123}, "from": {"id": 123},
        }})
        assert result["queued"] is True
        assert queue.flush_due(now=now + timedelta(seconds=60), background=False) == 1
    else:
        gateway = VKGatewayService(
            routing=routing, sender=sender, client=NoNetworkVKClient(), session_factory=factory,
        )
        gateway.handle_event({"type": "message_new", "group_id": 55, "object": {"message": {
            "id": 1, "peer_id": 123, "from_id": 123, "text": "Синтетический вопрос", "date": int(now.timestamp()),
        }}})
        assert gateway.process_due_turns(now=now + timedelta(seconds=60)) == 1
    assert client.calls, "The real model boundary must execute"
    with factory() as session:
        assistants = list(session.scalars(select(Message).where(Message.role == "assistant")))
        persisted = [m.content for m in assistants]
        sends = list(session.scalars(select(OutboundTransportSend)))
        # Telegram retries raw events; current VK retries the durable turn ledger.
        owner = TransportEvent if channel == "telegram" else VkTurn
        statuses = list(session.scalars(select(owner.status)))
    return routing.last_result, sender.messages, persisted, sends, statuses


@pytest.mark.parametrize("channel", ["telegram", "vk"])
@pytest.mark.parametrize("wiki", [False, True])
@pytest.mark.parametrize("value", [[], reply(route="unknown"), reply(response_text={"x": "bad"}), reply(response_text="")])
def test_real_gateway_invalid_output_has_zero_sends_and_assistants(tmp_path, channel, wiki, value):
    result, sent, persisted, sends, statuses = run_delivery(tmp_path, channel, value, wiki=wiki)
    assert result["route"]["route"] == "retry_pending"
    assert result["route"]["reason"] == "invalid_finalizer_output"
    assert result["outcome"]["outcome_payload"]["response_text"] == ""
    assert sent == persisted == sends == []
    assert statuses == ["retry_pending"]


@pytest.mark.parametrize("channel,limit", [("telegram", 4096), ("vk", 9000)])
@pytest.mark.parametrize("wiki", [False, True])
@pytest.mark.parametrize("delta", [-2500, 0, 1])
def test_real_gateway_checks_final_channel_length_without_cutting(tmp_path, channel, limit, wiki, delta):
    suffix = "\n\nТолько при выполнении условия."
    body = "А" * (limit + delta - len("Здравствуйте! ") - len(suffix)) + suffix
    value = reply(route="answer" if wiki else "social_reply", response_text=body, confidence=None)
    result, sent, persisted, sends, statuses = run_delivery(tmp_path, channel, value, wiki=wiki)
    if delta > 0:
        assert result["route"]["route"] == "retry_pending"
        assert result["route"]["reason"] == "output_too_long"
        assert result["outcome"]["outcome_payload"]["response_text"] == ""
        assert sent == persisted == sends == []
        assert statuses == ["retry_pending"]
    else:
        expected = "Здравствуйте! " + body
        assert result["route"]["route"] == "answer"
        assert sent == persisted == [expected]
        assert result["outcome"]["outcome_payload"]["response_text"] == expected
        assert len(expected) == limit + delta
