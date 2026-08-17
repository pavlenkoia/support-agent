# Full-corpus natural-answer experiment — concluded

**Status:** concluded on 2026-08-17. Not approved for production.
**Production remains:** `ANSWER_ENGINE_MODE=simple_llm_wiki`.

## Scope and isolation

The experiment compared the current selective Wiki pipeline with an isolated candidate mode named `simple_full_corpus_natural`.

- Customer transport was never used; all runs used `/api/internal/probe`.
- Production configuration, production containers, Telegram, and VK were not changed.
- The candidate code and runtime remained isolated from `/home/tian/support-agent`.
- Candidate results must not be treated as production-parity release evidence.

## Valid replay used for the final comparison

A real eight-turn customer dialogue was selected from persisted **customer-side messages only** before either candidate output was read. Historical assistant replies were deliberately excluded from selection.

The replay preserved the exact ordered customer text. It exercised schedule availability, pricing, a broken link, video examples, an ambiguous follow-up, a short acknowledgement, price confirmation, and training timing.

## Observed result

The candidate performed one LLM call per completed turn and was cheaper/faster in this one replay, but its customer-facing behavior was not accepted:

- it emitted generic `cannot_answer` on contextually answerable follow-ups where the current pipeline produced a natural clarification or grounded continuation;
- it surfaced internal/evidence-oriented wording such as uncertainty/confirmation language rather than customer-natural help;
- it did not demonstrate a quality improvement over `simple_llm_wiki` on the real multi-turn dialogue.

The current `simple_llm_wiki` architecture therefore remains the working baseline:

```text
compact compiled-Wiki catalog/page cards
→ semantic navigation
→ selective full-page reading
→ coverage review
→ grounded fact extraction
→ customer-facing finalizer
```

`simple_full_corpus_natural` is an isolated experiment only. Do not deploy, promote, or use it as a replacement baseline without a new approved evaluation.

## Candidate-contour telemetry and replay controls

The isolated candidate worktree added a probe-only helper that retries the same persisted test inbound without creating a second customer message in probe history. It was not promoted to the canonical production checkout. The replay collector recorded, per attempt:

- route and literal final answer;
- source references;
- wall-clock model time;
- logical LLM-call count and provider-attempt count;
- prompt/completion usage when supplied by the provider;
- sanitized provider error when present.

This is test-contour observability. It does not change production customer retry/delivery behavior.

## Safety boundary

No experiment result authorizes customer-facing wording changes, a production model/engine switch, customer-message replay, or removal of the current selective Wiki grounding boundary.
