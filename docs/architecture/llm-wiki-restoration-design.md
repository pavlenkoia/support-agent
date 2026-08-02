# LLM Wiki restoration — migration design

**Status:** implemented and deployed to production on 2026-07-28. This document remains the migration and release-evidence record.

**Governing specification:** [`llm-wiki-knowledge-contract.md`](llm-wiki-knowledge-contract.md).

## Delivery record — 2026-07-28

- Approved compiled candidate validated against the production profile's byte-identical `raw/` layer.
- Production profile now contains `compiled/`, `schema/`, and `index/`; legacy root pages and `raw/` were preserved.
- Runtime release `d03ca8872233a7ed5bd3ec8fc050c76e77d8ac1d` rebuilt and force-recreated `app`, `worker`, and `vk-worker`; their runtime hashes matched the repository.
- Isolated and post-deploy internal probes confirmed `llm_wiki → LLM navigation → LLM coverage review → grounded extraction`, with citations and no customer transport.
- Historical case `439`, previously `cannot_answer`, was replayed only through `internal/probe` and returned a grounded answer citing `compiled/concepts/restrictions-and-safety.md`.

## 1. Decision and scope

Restore the customer KB path to the contract sequence:

```text
OKF-compatible compiled wiki
→ complete compact catalog
→ LLM navigation from the current customer turn
→ selective full-page read
→ LLM coverage review (bounded authored links only)
→ grounded extraction with citations
→ answer only for grounding_status=ready
```

The migration is a **representation compatibility migration plus a separately gated runtime restoration**. OKF-compatible storage does not itself select pages, establish grounding, or make a model reliable.

### Non-goals and hard boundaries

- No change to production retrieval, prompts/flags, model, KB schema, or deployment before Igor approves this design.
- No lexical selector, keyword/topic dictionary, page-name boost, hardcoded FAQ branch, or silent lexical fallback in the target customer-KB path.
- No “all full pages in every request” fallback.
- Graphify remains code-impact tooling only; it is never a customer fact source.
- Existing safety routes remain independent: provider/transport failure → `retry_pending`; insufficient confirmed facts → `cannot_answer`; only `ready` → `answer`.

## 2. Observed baseline (read-only inventory)

| Surface | Observed state | Conformance implication |
|---|---|---|
| Production app container | `mistral-medium-latest`; `KB_AGENT_DETERMINISTIC_NAVIGATION=true`; `KB_AGENT_SKIP_COVERAGE_REVIEW=true`; minimal extraction enabled | Known non-conforming temporary mode under the governing contract. |
| Retired historical OpenRouter experiment | `gemma4:12b` was used with the same deterministic-navigation and coverage-skip flags | The runtime root and Docker project were removed on 2026-08-02. It is historical evidence only, not a runnable test contour or promotion candidate. |
| KB mount | Read-only `/home/tian/support-agent-profiles/parachute` → `/data/profile` | Existing runtime source remains untouched until an approved rollout. |
| Current bundle | 17 Markdown files: 4 `raw/` files, 10 runtime pages (8 concepts, 2 entities), root `SCHEMA.md`, `index.md`, and legacy runtime folders | It has useful LLM-Wiki content, but not the required `compiled/`, `schema/`, `index/` layer separation. |
| Metadata / relations | All 10 runtime pages have frontmatter; 42 authored wikilinks; raw sources have `source_url`, `ingested`, `sha256` | A lossless compiler/validator migration is feasible. |
| Current runtime implementation | The catalog path is bypassed once page/character thresholds are exceeded; the fallback path lexically scores snippets and expands links before the KB agent | The threshold and lexical path must not be retained as the normal restored architecture. |

The observed source tree is a baseline, not a license to rewrite the mounted production KB.

## 3. Target bundle and identity contract

The target is one immutable-versioned bundle with four logical layers:

```text
bundle/
├── raw/                         # immutable source material + ingest provenance
├── compiled/                    # curated runtime pages
│   ├── concepts/
│   ├── entities/
│   ├── comparisons/
│   └── queries/
├── schema/                      # taxonomy, conventions, validator policy
└── index/                       # compact catalog and compiler manifest
```

