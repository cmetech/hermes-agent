# Workflow Language Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned workflow-language boundary that preserves every existing unversioned workflow, makes Archon-profile unsupported behavior fail honestly, pins normalized semantics into durable runs, and exposes one bounded authoring contract through CLI, docs, API, and Desktop.

**Architecture:** The companion `name.hermes.yaml` selects `hermes-legacy` or `archon-2026-07`; the loader resolves that profile before field-policy decisions and produces an immutable source definition plus normalized definition and language metadata. Admission binds the normalized-definition digest to the trusted package digest, seals that metadata into the existing JSON snapshot, and resume verifies it before execution. Compatibility decisions and schema inventory remain backend-owned; CLI, documentation, the workflow-builder skill, API, and Desktop consume additive projections without parsing YAML or changing Desktop's read-only operational role.

**Tech Stack:** Python 3.11+ dataclasses, `StrEnum`, PyYAML, SHA-256 canonical JSON, pytest, argparse, Electron/React/TypeScript, Vitest/Testing Library, Docusaurus Markdown, existing upstream-customization merge gates.

## Global Constraints

- Start implementation in an isolated worktree created with `superpowers:using-git-worktrees`, on a feature branch based on `base`; literal `main` is synchronization-only.
- Do not modify, push to, or open a pull request against the upstream Hermes repository.
- Preserve all workflows without `language_compatibility` as `hermes-legacy`; do not reinterpret or reject a workflow that is runnable before this phase.
- New first-party workflows declare `language_compatibility: archon-2026-07` in `name.hermes.yaml`; the companion file is metadata, not a process or background service.
- Keep the existing core agent loop, model tool schema, prompt construction, role alternation, and per-conversation prompt cache unchanged.
- Normalization is deterministic, bounded, local, model-free, network-free, MCP-free, and side-effect-free.
- Under `archon-2026-07`, any accepted field without enforceable runtime behavior is blocking; under `hermes-legacy`, existing behavior remains operational and is diagnosed with stable warnings.
- Desktop remains operational and read-only for definitions. It consumes backend language/findings projections and never parses YAML, normalizes fields, or resolves capabilities.
- Catalog list payloads carry only bounded language status; full schemas and normalized definitions remain in explicit detail/schema calls.
- Extend the existing sealed JSON run snapshot before considering SQLite columns; this phase adds no database migration.
- Every upstream-owned touch must be generic, covered by an invariant test, and recorded in `docs/upstream-customizations/workflow-orchestration.yaml` in the same commit.
- No phase after Phase 1 begins until the complete base merge gate, Desktop tests/typecheck, and upstream merge rehearsal are green.
- This plan implements only Phase 1 from `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`; structured output, artifacts, timeout/retry reinterpretation, loops/includes, provider portability, and `loop_group` remain later gated phases.

---

## Execution Preconditions

Complete these checks before Task 1; they do not modify the shared dirty checkout:

- [ ] Create an isolated worktree from `base` with `superpowers:using-git-worktrees`, then confirm `git branch --show-current` is the new workflow-language feature branch and `git status --short` is empty.
- [ ] Make the ignored plan available in that worktree and force-add it in Task 1's commit; do not commit it onto the unrelated `feature/python-isolation` branch.
- [ ] Bootstrap the Python environment before invoking any test command. Reuse the existing checkout venv when present, otherwise create the locked development environment:

```bash
SHARED_CHECKOUT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
if [ -x "$SHARED_CHECKOUT/.venv/bin/python" ]; then
  ln -s "$SHARED_CHECKOUT/.venv" .venv
elif [ -x "$SHARED_CHECKOUT/venv/bin/python" ]; then
  ln -s "$SHARED_CHECKOUT/venv" venv
else
  uv sync --locked --extra all --extra dev
fi
scripts/run_tests.sh tests/plugins/workflow/test_schema.py -q
```

Expected: the schema baseline passes using the resolved virtual environment.

- [ ] Install or link existing workspace/desktop Node dependencies before the first Desktop or website task, using the shared-worktree convention already implemented by `scripts/test_workflow_merge_gate.sh`; verify with `(cd apps/desktop && npx tsc -p . --noEmit)`.

---

## File Map

### New files

- `plugins/workflow/language.py` — language profile resolution, versioned normalization registry, canonical normalized-definition digest, snapshot binding, and typed compatibility errors.
- `plugins/workflow/language_schema.py` — field inventory and bounded machine-readable authoring contract for the definition and companion file.
- `tests/plugins/workflow/test_language.py` — pure profile, normalization, fingerprint, and legacy/Archon contract tests.
- `tests/plugins/workflow/test_language_snapshot.py` — admission, seal, resume, tamper, unknown-version, and backward-compatibility tests.
- `tests/plugins/workflow/test_language_schema.py` — schema inventory, boundedness, CLI, and parser-parity tests.
- `tests/plugins/workflow/test_workflow_language_desktop_e2e.py` — real catalog/detail middleware projection tests for old and new Desktop clients.
- `website/docs/user-guide/features/workflow-yaml-reference.md` — complete profile-aware authoring reference and support matrix.

### Modified backend files

- `plugins/workflow/models.py` — dependency-neutral language metadata, then shared compatibility finding contracts and the normalized `WorkflowPackage` shape.
- `plugins/workflow/schema.py` — early companion parsing, profile-aware unknown-field policy, and one call to the normalization boundary.
- `plugins/workflow/discovery.py` — cache invalidation includes companion-file identity so profile edits cannot return stale packages.
- `plugins/workflow/compat.py` — stable codes and profile-aware legacy/deferred-field findings without message inspection.
- `plugins/workflow/admission.py` — additive language metadata on `PreparedRunSnapshot`.
- `plugins/workflow/store.py` — seal language metadata into `resources.json` and project it into durable run state.
- `plugins/workflow/scheduler.py` — verify and load the pinned normalizer/profile before executing or resuming.
- `plugins/workflow/scheduled_revalidation.py` — validate language identity as part of sealed scheduled-run verification.
- `plugins/workflow/cli.py` — language-aware validate/doctor payloads and `workflow schema` command.
- `plugins/workflow/catalog_api.py` — bounded list/detail language projections and authoritative compatibility for every catalog source.

### Modified authoring and Desktop files

