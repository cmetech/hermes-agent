# Declarative Reference Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a compact versioned scanner contract and independently reviewed literal conformance corpus, verified against the unchanged Hermes Python implementation and available from offline installations.

**Architecture:** `language_schema.py` derives the complete interpolation surface and embeds dependency-neutral scanner metadata only in amended Archon v6. `language_conformance.py` publishes packaged literal cases; test-only adapters exercise existing scanner, substitution, schema-proof and workflow APIs. The legacy contract/corpus outputs remain frozen.

**Tech Stack:** Python 3.11–3.13, standard-library JSON/importlib resources/hashlib, existing pytest/YAML/workflow helpers, setuptools, uv, and `scripts/run_tests.sh`.

**Spec:** `docs/superpowers/specs/2026-09-04-declarative-reference-scanner-design.md`.

## Global constraints

- Use the existing `.worktrees/workflow-reference-scanner-contract` checkout on `feat/workflow-reference-scanner-contract`; starting commit `74fed08f91014ca7fc80ee9ea4427568ec95b04f`.
- Do not create another worktree, modify another worktree, modify literal `main`, or modify Workflow Studio.
- Preserve the existing Python scanners, runtime APIs, accepted syntax, error precedence, iterator behavior, include v4 policy and scheduler preflight v3 policy.
- Do not create a VM, interpreter, bytecode, transition program, instruction accounting, or cross-language memory/session mechanism.
- Preserve legacy v1/v2 and Archon v1–v5 authoring bytes. Amended Archon v6 alone requires reader 3; schema/projection/normalizer remain 1/2/6.
- Freeze legacy corpus format 1, 11 cases, 7,265 canonical bytes, SHA-256 `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b`.
- Derive the 18 root + 18 body + 2 control surfaces from `_INTERPOLATION_SURFACE_INVENTORY`, including applicability and container-major traversal.
- Amended contract bound: 328,000 total bytes, 4,000 reserved bytes, 32,000 scanner-section bytes. Historical bounds stay unchanged.
- Expanded corpus bounds: 64 workflow cases, 256 scanner cases, 64 substitution cases, 64 structured-path cases, 384,000 canonical bytes, 768,000 emitted pretty-JSON bytes. Preserve legacy bounds.
- Expectations are reviewed literals; the publisher never executes the scanner. Existing runtime characterization can pass immediately; the missing publication behavior supplies TDD red checkpoints.
- Run pytest only through `scripts/run_tests.sh`. Do not silently skip an unavailable Python or installation prerequisite.
- Use one complete plan review, focused task reviews, then one complete-branch adversarial review. Escalate proposed new runtime/interpreter architecture instead of incorporating it.
- Commit the completed reviewed Hermes work on the feature branch, then stop for explicit approval before merging into `base` or starting Studio. Do not stage historical VM records or unrelated files in the feature commit.

## File ownership and interfaces

| File | Responsibility |
|---|---|
| `plugins/workflow/language_schema.py` | Inventory projection, v6 metadata integration, version-specific contract limits and v6 root reference descriptor |
| `plugins/workflow/reference_scanner_contract.py` (new) | Standard-library-only declarative rule descriptions; no scanner implementation |
| `plugins/workflow/conformance/reference_scanner_v1.json` (new) | Independently reviewed scanner/substitution/path literal input and outcome data |
| `plugins/workflow/language_conformance.py` | Load packaged literal data, assemble Archon format 2 and bind digests; preserve legacy output |
| `plugins/workflow/schema_cli.py` | Version-specific section and output bounds before stdout |
| `tests/plugins/workflow/fixtures/reference_scanner/baselines/` (new) | Seven historical canonical contracts, old v6 characterization, legacy corpus and digest manifest |
| `tests/plugins/workflow/fixtures/reference_scanner/case_manifest.json` (new) | Independently reviewed stable case IDs, requirement tags and source evidence |
| `tests/plugins/workflow/test_reference_scanner_baselines.py` (new) | Exact historical preservation and explicit v6 delta |
| `tests/plugins/workflow/test_reference_scanner_contract.py` (new) | Metadata, inventory, limits and dependency-neutral publication checks |
| `tests/plugins/workflow/reference_scanner_observations.py` (new) | Fixed test-only API adapters and outcome serialization |
| `tests/plugins/workflow/test_reference_scanner_conformance.py` (new) | Literal corpus execution, manifest coverage, Unicode profile probes and determinism |
| Existing workflow conformance/CLI/installed-distribution tests | Preserve old guarantees while exercising format 2 and sdist-built installation |
| `pyproject.toml` | Add workflow conformance JSON package data |
| `plugins/workflow/README.md` (new) | Packaged authoring metadata/corpus version and offline CLI guidance |

