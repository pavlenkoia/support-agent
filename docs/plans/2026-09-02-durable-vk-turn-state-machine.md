# Durable VK turn state-machine replacement

## Goal

Replace the current split ownership of a VK customer turn (in-memory `InboundQueue`, `transport_events`, `conversation_transport_states`, and separate retry delivery) with one database-backed turn record and one delivery state machine.

## Invariants

1. Every accepted `message_new` is durably written to `transport_events` and `messages` before any response work.
2. A `vk_turn` is the only owner of grouping, due time, claim, currentness, retry, and terminal status.
3. A turn is atomically claimed by one worker. A lease expiry permits recovery; an expired claimant cannot create an outbound intent or send.
4. Normal processing and retry use the same turn processor and the same outbound intent.
5. A turn has at most one `outbound_transport_sends` row and one stable VK `random_id`.
6. New inbound joins the open turn before claim; after claim it supersedes the prior turn. A human override suppresses the current open/claimed turn.
7. Viewer is a projection of already persisted messages and never waits for delivery or retry.
8. Long Poll ingests all available updates before selecting due turns.

## Durable states

`open` → `claimed` → `awaiting_send` → `sent`.

Additional terminal/recovery states:

- `retry_pending`: technical generation or delivery recovery, with next due time;
- `suppressed`: newer inbound or human takeover;
- `failed`: permanent transport failure.

A claim stores a token and lease deadline. Every transition and outbound-intent creation requires the active token. A lease recovery transitions only the matching expired claim back to `retry_pending`.

## Implementation slices

1. Migration/model/repository functions plus unit tests for atomic create/join/claim/expiry/suppression.
2. VK ingress writes events/messages and opens/extends a durable turn; remove VK dependence on `InboundQueue` for correctness.
3. One worker turn processor uses the same intent for initial and retry paths; remove duplicate normal/retry send logic.
4. Move polling order to `ingest → recover expired turns → process due turns` and add adversarial concurrency tests.
5. Migrate existing nonterminal VK transport events conservatively; production parity tests, independent reviews, release receipt, and no-send replay.

## Safety

No historical event is replayed to VK during migration. Existing historical records remain audit data. Production deployment is blocked unless all nonterminal legacy events are classified and the new worker proves one-send behavior with fake sender tests.