- `skills/software-development/workflow-builder/SKILL.md` — author new packages with the Archon profile and consume schema/doctor.
- `skills/software-development/workflow-builder/references/portable-schema.md` — generated-contract-aligned field/status reference.
- `skills/software-development/workflow-builder/references/authoring-checklist.md` — profile, schema, unsupported-field, and doctor gates.
- `website/docs/user-guide/features/workflows.md` — link to the YAML reference and explain legacy preservation.
- `apps/desktop/src/types/hermes.ts` — optional additive language projection types for backend-version skew.
- `apps/desktop/src/app/workflows/catalog.tsx` — compact Archon/legacy badge using server-authored status.
- `apps/desktop/src/app/workflows/view-workflow-dialog.tsx` — read-only language summary and migration guidance.
- `apps/desktop/src/app/workflows/index.test.tsx` — catalog behavior and older-backend fallback.
- `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx` — detail status, findings, and no-client-parser assertions.
- `apps/desktop/src/i18n/types.ts`, `en.ts`, `ja.ts`, `zh.ts`, `zh-hant.ts` — all-locale language-status copy.

### Modified merge and CI files

- `docs/upstream-customizations/workflow-orchestration.yaml` — one symbol-level entry in each implementation commit that touches upstream-owned files.
- `scripts/test_workflow_merge_gate.sh` — pin the new backend and Desktop regression suites.
- `tests/scripts/test_workflow_merge_gate.py` — assert the pinned suites and customization entries cannot silently disappear.
- `.github/workflows/ci.yml` — add the new workflow-language tests to the explicit portable workflow job.

---

### Task 1: Define immutable language contracts

**Files:**
- Add: `docs/superpowers/plans/2026-07-25-workflow-language-foundation.md`
- Create: `plugins/workflow/language.py`
- Create: `tests/plugins/workflow/test_language.py`
- Modify: `plugins/workflow/models.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: existing immutable `WorkflowDefinition`, `WorkflowNode`, `freeze_value`, and canonical JSON-safe option values.
- Produces: `WorkflowLanguageProfile`, `WorkflowLanguageSelection`, `WorkflowLanguageMetadata`, `WorkflowLanguageCompatibilityError`, `resolve_language_profile(sidecar)`, `normalize_workflow(source_definition, *, selection, normalizer_version)`, `bind_semantic_fingerprint(package_digest, metadata)`, and `language_projection(metadata, *, semantic_fingerprint=None)`.

- [ ] **Step 1: Write failing pure contract tests**

Add tests that construct one immutable definition and assert exact profiles, deterministic digests, and fail-closed versions:

```python
@pytest.fixture
def definition(tmp_path):
    return WorkflowDefinition(
        name="language-contract",
        description="Language contract fixture",
        nodes=(
            WorkflowNode(
                id="start",
                node_type="bash",
                value="true",
                depends_on=(),
                source_index=0,
                source_line=4,
                options=freeze_value({}),
            ),
        ),
        options=freeze_value({}),
        source_path=tmp_path / "definition.yaml",
    )


def test_absent_language_declaration_resolves_to_legacy():
    selection = resolve_language_profile({})
    assert selection.declared_profile is None
    assert selection.effective_profile is WorkflowLanguageProfile.HERMES_LEGACY


def test_archon_profile_normalization_is_deterministic(definition):
    first = normalize_workflow(
        definition,
        selection=WorkflowLanguageSelection(
            declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        ),
        normalizer_version=1,
    )
    second = normalize_workflow(
        definition,
        selection=WorkflowLanguageSelection(
            declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        ),
        normalizer_version=1,
    )
    assert first.definition == second.definition
    assert first.metadata.normalized_definition_digest == second.metadata.normalized_definition_digest
    assert len(first.metadata.normalized_definition_digest) == 64


def test_unknown_normalizer_version_fails_closed(definition):
    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        normalize_workflow(
            definition,
            selection=WorkflowLanguageSelection(
                declared_profile=WorkflowLanguageProfile.ARCHON_2026_07,
                effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            ),
            normalizer_version=99,
        )
    assert exc.value.code == "workflow_normalizer_version_unsupported"


def test_semantic_fingerprint_binds_package_and_normalized_definition(definition):
    result = normalize_workflow(
        definition,
        selection=WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
        ),
        normalizer_version=1,
    )
    left = bind_semantic_fingerprint("a" * 64, result.metadata)
    assert left != bind_semantic_fingerprint("b" * 64, result.metadata)
    assert len(left) == 64


def test_normalized_digest_excludes_source_location_and_diagnostics(definition):
    left = replace(
        definition,
        source_path=Path("/installed/workflows/example.yaml"),
        nodes=tuple(
            replace(node, source_index=index, source_line=10 + index)
            for index, node in enumerate(definition.nodes)
        ),
    )
    right = replace(
        definition,
        source_path=Path("/sealed/runs/abc/definition.yaml"),
        nodes=tuple(
            replace(node, source_index=100 + index, source_line=900 + index)
            for index, node in enumerate(definition.nodes)
        ),
    )
    selection = WorkflowLanguageSelection(
        declared_profile=None,
        effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
    )
    left_digest = normalize_workflow(
        left, selection=selection, normalizer_version=1
    ).metadata.normalized_definition_digest
    right_digest = normalize_workflow(
        right, selection=selection, normalizer_version=1
    ).metadata.normalized_definition_digest
    assert left_digest == right_digest
```

- [ ] **Step 2: Run the tests and verify the missing-contract failure**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_language.py -q`

Expected: FAIL during import because `plugins.workflow.language` and the new model contracts do not exist.

- [ ] **Step 3: Add the language-only immutable types**

Import `StrEnum` from `enum` in `plugins/workflow/models.py` and add only the language selection/metadata types in this task:

```python
class WorkflowLanguageProfile(StrEnum):
    HERMES_LEGACY = "hermes-legacy"
    ARCHON_2026_07 = "archon-2026-07"


@dataclass(frozen=True)
class WorkflowLanguageSelection:
    declared_profile: WorkflowLanguageProfile | None
    effective_profile: WorkflowLanguageProfile


@dataclass(frozen=True)
class WorkflowLanguageMetadata:
    declared_profile: WorkflowLanguageProfile | None
    effective_profile: WorkflowLanguageProfile
    normalizer_version: int
    normalized_definition_digest: str
```

Do not change `WorkflowPackage` or move `CompatibilityLevel`/`CompatibilityFinding` in Task 1. The sole package constructor is wired atomically with those required fields in Task 2, avoiding an uncompilable commit and avoiding two distinct compatibility classes across a commit boundary.

- [ ] **Step 4: Implement the versioned, identity-only Phase 1 normalizer**

Create `plugins/workflow/language.py` with these exact invariants:

```python
WORKFLOW_NORMALIZER_VERSION = 1
SUPPORTED_NORMALIZER_VERSIONS = frozenset({1})


class WorkflowLanguageCompatibilityError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class NormalizedWorkflow:
    definition: WorkflowDefinition
    metadata: WorkflowLanguageMetadata


def resolve_language_profile(sidecar: Mapping[str, object]) -> WorkflowLanguageSelection:
    declared = sidecar.get("language_compatibility")
    if declared is None:
        return WorkflowLanguageSelection(
            declared_profile=None,
            effective_profile=WorkflowLanguageProfile.HERMES_LEGACY,
        )
    try:
        profile = WorkflowLanguageProfile(declared)
    except (TypeError, ValueError) as exc:
        raise WorkflowLanguageCompatibilityError(
            "workflow_language_profile_unsupported",
            "language_compatibility must be hermes-legacy or archon-2026-07",
        ) from exc
    return WorkflowLanguageSelection(
        declared_profile=profile,
        effective_profile=profile,
    )
```

Canonicalize `name`, `description`, ordered node IDs/types/values/dependencies/options, sorted workflow option keys, profile, and normalizer version with `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Explicitly exclude `WorkflowDefinition.source_path` and every node's `source_index`/`source_line`; those are source-location diagnostics, not execution semantics, and installed and sealed paths differ. `normalized_definition_digest` means the SHA-256 of this profile-and-version-qualified normalized semantic document. Version 1 returns an immutable definition with unchanged execution values; it establishes the only transformation boundary for later phases.

Encode `bind_semantic_fingerprint` as SHA-256 of a canonical JSON object rather than delimiter-sensitive string concatenation:

```python
{
    "package_digest": package_digest,
    "effective_profile": metadata.effective_profile.value,
    "normalizer_version": metadata.normalizer_version,
    "normalized_definition_digest": metadata.normalized_definition_digest,
}
```

- [ ] **Step 5: Run pure tests and existing model tests**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_language.py tests/plugins/workflow/test_schema.py -q`

Expected: PASS; no existing definition value changes.

- [ ] **Step 6: Record the customization and commit**

Append manifest entry `workflow-language-contracts` with owned symbols, the contract test file, merge guidance to preserve dependency-neutral immutable contracts, removal only for an upstream-equivalent versioned normalization boundary, and the current `last_verified_upstream` value already used by adjacent entries.

```bash
git add -f docs/superpowers/plans/2026-07-25-workflow-language-foundation.md
git add plugins/workflow/language.py plugins/workflow/models.py \
  tests/plugins/workflow/test_language.py \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): define language profile contracts"
```

---

### Task 2: Resolve profiles before validation and emit truthful findings

**Files:**
- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/discovery.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_schema.py`
- Modify: `tests/plugins/workflow/test_discovery.py`
- Modify: `tests/plugins/workflow/test_compat_matrix.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/plugins/workflow/test_catalog_api.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_portable_compatibility_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_catalog.py`
- Modify: `tests/plugins/workflow/test_showcase_ai_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_distribution_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_offline_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_resilience_e2e.py`
- Modify: `tests/plugins/workflow/test_showcase_schedule_e2e.py`
- Modify: `tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py`
- Modify: `tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: Task 1 language profiles and normalizer.
- Produces: shared `CompatibilityLevel`/`CompatibilityFinding`; required `WorkflowPackage.source_definition`, `.language`, and `.compatibility_findings`; `load_workflow*()` packages with source/normalized definitions and metadata; `language_compatibility_findings(source_definition, metadata)` folded into `assess_compatibility()`; validate/doctor payloads with stable profile-aware fields.

- [ ] **Step 1: Write failing loader and cache tests**

Cover the profile declaration, default, invalid declaration, Archon unknown-field block, legacy warning, and companion cache identity:

```python
def test_companion_selects_archon_profile(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    assert package.language.effective_profile.value == "archon-2026-07"
    assert package.source_definition == package.definition


def test_unknown_top_level_field_remains_warning_for_legacy(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path, mystery=True)
    package = load_workflow(path)
    issue = next(
        item for item in package.validation_issues
        if item.code == "unknown_top_level_field"
    )
    assert issue.blocking is False


def test_unknown_top_level_field_blocks_archon_profile(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path, mystery=True)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)
    assert exc.value.issues[0].code == "archon_unknown_top_level_field"


def test_identical_bytes_loaded_from_installed_and_sealed_paths_have_same_digest(
    workflow_writer, tmp_path
):
    installed = workflow_writer(tmp_path / "installed")
    sealed = tmp_path / "run" / "definition.yaml"
    sealed.parent.mkdir()
    sealed.write_bytes(installed.read_bytes())
    assert (
        load_workflow(installed).language.normalized_definition_digest
        == load_workflow(sealed).language.normalized_definition_digest
    )
```

In `test_discovery.py`, load once without a companion, create the companion with the same workflow YAML untouched, load again, and assert the cached package changes from legacy to Archon.

- [ ] **Step 2: Run loader tests and verify they fail**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_discovery.py -q`

Expected: FAIL because the sidecar field is rejected and discovery does not include companion identity in its cache signature.

- [ ] **Step 3: Move findings and wire the package shape atomically with the loader**

Move `CompatibilityLevel` and `CompatibilityFinding` from `compat.py` into the dependency-neutral `models.py`, adding `severity`, `effective_profile`, and `migration` as optional/defaulted fields. Import and re-export those exact classes from `compat.py` so existing imports remain source-compatible; there must never be two runtime classes with these names.

Add `source_definition`, `language`, and `compatibility_findings` as required fields before the existing defaulted `validation_issues` field on `WorkflowPackage`. In the same edit, update the sole constructor in `schema.py`; `definition` remains the normalized definition so existing trust, topology, scheduler, and executor consumers stay unchanged. Do not stop or commit while the dataclass and constructor disagree.

Then refactor `schema.py` so `_load_workflow_bytes` obtains the companion mapping before applying unknown top-level policy. Add `language_compatibility` to `_SIDECAR_FIELDS`, require it to be a string enum, and split sidecar validation into parsing plus a later node-reference check. Do not loosen byte, YAML, path, secret, trust, or node-reference validation.

Call the Task 1 normalizer exactly once:

```python
selection = resolve_language_profile(sidecar)
normalized = normalize_workflow(
    source_definition,
    selection=selection,
    normalizer_version=WORKFLOW_NORMALIZER_VERSION,
)
return WorkflowPackage(
    source_definition=source_definition,
    definition=normalized.definition,
    language=normalized.metadata,
    compatibility_findings=language_compatibility_findings(
        source_definition, normalized.metadata
    ),
    # existing package identity fields remain unchanged
)
```

For `archon-2026-07`, turn unknown top-level fields into `WorkflowValidationError` code `archon_unknown_top_level_field`; legacy keeps the current nonblocking warning. Existing rejected `steps`/`kind` shapes remain rejected with their current actionable errors under both profiles.

- [ ] **Step 4: Make discovery cache depend on companion content**

Change `_PARSE_CACHE` signatures from only the workflow stat/digest to a tuple containing workflow identity and companion identity. Represent an absent companion as `(False, 0, 0, "")`; represent a present companion as `(True, size, mtime_ns, sha256)`. Read through the same bounded loader path after a cache miss. A companion create, edit, or delete must invalidate the entry even when the workflow YAML is byte-identical.

- [ ] **Step 5: Write failing profile-finding and doctor tests**

Add table-driven assertions for these stable Phase 1 decisions:

| Profile | Declared field | Code | Blocking | Migration |
|---|---|---|---:|---|
| legacy | profile absent or explicit legacy | `legacy_language_profile` | false | declare `archon-2026-07` after doctor is clean |
| legacy | `timeout` | `legacy_timeout_seconds` | false | convert seconds to milliseconds before profile change |
| legacy | `retry.max_attempts` | `legacy_retry_total_attempts` | false | account for Archon retry-count semantics in Phase 3 |
| legacy | `output_format` | `legacy_output_format_post_validation` | false | wait for Phase 2 enforcement before profile change |
| legacy | `output_type` | `legacy_output_type_not_published` | false | wait for Phase 2 typed artifacts |
| Archon | `timeout` | `archon_timeout_semantics_unavailable` | true | Phase 3 |
| Archon | `retry` | `archon_retry_semantics_unavailable` | true | Phase 3 |
| Archon | `output_format` | `archon_output_format_unavailable` | true | Phase 2 |
| Archon | `output_type` | `archon_output_type_unavailable` | true | Phase 2 |
| Archon | `maxBudgetUsd` | `archon_budget_enforcement_unavailable` | true | Phase 5 |
| Archon | `sandbox` | `archon_sandbox_enforcement_unavailable` | true | Phase 5 |

Assert every finding includes `severity`, `effective_profile`, and nonempty `migration` where the table specifies one. Assert an Archon workflow containing only currently enforceable fields stays runnable.

- [ ] **Step 6: Run compatibility tests and verify the old behavior fails**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_cli.py -q`