### Stable identity rule

`source_ref` becomes a bundle-relative, POSIX-normalized identity, for example:

```text
compiled/concepts/restrictions-and-safety.md
```

It must not be an absolute host/container path. The same compiled page therefore has one identity in fixtures, the isolated contour, and production. The compiler records the mapping from legacy absolute/runtime paths only in a migration report; runtime traces use the stable target identity.

### Catalog rule

The catalog contains **one card for every compiled page**, not a lexical top-K. Each card has at least:

- stable `source_ref`;
- title;
- concise authored description/summary;
- page type, tags, and update timestamp;
- authored outbound relation identities;
- provenance references available from page frontmatter.

A card may be compact, but it may not omit a page because of a user query. The navigation LLM receives the whole catalog; it chooses the initial bounded read set. Full-page text is loaded only after that choice.

## 4. OKF compatibility mapping

This is a semantic compatibility map. Before implementation, the exact pinned upstream **OKF v0.1** schema/field names and validator source must be recorded in the implementation PR; no field name is guessed from this document.

| Current representation | Target OKF-compatible representation | Migration/validation rule |
|---|---|---|
| `raw/**.md` with `source_url`, `ingested`, `sha256` | `raw/**` immutable resource with provenance metadata plus manifest `raw_resources[]` | Preserve raw bytes and declared `sha256` unchanged. Record both `declared_source_sha256` and recomputed `content_sha256` in the manifest; validator blocks any later change to either representation. A declared-vs-content mismatch is an explicit legacy compatibility record, never a silently rewritten source hash. |
| Root `SCHEMA.md` | `schema/SCHEMA.md` plus `schema/compatibility-taxonomy.json` | Preserve the source schema unchanged. Only an explicit migration extension records legacy page tags absent from it; validator treats the union as the candidate taxonomy and reports the extension. No source schema rewrite or free-form runtime tag expansion. |
| Root `index.md` | `index/catalog.*` plus a compiler manifest | Rebuild from compiled pages; verify every compiled page has exactly one catalog card. The old index is input/provenance, not runtime authority. |
| `concepts/`, `entities/`, `comparisons/`, `queries/` | `compiled/<same-type>/` | Preserve page body and logical type; relocation is recorded in the manifest. |
| `title` | required title/display metadata | Non-empty and unique within the compiled bundle. |
| `created`, `updated` | temporal metadata | Parseable dates; migration must not silently invent or overwrite them. |
| `type` | resource/page type | Must be one of the approved compiled types. |
| `tags` | taxonomy labels | Every tag must be declared in `schema/`; unknown tags fail validation. |
| `sources` | provenance/resource relation | Every listed source resolves to an immutable `raw/` resource or an explicitly versioned external source record. |
| `confidence`, `contested`, `contradictions` | quality/contradiction metadata | Preserve without converting a contested page into established fact. |
| `[[wikilink]]` | authored relation from stable source identity to stable source identity | Resolve at compile time; unresolved/ambiguous links block the bundle. Runtime may follow only these authored relations in coverage review. |
| Absolute filesystem `source_ref` emitted today | bundle-relative stable `source_ref` | A compatibility adapter may read legacy pages during staging, but target traces and fixtures use only the stable identity. |

### Compiler outputs and immutability

The compiler is deterministic for the same input bundle and produces:

1. compiled pages;
2. a complete catalog;
3. a manifest with compiler/version/input hashes/page identities;
4. a validation report.

Derived embeddings, chunks, search indexes, summaries, and graph projections are optional rebuildable artifacts. They are excluded from the factual-answer authority chain and cannot be the sole basis of a customer answer.

## 5. Migration sequence and approval gates