The production interfaces are:

```python
# reference_scanner_contract.py; no imports from runtime workflow modules
def reference_scanner_contract(
    *, grammar: Mapping[str, str], interpolation_surface: Mapping[str, object]
) -> dict[str, object]: ...

# language_schema.py; derives grouped/flattened views from the existing inventory
def reference_scanner_interpolation_surface() -> dict[str, object]: ...

# Existing public signatures remain unchanged:
def workflow_authoring_contract(profile, *, normalizer_version=None): ...
def workflow_language_conformance(profile): ...
def emit_schema(args): ...
def emit_schema_corpus(args): ...
```

The ellipses above specify signatures, not unimplemented deliverables. The literal loader is private to `language_conformance.py` and uses `importlib.resources.files("plugins.workflow").joinpath("conformance/reference_scanner_v1.json")`. No production consumer of the test adapters is permitted.

## Task 1: Freeze historical publication bytes

**Files:** Create the baseline fixture directory and `test_reference_scanner_baselines.py`.

**Consumes:** Existing `workflow_authoring_contract`, `workflow_language_conformance`, `canonical_contract_json`, `WorkflowLanguageProfile`.

**Produces:** Canonical fixture files `legacy-1.json`, `legacy-2.json`, `archon-1.json` through `archon-6.json`, `legacy-corpus-1.json`, and a manifest with byte counts and full SHA-256 values. Archon 6 is explicitly labeled characterization-only.

- [x] Record branch/status and verify production/test source still matches the starting commit. Preserve the historical pending documents. Use `git diff --name-only HEAD` and `git diff 74fed08f -- plugins/workflow tests/plugins/workflow pyproject.toml`.
- [x] Add the historical preservation test before creating fixtures:

```python
from hashlib import sha256
from pathlib import Path
import pytest
from plugins.workflow.language_schema import canonical_contract_json, workflow_authoring_contract
from plugins.workflow.language_conformance import workflow_language_conformance
from plugins.workflow.models import WorkflowLanguageProfile as P

BASELINES = Path(__file__).parent / "fixtures/reference_scanner/baselines"
PAIRS = [(P.HERMES_LEGACY, v, f"legacy-{v}") for v in (1, 2)] + [
    (P.ARCHON_2026_07, v, f"archon-{v}") for v in range(1, 6)
]

@pytest.mark.parametrize("profile,version,name", PAIRS)
def test_historical_contract_bytes(profile, version, name):
    expected = (BASELINES / f"{name}.json").read_bytes()
    actual = canonical_contract_json(workflow_authoring_contract(
        profile, normalizer_version=version
    )).encode("utf-8")
    assert actual == expected

def test_legacy_corpus_bytes():
    corpus = workflow_language_conformance(P.HERMES_LEGACY)
    encoded = canonical_contract_json(corpus).encode("utf-8")
    assert (corpus["format_version"], len(corpus["cases"]), len(encoded)) == (1, 11, 7265)
    assert sha256(encoded).hexdigest() == "c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b"
    assert encoded == (BASELINES / "legacy-corpus-1.json").read_bytes()
```

- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_baselines.py -q`. Confirm missing-fixture failure, not an import or environment failure.
- [x] Capture canonical bytes from the unchanged starting implementation. This historical-byte capture is permitted; it must not be reused to generate new scanner-case expected outcomes. Record old v6 as 283,522 bytes and SHA-256 `171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc`.
- [x] Add a test comparing the old/current v6 definition schema, sidecar schema, projection and normalizer; later explicitly allow only reader, scanner metadata, root reference paths, limits and derived digest changes. Derive the difference using dictionary paths so an unexpected node-kind or schema change is visible.
- [x] Re-run the baseline test and existing language-conformance test file. Both pass before metadata work.
- [x] Obtain a focused task review of fixtures, capture provenance and preservation tests. Keep the completed task's diff ready for the final feature commit.

## Task 2: Publish complete surfaces and declarative metadata

**Files:** Create `reference_scanner_contract.py` and `test_reference_scanner_contract.py`; modify `language_schema.py` and narrowly update existing metadata tests.

**Consumes:** Frozen baselines; existing grammar constants and `_INTERPOLATION_SURFACE_INVENTORY`.

**Produces:** `reference_scanner_interpolation_surface()` and the v6 `reference_scanner_v1` field with the exact required sections in the spec.

- [x] Write failing publication tests, including:

```python
def test_v6_requires_complete_reference_scanner_metadata():
    contract = workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6)
    assert contract["contract_reader_version"] == 3
    scanner = contract["reference_scanner_v1"]
    assert scanner["version"] == 1
    assert set(scanner) == {
        "version", "applicability", "grammar", "boundaries", "offsets",
        "unicode_profiles", "modes", "scalars", "callers", "diagnostics",
        "interpolation_surface",
    }
    assert scanner["grammar"]["node_id"] == ARCHON_V3_NODE_ID_PATTERN
    assert scanner["grammar"]["path_segment"] == ARCHON_V3_OUTPUT_PATH_SEGMENT_PATTERN
