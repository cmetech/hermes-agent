# Task 7 report: Phase 4 ordinary-loop normalization

## Status

DONE. Explicit Archon normalizer v4 now validates and normalizes ordinary loops,
authenticates command-backed prompts through the existing dependency manifest, and
reloads their exact collision-proof sealed bindings from format-2 snapshots. The
current Archon normalizer remains pinned to v3; activation, loop execution, durable
confirmation actions, UI work, and the security-review phase remain intentionally
out of scope.

## Implementation and contract decisions

- Extended the staged Phase 4 loop inventory with `command` and
  `signal_completes`, while the generated current Archon schema still exposes the
  unchanged v3 prompt-only shape.
- Explicit v4 compilation accepts exactly one nonempty `prompt` or `command`, a
  nonempty `until`, integer `max_iterations` from 1 through 100 (excluding booleans),
  boolean `interactive`/`signal_completes`, and no fields outside the approved loop
  shape. An authored interactive loop requires a nonempty string `gate_message`.
- Effective interactivity requires both the expanded root workflow option and the
  loop option to be true. Included child top-level options cannot grant operator
  authority. Signal completion defaults false only for an effectively interactive
  loop and true otherwise; explicit false without an effective operator path fails
  normalization.
- Every v4 loop projects exactly the four approved semantic fields under
  `node_semantics[node_id]["loop"]`. Inline loops project a null command binding.
  Command loops are finalized only after the existing authenticated resource sealer
  assigns the manifest binding, so semantics expose the sealed relative path and
  never the command body.
- Reused the Task 5 shared resource read budget, origin-aware command resolver,
  Markdown/frontmatter parser, final-ID reference rewrite, manifest bindings, and
  compiled-byte sealer. Empty loop-command bodies now fail before admission and
  included command bodies use their child origin plus expanded node IDs.
- Recomputed the normalized language digest, expanded-compilation digest, and
  composite digest after final semantic binding. Format-2 identity reconstruction
  applies the same finalization before comparison, without consulting live source.
- The v4 snapshot reader accepts only the exact loop semantic fields and legal
  combinations. Command bindings must be canonical collision-proof
  `packages/<sha256>/<sha256>/...` paths. Extra/missing fields, illegal confirmation
  states, wrong profile/version attachment, semantic binding changes, and sealed-byte
  tampering fail closed.
- Command prompt bodies remain only in authenticated sealed resource bytes. Tests
  prove they are absent from language semantics and risk summaries. No runtime
  interaction, logging, public metadata, core tool, telemetry, or UI surface was
  added.

## TDD chronology

All Python tests were invoked through `scripts/run_tests.sh`; `pytest` was never
called directly.

1. Initial schema/normalization RED:
   `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -q` reported
   **1 failed** because the inline loop had no v4 `loop` semantic projection
   (`KeyError: "refine"`).
2. Command-loop RED progression, using the same focused file command, first failed
   with `unknown_loop_field` for `command`; after structural staging it failed because
   semantics still held the authored command name instead of the sealed manifest
   binding.
3. Confirmation-default RED, using the same focused file command, reported the
   explicit `signal_completes: false` case did not raise when no effective operator
   path existed.
4. Snapshot RED progression, using the same focused file command, first failed with
   `workflow_language_snapshot_invalid` because the v3-only reader rejected loop
   semantics, then failed with `expanded workflow package identity changed` until
   reload applied the same sealed-binding finalization.
5. Authenticated-resource REDs, using the same focused file command, showed an empty
   loop command body was admitted and an included loop command retained its
   pre-expansion reference. Shared body validation and final-ID rewriting made both
   pass.
6. Language-schema staging RED:
   `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q` exposed
   that making `prompt` optional in the inventory prematurely changed the current v3
   authoring schema. Current-schema filtering now preserves required `prompt` and
   hides the two v4 fields until activation.
7. Final focused GREEN:
   `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py -q` produced
   **39 passed, 0 failed**, including source mutation isolation, sealed-byte tamper
   rejection, malformed semantic payloads, risk non-disclosure, include-origin
   authority, and v1-v3 rejection cases.

## Final verification

1. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_loop_executor.py`
   - **664 passed, 0 failed**.
2. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_security_boundaries.py`
   - **93 passed, 0 failed**.
3. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_phase4_snapshot.py`
   - **145 passed, 0 failed**.
4. `scripts/run_tests.sh tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_language_snapshot.py -q -k 'loop or version_one or version_two or version_three or v1 or v2 or v3'`
   - **72 passed, 0 failed**.
5. `.venv/bin/ruff check plugins/workflow/compilation.py plugins/workflow/dependency_manifest.py plugins/workflow/language.py plugins/workflow/language_schema.py plugins/workflow/scheduler.py plugins/workflow/schema.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_phase4_loops.py`
   - **All checks passed**.
6. `git diff --check`
   - Passed with no whitespace errors.

## Files changed

- `plugins/workflow/language.py` — v4 projection, binding finalizer, and strict v4
  snapshot decoding.
- `plugins/workflow/language_schema.py` — staged loop inventory and activation-aware
  generated schema.
- `plugins/workflow/schema.py` — exact version-aware validation and authenticated loop
  command body interpolation/reference rewriting.
- `plugins/workflow/compilation.py` — finalize manifest loop bindings and derived
  identities.
- `plugins/workflow/dependency_manifest.py` — reject empty loop commands and seal the
  validated/re-written command body.
- `plugins/workflow/scheduler.py` — narrowly required format-2 identity reconstruction
  with the same binding finalizer.
- `tests/plugins/workflow/test_language_schema.py` — staged-inventory/current-v3
  schema regression.
- `tests/plugins/workflow/test_phase4_loops.py` — Phase 4 ordinary-loop contract suite.

No changes were needed in `resources.py`, `trust.py`, `compat.py`, the loop executor,
or the existing language-snapshot test file: their Task 4-6 interfaces already
provided the required authority and the new focused suite exercises those paths.
The `scheduler.py` and `dependency_manifest.py` edits are narrowly adjacent to the
original ownership because format-2 identity reconstruction and the Task 5 sealer are
the only authorities able to finalize and reload immutable command bindings.

## Self-review and concerns

- Confirmed `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07] == 3` and explicit v1-v3
  source/snapshot behavior remains green.
- Confirmed command-backed semantics contain only the sealed binding, never command
  bytes, and risk projection does not disclose those bytes.
- Confirmed source edits after admission cannot affect reload and sealed-byte edits
  produce `workflow_snapshot_integrity_mismatch`.
- Confirmed included child interactivity cannot override the expanded root option and
  included commands resolve only through the child's authenticated origin.
- Confirmed no Task 8+ durable interaction/runtime, security-review, activation, core
  tool, prompt schema, telemetry, or UI work entered the diff.
- No known product defects or baseline failures remain in Task 7. Runtime consumption
  of these normalized semantics is intentionally deferred to its later planned task.
