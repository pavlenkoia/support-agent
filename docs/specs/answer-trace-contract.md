# Answer trace contract — stage 2 (C5)

`inbound_processed.payload.trace_packet` is an additive JSON envelope, version
`stage2-c5`. Existing probe event lists and payload dictionaries remain compatible;
no migration, transport, prompt or semantic policy change is required.

## Captured evidence

- `trace_id` identifies one processed turn, not an entire case.
- `source_turn` records case/conversation/channel and the actual inbound event/message IDs.
- `ordered_actions` preserves runtime order by linking each legacy action label to the matching real model call. Trace-local IDs are distinct from provider/native IDs; unknown links stay null rather than guessed.
- `tool_requests` preserves exact accepted request arguments and native call ID in an audit-only side channel. These fields are not injected into observations/prompts.
- `source_refs` identifies the grounded Wiki sources.
- `finalization_input` records an allowlisted projection of the **actual serialized model input**, at its real boundary (`begin_turn`, `continue_after_tool`, or `respond`). It includes current question, dialogue projection and the evidence/tool facts actually passed, where present. It is not reconstructed from a raw KB response after generation.
- Candidate basis is under `finalization_input.data.grounding_evidence.answer_basis` for the Wiki finalizer. Calendar continuation preserves its actual typed `tool_observation`; it does not invent a Wiki basis.
- `model_calls` records provider/model, duration, attempts and usage for actual calls. Metadata/result rows are excluded. Unknown provider attempts stay JSON null; explicit zero stays zero. Parse failures do not double-count a single generation. Calls without provider telemetry are still logical calls.
- `wiki_status` and `evidence_status` are separate: a calendar-only answer can have `not_started` Wiki and `ready` evidence.
- `result` preserves actual outcome kind/reason/confidence and the SHA-256 of the policy-formatted response, without duplicating the customer text.

## Size and privacy

Input projections are limited to 8192 UTF-8 bytes; the full packet to 16384 bytes,
measured using compact JSON. An oversized projection is replaced with an explicit
`overflow` receipt (size, limit and SHA-256), never a plausible truncated input.
Legacy strategy collections also have explicit 16384-byte capture receipts on overflow,
so an omitted large packet cannot leak through a duplicate field.
Top-level status is `incomplete` if finalization input is unavailable/overflow or
the finalizer was not invoked. Whole-packet overflow retains local turn identity and
output correlation. An ordinary reason remains intact; if the reduced packet still
exceeds its budget, only its reason becomes an explicit hash/size overflow receipt.
The exact reason remains in the enclosing audit `route_reason` field. Such traces
are **not complete semantic replay evidence**.

No raw HTTP/model envelopes, prompts, API credentials or hidden reasoning are
captured. The allowlist does retain the question and relevant dialogue text: this
is internal operational data and must not be exposed publicly or copied into a
customer reply. Existing probe/viewer access controls are unchanged.

## Output correlation and compatibility

Before outbound persistence, `result.delivery_status=not_recorded` (or
`not_applicable` for retry_pending). `record_outbound_message` adds an
`answer_delivery_recorded` event with assistant message ID, text hash and trace ID
only when exactly one unmatched same-case trace has that hash. Otherwise
`correlation_status=ambiguous|unavailable`; no match is guessed. The original
packet is immutable. `assistant_recorded` means local persistence, not a claim of
remote delivery: internal no-send probes also persist assistant messages locally.

Legacy payload fields remain present. `route_confidence` is now the actual nullable
value, not fabricated 1.0. Probe HTTP schemas accept the additive payload. The
support-governor get_trace adapter passes the trace dictionary through unchanged.
No governor/profile modification or network call is needed for compatibility.

Validation uses SQLite unit/integration tests plus a separate Docker PostgreSQL
migration and HTTP TestClient/probe JSON roundtrip, plus Viewer test-session
exclusion checks (not a new trace UI). Models and transports
are injected; production and customers are not touched. Stage 3 (multi-tool loop)
and later stages are explicitly outside this change.

## Stage 3 compatibility

`respond` is now the only customer-output boundary. Selector calls use `customer_turn` and `tool_result_selection`; their actual projected inputs remain in `model_calls`, while `finalization_input` belongs to the common writer. If collection fails before the writer, it is explicitly `not_invoked`. Native tool action correlation uses the recorded native decision ID, not positions in a trace that may include Wiki navigation/extraction calls. The `stage2-c5` envelope, byte limits, privacy allowlists and existing delivery correlation remain unchanged.

## Stage 4 candidate terminal projection allowlist

The terminal selector wire input is not writer evidence and not an audit-only placeholder. It contains the existing action rules/schema and factual `selector-terminal/v1` data, and actually replaces the exhausted-budget selector request. Only its factual projection is captured in audit: existing question/dialogue fields plus `protocol_version`, `selector_phase`, `tool_state`, `remaining_tool_calls`, and ordered `tool_exchanges` (`tool_call_id`, `name`, `arguments`, `observation`). Instructions (`task`, `rules`, `required_json_schema`) remain excluded from audit, as do raw HTTP, secrets, chain-of-thought and raw pages.

The actual serialized packet is captured at `continue_after_tool`, step `tool_result_selection`; no calls or attempts are fabricated. Writer input remains a separate `respond` capture. Limits stay 8192 bytes per input capture and 16384 per trace/strategy capture. Overflow carries an explicit hash/size/status, not a silently truncated complete packet, and audit overflow alone does not change a successful answer. This is a candidate contract, not a production-release or semantic-acceptance claim.
