# Workflow Package Marketplace Implementation Plan

**Execution pause (2026-09-04):** Tasks 1–13 are complete; historical unchecked boxes below are not a restart queue. Task 14's implementation at `c89f36c6b8` needs changes and must not be approved or merged. Task 15 has not started. The [draft lifecycle recovery amendment](../specs/2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md) and [replacement remaining-work plan](2026-09-04-workflow-marketplace-lifecycle-recovery.md) replace the remaining sequence once explicitly approved. Do not execute the monolithic Task 14 or resume production work before that approval. For future Python checks, repository `AGENTS.md` requires `scripts/run_tests.sh`; old direct-pytest examples below are historical.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Use
> `superpowers:test-driven-development` for every production change,
> `superpowers:requesting-code-review` before integration, and
> `superpowers:verification-before-completion` before reporting success.

**Goal:** Add a Git-backed workflow-package marketplace shared by the Hermes CLI and co-worker Desktop, with public/private sources, transactional install/update/removal, exact provenance, and trust that remains separate from installation.

**Architecture:** A focused `plugins.workflow.marketplace` domain layer owns package contracts, safe Git sources, catalog state, provenance, transactions, and review models. Existing workflow CLI and plugin API modules remain thin adapters; Desktop calls the authenticated profile/connection-scoped API and never performs Git or filesystem work in the renderer. Marketplace-installed workflows continue through ordinary discovery, compilation, admission, scheduling, and execution, with an additional distribution digest bound into their existing per-workflow trust identity.

**Tech Stack:** Python 3.11+, Pydantic 2, FastAPI, Git subprocesses without shell interpolation, existing workflow locks/resource budgets/trust store, pytest, Electron 41, React 19, TypeScript 6, TanStack Query, Vitest, and Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-03-workflow-package-marketplace-design.md`

## Global Constraints

- Work only in `.worktrees/workflow-package-marketplace` on `feat/workflow-package-marketplace`; do not merge to `base` without explicit user approval.
- YAML remains the sole workflow graph and node-behavior authority. Package manifests contain membership and publishing metadata only.
- A repository may contain multiple non-overlapping package roots, and one package may contain multiple workflow definition/companion pairs.
- Version one permits at most 512 included files, 1 MiB per included file, and 8 MiB total included bytes. The generated contract artifact is the runtime authority for these values.
- Reject absolute paths, traversal, NUL bytes, backslashes in canonical paths, ambiguous/case-fold collisions, nested package roots, nested repository metadata, and every distributable symlink.
- Do not normalize line endings or rewrite package files while hashing or installing.
- Publisher digests and indexes are claims; Hermes independently recomputes and compares them.
- Existing Git credential helpers, SSH agent/configuration, `gh` integration, and supported environment tokens provide private-repository authentication. Never add or persist marketplace credentials.
- Installation and updates never grant trust. Any included distribution byte change invalidates marketplace-origin trust grants.
- Missing runtimes, tools, providers, services, credentials, and reachability are advisories; invalid contracts, workflows, paths, resources, or digests are blockers.
- Desktop mutations target the selected backend connection and profile. The renderer performs no Git or direct filesystem operation.
- Preserve loose project/profile workflows, verified showcases, bundled Ericsson seeding, existing plugin installation, and existing skills-hub behavior.
- Add no model-facing tool, prompt text, or conversation-time marketplace catalog.
- Every persisted schema is bounded, versioned, locked, and atomically replaced. Every displayed or persisted Git identity/error is credential-sanitized.
- Long Git work is cancellable outside atomic replacement. A cancellation cannot interrupt an active swap.
- Update all Desktop locales: `ar`, `en`, `ja`, `zh`, and `zh-hant`.

## File Map

Canonical contract and package domain:

- `plugins/workflow/marketplace/models.py` — strict Pydantic manifest, digest, index, source, provenance, review, and operation models.
- `plugins/workflow/marketplace/contract.py` — canonical limits/rules, deterministic contract envelope, artifact loading, and version checks.
- `plugins/workflow/marketplace/package.py` — safe package scan, manifest/index/digest parsing, and independent composite digest calculation.
- `scripts/generate_workflow_package_contract.py` — deterministic artifact generator/checker.
- `plugins/workflow/contracts/workflow-package-v1.json` and `workflow-package-v1-vectors.json` — Studio-consumable immutable contract artifacts.
- `MANIFEST.in`, `pyproject.toml`, and `tests/test_project_metadata.py` — wheel/sdist inclusion.

Runtime discovery and trust:

- `plugins/workflow/models.py`, `schema.py`, `compilation.py`, and `discovery.py` — marketplace binding and manifest-aware discovery.
- `plugins/workflow/catalog_api.py`, `admission_service.py`, `api_admission.py`, and `trust.py` — package-wide byte verification, effective trust identity, and origin-aware grants.
- `plugins/workflow/marketplace/discovery.py` — declared-definition enumeration and installed provenance binding.
- `plugins/workflow/marketplace/trust_binding.py` — namespaced marketplace workflow digest and trust-review helpers.

Git, catalogs, and transactions:

- `hermes_cli/git_source.py` — reusable Git URL/ref/subdirectory, executable, fetch, and sanitizer helpers.
- `hermes_cli/plugins_cmd.py` — consume the shared helper without behavior change.
- `plugins/workflow/marketplace/source_store.py` — versioned profile-local source configuration and verified catalog cache.
- `plugins/workflow/marketplace/git.py` — workflow-specific shallow/sparse fetch adapter with cancellation.
- `plugins/workflow/marketplace/catalog.py` — source refresh, search, and package-detail projections.
- `plugins/workflow/marketplace/provenance.py` — installed identity and exact Git/package provenance.
- `plugins/workflow/marketplace/transactions.py` — prepare records, expiring tokens, atomic swap/rollback, and recovery.
- `plugins/workflow/marketplace/service.py` — install/update/remove/trust orchestration used by CLI and API.
- `plugins/workflow/marketplace/operations.py` — bounded background operation registry for API callers.

CLI and API:

- `plugins/workflow/marketplace/cli.py` — marketplace parser configuration, human/JSON rendering, and interactive confirmation.
- `plugins/workflow/cli.py` — register and dispatch thin marketplace commands.
- `plugins/workflow/marketplace/api.py` — strict FastAPI request/response routes mounted below `/marketplace`.
- `plugins/workflow/dashboard/plugin_api.py` — include the marketplace router with existing operator verification.

Desktop:

- `apps/desktop/src/types/hermes.ts` — marketplace public types and `marketplace` workflow view.
- `apps/desktop/src/api/workflow-marketplace.ts` — profile/connection-scoped API helpers.
- `apps/desktop/src/lib/workflow-marketplace-codec.ts` — strict untrusted-response decoders.
- `apps/desktop/src/app/workflows/marketplace/` — list/detail, installed packages, sources, operations, review dialogs, and tests.
- `apps/desktop/src/app/workflows/index.tsx` and `workflow-view-header.tsx` — Installed/Marketplace integration.
- `apps/desktop/src/i18n/{types,en,ar,ja,zh,zh-hant}.ts` — complete marketplace copy.

Documentation and gates:

- `docs/workflow-orchestration.md` — operator/package architecture reference.
- `website/docs/user-guide/features/workflows.md` — Desktop and CLI user guide.
- `website/docs/user-guide/features/workflow-packages.md` — publisher/source/package/trust guide.
- `website/docs/reference/cli-commands.md` — marketplace command reference.
- `tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py` — real Git-to-install-to-trust-to-admission path.
- `scripts/test_workflow_merge_gate.sh` — include marketplace backend and Desktop gates.

---

## Milestone A: Canonical package and trust foundations

### Task 1: Publish the canonical package contract and shared vectors

**Files:**

- Create: `plugins/workflow/marketplace/__init__.py`
- Create: `plugins/workflow/marketplace/models.py`
- Create: `plugins/workflow/marketplace/contract.py`
- Create: `scripts/generate_workflow_package_contract.py`
- Create: `plugins/workflow/contracts/workflow-package-v1.json`
- Create: `plugins/workflow/contracts/workflow-package-v1-vectors.json`
- Create: `tests/plugins/workflow/test_marketplace_contract.py`
- Modify: `MANIFEST.in`
- Modify: `pyproject.toml`
- Modify: `tests/test_project_metadata.py`

**Interfaces:**

- Produces: `WorkflowPackageManifest`, `WorkflowPackageIndex`, `WorkflowPackageDigests`, `WorkflowMember`, `ExternalRequirements`, `WorkflowPackageContract`, marketplace request/review/persistence models including `PackageReviewAssessment`, `load_package_contract()`, and `render_contract_artifacts()`.
- Contract JSON exposes `contract_version`, `package_manifest_schema`, `marketplace_index_schema`, `digests_schema`, `path_rules`, `resource_rules`, `digest_rules`, `compatibility_rules`, and `diagnostic_codes`.

- [ ] **Step 1: Write failing contract and distribution tests**

```python
def test_contract_artifacts_are_deterministic_and_self_validating():
    contract_bytes, vector_bytes = render_contract_artifacts()
    assert contract_bytes == CONTRACT_PATH.read_bytes()
    assert vector_bytes == VECTOR_PATH.read_bytes()
    assert load_package_contract().resource_rules.max_files == 512