| Phase | Work | Runtime / production effect | Exit gate |
|---|---|---|---|
| 0. Freeze and baseline | Capture the read-only inventory, fixture corpus, current trace examples, and source hashes. | None. | Baseline is reproducible and no production artifact changed. |
| 1. Compatibility compiler | Implement bundle compiler + validator against a copied fixture corpus only. Produce `compiled/`, `schema/`, `index/`, manifest, and migration report. | None. No mounted production KB write. | Lossless/provenance/link/identity tests pass. |
| 2. Restored runtime in isolated contour | Make catalog loading unconditional for an approved compiled fixture bundle. Enable LLM navigation and LLM coverage review there only, using the current production KB-agent model family/model. | Isolated test contour only; no production flag/model/prompt change. | All architecture contract tests and isolated probes pass. |
| 3. Shadow evidence | Run the approved internal probe corpus against the candidate bundle/release without customer delivery. Preserve complete traces for review. | No customer transport; test sessions only. | Igor reviews traces, citations, failure behavior, and bundle report. |
| 4. Controlled production rollout | One approved release changes the production bundle mount/reference and explicitly declares `llm_wiki`, `llm`, `llm`, `grounded` effective modes. | **Completed 2026-07-28:** production `app`, `worker`, and `vk-worker` rebuilt against the approved compiled bundle; post-deploy internal probe passed. | Future bundle changes still require the same approval and probe evidence. |
| 5. Post-rollout observation | Review only trace-level conformance and safety outcomes for the agreed observation window. | No heuristic tuning during observation. | Igor accepts evidence or explicitly authorizes a bounded remedial action. |

### Rollback principle

Do not automatically restore lexical selection or skip coverage review. If the restored path is unsafe or unavailable, halt rollout expansion and retain the contract safety routes (`retry_pending` / `cannot_answer`). Reverting to the current non-conforming lexical mode requires Igor’s explicit, time-bounded exception with an expiry identifier visible in traces.

## 6. Required runtime contract after restoration

The restored implementation must expose this trace structure for every customer-KB request:

```json
{
  "kb_architecture": "llm_wiki",
  "navigation_mode": "llm",
  "coverage_review_mode": "llm",
  "extraction_mode": "grounded",
  "catalog_source_refs": ["..."],
  "navigation": {"selected_source_refs": ["..."]},
  "loaded_full_page_refs": ["..."],
  "coverage_review": {"status": "enough|need_more_pages", "additional_source_refs": ["..."]},
  "grounded_facts": [{"fact": "...", "source_refs": ["..."]}],
  "final_route": "answer|cannot_answer|retry_pending",
  "exception": null
}
```

The exact wire shape may be refined during implementation, but these semantic fields are mandatory. The existing response strategy/audit trace is the intended carrier; this is an additive observability design, not a second hidden decision channel.

Failure behavior is strict:

- navigation, coverage-review, or structured-output failure: no lexical fallback and no answer from unrelated pages; follow the approved retry/waiting-human policy;
- `grounding_status=not_found`: `cannot_answer`, with no customer answer generated from page text;
- `grounding_status=ready`: every emitted factual answer retains cited stable `source_ref` values.

## 7. Fixture-based contract tests

The fixture corpus must be synthetic, versioned, and independent of customer data. It includes a catalog with at least one cross-page fact, an irrelevant prior-dialogue fact, a deliberately unresolved question, and a transport/provider failure double.

