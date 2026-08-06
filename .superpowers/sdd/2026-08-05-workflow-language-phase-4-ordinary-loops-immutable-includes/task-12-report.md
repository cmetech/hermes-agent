# Task 12 report: Phase 4 contracts, documentation, and installed flows

Status: DONE

## Outcome

Hermes now publishes a version-explicit normalizer v4 authoring contract for
ordinary loops and immutable compile-time includes without activating v4 for new
Archon packages. The current `archon-2026-07` mapping remains normalizer v3, while an
explicit v4 request receives the closed include shape, exact-one loop prompt/command
shape, structured operational documentation, cumulative v3/v4 code catalog, and v4
compatibility findings.

The Phase 4 stable include failures are registered only after real compiler/resource
emitters prove each code. Compatibility assessment reports the sealed loop prompt
source and operator confirmation requirement without introducing a blocker or a new
wire action.

The website and workflow-builder references now give an explicit-v4 reader enough
information to author a root/child package, understand root-only policy, inspect exact
closure bounds and include alias behavior, validate and trust the composite digest,
operate confirmed loop signals, diagnose bounded logical failures, and resume from the
immutable snapshot after source deletion. Every surface warns before trust that v4 is
staged and not the current Archon default.

## Scope and ownership resolution

The original Task 12 file list did not include the existing Step 1 relationship tests
or packaging metadata. The parent explicitly authorized the minimum necessary edits
to:

- `tests/plugins/workflow/test_phase4_code_catalog.py`
- `tests/plugins/workflow/test_language_schema.py`
- `tests/agent/test_workflow_builder_skill.py`
- `tests/skills/test_workflow_operator_behavior.py`
- `pyproject.toml`
- `MANIFEST.in`, if needed

Only `pyproject.toml` needed a packaging change: a data-files glob now retains the two
workflow-builder reference documents at their repository-relative wheel paths.
`MANIFEST.in` already has `graft skills`, so it was deliberately left unchanged.

## Implementation

- `plugins/workflow/language_schema.py`
  - registers ten behavior-backed include failure codes for explicit v4;
  - makes code and documentation selection normalizer-version-aware;
  - publishes one structured `ordinary-loops-and-includes` topic containing root
    policy, ignored child sidecars, exact bounds, entry/sink/first-sink behavior,
    logical sealed origins, loop source cardinality, signal defaults/actions, and
    deliberate later-Archon omissions;
  - preserves cumulative Phase 3 codes/topics in explicit v4 while omitting Phase 4
    codes/topics from the default v3 envelope.
- `plugins/workflow/compat.py`
  - emits nonblocking `phase4_loop_prompt_sealed` for the admitted prompt/command
    source;
  - emits nonblocking `phase4_signal_confirmation` only when the sealed loop requires
    operator confirmation.
- `pyproject.toml` ships the workflow-builder portable-schema and checklist Markdown
  in wheels. The existing sdist graft already covers them.
- Website and skill references publish the staged authoring and operator contract.
- `docs/upstream-customizations/workflow-orchestration.yaml` records the exact owned
  symbols, invariants, tests, merge guidance, and pre-activation v3 boundary.

## Documentation outline and cold reader test

The documentation was outlined around the reader's actual decision sequence:

1. establish that explicit v4 is staged and ordinary admissions remain v3;
2. author the root, literal compile-only include, child graph, and exactly-one loop
   prompt/command;
3. apply root policy and understand authenticated-but-ignored child companions;
4. check the exact closure, source, file, node, and edge bounds;
5. resolve entries, sinks, downstream joins, first-sink output, and logical resource
   origins;
6. validate/doctor, review warnings, trust the composite digest, and admit only an
   explicitly compiled v4 snapshot;
7. operate status/events plus approve/provide-input/cancel, including the final
   iteration restriction and no provider replay on approval;
8. diagnose stable codes and bounded logical paths, then resume from sealed bytes even
   if the original sources were deleted;
9. avoid inventing runtime child workflows, `include.with`, `loop_group`, portable
   budget, or portable sandbox semantics.

The workflow-builder reader-pressure test retrieves those facts across the portable
schema and authoring checklist and verifies that the default warning precedes the
trust instruction. The Docusaurus production build compiled both locales; neither
modified workflow page appeared in its pre-existing unrelated link/anchor warnings.

## TDD evidence

1. Baseline characterization, before test or production edits:
   - exact Step 1 command;
   - 4 files, 638 passed, 0 failed. This was explicitly treated as baseline, not RED.
2. Generated-contract/operator RED, after the authorized test edits and before
   production/documentation edits:
   - exact Step 1 command;
   - 4 files, 636 passed, 6 failed;
   - failures showed an empty Phase 4 durable catalog, stale workflow-builder
     references, a missing operator topic, and absent default/explicit-v4 contract
     relationships.