```

- [x] Add inventory parity tests that expand each existing record's ordered leaves and applicable root/body/control scopes independently, then compare to the published projection. Assert all root/body fields are paired by relative path and applicability; explicitly assert group controls and root Phase-4 leaves. Add a two-agent/two-hook-container test comparing the metadata traversal description with `iter_interpolation_surface_templates` order, so a matching count cannot hide missing or misordered fields.
- [x] Add tests for exact delimiter sets, malformed suffix rule metadata, ten scalar names and maximal form, caller names/versions, stable diagnostic mappings and all required Bash state families. Compare scalar descriptions with `_BASH_SCALAR_REFERENCE`, `_BASH_SCALAR_NAMES` and `_SCALAR_VARIABLE` in tests only.
- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_contract.py -q`; confirm failure on absent publication.
- [x] Implement the inventory projection. Preserve grouped traversal records as well as flattened fields, and derive v6 strict-reference root paths from this result. Retain `phase6_interpolation_surface()` as its existing scoped view. If mode annotations are added, put them on the existing records and leave runtime iteration untouched.
- [x] Implement the data-only metadata builder. Pass existing node/path/reference patterns from `language_schema.py`; return fresh JSON-compatible data. Describe exact Bash/caller rules from the reviewed source, including context-before-grammar and no-candidate failures. Do not import `bash_rendering`, `resources`, `schema`, providers or tools into the new module.
- [x] Select reader/limits by profile and normalizer. Preserve the current constants and historical envelope values; add reader-3 limits separately. `_require_contract_bounds` must select trusted limits from profile/version, not trust an arbitrary larger number in input metadata. Add exact-boundary and one-byte-overflow tests for new total/section limits and keep all historical bound tests.
- [x] Extend dependency-direction checks to admit only the new neutral module and verify its own imports remain standard-library-only. Test contract generation without scanner/runtime initialization.
- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_baselines.py tests/plugins/workflow/test_reference_scanner_contract.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_phase6_language.py -q`.
- [x] Obtain a focused task review against S1/S2/S5/S6/S7/S10 and H1; fix concrete publication mismatches without changing runtime scanner behavior.

## Task 3: Add independent literals and fixed Python observation adapters

**Files:** Create the JSON corpus resource, case manifest, observation helper and conformance test file. Do not modify runtime scanner, renderer, conditions, schema proof, include or scheduler code.

**Consumes:** Spec state-family table, the exact existing APIs, existing literal behavior assertions in Bash/reference/condition/Phase4/Phase6 tests.

**Produces:** Reviewed literal cases and these test-only adapters:

```python
def observe_scanner_case(case: dict[str, object], tmp_path: Path) -> dict[str, object]: ...
def observe_substitution_case(case: dict[str, object], tmp_path: Path) -> dict[str, object]: ...
def observe_structured_path_case(case: dict[str, object]) -> dict[str, object]: ...
```

- [x] Author the case-ID manifest from the spec's state-family table before implementing adapters. Each row records ID, requirement tags and the existing source assertion or manually reasoned rule supporting its expected outcome. Cover every listed family and each named malformed suffix. Do not compute IDs, family membership or expected outputs by asking the scanner.
- [x] Write resource/manifest tests that fail while the literal artifact is absent. Assert globally unique IDs, exact manifest membership, nonempty expected outcomes and profile applicability, and required family coverage. Validate result variants by the fixed API name, not arbitrary expressions in case data.
- [x] Author the literal JSON. Start with the spec's `text.whole-output` case, then include these exact iterator cases:

```json
[
  {
    "id": "text.valid-before-malformed.first", "requirements": ["H2", "S2"],
    "unicode_profiles": ["all"], "api": "iter_output_references",
    "normalizer_version": 6,
    "input": {"text": "$a.output $b.outputx", "consume": "first"},
    "expected": {"tokens": [{"node_id": "a", "path": [], "start": 0, "end": 9}], "error": null}
  },
  {
    "id": "text.valid-before-malformed.drain", "requirements": ["H2", "S2"],
    "unicode_profiles": ["all"], "api": "iter_output_references",
    "normalizer_version": 6,
    "input": {"text": "$a.output $b.outputx", "consume": "all"},
    "expected": {
      "tokens": [{"node_id": "a", "path": [], "start": 0, "end": 9}],
      "error": {"class": "WorkflowReferenceSyntaxError", "code": "output_reference_path_unsupported", "start": 10}
    }
  }
]
```

- [x] Fill the remaining manifest rows with literal cases from all spec families. Use existing independent expectations as evidence, correcting obsolete labels such as broadly literal quoted heredocs. Include paired text/Bash scalar examples, both condition APIs, malformed/valid presence tests, caller version boundaries, and full workflow diagnostics where a low-level token result cannot establish applicability.
- [x] Implement test adapters with explicit dispatch to known functions. For lazy iterators, call `next` or drain inside the observation boundary and retain values yielded before an exception. Serialize existing token attributes directly. For malformed spans, pass literal spans unchanged to the public API; do not prevalidate in the adapter. For known non-token APIs, use fixed result keys `spans`, `references`, `value` or `rendered_text` selected by API; reject unknown API names in the test harness.
- [x] Add fixed `scanner_cases` variants for `validate_authenticated_resource_references` and `compute_package_digest`. The first contains literal definition/companion YAML, explicit normalizer version and command/named-script body maps. Compile with the existing source compiler, then call `validate_authenticated_resource_references(package, command_bodies=..., named_script_bodies=...)`. The second supplies relative resource paths and literal hexadecimal bytes, writes those bytes with `bytes.fromhex` under `tmp_path`, loads the versioned package and calls `compute_package_digest(package)`. Do not decode resource bytes in the adapter; exercise the existing `surrogateescape` path in `trust.py`. Include the v3 valid-reference-before/after-invalid-bytes examples from `test_named_script_scan_never_loses_a_valid_reference_around_other_bytes`, as well as version-specific authenticated command/script behavior. Serialize ordered `WorkflowValidationError.issues`, native codes/authored paths and meaningful causes. On success compare validated body maps or applicable digest outcome. Map every authenticated case ID in the manifest; do not claim YAML-only workflow cases provide this coverage.
- [x] Add adapter assertions with manually constructed expected observations, including the first/drain distinction, an error after a yield, eager Bash behavior, a predicate result and quote-context result. These tests protect the observation boundary without duplicating scanner logic.
- [x] Parameterize corpus execution using the JSON resource and the three adapters. Compare complete actual observations to literal expected values. Use real temporary directories for Bash rendering with `secure_v3=True`; keep literal values below spill thresholds and close `RenderedBashCommand` using its existing context/lifecycle API. Do not launch a shell from the corpus adapter.
- [x] For static structured paths call `_v3_output_path_impossible(schema, tuple(path))` directly. For resolution use the existing output-resolution helpers and immutable facet fixtures from `test_strict_output_references.py`; keep static proof and runtime resolution API identifiers separate. Add manually expected cases for containing type plus `$ref`, union branches, numeric object/array alternatives and conservative unresolved refs.
- [x] Publish explicit Unicode profile IDs `python-3.11-unicode-14.0.0`, `python-3.12-unicode-15.0.0`, and `python-3.13-unicode-15.1.0`. Add profile distinction inputs such as the Kawi letter U+11F02, plus digit, whitespace, astral-offset and non-ASCII-suffix cases. Verify profile identity with `unicodedata.unidata_version` before execution; publication stays byte-identical across interpreters.
- [x] Add a matrix test that locates all three supported interpreters using `uv python find --offline <version>` and runs the fixed observation helper in subprocesses under those interpreters. Provision temporary venvs with cached required dependencies if needed, without changing the worktree's `.venv`. This is an API observation subprocess, not an alternate pytest runner. Missing interpreters/dependencies fail the explicit matrix check with a clear prerequisite message; install prerequisites before claiming verification. Test direct Python surrogate inputs separately from published JSON.
- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_conformance.py tests/plugins/workflow/test_phase3_bash_lexer_security.py tests/plugins/workflow/test_phase3_bash_reference_ordering.py tests/plugins/workflow/test_phase3_bash_substitution.py tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_phase3_conditions.py tests/plugins/workflow/test_phase4_references.py -q`.
- [x] Have a focused reviewer independently inspect expected literals, coverage and the observation adapters. Correct proven fixture/adapter errors using systematic debugging; escalate any proposed runtime behavior change. Review must distinguish reviewed literals from observations produced during test execution.

