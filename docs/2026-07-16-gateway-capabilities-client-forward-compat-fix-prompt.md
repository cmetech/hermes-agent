# Task brief — client forward-compatibility fix for gateway `model-capabilities` parsing

> Self-contained implementation brief. Work in the **Hermes client** repo
> (`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`). Do **not** modify the
> OTTO Gateway repo. Ground every change in the real code and its tests; read before editing.
> Follow this repo's normal workflow (GSD / superpowers if applicable).

---

## Problem

The client's capability-catalog parser is **too strict in one specific way**: if the gateway's
`GET /v1/model-capabilities` response contains a capability key outside the four the client knows
(`completion`, `tools`, `vision`, `reasoning`), the parser rejects the **entire** response as
`capability-response-invalid`. That turns a purely *additive* gateway change (e.g. a future
`audio` capability) into a catastrophic client failure: every explicit model becomes all-unknown
and ineligible, leaving only `auto`/main selectable.

A cross-repo integration review confirmed the two sides otherwise agree on the wire contract and
integration is GO. This is the one hardening item on the client side. (The gateway now documents
the coupling in its reference doc §10 and commits not to add capability keys without coordination —
but the client should be robust regardless.)

## The fix (single behavior change)

In `hermes_cli/model_capabilities.py`, function `_parse_catalog_payload` (~lines 90-158), the
per-entry `capabilities` validation (~line 133) currently raises when it encounters a key not in
`CAPABILITY_KEYS`. Change it to **ignore unknown capability keys** instead of rejecting the
response:

- Keep only the four known keys (`CAPABILITY_KEYS`), each defaulting to `"unknown"` when absent and
  still validated against `_CAPABILITY_STATES` when present.
- **Silently drop** any capability key the client doesn't recognize (optionally log at debug). Do
  **not** raise.
- Do **not** relax any other validation. Everything else stays strict: `object == "list"`,
  `registry_revision`/`generated_at` must be strings, `data` a list, `id` non-empty and **unique**
  (keep duplicate-id rejection), `available` a bool, `selection_mode ∈ {"automatic","explicit"}`,
  `explicit ⇒ available is True`, capability *values* in `_CAPABILITY_STATES`, evidence field
  values strings.
- Evidence: `_parse_evidence` (~71-87) may receive an evidence entry keyed by an unknown
  capability. Tolerate it — either drop evidence whose capability isn't in `CAPABILITY_KEYS`, or
  keep it as-is (it's only surfaced to the UI). Pick whichever is cleaner in context; do not let an
  unknown-cap evidence entry raise.

Net effect: a response carrying a 5th capability key parses successfully, the model keeps its four
known states, and the unknown key is ignored — no whole-catalog rejection.

## TDD

1. **Find and update the existing test** that asserts an unknown capability key is rejected (in
   `tests/hermes_cli/test_model_capabilities.py`). Its expectation inverts: an unknown key must now
   parse successfully (status `ready`, the model present) rather than yielding
   `capability-response-invalid`.
2. **Add a new test:** a `data` entry with `capabilities` containing all four known keys **plus** an
   extra (e.g. `"audio":"supported"`) parses without error; assert the four known states are read
   correctly and the model is eligible where expected; assert the unknown key does not appear in the
   stored `capabilities`.
3. Write the test first, watch it fail, then make the change, watch it pass.
4. Confirm you did **not** weaken any other rejection: the existing negative tests for bad `object`,
   non-string `registry_revision`/`generated_at`, non-bool `available`, bad `selection_mode`,
   **duplicate id**, bad capability *state value*, and non-string evidence must all still pass.

## Verify

Run the capability + eligibility + inventory suites with the repo venv:

```
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
./venv/bin/pytest tests/hermes_cli/test_model_capabilities.py tests/hermes_cli/test_model_eligibility.py tests/hermes_cli/test_inventory.py -q
```

(If your invocation differs, discover the correct one and report what you ran. The
`test_web_server.py` messaging-catalog failures are pre-existing and unrelated — don't chase them.)

## Docs

Update the client contract to state the new behavior: in `AGENTS.md` §"Gateway model inventory and
auth invariants" (~lines 849-865), add a line that the client **tolerates additive capability
keys** — it reads the known capability keys and ignores any it doesn't recognize, rather than
rejecting the catalog. This keeps the client and gateway docs consistent (the gateway's reference
doc §10 already describes this coupling).

## Constraints

- Client repo only. Do not touch the gateway. Follow the repo's existing conventions (its
  GSD/superpowers workflow if applicable). Keep the change minimal and focused — this is one
  behavior change plus its tests and a doc line. Report the files changed, the before/after of the
  capability-key handling, and the test results.

## Background context (why this matters)

- Producer side: the gateway emits exactly the four capability keys today
  (`internal/canonical/modelcaps.go`, `RequiredCapabilities`) and enforces it at registry load. The
  gateway's `docs/reference/model_capabilities.md` §10 documents that additive capability keys are a
  breaking change for strict clients and that duplicate IDs are whole-catalog-rejected — the gateway
  already dedupes `auto` and any repeated explicit ID defensively.
- This fix makes the client tolerant of an additive capability key so an accidental or coordinated
  gateway addition degrades gracefully (unknown key ignored) instead of blanking the catalog.
