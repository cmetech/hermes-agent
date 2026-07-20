# Workflow Showcase Desktop Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended when delegation is explicitly authorized) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute inline unless the maintainer explicitly authorizes subagents.

**Goal:** Make digest-verified bundled showcases visible in Desktop Workflows and run a shipped no-input approval showcase through the standard background-only API.

**Architecture:** Preserve project-over-profile precedence, but key visible rows by `(source, name)` and carry additive `catalog_source` targeting through detail and admission. Produce showcase targets only through the bounded existing digest-verification path, represent trust as `verified_bundled`, and persist showcase identity as metadata while trigger provenance remains server-derived.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, immutable workflow packages, SQLite `RunStore`, React 19, TypeScript, TanStack Query, Vitest/Testing Library, pytest, YAML/JSON digest manifests.

## Global Constraints

- Base all work on `origin/base`; a future PR targets `base`, never `main`.
- Make one local commit per task. Do not push, merge, tag, release, publish, or deploy.
- Strict red-green-refactor: no production behavior before the named test fails for the expected missing feature.
- Use `scripts/run_tests.sh` for focused Python tests and repository npm/Vitest commands for Desktop.
- Digest verification is the showcase trust boundary. Never raw-scan the showcase directory for discovery.
- Catalog/detail require authenticated `read`; POST requires authenticated `write`; provenance stays server-derived.
- Catalog/detail are read-only. Byte-snapshot workflow and trust state around security-boundary reads.
- HTTP is background-only. `RunScheduler.advance`, `run_showcase`, and `_advance_until_wait` remain unreachable.
- Reuse `show_package`, `_complete_projection`, and `sanitize_projection`; add no redactor or Mermaid generator.
- Keep `laptop-diagnostic` Run-disabled because legacy file/text inputs remain outside flat v1 input support.
- Keep `ai-extensions` Run-disabled because AI consent remains a hard cost/data-egress gate. Keep `scheduling` CLI-only because cron creation, schedule time, and exact-ID/nonce ownership live outside its workflow package in the CLI wrapper.
- Keep `_STORE_SCHEMA_VERSION == 13`; touch no generic host file and add no config or environment variable.
- Put each new `tests/plugins/workflow/test_*.py` file in the merge gate, native CI matrix, or explicit opt-out map in the same commit.
- Add every new i18n key to `types.ts` and en/ja/zh/zh-hant.
- Record every touched file in `docs/upstream-customizations/workflow-orchestration.yaml` in the same commit.
- Before each commit run the task GREEN selection, customization checker, and `git diff --check`.
- Before Task 8's commit, run the full merge gate and reconcile exact counts against the v3.0.1 baseline: Python 745 passed/1 skipped, installed distribution 1 passed, Desktop 51 passed/9 files, TypeScript exit 0.

---

### Task 0: Record approval amendments and deferred architecture

**Files:**

- Modify: `docs/superpowers/specs/2026-07-20-workflow-showcase-desktop-run-design.md`
- Modify: `docs/superpowers/plans/2026-07-20-workflow-showcase-desktop-run-plan.md`
- Create: `docs/backlog/v3.0.2-workflow-showcase-desktop-run.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Records the process-lifetime verified-bundle cache, three explicit regression tests, accepted concurrency contention, corrected scheduling architecture, and two deferred designs.
- Gate/matrix: documentation-only; customization checker and `git diff --check`.
- Ledger: add `workflow-showcase-desktop-run-approved-amendments`.

This task adds no production behavior, so no synthetic RED applies. The
maintainer's approval and contradiction resolution are the documentation
acceptance boundary.

- [ ] **Step 1: Update design, plan, and backlog**

Record Desktop coverage as two of five. Keep scheduling CLI-only because its
cron operation lives outside the package. Backlog background schedule creation
and a separately reviewed AI-consent/architecture pass.

- [ ] **Step 2: Verify and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git commit -m "docs(workflow): record showcase run amendments"
```

---

### Task 1: Ship the parameterless approval showcase

**Files:**

- Create: `plugins/workflow/showcases/packages/approval-gate/workflows/approval-gate.yaml`
- Create: `plugins/workflow/showcases/packages/approval-gate/workflows/approval-gate.hermes.yaml`
- Modify: `plugins/workflow/showcases/catalog.yaml`
- Modify: `plugins/workflow/showcases/digests.json`
- Modify: `plugins/workflow/showcase.py`
- Modify: `tests/plugins/workflow/test_showcase_catalog.py`
- Modify: `tests/plugins/workflow/test_showcase_evidence.py`
- Modify: `scripts/test_workflow_merge_gate.sh`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces scenario/workflow `approval-gate`, no inputs, claim `operator-approval`.
- Preserves all existing CLI showcase command and foreground-tour signatures.
- Gate: `test_showcase_catalog.py` is in merge gate + native matrix; `test_showcase_evidence.py` retains its explicit opt-out.
- Ledger: add `workflow-showcase-desktop-approval-package`.