def test_package_manifest_rejects_trust_and_editor_state():
    with pytest.raises(ValidationError):
        WorkflowPackageManifest.model_validate({**valid_manifest(), "trusted": True})


def test_workflow_contracts_ship_in_wheel_and_sdist(project_metadata):
    assert "workflow/contracts/*.json" in project_metadata["package-data"]["plugins"]
```

Add parameterized negative cases for invalid semantic versions, duplicate workflow definitions, companion aliases, unknown manifest fields, missing metadata, unsupported `schemaVersion`, malformed index entries, and malformed digest records. Add vectors for UTF-8, binary base64, CRLF versus LF, empty bytes, Unicode paths, sorting, traversal, backslashes, NUL, case collisions, symlinks, and each size limit.

- [ ] **Step 2: Run tests to confirm RED**

Run:

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_contract.py \
  tests/test_project_metadata.py
```

Expected: fail because the marketplace package, generator, and artifacts do not exist.

- [ ] **Step 3: Implement strict models and deterministic generation**

Implement frozen Pydantic models with `ConfigDict(extra="forbid", strict=True)`. Use aliases matching the public camelCase manifest/index files and serialize with sorted keys, UTF-8, two-space indentation, and one trailing newline.

```python
PACKAGE_MAX_FILES = 512
PACKAGE_MAX_FILE_BYTES = 1024 * 1024
PACKAGE_MAX_TOTAL_BYTES = 8 * 1024 * 1024


def render_contract_artifacts() -> tuple[bytes, bytes]:
    contract = _contract_envelope_from_models()
    vectors = _contract_vectors()
    return (_canonical_json(contract), _canonical_json(vectors))


def load_package_contract(path: Path | None = None) -> WorkflowPackageContract:
    payload = json.loads((path or CONTRACT_PATH).read_bytes())
    return WorkflowPackageContract.model_validate(payload)
```

The generator accepts only `--check` or `--write`; `--check` exits nonzero on any byte difference. Add contract patterns to both sdist and wheel package-data rules.

- [ ] **Step 4: Generate artifacts and confirm GREEN**

Run:

```bash
uv run python scripts/generate_workflow_package_contract.py --write
uv run python scripts/generate_workflow_package_contract.py --check
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_contract.py \
  tests/test_project_metadata.py
```

Expected: artifact check and all tests pass.

- [ ] **Step 5: Commit the canonical contract**

```bash
git add MANIFEST.in pyproject.toml plugins/workflow/marketplace \
  plugins/workflow/contracts scripts/generate_workflow_package_contract.py \
  tests/plugins/workflow/test_marketplace_contract.py tests/test_project_metadata.py
git commit -m "feat(workflow): publish package contract"
```

### Task 2: Validate package roots, resources, indexes, and digests

**Files:**

- Create: `plugins/workflow/marketplace/package.py`
- Create: `tests/plugins/workflow/test_marketplace_package.py`
- Create: `tests/plugins/workflow/fixtures/marketplace/repository/`

**Interfaces:**

- Consumes: contract models and resource limits from Task 1.
- Produces: `WorkflowDistribution`, `PackageFile`, `load_distribution(root, *, expected_digest=None, read_budget=None)`, `load_repository_index(root)`, `scan_package_files(root, *, read_budget=None)`, and `compute_distribution_digest(files)`.

- [ ] **Step 1: Write failing package-integrity tests**

```python
def test_load_distribution_verifies_every_package_owned_byte(package_root):
    distribution = load_distribution(package_root)
    assert distribution.manifest.id == "laptop-support"
    assert distribution.digest == distribution.publisher_digests.package_digest
    assert [item.relative_path for item in distribution.files] == sorted(
        item.relative_path for item in distribution.files
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (add_symlink, "package_symlink_unsupported"),
        (add_case_collision, "package_path_collision"),
        (change_script_after_digest, "package_digest_mismatch"),
        (add_nested_manifest, "package_root_nested"),
    ],
)
def test_load_distribution_fails_closed(package_root, mutate, code):
    mutate(package_root)
    with pytest.raises(WorkflowMarketplaceError, match=code):
        load_distribution(package_root)
```

Add boundary cases for exactly/over each file and byte limit, binary files, `.git` components, absolute/traversing manifest paths, duplicate definitions, missing companions, unexpected `digests.json` entries, stale index metadata, and two valid sibling packages.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q tests/plugins/workflow/test_marketplace_package.py
```

Expected: fail because safe package loading and digest calculation are absent.

- [ ] **Step 3: Implement descriptor-safe scanning and hashing**

```python
@dataclass(frozen=True, slots=True)
class WorkflowDistribution:
    root: Path
    manifest: WorkflowPackageManifest
    publisher_digests: WorkflowPackageDigests
    files: tuple[PackageFile, ...]
    digest: str

    @property
    def covered_paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.files if item.included)


