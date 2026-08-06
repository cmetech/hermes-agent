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

## Fix round 1

### Review findings resolved

1. Current-v3 authoring descriptors no longer advertise the staged Phase 4
   `loop.command` or `loop.signal_completes` fields while the current JSON Schema
   rejects them. One version-aware `_loop_specs()` selector now feeds both
   `_object_schema()` and nested node-kind descriptors.
2. `definition_json_schema()`, `node_kind_descriptors()`, and
   `workflow_authoring_contract()` accept an explicit keyword-only normalizer version
   for staged projections. Their default remains the profile's current normalizer,
   so Archon continues to publish v3 unless a caller explicitly asks for v4.
3. Explicit-v4 loop schema projection now derives boolean `interactive` and
   nonblank-string `gate_message` constraints from the same selected field specs.
   The interactive conditional requires that selected nonblank gate shape. The
   existing v1-v3 permissive/truthiness schema remains unchanged.
4. Explicit-v4 admission now rejects any authored `gate_message` that is not a
   nonblank string, including on a noninteractive loop, keeping loader behavior equal
   to the staged schema. Existing boolean validation for `interactive` and
   `signal_completes` remains the loader authority.
5. Field ordering now resolves projected field specs by their unique `(scope,
   yaml_name)` identity, preserving inventory order when a phase-aware projection
   changes only type/shape metadata.

No normalizer activation, identity/sealing/snapshot, runtime, interaction, Task 8,
UI, tool, or telemetry path changed.

### Authentic RED-GREEN evidence

All Python commands used `scripts/run_tests.sh`.

1. Current-v3 descriptor RED:
   `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q -k phase4_loop_inventory_is_staged_without_changing_current_v3_schema`
   produced **0 passed, 1 failed** because the current contract contained
   `nodes[].loop.command`. After sharing the selector, the same command produced
   **1 passed, 0 failed**.
2. Explicit staged-v4 RED:
   `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q -k explicit_v4_authoring_contract_exposes_staged_loop_fields`
   produced **0 passed, 1 failed** with an unexpected
   `normalizer_version` keyword. After localized version propagation, the combined
   current/staged selector checks produced **2 passed, 0 failed**.
3. Schema/admission parity RED:
   `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q -k explicit_v4_loop_schema_matches_admission_validation`
   produced **4 passed, 3 failed**. Staged schema admitted non-boolean
   `interactive`, whitespace-only gate text, and a numeric authored gate; the loader
   already rejected the first two but also needed to reject the inactive numeric
   gate. After phase-aware shapes and strict authored-gate validation, the focused
   staged/current/legacy matrix produced **19 passed, 0 failed**.

### Fix-round verification

- Complete language-schema suite: **613 passed, 0 failed**.
- Task 7 schema/loop-executor gate: **672 passed, 0 failed**.
- Task 7 manifest/security-boundary gate: **93 passed, 0 failed**.
- Task 7 language/format-2 snapshot gate: **145 passed, 0 failed**.
- Focused v1-v3 source/snapshot matrix: **72 passed, 0 failed**.
- `.venv/bin/ruff check plugins/workflow/language_schema.py plugins/workflow/schema.py tests/plugins/workflow/test_language_schema.py`: **all checks passed**.
- `git diff --check`: passed with no whitespace errors.

### Fix-round files and self-review

- `plugins/workflow/language_schema.py`
- `plugins/workflow/schema.py`
- `tests/plugins/workflow/test_language_schema.py`

Confirmed the default Archon authoring contract still reports normalizer v3 and the
legacy JSON-truthiness matrix remains green. Explicit v4 schema and loader outcomes
now agree for valid interactive/noninteractive loops, non-boolean interaction and
signal fields, missing/blank gates, and non-string authored gates. No known concern
remains from these two findings.

## Fix round 2

### Convergence finding resolved

The three public versioned authoring projections now reuse
`language.select_normalizer_version()` through `_authoring_normalizer_version()`.
They therefore share admission's complete version authority: supported integer
membership, boolean rejection, profile defaults, and the rule that normalizer v3 or
greater requires `archon-2026-07`. The removed local check can no longer drift from
admission or emit an impossible `hermes-legacy` v4 schema/descriptor/contract pair.

The supported projection matrix remains unchanged: default Archon emits current v3,
explicit Archon v4 remains available only as a staged projection, and legacy v1/v2
remain valid. No normalizer activation, identity, sealing, snapshot, runtime,
interaction, UI, tool, telemetry, or Task 8 path changed.

### Authentic RED-GREEN evidence