## Task 4: Publish and bind the expanded Archon corpus

**Files:** Modify `language_conformance.py`, `schema_cli.py`, `test_language_conformance.py`, `test_cli.py`; extend the new conformance tests and literal workflow cases.

**Consumes:** Task 2 contract and Task 3 packaged literals. Existing workflow case shape and CLI entry points.

**Produces:** Archon format 2 containing existing `cases`, new `scanner_cases`, `substitution_cases`, `structured_path_cases`, and `corpus_digest`. Legacy format 1 is unchanged.

- [x] Write the failing publication test:

```python
def test_archon_corpus_binds_reviewed_scanner_artifacts():
    corpus = workflow_language_conformance(P.ARCHON_2026_07)
    assert corpus["format_version"] == 2
    assert corpus["scanner_cases"]
    assert corpus["contract"]["contract_digest"] == workflow_authoring_contract(P.ARCHON_2026_07)["contract_digest"]
    payload = {key: value for key, value in corpus.items() if key != "corpus_digest"}
    assert corpus["corpus_digest"] == "sha256:" + sha256(
        canonical_contract_json(payload).encode("utf-8")
    ).hexdigest()
```

- [x] Add mutation-isolation tests: modifying one returned envelope does not affect the next call. Add a test patching runtime scanner entry points to raise if called during publication; metadata/corpus emission must still succeed. Keep source JSON free of calculated expressions and perform no scanner imports in the publisher.
- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_language_conformance.py -q`; confirm the new format/section expectation fails before implementation.
- [x] Load the resource with `importlib.resources`, preserve the existing `cases` section, and branch only Archon publication to format 2. Compute the digest once over the completed payload without its self-hash. Do not add timestamps, interpreter-dependent fields or source paths to canonical data.
- [x] Add literal workflow cases for root Phase-4 fields, current/outer/previous scope diagnostics and ordering, using the existing `_case`/diagnostic helpers strictly as data constructors. Preserve every preexisting case ID and its expected behavior. Publish Task 3's authenticated-resource variants in `scanner_cases`; the unchanged YAML workflow-case shape does not supply authenticated bodies.
- [x] Make CLI validation version-specific and validate every section count plus canonical and emitted-byte bounds before `print`. Retain pretty/compact output, existing errors, and the read-only early CLI paths. Test exact boundaries and one-byte/count overflow with no partial stdout. Unknown corpus formats fail explicitly.
- [x] Update old tests only where they assume Archon format 1, reader 2, or the old Archon corpus limit. Keep all legacy assertions and historical fixtures unchanged. Verify packaged/root CLI entry paths, help and invalid-profile behavior without provider/plugin discovery.
- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_baselines.py tests/plugins/workflow/test_reference_scanner_conformance.py tests/plugins/workflow/test_language_conformance.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_phase6_language.py -q`.
- [x] Obtain focused review for H1/H3 and S6/S7, including error precedence and lack of runtime calls during publication.

