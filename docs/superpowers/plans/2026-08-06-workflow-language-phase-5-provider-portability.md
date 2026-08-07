# Workflow Language Phase 5: Provider Portability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Status:** Review-ready; implementation requires explicit user approval

**Goal:** Deliver one sealed, backend-owned provider capability and model-resolution authority for new Archon workflows, with truthful hooks/MCP/provider controls, shared enforceable cost budgets, and no regression to v1-v4 behavior.

**Architecture:** Extend provider profiles and `ExecutionRuntimeCapabilities` with pure declarations, resolve every AI/fallback/inline route through `ExecutionCapabilityContext`, and seal a bounded credential-free `provider-resolution.json` in snapshot format 2. Normalizer v5 owns new model-reference and hook/MCP semantics. Compatibility, trust, admission, revalidation, execution, evidence, catalog/detail, and Desktop derive from the same decisions. A generic authenticated request authority shares provider attempts, authoritative settled cost, deadlines, resources, workdir, and cancellation across parent, fallback, repair, and inline workers.

**Tech Stack:** Python 3.11+, frozen dataclasses/enums, PyYAML, SQLite/JSONL workflow store, existing provider/plugin registries, pytest through `scripts/run_tests.sh`, FastAPI/Pydantic projections, Electron/React/TypeScript/Vitest, and existing workflow merge/ledger scripts.

## Global constraints

- Start from `cff7875049a7f369c2eae758503c63b6467c4433` on `feat/workflow-language-phase-5-provider-portability`.
- Do not rebase, push, merge, tag, release, or modify `base`, literal `main`, `otto`, `loop24`, brand repositories, or the Phase 4 worktree.
- Preserve exact v1-v4 snapshot behavior and legacy normalizer v2.
- Keep snapshot format 2; v5 adds exact conditional members.
- Do not activate current Archon normalizer v5 until Task 15.
- Do not implement Phase 6 `loop_group`, runtime child workflows, dynamic includes, `include.with`, deep child-output navigation, or input mapping.
- Add no core model tool, telemetry, synthetic conversation message, mid-conversation system-prompt mutation, or user-facing non-secret `HERMES_*` variable.
- Preserve `allowed_tools: []` as no callable tools. Do not auto-add `workflow_agent`; block an unreachable inline-agent declaration.
- Every unsupported Archon obligation blocks before trust mutation, run creation, MCP spawn, or provider transport.
- Public evidence is closed, bounded, redacted, and credential-free.
- Generic upstream-owned changes receive invariant tests and ledger entries. Do not advance `last_verified_upstream`.
- A task that changes files outside `plugins/workflow` must update its exact
  symbol-level entry in `docs/upstream-customizations/workflow-orchestration.yaml`,
  run `tests/scripts/test_check_upstream_customizations.py`, and stage the
  ledger in that same atomic commit. Task 15 only reconciles the ledger; it
  never retroactively records earlier generic commits.
- Use `apply_patch` and atomic commits. Run Python tests only through the canonical wrapper.
- In task file lists, `Test:` means an existing read-only regression target;
  `Create:` or `Modify:` test files are implementation changes and must appear
  in that task's `git add`. If a RED test requires changing a file currently
  marked only `Test:`, first promote it to `Modify:` and stage it in the same
  commit.

Canonical test prefix for this linked worktree:

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh
```

## Planned interfaces

```python
class CapabilityDisposition(str, Enum):
    NATIVE = "native"
    HERMES_ADAPTER = "hermes_adapter"
    DEGRADED_WITH_EXPLICIT_SEMANTICS = "degraded_with_explicit_semantics"
    UNSUPPORTED = "unsupported"

class WorkflowProviderFeature(str, Enum):
    STRUCTURED_OUTPUT = "structured_output"
    SESSION_RESUMPTION = "session_resumption"
    TOOL_RESTRICTIONS = "tool_restrictions"
    HOOKS = "hooks"
    MCP = "mcp"
    SKILLS_INLINE_AGENTS = "skills_inline_agents"
    EFFORT_THINKING = "effort_thinking"
    FALLBACK_MODELS = "fallback_models"
    WEB_EXECUTION = "web_execution"
    COST_BUDGETS = "cost_budgets"
    PROVIDER_NATIVE_SANDBOX = "provider_native_sandbox"

@dataclass(frozen=True, slots=True)
class ResolvedModelRoute:
    requested_reference: str
    reference_kind: Literal["tier", "configured_alias", "literal"]
    provider: str
    model: str
    api_mode: str
    route_fingerprint: str
    registration_provenance_digest: str
    provider_options: Mapping[str, JSONValue]
    config_scope: Literal["profile", "managed"]
    warnings: tuple[ResolutionDiagnostic, ...]

@dataclass(frozen=True, slots=True)
class ProviderCapabilityDecision:
    feature: WorkflowProviderFeature
    disposition: CapabilityDisposition
    provider: str
    model: str
    option: str | None
    requested_semantics: Mapping[str, JSONValue]
    effective_semantics: Mapping[str, JSONValue]
    adapter_version: int | None
    declaration_source: str
    registration_provenance_digest: str
    code: str
    rationale: str

@dataclass(frozen=True, slots=True)
class ProviderAuthoritySnapshot:
    schema_version: int
    resolver_version: int
    config_fingerprint: str
    node_resolutions: Mapping[str, NodeProviderResolution]
    authority_digest: str
```

`ProviderAuthoritySnapshot.canonical_bytes()` uses sorted canonical JSON and an exact bounded reader. It excludes URLs, credentials, environment values, prompts, commands, raw provider configuration/responses, and paths.

The generic budget wire object contains an exact bounded-decimal limit, a code-owned settlement strategy, and a private authenticated authority descriptor. Its public result is a closed typed ledger rather than arbitrary numeric keys.

---

### Task 0: Establish a clean implementation baseline or stop

**Files:** None.

- [ ] **Step 1: Reconfirm repository and isolation invariants.**

Verify the implementation worktree is on this feature branch, its merge-base is
the approved `base` SHA, root remains clean on `base`, the preserved Phase 4
worktree/files remain untouched, and no brand/literal-main checkout is active.
Capture `git worktree list --porcelain`, relevant local/remote refs, tags, and
status before any implementation mutation.

- [ ] **Step 2: Run the clean no-retry base baseline.**

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh
cd apps/desktop
npm run typecheck
npm test
npm run lint
cd ../../
```

