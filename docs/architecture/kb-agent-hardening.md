# KB Agent hardening and rollout notes

## Purpose

This note documents the KB-agent hardening work that made the current production model safer to run and reduced the main blocker for future experiments with alternative KB-agent models such as `gemma4:12b`.

The goal is not to replace the LLM Wiki approach. The goal is to keep selective wiki reading while moving the most failure-prone parts behind deterministic or narrower contracts.

## Problem that triggered the hardening

The original KB-agent path relied on several structured-output steps:
1. navigation over wiki cards
2. coverage review over selected pages
3. grounded extraction from full pages

On weaker or less JSON-disciplined models, the first two steps were especially fragile and produced recurring parsing failures such as `navigation_error:JSONDecodeError` and `coverage_review_error:JSONDecodeError`.

That made the end-to-end runtime noisy even when the semantic answer quality was acceptable.

## Hardened runtime controls

The repository now supports the following runtime flags:

- `KB_AGENT_DETERMINISTIC_NAVIGATION`
  - replaces free-form LLM navigation with lexical/topic-weighted deterministic page selection over wiki cards
  - uses the current turn plus recent conversation context to bias page choice
  - keeps the selective full-page read stage intact

- `KB_AGENT_SKIP_COVERAGE_REVIEW`
  - bypasses the extra coverage-review LLM step
  - useful when deterministic navigation already chooses a narrow and relevant page set

- `KB_AGENT_MINIMAL_EXTRACTION_SCHEMA`
  - asks the KB agent for a smaller extraction JSON object
  - keeps required fields focused on `grounding_status`, `answer_basis`, `grounded_facts`, `cited_source_refs`, and `reason`
  - allows runtime normalization to inject `missing_information=[]` when omitted

In addition to the flags above, KB-agent JSON parsing now attempts recovery from:
- fenced JSON blocks
- wrapped text that still contains a valid JSON object
- wrapped text that still contains a valid JSON array/object candidate

## Architectural effect

The hardened path keeps the same broad architecture:
- compiled wiki index + page cards
- selective full-page reads
- grounded fact extraction before customer-facing answer synthesis

What changes is where free-form structured generation is allowed.

### Before hardening
- LLM navigation
- LLM coverage review
- LLM extraction

### After hardening in the stricter mode
- deterministic navigation
- optional coverage-review skip
- LLM extraction with a narrower schema

This reduces the number of structured-output choke points while preserving the LLM Wiki method.

## Why this was safe to roll out for the current production model

The hardened path was validated on the current Mistral production KB-agent model in an isolated test contour before rollout.

Observed outcome from the rollout gate:
- real multi-turn probe cases succeeded without KB-agent JSON failures
- the current production model stayed stronger and faster than the Gemma test candidate on the same path
- the final runtime produced grounded customer-facing answers on pricing, booking, schedule, restrictions, and preparation-time questions

Because the hardening is feature-flagged, production could adopt the new architecture without changing the deployed model family.

## Why this matters for future Gemma experiments

The hardening did not make `gemma4:12b` production-ready by itself.

What it did accomplish:
- remove the largest navigation-format blocker
- shorten the structured path
- make extraction the primary remaining reliability question
- preserve an isolated test contour where Gemma can continue to evolve without touching production

That means Gemma now has a clearer path for future evaluation, but it should still be treated as a test candidate until its extraction reliability and latency are good enough on a broader live probe set.

## Operational guidance

### Recommended production posture
For the current production Mistral KB-agent model, the hardened mode is an acceptable operating shape:
- deterministic navigation: enabled
- coverage-review skip: enabled
- minimal extraction schema: enabled
- bounded timeout/retry/backoff: enabled

### Recommended test posture for alternative models
For experimental KB-agent models, keep a separate contour and evaluate:
- follow-up handling
- strict schedule/policy answers
- out-of-scope behavior
- extraction stability
- latency under live probe traffic

## Files to inspect when changing this area

- `app/services/kb_agent.py`
- `app/core/config.py`
- `tests/test_kb_agent.py`
- external prompt files for the support-agent profile:
  - `SYSTEM_PROMPT.md`
  - `KB_AGENT_PROMPT.md`

## Non-goals

This hardening note does **not** imply:
- that all customer-facing runtime latency is solved
- that every out-of-scope question is perfectly classified
- that Gemma is already approved for production rollover

It documents a safer KB-agent architecture and the rollout posture that was validated for the current production model.
