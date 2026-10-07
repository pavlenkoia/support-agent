"""Opt-in, no-send-friendly Jev turn type and single-fact answer engine."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from app.services.knowledge_bundle import validate_runtime_knowledge_artifact
from app.services.simple_answer_engine import SimpleAnswerEngine

JEV_ENDPOINT = "https://openrouter.ai/api/v1/systemone"
TURN_TYPES = {
    "substantive": "Содержательный запрос по теме поддержки: вопрос, выраженное намерение получить сведения или помощь, либо продолжение такого запроса. Учитывай текст, историю и переданный контекст вложения. Вопросительный знак и вопросительная форма не обязательны.",
    "social": "Только приветствие, благодарность, прощание или подтверждение понимания без нового запроса.",
    "off_topic": "Вопрос вне предмета поддержки, без вопроса по теме поддержки.",
    "flood": "Флуд без содержательного запроса; приветствие, благодарность и подтверждение понимания не флуд.",
}


class JevChoiceClient:
    def __init__(self, api_key: str, *, model: str = "typesafe/jev-1.13", endpoint: str = JEV_ENDPOINT):
        if not api_key:
            raise ValueError("Jev API key required for opt-in mode")
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint

    def choose(self, *, state: dict[str, Any], question: str, criteria: dict[str, str], instructions: str) -> str:
        if not criteria or len(criteria) > 255:
            raise ValueError("invalid Jev choice candidates")
        payload = {"model": self.model, "state": state, "questions": {question: {
            "type": "choice", "instructions": instructions, "criteria": criteria,
        }}}
        request = Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
        }, method="POST")
        for attempt in range(3):
            try:
                with urlopen(request, timeout=45) as response:
                    answer = json.load(response)["answers"][question]
                choice = answer["choice"]
                probabilities = answer["probabilities"]
                if (not isinstance(choice, str) or choice not in criteria or not isinstance(probabilities, dict)
                        or set(probabilities) != set(criteria) or any(
                            not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1
                            for value in probabilities.values()
                        ) or probabilities[choice] <= 0):
                    raise ValueError("invalid Jev choice result")
                return choice
            except HTTPError as exc:
                if exc.code not in {429, 502, 503, 504} or attempt == 2:
                    raise
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError("Jev request exhausted")


class JevAnswerEngine:
    def __init__(self, *, selector: Any, writer: Any, profile_root: str | Path, max_corpus_chars: int = 50_000):
        self.selector = selector
        self.writer = writer
        self.profile_root = Path(profile_root)
        self.max_corpus_chars = max_corpus_chars

    @staticmethod
    def _result(kind: str, text: str = "", refs: list[str] | None = None, **telemetry: Any) -> dict[str, Any]:
        return {"kind": kind, "response_text": text, "source_refs": refs or [], "telemetry": {
            "answer_engine": "jev_selected_fact", **telemetry,
        }}

    def answer(self, *, question: str, history: list[dict[str, Any]], tool_observations: list[dict[str, Any]] | None = None,
               internal_context: dict[str, Any] | None = None) -> dict[str, Any]:
        _ = (internal_context, tool_observations)
        projected_history = SimpleAnswerEngine._project_history(history)
        stage = "profile"
        decision: dict[str, Any] = {}
        try:
            profile = yaml.safe_load((self.profile_root / "profile.yaml").read_text(encoding="utf-8"))
            if not isinstance(profile, dict) or not isinstance(profile.get("scope"), dict):
                raise ValueError("missing support scope")
            state = {"message": question, "history": projected_history,
                     "role": profile.get("role"), "scope": profile["scope"]}
            stage = "turn_type"
            turn_type = self.selector.choose(state=state, question="turn_type", criteria=TURN_TYPES,
                instructions="Определи тип последней реплики в контексте истории. Если есть вопрос по теме поддержки, выбери substantive, даже если есть приветствие. Благодарность и подтверждение понимания не флуд.")
            if turn_type not in TURN_TYPES:
                raise ValueError("invalid turn type")
            if turn_type != "substantive":
                stage = "dialogue_writer"
                user_fields = {"question": question, "turn_type": turn_type,
                    "role": profile.get("role"), "scope": profile["scope"],
                    "output_contract": {"response_text": "short natural reply without new business facts"}}
                if turn_type != "social":
                    user_fields["history"] = projected_history
                raw = self.writer.generate(system_prompt=self._prompt("JEV_DIALOGUE_PROMPT.md"),
                    user_prompt=json.dumps(user_fields, ensure_ascii=False),
                    temperature=0.0, response_format={"type": "json_object"})
                try:
                    text = SimpleAnswerEngine._parse_json_object(raw).get("response_text")
                except json.JSONDecodeError:
                    plain = raw.strip()
                    if turn_type != "social" or not plain or len(plain) > 500 or "\n" in plain or plain.startswith(("{", "[", "```")):
                        raise
                    text = plain
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("invalid dialogue reply")
                return self._result("social_reply" if turn_type == "social" else "out_of_scope", text.strip(),
                                    turn_type=turn_type, logical_llm_call_count=1)

            stage = "knowledge"
            corpus_bytes = (self.profile_root / "kb" / "knowledge-base.v1.json").read_bytes()
            corpus = json.loads(corpus_bytes)
            validate_runtime_knowledge_artifact(corpus, max_chars=self.max_corpus_chars)
            decision["kb_sha256"] = hashlib.sha256(corpus_bytes).hexdigest()
            facts = {fact["id"]: fact for fact in corpus["facts"]}
            criteria = {fact_id: json.dumps({"text": fact["text"], "conditions": fact["conditions"]}, ensure_ascii=False)
                        for fact_id, fact in facts.items()}
            criteria["no_answer"] = "Нет факта, релевантного информационной потребности или намерению клиента, и нет подтверждённого факта о подходящем следующем шаге."
            stage = "fact_choice"
            choice = self.selector.choose(state=state, question="fact", criteria=criteria,
                instructions="Определи основную информационную потребность или намерение клиента по последней реплике с учётом истории и переданного контекста вложения. Выбери ровно один опубликованный факт, который прямо помогает ответить на эту потребность или сообщает подтверждённый способ дальнейшего действия. Явный вопрос не обязателен; выраженный интерес к конкретной услуге — содержательный запрос. Для составного запроса выбери факт по основной потребности. Не трактуй тематически близкий факт как подтверждение частного условия, которого в нём нет. Выбери no_answer только если ни один факт не помогает по установленной потребности и не даёт подтверждённого следующего шага.")
            if choice not in criteria:
                raise ValueError("invalid selected fact")
            if choice == "no_answer":
                decision["selected_fact_id"] = None
                stage = "fallback"
                text = profile["fallback_policy"]["no_answer"]["evidence"]["text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("missing no_answer fallback")
                return self._result("cannot_answer", text.strip(), **decision,
                                    turn_type=turn_type, logical_llm_call_count=0)
            fact = facts[choice]
            decision["selected_fact_id"] = choice
            decision["selected_fact"] = dict(fact)
            stage = "fact_writer"
            selected = {key: fact[key] for key in ("id", "text", "conditions")}
            raw = self.writer.generate(system_prompt=self._prompt("JEV_ANSWER_PROMPT.md"),
                user_prompt=json.dumps({"question": question, "history": projected_history,
                    "selected_facts": [selected],
                    "output_contract": {"response_text": "natural grounded reply limited to selected fact"}
                }, ensure_ascii=False), temperature=0.0, response_format={"type": "json_object"})
            try:
                parsed = SimpleAnswerEngine._parse_json_object(raw)
                content_format = "json"
            except json.JSONDecodeError:
                plain = raw.strip()
                if (not plain or len(plain) > 1200 or "\n" in plain
                        or plain.startswith(("{", "[", "```", "<think>"))):
                    raise
                parsed = {"response_text": plain}
                content_format = "plain"
            text = parsed.get("response_text")
            evidence = parsed.get("evidence")
            if (not isinstance(text, str) or not text.strip() or
                    (evidence is not None and (not isinstance(evidence, list) or len(evidence) != 1
                    or not isinstance(evidence[0], dict) or evidence[0].get("fact_id") != choice))):
                raise ValueError("unverified selected fact reply")
            return self._result("grounded_answer", text.strip(), [choice], **decision,
                                writer_content_format=content_format, turn_type=turn_type, logical_llm_call_count=1)
        except Exception as exc:
            # Transport and schema errors do not mean that knowledge is absent.
            return self._result("retry_pending", contract_error=f"jev_engine:{stage}:{type(exc).__name__}",
                                **decision, logical_llm_call_count=0)

    def _prompt(self, name: str) -> str:
        text = (self.profile_root / name).read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError("empty Jev prompt")
        return text