- [ ] **Step 3: Decide the gate from evidence.**

If any required baseline fails, stop Phase 5 implementation and report the
exact pre-existing failure; do not fold unrelated remediation into this phase.
Implementation begins only from a reproducibly green baseline or after the
user explicitly approves a separately scoped prerequisite fix. The earlier
388-test planning smoke suite is useful evidence but does not replace this
gate.

---

### Task 1: Prove and repair provider-resolution prerequisites

**Files:**

- Modify: `hermes_cli/providers.py`
- Modify: `providers/__init__.py`
- Modify: `tests/hermes_cli/test_runtime_provider_resolution.py`
- Create: `tests/hermes_cli/test_provider_profile_precedence.py`
- Create: `tests/hermes_cli/test_execution_runtime_capabilities.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write failing route and registry invariant tests.**

Table-drive every bundled `ProviderProfile.api_mode`, including `codex_responses`; prove prospective/actual classification parity without credentials/network/subprocess; prove user provider plugins cannot be overwritten by later legacy discovery; prove loader-assigned immutable provenance distinguishes bundled, legacy, and user-plugin registrations; mutate an imported declaration helper and same-version plugin body and require the closure digest to change; and prove custom/alias routes cannot borrow another profile's native guarantees by name or hostname.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_runtime_provider_resolution.py tests/hermes_cli/test_provider_profile_precedence.py tests/hermes_cli/test_execution_runtime_capabilities.py -q
```

Expected: failures expose the `codex_responses` transport mismatch and last-writer profile collision.

- [ ] **Step 3: Implement narrow generic fixes.**

Use the canonical API-mode spelling already declared by profiles. Make discovery precedence deterministic: bundled < legacy-compatible < user plugin, with bounded collision diagnostics. Registration provenance comes from the loader and binds origin kind plus a mandatory complete declaration/encoder code-closure digest across the owning distribution and imported local helpers; profiles cannot self-label it. An unhashable closure is ineligible for provider-native/degraded declarations. Add no workflow policy here.

- [ ] **Step 4: Run the Step 2 command GREEN.**

- [ ] **Step 5: Commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add hermes_cli/providers.py providers/__init__.py tests/hermes_cli/test_runtime_provider_resolution.py tests/hermes_cli/test_provider_profile_precedence.py tests/hermes_cli/test_execution_runtime_capabilities.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "fix(providers): make runtime authority deterministic"
```

### Task 2: Build the central capability decision engine

**Files:**

- Create: `hermes_cli/provider_capabilities.py`
- Modify: `providers/base.py`
- Modify: `hermes_cli/runtime_provider.py`
- Modify: `plugins/model-providers/openrouter/__init__.py`
- Create: `tests/hermes_cli/test_provider_capabilities.py`
- Test: `tests/hermes_cli/test_runtime_provider_resolution.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write exhaustive RED tests.**

Assert the feature enum is exactly the eleven Phase 5 features; each request gets exactly one disposition; malformed/unknown declarations fail closed; explicit unsupported beats hostname appearance; custom/aggregator routes cannot claim native budget/sandbox; an overriding user plugin named `openrouter` cannot inherit or assert distribution-owned billing/sandbox guarantees; missing/incomplete closure provenance permits only generic Hermes adapters; same-version/helper mutation changes decisions and authority identity; adapters require all runtime preconditions; and decisions/provenance are immutable, bounded, JSON-safe, and secret-free. Extend the installed-wheel test to import packaged provider profiles/declarations offline with repository imports excluded.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_provider_capabilities.py tests/hermes_cli/test_runtime_provider_resolution.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
```

- [ ] **Step 3: Implement declarations and resolver.**

Add defaulted profile facts/methods so existing constructors remain compatible. Provider facts describe native behavior only; the central resolver adds versioned Hermes adapters and explicit degraded semantics. Refactor structured output to consume shared route facts while retaining compatibility exports.

- [ ] **Step 4: Prove prospective resolution is pure.**

Monkeypatch credential loaders, network clients, and subprocess creation to fail if classification touches them.

- [ ] **Step 5: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_provider_capabilities.py tests/hermes_cli/test_runtime_provider_resolution.py tests/plugins/workflow/test_runner_binding.py tests/plugins/workflow/test_provider_compat.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add hermes_cli/provider_capabilities.py providers/base.py hermes_cli/runtime_provider.py plugins/model-providers/openrouter/__init__.py tests/hermes_cli/test_provider_capabilities.py tests/plugins/workflow/test_installed_distribution_e2e.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(providers): centralize workflow capability decisions"
```

### Task 3: Add pure portable model-reference resolution

**Files:**

- Create: `hermes_cli/workflow_model_resolution.py`
- Modify: `hermes_cli/model_switch.py`
- Modify: `hermes_cli/config.py`
- Create: `tests/hermes_cli/test_workflow_model_resolution.py`
- Modify: `tests/hermes_cli/test_config_validation.py`
- Modify: `tests/hermes_cli/test_ollama_cloud_auth.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED tests for all shapes and precedence.**

Cover `model_tiers.small|medium|large`, rich `model_aliases`, existing `model.aliases`, top-level precedence, exact `@alias`, literal pass-through, missing reference failures, tier/alias provider winning with one warning, literal provider precedence (node > workflow > immutable configured route), option precedence (node > workflow > tier/alias defaults), unresolved `auto` blocking, exact profile-versus-managed provenance and managed leaf precedence, project/showcase portability warnings, rejection of invented project/package alias sources, bounded options, secret-like key rejection, deterministic credential-free fingerprint, and no live catalog/global alias-cache use. Extend the installed-wheel test to parse both config forms from a temporary profile/managed config without repository imports.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_workflow_model_resolution.py tests/hermes_cli/test_config_validation.py tests/hermes_cli/test_ollama_cloud_auth.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
```

