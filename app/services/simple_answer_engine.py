from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.integrations.llm.openai_compatible import LLMRecoveryExhausted
from app.services.knowledge_bundle import BundleValidationError, validate_runtime_knowledge_artifact
from app.services.tool_runtime import ToolRuntimeService


class CorpusTooLargeError(ValueError):
    """The compiled profile corpus cannot be safely supplied as one packet."""


class SimpleAnswerEngine:
    def __init__(
        self,
        *,
        client: Any,
        profile_root: str | Path,
        max_corpus_chars: int = 50_000,
        temperature: float = 0.0,
        preserve_grounded_text: bool = False,
    ) -> None:
        self.client = client
        self.profile_root = Path(profile_root)
        self.max_corpus_chars = max_corpus_chars
        self.temperature = temperature
        self.preserve_grounded_text = preserve_grounded_text

    def build_input(
        self,
        *,
        question: str,
        history: list[dict[str, Any]],
        tool_observations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        corpus = self._load_corpus()
        return {
            "system_prompt": self._load_system_prompt(),
            "question": str(question).strip(),
            "history": self._project_history(history),
            "corpus": corpus,
            "tool_observations": self._project_tool_observations(tool_observations or []),
        }

    def answer(
        self,
        *,
        question: str,
        history: list[dict[str, Any]],
        tool_observations: list[dict[str, Any]] | None = None,
        internal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = internal_context
        packet = self.build_input(question=question, history=history, tool_observations=tool_observations)
        telemetry = {
            "answer_engine": "simple_full_corpus_natural" if self.preserve_grounded_text else "simple_full_corpus",
            "logical_llm_call_count": 1,
            "provider_attempt_count": 0,
            "used_failover": False,
            "failover_count": 0,
            "kb_page_count": packet["corpus"].get("page_count", len(packet["corpus"].get("facts", []))),
            "kb_char_count": sum(len(fact["text"]) for fact in packet["corpus"]["facts"]),
            "history_message_count": len(packet["history"]),
            "tool_observation_kinds": [item["kind"] for item in packet["tool_observations"]],
            "contract_error": None,
        }
        output_contract = (
            {
                "response_text": "non-empty natural customer response",
                "source_refs": ["fact ID"],
                "evidence": [{"fact_id": "fact ID", "quote": "exact fact text substring"}],
                "rule": (
                    "Верни один короткий ответ клиенту. Если последнее сообщение содержит благодарность «спасибо» или «благодарю» "
                    "и не содержит вопроса либо просьбы сообщить сведения, ответь ровно: «Пожалуйста!». "
                    "Не считай вежливое слово «пожалуйста» в вопросе клиента благодарностью. "
                    "Для благодарности не используй базу знаний или историю и не повторяй прошлый ответ. "
                    "Во всех остальных случаях, включая любой вопрос или просьбу, отвечай только по фактам из базы, которые прямо относятся к последнему сообщению. "
                    "Не задавай лишних вопросов, не предлагай следующих шагов и не добавляй рекламу."
                ),
            }
            if self.preserve_grounded_text
            else {
                "kind": "grounded_answer|social_reply|clarification_requested|cannot_answer|out_of_scope",
                "response_text": "string",
                "source_refs": ["fact ID"],
                "evidence": [{"fact_id": "fact ID", "quote": "exact fact text substring"}],
                "grounded_answer": "Use exact quotes only; response_text must be built exclusively from exact source substrings copied from evidence.",
                "grounded_answer_evidence_rule": "For grounded_answer, include 1 to 3 exact evidence items; otherwise choose a non-grounded kind.",
            }
        )
        user_prompt = json.dumps(
            {
                "task": "Сформируй один клиентский ответ строго по контракту.",
                "output_contract": output_contract,
                "question": packet["question"],
                "history": packet["history"],
                "knowledge_base": self._project_runtime_knowledge(packet["corpus"]),
                "tool_observations": packet["tool_observations"],
            },
            ensure_ascii=False,
        )
        try:
            raw = self.client.generate(
                system_prompt=packet["system_prompt"],
                user_prompt=user_prompt,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
        except LLMRecoveryExhausted:
            telemetry.update(self._provider_telemetry())
            return self._result("retry_pending", "", [], telemetry)
        except Exception as exc:
            telemetry.update(self._provider_telemetry())
            telemetry["contract_error"] = f"provider_error:{type(exc).__name__}"
            return self._result("retry_pending", "", [], telemetry)

        telemetry.update(self._provider_telemetry())
        try:
            parsed = self._parse_json_object(raw)
            kind_value = parsed.get("kind")
            kind = str(kind_value).strip() if kind_value is not None else "final_response"
            response_value = parsed["response_text"]
            if not isinstance(response_value, str):
                raise ValueError("invalid_required_fields")
            response_text = response_value.strip()
            source_refs = parsed["source_refs"]
            evidence = parsed.get("evidence")
            if kind not in {"grounded_answer", "final_response", "social_reply", "clarification_requested", "cannot_answer", "out_of_scope"}:
                raise ValueError("invalid_kind")
            if (kind not in {"grounded_answer", "out_of_scope"} and not response_text) or not isinstance(source_refs, list) or not all(
                isinstance(ref, str) for ref in source_refs
            ):
                raise ValueError("invalid_required_fields")
            if kind == "grounded_answer" or (kind == "final_response" and evidence not in (None, [])):
                source_refs, response_text = self._finalize_grounded_answer(parsed, packet, response_text)
                if kind == "final_response":
                    kind = "grounded_answer"
            else:
                if evidence not in (None, []):
                    telemetry["contract_error"] = "unexpected_evidence"
                    return self._result("cannot_answer", "", [], telemetry)
                if source_refs:
                    telemetry["contract_error"] = "unexpected_source_refs"
                    return self._result("cannot_answer", "", [], telemetry)
            if self._contains_forbidden_period_dates(response_text, packet["tool_observations"]):
                telemetry["contract_error"] = "forbidden_exact_dates_for_period"
                return self._result("cannot_answer", "", [], telemetry)
            return self._result(kind, response_text, source_refs, telemetry)
        except json.JSONDecodeError as exc:
            # Some OpenAI-compatible providers return customer text instead of
            # the requested JSON envelope. Preserve that single model output
            # rather than turning a usable reply into a blank retry.
            if self.preserve_grounded_text and isinstance(raw, str) and raw.strip():
                telemetry["contract_error"] = "provider_plain_text"
                return self._result("final_response", raw.strip(), [], telemetry)
            telemetry["contract_error"] = str(exc)
            return self._result("cannot_answer", "", [], telemetry)
        except ValueError as exc:
            telemetry["contract_error"] = str(exc)
            return self._result("cannot_answer", "", [], telemetry)
        except (KeyError, TypeError) as exc:
            telemetry["contract_error"] = f"invalid_envelope:{type(exc).__name__}"
            return self._result("cannot_answer", "", [], telemetry)

    @staticmethod
    def _result(kind: str, response_text: str, source_refs: list[str], telemetry: dict[str, Any]) -> dict[str, Any]:
        return {"kind": kind, "response_text": response_text, "source_refs": source_refs, "telemetry": telemetry}

    def _finalize_grounded_answer(
        self,
        parsed: dict[str, Any],
        packet: dict[str, Any],
        response_text: str,
    ) -> tuple[list[str], str]:
        evidence = parsed.get("evidence")
        if not isinstance(evidence, list) or not evidence or len(evidence) > 3:
            raise ValueError("invalid_evidence_count")
        facts = packet["corpus"].get("facts")
        if not isinstance(facts, list):
            raise ValueError("invalid runtime knowledge facts")
        facts_by_id = {fact["id"]: fact for fact in facts if isinstance(fact, dict) and isinstance(fact.get("id"), str) and isinstance(fact.get("text"), str)}
        if not facts_by_id:
            raise ValueError("invalid runtime knowledge facts")
        pages_by_ref = {fact_id: {"source_ref": fact_id, "content": fact["text"]} for fact_id, fact in facts_by_id.items()}
        validated_quotes: list[str] = []
        normalized_refs: list[str] = []
        seen_quotes: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("invalid_evidence_item")
            source_ref_value = item.get("fact_id", item.get("source_ref"))
            quote_value = item.get("quote")
            if not isinstance(source_ref_value, str) or not isinstance(quote_value, str):
                raise ValueError("invalid_evidence_types")
            source_ref = source_ref_value.strip()
            quote = quote_value.strip()
            if not source_ref or source_ref not in pages_by_ref:
                raise ValueError("invalid_evidence_source_ref")
            if not quote or not self._evidence_quote_matches(quote, pages_by_ref[source_ref]["content"]):
                raise ValueError("invalid_evidence_quote")
            if quote not in seen_quotes:
                seen_quotes.add(quote)
                validated_quotes.append(quote)
            if source_ref not in normalized_refs:
                normalized_refs.append(source_ref)
        # Evidence is the authoritative, validated provenance.  source_refs is
        # redundant model metadata and may be incomplete or ordered differently;
        # derive it from the validated evidence instead of discarding a usable
        # customer answer.
        if self.preserve_grounded_text:
            return normalized_refs, response_text
        return normalized_refs, " ".join(validated_quotes)

    def _evidence_quote_matches(self, quote: str, source_content: str) -> bool:
        if quote in source_content:
            return True
        if not self.preserve_grounded_text:
            return False
        return self._normalize_markdown_presentation(quote) in self._normalize_markdown_presentation(source_content)

    @staticmethod
    def _normalize_markdown_presentation(value: str) -> str:
        without_code_delimiters = re.sub(r"`([^`]*)`", r"\1", value)
        without_list_markers = re.sub(r"(?m)^\s*[-*+]\s+", "", without_code_delimiters)
        return re.sub(r"\s+", " ", without_list_markers).strip()

    def _provider_telemetry(self) -> dict[str, Any]:
        info = self.client.get_last_call_info() if hasattr(self.client, "get_last_call_info") else {}
        usage = info.get("usage") if isinstance(info, dict) else {}
        error_value = info.get("error") if isinstance(info, dict) else None
        return {
            "provider_attempt_count": int(info.get("attempts") or 1) if isinstance(info, dict) else 1,
            "provider_error": str(error_value)[:200] if error_value is not None else None,
            "used_failover": bool(info.get("used_failover")) if isinstance(info, dict) else False,
            "failover_count": int(info.get("failover_count") or 0) if isinstance(info, dict) else 0,
            "llm_duration_ms": info.get("duration_ms") if isinstance(info, dict) else None,
            "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            "completion_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
        }

    @staticmethod
    def _contains_forbidden_period_dates(response_text: str, observations: list[dict[str, Any]]) -> bool:
        if not any(item.get("kind") in {"calendar_period_weekends", "calendar_period_public"} for item in observations):
            return False
        return bool(re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", response_text))

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        candidates = [text]
        fenced = re.search(r"```(?:json)?\\s*(\\{.*?\\})\\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            candidates.append(fenced.group(1))
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start:end + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise json.JSONDecodeError("unable to recover JSON object", text, 0)

    def _load_system_prompt(self) -> str:
        prompt_name = "SIMPLE_ANSWER_PROMPT.md" if self.preserve_grounded_text else "SYSTEM_PROMPT.md"
        prompt_path = self.profile_root / prompt_name
        if not prompt_path.is_file() and self.preserve_grounded_text:
            prompt_path = self.profile_root / "SYSTEM_PROMPT.md"
        return prompt_path.read_text(encoding="utf-8").strip()

    def _load_corpus(self) -> dict[str, Any]:
        artifact_path = self.profile_root / "kb" / "knowledge-base.v1.json"
        if not artifact_path.is_file():
            raise ValueError("missing runtime knowledge artifact")
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

        try:
            validate_runtime_knowledge_artifact(artifact, max_chars=self.max_corpus_chars)
        except BundleValidationError as exc:
            if "invalid size" in str(exc):
                raise CorpusTooLargeError("runtime knowledge artifact exceeds configured limit") from exc
            raise ValueError(str(exc)) from exc
        return artifact

    @staticmethod
    def _project_runtime_knowledge(artifact: dict[str, Any]) -> dict[str, Any]:
        return artifact

    @staticmethod
    def _project_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
        projected: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content and not any(char in content for char in "{}[]"):
                projected.append({"role": role, "content": content})
        return projected[-10:]

    @staticmethod
    def _project_tool_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            kind = str(observation.get("kind") or "").strip()
            summary = str(observation.get("summary") or "").strip()
            structured = observation.get("structured")
            if kind == "calendar_period_weekends":
                projected.append(ToolRuntimeService.project_public_period_observation(observation))
            elif kind and summary:
                projected.append({"kind": kind, "summary": summary, "structured": structured if isinstance(structured, dict) else {}})
        return projected
