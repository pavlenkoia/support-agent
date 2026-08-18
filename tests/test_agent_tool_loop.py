from __future__ import annotations

from app.services.agent_tool_loop import AgentLoopService, WikiLookupTool


class ScriptedAgent:
    def __init__(self, actions: list[dict]) -> None:
        self.actions = list(actions)
        self.calls: list[dict] = []

    def next_action(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return self.actions.pop(0)


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


def test_unrecognized_agent_action_finishes_tool_phase_without_retries() -> None:
    agent = ScriptedAgent([{"action": "social_reply", "reason": "model returned natural action"}])
    wiki = RecordingWikiLookup()
    loop = AgentLoopService(agent=agent, wiki_lookup=wiki)

    result = loop.run(text="Спасибо", context={})

    assert len(agent.calls) == 1
    assert wiki.calls == []
    assert result["kb_result"] == {}
    assert result["trace"]["actions"] == ["tool_not_used:unsupported_action"]


def test_wiki_action_returns_grounding_for_finalizer() -> None:
    agent = ScriptedAgent([{"action": "wiki_lookup"}])
    wiki = RecordingWikiLookup()
    loop = AgentLoopService(agent=agent, wiki_lookup=wiki)

    result = loop.run(text="Как записаться на тандем?", context={})

    assert len(agent.calls) == 1
    assert len(wiki.calls) == 1
    assert result["kb_result"]["source_refs"] == ["compiled/concepts/booking.md"]
    assert result["trace"]["actions"] == ["wiki_lookup"]


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