- [ ] **Step 3: Implement the pure parser/resolver.**

Register optional roots without defaults or config-version bump. Share parsing with interactive direct aliases but return frozen routes from an explicit config snapshot. Evidence gets route fingerprint/trust class, never base URL.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_workflow_model_resolution.py tests/hermes_cli/test_config_validation.py tests/hermes_cli/test_ollama_cloud_auth.py tests/hermes_cli/test_model_switch_configured_provider_routing.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add hermes_cli/workflow_model_resolution.py hermes_cli/model_switch.py hermes_cli/config.py tests/hermes_cli/test_workflow_model_resolution.py tests/hermes_cli/test_config_validation.py tests/hermes_cli/test_ollama_cloud_auth.py tests/plugins/workflow/test_installed_distribution_e2e.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(config): resolve portable workflow model references"
```

### Task 4: Implement dormant normalizer v5 semantics

**Files:**

- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/resources.py`
- Create: `tests/plugins/workflow/test_phase5_language.py`
- Test: `tests/plugins/workflow/test_language_snapshot.py`
- Test: `tests/plugins/workflow/test_language_schema.py`

- [ ] **Step 1: Write explicit-v5 RED tests without activation.**

Prove cumulative v2-v4 semantics; tier/alias reference tagging; bounded canonical hook operations; string/null valid matchers; unsupported hook operations remain obligations; accepted MCP wrappers normalize identically; conflicting wrappers fail; and one semantic mutation changes the digest. Load v1-v4 fixtures and prove unchanged interpretation. Current Archon must remain v4.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_phase4_language.py -q
```

- [ ] **Step 3: Implement v5 as explicitly selectable but dormant.**

Add `supports_phase5_semantics`, exact v5 readers, and cumulative normalization. Add 5 to supported readers but leave current/latest Archon at 4 until Task 15.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_compilation.py -q
git add plugins/workflow/language.py plugins/workflow/language_schema.py plugins/workflow/schema.py plugins/workflow/models.py plugins/workflow/resources.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_language_schema.py
git commit -m "feat(workflow): define dormant phase 5 semantics"
```

### Task 5: Bind one immutable provider authority

**Files:**

- Create: `plugins/workflow/provider_authority.py`
- Modify: `plugins/workflow/runner_binding.py`
- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/trust.py`
- Create: `tests/plugins/workflow/test_phase5_provider_authority.py`
- Test: `tests/plugins/workflow/test_runner_binding.py`
- Test: `tests/plugins/workflow/test_provider_compat.py`
- Test: `tests/plugins/workflow/test_trust_policy.py`

- [ ] **Step 1: Write RED parity/fail-closed tests.**

Resolve workflow, node, inline, and fallback routes from one config snapshot. Assert every accepted field has a decision; legacy capability sets cannot promote budget/sandbox; node precedence is exact; alias changes alter authority/risk/trust identity; normalized-equivalent tool aliases do not; unsupported makes compatibility non-runnable with stable paths/codes.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_runner_binding.py tests/plugins/workflow/test_provider_compat.py tests/plugins/workflow/test_trust_policy.py -q
```

- [ ] **Step 3: Implement authority snapshot and compatibility consumption.**

Extend `ExecutionCapabilityContext`; replace production abstract capability sets with decisions; preserve old helpers only outside v5; fold authority digest/warnings into execution identity and risk-bound trust.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_runner_binding.py tests/plugins/workflow/test_provider_compat.py tests/plugins/workflow/test_compat_matrix.py tests/plugins/workflow/test_trust_policy.py -q
git add plugins/workflow/provider_authority.py plugins/workflow/runner_binding.py plugins/workflow/compat.py plugins/workflow/trust.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_runner_binding.py tests/plugins/workflow/test_provider_compat.py tests/plugins/workflow/test_trust_policy.py
git commit -m "feat(workflow): bind one provider capability authority"
```

### Task 6: Seal and recover the provider authority with snapshot format 2

**Files:**

- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/dependency_manifest.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/scheduled_revalidation.py`
- Create: `tests/plugins/workflow/test_phase5_provider_snapshot.py`
- Test: `tests/plugins/workflow/test_phase4_snapshot.py`
- Test: `tests/plugins/workflow/test_phase4_dependency_manifest.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`

- [ ] **Step 1: Write RED closure and recovery tests.**

Admit explicit-v5 packages into snapshot-format-2 roots and prove `provider-resolution.json` and its digest are exact manifest members. Exercise tamper, omission, extra-member, alias drift, provider-profile drift, scheduled revalidation, crash recovery, and installed-root relocation. Prove v1-v4 snapshots neither require nor reinterpret the file. Extend the installed wheel to admit/resume explicit v5 and resume v1-v4 fixtures offline while current Archon remains v4.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_schedule_revalidation.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
```

- [ ] **Step 3: Implement exact conditional sealing.**

Write canonical authority bytes before the atomic snapshot rename. Extend the exact-member reader only for v5 snapshots; recover exclusively from sealed bytes; compare live config/profile fingerprints during revalidation without replacing recorded execution authority.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_crash_recovery.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
git add plugins/workflow/resources.py plugins/workflow/dependency_manifest.py plugins/workflow/store.py plugins/workflow/admission.py plugins/workflow/scheduled_revalidation.py tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_installed_distribution_e2e.py
git commit -m "feat(workflow): seal provider resolution authority"
```

### Task 7: Make one admission decision feed every backend surface

**Files:**