## Task 5: Prove offline distribution and document consumption

**Files:** Modify `pyproject.toml` and `test_installed_distribution_e2e.py`; create `plugins/workflow/README.md` (already covered by the existing packaged README glob).

**Consumes:** Completed contract/corpus APIs and resource file; existing installed-distribution fixture and real-console test.

**Produces:** Actual direct-wheel and sdist-built-wheel artifact proof and offline installed CLI/resource parity.

- [x] Extend package tests before adding package data. Assert `plugins/workflow/conformance/reference_scanner_v1.json` is present in both direct wheel and source distribution; verify bytes against the reviewed source literal file. Add the new path to the installed-origin probe.
- [x] Extend the real installed CLI test to check both `schema` and `schema-corpus`, compact and pretty modes, both profiles, format-specific digests/limits, and installed resource origin outside the checkout. Keep the existing Jira resource check.
- [x] Build both artifacts from a temporary source copy with `uv build --wheel --sdist`; inspect archives with standard-library zipfile/tarfile. Safely extract the sdist into another temporary directory and build its wheel. Install that wheel with `uv pip install --offline` and `pip --no-index --no-deps` as appropriate in the existing real-venv fixture. No checkout imports or `PYTHONPATH` are allowed in the installed execution probe.
- [x] Run the new installation test before the package-data/documentation addition. Baseline artifact inspection established that the existing `MANIFEST.in` graft already ships the JSON in both wheel and sdist; preserve that correct behavior and use the absent packaged README for the intended publication failure. Do not manufacture a missing-resource failure.
- [x] Add `"workflow/conformance/*.json"` under the existing `plugins` setuptools package-data entry. Keep the package dependency list unchanged.
- [x] Add a test-only temporary `sitecustomize.py` in the installed venv that denies `socket.socket.connect` and `socket.create_connection`. Prove the guard is active with an explicit connection attempt, then run the real installed console. This proves network-free Python execution without modifying production code. Confirm child commands execute successfully with the guard and `PYTHONPATH` absent.
- [x] Run `scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -k 'scanner or corpus_resources' -q`. Check that the intended tests were collected and passed; default pytest markers otherwise exclude installation integration tests.
- [x] Document reader 3, format 2, the existing CLI commands, literal corpus authority, canonical digest rules, Unicode profile applicability and unsupported-capability behavior in `plugins/workflow/README.md`. State that Python behavior is unchanged and Studio consumption is separately delivered. Include the exact seven historical-version boundary and legacy corpus guarantee.
- [x] Obtain focused packaging/documentation review. Record built artifact origins and the source/sdist-wheel/installed canonical digest equality.