- [ ] **Step 1: Write RED tests**

Add:

```python
def test_bundled_approval_gate_is_verified_parameterless_and_portable() -> None:
    catalog = load_showcase_catalog()
    scenario = catalog["approval-gate"]
    package = showcase_module._scenario_package(scenario)
    preflight = preflight_showcase("approval-gate", hermes_home=REPO_ROOT)

    assert scenario.verified_bundled_provenance is True
    assert scenario.requires_ai is False
    assert scenario.requires_network is False
    assert "operator-approval" in scenario.capability_claims
    assert [node.node_type for node in package.definition.nodes] == ["approval"]
    assert preflight["input_requirements"] == []
```

Replace exact four-name/count snapshots with subset and per-entry digest invariants. Add evidence tests proving durable approval passes `operator-approval`, while paused reports `awaiting_operator_decision`.

- [ ] **Step 2: Verify RED**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_evidence.py -q
```

Expected: `KeyError: 'approval-gate'` and missing claim handling; existing cases pass.

- [ ] **Step 3: Implement minimal package**

`approval-gate.yaml`:

```yaml
name: approval-gate
description: Pause for an explicit Desktop approval before completing the bundled tour
nodes:
  - id: operator-approval
    approval:
      message: Approve completion of this bundled, offline workflow tour.
      capture_response: true
```

`approval-gate.hermes.yaml`:

```yaml
overlap_policy: queue
execution_environment: trusted_local
limits:
  max_parallel_nodes: 1
  max_total_workers: 1
  subprocess_timeout_seconds: 30
resource_limits:
  max_descendants: 1
outward_action_nodes: []
```

Register it as `Approval Gate Tour`: guided, offline, no AI/network, all three platforms, checkpoint `approval`, outcomes paused/succeeded, no artifacts, claim `operator-approval`, 60-second wall limit. Advance the top-level and every per-scenario bundle version together to 2.1.0; keep unchanged package versions at 1. Add the claim to `_ALLOWED_CLAIMS` and report it from `interaction_approved`.

Generate exact catalog/package digests:

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python - <<'PY'
import hashlib, json
from pathlib import Path
from plugins.workflow.showcase import _tree_digest
root = Path("plugins/workflow/showcases")
manifest = json.loads((root / "digests.json").read_text())
manifest["catalog_sha256"] = hashlib.sha256((root / "catalog.yaml").read_bytes()).hexdigest()
manifest["packages"]["approval-gate"] = _tree_digest(root / "packages" / "approval-gate")
print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
PY
```

Patch `digests.json` with that exact output. Replace the brand gate's exact length assertion with `approval-gate` presence plus all-entry digest invariants.

- [ ] **Step 4: Verify GREEN**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_evidence.py tests/plugins/workflow/test_showcase_offline_e2e.py tests/plugins/workflow/test_cli.py -q
```

- [ ] **Step 5: Ledger and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add plugins/workflow/showcase.py plugins/workflow/showcases tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_evidence.py scripts/test_workflow_merge_gate.sh docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): add bundled approval gate showcase"
```

---

### Task 2: Bound and expose verified showcase package loading

**Files:**

- Modify: `plugins/workflow/showcase.py`
- Modify: `tests/plugins/workflow/test_showcase_catalog.py`
- Modify: `tests/plugins/workflow/test_showcase_distribution_e2e.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces `VerifiedShowcasePackage`, rootless `load_verified_showcase_package(s)` APIs, optional `read_budget`/`allow_repair` on `load_showcase_catalog`, a process-lifetime verified-bundle cache, and one pure background-API eligibility helper.
- Gate: catalog/distribution tests are merge gate + native matrix; installed distribution stays in its integration gate.
- Ledger: add `workflow-showcase-bounded-verification`.

- [ ] **Step 1: Write RED tests**

```python
def test_explicit_catalog_copy_cannot_gain_verified_bundle_provenance(tmp_path: Path) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    scenario = load_showcase_catalog(copied)["approval-gate"]
    assert scenario.verified_bundled_provenance is False
    with pytest.raises(ShowcaseCatalogError, match="bundled distribution provenance"):
        showcase_module._verified_distribution_risk(
            scenario,
            showcase_module._scenario_package(scenario),
        )


def test_bounded_catalog_refuses_oversized_tampering(tmp_path: Path) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    (copied / "packages" / "approval-gate" / "oversized.txt").write_bytes(b"x" * 4097)
    budget = WorkflowResourceReadBudget(
        max_file_bytes=4096,
        max_total_bytes=16384,
        max_files=128,
    )
    with pytest.raises(WorkflowResourceCapacityError):
        load_showcase_catalog(copied, read_budget=budget, allow_repair=False)