- Create: `plugins/workflow/admission_service.py`
- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/api_admission.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/gateway_command.py`
- Modify: `plugins/workflow/catalog_api.py`
- Modify: `plugins/workflow/schema_cli.py`
- Create: `tests/plugins/workflow/test_phase5_admission_parity.py`
- Test: `tests/plugins/workflow/test_admission.py`
- Test: `tests/plugins/workflow/test_doctor.py`
- Test: `tests/plugins/workflow/test_api_runtime.py`
- Test: `tests/plugins/workflow/test_catalog_api.py`

- [ ] **Step 1: Write RED parity tests at real entry points.**

Feed the same package/config to CLI run, Gateway command, REST mutation, doctor, admission, catalog, and detail. Assert identical blocking codes/paths and identical bounded capability summaries. Assert unsupported obligations block before trust prompts, run rows, snapshot writes, subprocesses, or provider clients. Preserve existing REST URLs and old-client actions.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_doctor.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_cli.py -q
```

- [ ] **Step 3: Introduce a common admission service.**

Return one immutable result containing compiled workflow, sealed authority, compatibility, diagnostics, actions, and trust/risk inputs. Keep presentation adapters thin. Delete production call paths that can separately recompute provider compatibility.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_doctor.py tests/plugins/workflow/test_api_runtime.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_cli.py tests/plugins/workflow/test_portable_compatibility_e2e.py -q
git add plugins/workflow/admission_service.py plugins/workflow/admission.py plugins/workflow/api_admission.py plugins/workflow/cli.py plugins/workflow/gateway_command.py plugins/workflow/catalog_api.py plugins/workflow/schema_cli.py tests/plugins/workflow/test_phase5_admission_parity.py
git commit -m "refactor(workflow): unify provider-aware admission"
```

### Task 8: Enforce sealed routes, fresh-context boundaries, and exact recovery

**Files:**

- Modify: `plugins/workflow/runtime.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/sessions.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/execution_semantics.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `run_agent.py`
- Modify: `model_tools.py`
- Create: `tests/plugins/workflow/test_phase5_execution_context.py`
- Create: `tests/agent/test_plugin_agent_prefix_identity.py`
- Test: `tests/plugins/workflow/test_persistent_session_recovery.py`
- Test: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED execution-identity tests.**

Prove execution uses sealed provider/model/options, never current aliases. Provider authority changes must change idempotency and intended-authority identity. After MCP discovery and final filtering, compute `model_visible_prefix_digest` over the exact rendered system bytes and complete canonical model-visible tool schemas. Same-name MCP schema/description drift, built-in/plugin tool-schema drift, context-file/memory changes, prompt-contributor changes, and renderer-version changes must select fresh context before transport. Missing expected tools block rather than weaken. Crash recovery resumes only when both digests match. Existing v1-v4 recovery remains byte-compatible.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_execution_context.py tests/agent/test_plugin_agent_prefix_identity.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_schedule_store_identity.py -q
```

- [ ] **Step 3: Thread the sealed authority through runtime objects.**

Implement a two-stage handshake: sealed intended authority from admission, then a worker-computed runtime prefix digest after all private prompt contributors and final tool definitions exist but before session lookup/provider I/O. Persist both with the node session and reuse the exact rendered bytes/schemas for that conversation. Reject drift rather than mutating a cached prefix; preserve strict message alternation and skill injection into the current user turn. Do not add synthetic messages.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_execution_context.py tests/agent/test_plugin_agent_prefix_identity.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_schedule_store_identity.py tests/plugins/workflow/test_typed_publication_recovery.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add plugins/workflow/runtime.py plugins/workflow/coordinator.py plugins/workflow/sessions.py plugins/workflow/executors/ai.py plugins/workflow/execution_semantics.py agent/plugin_agent_worker.py run_agent.py model_tools.py tests/plugins/workflow/test_phase5_execution_context.py tests/agent/test_plugin_agent_prefix_identity.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): enforce sealed provider execution context"
```

### Task 9: Normalize hooks and add a scoped lifecycle seam

**Files:**

- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `hermes_cli/plugins.py`
- Create: `tests/plugins/workflow/test_phase5_hooks.py`
- Modify: `tests/plugins/workflow/test_node_hooks.py`
- Test: `tests/test_transform_tool_result_hook.py`
- Test: `tests/test_transform_llm_output_hook.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED semantic and lifecycle tests.**

Table-drive every documented hook event against exact internal operations. Unsupported events block. Prove deterministic ordering, bounded matchers, scoped registration, exception cleanup, cancellation cleanup, concurrent-run isolation, foreign concurrent registration survival, and absence of direct private-registry mutation. Assert no hook changes the system prompt or inserts a conversation message.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_hooks.py tests/plugins/workflow/test_node_hooks.py tests/test_transform_tool_result_hook.py tests/test_transform_llm_output_hook.py -q
```

- [ ] **Step 3: Implement the smallest generic lifecycle API.**

Expose authenticated/opaque run-scoped registration and teardown in the plugin manager, then consume it from the workflow adapter. Registration/removal occurs under the manager's synchronization boundary and removes only callbacks owned by that token; it never snapshot-clears foreign hooks. Advertise only behaviorally proven hook events; keep arbitrary delegation and shell execution out of the seam.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_hooks.py tests/plugins/workflow/test_node_hooks.py tests/test_transform_tool_result_hook.py tests/test_transform_llm_output_hook.py tests/tools/test_approval_plugin_hooks.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add plugins/workflow/language.py plugins/workflow/executors/ai.py agent/plugin_agent_worker.py hermes_cli/plugins.py tests/plugins/workflow/test_phase5_hooks.py tests/plugins/workflow/test_node_hooks.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(plugins): scope workflow hook lifecycles"
```

### Task 10: Bind MCP executable closure and teardown evidence

**Files:**

- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/dependency_manifest.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `tools/mcp_tool.py`
- Create: `tests/plugins/workflow/test_phase5_mcp.py`
- Modify: `tests/plugins/workflow/test_node_mcp.py`
- Test: `tests/plugins/workflow/test_process_lifecycle_soak.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED package-boundary tests.**

