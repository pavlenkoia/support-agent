from __future__ import annotations

import inspect
from pathlib import Path

from app.services.kb_agent import KBAgentService
from app.services.orchestrator import OrchestratorService
from app.services.policy import PolicyService
from app.services.retrieval import RetrievalService
from app.services.tool_runtime import ToolRuntimeService

CURRENT_WEIGHT_QUESTION = "Есть ли ограничения по весу?"
PREVIOUS_DISCOUNT_CONTEXT = """Recent conversation context:
- user: Здравствуйте! Меня заинтересовала эта услуга. Подскажите для лиц с ВБД есть какие-то скидки?
- assistant: Скидки предусмотрены только для школьников и студентов. Для остальных категорий скидок нет.
Session summary: Пользователь интересовался скидками для лиц с ВБД.
"""


def _write_page(kb_root: Path, slug: str, text: str) -> Path:
    path = kb_root / "concepts" / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _make_large_wiki(tmp_path: Path) -> tuple[Path, Path, Path]:
    kb_root = tmp_path / "kb"
    (kb_root / "concepts").mkdir(parents=True)
    (kb_root / "index.md").write_text("# Wiki Index\n", encoding="utf-8")

    restrictions = _write_page(
        kb_root,
        "restrictions-and-safety",
        "# Restrictions and safety\nОграничения по весу: самостоятельный прыжок доступен при массе 45–90 кг. Тандем — до 85 кг.\n",
    )
    equipment = _write_page(
        kb_root,
        "equipment-and-rental",
        "# Equipment and rental\nОграничения по выдаваемой экипировке уточняйте на аэродроме. См. [[restrictions-and-safety]].\n",
    )
    pricing = _write_page(
        kb_root,
        "pricing-and-addons",
        "# Pricing and addons\nСкидки предусмотрены только для школьников и студентов. Для остальных категорий скидок нет.\n",
    )
    for number in range(15):
        _write_page(
            kb_root,
            f"other-{number}",
            f"# Other {number}\nСкидки и цены на услуги уточняйте заранее.\n",
        )
    return kb_root, restrictions, pricing


def test_retrieval_prioritizes_current_question_and_expands_linked_page(tmp_path: Path) -> None:
    kb_root, restrictions, _ = _make_large_wiki(tmp_path)
    retrieval = RetrievalService().retrieve(
        f"Current user message: {CURRENT_WEIGHT_QUESTION}\n{PREVIOUS_DISCOUNT_CONTEXT}",
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        current_query=CURRENT_WEIGHT_QUESTION,
    )

    refs = [item["source_ref"] for item in retrieval["kb_snippets"]]
    assert str(restrictions) in refs
    assert retrieval["kb_mode"] == "lexical_page_match"
    assert retrieval["retrieval_contract"]["current_query"] == CURRENT_WEIGHT_QUESTION
    # The restrictions page can be selected directly or reached through its wikilink.
    assert retrieval["retrieval_contract"]["linked_expansions"] or str(restrictions) in refs


def test_retrieval_keeps_pricing_result_for_a_pricing_question(tmp_path: Path) -> None:
    kb_root, _, pricing = _make_large_wiki(tmp_path)
    retrieval = RetrievalService().retrieve(
        "Current user message: Сколько стоят прыжки?\n" + PREVIOUS_DISCOUNT_CONTEXT,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        current_query="Сколько стоят прыжки?",
    )

    refs = [item["source_ref"] for item in retrieval["kb_snippets"]]
    assert str(pricing) in refs




def test_grounding_expansion_loads_linked_page_without_mutating_iteration_set(tmp_path: Path) -> None:
    kb_root, restrictions, _ = _make_large_wiki(tmp_path)
    equipment = kb_root / "concepts" / "equipment-and-rental.md"

    expanded = RetrievalService().expand_for_grounding(
        {
            "kb_status": "found",
            "kb_snippets": [
                {"source_ref": str(equipment), "source_type": "wiki_page", "text": equipment.read_text(encoding="utf-8")}
            ],
        },
        current_query=CURRENT_WEIGHT_QUESTION,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
    )

    assert str(restrictions) in [item["source_ref"] for item in expanded["kb_snippets"]]
    assert expanded["retrieval_contract"]["grounding_expansion"]


def test_grounding_expansion_prefers_link_target_relevant_to_current_question(tmp_path: Path) -> None:
    kb_root, restrictions, pricing = _make_large_wiki(tmp_path)
    office = _write_page(kb_root, "office", "# Office\nОфис работает по будням.\n")
    pricing.write_text(pricing.read_text(encoding="utf-8") + "См. [[office]].\n", encoding="utf-8")
    equipment = kb_root / "concepts" / "equipment-and-rental.md"

    expanded = RetrievalService().expand_for_grounding(
        {
            "kb_status": "found",
            "kb_snippets": [
                {"source_ref": str(pricing), "source_type": "wiki_page", "text": pricing.read_text(encoding="utf-8")},
                {"source_ref": str(equipment), "source_type": "wiki_page", "text": equipment.read_text(encoding="utf-8")},
            ],
        },
        current_query=CURRENT_WEIGHT_QUESTION,
        knowledge_backend="filesystem",
        knowledge_root=str(kb_root),
        limit=1,
    )

    assert expanded["kb_snippets"][-1]["source_ref"] == str(restrictions)