```

Add a default-root tamper harness proving `allow_repair=False` never calls
`repair_authenticated_resource_checkout`. Add installed-wheel proof that the
installed resource can construct a verified `approval-gate` package without
source-checkout fallback. Add a table test proving only scenarios that are
guided, offline, non-AI, and non-networked are background-policy eligible;
specifically approval-gate/resilience pass while ai-extensions/scheduling do
not. Input support remains a separate catalog check, so laptop still fails the
combined policy later.

Add cache tests that count `_tree_digest` calls: two unchanged cached loads
perform one full verification, while a package mutation after the first load
invalidates the entry and fails closed. Assert failures are not cached and a
concurrent-miss test produces only one successful verification. Reset the
process cache explicitly between tests.

- [ ] **Step 2: Verify RED**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_distribution_e2e.py -q
```

Expected: missing budget parameter and verified-record API.

- [ ] **Step 3: Implement bounded verification**

Add:

```python
@dataclass(frozen=True, slots=True)
class VerifiedShowcasePackage:
    scenario: ShowcaseScenario
    package: WorkflowPackage
    risk: WorkflowRiskSummary
    bundle_digest: str
```

Thread an optional `WorkflowResourceReadBudget` through metadata reads, `_validate_package_safety`, `_tree_digest`, `_bundle_digest`, `load_showcase_catalog`, and `_verified_distribution_risk`. Add `allow_repair: bool = True` to `load_showcase_catalog`; HTTP-facing callers pass false while existing CLI callers retain the default. Read via:

```python
def _read_verified_bytes(path: Path, budget: WorkflowResourceReadBudget | None) -> bytes:
    resolved = path.resolve(strict=True)
    return resolved.read_bytes() if budget is None else budget.read(resolved)
```

On checkout repair, restart verification with a fresh budget using the same maxima. Extend the internal package loader with keyword defaults `source="explicit"`, `precedence=0`; CLI callers remain unchanged.

Implement `load_verified_showcase_packages(*, read_budget)` and
`load_verified_showcase_package(showcase_id, *, read_budget)` without any
bundle-root or scenario parameter. Each opens `_bundle_path()` once, invokes
the same catalog/digest/safety implementation with repair disabled, loads the
workflow from that same verified root, rechecks its tree digest, builds
ordinary risk, runs ordinary risk preflight, and returns the immutable record.
Do not reject a verified record merely because its current environment is
incompatible; Task 3 projects that state honestly. Keep the existing strict
compatibility default for CLI execution and the false-provenance guard in
`_verified_distribution_risk`. Never infer trust from a caller-constructed
dataclass or boolean. Add a pure
`showcase_background_api_eligible(scenario)` helper; both catalog projection
and admission must call it rather than duplicating consent rules.

Memoize successful rootless list/detail loads by `_bundle_digest()` plus a
bounded complete tree signature (relative path/type, device/inode when
available, size, mtime_ns, ctime_ns). The signature invalidates only; SHA-256
remains authoritative. Disable the fast path if the platform cannot supply the
signature, lock concurrent misses, never cache failures, and expose only a
private test reset. Admission passes `force_reverify=True` and never uses the
fast path.

- [ ] **Step 4: Verify GREEN and installed wheel**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_distribution_e2e.py -q
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python -m pytest -q -m integration tests/plugins/workflow/test_installed_distribution_e2e.py
```

- [ ] **Step 5: Ledger and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add plugins/workflow/showcase.py tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_distribution_e2e.py tests/plugins/workflow/test_installed_distribution_e2e.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "fix(workflow): bound verified showcase loading"
```

---

### Task 3: Add verified showcases to list and detail

**Files:**

