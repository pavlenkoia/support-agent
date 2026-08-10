# Session boundaries and geography guardrails

## Goal
Prevent stale customer dialogue from being reused in a new interaction and prevent the support agent from treating a customer's city as a service location.

## Session boundary
All timestamps are interpreted in `Asia/Yekaterinburg`.

For an inbound customer message, start a new support case with empty dialogue context when either condition is true:

1. its local calendar date differs from the latest message in the current case; or
2. more than two hours elapsed since the latest message in the current case.

The prior case remains stored for history. It is not deleted or passed to the model. A message within the same local day and within two hours continues the current case.

A new case is a first reply, so the customer-facing greeting contract applies.

## Geography rule
The customer-facing system prompt must require direct KnowledgeBase confirmation for every claimed service location. When a customer asks about a city, the answer must address the confirmed place of service, not infer availability from where customers may live or travel from.

The prompt must prohibit claims that services are available in a city unless that city is explicitly confirmed by the knowledge base. City facts belong in the knowledge base, not in the system prompt.

## Acceptance criteria
- A same-chat message on the next local day receives a new `case_id` and has no prior-case messages in its context.
- A same-day message sent more than two hours after the last case message receives a new `case_id` and no prior-case context.
- A same-day message sent within two hours remains in the current case.
- `SYSTEM_PROMPT.md` contains the explicit no-city-substitution rule and confirms that services/certificates are used only in Chelyabinsk at Kalachevo airfield.