Expected: FAIL because findings still derive some codes from message strings and have no profile metadata.

- [ ] **Step 7: Centralize stable finding creation and remove CLI message parsing**

Change `compat.py::_finding()` to require an explicit stable code and start `assess_compatibility()` with `package.compatibility_findings`. When folding `package.validation_issues`, preserve `ValidationIssue.code` instead of replacing it with the default `compatibility` code. Add environment/provider findings without duplicating `(code, path)` pairs.

Delete `cli.py::_compatibility_code()` and `_coded_compatibility_findings()` message inspection; `doctor_package()` consumes the already-coded compatibility findings directly. `cli.py`, not `compat.py`, owns the functions being removed.

Use severity `error` for blocking findings, `warning` for mapped legacy behavior, and `info` for ordinary portable mappings. Preserve existing `level`, `message`, and `blocking` JSON fields so old Desktop clients continue to decode responses.

- [ ] **Step 8: Project language metadata through validate and doctor**

Add this bounded object to both JSON payloads and a single text line to non-JSON output:

```python
"language": {
    "declared_profile": package.language.declared_profile.value
        if package.language.declared_profile else None,
    "effective_profile": package.language.effective_profile.value,
    "normalizer_version": package.language.normalizer_version,
    "normalized_definition_digest": package.language.normalized_definition_digest,
    "legacy": package.language.effective_profile is WorkflowLanguageProfile.HERMES_LEGACY,
}
```

Doctor must expose the same structured findings in its ordinary `findings` array and `--compat-report` array; it must not recalculate codes from prose.

- [ ] **Step 9: Run focused and downstream finding-contract tests**

Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_discovery.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_showcase_catalog.py \
  tests/plugins/workflow/test_portable_compatibility_e2e.py \
  tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py \
  tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py \
  tests/plugins/workflow/test_showcase_ai_e2e.py \
  tests/plugins/workflow/test_showcase_distribution_e2e.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py \
  tests/plugins/workflow/test_showcase_resilience_e2e.py \
  tests/plugins/workflow/test_showcase_schedule_e2e.py -q
```

Expected: PASS. Update exact finding-array assertions only where the intentional `legacy_language_profile` warning is additive; do not weaken trust, compatibility, or showcase behavior assertions.

- [ ] **Step 10: Record customization and commit**

Append manifest entry `workflow-language-profile-normalization` covering the parser, cache, compatibility, CLI symbols, stable-code invariant tests, and the rule that legacy remains permissive while declared Archon fails closed.

```bash
git add plugins/workflow/language.py plugins/workflow/models.py \
  plugins/workflow/schema.py plugins/workflow/discovery.py \
  plugins/workflow/compat.py plugins/workflow/cli.py \
  tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_discovery.py \
  tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_portable_compatibility_e2e.py \
  tests/plugins/workflow/test_showcase_catalog.py \
  tests/plugins/workflow/test_showcase_ai_e2e.py \
  tests/plugins/workflow/test_showcase_distribution_e2e.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py \
  tests/plugins/workflow/test_showcase_resilience_e2e.py \
  tests/plugins/workflow/test_showcase_schedule_e2e.py \
  tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py \
  tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): normalize declared language profiles"
```

---

### Task 3: Pin normalized semantics in admission and verify resume

**Files:**
- Create: `tests/plugins/workflow/test_language_snapshot.py`
- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/scheduled_revalidation.py`
- Modify: `tests/plugins/workflow/test_schedule_revalidation.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: normalized definition digest and trusted `WorkflowPackageDigest.sha256` from Tasks 1–2.
- Produces: `WorkflowLanguageSnapshot`, `make_language_snapshot(package, package_digest)`, `read_language_snapshot(value: object | None)`, `verify_language_snapshot(package, package_digest, snapshot)`, additive run projection field `language`, and `load_workflow_snapshot(..., normalizer_version=WORKFLOW_NORMALIZER_VERSION)`.

- [ ] **Step 1: Write failing admission and resume tests**

Follow the suite's established inline construction style; do not assume new global fixtures. Define these module-local helpers in `test_language_snapshot.py`:

```python
def _profile_package(workflow_writer, root: Path, *, profile: str):
    path = workflow_writer(root / "package", name=f"{profile}-snapshot")
    if profile == "archon-2026-07":
        path.with_name(f"{path.stem}.hermes.yaml").write_text(
            "language_compatibility: archon-2026-07\n", encoding="utf-8"
        )
    return load_workflow(path)


def _start(store: RunStore, package, *, key: str):
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return prepared, admitted


def _prepare_pre_language_snapshot(store: RunStore, package):
    prepared = store.prepare_run_snapshot(package)
    resources_path = prepared.staging_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources.pop("language")
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    resources_path.write_bytes(encoded)
    reserved_bytes = sum(
        path.stat().st_size
        for path in prepared.staging_directory.rglob("*")
        if path.is_file()
    )
    return replace(
        prepared,
        input_manifest_digest=sha256(encoded).hexdigest(),
        reserved_bytes=reserved_bytes,
        language=None,
    )