- RED:
  `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q -k versioned_authoring_projections_reject_impossible_profile_pair`
  produced **0 passed, 3 failed**. `definition_json_schema()`,
  `node_kind_descriptors()`, and `workflow_authoring_contract()` all failed to raise
  for `hermes-legacy` plus normalizer v4.
- GREEN: the same command produced **3 passed, 0 failed** after delegating to the
  authoritative selector.
- Supported projection matrix:
  `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q -k 'versioned_authoring_projections_reject_impossible_profile_pair or legacy_authoring_contract_preserves_supported_versions or explicit_v4_authoring_contract_exposes_staged_loop_fields or archon_authoring_contract_is_bounded_and_versioned'`
  produced **7 passed, 0 failed**, covering Archon current v3/staged v4 and legacy
  explicit v1/v2.

### Fix-round verification

- Complete language-schema suite: **618 passed, 0 failed**.
- Task 7 schema/loop-executor gate: **677 passed, 0 failed**.
- Focused v1-v3 source/snapshot matrix: **72 passed, 0 failed**.
- `.venv/bin/ruff check plugins/workflow/language_schema.py tests/plugins/workflow/test_language_schema.py`: **all checks passed**.
- `git diff --check`: passed with no whitespace errors.

### Fix-round files and self-review

- `plugins/workflow/language_schema.py`
- `tests/plugins/workflow/test_language_schema.py`

Confirmed every public versioned projection rejects the impossible pair with the
same `workflow_normalizer_version_unsupported` compatibility error used by admission.
No known concern remains from this convergence finding.

## Fix round 3

### Convergence finding resolved

`semantic_rule_descriptors()` is now version-aware through the same
`_authoring_normalizer_version()` authority as schema, node-kind, and full-contract
projection. `workflow_authoring_contract()` passes its already-selected version into
the rule projection, so one explicit-v4 contract is internally consistent.

The inherited Archon strict-output-reference rule adds exactly
`nodes[].loop.command` and `nodes[].loop.gate_message` when Phase 4 semantics are
selected. Current Archon v3 adds neither. Default Archon rules and explicit-v3 rules
are exactly equal to the current contract rule list, while legacy v1, v2, and default
rule descriptors remain exactly equal. Direct semantic-rule projection also rejects
impossible profile/version pairs through the shared authoritative selector.

No normalizer activation, identity, sealing, snapshot, runtime, interaction, UI,
tool, telemetry, or Task 8 path changed.

### Authentic RED-GREEN evidence

- RED:
  `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q -k strict_output_rule_adds_only_v4_loop_template_paths`
  produced **0 passed, 1 failed** because staged-v4 minus current-v3 strict-reference
  paths was empty.
- GREEN: the same command produced **1 passed, 0 failed** after versioning the rule
  projection and adding the Phase 4-only path delta.
- Focused contract/loader parity:
  `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase4_loops.py -q -k 'strict_output_rule_adds_only_v4_loop_template_paths or versioned_authoring_projections_reject_impossible_profile_pair or v3_generated_contract_uses_the_same_ascii_reference_grammar or included_nodes_rewrite_every_inline_reference_surface or rewritten_extension_templates_reuse_final_typed_path_validation or included_loop_command_uses_child_origin_and_rewritten_sealed_body'`
  produced **11 passed, 0 failed**. This covers the exact current-v3 rule, the staged
  rule delta, gate-message reference rewrite/typed validation, and authenticated
  loop-command rewrite/validation.
- Final current-v3 equality check plus exact strict-reference path check produced
  **2 passed, 0 failed**.

### Fix-round verification

- Complete language-schema plus strict-output-reference suites: **756 passed, 0 failed**.
- Task 7 schema/loop-executor gate: **679 passed, 0 failed**.
- Task 7 manifest/security-boundary gate: **93 passed, 0 failed**.
- Task 7 language/format-2 snapshot gate: **145 passed, 0 failed**.
- Focused v1-v3 source/snapshot matrix: **72 passed, 0 failed**.
- `.venv/bin/ruff check plugins/workflow/language_schema.py tests/plugins/workflow/test_language_schema.py`: **all checks passed**.
- `git diff --check`: passed with no whitespace errors.

### Fix-round files and self-review

- `plugins/workflow/language_schema.py`
- `tests/plugins/workflow/test_language_schema.py`

Confirmed staged-v4 adds only the two requested loader-validated paths, current v3
retains its exact pre-fix rule projection, and legacy rules remain unchanged. No known
concern remains from this convergence finding.