Normalize `mcp_servers`, `mcpServers`, bare-map, and single-server wrappers identically; reject conflicts. For v5 local stdio, prove only the exact Hermes Python interpreter with a sealed script, `-I -S`, and a lifetime import-root guard can run. RED cases cover `python -m`, `python -c`, ambient/transitive `site-packages`, Node/npm/`node_modules`, PATH resolution, interpreter or dependency mutation, registry downloads, standalone binaries, shebang scripts, ELF/Mach-O/PE dynamic dependencies, symlinks, and delayed imports. Canonical remote HTTP/SSE definitions must receive an explicit blocking decision. Exercise startup failure, timeout, cancellation, crash recovery, teardown, no orphan process, and bounded redacted stderr/tool evidence.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_mcp.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_process_lifecycle_soak.py tests/tools/test_mcp_initial_connect_shutdown.py tests/tools/test_mcp_cancelled_error_propagation.py tests/tools/test_mcp_reconnect_log_hygiene.py -q
```

- [ ] **Step 3: Implement canonical closure and owned teardown.**

Resolve MCP from sealed resources only; never export package secrets. Seal interpreter/import-policy identities and enforce permitted import roots for the server lifetime. Recognize but block remote transports until a version-pinned remote-adapter design exists. Route every process handle through the existing managed-process registry and redact potentially secret-bearing stderr/tool payloads before bounded evidence projection. This is dependency immutability, not an OS sandbox.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_mcp.py tests/plugins/workflow/test_node_mcp.py tests/plugins/workflow/test_process_lifecycle_soak.py tests/tools/test_mcp_initial_connect_shutdown.py tests/tools/test_mcp_cancelled_error_propagation.py tests/tools/test_mcp_reconnect_log_hygiene.py tests/tools/test_process_registry.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add plugins/workflow/language.py plugins/workflow/dependency_manifest.py plugins/workflow/executors/ai.py agent/plugin_agent_worker.py tools/mcp_tool.py tests/plugins/workflow/test_phase5_mcp.py tests/plugins/workflow/test_node_mcp.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): seal MCP executable closure"
```

### Task 11: Preserve tool and skill semantics while bounding inline agents

**Files:**

- Modify: `plugins/workflow/compat.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Create: `tests/plugins/workflow/test_phase5_inline_limits.py`
- Test: `tests/plugins/workflow/test_node_agents.py`
- Test: `tests/plugins/workflow/test_node_skills.py`
- Test: `tests/plugins/workflow/test_node_tool_policy.py`
- Test: `tests/agent/test_plugin_agent.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED inheritance and least-authority tests.**

Prove omitted `allowed_tools` differs from exact `allowed_tools: []`; an inline agent declared behind an empty/unreachable tool policy blocks rather than causing auto-addition; denied tools stay denied; skill files are fully loaded from sealed bytes into the current user turn only; and inline workers inherit the parent's remaining provider attempts, absolute wall/provider/idle deadlines, workdir/resource ceilings, and cancellation token. Exercise nested agents, retry, repair, and concurrent cancellation. Shared monetary authority and exactly-once cost settlement are added in Task 12.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_inline_limits.py tests/plugins/workflow/test_node_agents.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_tool_policy.py tests/agent/test_plugin_agent.py -q
```

- [ ] **Step 3: Extend the existing authenticated request authority minimally.**

Remove workflow-side `workflow_agent` auto-addition. Pass the existing attempt authority, absolute deadlines, workdir/resource ceilings, and cancellation through `PluginAgentRunRequest`; do not expose a raw delegation callback or broaden core tool schemas. Keep validation generic and wire-compatible for non-workflow plugin callers.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_inline_limits.py tests/plugins/workflow/test_node_agents.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_node_tool_policy.py tests/agent/test_plugin_agent.py tests/run_agent/test_iteration_budget_race.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add plugins/workflow/compat.py plugins/workflow/executors/ai.py agent/plugin_agent.py agent/plugin_agent_worker.py tests/plugins/workflow/test_phase5_inline_limits.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(agent): share sealed inline-agent limits"
```

### Task 12: Enforce one authoritative settled-cost budget

**Files:**

- Create: `agent/cost_budget.py`
- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `agent/conversation_loop.py`
- Modify: `agent/usage_pricing.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/model-providers/openrouter/__init__.py`
- Create: `tests/agent/test_cost_budget.py`
- Create: `tests/plugins/workflow/test_phase5_cost_budget.py`
- Test: `tests/agent/test_usage_pricing.py`
- Test: `tests/agent/test_provider_attempt_transport.py`
- Test: `tests/agent/test_plugin_agent.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED authority and accounting tests.**

First test the provider-neutral authority with synthetic authoritative settlements: reject estimates, local price tables, arbitrary plugin result keys, NaN/negative/overflow values, and provider-name spoofing. Prove pre-call exhaustion blocks, one authenticated in-flight settlement lease serializes parent/repair/fallback/inline transports, a settled call can produce only one-call overrun, retries never reset cost, duplicate/replayed settlements are idempotent, budget exhaustion is terminal/non-retryable, and cancellation cannot evade or reopen an ambiguous settlement. Separately test route enablement: OpenRouter BYOK is always unsupported, an overriding user plugin cannot claim support, and shared-credit OpenRouter remains unsupported unless an explicit provider contract covers success, stream, charged error, disconnect, timeout, and cancellation settlement.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/agent/test_cost_budget.py tests/plugins/workflow/test_phase5_cost_budget.py tests/agent/test_usage_pricing.py tests/agent/test_provider_attempt_transport.py tests/agent/test_plugin_agent.py -q
```

- [ ] **Step 3: Implement exact bounded-decimal settlement.**

Implement the provider-neutral authenticated authority with an exclusive in-flight lease, atomic pre-call `remaining > 0` checks, and idempotent post-call settlement keyed by attempt identity. Release the lease only after authoritative settlement or proof of no transport/no bill; ambiguous cancellation/timeout poisons the authority terminally. Parse bounded provider decimals without binary float or downward rounding; unsupported precision blocks. Emit only the closed public ledger from the design. Do not persist provider payloads or credentials.

- [ ] **Step 4: Make route enablement a separate evidence gate.**