- Modify: `plugins/workflow/catalog_api.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_catalog_api.py`
- Modify: `tests/plugins/workflow/test_workflow_detail_api.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces `CatalogSource`, `CatalogTrustState`, `CatalogRunSupport`, shared `workflow_catalog_run_support(...)`, source-aware resolved target, optional detail `catalog_source`.
- Consumes Task 2's verified package API.
- Gate: both test files are explicitly in the focused merge gate.
- Ledger: add `workflow-showcase-catalog-projection`.

- [ ] **Step 1: Write RED tests**

Assert list contains:

```python
approval = next(
    item for item in response.json()["items"]
    if item.get("name") == "approval-gate" and item.get("source") == "showcase"
)
assert approval["trust_state"] == "verified_bundled"
assert approval["supported_inputs"] == {"supported": True, "reason": "parameterless"}
assert approval["run_support"] == {"supported": True, "reason": "supported"}
```

Create a project workflow also named `approval-gate`; assert both rows appear, bare detail resolves project, and `catalog_source=showcase` resolves the bundle. With no user collision, assert bare detail remains project/profile-only rather than silently opting into a showcase. Explicitly request `catalog_source=project` for a showcase-only name and require typed 404. Assert laptop has `unsupported_inputs`, while ai-extensions and scheduling have `showcase_cli_required`. With MCP unavailable, assert ai-extensions remains visible with honest incompatible state and every other showcase remains visible. Add CF-1 POSIX/Windows path-in-description parity. Tamper the default-bundle harness and assert list omits every showcase without invoking checkout repair, exact detail returns typed verification failure, and store/trust byte snapshots do not change. Make the default bundle missing/unreadable and assert list still returns 200 with user rows and zero showcases. Rewrite existing list tests to select user rows by `(source, name)`, retaining all prior assertions and adding their input-derived run support.

Add a measured hot-path test: call `build_workflow_catalog` twice, count
full-tree digest/read operations, require only the first stable request to pay
the full verification cost, and prove a normal user row remains present with
`truncated=False` on both calls. Mutate the bundle and require the next list to
invalidate the cache and omit showcases rather than serve stale trust.

- [ ] **Step 2: Verify RED**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py -q
```

Expected: no showcase row, no exact source selection, and CF-1 path leak.

- [ ] **Step 3: Implement combined projection**

Add:

```python
CatalogSource = Literal["project", "profile", "showcase"]
CatalogTrustState = Literal["trusted", "untrusted", "verified_bundled"]
CatalogRunSupportReason = Literal[
    "supported",
    "unsupported_inputs",
    "showcase_cli_required",
]
```

Keep user precedence collapse unchanged. Build showcase targets only from cached `load_verified_showcase_packages(...)` with repair disabled, charge actual bytes read on that request against the existing aggregate request budget, assign source/precedence `showcase`/3, reserve verified showcase rows inside the 500-row limit, and sort by `(name, precedence, source)`. Missing/unreadable/verification-failed bundles degrade to no showcase rows without failing user rows; only a genuine capacity omission changes `truncated`. Set all valid list descriptions from:

```python
"description": str(shown["definition"]["description"])
```

Project `run_support` on every list/detail result. Users mirror existing input
support. Showcases require Task 2's scenario policy and supported inputs;
AI/architecture-only failures use `showcase_cli_required`, while laptop uses
`unsupported_inputs`. Put that composition in public pure
`workflow_catalog_run_support(package, *, showcase_scenario=None)` so admission
can re-derive the exact policy instead of trusting a response or duplicating
logic.

Extend `build_workflow_detail(..., catalog_source=None)`. Omitted source retains project/profile name precedence and never selects a showcase; project/profile filters the selected user target; showcase resolves only a freshly verified record. Extend Pydantic source/trust/run-support literals and add a query parameter. Map verification to typed nonretryable 409 and budget exhaustion to retryable 503.

- [ ] **Step 4: Verify GREEN**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_catalog_cli.py -q
```

- [ ] **Step 5: Ledger and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add plugins/workflow/catalog_api.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): list verified bundled showcases"
```

---

### Task 4: Admit verified showcases through background POST /runs

**Files:**

- Modify: `plugins/workflow/api_admission.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_provenance.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces optional `catalog_source` in request and `start_api_run`; showcase run metadata.
- Gate: `test_desktop_api.py` is native matrix; `test_provenance.py` remains explicitly opted out. Task 7 supplies focused-gate E2E.
- Ledger: add `workflow-showcase-api-admission`.

- [ ] **Step 1: Write RED tests**

POST `approval-gate` with `catalog_source=showcase`; assert 202, server-derived Desktop provenance, background execution, `showcase_id`, `showcase_provenance=verified_bundled`, and ready nodes at response time. Monkeypatch `RunScheduler.advance` to raise. Repeat the same showcase admission with the same idempotency key and assert the same start digest/run ID with `existing` disposition; bundle/risk content digests must be stable. Add copied-bundle, post-verification mutation, unsupported laptop (422 `workflow_inputs_unsupported`), AI-consent/scheduling (409 `workflow_showcase_cli_required`), an environment-incompatible selected showcase (typed nonretryable compatibility failure), unhealthy coordinator, forged provenance, omitted-source, and same-name user/showcase targeting cases; every failure leaves no run/staging residue. Run existing user-source golden start-digest tests unchanged.

- [ ] **Step 2: Verify RED**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_provenance.py -q
```

Expected: request rejects extra `catalog_source` or resolves the user row; existing digest goldens pass.

- [ ] **Step 3: Implement showcase admission branch**