```

Use those helpers in the durable-path tests:

```python
def test_admission_seals_package_bound_language_metadata(tmp_path, workflow_writer):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    resources = json.loads((prepared.staging_directory / "resources.json").read_text())
    assert resources["language"]["effective_profile"] == "archon-2026-07"
    assert resources["language"]["normalizer_version"] == 1
    assert len(resources["language"]["normalized_definition_digest"]) == 64
    assert len(resources["language"]["semantic_fingerprint"]) == 64


def test_resume_rejects_unknown_pinned_normalizer(tmp_path, workflow_writer):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="unknown-version")
    resources_path = store.run_directory(admitted.run_id) / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources["language"]["normalizer_version"] = 99
    resources_path.write_text(json.dumps(resources), encoding="utf-8")
    scheduler = RunScheduler(store)
    try:
        with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
            scheduler._load_run_package(admitted.run_id)
        assert exc.value.code == "workflow_normalizer_version_unsupported"
    finally:
        scheduler.shutdown(deadline_seconds=2)


def test_legacy_snapshot_without_language_metadata_still_loads(tmp_path, workflow_writer):
    package = _profile_package(workflow_writer, tmp_path, profile="hermes-legacy")
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-v0",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    scheduler = RunScheduler(store)
    try:
        loaded = scheduler._load_run_package(admitted.run_id)
        assert loaded.language.effective_profile.value == "hermes-legacy"
    finally:
        scheduler.shutdown(deadline_seconds=2)
```

Also assert: changing the installed source after admission does not affect resume; changing sealed `policy.yaml` from Archon to legacy fails; changing the normalized digest or fingerprint in `resources.json` fails; `clone_prepared_snapshot` preserves metadata; scheduled revalidation includes language identity; coordinator polling does not reopen an installed workflow.

- [ ] **Step 2: Run snapshot tests and verify they fail**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_schedule_revalidation.py -q`

Expected: FAIL because run snapshots and scheduler projections do not carry language metadata.

- [ ] **Step 3: Define the sealed language snapshot contract**

Add an immutable `WorkflowLanguageSnapshot` in `language.py`:

```python
@dataclass(frozen=True)
class WorkflowLanguageSnapshot:
    effective_profile: WorkflowLanguageProfile
    normalizer_version: int
    normalized_definition_digest: str
    semantic_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "effective_profile": self.effective_profile.value,
            "normalizer_version": self.normalizer_version,
            "normalized_definition_digest": self.normalized_definition_digest,
            "semantic_fingerprint": self.semantic_fingerprint,
        }
```

`make_language_snapshot()` binds the package digest through Task 1's fingerprint function. `read_language_snapshot()` accepts only exact keys, known profile strings, integer version values that are not booleans, and 64-character lowercase SHA-256 strings. It raises typed codes `workflow_language_snapshot_invalid`, `workflow_language_profile_unsupported`, or `workflow_normalizer_version_unsupported`.

`verify_language_snapshot()` checks exact equality of profile, version, normalized digest, and the recomputed package-bound fingerprint. These digests are integrity identifiers rather than secrets. Use code `workflow_language_snapshot_mismatch` for any mismatch.

- [ ] **Step 4: Seal and project metadata without a database migration**

Add `language: Mapping[str, object] | None = None` at the end of `PreparedRunSnapshot` to preserve positional construction. In `prepare_run_snapshot()`:

1. compute the existing package digest;
2. build the language snapshot;
3. insert `language_snapshot.to_dict()` under `resources.json["language"]`;
4. retain the existing SHA-256 of the whole `resources.json` as `input_manifest_digest`;
5. copy the same bounded object into `PreparedRunSnapshot.language`.

Copy it in `clone_prepared_snapshot()`, and add top-level `projection["language"]` in `_publish_reserved_run()`. `prepare_empty_snapshot()` leaves the field `None`; no existing synthetic/test snapshot changes meaning.

- [ ] **Step 5: Verify pinned metadata before scheduler execution**

Extend `schema.py::load_workflow_snapshot()` and its private loader path with keyword-only `normalizer_version: int = WORKFLOW_NORMALIZER_VERSION`; pass that value to `normalize_workflow()` and reject unsupported versions through the typed language error.

Then change the verified `RunScheduler._load_run_package(run_id)` path to:

```python
projection = self.store.load_run(run_id)
resources = json.loads((run_directory / "resources.json").read_bytes())
snapshot = read_language_snapshot(resources.get("language"))
package = load_workflow_snapshot(
    definition,
    workflow_bytes=definition.read_bytes(),
    sidecar_bytes=policy.read_bytes() if policy.is_file() else None,
    normalizer_version=(
        snapshot.normalizer_version
        if snapshot is not None
        else WORKFLOW_NORMALIZER_VERSION
    ),
)
if snapshot is None and (
    package.language.effective_profile is not WorkflowLanguageProfile.HERMES_LEGACY
):
    raise WorkflowLanguageCompatibilityError(
        "workflow_language_snapshot_missing",
        "declared Archon workflow is missing admitted language metadata",
    )
if snapshot is not None:
    verify_language_snapshot(
        package,
        str(projection["definition_digest"]),
        snapshot,
    )
return package
```

Missing metadata is accepted only for a legacy package and projected as legacy/unknown. A declared Archon policy with missing metadata fails closed. Extend scheduled `verify_sealed_snapshot()` to compare the bounded run projection language object with `resources.json["language"]` after its existing whole-file digest checks.

- [ ] **Step 6: Run durability, fault, and performance tests**

Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_schedule_revalidation.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_performance_bounds.py -q
```

Expected: PASS, including crash/retry and bounded coordinator behavior.

- [ ] **Step 7: Record customization and commit**

Append manifest entry `workflow-language-admission-pinning` with the sealed JSON fields, typed fail-closed errors, no-SQLite-migration decision, legacy-snapshot fallback, and scheduler/revalidation tests.

```bash
git add plugins/workflow/language.py plugins/workflow/admission.py \
  plugins/workflow/schema.py plugins/workflow/store.py plugins/workflow/scheduler.py \
  plugins/workflow/scheduled_revalidation.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_schedule_revalidation.py \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): pin workflow semantics at admission"
```

---

### Task 4: Publish the machine-readable authoring contract

**Files:**
- Create: `plugins/workflow/language_schema.py`
- Create: `tests/plugins/workflow/test_language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: profile enum, normalizer version, loader field rules, stable compatibility codes.
- Produces: `workflow_authoring_contract(profile) -> dict[str, object]`, `definition_json_schema(profile)`, `sidecar_json_schema(profile)`, and CLI `hermes workflow schema --profile PROFILE --json`.

