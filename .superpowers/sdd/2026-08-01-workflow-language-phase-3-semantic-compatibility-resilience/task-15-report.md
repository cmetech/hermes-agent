# Task 15 Report — Published Phase 3 Language Contract

## Outcome

The dynamic workflow contract now publishes compact, profile-specific Phase 3
semantics from `language_schema.py`: Archon timeout units and omission behavior,
retry-after-initial defaults, direct-dependency and typed-condition rules,
bounded Bash substitution, and confirmed-missing cross-run recovery. Legacy
keeps normalizer v2, seconds, and its existing warnings.

The website and workflow-builder references now follow that authority. They
direct readers to generated `compatibility_codes` instead of copying an
exhaustive stable-code list, retain MCP and skills as AI-node options, and keep
loop/include expansion in Phase 4. No generated JSON Schema was checked in.

The four Bash bounds are centralized in the dependency-neutral language
inventory and mechanically re-exported by `bash_rendering.py`, so runtime and
generated metadata cannot drift.

The quality-review correction makes the editor projection self-contained on
the wire. Contract-reader/editor-projection v2 descriptors carry
scope-qualified `definition_ref` values, and the bounded top-level
`field_definitions` table carries useful labels, descriptions, examples,
widgets, sections, units, and profile semantics. All 96 serialized references
resolve after a JSON round trip without importing a Python helper. The same
compaction restores human field descriptions and reduces the Archon envelope
to 249,509 bytes, preserving 6,491 bytes below the unchanged 256,000-byte
external ceiling.

The portable Bash fixtures now follow the host contract: descriptor-capable
POSIX hosts prove inline and spilled success, while native Windows proves the
stable large-value failure without skipping the complete durable-code
assertion. Every scheduler introduced by these fixtures is retained for its
full sequence and shut down in a bounded `finally` block, including the
installed-wheel subprocess.

## TDD Evidence

- Initial RED: the four-file generated-contract/docs/skill gate reported
  **622 passed / 4 failed**. The failures were the intended generic timeout
  description, missing generated stable-code/recovery topics, and stale Phase 2
  skill guidance.
- Contract-bound correction: the first GREEN attempt reported **624 passed /
  2 failed** because the additive envelope exceeded its existing 256,000-byte
  bound. Structured metadata was compacted without weakening the bound.
- Generated/docs GREEN: the same four-file command reported **626 passed / 0
  failed**.
- Fixture RED: the representative Archon fixture first rejected the legacy
  `variables` field, then rejected an indirect output reference. Removing the
  legacy-only field and declaring the referenced producer directly made the
  fixture valid without weakening v3.
- Portable GREEN: **3 passed / 0 failed**, including real 32,768-byte inline and
  32,769-byte spill behavior.
- Installed GREEN: the integration-marked installed-distribution test built and
  installed the wheel, used a clean temporary `HERMES_HOME`, and reported
  **1 passed / 0 failed** while querying the dynamic Archon schema and executing
  the representative retry/typed-condition flow from the installed artifact.
- Specification-review I1 RED/GREEN: editor projection reported **597 passed /
  2 failed** on missing semantic/unit metadata, then **599 passed / 0 failed**
  after the shared-authority projection and bounded compaction.
- Specification-review I2 RED/GREEN: the exact durable-code authority reported
  **19 passed / 1 failed**, identifying the four Bash and four session emitters,
  then **20 passed / 0 failed** after every one of the 34 observed codes was
  derived from an invoked production behavior path.
- Specification-review I3 GREEN: the provenance-linked portable suite reported
  **4 passed / 0 failed** after real retry, typed-condition, 32,768/32,769-byte
  Bash, and confirmed-missing recovery execution.
- Specification-rereview I4 RED/GREEN: generated-contract coverage reported
  **598 passed / 1 failed** on the absent phase-boundary record, then **599
  passed / 0 failed** after restoring an Archon-only `extension-options` topic
  derived from the AI extension-option inventory and Phase 4 constant. The
  focused schema/operator/builder gate passed **608 / 608**.
- Quality-review I1/I2 RED/GREEN: the serialized contract gate reported **599
  passed / 4 failed** for the missing definition table, reader/projection v2,
  reserved headroom, and useful semantic-field descriptions, then **604 / 604**
  after the wire-table compaction, collision guard, and consumer updates.
- Quality-review I3/M1 RED/GREEN: the portable helper reported **3 passed / 1
  failed** for its missing shutdown; the durable-code catalog reported **19
  passed / 2 failed** for the skipped Windows emitter and missing bounded
  shutdowns. The combined focused gate then passed **25 / 25**.
- The corrected installed-wheel subprocess retained one scheduler across retry
  time advancement and shut it down in `finally`; its explicit integration run
  passed **1 / 1**.

Non-integration Python evidence used `scripts/run_tests.sh` with file retries
disabled; the installed-wheel test used an explicit integration-marker run.

## Dynamic Schema Evidence

With the project `.venv` active:

```text
.venv/bin/python ./hermes workflow schema --profile archon-2026-07 --json
profile=archon-2026-07 normalizer=3 reader=2 projection=2 bytes=249509 definitions=96 refs=96 unresolved=0 compatibility_codes=39

.venv/bin/python ./hermes workflow schema --profile hermes-legacy --json
profile=hermes-legacy normalizer=2 reader=2 projection=2 bytes=240669 definitions=96 refs=96 unresolved=0 compatibility_codes=9
```

The first probe before activating `.venv` selected the host Python and failed
before command dispatch. Repeating it in the documented development environment
produced the evidence above; no product change was needed.

## Verification

The required seven-file matrix plus the narrow Bash authority regression:

```text
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_portable_compatibility_e2e.py \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  tests/plugins/workflow/test_showcase_distribution_e2e.py \
  tests/agent/test_workflow_builder_skill.py \
  tests/skills/test_workflow_operator_behavior.py \
  tests/plugins/workflow/test_phase3_bash_substitution.py
```

Result after the quality-review correction: **1,764 passed / 0 failed**.
The default marker selection filters the
installed integration test in this aggregate run; its separate `-m integration`
run passed 1/1 as recorded above.

Scoped Ruff and `git diff --check` passed. Both dynamic profile probes parsed
successfully with every definition reference resolved. The Archon envelope is
within its 252,000-byte growth target and all three published section budgets.

## Files Changed

- `plugins/workflow/language_schema.py`
- `plugins/workflow/bash_rendering.py`
- `website/docs/user-guide/features/workflow-yaml-reference.md`
- `skills/software-development/workflow-builder/references/portable-schema.md`
- `skills/software-development/workflow-builder/references/authoring-checklist.md`
- `tests/plugins/workflow/test_language_schema.py`
- `tests/plugins/workflow/test_phase3_code_catalog.py`
- `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- `tests/plugins/workflow/test_installed_distribution_e2e.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`
- `tests/agent/test_workflow_builder_skill.py`
- `tests/skills/test_workflow_operator_behavior.py`
- retained Task 15 progress and this report

## Deviations and Concerns

The parent authorized one narrow ownership exception for `bash_rendering.py`
and its regression test so the generated contract could own the four existing
bounds without creating a second authority. The change is import/re-export
only and preserves existing names and behavior.

No blocking concern remains. Per user instruction, threat-model/security
testing and validation were excluded. Task 16 final verification is still
pending; this report does not claim Phase 3 completion.