Add optional `catalog_source` to Pydantic and `start_api_run`. Omission keeps the current user-only resolver; `showcase` resolves by ID through Task 2's rootless verified loader with repair disabled. Before snapshot preparation, call Task 3's `workflow_catalog_run_support(...)` and reject a false result with the same typed reason projected to the UI. Use one standard resource budget for verified package recheck, ordinary digest/risk equality, cache seal, compatibility, preflight, coordinator health, immutable snapshot, and snapshot digest check. User trust remains the current read-only trust snapshot. Showcase trust is valid only when that loader returns `VerifiedShowcasePackage`; no record or root is accepted as a function argument.

Set showcase-only fields:

```python
run_metadata = {
    "showcase_id": verified.scenario.id,
    "showcase_version": verified.scenario.package_version,
    "bundle_digest": verified.bundle_digest,
    "risk_digest": risk.risk_digest,
    "showcase_provenance": "verified_bundled",
}
concurrency_key = f"showcase:{verified.scenario.id}"
```

For users retain `run_metadata=None` and existing concurrency semantics. Always use authority namespace/scope, server provenance, and `execution_mode="background"`.

- [ ] **Step 4: Verify GREEN and reachability**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_provenance.py -q
rg -n "RunScheduler|\.advance\(|run_showcase|_advance_until_wait" plugins/workflow/api_admission.py plugins/workflow/dashboard/plugin_api.py
```

Expected: tests pass; `rg` prints no request/admission inline-execution reference.

- [ ] **Step 5: Ledger and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add plugins/workflow/api_admission.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_provenance.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): admit verified showcases in background"
```

---

### Task 5: Carry exact catalog identity through Desktop transport

**Files:**

- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/lib/hermes-api.ts`
- Modify: `apps/desktop/src/lib/hermes-api.test.ts`
- Modify: `apps/desktop/src/app/workflows/detail-query.ts`
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces `WorkflowCatalogSource`, `WorkflowRunSupport`, source-aware preflight, `StartWorkflowRunRequest.catalogSource`, source-aware query key.
- Gate: review/view tests are Desktop merge gate; API tests are `test:workflow-ui`.
- Ledger: add `desktop-showcase-source-identity`.

- [ ] **Step 1: Write RED transport/cache tests**

Require:

```typescript
await preflightWorkflow('approval-gate', 'showcase', 'profile-a')
expect(apiStructured).toHaveBeenCalledWith({
  path: '/api/plugins/workflow/workflows/approval-gate?catalog_source=showcase',
  profile: 'profile-a'
})
```

Require POST body `catalog_source: 'showcase'`. Prove project/showcase same-name query keys differ. Update View/Review expectations to carry exact source plus captured profile while keeping one modal UUID.

- [ ] **Step 2: Verify RED**

```bash
cd apps/desktop
npx vitest run src/lib/hermes-api.test.ts src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx
```

- [ ] **Step 3: Implement exact source transport**

Add:

```typescript
export type WorkflowCatalogSource = 'profile' | 'project' | 'showcase'
export type WorkflowTrustState = 'trusted' | 'untrusted' | 'verified_bundled'
export interface WorkflowRunSupport {
  supported: boolean
  reason: 'supported' | 'unsupported_inputs' | 'showcase_cli_required'
}
```

Extend definition source/precedence/trust unions and require `run_support` on list/detail types. Add `catalogSource` to the start request. Encode `catalog_source` with `URLSearchParams`. Make query keys `['workflow-detail', profile ?? 'default', source, name]`. Pass `workflow.source` through View, Review, cancel, and POST without changing retry/focus/profile/idempotency behavior.

- [ ] **Step 4: Verify GREEN**

```bash
cd apps/desktop
npx vitest run src/lib/hermes-api.test.ts src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx
npx tsc -p . --noEmit
```

- [ ] **Step 5: Ledger and commit**

```bash
cd ../..
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add apps/desktop/src/types/hermes.ts apps/desktop/src/lib/hermes-api.ts apps/desktop/src/lib/hermes-api.test.ts apps/desktop/src/app/workflows/detail-query.ts apps/desktop/src/app/workflows/index.tsx apps/desktop/src/app/workflows/review-run-dialog.tsx apps/desktop/src/app/workflows/view-workflow-dialog.tsx apps/desktop/src/app/workflows/review-run-dialog.test.tsx apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(desktop): preserve workflow catalog source identity"
```

---

### Task 6: Render bundled trust and honest Run policy

**Files:**

- Create: `apps/desktop/src/app/workflows/catalog-run-policy.ts`
- Create: `apps/desktop/src/app/workflows/catalog-run-policy.test.ts`
- Modify: `apps/desktop/src/app/workflows/catalog.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.test.tsx`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`
- Modify: `scripts/test_workflow_merge_gate.sh`
- Modify: `tests/scripts/test_workflow_merge_gate.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces `workflowTrustAllowsRun(state)`, authoritative run-support handling, Bundled showcase/Verified bundle labels, and CLI-only showcase guidance.
- Gate: add new policy test and `index.test.tsx` to Desktop merge gate; pin in meta-test; both are in `test:workflow-ui`.
- Ledger: add `desktop-showcase-catalog-ui`.

- [ ] **Step 1: Write RED policy/UI/i18n tests**

```typescript
describe('workflow catalog run trust', () => {
  it.each([
    ['trusted', true],
    ['verified_bundled', true],
    ['untrusted', false]
  ] as const)('%s -> %s', (state, expected) => {
    expect(workflowTrustAllowsRun(state)).toBe(expected)
  })
})
```

Add same-name Project/Bundled rows. Assert Bundled showcase source, Verified bundle trust, enabled Run for no-input approval, disabled laptop Run with accessible “run from CLI” reason, disabled ai-extensions/scheduling Run with the same honest CLI route, and View/Review trust plus `run_support` eligibility from authoritative detail. Run locale key parity.

- [ ] **Step 2: Verify RED**

```bash
cd apps/desktop
npx vitest run src/app/workflows/catalog-run-policy.test.ts src/app/workflows/index.test.tsx src/app/workflows/view-workflow-dialog.test.tsx src/app/workflows/review-run-dialog.test.tsx src/i18n/languages.test.ts
```

- [ ] **Step 3: Implement one trust predicate and labels**

```typescript
import type { WorkflowTrustState } from '@/types/hermes'