Evaluate direct shared-credit OpenRouter `usage.cost` as the first candidate. Recorded fixtures prove parsing/poisoning but cannot establish the external billing contract. Enable the route only with an authoritative contractual provider guarantee covering every billable terminal outcome plus adapter-path tests, or with authoritative per-attempt reconciliation before lease release. Current documentation is insufficient, so the activation expectation is `authoritative_cost_unavailable` unless one of those gates is newly satisfied. BYOK stays unsupported. If enabled, run the route adapter through retry, structured repair, and inline paths and assert one in-flight call may settle beyond the ceiling, no later provider call starts, status is `budget_exhausted`, retry count does not advance, and bounded evidence totals equal settlements exactly once.

- [ ] **Step 5: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/agent/test_cost_budget.py tests/plugins/workflow/test_phase5_cost_budget.py tests/agent/test_usage_pricing.py tests/agent/test_provider_attempt_transport.py tests/agent/test_plugin_agent.py tests/plugins/workflow/test_ai_e2e.py tests/plugins/workflow/test_retry.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add agent/cost_budget.py agent/plugin_agent.py agent/plugin_agent_worker.py agent/conversation_loop.py agent/usage_pricing.py plugins/workflow/executors/ai.py plugins/model-providers/openrouter/__init__.py tests/agent/test_cost_budget.py tests/plugins/workflow/test_phase5_cost_budget.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(agent): enforce shared authoritative cost budgets"
```

### Task 13: Encode provider options truthfully and block native sandbox claims

**Files:**

- Modify: `plugins/workflow/executors/ai.py`
- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `hermes_cli/provider_capabilities.py`
- Modify: `agent/chat_completion_helpers.py`
- Modify: `plugins/model-providers/openrouter/__init__.py`
- Create: `tests/plugins/workflow/test_phase5_provider_options.py`
- Test: `tests/run_agent/test_fallback_reasoning_override.py`
- Test: `tests/plugins/web/test_web_search_provider_plugins.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED per-option transport tests.**

For effort/thinking, fallback, web execution, and provider-native sandbox, prove each accepted decision reaches the exact provider request with its effective semantics. Missing encoders block. Fallback uses its own sealed provider/model/options and a fresh context while sharing cost/attempt/deadline/cancellation limits. Every current sandbox request blocks with a stable code and an `execution_environment: isolated_backend_required` recommendation; resource ceilings are never described as sandbox enforcement.

- [ ] **Step 2: Run RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_provider_options.py tests/run_agent/test_fallback_reasoning_override.py tests/run_agent/test_conversation_fallback_state.py tests/plugins/web/test_web_search_provider_plugins.py tests/plugins/workflow/test_security_boundaries.py -q
```

- [ ] **Step 3: Add only proven encoders.**

Map sealed effective options at the transport boundary. Do not infer support from OpenAI compatibility or hostnames. Leave unsupported routes blocking and add no OS sandbox. Make the fallback context boundary explicit rather than mutating the primary conversation.

- [ ] **Step 4: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_provider_options.py tests/run_agent/test_fallback_reasoning_override.py tests/run_agent/test_conversation_fallback_state.py tests/plugins/web/test_web_search_provider_plugins.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_provider_failures.py -q
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add plugins/workflow/executors/ai.py agent/plugin_agent.py agent/plugin_agent_worker.py agent/chat_completion_helpers.py hermes_cli/provider_capabilities.py plugins/model-providers/openrouter/__init__.py tests/plugins/workflow/test_phase5_provider_options.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): enforce provider option capabilities"
```

### Task 14: Project backend authority to evidence, catalog, REST, and Desktop

**Files:**

- Modify: `plugins/workflow/evidence.py`
- Modify: `plugins/workflow/catalog_api.py`
- Modify: `plugins/workflow/actions.py`
- Modify: `plugins/workflow/sanitize.py`
- Modify: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/app/workflows/catalog.tsx`
- Modify: `apps/desktop/src/app/workflows/catalog-run-policy.ts`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.tsx`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ar.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`
- Create: `tests/plugins/workflow/test_phase5_surfaces.py`
- Test: `tests/plugins/workflow/test_evidence_api.py`
- Test: `tests/plugins/workflow/test_notifications.py`
- Test: `tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py`
- Modify: `apps/desktop/src/app/workflows/catalog-run-policy.test.ts`
- Modify: `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/review-run-dialog.test.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/i18n/languages.test.ts`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Test: `tests/scripts/test_check_upstream_customizations.py`

- [ ] **Step 1: Write RED closed-projection and client-skew tests.**

Assert evidence/catalog/detail/doctor/action payloads contain only bounded enum codes, feature disposition, safe provider/model display identifiers, effective-option summaries, authority digest, and closed budget totals. Attempt prompts, commands, credentials, provider responses, feedback, temporary-root paths, base URLs, MCP stderr, arbitrary nested accounting fields, URI/userinfo/query/fragment strings, absolute/traversal paths, control characters, pasted credential forms, and high-entropy/overlong literal model IDs; all must be absent or replaced wholesale with a stable redacted digest label. An old Desktop client must preserve old action vocabulary and block safely on unknown backend codes without locally re-resolving a model or capability.

- [ ] **Step 2: Run Python RED.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_surfaces.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py -q
```

- [ ] **Step 3: Run Desktop RED.**

```bash
cd apps/desktop
npx vitest run src/app/workflows/catalog-run-policy.test.ts
npx vitest run src/app/workflows/view-workflow-dialog.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/index.test.tsx src/i18n/languages.test.ts
npx tsc --noEmit
```

- [ ] **Step 4: Implement backend-authored projections and display-only Desktop use.**

Add closed serializers and a separate display-identifier redactor adjacent to the owning backend types. Desktop renders resolutions/diagnostics/actions but contains no provider matrix, tier/alias resolver, or capability inference. The review-run dialog validates authoritative detail immediately before submission; malformed/missing/unknown Phase 5 authority produces bounded localized copy and no mutation POST. Keep existing mutation URLs and legacy action names.