def load_distribution(
    root: Path,
    *,
    expected_digest: str | None = None,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> WorkflowDistribution:
    files = scan_package_files(root, read_budget=read_budget)
    manifest = _parse_manifest(files)
    published = _parse_digests(files)
    actual = compute_distribution_digest(files)
    _verify_membership_and_claims(manifest, published, files, actual, expected_digest)
    return WorkflowDistribution(root.resolve(), manifest, published, files, actual)
```

Walk without following symlinks, reject reparse/symlink components before and after reads, enforce canonical `PurePosixPath` identities, sort by Unicode code point, hash exact bytes, and exclude only `digests.json` from the composite. Validate repository index paths against non-overlapping manifest roots and compare index claims with loaded packages.

- [ ] **Step 4: Run contract and package tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_contract.py \
  tests/plugins/workflow/test_marketplace_package.py \
  tests/plugins/workflow/test_resources.py
```

Expected: all pass.

- [ ] **Step 5: Commit package validation**

```bash
git add plugins/workflow/marketplace/package.py \
  tests/plugins/workflow/test_marketplace_package.py \
  tests/plugins/workflow/fixtures/marketplace
git commit -m "feat(workflow): validate package distributions"
```

### Task 3: Make discovery manifest-aware without changing loose workflows

**Files:**

- Create: `plugins/workflow/marketplace/discovery.py`
- Create: `tests/plugins/workflow/test_marketplace_discovery.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/compilation.py`
- Modify: `plugins/workflow/discovery.py`
- Modify: `plugins/workflow/catalog_api.py`
- Modify: `tests/plugins/workflow/test_discovery.py`
- Modify: `tests/plugins/workflow/test_catalog_api.py`

**Interfaces:**

- Consumes: `load_distribution()` from Task 2.
- Produces: `WorkflowMarketplaceBinding` on source/package models and `enumerate_workflow_candidates(location, *, excluded_top_level, binding_resolver=None)`.

- [ ] **Step 1: Write failing declared-definition discovery tests**

```python
def test_package_discovers_only_manifest_definition_yaml(tmp_path, package_repo):
    copy_package(package_repo, tmp_path / ".hermes" / "workflows" / "packages" / "support")
    packages = discover_workflows(tmp_path, tmp_path / "profile", tmp_path)
    assert [item.definition.name for item in packages] == ["laptop-diagnostic"]


def test_mcp_fixture_and_companion_yaml_are_not_workflow_candidates(tmp_path, package_repo):
    root = install_fixture_package(tmp_path, package_repo)
    (root / "mcp" / "server.yaml").write_text("command: server\n")
    (root / "fixtures" / "bad.yaml").write_text("not: a workflow\n")
    assert [item.definition.name for item in discover_for(tmp_path)] == ["laptop-diagnostic"]
```

Add regression cases for ordinary loose workflows, explicit package roots, duplicate names, invalid package manifests, sibling packages, nested roots, and catalog list/detail failure isolation.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_discovery.py \
  tests/plugins/workflow/test_discovery.py \
  tests/plugins/workflow/test_catalog_api.py
```

Expected: resource YAML is still recursively treated as a workflow or marketplace binding fields are absent.

- [ ] **Step 3: Implement manifest-aware enumeration and binding propagation**

```python
@dataclass(frozen=True, slots=True)
class WorkflowMarketplaceBinding:
    installation_key: str
    source_name: str
    package_id: str
    package_version: str
    workflow_relative_path: str
    distribution_digest: str


def enumerate_workflow_candidates(
    location: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
    binding_resolver: Callable[[Path, str], WorkflowMarketplaceBinding | None] | None = None,
) -> tuple[WorkflowCandidate, ...]:
    """Return declared package definitions plus loose YAML outside package roots."""
```

Detect package roots first, validate them, exclude their full subtrees from loose YAML scanning, and emit only manifest `definition` members. Attach a marketplace binding only when the supplied resolver returns one for the canonical root and definition path. Add the optional binding to source signatures and compiled `WorkflowPackage` so cache reuse cannot cross bindings. Task 7 supplies the production resolver backed by installed provenance; tests in this task use an explicit resolver and ordinary discovery passes none until that store exists.

- [ ] **Step 4: Run discovery and catalog tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_discovery.py \
  tests/plugins/workflow/test_discovery.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_phase4_compilation.py
```

Expected: all pass and loose precedence is unchanged.

- [ ] **Step 5: Commit manifest-aware discovery**

```bash
git add plugins/workflow/marketplace/discovery.py plugins/workflow/models.py \
  plugins/workflow/schema.py plugins/workflow/compilation.py \
  plugins/workflow/discovery.py plugins/workflow/catalog_api.py \
  tests/plugins/workflow/test_marketplace_discovery.py \
  tests/plugins/workflow/test_discovery.py tests/plugins/workflow/test_catalog_api.py
git commit -m "feat(workflow): discover packaged definitions safely"
```

### Task 4: Bind marketplace distributions into origin-aware workflow trust

**Files:**

- Create: `plugins/workflow/marketplace/trust_binding.py`
- Create: `tests/plugins/workflow/test_marketplace_trust.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/admission_service.py`
- Modify: `plugins/workflow/api_admission.py`
- Modify: `plugins/workflow/catalog_api.py`
- Modify: `tests/plugins/workflow/test_trust_policy.py`
- Modify: `tests/plugins/workflow/test_admission.py`

**Interfaces:**

- Consumes: `WorkflowMarketplaceBinding`, `load_distribution()`, compilation/resource budgets, and `WorkflowTrustStore`.
- Produces: `effective_marketplace_digest()`, `bind_marketplace_package_digest()`, `trust_origin()`, and `revoke_origin()`.

- [ ] **Step 1: Write failing trust-invalidation and grant-origin tests**

```python
def test_unreferenced_package_byte_change_invalidates_marketplace_trust(installed_package):
    assessment = assess(installed_package)
    trust_store.trust_origin(
        assessment.package_digest.sha256,
        risk_digest=assessment.risk.risk_digest,
        actor="desktop",
        origin="marketplace:source/package",
    )
    (installed_package.root / "fixtures" / "unused.json").write_text('{"changed":true}')
    assert catalog_entry(installed_package).trust_state == "untrusted"


def test_revoking_one_install_origin_preserves_manual_and_other_install_grants():
    store.trust_origin(DIGEST, risk_digest=RISK, actor="operator", origin="manual")
    store.trust_origin(DIGEST, risk_digest=RISK, actor="desktop", origin="marketplace:a/p")
    assert store.revoke_origin("marketplace:a/p") == 1
    assert store.check(DIGEST, risk_digest=RISK) == "trusted"
```

Add migration cases from version-one records, multi-workflow packages with distinct risk digests, changed manifest/script/fixture bytes, a mismatched provenance digest, update-origin revocation, read-only corrupt-store behavior, and admission/catalog parity.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_trust.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py
```

Expected: fail because trust is not distribution-bound and records have no grant origins.

- [ ] **Step 3: Implement effective digest and version-two grants**

```python
def effective_marketplace_digest(
    *, distribution_digest: str, workflow_relative_path: str, closure_digest: str
) -> str:
    canonical = (
        b"hermes-workflow-marketplace-v1\0"
        + distribution_digest.encode("ascii") + b"\0"
        + workflow_relative_path.encode("utf-8") + b"\0"
        + closure_digest.encode("ascii")
    )
    return hashlib.sha256(canonical).hexdigest()


def bind_marketplace_package_digest(
    package: WorkflowPackage,
    base: WorkflowPackageDigest,
    *,
    read_budget: WorkflowResourceReadBudget | None,
) -> WorkflowPackageDigest:
    if package.marketplace_binding is None:
        return base
    distribution = load_distribution(
        package.root,
        expected_digest=package.marketplace_binding.distribution_digest,
        read_budget=read_budget,
    )
    return WorkflowPackageDigest(
        effective_marketplace_digest(
            distribution_digest=distribution.digest,
            workflow_relative_path=package.marketplace_binding.workflow_relative_path,
            closure_digest=base.sha256,
        ),
        tuple(sorted(set(base.covered_relative_paths) | distribution.covered_paths)),
    )
```

Normalize version-one trust records in memory to version-two `grants` maps. Mutation persists version two. `check` succeeds when any bounded grant matches the requested risk digest. Manual `trust()`/`revoke()` remain compatible through the `manual` origin. Ensure admission calculates the effective digest once from authenticated bytes and risk calculation uses that same digest.

- [ ] **Step 4: Run trust/admission/catalog tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_trust.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_phase5_admission_parity.py
```

Expected: all pass; loose and showcase trust behavior is unchanged.

- [ ] **Step 5: Commit marketplace trust binding**

```bash
git add plugins/workflow/marketplace/trust_binding.py plugins/workflow/trust.py \
  plugins/workflow/admission_service.py plugins/workflow/api_admission.py \
  plugins/workflow/catalog_api.py tests/plugins/workflow/test_marketplace_trust.py \
  tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_admission.py
git commit -m "feat(workflow): bind package bytes into trust"
```

## Milestone B: Git marketplace service, CLI, and API

### Task 5: Extract credential-safe shared Git source helpers

**Files:**

- Create: `hermes_cli/git_source.py`
- Create: `tests/hermes_cli/test_git_source.py`
- Modify: `hermes_cli/plugins_cmd.py`
- Modify: `tests/hermes_cli/test_plugin_install_ref.py`
- Modify: `tests/hermes_cli/test_plugins_cmd.py`

**Interfaces:**

- Produces: `GitSourceError`, `ResolvedGitSource`, `resolve_git_source()`, `resolve_git_executable()`, `noninteractive_git_env()`, `safe_git_error()`, `scrub_git_url()`, `canonical_git_source()`, `git_head_revision()`, `checkout_exact_revision()`, and `scrub_cloned_origin()`.

- [ ] **Step 1: Write failing shared-helper parity and redaction tests**

```python
def test_resolve_git_source_supports_private_ssh_and_tree_subdirectory():
    assert resolve_git_source("git@gitlab.example:team/repo.git#packages/support") == ResolvedGitSource(
        clone_url="git@gitlab.example:team/repo.git",
        subdirectory="packages/support",
    )


def test_safe_git_error_removes_embedded_credentials_and_query_tokens():
    result = completed(stderr="fatal: https://alice:secret@example.test/repo.git?token=abc")
    rendered = safe_git_error(result, "https://alice:secret@example.test/repo.git?token=abc")
    assert "secret" not in rendered
    assert "token=abc" not in rendered
```

Keep all existing plugin URL, full-SHA pinning, Portable Git, credential scrub, timeout, and reinstall tests as parity requirements.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/hermes_cli/test_git_source.py \
  tests/hermes_cli/test_plugin_install_ref.py \
  tests/hermes_cli/test_plugins_cmd.py
```

Expected: fail because `hermes_cli.git_source` does not exist.

- [ ] **Step 3: Move helpers without changing plugin behavior**

Move the current implementations and constants from `plugins_cmd.py`, parameterizing user-facing noun/error context only where necessary. Keep subprocess argument arrays, timeouts, `stdin=DEVNULL`, UTF-8 replacement decoding, and environment behavior byte-compatible. Import the public shared names back into `plugins_cmd.py`; do not duplicate URL or sanitizer logic.

```python
@dataclass(frozen=True, slots=True)
class ResolvedGitSource:
    clone_url: str
    subdirectory: str | None


def resolve_git_source(identifier: str) -> ResolvedGitSource:
    value = identifier.strip()
    if not value:
        raise GitSourceError("Git source must not be empty.")
    clone_url, subdirectory = _split_supported_identifier(value)
    return ResolvedGitSource(
        clone_url=clone_url,
        subdirectory=_normalize_subdirectory(subdirectory),
    )
```

- [ ] **Step 4: Run shared and plugin tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/hermes_cli/test_git_source.py \
  tests/hermes_cli/test_plugin_install_ref.py \
  tests/hermes_cli/test_plugins_cmd.py \
  tests/hermes_cli/test_plugins_cmd_list.py
```

Expected: all pass with unchanged plugin contracts.

- [ ] **Step 5: Commit the shared Git seam**

```bash
git add hermes_cli/git_source.py hermes_cli/plugins_cmd.py \
  tests/hermes_cli/test_git_source.py tests/hermes_cli/test_plugin_install_ref.py \
  tests/hermes_cli/test_plugins_cmd.py
git commit -m "refactor(cli): share credential-safe Git sources"
```

### Task 6: Add profile-local sources and verified catalog refresh

**Files:**

- Create: `plugins/workflow/marketplace/source_store.py`
- Create: `plugins/workflow/marketplace/git.py`
- Create: `plugins/workflow/marketplace/catalog.py`
- Create: `tests/plugins/workflow/test_marketplace_sources.py`
- Create: `tests/plugins/workflow/test_marketplace_git.py`
- Create: `tests/plugins/workflow/test_marketplace_catalog.py`

**Interfaces:**

- Consumes: shared Git helpers and package/index validation.
- Produces: `WorkflowSourceStore`, `WorkflowGitFetcher`, `WorkflowMarketplaceCatalog`, `add_source()`, `refresh_source()`, `search()`, and `inspect()`.

- [ ] **Step 1: Write failing source, private-auth, cache, and catalog tests**

```python
def test_source_store_never_persists_credentials(tmp_path):
    store = WorkflowSourceStore(tmp_path)
    with pytest.raises(WorkflowMarketplaceError, match="source_credentials_forbidden"):
        store.add("private", "https://alice:secret@example.test/team/repo.git")
    assert not store.path.exists()


def test_failed_refresh_preserves_last_verified_catalog(source_store, fake_git):
    first = catalog.refresh_source("company")
    fake_git.fail_with("authentication failed for https://token@example.test/repo.git")
    second = catalog.refresh_source("company")
    assert second.state == "stale"
    assert catalog.search("support")[0].resolved_commit == first.resolved_commit
    assert "token" not in repr(second)
```

Use local bare Git repositories for default branch, named branch, tag, exact SHA, multiple package index, direct subdirectory, missing index, stale digest, auth-failure simulation, filter unsupported fallback, cancellation, timeout, and URL sanitization.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_sources.py \
  tests/plugins/workflow/test_marketplace_git.py \
  tests/plugins/workflow/test_marketplace_catalog.py
```

Expected: fail because source persistence, fetcher, and catalog are absent.

- [ ] **Step 3: Implement bounded source state and shallow/sparse refresh**

```python
class WorkflowGitFetcher:
    def fetch(
        self,
        source: WorkflowMarketplaceSource,
        destination: Path,
        *,
        sparse_paths: tuple[str, ...],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ResolvedCheckout:
        """Fetch one exact, sanitized checkout without executing package content."""


class WorkflowMarketplaceCatalog:
    def refresh_source(self, name: str, *, cancelled=lambda: False) -> SourceRefreshResult:
        source = self.source_store.get(name)
        try:
            verified = self._fetch_and_verify_index(source, cancelled=cancelled)
        except WorkflowMarketplaceError as exc:
            return self.source_store.record_failed_refresh(source, exc)
        self.source_store.replace_verified_cache(source, verified)
        return SourceRefreshResult.from_verified(source, verified)
```

Persist sources and verified cache under `$HERMES_HOME/marketplace/workflows`. Use `workflow_lock`, bounded reads, versioned strict models, `atomic_write_text`, and mode `0700` directories/`0600` state on POSIX. Fetch with `clone --depth 1 --filter=blob:none --no-checkout`, configure sparse checkout for the index/package paths, resolve and verify HEAD, and fall back when the server explicitly rejects filtering. Never persist the temporary clone.

- [ ] **Step 4: Run source/catalog tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_sources.py \
  tests/plugins/workflow/test_marketplace_git.py \
  tests/plugins/workflow/test_marketplace_catalog.py \
  tests/hermes_cli/test_git_source.py
```

Expected: all pass.

- [ ] **Step 5: Commit sources and catalog**

```bash
git add plugins/workflow/marketplace/source_store.py \
  plugins/workflow/marketplace/git.py plugins/workflow/marketplace/catalog.py \
  tests/plugins/workflow/test_marketplace_sources.py \
  tests/plugins/workflow/test_marketplace_git.py \
  tests/plugins/workflow/test_marketplace_catalog.py
git commit -m "feat(workflow): add Git marketplace sources"
```

### Task 7: Add installed provenance and recoverable transaction primitives

**Files:**

- Create: `plugins/workflow/marketplace/provenance.py`
- Create: `plugins/workflow/marketplace/transactions.py`
- Create: `tests/plugins/workflow/test_marketplace_provenance.py`
- Create: `tests/plugins/workflow/test_marketplace_transactions.py`
- Modify: `plugins/workflow/marketplace/discovery.py`
- Modify: `plugins/workflow/discovery.py`
- Modify: `tests/plugins/workflow/test_marketplace_discovery.py`

**Interfaces:**

- Produces: `InstalledPackageStore`, `InstalledPackageStore.binding_for_workflow()`, `MarketplaceTransactionStore`, `PreparedTransaction`, `prepare()`, `consume()`, `atomic_install()`, `atomic_remove()`, and `recover_transactions()`.

- [ ] **Step 1: Write failing identity, token, rollback, and recovery tests**

```python
def test_confirmation_token_is_single_use_actor_profile_and_digest_bound(staged_candidate):
    prepared = transactions.prepare(staged_candidate, review_digest=REVIEW, actor="alice", profile="p1")
    transactions.consume(prepared.token, actor="alice", profile="p1")
    with pytest.raises(WorkflowMarketplaceError, match="confirmation_token_invalid"):
        transactions.consume(prepared.token, actor="alice", profile="p1")


def test_failed_provenance_write_restores_previous_package(transactions, installed_v1, candidate_v2, fail_write):
    original = snapshot_tree(installed_v1.root)
    with pytest.raises(OSError):
        transactions.atomic_install(candidate_v2, provenance_writer=fail_write)
    assert snapshot_tree(installed_v1.root) == original
    assert installed_store.get(installed_v1.identity).version == "1.0.0"


def test_only_matching_installed_provenance_creates_a_marketplace_binding(installed_store, installed_v1):
    binding = installed_store.binding_for_workflow(
        installed_v1.root, "workflows/laptop-diagnostic.yaml"
    )
    assert binding.installation_key == installed_v1.identity.key
    tamper(installed_v1.root / "fixtures" / "unused.json")
    assert installed_store.binding_for_workflow(
        installed_v1.root, "workflows/laptop-diagnostic.yaml"
    ) is None
```

Add expired/replayed/cross-actor/cross-profile tokens, two sources with the same package ID, direct-source key stability, symlinked destination, concurrent lock timeout, interrupted swap journal, abandoned owned staging cleanup, and unrelated-directory preservation.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_provenance.py \
  tests/plugins/workflow/test_marketplace_transactions.py
```

Expected: fail because installed/provenance and transaction stores are absent.

- [ ] **Step 3: Implement namespaced provenance and two-phase filesystem transactions**

```python
@dataclass(frozen=True, slots=True)
class InstalledPackageIdentity:
    source_key: str
    package_id: str


class MarketplaceTransactionStore:
    def consume(self, raw_token: str, *, actor: str, profile: str) -> PreparedTransaction:
        with workflow_lock(self.lock_path):
            prepared = self._load_by_token_digest(hashlib.sha256(raw_token.encode()).hexdigest())
            prepared.require_current(actor=actor, profile=profile, now=self.clock())
            self._delete_record(prepared.id)
            return prepared
```

Store only SHA-256 of the random token. Bind transaction records to source/ref/commit/path/id/version/digest/destination/review digest/actor/profile/expiry. Recalculate the candidate and review digests immediately before atomic install so token lookup alone cannot authorize changed bytes. Stage verified copies below `$HERMES_HOME/workflows/.staging`, move the current package to `.quarantine`, replace, fsync, persist provenance, then retire the backup. Journal every state transition before it becomes externally visible. Recovery acts only on paths whose recorded identity and marker match. Implement `InstalledPackageStore.binding_for_workflow()` and pass it into profile discovery so only provenance-matching installed roots receive marketplace trust bindings.

- [ ] **Step 4: Run transaction tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_provenance.py \
  tests/plugins/workflow/test_marketplace_transactions.py \
  tests/plugins/workflow/test_quarantine_replace_retry.py
```

Expected: all pass.

- [ ] **Step 5: Commit transaction foundations**

```bash
git add plugins/workflow/marketplace/provenance.py \
  plugins/workflow/marketplace/transactions.py \
  plugins/workflow/marketplace/discovery.py plugins/workflow/discovery.py \
  tests/plugins/workflow/test_marketplace_provenance.py \
  tests/plugins/workflow/test_marketplace_transactions.py \
  tests/plugins/workflow/test_marketplace_discovery.py
git commit -m "feat(workflow): add package install transactions"
```

### Task 8: Implement install, update, remove, and trust orchestration

**Files:**

- Create: `plugins/workflow/marketplace/service.py`
- Create: `tests/plugins/workflow/test_marketplace_service.py`
- Modify: `plugins/workflow/marketplace/models.py`
- Modify: `plugins/workflow/marketplace/provenance.py`
- Modify: `plugins/workflow/marketplace/transactions.py`

**Interfaces:**

- Consumes: catalog, fetcher, distribution loader, transactions, discovery/assessment, and origin-aware trust.
- Produces: `WorkflowMarketplaceService` source CRUD/refresh/search methods plus `prepare_install`, `confirm_install`, `check_updates`, `prepare_update`, `confirm_update`, `prepare_remove`, `confirm_remove`, `review_trust`, `grant_trust`, and `revoke_trust`.

- [ ] **Step 1: Write failing lifecycle and review tests**

```python
def test_install_is_verified_atomic_and_untrusted(service, published_repo):
    review = service.prepare_install(InstallRequest(identifier="public/laptop-support"), actor="alice")
    installed = service.confirm_install(review.confirmation_token, actor="alice")
    assert installed.version == "1.0.0"
    assert service.workflow_trust(installed) == {"laptop-diagnostic": "untrusted"}


def test_changed_update_revokes_only_package_origin_grants(service, installed_v1, published_v2):
    service.grant_trust(service.review_trust(installed_v1.identity), actor="alice")
    review = service.prepare_update(installed_v1.identity, actor="alice")
    updated = service.confirm_update(review.confirmation_token, actor="alice")
    assert updated.version == "2.0.0"
    assert all(state == "untrusted" for state in service.workflow_trust(updated).values())
```

Cover registered and direct installs, ambiguous direct repositories, exact refs, same source/package conflicts, version regressions, unchanged updates, changed files/requirements/risks, invalid candidates preserving v1, source removal leaving an orphaned install, removal preview, trust one/all workflows, destination advisories, and no execution during scans.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q tests/plugins/workflow/test_marketplace_service.py
```

Expected: fail because the orchestration service does not exist.

- [ ] **Step 3: Implement review-first lifecycle methods**

```python
class WorkflowMarketplaceService:
    def add_source(self, source: WorkflowMarketplaceSource) -> WorkflowMarketplaceSource:
        return self.catalog.source_store.add(source)

    def refresh_source(self, name: str, *, cancelled=lambda: False) -> SourceRefreshResult:
        return self.catalog.refresh_source(name, cancelled=cancelled)

    def prepare_install(self, request: InstallRequest, *, actor: str) -> InstallReview:
        candidate = self.catalog.fetch_install_candidate(request)
        assessment = self._assess_candidate(candidate)
        prepared = self.transactions.prepare(
            candidate,
            review_digest=assessment.review_digest,
            actor=actor,
            profile=self.profile,
        )
        return InstallReview.from_prepared(prepared, assessment)

    def confirm_install(self, token: str, *, actor: str) -> InstalledPackage:
        prepared = self.transactions.consume(token, actor=actor, profile=self.profile)
        return self.transactions.atomic_install(prepared)
```

Build reviews from exact fetched/installed bytes. Report structural blockers separately from destination advisories. Diff sorted file digests rather than unbounded text. Confirm through transaction primitives only. Update revokes prior origin grants after the new package/provenance commit succeeds. Removal revokes its origin grants and package/provenance together without touching source state.

- [ ] **Step 4: Run full marketplace backend slice to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_contract.py \
  tests/plugins/workflow/test_marketplace_package.py \
  tests/plugins/workflow/test_marketplace_discovery.py \
  tests/plugins/workflow/test_marketplace_trust.py \
  tests/plugins/workflow/test_marketplace_sources.py \
  tests/plugins/workflow/test_marketplace_git.py \
  tests/plugins/workflow/test_marketplace_catalog.py \
  tests/plugins/workflow/test_marketplace_provenance.py \
  tests/plugins/workflow/test_marketplace_transactions.py \
  tests/plugins/workflow/test_marketplace_service.py
```

Expected: all pass.

- [ ] **Step 5: Commit the lifecycle service**

```bash
git add plugins/workflow/marketplace tests/plugins/workflow/test_marketplace_service.py
git commit -m "feat(workflow): orchestrate package lifecycle"
```

### Task 9: Add the complete workflow marketplace CLI

**Files:**

- Create: `plugins/workflow/marketplace/cli.py`
- Create: `tests/plugins/workflow/test_marketplace_cli.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/plugins/workflow/test_catalog_cli.py`

**Interfaces:**

- Consumes: `WorkflowMarketplaceService`.
- Produces: `configure_marketplace_parsers(actions)`, `dispatch_marketplace_command(args)`, stable JSON envelopes, and human renderers.

- [ ] **Step 1: Write failing parser, JSON, confirmation, and exit-code tests**

```python
def test_marketplace_cli_contract(parser):
    args = parser.parse_args(["workflow", "source", "add", "company", "git@example:team/repo.git", "--ref", "main"])
    assert (args.workflow_action, args.source_action, args.name) == ("source", "add", "company")


def test_install_json_requires_explicit_confirmation(cli, service):
    result = cli("workflow", "install", "company/laptop-support", "--prepare-only", "--json")
    payload = json.loads(result.stdout)
    assert payload["status"] == "review_required"
    assert payload["confirmation_token"]
    assert service.confirm_calls == []
```

Pin source add/list/refresh/remove; search; inspect; install from source/direct URL; installed; check; update one/all; uninstall; trust/untrust package or workflow; `--json`; `--yes`; `--prepare-only`; `--confirmation-token`; invalid combinations; auth errors; stable codes; and sanitized output.

The marketplace trust grammar is `trust SOURCE/PACKAGE [--workflow NAME] [--prepare-only | --confirmation-token TOKEN | --yes]`. Legacy loose-workflow trust remains `trust NAME --digest DIGEST`. Marketplace untrust uses `untrust SOURCE/PACKAGE [--workflow NAME]`; loose-workflow `untrust NAME` remains unchanged.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_cli.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_catalog_cli.py
```

Expected: fail because parsers and dispatch are absent.

- [ ] **Step 3: Implement thin parser/renderer adapters**

Register the approved commands under the existing workflow subparser. Preserve current `trust NAME --digest DIGEST` and `untrust NAME` behavior for loose workflows; resolve `source/package` or explicit `--installed-package` through the marketplace service. `--prepare-only` emits the token/review without mutation. `--confirmation-token` confirms an earlier review. Interactive `--yes` still performs prepare then confirm and never trusts.

```python
def dispatch_marketplace_command(args: argparse.Namespace) -> int | None:
    action = args.workflow_action
    if action not in MARKETPLACE_ACTIONS:
        return None
    try:
        payload = _invoke_service(args)
    except WorkflowMarketplaceError as exc:
        return emit_marketplace_error(exc, as_json=args.json)
    return emit_marketplace_result(payload, as_json=args.json)
```

- [ ] **Step 4: Run CLI tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_cli.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_catalog_cli.py \
  tests/agent/test_workflow_product_cli_guidance.py
```

Expected: all pass and old CLI help/contracts remain available.

- [ ] **Step 5: Commit CLI support**

```bash
git add plugins/workflow/marketplace/cli.py plugins/workflow/cli.py \
  tests/plugins/workflow/test_marketplace_cli.py \
  tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_catalog_cli.py
git commit -m "feat(workflow): add package marketplace CLI"
```

### Task 10: Expose authenticated API routes and cancellable operations

**Files:**

- Create: `plugins/workflow/marketplace/operations.py`
- Create: `plugins/workflow/marketplace/api.py`
- Create: `tests/plugins/workflow/test_marketplace_operations.py`
- Create: `tests/plugins/workflow/test_marketplace_api.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/hermes_cli/test_workflow_dashboard_auth.py`

**Interfaces:**

- Consumes: marketplace service and existing `_verified_operator` authority.
- Produces: `WorkflowMarketplaceOperationRegistry`, strict `/marketplace` routes, operation status, and cancellation.

- [ ] **Step 1: Write failing API authority, scope, and operation tests**

```python
def test_marketplace_reads_require_read_and_mutations_require_admin(client, scopes):
    assert client.get("/api/plugins/workflow/marketplace/packages", headers=scopes.none).status_code == 403
    assert client.get("/api/plugins/workflow/marketplace/packages", headers=scopes.read).status_code == 200
    assert client.post("/api/plugins/workflow/marketplace/install/prepare", json=REQUEST, headers=scopes.write).status_code == 403
    assert client.post("/api/plugins/workflow/marketplace/install/prepare", json=REQUEST, headers=scopes.admin).status_code == 202


def test_cancelled_fetch_never_reports_committed(operation_registry):
    operation = operation_registry.start("refresh", blocking_fetch)
    operation_registry.cancel(operation.id)
    assert wait_terminal(operation.id).state == "cancelled"
    assert wait_terminal(operation.id).result is None
```

Cover strict request/response schemas, bounded pagination/search, source CRUD, installed listing, prepare/confirm actions, expired tokens, operation polling/cancel, unknown IDs, sanitized errors, same-profile single-flight conflicts, and registry eviction limits.

- [ ] **Step 2: Run tests to confirm RED**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_operations.py \
  tests/plugins/workflow/test_marketplace_api.py \
  tests/hermes_cli/test_workflow_dashboard_auth.py
```

Expected: fail because the operation registry and router do not exist.

- [ ] **Step 3: Implement the bounded operation registry and router**

```python
class WorkflowMarketplaceOperationRegistry:
    def start(self, kind: OperationKind, call: Callable[[CancellationToken], BaseModel]) -> MarketplaceOperation:
        operation = self._reserve_bounded_operation(kind)
        future = self.executor.submit(self._run, operation.id, call, operation.cancellation)
        self._attach_future(operation.id, future)
        return operation

    def cancel(self, operation_id: str) -> MarketplaceOperation:
        operation = self._require_operation(operation_id)
        operation.cancellation.cancel()
        return self.get(operation_id)


router = APIRouter(prefix="/marketplace")
```

Use a bounded thread pool and bounded terminal-result retention. Cancellation sets a token checked between Git phases and before commit; transaction code masks cancellation during the atomic swap. API dependencies receive the already-verified operator, require `read` or `admin`, derive a bounded actor identity, and instantiate the service with `get_hermes_home()` so existing profile routing remains authoritative.

- [ ] **Step 4: Run API and existing Desktop-backend tests to confirm GREEN**

```bash
uv run pytest -q \
  tests/plugins/workflow/test_marketplace_operations.py \
  tests/plugins/workflow/test_marketplace_api.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/hermes_cli/test_workflow_dashboard_auth.py \
  tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py
```

Expected: all pass.

- [ ] **Step 5: Commit API support**

```bash
git add plugins/workflow/marketplace/operations.py \
  plugins/workflow/marketplace/api.py plugins/workflow/dashboard/plugin_api.py \
  tests/plugins/workflow/test_marketplace_operations.py \
  tests/plugins/workflow/test_marketplace_api.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/hermes_cli/test_workflow_dashboard_auth.py
git commit -m "feat(workflow): expose marketplace API"
```

## Milestone C: Co-worker Desktop, documentation, and end-to-end proof

### Task 11: Add strict Desktop marketplace API types and decoders

**Files:**

- Create: `apps/desktop/src/api/workflow-marketplace.ts`
- Create: `apps/desktop/src/api/workflow-marketplace.test.ts`
- Create: `apps/desktop/src/lib/workflow-marketplace-codec.ts`
- Create: `apps/desktop/src/lib/workflow-marketplace-codec.test.ts`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/hermes.ts`

**Interfaces:**

- Produces: decoded source/package/detail/installed/review/operation/trust types and API helpers for every Task 10 endpoint.

- [ ] **Step 1: Write failing decode and routing tests**

```typescript
it('rejects an install review containing unknown credential material', () => {
  expect(decodeMarketplaceInstallReview({ ...review, access_token: 'secret' })).toBeNull()
})

it('scopes package search to the explicit connection and profile', async () => {
  await searchWorkflowPackages('laptop', { connectionId: 'remote-a', profile: 'support' })
  expect(api).toHaveBeenCalledWith(expect.objectContaining({
    connectionId: 'remote-a',
    profile: 'support',
    path: '/api/plugins/workflow/marketplace/packages?q=laptop'
  }))
})
```

Test every union discriminator, collection limit, unknown key, invalid URL projection, operation state, review token, error envelope, and explicit/ambient profile scope.

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd apps/desktop
npx vitest run --project ui \
  src/api/workflow-marketplace.test.ts \
  src/lib/workflow-marketplace-codec.test.ts
```

Expected: fail because types, decoders, and API helpers are absent.

- [ ] **Step 3: Implement fail-closed decoders and scoped helpers**

```typescript
export async function searchWorkflowPackages(
  query: string,
  scope?: ProfileScope
): Promise<WorkflowMarketplaceSearchPage> {
  const value = await hermesApi<unknown>({
    ...capabilityScoped(scope),
    path: `/api/plugins/workflow/marketplace/packages?${new URLSearchParams({ q: query })}`
  })
  const decoded = decodeWorkflowMarketplaceSearchPage(value)
  if (!decoded) throw new TypeError('Hermes returned an invalid workflow marketplace page')
  return decoded
}
```

Decode into fresh bounded objects; never cast the untrusted API response. Preserve structured `404` capability detection so a newer Desktop can show backend-upgrade guidance.

- [ ] **Step 4: Run API/codec tests and typecheck to confirm GREEN**

```bash
cd apps/desktop
npx vitest run --project ui \
  src/api/workflow-marketplace.test.ts \
  src/lib/workflow-marketplace-codec.test.ts \
  src/lib/hermes-api.test.ts
npm run typecheck
```

Expected: all pass.

- [ ] **Step 5: Commit Desktop contracts**

```bash
git add apps/desktop/src/api/workflow-marketplace.ts \
  apps/desktop/src/api/workflow-marketplace.test.ts \
  apps/desktop/src/lib/workflow-marketplace-codec.ts \
  apps/desktop/src/lib/workflow-marketplace-codec.test.ts \
  apps/desktop/src/types/hermes.ts apps/desktop/src/hermes.ts
git commit -m "feat(desktop): add workflow marketplace client"
```

### Task 12: Add Installed and Marketplace list/detail views

**Files:**

- Create: `apps/desktop/src/app/workflows/marketplace/index.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/index.test.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/package-list.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/package-detail.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/installed-packages.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/query-keys.ts`
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-view-header.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-view-header.test.tsx`
- Modify: `apps/desktop/src/types/hermes.ts`

**Interfaces:**

- Consumes: Task 11 API helpers.
- Produces: `WorkflowMarketplaceView`, `MarketplacePackageList`, `MarketplacePackageDetail`, and Installed/Marketplace tab behavior.

- [ ] **Step 1: Write failing list/detail, state, and navigation tests**

```tsx
it('shows Installed and Marketplace without disturbing run views', async () => {
  render(<WorkflowsView />)
  expect(screen.getByRole('tab', { name: 'Installed' })).toHaveAttribute('aria-selected', 'true')
  await user.click(screen.getByRole('tab', { name: 'Marketplace' }))
  expect(await screen.findByRole('searchbox', { name: 'Search workflow packages' })).toBeVisible()
  expect(screen.getByRole('tab', { name: 'Active' })).toBeVisible()
})

it('keeps package selection scoped when the backend profile changes', async () => {
  const view = renderMarketplace({ connectionId: 'a', profile: 'one' })
  await selectPackage('company/support')
  view.rerender(<Marketplace scope={{ connectionId: 'b', profile: 'two' }} />)
  expect(screen.queryByText('company/support')).not.toHaveAttribute('aria-selected', 'true')
})
```

Cover loading, empty, search/no-match, partial/stale catalog, source filter, selected detail, installed/update badges, loose workflows remaining in Installed, responsive single-column detail, focus return, keyboard selection, and unsupported older backend.

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd apps/desktop
npx vitest run --project ui \
  src/app/workflows/marketplace/index.test.tsx \
  src/app/workflows/index.test.tsx \
  src/app/workflows/workflow-view-header.test.tsx
```

Expected: fail because the Marketplace tab and components do not exist.

- [ ] **Step 3: Implement backend-owned list/detail UI**

```tsx
export function WorkflowMarketplaceView({ scope }: { scope: ProfileScope }) {
  const scopeKey = profileScopeKey(scope)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const packages = useQuery({
    queryKey: marketplaceKeys.search(scopeKey, query),
    queryFn: () => searchWorkflowPackages(query, scope)
  })
  return <MarketplaceMasterDetail packages={packages} selectedId={selectedId} onSelect={setSelectedId} />
}
```

Rename the current catalog tab label to Installed while retaining its internal `'workflows'` identifier for compatibility. Add `'marketplace'` to the view union and header. Display only bounded API projections; repository links use the existing safe external-link component.

- [ ] **Step 4: Run workflow UI tests and typecheck to confirm GREEN**

```bash
cd apps/desktop
npx vitest run --project ui src/app/workflows/ src/app/routes.test.ts
npm run typecheck
```

Expected: all pass.

- [ ] **Step 5: Commit marketplace browsing UI**

```bash
git add apps/desktop/src/app/workflows apps/desktop/src/types/hermes.ts
git commit -m "feat(desktop): browse workflow packages"
```

### Task 13: Add source management and operation reconciliation

**Files:**

- Create: `apps/desktop/src/app/workflows/marketplace/source-dialog.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/source-dialog.test.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/use-marketplace-operation.ts`
- Create: `apps/desktop/src/app/workflows/marketplace/use-marketplace-operation.test.tsx`
- Modify: `apps/desktop/src/app/workflows/marketplace/index.tsx`
- Modify: `apps/desktop/src/app/workflows/marketplace/query-keys.ts`

**Interfaces:**

- Produces: `ManageWorkflowSourcesDialog` and `useMarketplaceOperation()` with polling, cancellation, navigation survival, and scoped invalidation.

- [ ] **Step 1: Write failing source/auth and operation-lifecycle tests**

```tsx
it('explains private Git authentication without collecting a credential', async () => {
  render(<ManageWorkflowSourcesDialog open scope={scope} />)
  await addSource({ name: 'private', url: 'git@example.test:team/repo.git' })
  server.returnAuthFailure()
  expect(await screen.findByText(/Git credential helper, SSH agent, or gh auth/)).toBeVisible()
  expect(screen.queryByLabelText(/token|password/i)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
})

it('ignores a completed operation from the previously selected profile', async () => {
  const { rerender } = renderHook(scopeA)
  startDeferredOperation('op-a')
  rerender(scopeB)
  completeOperation('op-a')
  expect(invalidate).not.toHaveBeenCalledWith(expect.objectContaining({ queryKey: marketplaceKeys.root(scopeBKey) }))
})
```

Cover add/edit/enable/disable/remove, source removal not uninstalling packages, refresh one/all, fresh/stale timestamps, sanitized errors, Retry, operation cancellation, terminal eviction, hidden-tab polling cadence, unmount, connection/profile switch, and no focus stealing.

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd apps/desktop
npx vitest run --project ui \
  src/app/workflows/marketplace/source-dialog.test.tsx \
  src/app/workflows/marketplace/use-marketplace-operation.test.tsx
```

Expected: fail because source management and operation reconciliation are absent.

- [ ] **Step 3: Implement typed source forms and backend operation polling**

Use controlled name/URL/ref fields with client-side length checks only; backend validation is authoritative. Do not add credential fields. Poll an active operation by `connectionId + profile + operationId`, cancel polling when hidden/unmounted/switched, and invalidate only the originating scope after a terminal backend result.

```typescript
export function useMarketplaceOperation(scope: ProfileScope) {
  const generation = useRef(0)
  const activeOperation = useRef<string | null>(null)
  const start = async (request: () => Promise<MarketplaceOperation>) => {
    const current = ++generation.current
    const operation = await request()
    activeOperation.current = operation.id
    return pollMarketplaceOperation(operation.id, scope, () => current === generation.current)
  }
  const cancel = () => {
    const operationId = activeOperation.current
    return operationId ? cancelMarketplaceOperation(operationId, scope) : Promise.resolve(null)
  }
  return { start, cancel }
}
```

- [ ] **Step 4: Run focused and workflow UI tests to confirm GREEN**

```bash
cd apps/desktop
npx vitest run --project ui src/app/workflows/marketplace/ src/app/workflows/index.test.tsx
npm run typecheck
```

Expected: all pass.

- [ ] **Step 5: Commit sources and operation state**

```bash
git add apps/desktop/src/app/workflows/marketplace
git commit -m "feat(desktop): manage workflow sources"
```

### Task 14: Add install, update, removal, and separate trust reviews

**Historical task, paused:** Initial implementation exists at `c89f36c6b8`; review found protocol gaps. Unconditional unchanged-version copy and invalidation-only recovery below are not safe implementation instructions. Continue through the replacement plan only after design approval.

**Files:**

- Create: `apps/desktop/src/app/workflows/marketplace/install-review-dialog.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/install-review-dialog.test.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/trust-review-dialog.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/trust-review-dialog.test.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/remove-review-dialog.tsx`
- Create: `apps/desktop/src/app/workflows/marketplace/remove-review-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/marketplace/index.tsx`
- Modify: `apps/desktop/src/app/workflows/marketplace/package-detail.tsx`
- Modify: `apps/desktop/src/app/workflows/marketplace/installed-packages.tsx`

**Interfaces:**

- Produces: review-first install/update/remove flows and a distinct per-workflow trust flow.

- [ ] **Step 1: Write failing review-separation and recovery-state tests**

```tsx
it('finishes installation as untrusted and offers a separate trust review', async () => {
  renderMarketplaceWithInstallCandidate()
  await user.click(screen.getByRole('button', { name: 'Install package' }))
  expect(await screen.findByRole('dialog', { name: 'Review installation' })).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Confirm install' }))
  expect(await screen.findByText('Installed — trust required to run')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Review trust' })).toBeVisible()
  expect(grantTrust).not.toHaveBeenCalled()
})

it('shows that the previous version remains installed after update failure', async () => {
  server.failUpdateConfirmation({ currentVersion: '1.0.0', recovery: 'unchanged' })
  await confirmUpdate()
  expect(await screen.findByText(/Version 1.0.0 remains installed/)).toBeVisible()
})
```

Cover exact source/ref/commit/digest, files and requirements, structural blockers, advisories, scripts/commands/MCP/outward risks, install cancellation, stale/expired review, update diff, changed trust, remove scope, trust selected/all workflows, keyboard/focus behavior, and truthful rollback status.

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd apps/desktop
npx vitest run --project ui \
  src/app/workflows/marketplace/install-review-dialog.test.tsx \
  src/app/workflows/marketplace/trust-review-dialog.test.tsx \
  src/app/workflows/marketplace/remove-review-dialog.test.tsx
```

Expected: fail because lifecycle review dialogs do not exist.

- [ ] **Step 3: Implement distinct prepare/confirm/trust UI states**

Never combine the install confirmation token with trust. Disable close only during the short atomic confirm call, not during fetch preparation. Render changed-file lists and risks from the bounded server review. After confirmed install/update/remove, invalidate marketplace, installed workflow catalog, and trust queries for the originating scope.

```tsx
<DialogFooter>
  <Button variant="secondary" onClick={onCancel}>Cancel</Button>
  <Button disabled={!review || confirming} onClick={() => onConfirm(review.confirmation_token)}>
    Confirm install
  </Button>
</DialogFooter>
```

Trust review is opened only after fetching a fresh review token for installed bytes. Workflow checkboxes identify exactly which grants will be written. A package update with changed bytes returns to the Installed state labeled untrusted.

- [ ] **Step 4: Run complete Desktop workflow UI and accessibility tests**

```bash
cd apps/desktop
npx vitest run --project ui src/app/workflows/ src/components/activity-board/
npm run typecheck
npm run lint -- --quiet
```

Expected: all pass.

- [ ] **Step 5: Commit lifecycle UI**

```bash
git add apps/desktop/src/app/workflows/marketplace
git commit -m "feat(desktop): review workflow package lifecycle"
```

### Task 15: Complete localization, documentation, end-to-end coverage, and merge gates

**Not started:** Execute the amended Task 15 in the replacement plan after all revised Task 14 gates are accepted. Preserve this section as the original scope record.

**Files:**

- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ar.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`
- Modify: `apps/desktop/src/i18n/languages.test.ts`
- Modify: `docs/workflow-orchestration.md`
- Modify: `website/docs/user-guide/features/workflows.md`
- Create: `website/docs/user-guide/features/workflow-packages.md`
- Modify: `website/docs/reference/cli-commands.md`
- Create: `tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py`
- Modify: `scripts/test_workflow_merge_gate.sh`

**Interfaces:**

- Consumes: all previous tasks.
- Produces: complete user/operator guidance and one real Git-to-Desktop-API-to-trust-to-admission proof included in the workflow merge gate.

- [ ] **Step 1: Write failing localization, documentation, and E2E contracts**

```python
def test_git_package_installs_untrusted_then_runs_after_explicit_trust(tmp_path):
    remote = create_multi_package_bare_remote(tmp_path)
    service = service_for(tmp_path)
    service.add_source("public", remote.as_uri())
    review = service.prepare_install(
        InstallRequest(identifier="public/laptop-support"), actor="test"
    )
    install = service.confirm_install(review.confirmation_token, actor="test")
    with pytest.raises(ApiAdmissionError, match="workflow_trust_required"):
        admit(installed_workflow(install, "laptop-diagnostic"))
    trust_review = service.review_trust(install.identity)
    service.grant_trust(trust_review.confirmation_token, actor="test")
    assert admit(installed_workflow(install, "laptop-diagnostic")).run_id
```

Add E2E assertions for private-auth subprocess environment without credential persistence, multiple packages/workflows, update invalidation, failed update rollback, removal, source/profile identity, and installed distribution discovery. Extend locale tests to require every marketplace key. Add documentation tests asserting package/index paths and install-versus-trust wording where the repository uses generated/reference checks.

- [ ] **Step 2: Run new E2E and locale tests to confirm RED**

```bash
uv run pytest -q tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py
cd apps/desktop
npx vitest run --project ui src/i18n/languages.test.ts
```

Expected: fail because the E2E fixture, locale keys, and final docs are incomplete.

- [ ] **Step 3: Add all locale copy, documentation, and merge-gate entries**

Document the exact repository tree, manifest/index/digest roles, Studio prepare-and-user-push handoff, public/private source setup, credential-helper/SSH/`gh` examples, CLI/Desktop lifecycle, install/trust separation, advisories, update diff/trust renewal, rollback, cache recovery, and loose-workflow migration. Add all Marketplace UI keys to the locale type and every locale module; do not weaken locale parity tests.

Add every new backend marketplace test plus `test_marketplace_installed_distribution_e2e.py` to `scripts/test_workflow_merge_gate.sh`. Add `npm run test:workflow-ui --workspace apps/desktop`, focused marketplace API/codec tests, Desktop typecheck, and Desktop lint to the gate's existing Desktop phase without removing current tests.

- [ ] **Step 4: Run full verification**

Run:

```bash
uv run python scripts/generate_workflow_package_contract.py --check
uv run pytest -q tests/plugins/workflow/test_marketplace_*.py \
  tests/hermes_cli/test_git_source.py \
  tests/hermes_cli/test_plugin_install_ref.py \
  tests/plugins/workflow/test_discovery.py \
  tests/plugins/workflow/test_trust_policy.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/test_project_metadata.py
npm run test:workflow-ui --workspace apps/desktop
npm run typecheck --workspace apps/desktop
npm run lint --workspace apps/desktop -- --quiet
scripts/test_workflow_merge_gate.sh
git diff --check
git status --short
```

Expected: all commands exit zero; final status contains only the intended documentation/test changes before commit.

- [ ] **Step 5: Commit the final Hermes slice**

```bash
git add apps/desktop/src/i18n docs/workflow-orchestration.md \
  website/docs/user-guide/features/workflows.md \
  website/docs/user-guide/features/workflow-packages.md \
  website/docs/reference/cli-commands.md \
  tests/plugins/workflow/test_marketplace_installed_distribution_e2e.py \
  scripts/test_workflow_merge_gate.sh
git commit -m "docs(workflow): document package marketplace"
```

## Final review checkpoint

After Task 15:

1. Use `superpowers:requesting-code-review` against `base...HEAD` and address verified findings with test-first commits.
2. Use `superpowers:verification-before-completion` and rerun the final verification commands from a clean worktree.
3. Record the exact contract artifact commit SHA for Workflow Studio.
4. Present the branch, commit list, test totals, known environmental advisories, and review instructions to the user.
5. Stop. Do not merge, delete the worktree, create a release, or modify Workflow Studio until the user explicitly approves the Hermes branch.