- [ ] **Step 1: Write failing schema authority tests**

Assert exact envelope, bounded serialization, profile selection, and parser parity:

```python
def test_archon_authoring_contract_is_bounded_and_versioned():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    assert contract["schema_version"] == 1
    assert contract["profile"] == "archon-2026-07"
    assert contract["normalizer_version"] == 1
    assert contract["definition_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert len(json.dumps(contract).encode()) < 256_000


def test_parser_field_sets_come_from_authoring_inventory():
    assert TOP_LEVEL_FIELDS == definition_field_names()
    assert COMMON_NODE_FIELDS == common_node_field_names()
    assert SIDECAR_FIELDS == sidecar_field_names()
```

Add tests that the contract marks Phase 1 deferred Archon fields with their blocking compatibility codes and that legacy marks the same fields with warning codes. Ensure secret values and runtime data never appear.

- [ ] **Step 2: Run tests and verify the missing schema failure**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q`

Expected: FAIL because the generator and inventory functions do not exist.

- [ ] **Step 3: Implement one field inventory and two JSON Schemas**

In `language_schema.py`, define immutable specs for top-level, common node, per-node, hook, retry, and companion fields. Each spec includes YAML name, JSON type/shape, applicable node types, enforcement phase, and compatibility code per profile. Export frozenset accessors used by `schema.py`; remove the duplicated `TOP_LEVEL_FIELDS`, `COMMON_NODE_FIELDS`, and `_SIDECAR_FIELDS` literals there.

Enforce this import direction: `language_schema.py` may import only the standard library plus dependency-neutral contracts from `models.py` and `language.py`; `schema.py` imports inventory accessors from `language_schema.py`. `language_schema.py` must never import `schema.py`, its validators, or `_fail`. Semantic validation stays in `schema.py`, preventing a load-time cycle.

Return this envelope:

```python
{
    "schema_version": 1,
    "profile": profile.value,
    "normalizer_version": WORKFLOW_NORMALIZER_VERSION,
    "definition_schema": definition_json_schema(profile),
    "sidecar_schema": sidecar_json_schema(profile),
    "compatibility_codes": compatibility_code_catalog(profile),
}
```

Both schemas use Draft 2020-12, `additionalProperties: false` for Archon, required definition fields `name`, `description`, `nodes`, and a sidecar enum for `language_compatibility`. The legacy definition schema sets `additionalProperties: true` to reflect preserved warning behavior. Include `x-hermes-status` and `x-hermes-compatibility-code` annotations on deferred fields; annotations do not make a deferred field runnable.

- [ ] **Step 4: Add the CLI command and failing CLI tests**

Register:

```python
schema_parser = actions.add_parser(
    "schema", help="Print the workflow authoring contract"
)
schema_parser.add_argument(
    "--profile",
    choices=("hermes-legacy", "archon-2026-07"),
    default="archon-2026-07",
)
_json_flag(schema_parser)
```

Dispatch `schema` before commands that require workflow discovery. JSON mode emits one compact machine object; text mode emits indented JSON to stdout with no filesystem writes. Update the root usage string to include `schema`.

Tests must assert default Archon selection, explicit legacy selection, deterministic byte-for-byte JSON across two calls, exit code 0 without a workflow directory, and no model/MCP/network calls.

- [ ] **Step 5: Run focused tests and command smoke test**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_cli.py -q
.venv/bin/python -m hermes_cli.main workflow schema --profile archon-2026-07 --json
```

Expected: tests PASS; command exits 0 and prints one JSON object with `profile` equal to `archon-2026-07`.

- [ ] **Step 6: Record customization and commit**

Append manifest entry `workflow-language-schema-cli` with the inventory accessors, generated contract, CLI command, parser-parity test, bounded size, and merge guidance that schema annotations must remain truthful to execution.

```bash
git add plugins/workflow/language_schema.py plugins/workflow/schema.py \
  plugins/workflow/cli.py tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_cli.py \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): publish authoring schema"
```

---

### Task 5: Expose authoritative language status in API and Desktop

**Files:**
- Create: `tests/plugins/workflow/test_workflow_language_desktop_e2e.py`
- Modify: `plugins/workflow/catalog_api.py`
- Modify: `tests/plugins/workflow/test_catalog_api.py`
- Modify: `tests/plugins/workflow/test_workflow_detail_api.py`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/app/workflows/catalog.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: server-owned language metadata and compatibility findings from Tasks 2–4.
- Produces: additive optional `language` API object and Desktop `WorkflowLanguageStatus`; no Desktop-side YAML or compatibility logic.

- [ ] **Step 1: Write failing backend projection tests**

For project, profile, and verified showcase catalog entries, assert the list projection includes only:

```json
{"effective_profile":"hermes-legacy","legacy":true}
```

or:

```json
{"effective_profile":"archon-2026-07","legacy":false}
```

Detail adds `declared_profile`, `normalizer_version`, and `normalized_definition_digest`. It does not include the full authoring schema. Project/profile list entries receive only compatibility `level` and `runnable`; verified showcases retain their existing compatibility payload for backward compatibility. Detailed findings remain in the bounded detail response so Run stays backend-authoritative without multiplying catalog payload size.

In the real middleware E2E test, call the actual catalog and detail endpoints with temporary project/profile workflows. Assert existing identity/trust/input/run-support fields remain unchanged, while intentionally adding `language` and the compatibility summary. Archon deferred fields make `compatibility.runnable` false, legacy stays listed, and no file is modified.

Document and test the intended old-client behavior change: because older Desktop builds already honor optional `compatibility.runnable`, a new backend will cause those clients to disable Run and show their existing incompatible badge for project/profile workflows that the backend now reports as unrunnable. This is the desired fail-closed behavior, not field-level backward equivalence.

- [ ] **Step 2: Run backend tests and verify missing projections**

Run: `scripts/run_tests.sh tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py -q`

Expected: FAIL because list/detail entries have no language field and user entries omit compatibility.

- [ ] **Step 3: Add bounded additive projections**

Extend `CatalogEntry` with required backend field `language: dict[str, object]`. Add `_catalog_language_projection(package, detail=False)` that emits only status in lists and expanded language metadata in detail. Keep fingerprints out of catalog lists. Add `_compatibility_summary(compatibility)` returning only `level` and `runnable` for project/profile entries; preserve `_compatibility_projection(compatibility)` for verified showcases and detail responses.

Do not change discovery limits, trust checks, input redaction, topology bounds, coordinator checks, source identity, or run-support logic.

- [ ] **Step 4: Write failing Desktop tests for new and old backends**

Add fixtures with and without `language`. Assert:

- Archon shows an `Archon 2026-07` badge.
- Legacy shows `Legacy semantics` and the detail dialog explains that existing behavior is preserved.
- A missing language object from an older backend renders exactly the existing source row without an error or fabricated profile.
- Blocking server findings still disable Run through `compatibility.runnable`; the client does not inspect YAML fields or finding codes to make that decision.
- The Review Run preflight displays the same server-authored effective profile beside the digest-bound trust/risk review; it does not recompute the profile.
- View remains read-only: opening/closing, switching diagram/definition, and copying normalized JSON issue no mutation request.

- [ ] **Step 5: Run Desktop tests and verify missing UI/types**

Run:

```bash
cd apps/desktop
npx vitest run \
  src/app/workflows/index.test.tsx \
  src/app/workflows/review-run-dialog.test.tsx \
  src/app/workflows/view-workflow-dialog.test.tsx
```

Expected: FAIL on missing `language` type and labels.

- [ ] **Step 6: Implement additive optional Desktop types and presentation**

Add:

```typescript
export interface WorkflowLanguageStatus {
  declared_profile?: null | string
  effective_profile: 'archon-2026-07' | 'hermes-legacy'
  legacy: boolean
  normalized_definition_digest?: string
  normalizer_version?: number
}

export interface WorkflowDefinition {
  // existing fields
  language?: WorkflowLanguageStatus
}
```

In `catalog.tsx`, render a small badge beside the existing source/incompatible badges only when `item.language` exists. In `view-workflow-dialog.tsx`, render one bounded `Alert` above the segmented control: Archon shows the effective profile; legacy shows preservation/migration copy. In `review-run-dialog.tsx`, show the same language badge in the trust/risk preflight section so the profile is visibly reviewed alongside the exact package digest and trust state. Display no digest in the catalog; detail/preflight may show the normalizer version and an abbreviated normalized digest with the full value in `title`.

Add exact keys `workflowLanguageArchon`, `workflowLanguageLegacy`, `workflowLanguageLegacyDescription`, `workflowLanguageNormalizer`, and `workflowLanguageDigest` to the type and all four locale files. Do not add a fallback hard-coded English branch.

- [ ] **Step 7: Run backend, Desktop, typecheck, and performance-sensitive tests**

Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_workflow_detail_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py -q
cd apps/desktop
npx vitest run \
  src/app/workflows/index.test.tsx \
  src/app/workflows/review-run-dialog.test.tsx \
  src/app/workflows/view-workflow-dialog.test.tsx \
  src/app/workflows/workflow-operations.e2e.test.tsx
npx tsc -p . --noEmit
```

Expected: all commands PASS; Desktop remains read-only and its existing operational flow remains intact, with only the deliberate backend-authored fail-closed compatibility behavior described in Step 1.

- [ ] **Step 8: Record customization and commit**

Append manifest entry `workflow-language-desktop-status` covering backend projections, optional skew-safe type, read-only badges/alert, all-locale parity, authoritative Run gating, E2E test, and removal only for an upstream-equivalent language projection.

```bash
git add plugins/workflow/catalog_api.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_workflow_detail_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py \
  apps/desktop/src/types/hermes.ts apps/desktop/src/app/workflows/catalog.tsx \
  apps/desktop/src/app/workflows/index.test.tsx \
  apps/desktop/src/app/workflows/review-run-dialog.tsx \
  apps/desktop/src/app/workflows/review-run-dialog.test.tsx \
  apps/desktop/src/app/workflows/view-workflow-dialog.tsx \
  apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx \
  apps/desktop/src/i18n/types.ts apps/desktop/src/i18n/en.ts \
  apps/desktop/src/i18n/ja.ts apps/desktop/src/i18n/zh.ts \
  apps/desktop/src/i18n/zh-hant.ts \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(desktop): surface workflow language status"
```

---

### Task 6: Align website and first-party authoring guidance

**Files:**
- Create: `website/docs/user-guide/features/workflow-yaml-reference.md`
- Modify: `website/docs/user-guide/features/workflows.md`
- Modify: `skills/software-development/workflow-builder/SKILL.md`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`
- Modify: `skills/software-development/workflow-builder/references/authoring-checklist.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: generated contract envelope, stable codes, and exact Phase 1 behavior.
- Produces: one user-facing YAML reference and an authoring skill that defaults new packages to the declared Archon profile and refuses blocked fields.

- [ ] **Step 1: Establish RED behavior evidence for the current workflow-builder skill**

Use `superpowers:writing-skills` for this task. Before changing the skill, run its scenario-based pressure-test workflow against the current `workflow-builder` using these user requests and record the outputs in the Task 6 SDD report:

- "Create a new workflow that uses output_format and a 30-second timeout."
- "Create a workflow shared by an older Hermes install and this install."
- "Use the create-workflow skill's steps/produces format for Hermes."

RED evidence is established when the current skill fails to require the generated Archon contract, fails to surface deferred-field choices, fails to warn about the backend version floor, or presents OTTO V1 fields as authoritative. Evaluate observable proposed files and user guidance, not source-text presence.

- [ ] **Step 2: Write the complete profile-aware YAML reference**

The new website page must include:

1. package layout and the fact that the companion is metadata, not a process;
2. `hermes-legacy` preservation and `archon-2026-07` opt-in;
3. complete top-level, node-common, per-node, hook, retry, and companion tables driven by the generated inventory;
4. status columns `enforced`, `mapped`, `legacy-only`, and `blocked pending phase`;
5. exact units and present semantics without claiming future behavior;
6. examples for a minimal parameterless workflow and an immutable-input package;
7. schema and doctor commands;
8. migration path: validate legacy, review warnings, convert units/semantics only in their implementing phase, declare Archon, rerun doctor;
9. explicit statement that structured `output_format`, `output_type`, Archon timeout/retry meanings, enforceable budget, and sandbox portability are blocked in Phase 1;
10. explicit rejection of OTTO V1 `steps`, `produces`, `context_from`, `verify`, and `iterate` shapes.
11. backend-version floor: an Archon companion is intentionally unreadable to pre-Phase-1 backends because they reject unknown companion fields; mixed-version or shared-workflow installations must stay on unversioned/explicit `hermes-legacy` until every consuming runtime supports `language_compatibility`.

Update `workflows.md` with a short Language profiles section and link to the new reference.

- [ ] **Step 3: Make workflow-builder consume the authoritative contract**

Update the skill to write this companion declaration for every newly authored package:

```yaml
language_compatibility: archon-2026-07
```

Before writing YAML, instruct it to obtain the contract with:

```bash
PRODUCT_CLI workflow schema --profile archon-2026-07 --json
```

It must refuse fields whose contract annotation is blocking, run validate and doctor after writing, and never silently downgrade a requested Archon field to legacy. When the author needs a Phase 1-deferred field today, the skill must stop and explain the exact unavailable semantics, then offer only two explicit choices: omit the field and remain on `archon-2026-07`, or deliberately author `hermes-legacy` with the current legacy meaning and doctor warning. The user—not the skill—selects legacy compatibility. For timeout/resource needs that are enforceable through existing Hermes companion `limits`/`resource_limits`, prefer those policy controls and describe them as Hermes execution policy, not Archon field semantics.

Add a warning that the globally installed legacy `create-workflow` skill emits incompatible OTTO V1 `steps` documents and is not a Hermes authoring authority; recommend this bundled `workflow-builder` skill instead. Also warn that Archon-profile packages require a Phase-1-capable backend and should not be placed in a workflow directory shared with older brand/runtime installations. Preserve the existing digest-bound trust confirmation and package-resource rules.

- [ ] **Step 4: Verify GREEN behavior and build the documentation**

Repeat the three Step 1 pressure-test scenarios through `superpowers:writing-skills`. Record evidence that the revised skill now proposes the declared Archon companion, queries the generated contract, blocks deferred semantics with explicit choices, warns about older runtimes, and rejects OTTO V1 authoring shapes. The evaluation must inspect the skill's resulting authoring behavior and proposed package, not grep its source.

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_language_schema.py -q
npm --prefix website run build
```