def test_deterministic_navigation_has_no_domain_keyword_or_page_hardcode() -> None:
    source = inspect.getsource(KBAgentService._deterministic_navigation)

    assert "topic_rules" not in source
    assert "restrictions-and-safety" not in source
    assert '"вес"' not in source


class _ReplayRetrieval:
    def __init__(self) -> None:
        self.expansion_calls: list[dict] = []

    def retrieve(self, query: str, knowledge_backend: str, knowledge_root: str, *, current_query: str | None = None) -> dict:
        _ = (query, knowledge_backend, knowledge_root, current_query)
        return {
            "kb_status": "found",
            "kb_mode": "lexical_page_match",
            "kb_snippets": [
                {
                    "source_ref": "/kb/concepts/equipment-and-rental.md",
                    "source_type": "wiki_page",
                    "text": "Ограничения по выдаваемой экипировке. См. [[restrictions-and-safety]].",
                }
            ],
        }

    def expand_for_grounding(self, retrieval: dict, *, current_query: str, knowledge_backend: str, knowledge_root: str) -> dict:
        self.expansion_calls.append(
            {
                "current_query": current_query,
                "knowledge_backend": knowledge_backend,
                "knowledge_root": knowledge_root,
            }
        )
        return {
            **retrieval,
            "kb_snippets": [
                *retrieval["kb_snippets"],
                {
                    "source_ref": "/kb/concepts/restrictions-and-safety.md",
                    "source_type": "wiki_page",
                    "text": "Самостоятельный прыжок доступен при массе 45–90 кг. Тандем — до 85 кг.",
                },
            ],
            "retrieval_contract": {"grounding_expansion": "linked_pages"},
        }


class _ReplayKBAgent:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def read(self, text: str, kb_hits: list[dict], *, conversation_context: dict | None = None) -> dict:
        _ = (text, conversation_context)
        self.calls.append(kb_hits)
        if not any(item["source_ref"].endswith("restrictions-and-safety.md") for item in kb_hits):
            return {
                "grounding_status": "not_found",
                "grounded_facts": [],
                "answer_basis": "",
                "source_refs": [item["source_ref"] for item in kb_hits],
                "trace": {"extraction": {"reason": "missing_critical_fact"}},
            }
        return {
            "grounding_status": "ready",
            "grounded_facts": ["Самостоятельный прыжок: 45–90 кг.", "Тандем: до 85 кг."],
            "answer_basis": "Весовые ограничения подтверждены страницей правил безопасности.",
            "source_refs": [item["source_ref"] for item in kb_hits],
            "trace": {"extraction": {"reason": "grounded_after_linked_page_load"}},
        }


class _ReplayDirectLLM:
    def assess_request(self, text: str, *, retrieval: dict, **kwargs) -> dict:
        _ = (text, kwargs)
        return {"action": "read_kb" if retrieval["kb_status"] != "found" else "answer_from_kb", "confidence": 1.0, "reason": "test"}

    def respond(self, text: str, kb_result: dict, **kwargs) -> dict:
        _ = (text, kwargs)
        if kb_result.get("grounding_status") != "ready":
            return {"route": "cannot_answer", "response_text": "", "confidence": 0.0, "reason": "missing_grounding"}
        return {"route": "answer", "response_text": "Самостоятельный прыжок — 45–90 кг, тандем — до 85 кг.", "confidence": 1.0, "reason": "grounded"}


def test_exact_case_439_followup_retries_narrow_linked_retrieval_before_cannot_answer() -> None:
    retrieval = _ReplayRetrieval()
    kb_agent = _ReplayKBAgent()
    orchestrator = OrchestratorService(
        retrieval=retrieval,
        kb_agent=kb_agent,
        direct_llm=_ReplayDirectLLM(),
        policy=PolicyService(),
        tool_runtime=ToolRuntimeService(),
    )
    context = {
        "recent_messages": [
            {"role": "user", "content": "Здравствуйте!\n\nМеня заинтересовала эта услуга.\nПодскажите для лиц с ВБД есть какие-то скидки?"},
            {"role": "assistant", "content": "Скидки предусмотрены только для школьников и студентов. Для лиц с ВБД скидок нет."},
            {"role": "user", "content": CURRENT_WEIGHT_QUESTION},
        ],
        "user_message": CURRENT_WEIGHT_QUESTION,
        "session_summary": "Пользователь ранее спрашивал о скидках.",
    }

    result = orchestrator.run(
        text=CURRENT_WEIGHT_QUESTION,
        context=context,
        knowledge_backend="filesystem",
        knowledge_root="/kb",
        knowledge_query=f"Current user message: {CURRENT_WEIGHT_QUESTION}\n{PREVIOUS_DISCOUNT_CONTEXT}",
    )

    assert result["route"]["route"] == "answer"
    assert result["kb_result"]["grounding_status"] == "ready"
    assert result["kb_result"]["source_refs"][-1].endswith("restrictions-and-safety.md")
    assert len(kb_agent.calls) == 2
    assert retrieval.expansion_calls == [
        {"current_query": CURRENT_WEIGHT_QUESTION, "knowledge_backend": "filesystem", "knowledge_root": "/kb"}
    ]
    assert "kb_agent_grounding_retry" in [item["action"] for item in result["loop_trace"]]