export function workflowTrustAllowsRun(state: WorkflowTrustState): boolean {
  return state === 'trusted' || state === 'verified_bundled'
}
```

Use it in Catalog, View, Review together with authoritative
`run_support.supported`; never derive consent eligibility client-side. Add
`workflowSourceBundled`, `workflowVerifiedBundle`, and
`workflowRunShowcaseFromCli` to all locale files. Show the CLI message for
showcase `unsupported_inputs` and `showcase_cli_required`; preserve existing
user-input messaging. Keep existing error priority, View availability,
focusable disabled explanation, and `aria-describedby`. Add policy/index tests
to merge script and meta-test.

- [ ] **Step 4: Verify GREEN**

```bash
cd apps/desktop
npx vitest run src/app/workflows/catalog-run-policy.test.ts src/app/workflows/index.test.tsx src/app/workflows/view-workflow-dialog.test.tsx src/app/workflows/review-run-dialog.test.tsx src/i18n/languages.test.ts
npm run test:workflow-ui
npx tsc -p . --noEmit
```

- [ ] **Step 5: Ledger and commit**

```bash
cd ../..
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add apps/desktop/src/app/workflows apps/desktop/src/i18n apps/desktop/src/types/hermes.ts scripts/test_workflow_merge_gate.sh tests/scripts/test_workflow_merge_gate.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(desktop): surface verified bundled workflows"
```

---

### Task 7: Prove real middleware and gate membership

**Files:**

- Create: `tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py`
- Modify: `scripts/test_workflow_merge_gate.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/scripts/test_workflow_merge_gate.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces real authenticated catalog -> detail -> POST -> board proof.
- Gate: new Python file goes in focused merge gate and three-OS native matrix in this commit; structural test pins both.
- Ledger: add `workflow-showcase-desktop-real-middleware-e2e`.

- [ ] **Step 1: Write the RED structural gate test first**

Before creating the E2E file or editing either gate, add:

```python
def test_showcase_desktop_e2e_is_in_merge_gate_and_native_matrix() -> None:
    path = "tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py"
    assert path in GATE.read_text()
    assert path in CI.read_text()
```

Run:

```bash
scripts/run_tests.sh tests/scripts/test_workflow_merge_gate.py -q
```

Expected RED: the prospective E2E is absent from both required selections.

- [ ] **Step 2: Write and run the real-middleware E2E**

Follow the real app/middleware setup in `test_workflow_catalog_desktop_e2e.py`. Use no catalog/detail/admission/store mocks. Assert:

```python
row = next(
    item for item in client.get("/api/plugins/workflow/workflows", headers=headers).json()["items"]
    if item.get("source") == "showcase" and item.get("name") == "approval-gate"
)
assert row["trust_state"] == "verified_bundled"
assert row["run_support"] == {"supported": True, "reason": "supported"}

detail = client.get(
    "/api/plugins/workflow/workflows/approval-gate",
    params={"catalog_source": "showcase"},
    headers=headers,
)
assert detail.status_code == 200
assert detail.json()["topology"]["mermaid"]
assert detail.json()["definition"]["nodes"][0]["value"] == "[REDACTED]"
```

POST with exact source and idempotency key; assert 202, verified-bundled metadata, Desktop provenance, background mode, ready node at response, and board visibility. Patch `RunScheduler.advance` to raise. Byte-snapshot store/trust before list/detail and prove only POST mutates.

