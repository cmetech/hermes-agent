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

The specification-review correction also projects units and compact semantic
IDs into editor field descriptors. `resolve_field_semantics()` resolves those
IDs through the same inventory authority as the inline JSON Schema annotation;
legacy descriptors retain seconds and expose no v3 semantic ID. The stricter
default-`json.dumps` envelope remains bounded at 255,795 bytes.

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

All Python evidence used `scripts/run_tests.sh` with file retries disabled.

## Dynamic Schema Evidence

With the project `.venv` active:

```text
./hermes workflow schema --profile archon-2026-07 --json
profile=archon-2026-07 normalizer_version=3 bytes=255795 compatibility_codes=39 semantic_refs=25 extension_options=true
timeout semantics={omitted: 120000, scope: attempt, unit: milliseconds}

./hermes workflow schema --profile hermes-legacy --json
profile=hermes-legacy normalizer_version=2 bytes=249839 compatibility_codes=9 semantic_refs=0 extension_options=false
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

Result after the specification-review correction: **1,758 passed / 0 failed**.
The default marker selection filters the
installed integration test in this aggregate run; its separate `-m integration`
run passed 1/1 as recorded above.

Scoped Ruff and `git diff --check` passed. Both dynamic profile probes parsed
successfully, and the Archon envelope remains below its original bound.

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

No blocking concern remains. Task 16 final verification is still pending; this
report does not claim Phase 3 completion.
