from __future__ import annotations

from app.services.agent_tool_loop import UnifiedTurnService, WikiLookupTool


class RecordingWikiLookup:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def lookup(self, *, text: str, context: dict) -> dict:
        self.calls.append({"text": text, "context": context})
        return {
            "grounding_status": "ready",
            "grounded_facts": ["Запись на тандем доступна через форму."],
            "answer_basis": "Предложить форму записи на тандем.",
            "source_refs": ["compiled/concepts/booking.md"],
        }


def test_unified_turn_always_runs_wiki_before_customer_finalization() -> None:
    class TurnModel:
        def begin_turn(self, *, text: str, context: dict) -> dict:
            raise AssertionError("UnifiedTurnService must not classify or finalize before Wiki")

    wiki = RecordingWikiLookup()
    turn = UnifiedTurnService(model=TurnModel(), wiki_lookup=wiki)

    result = turn.run(text="Спасибо", context={"recent_messages": []})

    assert wiki.calls == [{"text": "Спасибо", "context": {"recent_messages": []}}]
    assert result["kb_result"]["grounding_status"] == "ready"
    assert result["trace"]["actions"] == ["wiki_lookup"]


def test_unified_turn_runs_wiki_after_model_requests_its_tool() -> None:
    class TurnModel:
        def begin_turn(self, *, text: str, context: dict) -> dict:
            _ = (text, context)
            return {"kind": "wiki_lookup", "llm_trace": [{"role": "direct_llm", "step": "customer_turn"}]}

    wiki = RecordingWikiLookup()
    turn = UnifiedTurnService(model=TurnModel(), wiki_lookup=wiki)

    result = turn.run(text="Как записаться на тандем?", context={})

    assert len(wiki.calls) == 1
    assert result["kb_result"]["source_refs"] == ["compiled/concepts/booking.md"]
    assert result["trace"]["actions"] == ["wiki_lookup"]


def test_unified_turn_keeps_wiki_llm_trace_for_operational_audit() -> None:
    class TurnModel:
        def begin_turn(self, *, text: str, context: dict) -> dict:
            raise AssertionError("Wiki must run before the customer-facing model")

    class RetryingWikiLookup:
        def lookup(self, *, text: str, context: dict) -> dict:
            _ = (text, context)
            return {
                "grounding_status": "llm_unavailable",
                "source_refs": [],
                "trace": {
                    "llm_trace": [
                        {"role": "kb_agent", "step": "grounded_extraction", "attempts": 4, "error": "TimeoutError"}
                    ]
                },
            }

    turn = UnifiedTurnService(model=TurnModel(), wiki_lookup=RetryingWikiLookup())

    result = turn.run(text="Вопрос", context={})

    assert result["llm_trace"] == [
        {"role": "kb_agent", "step": "grounded_extraction", "attempts": 4, "error": "TimeoutError"},
    ]


def test_wiki_lookup_tool_preserves_existing_catalog_reader_contract() -> None:
    class Retrieval:
        def retrieve(self, *args: object, **kwargs: object) -> dict:
            return {
                "kb_architecture": "llm_wiki",
                "kb_mode": "llm_wiki_catalog",
                "kb_snippets": [{"source_ref": "index/catalog.json"}],
            }

    class Reader:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def read(self, text: str, hits: list[dict], **kwargs: object) -> dict:
            self.calls.append({"text": text, "hits": hits, **kwargs})
            return {"grounding_status": "ready", "source_refs": ["compiled/concepts/booking.md"]}

    reader = Reader()
    tool = WikiLookupTool(
        retrieval=Retrieval(),
        kb_agent=reader,
        knowledge_backend="filesystem",
        knowledge_root="/tmp/wiki",
    )

    result = tool.lookup(text="Как записаться?", context={"recent_messages": []})

    assert result["source_refs"] == ["compiled/concepts/booking.md"]
    assert reader.calls == [{
        "text": "Как записаться?",
        "hits": [{"source_ref": "index/catalog.json"}],
        "conversation_context": {"recent_messages": []},
        "require_coverage_review": True,
    }]