| ID | Test | Required assertion |
|---|---|---|
| CT-01 | Complete catalog | Every compiled fixture page contributes exactly one card; card count equals compiled page count; no query-driven prefilter occurs. |
| CT-02 | LLM navigation | A scripted LLM selects the expected initial stable references. The trace says `navigation_mode=llm`; no lexical scorer/fallback is invoked. |
| CT-03 | Selective full-page read | Only navigation-selected pages are loaded initially; loaded bodies and card choices share the identical stable `source_ref`. |
| CT-04 | Coverage expansion | The coverage LLM requests an authored linked page for a missing answer-critical fact; runtime loads at most the configured bounded number and traces the relation. |
| CT-05 | Current-turn priority | An old dialogue topic conflicts with a new customer turn. Navigation selects the page for the new turn; history is retained only as disambiguation context. |
| CT-06 | Grounded answer | `ready` extraction emits facts with citations; final answer is permitted only from those facts and cited pages. |
| CT-07 | Insufficient grounding | `not_found` extraction yields `cannot_answer`; customer prose contains no invented or uncited fact. |
| CT-08 | Provider/parse failure | Navigation, coverage, and extraction failures each retain `retry_pending` semantics (or the existing explicitly approved deferred policy), have no customer answer, and never invoke lexical fallback. |
| CT-09 | No bypasses | Static/AST policy test rejects normal-path references to lexical scoring, deterministic navigation, coverage skip, topic/page/keyword hardcodes, and silent fallback in the restored KB route. |
| CT-10 | OKF compatibility validator | Hashes, frontmatter, schema tags, page types, stable IDs, catalog completeness, provenance targets, and wikilink resolution all validate. |
| CT-11 | Trace completeness | Every fixture outcome asserts the architecture trace, not only reply text: modes, selected/loaded refs, review decision, citations, route, and exception state. |

Tests must use deterministic scripted clients for architecture assertions. Live-model probes complement them; they do not replace them.

## 8. Internal probe acceptance criteria

Run probes only through the existing `internal_test` / `governor_probe` session API. They must never publish to Telegram, VK, or another customer transport.

### Probe corpus

1. **Single-page factual request** — proves catalog → LLM navigation → one full page → review → grounded answer.
2. **Cross-page request** — initial page is insufficient; coverage review requests a named authored linked page and the answer cites both stable references.
3. **Conflicting-history follow-up** — previous dialogue discusses topic A, newest turn asks topic B; trace selects B.
4. **Unknown fact** — no answer-critical evidence exists; outcome is `cannot_answer`, with no factual reply.
5. **Injected provider failure** — isolated contour only; outcome is `retry_pending` with empty customer text and no lexical/skip trace.

### Per-probe pass conditions

- session records `is_test=true`, `source=support_governor`, and a named scenario;
- trace contains all fields from section 6;
- all navigation and coverage decisions show LLM mode and stable references;
- any coverage addition is an authored relation and stays within the configured bound;
- `answer` occurs only with `ready` plus cited grounded facts;
- no trace contains `deterministic_navigation`, `fallback_lexical`, `lexical_page_match`, `skip_coverage_review`, or an undeclared exception identifier;
- no probe causes a customer-channel transport event;
- all planned probes pass in the isolated contour before an approval request for production rollout.

## 9. Production rollout acceptance criteria

Production rollout may be proposed only after phases 0–3 have passed and Igor has explicitly approved the rollout change.

### Entry criteria

- 100% of CT-01…CT-11 pass on the candidate release;
- compiler manifest and validator report are attached to the release evidence;
- candidate bundle has a reproducible version/hash and complete catalog;
- current production KB-agent model remains unchanged for this migration;
- candidate production effective configuration is reviewed without exposing secrets and declares the four required modes;
- Graphify impact review confirms no new bypass path across retrieval, KB agent, orchestrator, configuration, or tests.

### Canary criteria

- only internal probes initially; no customer transport use;
- all five approved probe scenarios pass on the deployed candidate;
- trace evidence confirms the target path, citations, and safety routes;
- no navigation/coverage parser failure is masked by lexical fallback;
- no uncited factual answer and no `answer` after `not_found` grounding;
- no exception mode unless Igor approved its identifier and expiry in advance.

### Release decision

A correct natural-language answer is insufficient. The production-safe release decision requires the trace, citations, compiler manifest, validator report, contract-test report, and probe report to agree. Any failure blocks expansion and returns the work to the isolated contour; it does not authorize opportunistic production heuristics.

## 10. Implementation handoff after approval

After Igor approves this design, implementation starts in this order:

1. add only fixture/compiler/validator tests and prove the baseline failures;
2. implement the compatibility compiler against copied fixtures;
3. implement restored catalog/navigation/coverage behavior only in the isolated contour;
4. prove CT-01…CT-11 and the internal probe corpus;
5. submit the evidence and the exact production change scope for a separate rollout approval.

No production action follows from this document alone.