Expected: schema behavior tests PASS; Docusaurus resolves the new page and links; the scenario-based skill evaluation is GREEN.

- [ ] **Step 5: Record customization and commit**

Append manifest entry `workflow-language-authoring-reference` covering the generated-contract consumption behavior, first-party Archon default, legacy preservation copy, backend version floor, and explicit incompatible-skill guidance.

```bash
git add website/docs/user-guide/features/workflow-yaml-reference.md \
  website/docs/user-guide/features/workflows.md \
  skills/software-development/workflow-builder/SKILL.md \
  skills/software-development/workflow-builder/references/portable-schema.md \
  skills/software-development/workflow-builder/references/authoring-checklist.md \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "docs(workflow): publish language authoring contract"
```

---

### Task 7: Pin regression gates and prove Phase 1 integration

**Files:**
- Modify: `scripts/test_workflow_merge_gate.sh`
- Modify: `tests/scripts/test_workflow_merge_gate.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: all Phase 1 backend, snapshot, schema, API, Desktop, and documentation tests.
- Produces: mandatory base/CI coverage and merge-rehearsal evidence that future upstream syncs cannot silently drop the language boundary.

- [ ] **Step 1: Write failing meta-gate assertions**

In `tests/scripts/test_workflow_merge_gate.py`, assert the base script names these suites:

```text
tests/plugins/workflow/test_language.py
tests/plugins/workflow/test_language_snapshot.py
tests/plugins/workflow/test_language_schema.py
tests/plugins/workflow/test_workflow_language_desktop_e2e.py
src/app/workflows/index.test.tsx
src/app/workflows/view-workflow-dialog.test.tsx
```

Assert the manifest contains the six implementation customization IDs plus the intended `workflow-language-regression-gates` entry, and that CI names the three new backend unit suites plus the middleware E2E suite.

- [ ] **Step 2: Run the meta-gate test and verify it fails**

Run: `scripts/run_tests.sh tests/scripts/test_workflow_merge_gate.py -q`

Expected: FAIL because the new suites are not pinned.

- [ ] **Step 3: Add tests to the base gate and portable CI job**

Add the four Python suites to the existing pytest invocation in `scripts/test_workflow_merge_gate.sh`; keep the two Desktop suites in the existing Vitest invocation. Add the same Python tests to the explicit portable workflow test list in `.github/workflows/ci.yml`. Do not create a new workflow, background service, or duplicate dependency-install step.

- [ ] **Step 4: Record the gate customization and commit**

Append manifest entry `workflow-language-regression-gates` listing the shell gate, meta-test, CI workflow, owned suite names, preservation guidance, and current upstream baseline.

Run: `scripts/run_tests.sh tests/scripts/test_workflow_merge_gate.py -q`

Expected: PASS.

```bash
git add scripts/test_workflow_merge_gate.sh \
  tests/scripts/test_workflow_merge_gate.py .github/workflows/ci.yml \
  docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "test(workflow): gate language compatibility foundation"
```

- [ ] **Step 5: Run the focused Phase 1 backend suite**

Run:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_language.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_discovery.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_workflow_detail_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py \
  tests/plugins/workflow/test_schedule_revalidation.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_performance_bounds.py -q
```

Expected: PASS.

- [ ] **Step 6: Run Desktop and documentation verification**

Run:

```bash
cd apps/desktop
npx vitest run \
  src/app/workflows/index.test.tsx \
  src/app/workflows/view-workflow-dialog.test.tsx \
  src/app/workflows/workflow-operations.e2e.test.tsx
npx tsc -p . --noEmit
cd ../..
npm --prefix website run build
git diff --check
```

Expected: all commands PASS and `git diff --check` emits no output.

- [ ] **Step 7: Run the complete base workflow merge gate**

Run: `PYTHON_BIN=.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base`

Expected: exit 0 and final output contains `TESTED_BASE_SHA=<40-character commit>`.

- [ ] **Step 8: Run the controlled upstream merge rehearsal**

Run:

```bash
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml
scripts/test_workflow_upstream_merge.sh
```

Expected: both exit 0; every Phase 1 entry receives a preserve/adapt/remove decision under the rehearsal and no upstream repository is mutated.

- [ ] **Step 9: Review acceptance evidence before merging to base**

Verify from test output and one manual CLI sample that:

- an unchanged unversioned workflow validates, doctors, admits, resumes, and appears in Desktop as legacy;
- an Archon-profile workflow with only enforced fields validates, admits, resumes, and appears as Archon;
- every deferred Archon field blocks with its stable code and migration text;
- changing the companion invalidates discovery cache;
- changing sealed language identity fails before node execution;
- old run snapshots and older-backend Desktop payloads remain operational;
- no full schema is present in catalog lists;
- no core model tool or prompt-cache surface changed;
- no upstream PR, push, or direct upstream mutation occurred.

If any item is false, do not merge the feature branch into `base`. Fix it with a new test-first commit, rerun Steps 5–8, and preserve the corrective symbol in the customization ledger.

---

## Phase Boundary

After Task 7 is green, merge the completed feature branch into `base` using the repository's normal local integration process. Stop there. Before Phase 2 begins, create a separate Phase 2 implementation plan from the approved design for provider-enforced `output_format`, one bounded isolated repair, canonical typed references, and durable `output_type` artifacts. Do not implement those behaviors opportunistically in this Phase 1 branch.
