# Task 13 Round 9 Pre-Implementation Analysis

## Why prior rounds did not close

Prior fixes followed one concrete review reproduction at a time. The underlying
boundary has five independent dimensions: private authority source, public
state, event identity, event position, and historical order. Green tests proved
the named mutation but did not cover the complete cross-product. Round 9 must
implement invariants for the whole class before closing individual examples.

## Trusted and untrusted state

Trusted within the approved threat model:

- canonical insert-only private authority rows in SQLite;
- the per-run lock while observing the journal and writing a precommit;
- SQLite FULL synchronous commit semantics;
- SHA-256 collision resistance.

Untrusted/recomputable:

- `events.jsonl`, `run.json`, event sequence numbers and identities;
- event projections, frame/projection self-digests;
- the mutable runs-table projection and whole-journal digest index;
- public state, markers, diagnostics, event payloads, and aliases.

Direct coordinated modification of both the private authority JSON and its
stored digest is outside this bounded damage model.

## Binding invariant

Emit schema-v3 selection and winner authority rows. Retain the schema-v2 fields
and add:

- `journal_order_version: 1`
- `activation_predecessor_sequence: activation_event_sequence - 1`
- `activation_predecessor_chain_sha256: <64 lowercase hex>`

Use a domain-separated ordered chain over recomputed canonical frame digests:

```text
H0 = SHA256(domain-genesis || length(run_id) || run_id)
Hi = SHA256(domain-step || H[i-1] || u64be(i) || frame_digest(event_i))
```

Compute the predecessor checkpoint once under the run lock before inserting a
selection or winner precommit. Require the current journal length and projection
event sequence to equal activation minus one. Validate all schema-v3 prefix
commitments in one O(events + authorities) scan before semantic binding,
projection rebuild, public reads, repair adoption, mutable index resync, or
notification reconciliation.

Any deletion, prefix truncation, insertion, duplication, reorder, renumbering,
or rewrite before activation must cause a fixed value-free fail-closed result.
Suffix damage after activation may remain readable only when every returned
event is value-free. Raw precommits remain privacy-only; semantic continuation
still requires the exact activation frame and exact winner projection.

Apply the prefix commitment to both selection and winner rows. Anchoring only
selection would move unauthenticated order into winner/CAS semantics.

Schema policy:

- schema-v3: exact prefix validation and authentic pre-activation behavior;
- schema-v2: never infer/backfill a prefix from the mutable journal; use
  conservative all-run privacy and fail closed for active semantic authority;
- schema-v1: preserve the approved exact compatibility behavior.

## Crash invariants

- Before private commit: no authority or activation.
- No-fence private commit before append: prefix-bound privacy-only precommit;
  exact activation absent, so no semantic authority or CAS.
- Torn activation append: truncate/preserve the torn tail; precommit remains
  inert and the predecessor checkpoint remains valid.
- Journal fsync before run.json: journal transition authority rebuilds only
  after prefix validation.
- Fenced journal fsync before SQLite commit: transaction rollback leaves public
  recovery without private authority and must fail closed; never backfill.
- Winner precommit before completion: unrelated events cannot activate winner
  semantics.
- Winner completion with rolled-back private row: reverse invariant fails and
  no CAS occurs.

## Privacy invariant

Every public projection either fails with a fixed value-free error or contains
none of the exact private values. This includes missing/new session IDs, cache
fingerprints, provider paths/content/history canaries, and exact candidate
containers.

Defense in depth:

1. Strictly reject unexpected top-level journal-frame fields.
2. Recursively remove structural `session_id` and `cache_fingerprint` keys from
   the entire activation-or-later event, not only the projection/payload.
3. Recursively redact authority-known exact values and occurrences inside
   strings throughout the whole public object.
4. Persist only an allowlisted failed-recovery audit/diagnostic shape. Never
   persist raw provider/worker exception text or audit history for a selected
   recovery.
5. Use fixed failure codes/messages for public errors.
6. Keep private candidate/key containers out of every public location.

Normal failed-fresh, cancelled, interrupted, and paused selections are privacy
protected even without a winner. Successful continuation still requires an
exact bound winner.

## Consumers that must share verification/redaction

- status, run list/board/history, attention;
- tail/latest/events-after and every pagination boundary;
- timeline, interactions, attempts, notifications, and recovery evidence;
- authenticated detail/events/evidence/notification APIs and Desktop;
- CLI status/events and Showcase status/report collection;
- NotificationOutbox direct append and raw-journal reconciliation.

`NotificationOutbox._journal_candidates` currently reads raw journal data and
must use the same verified/redacted path or an equivalent centralized helper.
Direct append notifications must not persist raw `last_error`.

## Required RED matrix

Order/persistence:

- delete, prefix-truncate, insert, duplicate, swap, or rewrite pre-activation
  frames, then contiguously renumber and recompute all mutable digests/indexes;
- move a post-activation private payload before activation;
- delete/replace activation; reorder/rewrite suffix; torn activation/suffix;
- no-fence selection and winner precommit with unrelated event at reserved
  sequence;
- fenced journal-first rollback for selection and winner;
- malformed schema-v3 chain fields, wrong predecessor count, wrong run genesis,
  unsupported version, extra fields, bad hex;
- schema-v2 conservative behavior and exact schema-v1 compatibility.

Privacy/diagnostics:

- exact private values in canonical keys, aliases, top-level fields, nested
  maps/lists, messages, warnings, last_error, sibling metadata, artifacts, and
  audit/error text;
- worker audit failure plus direct PermissionError/OSError/ValueError/
  RuntimeError paths;
- success, failed result, thrown exception, timeout, cancellation,
  interruption, pause; typed and schemaless outputs;
- original and substituted canaries;
- pages beginning before, at, and after activation.

Consumers:

- status/list/attention, tail/latest/events-after, all evidence kinds,
  notifications, authenticated API, Desktop inheritance, CLI, and Showcase.

Each case may fail closed or return sanitized output, but no surface may contain
any private canary merely because another surface rejects it.

## Round 9 file ownership

Production:

- `plugins/workflow/store.py`
- `plugins/workflow/executors/ai.py`
- `plugins/workflow/notifications.py`

Tests:

- `tests/plugins/workflow/test_persistent_session_recovery.py`
- `tests/plugins/workflow/test_notifications.py`
- `tests/plugins/workflow/test_desktop_api.py`
- `tests/plugins/workflow/test_cli.py`
- `tests/plugins/workflow/test_showcase_evidence.py`

Evidence:

- tracked `task-13-report.md`

Do not modify the generic worker unless a proven invariant cannot be enforced
at the workflow executor boundary. Stop for any other file.

## Closure conditions

- All Round 8 findings and every matrix sibling have behavioral RED then GREEN.
- Both private authority types authenticate prefix order.
- No public consumer bypasses the centralized verified/redacted path.
- Diagnostics are allowlisted and value-free before durable persistence.
- Ordinary v3 non-recovery and exact unversioned/Hermes-legacy/v1/v2/schema-v1
  behavior remain unchanged.
- Provider replay, cancellation, CAS, finalization, bounded retry, prompt cache,
  and narrow-waist guarantees remain unchanged.
- Complete focused, canonical, expanded, notification, Desktop/API/CLI,
  Showcase, Ruff, and diff gates pass with retries disabled.
