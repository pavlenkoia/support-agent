# LLM Wiki Knowledge Contract

**Status:** proposed governing specification — implementation changes require explicit approval.

## 1. Purpose

This contract protects the original Karpathy LLM Wiki design from architectural drift. It applies to the support-agent customer knowledge base, its runtime query path, configuration, tests, and release gates.

The system's knowledge base is not a keyword index, a collection of chunks, or an implementation detail of a specific model. It is a curated, versioned knowledge product.

## 2. Non-negotiable architecture

The normal customer-KB path is:

```text
OKF-compatible compiled wiki
  → compact wiki catalog (index + page cards + authored relations)
  → LLM navigation using the current user turn and dialogue context
  → selective loading of complete cited pages
  → LLM coverage review
  → grounded fact extraction with page citations
  → customer-facing answer constrained to grounded facts
```

### Required meaning of each stage

1. **Catalog:** every compiled page is visible to the navigation model through a compact, source-labelled card. The catalog is not a pre-filtered lexical top-K result.
2. **Navigation:** an LLM selects relevant pages by meaning. The newest customer turn is the primary intent; conversation history is only a contextual disambiguator.
3. **Selective read:** the runtime loads the full text of the selected pages. Page selection and page text must remain traceable by `source_ref`.
4. **Coverage review:** an LLM decides whether the loaded evidence is enough for the requested answer. It may request a bounded number of additional authored linked pages.
5. **Grounded extraction:** an LLM returns only supported facts, source references, and an explicit status.
6. **Answer generation:** customer prose may be generated only when the extraction status is `ready`.

## 3. Explicit prohibitions

The following must not become the normal production KB architecture:

- lexical/substring/token/stem scoring as the authoritative page selector;
- topic dictionaries, page-name boosts, question-specific rules, or hardcoded FAQ branches;
- a silent fallback from failed LLM navigation to lexical retrieval;
- skipping coverage review because a deterministic selector considers its own selection sufficient;
- sending every full KB page to the model as a substitute for wiki navigation;
- treating chunks, embeddings, a filesystem search result, Graphify output, or an LLM summary as canonical knowledge.

A temporary operational safety response is allowed only when it does **not** replace the architecture and is observable, time-bounded, and explicitly approved.

## 4. Safety is separate from navigation

These safety invariants are retained independently of the LLM Wiki design:

| Condition | Required route | Customer reply |
|---|---|---|
| KB transport/provider failure | `retry_pending` followed by existing retry/waiting-human policy | empty during deferred retry |
| Extraction has no confirmed answer-critical fact | `cannot_answer` | approved safe inability response |
| Extraction is `ready` | `answer` | grounded answer only |

A failure of navigation, coverage review, or structured-output parsing is not permission to answer from an unrelated page or to downgrade silently to word search.

## 5. OKF role

Open Knowledge Format (OKF) is the target **knowledge representation and interchange contract**, not a replacement runtime or retrieval algorithm.

The compiled wiki should converge on an OKF-compatible bundle:

```text
markdown documents
+ YAML frontmatter
+ stable source/resource identity
+ title, description, type, tags, timestamp
+ authored wikilinks and provenance
```

Required knowledge layers:

```text
raw/       immutable source material and ingest provenance
compiled/  curated knowledge pages used by runtime
schema/    taxonomy, conventions, validation rules
index/     compact catalog/navigation artifact
```

Derived artifacts — embeddings, search indexes, chunks, summaries, and graph projections — may be rebuilt and must never be the only authority for a customer-facing factual answer.

Migration to exact OKF v0.1 fields must be planned as a compatibility migration with a validator and fixture corpus. Do not claim that OKF alone solves navigation, grounding, or model reliability.

## 6. Graphify role

Graphify is a **codebase architecture and impact-analysis tool** for support-agent. It is not the customer knowledge base and must not be introduced as a substitute runtime fact source.

Use Graphify to:

- map the impact of changes across retrieval, KB-agent, orchestrator, routing, tests, and configuration;
- detect added bypasses around the LLM Wiki contract;
- support code review and release scope checks.

Use OKF wikilinks/provenance for knowledge relationships. A future knowledge-graph projection is optional and derived from the OKF bundle.

## 7. Configuration policy

Production configuration must make the architecture explicit. The trace and effective configuration must identify:

```text
kb_architecture=llm_wiki
navigation_mode=llm
coverage_review_mode=llm
extraction_mode=grounded
```

The following modes are prohibited in production unless Igor explicitly approves a time-bounded exception:

```text
deterministic_navigation
coverage_review_skipped
lexical_page_match as the normal KB path
silent lexical fallback
```

Experimental models and parser-recovery work belong only to an isolated test contour. A model that cannot reliably execute the required navigation/coverage schema is not production-ready for this role.

## 8. Required observability and release gates

Every customer-KB trace must preserve:

- effective architecture mode;
- navigation decision and selected source references;
- loaded full-page references;
- coverage-review decision and any additional pages;
- grounded facts and their citations;
- final route and reason;
- any exception mode and its approval/expiry identifier.

A release affecting KB code, prompt, model, runtime config, or compiled KB schema must include contract tests that prove:

1. catalog mode is used for a normal KB request;
2. LLM navigation, not lexical selection, selected the pages;
3. coverage review ran and can request an additional linked page;
4. the current question wins over irrelevant earlier dialogue context;
5. every customer answer has grounded facts and cited sources;
6. transport failure retains `retry_pending` semantics;
7. no answer is produced after `not_found` grounding;
8. no domain/page/keyword hardcode was introduced.

A correct answer alone is insufficient evidence of conformance: the test must also assert the architecture trace.

## 9. Current-state warning

At the time of this specification, production contains temporary retrieval hardening introduced around case #439. The runtime can use lexical selection because its small-wiki catalog threshold is exceeded, and existing flags can enable deterministic navigation and skip coverage review.

This is a **known non-conforming state**, not the approved target architecture. Do not add further lexical, link-expansion, or retry heuristics as a long-term solution. Preserve independently validated transport and grounded-answer safety semantics while designing a controlled restoration of the contract above.

## 10. Required next-session work

Before implementation:

1. Read this specification, `docs/architecture/overview.md`, `docs/architecture/kb-agent-hardening.md`, and the current runtime configuration.
2. Inventory the actual KB bundle, its page cards, frontmatter, index, wikilinks, provenance, and effective runtime modes.
3. Write a concrete migration design from the current path to the contract in section 2, including an OKF compatibility mapping.
4. Define fixture-based architecture tests and production-safe internal probe acceptance criteria.
5. Submit the design for Igor's approval.

Do not modify production retrieval, prompt flags, model selection, KB schema, or deployment before approval of that migration design.

## 11. Decision log

- The "send all full KB pages in every request" proposal is rejected. It is a fallback around failed navigation, not LLM Wiki.
- The "grow lexical retrieval heuristics" direction is rejected as a target architecture.
- OKF is adopted as the intended representation/interoperability direction, subject to a planned compatibility migration.
- Graphify is retained as code architecture tooling, not as customer knowledge runtime.