- [ ] **Step 5: Run GREEN and commit.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_surfaces.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_workflow_detail_api.py tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py -q
cd apps/desktop
npx vitest run src/app/workflows/catalog-run-policy.test.ts src/app/workflows/view-workflow-dialog.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/index.test.tsx src/i18n/languages.test.ts
npx tsc --noEmit
cd ../../
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
git add plugins/workflow/evidence.py plugins/workflow/catalog_api.py plugins/workflow/actions.py plugins/workflow/sanitize.py plugins/workflow/notifications.py plugins/workflow/dashboard/plugin_api.py apps/desktop/src/types/hermes.ts apps/desktop/src/app/workflows/catalog.tsx apps/desktop/src/app/workflows/catalog-run-policy.ts apps/desktop/src/app/workflows/catalog-run-policy.test.ts apps/desktop/src/app/workflows/view-workflow-dialog.tsx apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx apps/desktop/src/app/workflows/review-run-dialog.tsx apps/desktop/src/app/workflows/review-run-dialog.test.tsx apps/desktop/src/app/workflows/run-inspector.tsx apps/desktop/src/app/workflows/index.test.tsx apps/desktop/src/i18n/types.ts apps/desktop/src/i18n/en.ts apps/desktop/src/i18n/ar.ts apps/desktop/src/i18n/ja.ts apps/desktop/src/i18n/zh.ts apps/desktop/src/i18n/zh-hant.ts apps/desktop/src/i18n/languages.test.ts tests/plugins/workflow/test_phase5_surfaces.py
git add docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "feat(workflow): project provider authority to clients"
```

### Task 15: Document, verify, then atomically activate normalizer v5

**Files:**

- Modify: `website/docs/user-guide/features/workflow-yaml-reference.md`
- Modify: `website/docs/user-guide/features/workflows.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`
- Modify: `plugins/workflow/language.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `tests/plugins/workflow/test_phase5_language.py`
- Modify: `tests/plugins/workflow/test_language_snapshot.py`
- Modify: `tests/plugins/workflow/test_admission.py`
- Modify: `tests/plugins/workflow/test_phase4_language.py`
- Create: `docs/reviews/2026-08-06-workflow-language-phase-5-validation.md`

- [ ] **Step 1: Update documentation and reconcile customization entries before activation.**

Document config-only tiers/aliases, exact conflict precedence, all four dispositions, blocking semantics, supported hook/MCP shapes, `allowed_tools: []`, fresh-context boundaries, settled-call budget semantics and possible one-call overrun, current lack of native sandbox support, and the isolated-backend recommendation. Audit that every generic upstream-owned commit already contains its own symbol-level ledger entry and invariant tests; add no retroactive catch-all entry and do not advance `last_verified_upstream`.

- [ ] **Step 2: Run focused Phase 5 and v1-v4 compatibility gates without retries.**

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/hermes_cli/test_provider_capabilities.py tests/hermes_cli/test_workflow_model_resolution.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_phase5_execution_context.py tests/plugins/workflow/test_phase5_hooks.py tests/plugins/workflow/test_phase5_mcp.py tests/plugins/workflow/test_phase5_inline_limits.py tests/plugins/workflow/test_phase5_cost_budget.py tests/plugins/workflow/test_phase5_provider_options.py tests/plugins/workflow/test_phase5_surfaces.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_defensive_invariants.py -q
```

- [ ] **Step 3: Run installed-distribution, Desktop, ledger, and upstream-rehearsal gates.**

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
cd apps/desktop
npm run typecheck
npm test
npm run lint
cd ../../
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py tests/scripts/test_workflow_upstream_merge.py tests/test_desktop_workflow_test_gate.py -q
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_upstream_merge.sh --phase base
```

- [ ] **Step 4: Run full base and branded-release regression gates without publishing.**

Before the rehearsals, enumerate every production descriptor from
`brands/*.json`, excluding schema/fixture files, and validate each slug through
`scripts/brand/descriptor.mjs`; do not hardcode OTTO/LOOP24. Capture local and
remote refs, `git worktree list --porcelain`, branch/status, tags, each
descriptor's release-repository refs, and `gh release list` metadata in a
private temporary directory. Re-capture and byte-compare them after every
brand. Any mutation is a blocking gate failure.

```bash
PHASE5_AUDIT_DIR="$(mktemp -d)"
trap 'if test -n "${PHASE5_AUDIT_DIR:-}" && test -d "$PHASE5_AUDIT_DIR"; then rm -rf -- "$PHASE5_AUDIT_DIR"; fi' EXIT
phase5_snapshot_external_state() {
  local snapshot_label="$1"
  local source_remote source_remote_url
  mkdir -p "$PHASE5_AUDIT_DIR/$snapshot_label"
  git worktree list --porcelain > "$PHASE5_AUDIT_DIR/$snapshot_label/worktrees"
  git branch --show-current > "$PHASE5_AUDIT_DIR/$snapshot_label/branch"
  git status --porcelain=v2 --branch > "$PHASE5_AUDIT_DIR/$snapshot_label/status"
  git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/remotes refs/tags > "$PHASE5_AUDIT_DIR/$snapshot_label/local-refs"
  git remote -v > "$PHASE5_AUDIT_DIR/$snapshot_label/source-remotes"
  for source_remote in $(git remote); do
    source_remote_url="$(git remote get-url "$source_remote")"
    git ls-remote "$source_remote_url" > "$PHASE5_AUDIT_DIR/$snapshot_label/source-$source_remote-refs"
  done
  while IFS=$'\t' read -r brand_slug releases_repo; do
    git ls-remote "https://github.com/$releases_repo.git" > "$PHASE5_AUDIT_DIR/$snapshot_label/$brand_slug-remote-refs"
    gh release list -R "$releases_repo" --limit 200 --json tagName,name,publishedAt,isDraft,isPrerelease > "$PHASE5_AUDIT_DIR/$snapshot_label/$brand_slug-releases.json"
  done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json")) { const slug=file.slice(0,-5); const d=loadDescriptor(slug,{root:process.cwd()}); console.log(`${slug}\t${d.releasesRepo}`); }')
}
phase5_snapshot_external_state before
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh
PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_merge_gate.sh --phase base
PHASE5_TESTED_SHA="$(git rev-parse HEAD)"
PHASE5_BRANDS="$(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json")) { const slug=file.slice(0,-5); loadDescriptor(slug,{root:process.cwd()}); console.log(slug); }')"
for brand_slug in $PHASE5_BRANDS; do
  PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/test_workflow_merge_gate.sh --phase brand --brand "$brand_slug" --tested-base-sha "$PHASE5_TESTED_SHA"
done
phase5_snapshot_external_state after
diff -ru "$PHASE5_AUDIT_DIR/before" "$PHASE5_AUDIT_DIR/after"
git diff --check
```