- [ ] **Step 3: Verify the direct behavior test**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py -q
```

Expected GREEN: Tasks 1-6 already supplied the behavior through focused
red/green cycles. The still-failing structural test prevents committing the
new E2E without its required gate/matrix ownership.

- [ ] **Step 4: Add gate/matrix membership**

Add the file beside the existing catalog E2E in the merge gate and beside
`test_desktop_api.py` in CI.

- [ ] **Step 5: Verify GREEN**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py tests/scripts/test_workflow_merge_gate.py -q
```

- [ ] **Step 6: Ledger and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py scripts/test_workflow_merge_gate.sh .github/workflows/ci.yml tests/scripts/test_workflow_merge_gate.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "test(workflow): prove bundled Desktop admission end to end"
```

---

### Task 8: Update docs, run real UAT, and reconcile all gates

**Files:**

- Modify: `tests/test_desktop_workflow_test_gate.py`
- Modify: `website/docs/user-guide/features/workflows.md`
- Create: `docs/reviews/2026-07-20-workflow-showcase-desktop-run-verification.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces user guidance and exact verification/UAT evidence.
- Gate: docs test is standard suite; security files are already focused/native selections.
- Ledger: add `workflow-showcase-desktop-run-verification`.

- [ ] **Step 1: Write RED docs test**

```python
assert "Bundled showcase" in docs
assert "Verified bundle" in docs
assert re.search(r"approval-gate[^.]*Attention[^.]*Approve", docs, re.IGNORECASE)
assert re.search(r"laptop-diagnostic[^.]*CLI", docs, re.IGNORECASE)
assert re.search(r"ai-extensions[^.]*CLI", docs, re.IGNORECASE)
assert re.search(r"scheduling[^.]*CLI", docs, re.IGNORECASE)
assert "trust the bundled showcase" not in docs.lower()
```

- [ ] **Step 2: Verify RED**

```bash
scripts/run_tests.sh tests/test_desktop_workflow_test_gate.py -q
```

- [ ] **Step 3: Update docs minimally**

Document badges, source collisions, no trust action for verified bundles, approval-gate walkthrough, CLI-only laptop inputs, the retained AI-consent path, and scheduling's wrapper/package architecture boundary. Remove L-A/L-B wording without claiming rich Desktop inputs or background admission for CLI-only tours.

- [ ] **Step 4: Run real Desktop UAT**

Use fresh temporary Hermes/profile/project state and the real Desktop app/backend. Record:

1. approval-gate appears Bundled showcase / Verified bundle / No inputs;
2. View renders diagram and read-only redacted Definition;
3. Review & Run offers no trust action;
4. Start uses authenticated POST and opens Active;
5. coordinator moves run to Attention;
6. real Attention Approve completes the run;
7. durable projection says desktop, background, verified_bundled;
8. laptop View works but Run is disabled with CLI guidance;
9. ai-extensions remains CLI-only for AI consent and scheduling remains CLI-only because cron creation is outside its workflow package.

Do not use `run_showcase`, direct advance, copied packages, trust injection, or mocked middleware.

- [ ] **Step 5: Run full verification before the commit**

```bash
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
cd apps/desktop
npm run test:workflow-ui
npm run typecheck
npx eslint src/app/workflows/catalog-run-policy.ts src/app/workflows/catalog-run-policy.test.ts src/app/workflows/catalog.tsx src/app/workflows/detail-query.ts src/app/workflows/index.tsx src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.tsx src/app/workflows/view-workflow-dialog.test.tsx src/lib/hermes-api.ts src/lib/hermes-api.test.ts src/types/hermes.ts src/i18n/types.ts src/i18n/en.ts src/i18n/ja.ts src/i18n/zh.ts src/i18n/zh-hant.ts
cd ../..
scripts/run_tests.sh tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_showcase_distribution_e2e.py tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py -q
git diff --check
```

Then prove the paired-release contract without merging or updating any branch
ref. Create two detached temporary worktrees at the tested feature SHA,
materialize OTTO and LOOP24 independently with the normal brand generator, and
run each brand gate:

```bash
TESTED_FEATURE_SHA="$(git rev-parse HEAD)"
SHOWCASE_BRAND_REHEARSAL="$(mktemp -d)"
OTTO_REHEARSAL="$SHOWCASE_BRAND_REHEARSAL/otto"
LOOP24_REHEARSAL="$SHOWCASE_BRAND_REHEARSAL/loop24"
git worktree add --detach "$OTTO_REHEARSAL" "$TESTED_FEATURE_SHA"
git worktree add --detach "$LOOP24_REHEARSAL" "$TESTED_FEATURE_SHA"
(cd "$OTTO_REHEARSAL" && node scripts/brand/generate.mjs otto)
(cd "$LOOP24_REHEARSAL" && node scripts/brand/generate.mjs loop24)
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python "$OTTO_REHEARSAL/scripts/test_workflow_merge_gate.sh" --repo "$OTTO_REHEARSAL" --phase brand --brand otto --tested-base-sha "$TESTED_FEATURE_SHA"
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python "$LOOP24_REHEARSAL/scripts/test_workflow_merge_gate.sh" --repo "$LOOP24_REHEARSAL" --phase brand --brand loop24 --tested-base-sha "$TESTED_FEATURE_SHA"
git worktree remove --force "$OTTO_REHEARSAL"
git worktree remove --force "$LOOP24_REHEARSAL"
rmdir "$SHOWCASE_BRAND_REHEARSAL"
```

Remove the detached worktrees afterward. Do not merge, update, push, or create
OTTO or LOOP24 refs during this rehearsal.

Record exact counts/durations/skips/tested SHA. Reconcile every new selected test function against the 745/1, 1, and 51/9 baseline. Removed tests must be zero; rewritten count snapshots are modified invariants, not removed behavior.

- [ ] **Step 6: Write verification and GREEN docs test**

The verification doc records one-commit-per-task RED/GREEN evidence, exact gates and arithmetic, real middleware, real UAT, no schema/host/inline changes, both-brand release intent, and no-push/no-release boundary.

```bash
scripts/run_tests.sh tests/test_desktop_workflow_test_gate.py -q
```

- [ ] **Step 7: Ledger and commit**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add tests/test_desktop_workflow_test_gate.py website/docs/user-guide/features/workflows.md docs/reviews/2026-07-20-workflow-showcase-desktop-run-verification.md docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "docs(workflow): verify bundled Desktop showcase runs"
```

---

### Task 9: Perform the fresh adversarial review

**Files:**

- Create: `docs/reviews/2026-07-20-workflow-showcase-desktop-run-adversarial-review.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Produces completion verdict against the design and plan.
- Gate: documentation-only final commit; reproduce decisive focused selections and rerun the full merge gate immediately before committing.
- Ledger: add `workflow-showcase-desktop-run-adversarial-review`.

This audit adds no production behavior, so there is no synthetic RED step. Its
fail-closed review verdict and fresh verification gate are the test-first
boundary for the documentation-only commit.

- [ ] **Step 1: Review exact `origin/base...HEAD` diff**

Threat-check raw scans/arbitrary roots, digest/budget/TOCTOU, copied-bundle confusion, collision ambiguity, caller provenance, existing digest drift, user-trust conflation, AI-consent/scheduling bypass, request-time execution, snapshot-byte drift, auth ordering, read mutation, redaction/Mermaid bypass, laptop enablement, i18n/gate/matrix/ledger/wheel gaps, CLI regressions, schema/host drift, and paired-brand scope.

- [ ] **Step 2: Reproduce decisive tests**

```bash
scripts/run_tests.sh tests/plugins/workflow/test_showcase_catalog.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py tests/plugins/workflow/test_provenance.py -q
cd apps/desktop
npx vitest run src/lib/hermes-api.test.ts src/app/workflows/catalog-run-policy.test.ts src/app/workflows/index.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx
cd ../..
```

- [ ] **Step 3: Enforce finding gate**

If any Critical or High exists, stop without a completion verdict. Add a new red-green remediation task/commit, rerun Task 8, and restart review at the new SHA. Important/Medium requires maintainer disposition. Only zero Critical/High permits the final review doc.

- [ ] **Step 4: Rerun the full gate at the reviewed tree**

```bash
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
```

Require the same reconciled counts as Task 8 plus only explicitly counted test
additions. Record this fresh run in the adversarial review so the last commit
is preceded by a full green gate.

- [ ] **Step 5: Ledger and commit review**

```bash
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml
git diff --check
git add docs/reviews/2026-07-20-workflow-showcase-desktop-run-adversarial-review.md docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "docs(workflow): record bundled Desktop showcase review"
```

Expected final state: clean local branch; nine planned commits plus explicitly documented TDD remediation commits if required; Task 8 full gate green before the final review commit; zero Critical/High; nothing pushed, merged, tagged, released, or published.

---

## Plan self-review checklist

- Every design requirement has a task owner.
- Every production behavior starts with an observed failing behavioral test.
- Existing precedence and idempotency bytes are pinned.
- Digest verification is reused and bounded, not duplicated.
- Showcase metadata and trigger provenance stay separate.
- Read-only and background-only invariants have real-path tests.
- The approval package does not alter laptop-diagnostic.
- Four locales, gate/matrix membership, installed wheel, ledger, baseline reconciliation, UAT, and adversarial review are explicit.
- No migration, generic host import, environment variable, inline HTTP execution, raw scan, trust-store grant, or release action appears.