3. Installed/distribution RED, before packaging and contract implementation:
   - exact Step 3 command;
   - 3 files, 5 passed, 2 failed;
   - the wheel omitted workflow-builder references and the explicit-v4 compatibility
     report omitted both Phase 4 loop findings. The integration-marked fresh-install
     test was filtered by the repository default marker policy.
4. Focused GREEN after implementation:
   - code catalog + language schema + portable compatibility: 3 files, 636 passed,
     0 failed.
5. Documentation reader iteration:
   - the first exact Step 1 GREEN attempt retained two deliberate phrase/order
     failures; Markdown whitespace normalization and unambiguous staging/ignored-child
     wording corrected the reader contract without weakening its required facts.
6. Installed fresh-wheel fixture iteration:
   - the first integration run exposed a fixture/interface mismatch: the raw
     `scheduler.advance()` projection does not carry the operator-level pending
     interaction;
   - the second exposed that historical EvidenceReader interaction items carry
     `event_type`, not pending-item `type`;
   - the fixture was corrected to use `get_run_status()` and independently assert the
     journal and EvidenceReader projections. No production change was needed.

## Installed-wheel evidence

The integration-marked test builds a wheel, extracts it to a temporary site outside
the repository, and imports the installed plugin from that site. It proves:

- ordinary installed schema output remains normalizer 3 and explicit contract output
  is normalizer 4;
- a root includes a child whose loop uses a child-owned named command;
- the child sidecar's required-secret declaration is authenticated but ignored;
- validation has no blocker, compatibility is runnable, the composite digest is
  trusted, and the expanded node is namespaced;
- one provider result pauses with the exact existing signal actions;
- deleting both source trees does not affect authenticated snapshot reload;
- approval completes without another provider request;
- the normalizer remains pinned to 4 and both required/accepted signal facts are
  visible in the journal and interaction evidence.

The non-integration wheel-content test independently opens the built archive and
asserts the Phase 4 modules, both workflow-builder references, showcases, fixtures,
skills, and capability descriptors are present.

## Final verification

- Exact Step 1:
  `scripts/run_tests.sh tests/plugins/workflow/test_phase4_code_catalog.py tests/plugins/workflow/test_language_schema.py tests/agent/test_workflow_builder_skill.py tests/skills/test_workflow_operator_behavior.py`
  — 4 files, 642 passed, 0 failed.
- Exact Step 3:
  `scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_portable_compatibility_e2e.py tests/plugins/workflow/test_showcase_distribution_e2e.py`
  — 3 files, 7 passed, 0 failed; the integration marker is filtered by the repository
  default and is covered separately below.
- Installed integration:
  `scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration`
  — 1 file, 1 passed, 0 failed.
- Exact no-retry Step 4:
  `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_compilation.py tests/plugins/workflow/test_phase4_includes.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_surfaces.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_phase4_code_catalog.py`
  — 11 files, 217 passed, 0 failed.
- Ruff on every touched Python file — all checks passed.
- Direct generated-contract/schema probe — current Archon `3`, explicit `4`, both
  definition/sidecar schemas valid Draft 2020-12, explicit envelope 239,878 bytes.
- Strict upstream-customization validation against an exact working-tree snapshot —
  passed after keeping prose in `owned_invariants` and machine identifiers in
  `owned_symbols`.
- Docusaurus production build using the required npm 11.17 toolchain — en and zh-Hans
  both built successfully. Existing unrelated link/anchor warnings remain; no modified
  workflow page was listed.
- `git diff --check` — passed.
- Generated `build`, `.docusaurus`, and `node_modules` directories were moved to the
  system Trash after validation; no build output remains in the worktree.

## Self-review and concerns

- `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` remains exactly `3`; Task 12 does
  not activate v4 or add a user-facing normalizer flag.
- New public code entries are tied to real compiler/resource emitters rather than
  enumeration counts or synthetic return values.
- Compatibility findings are v4-gated and nonblocking. They report admitted semantics
  and do not create new actions, endpoints, providers, or execution paths.
- Includes remain compile-only, child policy remains ignored, and docs never claim
  source paths are execution authority after admission.
- The initial website install command used the ambient npm 10.9.8 and correctly failed
  the repository's `npm >=11.17.0` engine requirement. Validation was rerun with npm
  11.17.0. The existing lockfile reports three dependency audit findings; this task did
  not change website dependencies or the lockfile.
- No scoped production, security, or compatibility concern remains. Task 13 security
  review and Task 14 v4 activation were intentionally not performed.