The descriptor loader output supplies each `releasesRepo` for the pre/post
`git ls-remote` and `gh release list --json tagName,name,publishedAt,isDraft,isPrerelease`
snapshots. The brand commands are read-only regression rehearsals in temporary
worktrees. They must not push, tag, publish, mutate releases, or leave the
implementation checkout off its feature branch. Record exact counts, duration,
retry count, pre/post comparison digests, and any justified exclusions in the
validation report.

- [ ] **Step 5: Obtain implementation review and close all Critical/Important findings.**

Independently review premise/intent, resolver exhaustiveness, sealed closure, budget authority, hook/MCP teardown, cache identity, redaction, recovery, client skew, and scope. Reproduce every finding, add a RED regression, make the smallest generic fix, rerun its focused gate, and record disposition. No Critical or Important finding may remain open.

- [ ] **Step 6: Add the activation RED test.**

Change current-version and installed-wheel assertions to require v5 for newly admitted `archon-2026-07` workflows while retaining legacy v2 and explicit/sealed v1-v4 behavior. Run the language/snapshot/admission suite and require failure only because current Archon still selects v4.

```bash
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_phase4_language.py -q
```

- [ ] **Step 7: Activate v5 with the single current-version mapping change.**

Set `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` from 4 to 5. Do not change legacy selection, snapshot format, or v1-v4 readers. Run the Step 6 language command GREEN. Do not run post-activation merge/brand rehearsals from the dirty checkout; Step 8 first commits the candidate.

- [ ] **Step 8: Commit by concern, then run every post-activation gate against that exact HEAD.**

```bash
git add website/docs/user-guide/features/workflow-yaml-reference.md website/docs/user-guide/features/workflows.md docs/upstream-customizations/workflow-orchestration.yaml docs/reviews/2026-08-06-workflow-language-phase-5-validation.md
git commit -m "docs(workflow): document provider portability"
git add plugins/workflow/language.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_installed_distribution_e2e.py
git commit -m "feat(workflow): activate phase 5 provider portability"
```

Rerun Steps 2, 3, and 4 from the clean committed checkout. `PHASE5_TESTED_SHA`
must equal the activation commit returned by `git rev-parse HEAD`; every base
and brand rehearsal uses that SHA. If a gate fails, activation is incomplete:
add a focused RED regression, commit the smallest fix, and rerun all three
steps against the new exact HEAD.

- [ ] **Step 9: Record post-activation evidence and verify the final checkout.**

Append exact post-activation commands, counts, durations, retry count, commit IDs, review dispositions, installed-distribution result, and rehearsal exclusions to the validation report. Do not claim merge/release activity.

```bash
git add docs/reviews/2026-08-06-workflow-language-phase-5-validation.md
git commit -m "docs(workflow): record phase 5 activation evidence"
git diff --check
git status --short
git branch --show-current
```

Expected handoff: clean `feat/workflow-language-phase-5-provider-portability`; new Archon admissions on v5; legacy on v2; sealed v1-v4 recover unchanged; no unresolved Critical/Important findings; no push, merge, tag, release, or brand mutation.

---

## Proposed task sequence and ownership

| Sequence | Ownership | Tasks | Dependency |
|---|---|---|---|
| 0 | Integration owner | 0 | approved reviewed plan |
| 1 | Provider/config owner | 1-3 | green baseline |
| 2 | Language/authority owner | 4-7 | reviewed provider interfaces |
| 3 | Runtime/recovery owner | 8 | sealed authority |
| 4 | Extension-runtime owner | 9-11 | v5 normalization and execution context |
| 5 | Accounting/provider owner | 12-13 | shared authority and proven transports |
| 6 | Backend/Desktop owner | 14 | stable backend projections |
| 7 | Integration owner | 15 | every prior task and review green |

One owner completes and commits each task before the dependent owner begins. Parallel work is safe only within independent test research; shared authority/runtime files must be serialized. No concurrent implementation agents should edit the same worktree.

## Plan self-review checklist

- Every accepted provider-dependent feature receives exactly one central disposition, and unsupported always blocks before side effects.
- Model tiers, aliases, and literals resolve from an explicit config snapshot to one sealed concrete route; Desktop never resolves them.
- Provider declarations carry loader-owned complete code-closure provenance;
  unhashable or user-plugin security claims cannot become native billing/sandbox.
- Normalizer v5 is dormant through all implementation and verification tasks; Task 15 is the sole activation boundary.
- Snapshot format 2 remains exact and preserves v1-v4 recovery behavior.
- Hooks and MCP are canonical, package-bound, bounded, isolated through existing machinery, and deterministically torn down.
- Skills remain complete current-user-turn content; `allowed_tools: []` remains no callable tools; inline agents inherit all remaining authorities.
- Hard cost support is restricted to authoritative billed-cost routes and transparently permits only the already-started call to overrun.
- No provider-native sandbox is claimed without a code-owned guarantee; resource limits are not security boundaries.
- Cache-changing authority starts fresh context; no system prompt or historical message is mutated.
- Session reuse also requires the runtime digest of exact rendered system bytes
  and complete final model-visible tool schemas, not merely tool names.
- Public evidence and clients receive only closed backend projections with stable actions and redaction.
- Every production task starts with an exact RED command, ends with GREEN verification, and has an atomic commit.
- Compatibility, installed distribution, Desktop, ledger, upstream rehearsal, full base, and non-publishing brand regressions are mandatory gates.