## Task 6: Complete verification, review and commit Hermes

**Files:** Completed feature files; add `docs/reviews/2026-09-04-declarative-reference-scanner-delivery.md` for evidence.

**Consumes:** Tasks 1–5, matrix coverage, task review findings and the original starting commit.

**Produces:** Reviewed feature-branch commit and an explicit user approval boundary. No merge or Studio edits.

- [x] Run focused publication tests and record actual artifact sizes, counts, digest values and headroom. Confirm all seven historical byte fixtures and exact legacy corpus remain unchanged.
- [x] Run the full workflow suite with `scripts/run_tests.sh tests/plugins/workflow -q`, then the explicit installation integration command from Task 5. Run the supported-Python matrix test and record each interpreter/Unicode identity.
- [x] Run the complete repository suite with `scripts/run_tests.sh -q`. Record failures by test and cause; use systematic debugging and resolve regressions introduced by this feature. Preserve unrelated changes and report independently established environment/baseline failures honestly instead of declaring the suite green.
- [x] Register the three new scanner test suites in the existing base-phase release gate. The complete-repository run exposed their omission through the existing exhaustive gate-inventory assertion; the three-line registration passed that assertion and all 51 tests in its file, without adding opt-outs or changing runtime behavior.
- [x] Run `git diff --check` and inspect `git diff 74fed08f -- plugins/workflow/bash_rendering.py plugins/workflow/resources.py plugins/workflow/conditions.py plugins/workflow/schema.py`. The runtime files must have no implementation changes. Inspect the language-schema diff to ensure only metadata/projection/publication changes and inert inventory annotations occurred.
- [x] Perform the single complete-branch adversarial review against the spec and matrix. Provide the exact feature diff, literal resource/manifest, adapter code, baseline evidence, packaging and test results. Ask for concrete compatibility regressions, uncovered state families and unsupported claims; reject interpreter/runtime expansion outside scope.
- [x] Resolve concrete review findings with focused regression tests and repeat affected verification. Do not restart serial architecture/plan reviews. If a fix changes runtime syntax/behavior or needs a new runtime design, stop and explain the decision required.
- [x] Write delivery evidence with the requirements matrix marked proved/deferred/blocked, actual test commands/results and artifact hashes. Mark S8/S9/S10 Studio acceptance explicitly deferred, never passed by Hermes tests.
- [x] Stage only the replacement spec/plan, delivery/review records and intended feature files using explicit paths. Existing ignored documentation may require `git add -f` for those exact new files. Do not use `git add .` or stage abandoned documents.
- [x] Commit on `feat/workflow-reference-scanner-contract` with `feat: publish declarative workflow reference scanner contract`. Verify branch, commit contents and remaining status. Leave unrelated/historical pending documents intact.
- [x] Explain in plain language what Hermes now publishes, that its scanner behavior is unchanged, what passed and what remains deferred. Stop for explicit user approval before any merge into `base` or any Studio changes. Invoke finishing-a-development-branch only after that approval.

## Plan self-review and review gate

All spec requirements map to Tasks 1–6. The ten Studio findings are represented in the shared matrix; Studio implementation/performance is expressly deferred. No task authorizes VM code, scanner runtime replacement, literal-main work or modification of another worktree.

One independent review of this complete plan is required before implementation. Record findings and their disposition in `docs/reviews/2026-09-04-declarative-reference-scanner-plan-review.md`. Address concrete gaps in place; do not create serial rereview documents. Subagent-driven execution is already the user's selected implementation method, so do not ask for a second execution-method choice.

## Execution result

Tasks 1–6 are delivered on `feat/workflow-reference-scanner-contract`. The feature commit contains this completed checklist; branch/commit contents are checked immediately after commit. See the delivery, complete-branch review, and verification records for the proved/deferred matrix and baseline failure evidence. No merge or Workflow Studio change is included.
