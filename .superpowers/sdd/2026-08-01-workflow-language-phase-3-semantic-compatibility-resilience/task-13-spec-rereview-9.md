# Task 13 Fix Round 9 Independent Specification Rereview

## Verdict

**PASS.** Fix Round 9 closes the sole Round 8 specification finding. New
selection and winner authorities independently authenticate their journal
predecessor order, and every schema-v3 authority is checked against that
immutable checkpoint before it can bind recovery semantics or contribute to a
public journal projection. Recomputed contiguous deletion, insertion,
duplication, reorder, renumbering, or rewrite before activation therefore
fails closed instead of moving a private event across a mutable sequence
boundary.

Historical schema-v2 authorities are not upgraded from current journal bytes:
they are excluded from semantic binding and retained only for conservative
privacy. Public journal consumers now share one locked, chain-verified,
value-free projection boundary. The bounded failure and notification changes
also prevent raw selected-recovery diagnostics from becoming durable public
data. Genuine pre-activation data, ordinary v3 behavior, schema-v1 authority
compatibility, and unversioned/Hermes-legacy/normalizer-v1/v2 behavior remain
outside the new activation rule.

Finding counts:

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

There is no blocking concern. Task 13 is specification-complete at the
reviewed identity and may proceed to its next separately authorized handoff.

## Scope and identity

- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Fix base: `d6ed3aeb359a920fe806719b09ca9aaac68756bf`
- Reviewed HEAD: `18b0c8d8697a49ea3beb8c006a110c2994ad0e5f`
- Reviewed tree: `a1634e52267f663a25b98c86e18dce788be75a02`
- Bounded package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/review-d6ed3aeb3..18b0c8d86.diff`
- Package scope: one implementation commit; three production modules, two
  focused test modules, and two retained Task 13 report artifacts.

The branch, HEAD, tree, and clean worktree matched the handoff. I read the
complete repository `AGENTS.md`, approved Phase 3 design, implementation plan,
Task 13 brief, and bounded review package. I inspected the changed authority
codecs and writes, journal chain validation and activation binding, projection
validation/rebuild, public event and notification consumers, selected-failure
projection, the focused tests, and relevant unchanged Task 13 compatibility
paths. Per the user boundary, this was a functional specification rereview
only: I did not perform threat-model analysis, run security/threat-model tests,
or produce exploit/adversarial validation.

## Round 8 finding disposition

### Immutable predecessor-order authority — addressed

Both private authority codecs now emit schema version 3 with
`journal_order_version`, the exact predecessor sequence, and its checkpoint:
selection at `plugins/workflow/store.py:222-252` and winner at
`plugins/workflow/store.py:255-272`. The checkpoint is a domain-separated,
run-bound ordered chain over canonical frame digests
(`plugins/workflow/store.py:842-879`). Field shape, version, predecessor count,
and digest syntax are validated at `plugins/workflow/store.py:882-899`.

The store computes the predecessor checkpoint while holding the run mutation
boundary and requires the journal length to equal the expected predecessor
sequence (`plugins/workflow/store.py:4807-4819`). Selection and winner writes
both consume that checkpoint immediately before their activation frames
(`plugins/workflow/store.py:10097-10118` and
`plugins/workflow/store.py:11911-11932`). The behavioral contract is exercised
for both authority kinds at
`tests/plugins/workflow/test_persistent_session_recovery.py:4197-4250`.

### Recomputed order damage before projection — addressed

All schema-v3 selection and winner checkpoints are verified in one bounded
scan (`plugins/workflow/store.py:902-929`). Binding invokes this validation
before looking up an activation frame, then still requires the exact run,
event type, node, and attempt identity (`plugins/workflow/store.py:4821-4884`).
Projection validation and rebuild obtain only these bound authorities
(`plugins/workflow/store.py:4886-4896` and
`plugins/workflow/store.py:7138-7154`), so semantic continuation cannot obtain
authority from a reordered prefix.

The public journal boundary likewise validates contiguous positions, private
order commitments, exact activation binding, and completion corroboration
before redaction (`plugins/workflow/store.py:8312-8346`). Tail,
events-after, latest, and latest-page consumers inherit that one boundary
(`plugins/workflow/store.py:8348-8445`), while raw-journal notification repair
uses it rather than reparsing unverified events
(`plugins/workflow/notifications.py:490-518`).

The focused matrix rewrites all mutable sequence, projection, frame, journal,
and index digests after prefix deletion, insertion, duplication, reorder, and
moving a later event before activation; each public reader remains value-safe
(`tests/plugins/workflow/test_persistent_session_recovery.py:4340-4386`). It
also covers malformed schema-v3 order fields
(`tests/plugins/workflow/test_persistent_session_recovery.py:4224-4280`),
independent winner-prefix binding
(`tests/plugins/workflow/test_persistent_session_recovery.py:4283-4311`), and
pages starting before, at, and after activation
(`tests/plugins/workflow/test_persistent_session_recovery.py:4314-4337`).

### Schema-v2 privacy-only handling — addressed

Schema-v2 selection and winner rows are explicitly skipped during semantic
binding instead of receiving a checkpoint inferred from mutable history
(`plugins/workflow/store.py:4850-4853` and
`plugins/workflow/store.py:4871-4872`). The unbound authorities are still
supplied to public redaction; an unchained schema-v2 selection activates
conservative all-run privacy (`plugins/workflow/store.py:1092-1110` and
`plugins/workflow/store.py:1193-1199`). The behavior test confirms privacy and
semantic rejection at
`tests/plugins/workflow/test_persistent_session_recovery.py:4460-4501`.
Schema-v1 authorities continue through the existing no-activation branch, so
the fix does not silently redefine that approved historical contract.

### Centralized value-free projection and diagnostics — addressed

The shared event redactor collects exact private authority values, removes
session/fingerprint aliases and private candidate containers recursively, and
replaces authority-known values even when embedded in strings or lists
(`plugins/workflow/store.py:980-1065`). Once an authenticated selection is
active it applies this across the entire event, with conservative removal of
message/error/history/provider-bearing fields
(`plugins/workflow/store.py:1077-1110` and
`plugins/workflow/store.py:1180-1199`). Current framed journals also reject
unexpected top-level fields before use (`plugins/workflow/store.py:6578-6615`).
Whole-event alias, nested-string, list, sibling, candidate, and extra-frame
coverage appears at
`tests/plugins/workflow/test_persistent_session_recovery.py:4389-4457`.

Selected fresh failures now retain no artifacts, use a fixed message, and
allow only bounded operational metadata (`plugins/workflow/executors/ai.py:802-847`).
Direct exception and worker-audit cases prove raw diagnostics do not persist
(`tests/plugins/workflow/test_persistent_session_recovery.py:4504-4575`).
Notification recording applies a recursive value-free payload projection
(`plugins/workflow/notifications.py:148-216`), notification reconciliation
uses the verified journal boundary, and delivery failures persist fixed text
(`plugins/workflow/notifications.py:835-865` and
`plugins/workflow/notifications.py:879-925`). The durable notification test is
at `tests/plugins/workflow/test_notifications.py:96-143`.

### Compatibility and scope — preserved

The new order rule is conditional on schema-v3 private authorities; runs with
no such authority do not enter checkpoint validation
(`plugins/workflow/store.py:909-919`). Genuine events created before selection
remain public under the authenticated position boundary, covered at
`tests/plugins/workflow/test_persistent_session_recovery.py:4046-4080`.
Exact legacy public session projection remains covered at
`tests/plugins/workflow/test_persistent_session_recovery.py:1692-1732`, and
the no-fence selection/winner precommit cases remain inert at
`tests/plugins/workflow/test_persistent_session_recovery.py:3144-3275`.

The production diff is confined to Task 13 order authority, recovery privacy
and diagnostics, and the notification consumer of recovery journals. It adds
no endpoint, Desktop production authority, Phase 4 loop/include behavior,
Phase 5 provider portability, model tool, prompt/history mutation, provider
replay path, or registry-CAS semantic outcome.

## Verification evidence

I relied on the controller's fresh evidence at this exact HEAD, as requested:

- Canonical Task 13 functional gate: **10 files, 360 passed, 0 failed**, flaky
  retries disabled.
- Non-overlapping agent integration, query/API/CLI, scheduler, notification,
  and Showcase siblings: **8 files, 451 passed, 0 failed**, flaky retries
  disabled.
- Static `git diff --check` passed during this rereview; HEAD/tree remained
  exact and the worktree remained clean before the report artifact was added.

I did not rerun broad tests. No narrow execution was necessary because the
bounded diff, surrounding functional paths, focused behavioral matrix, and
reported exact-HEAD evidence resolved the code questions without ambiguity.

## Final disposition

**PASS — 0 Critical, 0 Important, 0 Minor.** The Round 8 immutable-order
finding is closed, no new functional specification breakage was found, and
there is no blocking concern.
