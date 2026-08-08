# Workflow Language Phase 5: Adversarial Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Implementation authorized; review-driven transaction addendum approved and pending execution

**Goal:** Clear the Phase 5 adversarial BLOCK verdict by restoring truthful shared-context reuse, binding structured repair to the admitted route, enforcing deterministic provider-name precedence, detecting same-trust-class endpoint drift before any runtime side effect, and making sealed credential adoption transactionally consistent with retry ownership.

**Architecture:** Keep node-bound execution authority separate from cross-node cache compatibility; derive both from the same sealed Phase 5 package. Advance only the internal v5 provider-authority schema to version 2 and add a credential-free normalized endpoint digest to one centralized six-field runtime identity. A sealed AIAgent retains one immutable credential candidate for one bounded immediate re-adoption, publishes the concrete client and mode-specific execution state atomically, and updates conversation-owned retry state only after success. Make structured repair a deliberately narrowed copy of the admitted primary request, and make provider canonical names and aliases participate in one deterministic namespace. Preserve normalizer v5, snapshot format 2, every v1-v4 behavior, and every public wire contract.

**Tech Stack:** Python 3.11+, frozen dataclasses, canonical JSON and SHA-256 identities, immutable workflow snapshots, SQLite/JSONL workflow state, Pytest through `scripts/run_tests.sh`, Electron React/TypeScript regression gates, and YAML customization ledgers.

## Authority and Baseline

- Approved design: `docs/superpowers/specs/2026-08-08-workflow-language-phase-5-adversarial-remediation-design.md`; original approval commit `6dd25e19c0ab13cb67ac1442b13e972d652b70e6`, review-driven sealed-credential transaction approval commit `f620651d09686880cb1102b31d2816d0b03de4ff`.
- Product-code candidate under review: `1373c306061d2f4a2cf1dd313df16f6453fa1939`.
- Adversarial verdict: `docs/reviews/2026-08-07-workflow-language-phase-5-adversarial-review-fable.md` is BLOCK with confirmed F-1 through F-4.
- Execute only in `.worktrees/workflow-language-phase-5-provider-portability` on `feat/workflow-language-phase-5-provider-portability`.
- The development main is `base`; literal `main` is synchronization-only and is never an execution or merge target.
- Do not clean, edit, stage, or commit either preserved untracked Phase 5 review document unless the user separately authorizes publishing those exact files.
- Do not touch `.worktrees/workflow-language-phase-4-ordinary-loops-immutable-includes` or its preserved review documents.
- Do not rebase, push, merge, tag, publish, create releases, or modify `base`, `otto`, `loop24`, literal `main`, or any brand/release repository under this plan.

## Global Invariants

- New Archon workflows remain normalizer v5; legacy remains v2; already admitted v1-v4 snapshots retain their recorded behavior.
- Snapshot format remains 2. Only the internal v5 provider-authority schema and matching resolver/identity version advance from 1 to 2.
- Unmerged v5 provider-authority schema 1 fails closed and requires re-admission. It is never completed from live configuration.
- Provider/model/options resolve once from one immutable config snapshot. Runtime may reload credentials, but any credential-free route drift blocks before MCP startup, agent construction, provider transport, trust mutation, or evidence that implies execution.
- The full sealed closure remains the unit of identity, trust, risk, recovery, and scheduled revalidation.
- The system prompt, complete model-visible prefix, tool schema, hooks, MCP, skills, and inline-agent configuration stay byte-stable within a conversation. Identity changes start fresh context.
- Structured repair, retry, fallback, approval, and inline agents share the parent attempt, time, cost, resource, workdir, cancellation, and sandbox limits; none may mint authority.
- `allowed_tools: []` remains exact. Skills are fully read and added only to the current user turn. No system-prompt mutation, synthetic conversation message, raw delegation, core model tool, telemetry, new OS sandbox, or user-facing nonsecret `HERMES_*` variable is introduced.
- Public diagnostics/evidence contain bounded logical identifiers and digests only—never prompts, commands, secret values, credentials, provider payloads, feedback, temporary-root absolute paths, or unnecessary local paths.
- REST mutation URLs, old-client action vocabulary, Desktop backend-authority boundaries, and existing branded copy remain unchanged.
- Every generic upstream-owned change is generic, invariant-tested, and recorded in `docs/upstream-customizations/workflow-orchestration.yaml` in the same task commit. Do not advance `last_verified_upstream`.
- Run Python tests only through `scripts/run_tests.sh`, always with `HERMES_TEST_FILE_RETRIES=0`; never call Pytest directly.
- Keep commits atomic. Every task begins with a failing behavior test, reaches GREEN, runs focused regressions plus `git diff --check`, and commits only its owned files.

## Execution Roles and Ownership

The recommended execution mode is subagent-driven development with serial implementation ownership. Concurrent implementation agents are prohibited because Tasks 1–3 overlap the request/identity path.

| Role | Ownership | Restriction |
| --- | --- | --- |
| Implementer A | Task 1 runtime identity and provider-authority schema | Must not begin Task 2 or change shared-context semantics |
| Implementer A1 | Task 1A sealed Vertex/pool transaction and turn ownership | Starts only after Task 1 review findings are reproduced; must not change Anthropic or Task 2 |
| Implementer A2 | Task 1B sealed Anthropic transaction | Starts only after Task 1A independently reviews clean |
| Implementer B | Task 2 structured-repair request derivation | Starts only after Task 1 is committed |
| Implementer C | Task 3 shared-context compatibility and durable handoff | Starts only after Task 2 is committed |
| Implementer D | Task 4 provider registry token precedence | May begin only after Task 3 unless the primary agent explicitly proves a disjoint worktree; default is serial |
| Primary integrator | Tasks 5–6 documentation, full gates, history/state audit | Does not waive RED/GREEN requirements |
| Independent reviewers | Specification compliance, code quality/security, and final adversarial replay | Must not be the implementer whose work they review |

Every worker must be told that they are not alone in the codebase, must preserve all user changes, and must not revert or rewrite another worker's edits. A worker owns only the files named in its task until handoff.

## Test Command Prefix

Use this exact prefix for every Python gate:

```bash
HERMES_TEST_FILE_RETRIES=0 \
HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python \
scripts/run_tests.sh
```

When the plan shows `scripts/run_tests.sh ...`, prepend the two environment assignments above. Tests must never be silently retried.

---

### Task 0: Reconfirm the immutable execution baseline

**Files:** None

**Interfaces:** None; this is a read-only safety gate.

- [ ] **Step 1: Prove the checkout and ancestry before editing.**

  Run:

  ```bash
  pwd
  git branch --show-current
  git rev-parse HEAD
  git merge-base --is-ancestor 1373c306061d2f4a2cf1dd313df16f6453fa1939 HEAD
  git status --short
  git worktree list --porcelain
  git rev-parse base origin/base origin/otto origin/loop24
  ```

  Expected:

  - path ends with `.worktrees/workflow-language-phase-5-provider-portability`;
  - branch is `feat/workflow-language-phase-5-provider-portability`;
  - HEAD contains approved design commit `6dd25e19...`;
  - candidate ancestry command exits 0;
  - only the two preserved Phase 5 review files are untracked;
  - the root and Phase 4 worktrees remain distinct and untouched.

- [ ] **Step 2: Record preservation hashes outside the implementation diff.**

  Run:

  ```bash
  shasum -a 256 \
    docs/reviews/2026-08-07-workflow-language-phase-5-adversarial-review-fable.md \
    docs/reviews/2026-08-07-workflow-language-phase-5-adversarial-review-prompt.md
  git -C ../workflow-language-phase-4-ordinary-loops-immutable-includes status --short
  shasum -a 256 \
    ../workflow-language-phase-4-ordinary-loops-immutable-includes/docs/reviews/2026-08-06-workflow-language-phase-4-adversarial-review-fable.md \
    ../workflow-language-phase-4-ordinary-loops-immutable-includes/docs/reviews/2026-08-06-workflow-language-phase-4-adversarial-review-prompt.md
  ```

  Save the four hashes in the execution handoff, not in production code.

- [ ] **Step 3: Re-run the known-green focused baseline with retries disabled.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_ai_executor.py \
    tests/hermes_cli/test_provider_profile_precedence.py \
    tests/agent/test_plugin_agent_prefix_identity.py -q
  ```

  Expected: the pre-remediation suite is green. A failure here is a baseline change; stop, diagnose, and do not edit around it.

---

### Task 1: Centralize endpoint-bound runtime identity and atomically advance v5 provider authority

**Finding:** F-4

**Files:**

- Modify: `hermes_cli/runtime_provider.py`
- Modify: `hermes_cli/workflow_model_resolution.py`
- Modify: `plugins/workflow/provider_authority.py`
- Modify: `agent/plugin_agent.py`
- Modify: `agent/plugin_agent_worker.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/executors/approval.py`
- Modify: `tests/hermes_cli/test_runtime_provider_resolution.py`
- Modify: `tests/hermes_cli/test_workflow_model_resolution.py`
- Modify: `tests/agent/test_plugin_agent_prefix_identity.py`
- Modify: `tests/plugins/workflow/test_phase5_provider_snapshot.py`
- Modify: `tests/plugins/workflow/test_phase5_provider_authority.py`
- Modify: `tests/plugins/workflow/test_phase5_execution_context.py`
- Modify: `tests/plugins/workflow/test_phase5_provider_options.py`
- Modify: `tests/plugins/workflow/test_approval.py`
- Modify: `tests/plugins/workflow/test_phase5_inline_limits.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Exact interfaces:**

```python
@dataclass(frozen=True)
class ExecutionRuntimeIdentity:
    provider: str
    model: str
    api_mode: str
    base_url_trust_class: str
    endpoint_sha256: str
    registration_provenance_digest: str

    def to_dict(self) -> dict[str, str]: ...


def execution_runtime_identity(
    capabilities: ExecutionRuntimeCapabilities,
) -> ExecutionRuntimeIdentity: ...


def execution_runtime_identity_from_sealed_route(
    route: Mapping[str, object],
) -> ExecutionRuntimeIdentity: ...


@dataclass(frozen=True, slots=True)
class _ExecutionEndpointIdentity:
    endpoint_sha256: str
    error_code: str | None


_ENDPOINTLESS_EXECUTION_API_MODES = frozenset({"codex_app_server"})
```

`ExecutionRuntimeCapabilities` gains `endpoint_sha256: str = field(default="", compare=False)` and `endpoint_identity_error: str | None = field(default=None, compare=False)` so legacy capability equality remains unchanged. Define `WORKFLOW_MODEL_RESOLVER_VERSION = 2` in `workflow_model_resolution.py`, import it into provider authority, and set both `_AUTHORITY_SCHEMA_VERSION = 2` and `_AUTHORITY_RESOLVER_VERSION = WORKFLOW_MODEL_RESOLVER_VERSION`. Resolve the effective endpoint in this order: explicit resolved runtime route, provider-profile default, then the code-owned endpointless API-mode set. The set initially contains only `codex_app_server`; adding any mode requires an invariant test proving its provider contract has no HTTP endpoint. Malformed, unsupported, ambiguous, or otherwise unresolvable routes return an empty digest plus a stable error and block authority creation; they never become the sentinel. The endpoint digest is domain-separated SHA-256 over the credential-free normalized structural URL returned by the existing `_credential_free_route_url()` policy. The wire reader accepts exactly the six fields above—no missing or extra keys.

Set `_AUTHORITY_SCHEMA_VERSION = 2` and advance its matching resolver/identity version constants together. Every schema-2 sealed primary, fallback, and inline route carries `endpoint_sha256`. V5 schema 1 is rejected; v1-v4 snapshots bypass this v5 authority reader exactly as before.

- [ ] **Step 1: Add RED normalization and exact-codec tests.**

  Add table-driven tests proving:

  - equivalent normalized endpoints (case/default port/trailing-slash forms already treated as equivalent by `_credential_free_route_url`) yield the same digest;
  - distinct path, host, scheme, or nondefault-port routes yield distinct digests;
  - approved structural-query value changes yield distinct digests while credential-query value changes retain key-only presence semantics;
  - unclassified query parameters, malformed ports/URLs, unsupported schemes, and unresolvable provider routes produce a stable error and block rather than using the no-endpoint sentinel;
  - provider-profile default endpoints produce the same identity at admission and worker runtime when `base_url` is omitted;
  - a provider contract that genuinely has no endpoint receives the versioned sentinel, and a later concrete endpoint does not compare equal;
  - credential-bearing query values never enter the normalized material, error text, or public serialization;
  - `ExecutionRuntimeIdentity.to_dict()` has exactly six keys;
  - the reader rejects missing, empty, nonstring, or additional fields.

  ```python
  def test_runtime_identity_accepts_normalization_equivalent_endpoint():
      left = resolve_execution_runtime_capabilities(base_url="https://EXAMPLE.test:443/v1/")
      right = resolve_execution_runtime_capabilities(base_url="https://example.test/v1")
      assert execution_runtime_identity(left) == execution_runtime_identity(right)
  ```

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/hermes_cli/test_runtime_provider_resolution.py \
    tests/agent/test_plugin_agent_prefix_identity.py -q
  ```

  Expected: FAIL because endpoint identity and the six-field codec do not exist.

- [ ] **Step 2: Implement the centralized credential-free identity.**

  Extend the existing runtime-provider resolver; do not create a second URL normalizer. Hash only the normalized structural URL and never store or return that URL from the identity object.

  ```python
  _EXECUTION_ENDPOINT_IDENTITY_DOMAIN = b"hermes-execution-endpoint-v1\0"
  _NO_EXECUTION_ENDPOINT_SENTINEL = b"no-endpoint-v1"
  endpoint_material = (
      normalized_endpoint.encode("utf-8")
      if normalized_endpoint is not None
      else _NO_EXECUTION_ENDPOINT_SENTINEL
  )
  endpoint_sha256 = hashlib.sha256(
      _EXECUTION_ENDPOINT_IDENTITY_DOMAIN + endpoint_material
  ).hexdigest()
  ```

  Resolve provider-profile defaults before this helper. Call the sentinel branch only when the sealed API mode is in `_ENDPOINTLESS_EXECUTION_API_MODES`; an empty normalization result for every other mode is `provider_endpoint_identity_invalid`. Preserve the existing fail-closed unclassified-query behavior. Replace the route fingerprint's raw `base_url_sha256` material with the endpoint digest; do not retain a parallel raw-URL hash that can encode credentials or spelling differences.

  Use one `ExecutionRuntimeIdentity` builder for direct execution, approval, primary route, fallback route, and inline-agent route. Remove handwritten production dict literals for `expected_runtime_identity`.

  Run:

  ```bash
  rg -n '"expected_runtime_identity"\s*:\s*\{' \
    plugins/workflow agent hermes_cli
  ```

  Expected: no handwritten production identity object remains; only codec tests may construct literals.

- [ ] **Step 3: Add RED provider-authority schema-2 tests.**

  Test all of these boundaries:

  - new v5 authority writes schema 2 and includes endpoint digest on primary/fallback/inline routes;
  - tampering one endpoint digest breaks provider-authority integrity;
  - deleting the digest blocks rather than live-completing it;
  - an unmerged v5 schema-1 record returns the stable version/integrity diagnostic and requires re-admission;
  - v1-v4 snapshot fixtures are byte-identical and still recover;
  - snapshot format remains 2.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_provider_snapshot.py \
    tests/plugins/workflow/test_phase5_provider_authority.py \
    tests/hermes_cli/test_workflow_model_resolution.py -q
  ```

  Expected: FAIL because the authority schema is still 1 and sealed routes omit the endpoint digest.

- [ ] **Step 4: Upgrade the authority writer and reader as one atomic unit.**

  Change `WORKFLOW_MODEL_RESOLVER_VERSION`, `_AUTHORITY_SCHEMA_VERSION`, and `_AUTHORITY_RESOLVER_VERSION` together. The schema-2 reader must validate exact shape, digest syntax, route coverage, and authority integrity before exposing any route. Do not add a compatibility upgrader for v5 schema 1.

  Assert the sealed route and centralized runtime builder agree:

  ```python
  expected = execution_runtime_identity_from_sealed_route(sealed_route)
  assert expected.to_dict() == request.expected_runtime_identity
  ```

- [ ] **Step 5: Add the RED pre-transport same-trust drift reproduction.**

  Write an end-to-end worker test that admits `https://endpoint-a.test/v1`, changes live config to `https://endpoint-b.test/v1` with the same trust class, and spies on MCP startup, `AIAgent` construction, and provider transport.

  ```python
  def test_phase5_worker_blocks_same_trust_endpoint_drift_before_agent_construction(...):
      result = run_worker(admitted_endpoint="https://a.test/v1",
                          runtime_endpoint="https://b.test/v1")
      assert result.code == "provider_capability_drift"
      assert not mcp_started
      assert not agent_constructed
      assert not provider_called
  ```

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/agent/test_plugin_agent_prefix_identity.py \
    tests/plugins/workflow/test_phase5_execution_context.py -q
  ```

  Expected: FAIL because the current five-field comparison accepts same-trust-class endpoint drift.

- [ ] **Step 6: Enforce exact runtime identity before every side effect.**

  In `plugin_agent_worker.py`, derive the live six-field identity once and compare the exact object to the request identity before MCP launch, extension setup, agent construction, provider call, or fallback. Keep the existing diagnostic bounded: report only the stable failure code and mismatched field names. Never report endpoint text, endpoint digests, registration digests, credentials, or identity values.

  Apply the same builder to primary, fallback, inline-agent, and approval requests. Preserve the existing selector-versus-effective-provider fix and the trusted-direct gate.

- [ ] **Step 7: Prove installed-distribution and historical compatibility.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/hermes_cli/test_runtime_provider_resolution.py \
    tests/hermes_cli/test_workflow_model_resolution.py \
    tests/agent/test_plugin_agent_prefix_identity.py \
    tests/plugins/workflow/test_phase5_provider_snapshot.py \
    tests/plugins/workflow/test_phase5_provider_authority.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_phase5_provider_options.py \
    tests/plugins/workflow/test_approval.py \
    tests/plugins/workflow/test_phase5_inline_limits.py \
    tests/plugins/workflow/test_language_snapshot.py \
    tests/plugins/workflow/test_phase4_snapshot.py \
    tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
  ```

  Expected: PASS with v1-v4 snapshot hashes unchanged, v5 schema-1 fail-closed coverage, and installed-wheel imports using the same identity code.

- [ ] **Step 8: Ledger and commit the atomic identity change.**

  Update the existing runtime-provider/plugin-agent/workflow entries in `workflow-orchestration.yaml`; do not add a Phase-5-specific special-case entry where a generic entry already exists.

  Run:

  ```bash
  scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
  git diff --check
  git status --short
  git add \
    hermes_cli/runtime_provider.py \
    hermes_cli/workflow_model_resolution.py \
    plugins/workflow/provider_authority.py \
    agent/plugin_agent.py \
    agent/plugin_agent_worker.py \
    plugins/workflow/executors/ai.py \
    plugins/workflow/executors/approval.py \
    tests/hermes_cli/test_runtime_provider_resolution.py \
    tests/hermes_cli/test_workflow_model_resolution.py \
    tests/agent/test_plugin_agent_prefix_identity.py \
    tests/plugins/workflow/test_phase5_provider_snapshot.py \
    tests/plugins/workflow/test_phase5_provider_authority.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_phase5_provider_options.py \
    tests/plugins/workflow/test_approval.py \
    tests/plugins/workflow/test_phase5_inline_limits.py \
    tests/plugins/workflow/test_installed_distribution_e2e.py \
    docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "fix(workflow): bind phase 5 runtime identity to endpoint"
  ```

---

### Task 1A: Complete the sealed Vertex and credential-pool retry transaction

**Finding:** Review-driven F-4 closure — shared client publication is atomic,
but candidate retention, real OAuth retry, turn markers, cross-source
precedence, cleanup, and redaction are not.

**Files:**

- Create: `agent/credential_adoption.py`
- Modify: `run_agent.py`
- Modify: `agent/agent_runtime_helpers.py`
- Modify: `agent/chat_completion_helpers.py`
- Modify: `agent/conversation_loop.py`
- Modify: `agent/turn_retry_state.py`
- Modify: `tests/run_agent/test_env_credential_turn_refresh.py`
- Modify: `tests/run_agent/test_credential_pool_interrupt.py`
- Modify: `tests/run_agent/test_fallback_credential_isolation.py`
- Modify: `tests/run_agent/test_primary_runtime_restore.py`
- Create: `tests/agent/test_credential_adoption.py`
- Modify: `tests/agent/test_turn_retry_state.py`
- Modify: `tests/agent/test_credential_pool_routing.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Consumes: `CredentialFreeExecutionRouteConstraint`, `ExecutionSdkEndpoint`,
  `_replace_primary_openai_client()`, the AIAgent OpenAI client lock, current
  `CredentialPool.try_refresh_matching()` persistence semantics, and the exact
  sealed route assertion completed by Task 1.
- Produces:

  ```python
  @dataclass(slots=True)
  class _CredentialRecoveryTurnState:
      generation: int
      auth_pool_refresh_counts: dict[tuple[str, str], int] = field(
          default_factory=dict
      )


  @dataclass(frozen=True, slots=True, repr=False)
  class _PendingSealedCredentialAdoption:
      generation: int
      source: Literal["vertex", "pool", "anthropic_direct"]
      route_constraint: CredentialFreeExecutionRouteConstraint = field(repr=False)
      api_key: str = field(repr=False)
      base_url: str = field(repr=False)
      client_kwargs: Mapping[str, Any] = field(repr=False, compare=False)
      pool_entry_id: str | None = None
      is_anthropic_oauth: bool | None = None
      adoption_attempts: int = 0


  class _CredentialRefreshStatus(StrEnum):
      ADOPTED = "adopted"
      ACQUISITION_FAILED = "acquisition_failed"
      ADOPTION_FAILED = "adoption_failed"
      INVALIDATED = "invalidated"
      NOT_APPLICABLE = "not_applicable"


  class _CandidateAttemptStatus(StrEnum):
      ADOPTED = "adopted"
      RETRYABLE_BUILD_FAILURE = "retryable_build_failure"
      INVALIDATED = "invalidated"
  ```

  `_PendingSealedCredentialAdoption`, `_CredentialRefreshStatus`,
  `_snapshot_candidate_client_kwargs()`, and
  `_materialize_candidate_client_kwargs()` live in the dependency-light
  `agent/credential_adoption.py`; `_CredentialRecoveryTurnState` lives in
  `agent/turn_retry_state.py`. The candidate stores the frozen authoritative
  `CredentialFreeExecutionRouteConstraint`; it does not handwrite or duplicate
  provider/model/API/endpoint identity. The snapshot helper accepts only
  `None`, booleans, numbers, strings, bytes, nested string-key mappings,
  lists/tuples/sets/frozensets of supported values, and `httpx.Timeout` or
  `httpx.Limits` reconstructed from their scalar settings. It rejects cycles,
  callables, SDK clients, transports, custom mutable objects, and unsupported
  values before candidate installation with `ACQUISITION_FAILED`. It exposes a
  read-only deep copy and excludes `http_client`. The materializer returns a
  fresh dict for each construction attempt. The candidate never retains a
  mutable `PooledCredential` and exposes no public API or wire type.

- [ ] **Step 1: Add RED real-path pool and Vertex reproductions.**

  In `test_env_credential_turn_refresh.py`, create real sealed `AIAgent`
  instances and concrete OpenAI clients. Use a temporary real
  `CredentialPool`; patch only the OAuth token endpoint or provider credential
  resolver and the lowest SDK constructor boundary.

  ```python
  def test_sealed_pool_constructor_failure_retries_same_real_oauth_token_once(
      tmp_path, monkeypatch
  ):
      first_client = agent.client
      result = drive_real_pool_401_through_conversation(agent)
      assert oauth_refresh_calls == 1
      assert constructor_tokens == [fresh_token, fresh_token]
      assert result.get("failed") is not True
      assert agent.client is not first_client


  def test_sealed_vertex_constructor_failure_reads_credentials_once(
      tmp_path, monkeypatch
  ):
      result = drive_vertex_401_through_conversation(agent)
      assert vertex_credential_reads == 1
      assert constructor_tokens == [fresh_token, fresh_token]
      assert result.get("failed") is not True
  ```

  Add
  `test_sealed_pool_two_adoption_failures_clear_candidate`,
  `test_sealed_vertex_acquisition_empty_is_bounded`, and
  `test_sealed_vertex_acquisition_error_is_bounded`. Assert no third
  constructor call and no repeated credential-source call after acquisition
  itself fails. Define `_build_real_sealed_openai_agent`,
  `_sealed_route_constraint`, `_LowestConstructorBarrier`, and
  `_drive_provider_error` in this test file; all later Task 1A integration
  tests reuse those exact helpers.

  In `tests/agent/test_credential_adoption.py`, add pure RED tests requiring
  the new type, secret-safe representation, defensive nested snapshots, and
  transport exclusion:

  ```python
  def test_pending_candidate_snapshot_is_private_and_transport_free():
      original = {
          "api_key": "TOKEN_CANARY",
          "default_headers": {"Authorization": "HEADER_CANARY"},
          "default_query": {"api-version": ["one", "two"]},
          "http_client": object(),
      }
      candidate = _PendingSealedCredentialAdoption(
          generation=1,
          source="pool",
          route_constraint=_sealed_route_constraint(),
          api_key="TOKEN_CANARY",
          base_url="https://route.test/v1",
          client_kwargs=_snapshot_candidate_client_kwargs(original),
      )
      original["default_headers"]["Authorization"] = "mutated"
      assert "TOKEN_CANARY" not in repr(candidate)
      assert "HEADER_CANARY" not in repr(candidate)
      assert "http_client" not in candidate.client_kwargs
      materialized = _materialize_candidate_client_kwargs(
          candidate.client_kwargs
      )
      assert materialized["default_headers"]["Authorization"] == "HEADER_CANARY"
  ```

  Parameterize the pure test with a callable, cycle, arbitrary mutable object,
  SDK client, and caller-owned transport; each must fail before a candidate is
  installed. Add positive rows for nested mappings, `httpx.Timeout`, and
  `httpx.Limits`, and assert two materializations do not share mutable values.

- [ ] **Step 2: Run the real-path tests RED.**

  First run only the existing-file behavioral reproductions so the new module
  cannot mask them with a collection error:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_env_credential_turn_refresh.py \
    -k "sealed_pool_constructor_failure_retries_same_real_oauth_token_once or sealed_vertex_constructor_failure_reads_credentials_once or sealed_pool_two_adoption_failures_clear_candidate or sealed_vertex_acquisition_empty_is_bounded or sealed_vertex_acquisition_error_is_bounded" \
    -q
  ```

  Expected: FAIL on behavioral assertions because the real pool force-refreshes
  again, the existing Vertex guard prevents the identical-candidate retry, and
  retry state is not transaction-aware.

  Then run the pure new-module RED separately:

  ```bash
  scripts/run_tests.sh tests/agent/test_credential_adoption.py -q
  ```

  Expected: FAIL at import because `agent.credential_adoption` and its private
  types do not exist. Record both RED results independently.

- [ ] **Step 3: Add turn ownership and locked pending-candidate lifecycle.**

  In `agent/turn_retry_state.py`, add `_CredentialRecoveryTurnState` exactly as
  above. In the AIAgent forwarder around `conversation_loop.run_conversation`,
  install a generation-scoped state and guarantee cleanup through its existing
  outer `try/finally`:

  ```python
  recovery_state = self._begin_credential_recovery_turn()
  try:
      result = run_conversation(
          self,
          user_message,
          system_message,
          conversation_history,
          effective_task_id,
          stream_callback,
          persist_user_message,
          persist_user_timestamp=persist_user_timestamp,
          persist_user_display_kind=persist_user_display_kind,
          persist_user_display_metadata=persist_user_display_metadata,
          moa_config=moa_config,
          credential_recovery_state=recovery_state,
      )
  finally:
      self._end_credential_recovery_turn(recovery_state.generation)
  ```

  Implement private AIAgent methods that acquire `_openai_client_lock()` for
  every pending inspect/install/clear and use a monotonically increasing
  generation. `_end_credential_recovery_turn()` and `close()` deactivate and
  advance the generation under that lock before clearing, so a late publisher
  cannot revive the candidate. `close()` performs the same idempotent clear. Remove the
  per-turn `_auth_pool_refresh_counts` mutation from shared agent state and
  pass `recovery_state` explicitly through `_recover_with_credential_pool()`.

- [ ] **Step 4: Implement bounded identical-candidate adoption.**

  Build one immutable snapshot after the credential source releases its lock.
  Never call a pool or provider credential resolver while holding the client
  lock. Install the candidate once. The owning branch performs at most two
  complete adoption attempts:

  ```python
  with self._openai_client_lock():
      if not self._install_pending_sealed_credential_locked(candidate):
          return _CredentialRefreshStatus.INVALIDATED
      for attempt in range(2):
          try:
              status = self._attempt_pending_openai_candidate_locked(
                  candidate.generation,
                  attempt_number=attempt + 1,
              )
              if status is _CandidateAttemptStatus.ADOPTED:
                  return _CredentialRefreshStatus.ADOPTED
              if status is _CandidateAttemptStatus.INVALIDATED:
                  return _CredentialRefreshStatus.INVALIDATED
              if attempt == 0:
                  self._record_pending_adoption_failure_locked(
                      candidate.generation,
                      attempt_number=1,
                  )
                  continue
          except ProviderCapabilityDriftError:
              raise
      self._clear_pending_sealed_credential_locked(
          candidate.generation,
          reason="adoption_exhausted",
      )
      return _CredentialRefreshStatus.ADOPTION_FAILED
  ```

  The owning branch holds the client lock across install and both bounded
  attempts, so another credential source or fallback cannot interleave.
  `_attempt_pending_openai_candidate_locked()` constructs and route-validates
  under that lock. Before publication it rechecks generation, cancellation,
  the exact stored `CredentialFreeExecutionRouteConstraint`, and pending
  identity. On success it publishes client/API key/base/default query/AIAgent
  pool-entry identity and clears pending before releasing the same lock. A
  retryable constructor/build failure retains pending. Invalidation closes the
  unpublished client and returns `INVALIDATED`; it is never reinstalled or
  retried. Route drift closes and clears under the lock, then raises the exact
  terminal exception. If publication wins, later invalidation observes one
  consistent committed state.

- [ ] **Step 5: Defer conversation-owned retry markers until success.**

  Change the Vertex branch to consume its one-shot guard only on `ADOPTED` or
  on `ACQUISITION_FAILED`; `ADOPTION_FAILED` leaves the success guard false but
  returns control after the candidate's internal two-attempt bound is
  exhausted.

  ```python
  status = agent._refresh_vertex_credentials_for_turn(
      credential_recovery_state
  )
  if status is _CredentialRefreshStatus.ADOPTED:
      _retry.vertex_auth_retry_attempted = True
      continue
  if status is _CredentialRefreshStatus.ACQUISITION_FAILED:
      _retry.vertex_auth_retry_attempted = True
  if status in {
      _CredentialRefreshStatus.INVALIDATED,
      _CredentialRefreshStatus.NOT_APPLICABLE,
  }:
      pass
  ```

  In pool recovery, update `auth_pool_refresh_counts` and return a consumed
  `has_retried_429` only after `_swap_credential()` reports successful sealed
  adoption. For a candidate that fails both adoption attempts, return the
  incoming marker unchanged. Preserve the existing marker behavior when no
  candidate is acquired. `bedrock_converse` and `codex_app_server` return
  `NOT_APPLICABLE` before candidate installation and do not mutate retry
  guards.

- [ ] **Step 6: Run the candidate and turn-state tests GREEN.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/agent/test_credential_adoption.py \
    tests/run_agent/test_env_credential_turn_refresh.py \
    tests/run_agent/test_credential_pool_interrupt.py \
    tests/agent/test_turn_retry_state.py \
    tests/agent/test_credential_pool_routing.py -q
  ```

  Expected: PASS with one credential-source call, two identical constructor
  attempts, success-only marker publication, and unchanged acquisition-failure
  bounds.

- [ ] **Step 7: Add RED exclusivity, race, lifecycle, and accounting tests.**

  Drive real `run_conversation()` failures with deterministic barriers:

  ```python
  def test_fallback_waits_for_pending_candidate_then_runs_once_after_exhaustion(
      tmp_path, monkeypatch
  ):
      barrier = _LowestConstructorBarrier(failures=2)
      recovery_thread = start_pool_recovery(agent, barrier)
      barrier.wait_until_first_failure()
      fallback_thread = start_fallback_activation(agent)
      assert pool_refresh_calls == 1
      assert fallback_resolution_calls == 0
      barrier.release_second_attempt()
      recovery_thread.join()
      fallback_thread.join()
      assert fallback_resolution_calls == 1


  @pytest.mark.parametrize(
      "later_source",
      ["vertex", "anthropic", "nous", "codex"],
  )
  def test_pending_pool_candidate_precedes_every_later_source(
      later_source, tmp_path, monkeypatch
  ):
      barrier = _LowestConstructorBarrier(failures=1)
      drive_mixed_source_401(agent, later_source, barrier)
      assert source_calls[later_source] == 0


  @pytest.mark.parametrize(
      "exit_kind",
      ["normal", "direct_return", "exception", "cancelled", "agent_close"],
  )
  def test_pending_candidate_is_cleared_on_every_turn_exit(
      exit_kind, tmp_path, monkeypatch
  ):
      drive_exit_with_pending_candidate(agent, exit_kind)
      assert agent._pending_sealed_credential_adoption is None
  ```

  Add both cancellation-before-publication and publication-before-cancellation
  schedules. Add named real-conversation tests
  `test_sealed_pool_ordinary_429_markers_change_only_after_adoption`,
  `test_sealed_pool_preexhausted_429_markers_change_only_after_adoption`, and
  `test_pending_candidate_accepts_reordered_repeated_query_identity`.
  Assert provider-attempt and budget ledgers are unchanged by local client
  construction, route drift stays terminal, fallback performs no credential
  resolution while pending, and fallback becomes eligible exactly once after
  the second adoption failure clears pending.

- [ ] **Step 8: Make exclusivity and cleanup GREEN.**

  At the start of every credential recovery/fallback selection, acquire the
  client lock and wait for the owning pending adoption to resolve before
  consulting another source. Add this gate at `try_activate_fallback()` in
  `agent/chat_completion_helpers.py` before fallback credential lookup or
  client construction. Immediately before fallback publication, invalidate
  under the client lock before provider/model/API-mode mutation.
  Reuse the AIAgent forwarder's existing outer `finally`; do not add partial
  cleanup to only `finalize_turn()`.

  ```python
  transition_generation = agent._credential_transition_generation()
  if transition_generation is None:
      return False
  fb_client, _resolved_fb_model = resolve_provider_client(
      fb_provider,
      model=fb_model,
      raw_codex=True,
      explicit_base_url=fb_base_url_hint,
      explicit_api_key=fb_api_key_hint,
  )
  if not agent._invalidate_for_route_transition(
      transition_generation,
      fallback_client=fb_client,
  ):
      agent._close_openai_client(
          fb_client,
          reason="rejected:fallback_generation_changed",
          shared=False,
      )
      return False
  agent.model = fb_model
  agent.provider = fb_provider
  agent.requested_provider = fb_provider
  agent.base_url = fb_base_url
  agent.api_mode = fb_api_mode
  ```

  `_credential_transition_generation()` waits for the client lock, returns only
  after pending adoption has resolved, and captures the active generation.
  `_invalidate_for_route_transition()` reacquires the lock immediately before
  route mutation; it rejects a changed generation or new pending candidate and
  otherwise invalidates the old generation before the existing mode-specific
  fallback publication continues.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_env_credential_turn_refresh.py \
    tests/run_agent/test_fallback_credential_isolation.py \
    tests/run_agent/test_primary_runtime_restore.py \
    -k "pending or sealed_pool_ordinary_429 or sealed_pool_preexhausted_429 or reordered_repeated_query" \
    -q
  ```

  Expected: PASS in both race orderings, all source rows, both 429 states, the
  canonical query case, and all exit paths.

- [ ] **Step 9: Add RED privacy and resource-ownership canaries.**

  Supply canaries in the token, structural endpoint, exception text, endpoint
  digest, and temporary path. Exercise constructor failure, second adoption
  failure, route drift, nested `_create_openai_client` failure, nested
  `_close_openai_client` failure, rejected-client close, and prior-client
  retirement failure. Name these tests with the shared substring
  `sealed_candidate_redaction`.

  ```python
  combined = caplog.text + json.dumps(public_result, sort_keys=True)
  for canary in secret_canaries:
      assert canary not in combined
  assert old_client.close_calls == 0  # failed adoption
  assert rejected_owned_transport.closed
  assert not caller_owned_transport.closed
  ```

- [ ] **Step 10: Replace raw candidate-path logging and run privacy GREEN.**

  Add `candidate_safe: bool = False` to the private construction, close, and
  retirement helpers reached by pending adoption. Candidate calls pass true;
  in that mode every nested helper logs only a stable reason token and
  `type(exc).__name__`. Never interpolate `_client_log_context()`, `base_url`,
  `str(exc)`, the candidate, token, or digest.

  ```python
  logger.warning(
      "sealed credential adoption failed reason=%s error_type=%s",
      reason,
      type(exc).__name__,
  )
  ```

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_env_credential_turn_refresh.py \
    tests/run_agent/test_fallback_credential_isolation.py \
    -k "sealed_candidate_redaction or rejected_owned_transport or caller_owned_transport or retirement_failure" \
    -q
  ```

  Expected: PASS with bounded nested logs and correct transport ownership.

- [ ] **Step 11: Prove sealed and legacy compatibility.**

  Extend `test_installed_distribution_e2e.py` with
  `test_installed_distribution_runs_sealed_openai_credential_transaction`.
  Build/install the wheel through its existing fixture, import
  `agent.credential_adoption` and `AIAgent` from that installed environment,
  replace only the SDK constructor with a deterministic first-failure/second-
  success factory, and assert one candidate produces two identical keys and
  one published concrete client. The subprocess must assert its imported
  module paths are outside the checkout.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_env_credential_turn_refresh.py \
    tests/run_agent/test_credential_pool_interrupt.py \
    tests/run_agent/test_credential_rotation_route_settings.py \
    tests/run_agent/test_fallback_credential_isolation.py \
    tests/run_agent/test_primary_runtime_restore.py \
    tests/run_agent/test_switch_model_pool_reload_52727.py \
    tests/agent/test_turn_retry_state.py \
    tests/agent/test_credential_pool.py \
    tests/agent/test_credential_pool_oauth_writethrough.py \
    tests/agent/test_credential_pool_routing.py \
    tests/agent/test_provider_attempt_transport.py \
    tests/plugins/workflow/test_phase5_cost_budget.py \
    tests/plugins/workflow/test_retry.py -q

  scripts/run_tests.sh \
    tests/plugins/workflow/test_installed_distribution_e2e.py \
    -m integration -q
  ```

  Expected: PASS with sealed transactional behavior and unsealed behavior
  characterized exactly as recorded at `e1c7ca745`. Add explicit
  characterization assertions before changing any unsealed branch; do not
  treat the current `7001ca3fe` behavior as the compatibility baseline.

- [ ] **Step 12: Ledger and commit Task 1A.**

  Update only the existing `workflow-phase5-sealed-runtime-context` entry with
  the pending-candidate, turn-state, lifecycle, source-precedence, privacy, and
  retry-marker symbols. Do not add a new family or advance
  `last_verified_upstream`.

  Run:

  ```bash
  scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
  git diff --check
  git status --short
  git add \
    run_agent.py \
    agent/credential_adoption.py \
    agent/agent_runtime_helpers.py \
    agent/chat_completion_helpers.py \
    agent/conversation_loop.py \
    agent/turn_retry_state.py \
    tests/run_agent/test_env_credential_turn_refresh.py \
    tests/run_agent/test_credential_pool_interrupt.py \
    tests/run_agent/test_fallback_credential_isolation.py \
    tests/run_agent/test_primary_runtime_restore.py \
    tests/agent/test_credential_adoption.py \
    tests/agent/test_turn_retry_state.py \
    tests/agent/test_credential_pool_routing.py \
    tests/plugins/workflow/test_installed_distribution_e2e.py \
    docs/upstream-customizations/workflow-orchestration.yaml
  git commit -m "fix(workflow): bind sealed credential retry ownership"
  ```

---

### Task 1B: Apply the sealed transaction to Anthropic credential adoption

**Finding:** Task 1A's generic credential-pool contract must cover the already
supported `anthropic_messages` mode; the current direct and pool paths close or
mutate the live client before replacement succeeds.

**Files:**

- Modify: `run_agent.py`
- Modify: `agent/agent_runtime_helpers.py`
- Modify: `agent/conversation_loop.py`
- Create: `tests/run_agent/test_sealed_anthropic_credential_adoption.py`
- Modify: `tests/run_agent/test_anthropic_third_party_oauth_guard.py`
- Modify: `tests/run_agent/test_28161_anthropic_stream_pool_cleanup.py`
- Modify: `tests/run_agent/test_run_agent.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

- Consumes: `_PendingSealedCredentialAdoption`,
  `_CredentialRecoveryTurnState`, generation-checked lifecycle, and the
  success-only marker contract from Task 1A.
- Produces:

  Required private signature:

  ```text
  AIAgent._publish_pending_anthropic_candidate(
      candidate: _PendingSealedCredentialAdoption,
  ) -> _CandidateAttemptStatus
  ```

  On success it publishes `_anthropic_client`, `_anthropic_api_key`,
  `_anthropic_base_url`, `_is_anthropic_oauth`, generic `api_key`, generic
  `base_url`, and `_credential_pool_entry_id` together. It retires the old
  client only after publication.

- [ ] **Step 1: Add RED concrete Anthropic direct and pool tests.**

  Use real initialized sealed agents and concrete Anthropic SDK clients. Patch
  only the native token resolver/OAuth endpoint and lowest Anthropic client
  constructor boundary. Define `_build_real_sealed_anthropic_agent`,
  `_snapshot_anthropic_state`, `_AnthropicConstructorBarrier`, and
  `_drive_native_anthropic_401`, and `_drive_pool_adoption_failure` in the new
  test file.

  ```python
  def test_sealed_anthropic_direct_refresh_is_atomic_and_reuses_token(
      tmp_path, monkeypatch
  ):
      old = _snapshot_anthropic_state(agent)
      result = _drive_native_anthropic_401(agent)
      assert token_reads == 1
      assert constructor_tokens == [fresh_token, fresh_token]
      assert result.get("failed") is not True
      assert old.client.closed_after_publish


  def test_sealed_anthropic_pool_failure_preserves_all_mode_fields(
      tmp_path, monkeypatch
  ):
      old = _snapshot_anthropic_state(agent)
      _drive_pool_adoption_failure(agent)
      assert _snapshot_anthropic_state(agent) == old
      assert not old.client.closed
  ```

  Include route drift, cancellation at the barrier, second failure, old-client
  close failure, third-party Anthropic-compatible provider identity, and pool
  entry publication. Add conversation-level
  `test_sealed_anthropic_acquisition_empty_consumes_existing_guard`,
  `test_sealed_anthropic_acquisition_error_consumes_existing_guard`,
  `test_sealed_anthropic_first_build_failure_then_success_sets_guard`, and
  `test_sealed_anthropic_two_build_failures_leave_success_guard_clear`.

- [ ] **Step 2: Run Anthropic RED.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_sealed_anthropic_credential_adoption.py \
    tests/run_agent/test_anthropic_third_party_oauth_guard.py \
    tests/run_agent/test_28161_anthropic_stream_pool_cleanup.py \
    tests/run_agent/test_run_agent.py -q
  ```

  Expected: FAIL because the old Anthropic client closes and Anthropic fields
  mutate before the replacement is constructed and route-validated.

- [ ] **Step 3: Implement the Anthropic publication row.**

  Add `_refresh_anthropic_credentials_for_turn()` returning
  `_CredentialRefreshStatus`. The 401 branch in `conversation_loop.py` sets
  `anthropic_auth_retry_attempted` only on `ADOPTED` or
  `ACQUISITION_FAILED`; `ADOPTION_FAILED`, `INVALIDATED`, and
  `NOT_APPLICABLE` do not consume the success guard.

  For sealed routes, `_create_request_anthropic_client()` and the shared-client
  fallback inside `_anthropic_messages_create()` use the currently published
  credential and do not call the credential resolver proactively. The 401
  recovery branch is the sole owner of sealed direct credential acquisition
  and pending adoption. Preserve the current proactive refresh behavior for
  unsealed agents exactly as recorded at `e1c7ca745`.

  Construct the candidate client while the old client stays live. Recheck
  generation, cancellation, provider/model/API mode, and sealed route under the
  client lock immediately before publication.

  ```python
  new_client = build_anthropic_client(
      candidate.api_key,
      candidate.base_url,
      timeout=get_provider_request_timeout(self.provider, self.model),
  )
  self._assert_execution_route_constraint(
      new_client,
      base_url=candidate.base_url,
  )
  old_client = self._anthropic_client
  self._anthropic_client = new_client
  self._anthropic_api_key = candidate.api_key
  self._anthropic_base_url = candidate.base_url
  self._is_anthropic_oauth = candidate.is_anthropic_oauth
  self.api_key = candidate.api_key
  self.base_url = candidate.base_url
  self._credential_pool_entry_id = candidate.pool_entry_id
  ```

  Execute that publication only inside the Task 1A transaction. After lock
  release, retire `old_client` best-effort; close failure logs only reason and
  exception type and does not roll back the new state. Reject
  `bedrock_converse` and `codex_app_server` pool fallthrough explicitly.

- [ ] **Step 4: Run Anthropic GREEN and redaction checks.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_sealed_anthropic_credential_adoption.py \
    tests/run_agent/test_anthropic_third_party_oauth_guard.py \
    tests/run_agent/test_28161_anthropic_stream_pool_cleanup.py \
    tests/run_agent/test_run_agent.py -q
  ```

  Expected: PASS with exact state preservation on failure, identical-token
  retry, no third attempt, and canaries absent from logs/results.

- [ ] **Step 5: Run installed, transport, and historical closure.**

  Extend the installed-distribution transaction probe with a sealed
  `anthropic_messages` row. It must construct a real installed Anthropic client,
  fail the first lowest-boundary construction, adopt the exact same token on
  the second construction, and assert every generic and Anthropic field is
  consistent before the installed subprocess exits.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/run_agent/test_sealed_anthropic_credential_adoption.py \
    tests/run_agent/test_env_credential_turn_refresh.py \
    tests/run_agent/test_run_agent.py \
    tests/run_agent/test_28161_anthropic_stream_pool_cleanup.py \
    tests/run_agent/test_anthropic_third_party_oauth_guard.py \
    tests/agent/test_anthropic_adapter.py \
    tests/agent/test_provider_attempt_transport.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_phase5_cost_budget.py \
    tests/plugins/workflow/test_language_snapshot.py \
    tests/plugins/workflow/test_phase4_snapshot.py -q

  scripts/run_tests.sh \
    tests/plugins/workflow/test_installed_distribution_e2e.py \
    -m integration -q
  ```

  Expected: PASS with v1-v4 behavior unchanged, sealed Anthropic route identity
  exact, no provider-attempt or budget consumption during local construction,
  and installed-wheel imports using the same private transaction code.

- [ ] **Step 6: Ledger and commit Task 1B.**

  Extend only the existing `workflow-phase5-sealed-runtime-context` ledger
  entry with the Anthropic publication symbols/tests. Run:

  ```bash
  scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
  git diff --check
  git status --short
  git add \
    run_agent.py \
    agent/agent_runtime_helpers.py \
    agent/conversation_loop.py \
    tests/run_agent/test_sealed_anthropic_credential_adoption.py \
    tests/run_agent/test_anthropic_third_party_oauth_guard.py \
    tests/run_agent/test_28161_anthropic_stream_pool_cleanup.py \
    tests/run_agent/test_run_agent.py \
    tests/plugins/workflow/test_installed_distribution_e2e.py \
    docs/upstream-customizations/workflow-orchestration.yaml
  git commit -m "fix(workflow): make sealed anthropic adoption atomic"
  ```

  Task 2 may start only after fresh independent reviews report zero Critical or
  Important findings for both Task 1A and Task 1B.

---

### Task 2: Derive structured repair from the sealed primary request

**Finding:** F-2

**Files:**

- Modify: `plugins/workflow/executors/ai.py`
- Modify: `tests/plugins/workflow/test_ai_executor.py`
- Modify: `tests/plugins/workflow/test_phase5_provider_options.py`
- Modify: `tests/plugins/workflow/test_phase5_execution_context.py`
- Modify: `tests/agent/test_plugin_agent_prefix_identity.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Exact interface:**

```python
def _phase5_structured_repair_request(
    self,
    *,
    initial_request: PluginAgentRunRequest,
    repair_prompt: str,
    remaining_provider_attempts: int,
    remaining_timeout_seconds: float,
) -> PluginAgentRunRequest: ...
```

The helper uses `dataclasses.replace(initial_request, ...)`. It preserves provider, model, intended authority, complete six-field runtime identity, reasoning configuration, request overrides, `sealed_provider_attempt_grant`, `_provider_attempt_authority`, `max_budget_usd`, `_cost_budget_authority`, `_cost_budget_contract`, deadlines, resources, workdir, execution environment, and sandbox decision; the runner passes the same parent cancellation callback. It deliberately clears session/prefix reuse, tool access, hooks, MCP, skills, inline agents, fallback, approval, and ephemeral system prompt. Legacy v1-v4 repair retains its existing request construction.

- [ ] **Step 1: Add RED tests for the exact inherited authority envelope.**

  Assert the repair request preserves:

  - `provider`, `model`, `intended_authority_digest`;
  - all six `expected_runtime_identity` fields;
  - `reasoning_config` and sealed `request_overrides` including degraded option translation;
  - the same provider-attempt authority, cost-budget authority/contract, deadlines, resource/workdir/cancellation limits, execution environment, and sandbox decision.

  Assert it clears:

  - `session_id` and both expected prefix/MCP-runtime reuse digests;
  - enabled/allowed tools and denies delegation;
  - hooks, MCP servers, skills, inline agents, fallback, approval digest, and ephemeral system prompt.

  In the same RED edit, admit one route, force invalid structured output, mutate the runtime endpoint or registration provenance before repair, and assert `provider_capability_drift` before repair agent construction or transport. Add a budget-exhaustion case proving exhaustion is terminal and cannot become repair or retry.

  ```python
  def test_phase5_structured_repair_inherits_sealed_route_and_option_transport(...):
      primary, repair = execute_invalid_structured_output(...)
      assert repair.expected_runtime_identity == primary.expected_runtime_identity
      assert repair.reasoning_config == primary.reasoning_config
      assert repair.request_overrides == primary.request_overrides
      assert repair.session_id is None
      assert repair.expected_mcp_runtime_identity_digest is None
      assert repair.allowed_tools == ()
  ```

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_ai_executor.py \
    tests/plugins/workflow/test_phase5_provider_options.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/agent/test_plugin_agent_prefix_identity.py -q
  ```

  Expected: FAIL because repair currently constructs a partial request and live-resolves omitted route/options.

- [ ] **Step 2: Implement the narrowed v5 copy without changing legacy repair.**

  Branch on recorded Phase 5 semantics, not the process's latest version. Use `replace()` so newly added limit fields cannot be silently omitted later. Explicitly enumerate every cleared extension/cache field and deny `delegate_task` plus `workflow_agent`.

  Before launch, validate that the repair decision's effective provider, model, API mode, structured-output strategy, adapter version, and schema fingerprint match the admitted authority. A mismatch returns the existing bounded fail-closed diagnostic and consumes no extra provider attempt.

- [ ] **Step 3: Run the repair drift and no-side-effect tests GREEN.**

  Re-run the drift, registration-provenance, budget, and construction/transport spies added RED in Step 1:

  ```python
  def test_phase5_structured_repair_runtime_drift_blocks_before_agent_construction(...):
      result = execute_with_invalid_output_then_runtime_drift(...)
      assert result.code == "provider_capability_drift"
      assert agent_constructions == 1  # primary only
      assert provider_calls == 1
  ```

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/agent/test_plugin_agent_prefix_identity.py -q
  ```

  Expected: PASS only when repair carries the sealed identity, shares the parent authorities, and the worker blocks drift before side effects.

- [ ] **Step 4: Run repair, budget, cache, and legacy regression gates.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_ai_executor.py \
    tests/plugins/workflow/test_phase5_provider_options.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_phase5_cost_budget.py \
    tests/plugins/workflow/test_structured_output_language.py \
    tests/plugins/workflow/test_persistent_session_recovery.py \
    tests/agent/test_plugin_agent_prefix_identity.py -q
  ```

  Expected: PASS; v5 repair is sealed/fresh/bounded, legacy repair behavior is unchanged, and retries cannot reset budget or attempt accounting.

- [ ] **Step 5: Ledger and commit only the repair envelope.**

  Update the existing generic plugin-agent/workflow structured-repair symbols in `workflow-orchestration.yaml` in this same commit; do not defer the ownership record to Task 5 and do not advance `last_verified_upstream`.

  Run:

  ```bash
  scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
  git diff --check
  git add \
    plugins/workflow/executors/ai.py \
    tests/plugins/workflow/test_ai_executor.py \
    tests/plugins/workflow/test_phase5_provider_options.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/agent/test_plugin_agent_prefix_identity.py \
    docs/upstream-customizations/workflow-orchestration.yaml
  git commit -m "fix(workflow): seal phase 5 structured repair authority"
  ```

---

### Task 3: Restore v5 shared context with a distinct compatibility identity and durable handoff

**Finding:** F-1

**Files:**

- Modify: `plugins/workflow/execution_semantics.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py` only if a bounded winning-attempt metadata reader cannot use the existing store API
- Modify: `tests/plugins/workflow/test_phase5_execution_context.py`
- Modify: `tests/plugins/workflow/test_ai_e2e.py`
- Modify: `tests/plugins/workflow/test_scheduler.py`
- Modify: `tests/plugins/workflow/test_schedule_store_identity.py`
- Modify: `tests/plugins/workflow/test_persistent_session_recovery.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Exact interfaces:**

```python
def phase5_shared_context_compatibility_digest(
    package: WorkflowPackage,
    provider_authority: WorkflowProviderAuthority,
    *,
    node_id: str,
    sealed_closure_digest: str,
) -> str: ...


@dataclass(frozen=True)
class NodeExecutionContext:
    # existing fields remain unchanged
    shared_context_compatibility_digest: str | None = None
```

The new digest is cross-node cache compatibility, not node execution authority. `phase5_node_intended_authority_digest()` remains node-bound and unchanged. The scheduler computes both from the same sealed package and passes both explicitly.

- [ ] **Step 1: Add RED pure-contract tests separating the two identities.**

  Construct two nodes with distinct IDs/routes/obligation paths but byte-identical cache-affecting semantics. Assert:

  ```python
  assert intended_digest(node_a) != intended_digest(node_b)
  assert shared_compatibility_digest(node_a) == shared_compatibility_digest(node_b)
  ```

  Add table-driven mutations proving the compatibility digest changes for endpoint, provider/model/API mode, options/reasoning, allowed or denied tools, hooks, MCP closure/tool/import policy, skills, inline agents, system-prompt configuration, fallback, structured-output schema/adapter, budget/sandbox decisions, adapter versions, and sealed-closure digest.

  Add exclusions proving it does not change solely for node/route ID, source path/index/line, dependency name/graph position, node prompt, `context` selection, retry, timeout, persistence, or scheduling controls.

  Run:

  ```bash
  scripts/run_tests.sh tests/plugins/workflow/test_phase5_execution_context.py -q
  ```

  Expected: FAIL because only the node-bound intended digest exists.

- [ ] **Step 2: Implement one documented canonical projection.**

  Build the compatibility material from normalized/sealed objects—never raw YAML, live config, or live resource paths. Strip only these structural fields from route/decision projections:

  ```python
  _SHARED_CONTEXT_STRUCTURAL_FIELDS = frozenset({
      "node_id", "route_id", "path", "source_index", "source_line",
  })
  ```

  Do not use a recursive blanket stripper that could accidentally remove a semantic field with the same spelling. Construct the explicit projection at each typed layer, sort decisions by semantic content, domain-separate/version the canonical JSON, and retain only the final digest in runtime metadata/evidence.

- [ ] **Step 3: Add RED executor tests for real distinct-node sharing.**

  Replace fake equal intended digests with two real admitted nodes. Cover:

  - compatible predecessor resumes its session;
  - current node still sends its own intended authority to the worker;
  - predecessor prefix digest is the worker's expected reused-prefix digest;
  - predecessor cache fingerprint is recomputed from predecessor intended authority plus predecessor prefix;
  - any shared-compatibility mismatch returns `context_incompatible` before construction/transport;
  - `context: fresh` never consumes predecessor session state.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_ai_e2e.py -q
  ```

  Expected: FAIL because the executor currently compares predecessor intended identity to the current node's intended identity.

- [ ] **Step 4: Wire the separate predicate into the executor.**

  The Phase 5 shared-context predicate is exactly:

  ```python
  if predecessor.shared_context_compatibility_digest != current.shared_context_compatibility_digest:
      return context_incompatible()

  expected_predecessor_cache = phase5_session_cache_fingerprint(
      intended_authority_digest=predecessor.intended_authority_digest,
      model_visible_prefix_digest=predecessor.model_visible_prefix_digest,
  )
  if predecessor.cache_fingerprint != expected_predecessor_cache:
      return context_incompatible()
  ```

  The launch request then carries the current node's intended authority and the predecessor's exact model-visible prefix digest. Never substitute the compatibility digest into same-node recovery, request authority, or evidence fields.

- [ ] **Step 5: Add RED durable scheduler/restart tests.**

  Complete a predecessor, reconstruct scheduler state from the real store/journal, and assert `_predecessor_results()` recovers from the winning successful attempt's authenticated metadata:

  - `intended_authority_digest`;
  - `model_visible_prefix_digest`;
  - `shared_context_compatibility_digest`;
  - existing top-level `session_id` and `cache_fingerprint`.

  Exercise both immediate scheduling and process/crash recovery. A failed or superseded attempt must never provide identities; missing/tampered metadata must fail closed, not fall back to a fabricated live identity.

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_scheduler.py \
    tests/plugins/workflow/test_schedule_store_identity.py \
    tests/plugins/workflow/test_persistent_session_recovery.py \
    tests/plugins/workflow/test_crash_recovery.py -q
  ```

  Expected: FAIL because `_predecessor_results()` currently reconstructs only `session_id` and `cache_fingerprint`.

- [ ] **Step 6: Implement bounded winning-attempt reconstruction.**

  Prefer the existing store's successful-attempt metadata API. If it cannot identify the winning completed attempt without duplicating journal logic, add one private bounded store reader whose return type contains only the three identity digests; do not promote them to new public run fields or REST projections.

  On successful v5 completion, persist `shared_context_compatibility_digest` beside the already-retained intended and prefix digests in authenticated attempt metadata. On restart, validate all three as lowercase SHA-256 before use.

- [ ] **Step 7: Prove compatibility, cache stability, cancellation, and recovery.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_ai_e2e.py \
    tests/plugins/workflow/test_scheduler.py \
    tests/plugins/workflow/test_schedule_store_identity.py \
    tests/plugins/workflow/test_persistent_session_recovery.py \
    tests/plugins/workflow/test_crash_recovery.py \
    tests/plugins/workflow/test_phase4_loop_interactions.py \
    tests/plugins/workflow/test_cancel_node.py \
    tests/plugins/workflow/test_shutdown_recovery.py -q
  ```

  Expected: PASS; v5 shared context works across distinct compatible nodes and after restart, while v1-v4 recovery and cancelled-loop behavior remain exact.

- [ ] **Step 8: Ledger and commit the shared-context repair.**

  Update the existing generic scheduler/session/plugin-agent seam entries in `workflow-orchestration.yaml` for the new dual-identity handoff and winning-attempt reader. Keep the entry symbol-level and do not advance `last_verified_upstream`.

  Run:

  ```bash
  scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
  git diff --check
  git add \
    plugins/workflow/execution_semantics.py \
    plugins/workflow/executors/base.py \
    plugins/workflow/executors/ai.py \
    plugins/workflow/scheduler.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_ai_e2e.py \
    tests/plugins/workflow/test_scheduler.py \
    tests/plugins/workflow/test_schedule_store_identity.py \
    tests/plugins/workflow/test_persistent_session_recovery.py \
    tests/plugins/workflow/test_crash_recovery.py \
    docs/upstream-customizations/workflow-orchestration.yaml
  if ! git diff --quiet -- plugins/workflow/store.py; then git add plugins/workflow/store.py; fi
  git commit -m "fix(workflow): restore phase 5 shared context"
  ```

---

### Task 4: Enforce deterministic canonical and alias precedence in the provider registry

**Finding:** F-3

**Files:**

- Modify: `providers/__init__.py`
- Modify: `tests/providers/test_provider_registry.py`
- Modify: `tests/providers/test_plugin_discovery.py`
- Modify: `tests/hermes_cli/test_provider_profile_precedence.py`
- Modify: `tests/hermes_cli/test_provider_capabilities.py`
- Modify: `tests/hermes_cli/test_resolve_provider_full_plugin_registry.py`
- Modify: `tests/hermes_cli/test_inventory.py`
- Modify: `tests/hermes_cli/test_web_server.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Exact internal interface:**

```python
@dataclass(frozen=True)
class _ProviderAliasRegistration:
    canonical_name: str
    provenance: ProviderRegistrationProvenance


_ALIASES: dict[str, _ProviderAliasRegistration] = {}
```

Canonical tokens always outrank alias claims. Canonical-versus-canonical keeps the existing origin order `bundled < legacy_compatible < user_plugin`. Alias-versus-alias uses the same origin order. Equal-precedence collisions preserve existing last-writer compatibility and emit one bounded diagnostic. Lookup checks `_REGISTRY` before `_ALIASES`.

- [ ] **Step 1: Add RED collision reproductions for both reported directions.**

  Add tests that register:

  - a user plugin aliasing `openrouter`, where the bundled canonical provider must remain selected and a canonical-over-alias diagnostic must exist;
  - a user plugin canonically named `claude`, where the user canonical provider must be selectable even though `claude` was already a bundled alias;
  - two aliases from bundled/legacy/user origins, where origin precedence selects deterministically;
  - replacement/unregistration, where stale alias provenance is removed;
  - capability resolution through the winning token, where no alias can forge trusted-direct/native capability.

  ```python
  def test_canonical_name_always_outranks_user_alias_collision():
      register(user_profile(name="shadow", aliases=("openrouter",)))
      assert get_provider("openrouter").name == "openrouter"
      assert collision_codes() == ["provider_alias_rejected_canonical"]
  ```

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/providers/test_provider_registry.py \
    tests/hermes_cli/test_provider_profile_precedence.py -q
  ```

  Expected: FAIL because alias registration is unconditional and lookup currently resolves aliases before canonical names.

- [ ] **Step 2: Implement one token-namespace precedence algorithm.**

  Preserve public registry functions. Change only internal alias storage and collision arbitration:

  ```python
  def get_provider(name: str) -> ProviderProfile | None:
      if name in _REGISTRY:
          return _REGISTRY[name]
      alias = _ALIASES.get(name)
      return _REGISTRY.get(alias.canonical_name) if alias else None
  ```

  Registration rules:

  1. arbitrate/rewrite the canonical registration using existing provenance precedence;
  2. remove alias claims previously owned by the replaced canonical registration;
  3. for each new alias, refuse it when the token is canonical;
  4. otherwise arbitrate alias provenance with the same origin ordering and replace on equal precedence;
  5. emit exactly one of `provider_alias_rejected_canonical`, `provider_alias_displaced_by_canonical`, `provider_alias_lower_precedence_ignored`, `provider_alias_higher_precedence_replaced`, or `provider_alias_same_precedence_replaced`, containing logical token/provider/origin names only;
  6. invalidate the provider list and any registry-derived cache on every winning canonical or alias-token change.

  Do not change the `bundled + trusted_direct` capability gate.

- [ ] **Step 3: Adapt test fixtures without exposing the internal claim type publicly.**

  Replace fixtures that insert raw strings into `_ALIASES` with registry registration helpers or `_ProviderAliasRegistration` only inside provider-registry tests. Application-level tests must stop depending on the private value representation and assert behavior through `get_provider()`, inventory, auth, and web APIs.

- [ ] **Step 4: Run registry, discovery, capability, inventory, and web regressions.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/providers/test_provider_registry.py \
    tests/providers/test_plugin_discovery.py \
    tests/hermes_cli/test_provider_profile_precedence.py \
    tests/hermes_cli/test_provider_capabilities.py \
    tests/hermes_cli/test_resolve_provider_full_plugin_registry.py \
    tests/hermes_cli/test_inventory.py \
    tests/hermes_cli/test_web_server.py \
    tests/plugins/workflow/test_phase5_provider_authority.py \
    tests/plugins/workflow/test_phase5_provider_options.py -q
  ```

  Expected: PASS with deterministic collision diagnostics and unchanged native-capability trust gating.

- [ ] **Step 5: Ledger and commit the generic registry fix.**

  Run:

  ```bash
  scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q
  git diff --check
  git add \
    providers/__init__.py \
    tests/providers/test_provider_registry.py \
    tests/providers/test_plugin_discovery.py \
    tests/hermes_cli/test_provider_profile_precedence.py \
    tests/hermes_cli/test_provider_capabilities.py \
    tests/hermes_cli/test_resolve_provider_full_plugin_registry.py \
    tests/hermes_cli/test_inventory.py \
    tests/hermes_cli/test_web_server.py \
    docs/upstream-customizations/workflow-orchestration.yaml
  git commit -m "fix(providers): enforce canonical alias precedence"
  ```

---

### Task 5: Add defensive closure tests and reconcile review documentation

**Files:**

- Create: `tests/plugins/workflow/test_phase5_adversarial_remediation.py`
- Create: `docs/reviews/2026-08-08-workflow-language-phase-5-adversarial-remediation.md`
- Modify: `docs/reviews/2026-08-06-workflow-language-phase-5-validation.md`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml` only if Task 5 changes a generic upstream-owned contract

**Interfaces:** No new production interface. This task locks the four fixes together and records evidence without erasing the original BLOCK history.

- [ ] **Step 1: Add one defensive invariant suite covering the whole bug class.**

  The new test module must prove:

  - no v5 executable route lacks endpoint identity;
  - every request carrying `intended_authority_digest` also carries exact runtime identity;
  - primary, structured repair, fallback, approval, and inline-agent requests use the same central identity builder;
  - shared compatibility can cross node IDs but cannot cross any cache-affecting provider/tool/MCP/skill/hook/inline/prompt configuration;
  - scheduler restart never invents or live-resolves predecessor identity;
  - a canonical provider token cannot be shadowed by an alias of any origin;
  - diagnostics, evidence, notifications, catalog/detail projections, REST responses, Desktop payload/render output, and log capture contain none of the supplied credential, raw URL with credential query, computed endpoint-digest value, registration-digest value, prompt, tool command, provider payload, feedback, or temporary absolute path canaries; endpoint/registration field names may identify the mismatched authority class, but their values remain private.

  Use relation/invariant assertions rather than freezing provider counts, model lists, or incidental serialized ordering.

  Run:

  ```bash
  scripts/run_tests.sh tests/plugins/workflow/test_phase5_adversarial_remediation.py -q
  ```

  Expected: PASS against Tasks 1–4. If it fails, fix the owning task's implementation and add the regression there before proceeding.

- [ ] **Step 2: Run the complete Phase 5 security/redaction/cache closure.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_adversarial_remediation.py \
    tests/plugins/workflow/test_phase4_defensive_invariants.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_phase5_hooks.py \
    tests/plugins/workflow/test_phase5_mcp.py \
    tests/plugins/workflow/test_node_skills.py \
    tests/plugins/workflow/test_phase5_inline_limits.py \
    tests/plugins/workflow/test_phase5_cost_budget.py \
    tests/plugins/workflow/test_phase5_provider_authority.py \
    tests/plugins/workflow/test_security_boundaries.py \
    tests/plugins/workflow/test_phase5_surfaces.py -q
  ```

  Expected: PASS with no unbounded or secret-bearing output.

- [ ] **Step 3: Perform the bounded defensive review once.**

  Review exact changes since `1373c306...` for trust-boundary ordering, URL credential leakage, alias provenance forgery, cache identity omissions, budget/attempt minting, MCP teardown, cancellation, and crash recovery. Record concrete file/line evidence. If a platform security gate blocks this review, record `BLOCKED_BY_PLATFORM_GATE` once and do not attempt to bypass it.

- [ ] **Step 4: Reconcile validation history truthfully.**

  In `2026-08-06-workflow-language-phase-5-validation.md`, preserve the original pre-adversarial GO evidence but add a dated superseding section that records:

  - the 2026-08-07 adversarial BLOCK;
  - F-1 through F-4 and exact remediation commit SHAs;
  - fresh gate totals and commands;
  - whether the BLOCK is cleared or remains open.

  Create `2026-08-08-workflow-language-phase-5-adversarial-remediation.md` with a finding-by-finding evidence table, RED reproduction, GREEN proof, compatibility proof, redaction proof, and residual risks. Never mutate or stage the preserved Fable review.

- [ ] **Step 5: Commit the defensive closure and review evidence.**

  Run:

  ```bash
  git diff --check
  git add \
    tests/plugins/workflow/test_phase5_adversarial_remediation.py \
    docs/reviews/2026-08-08-workflow-language-phase-5-adversarial-remediation.md \
    docs/reviews/2026-08-06-workflow-language-phase-5-validation.md
  if ! git diff --quiet -- docs/upstream-customizations/workflow-orchestration.yaml; then \
    git add docs/upstream-customizations/workflow-orchestration.yaml; \
  fi
  git commit -m "test(workflow): close phase 5 adversarial findings"
  ```

---

### Task 6: Run full distribution, Desktop, upstream, brand, and independent-review gates

**Files:** No production files are expected. Review-driven fixes return to the owning task and receive a new atomic commit.

**Interfaces:** Release-readiness evidence only. This task does not merge, push, tag, publish, or create releases.

- [ ] **Step 1: Run the focused four-finding closure from a clean index.**

  Run:

  ```bash
  git status --short
  scripts/run_tests.sh \
    tests/plugins/workflow/test_phase5_adversarial_remediation.py \
    tests/plugins/workflow/test_phase5_execution_context.py \
    tests/plugins/workflow/test_ai_executor.py \
    tests/plugins/workflow/test_ai_e2e.py \
    tests/plugins/workflow/test_scheduler.py \
    tests/plugins/workflow/test_schedule_store_identity.py \
    tests/plugins/workflow/test_persistent_session_recovery.py \
    tests/plugins/workflow/test_crash_recovery.py \
    tests/plugins/workflow/test_phase5_provider_snapshot.py \
    tests/plugins/workflow/test_phase5_provider_options.py \
    tests/plugins/workflow/test_approval.py \
    tests/agent/test_plugin_agent_prefix_identity.py \
    tests/hermes_cli/test_runtime_provider_resolution.py \
    tests/hermes_cli/test_workflow_model_resolution.py \
    tests/hermes_cli/test_provider_profile_precedence.py \
    tests/providers/test_provider_registry.py -q
  ```

  Expected: PASS with retries disabled. `git status` shows only the two preserved untracked review documents.

- [ ] **Step 2: Run the entire workflow-plugin suite.**

  Run:

  ```bash
  scripts/run_tests.sh tests/plugins/workflow -q
  ```

  Expected: zero failures. Record the exact test count and wall time in the remediation review.

- [ ] **Step 3: Run installed-distribution and package-boundary gates.**

  Run:

  ```bash
  scripts/run_tests.sh \
    tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -q
  scripts/run_tests.sh \
    tests/scripts/test_check_upstream_customizations.py \
    tests/scripts/test_workflow_merge_gate.py \
    tests/scripts/test_workflow_upstream_merge.py \
    tests/test_desktop_workflow_test_gate.py -q
  ```

  Expected: PASS. The installed wheel must not rely on repository-only imports or live source files.

- [ ] **Step 4: Run the full Python suite and lint.**

  Run:

  ```bash
  scripts/run_tests.sh -q
  .venv/bin/ruff check \
    hermes_cli/runtime_provider.py \
    hermes_cli/workflow_model_resolution.py \
    providers/__init__.py \
    agent/plugin_agent.py \
    agent/plugin_agent_worker.py \
    plugins/workflow/provider_authority.py \
    plugins/workflow/execution_semantics.py \
    plugins/workflow/executors/base.py \
    plugins/workflow/executors/ai.py \
    plugins/workflow/executors/approval.py \
    plugins/workflow/scheduler.py
  ```

  Expected: zero test failures and Ruff clean. If an unrelated environmental failure occurs, prove it independently and record it; do not relabel a candidate defect as environmental without reproduction.

- [ ] **Step 5: Run Desktop contract and installed backend regressions.**

  Run the complete Desktop suite, typecheck, and lint exactly as used for the accepted Phase 5 candidate:

  ```bash
  cd apps/desktop
  npm test
  npm run typecheck
  npm run lint
  cd ../..
  ```

  Expected: PASS. Desktop displays backend-authored provider/capability results and does not gain an endpoint, alias, or shared-context resolver.

- [ ] **Step 6: Rehearse upstream and branded release gates without publishing.**

  First run the combined upstream/descriptor rehearsal from the feature worktree:

  ```bash
  export HERMES_TEST_FILE_RETRIES=0
  PHASE5_UPSTREAM_REHEARSAL_ARGS=()
  while IFS= read -r brand_slug; do
    PHASE5_UPSTREAM_REHEARSAL_ARGS+=(--brand-ref "origin/$brand_slug")
  done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json")) { const slug=file.slice(0,-5); loadDescriptor(slug,{root:process.cwd()}); console.log(slug); }')
  PYTHON_BIN=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python \
    scripts/test_workflow_upstream_merge.sh \
    --upstream-ref origin/main \
    --base-ref HEAD \
    "${PHASE5_UPSTREAM_REHEARSAL_ARGS[@]}"
  ```

  Then create one disposable detached worktree at the exact candidate SHA. Link only the repository's existing ignored dependency installations, run the base release gate and the read-only external-state-audited brand loop, and remove only that disposable worktree:

  ```bash
  export HERMES_TEST_FILE_RETRIES=0
  PHASE5_REPOSITORY_ROOT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
  PHASE5_TESTED_SHA="$(git rev-parse HEAD)"
  PHASE5_REHEARSAL_WT="$(mktemp -d)"
  rmdir "$PHASE5_REHEARSAL_WT"
  git worktree add --detach "$PHASE5_REHEARSAL_WT" "$PHASE5_TESTED_SHA"
  ln -s "$PHASE5_REPOSITORY_ROOT/.venv" "$PHASE5_REHEARSAL_WT/.venv"
  if test -d "$PHASE5_REPOSITORY_ROOT/node_modules"; then
    ln -s "$PHASE5_REPOSITORY_ROOT/node_modules" "$PHASE5_REHEARSAL_WT/node_modules"
  fi
  if test -d "$PHASE5_REPOSITORY_ROOT/apps/desktop/node_modules"; then
    ln -s "$PHASE5_REPOSITORY_ROOT/apps/desktop/node_modules" "$PHASE5_REHEARSAL_WT/apps/desktop/node_modules"
  fi
  cd "$PHASE5_REHEARSAL_WT"
  test "$(git rev-parse HEAD)" = "$PHASE5_TESTED_SHA"
  test -z "$(git status --porcelain)"
  PYTHON_BIN="$PHASE5_REPOSITORY_ROOT/.venv/bin/python" \
    scripts/test_workflow_merge_gate.sh --phase base

  PHASE5_AUDIT_DIR="$(mktemp -d)"
  case "$PHASE5_AUDIT_DIR" in
    /tmp/*|/private/tmp/*|/var/folders/*) ;;
    *) echo "unsafe temporary audit directory" >&2; exit 2 ;;
  esac
  phase5_snapshot_external_state() {
    local snapshot_label="$1"
    local source_remote source_remote_url brand_slug releases_repo
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
      gh release list -R "$releases_repo" --limit 200 \
        --json tagName,name,publishedAt,isDraft,isPrerelease \
        > "$PHASE5_AUDIT_DIR/$snapshot_label/$brand_slug-releases.json"
    done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json")) { const slug=file.slice(0,-5); const d=loadDescriptor(slug,{root:process.cwd()}); console.log(`${slug}\t${d.releasesRepo}`); }')
  }
  phase5_snapshot_external_state before
  while IFS= read -r brand_slug; do
    PYTHON_BIN="$PHASE5_REPOSITORY_ROOT/.venv/bin/python" \
      scripts/test_workflow_upstream_merge.sh \
      --upstream-ref origin/main \
      --base-ref "$PHASE5_TESTED_SHA" \
      --brand-ref "origin/$brand_slug" \
      --report-dir "$PHASE5_AUDIT_DIR/rehearsal-$brand_slug"
    phase5_snapshot_external_state "after-$brand_slug"
    diff -ru "$PHASE5_AUDIT_DIR/before" "$PHASE5_AUDIT_DIR/after-$brand_slug"
  done < <(node --input-type=module -e 'import fs from "node:fs"; import {loadDescriptor} from "./scripts/brand/descriptor.mjs"; for (const file of fs.readdirSync("brands").filter(name => /^[a-z][a-z0-9-]*\.json$/.test(name) && name !== "schema.json").sort()) { const slug=file.slice(0,-5); loadDescriptor(slug,{root:process.cwd()}); console.log(slug); }')
  phase5_snapshot_external_state after
  diff -ru "$PHASE5_AUDIT_DIR/before" "$PHASE5_AUDIT_DIR/after"
  git diff --check
  cd "$PHASE5_REPOSITORY_ROOT/.worktrees/workflow-language-phase-5-provider-portability"
  git worktree remove --force "$PHASE5_REHEARSAL_WT"
  ```

  Retain the private audit directory until independent evidence review is complete, then remove only that validated temporary directory. Do not check out or modify `otto`/`loop24`, change version files, or create tags/releases.

  Expected: generic customization and merge gates pass; branded copy/assertions remain correct; Desktop v5.3.0 releases are neither changed nor republished; Python remains independently versioned at 4.2.2 unless an authorized release plan says otherwise.

- [ ] **Step 7: Obtain fresh independent specification and code-quality reviews.**

  Give reviewers the approved remediation design, this plan, adversarial review, exact commit range `1373c306..HEAD`, and test evidence. Require separate verdicts for:

  1. F-1 shared-context semantic and durable recovery compliance;
  2. F-2 structured-repair authority/limits compliance;
  3. F-3 canonical/alias namespace and trust-gate compliance;
  4. F-4 endpoint identity, schema activation, and pre-side-effect ordering;
  5. v1-v4 compatibility, snapshot format 2, redaction, cache stability, installed distribution, Desktop authority, upstream ledger, and brand regressions.

  Address every Critical or Important finding with a new RED test, the smallest owning-task fix, focused GREEN, and an atomic commit. Re-run the affected task gate and this full closure. Treat reviewer leads as hypotheses until reproduced or code-traced to the ten-element adversarial standard.

- [ ] **Step 8: Re-run the adversarial prompt against the immutable final candidate.**

  Create a disposable detached worktree for the final candidate, run the exact Phase 5 adversarial prompt there, and remove only that disposable worktree afterward. Never use, clean, or alter the shared Phase 5 or Phase 4 worktrees. Require a final GO; BLOCK or CONDITIONAL returns to the owning task.

- [ ] **Step 9: Verify exact final Git/worktree preservation state.**

  Run:

  ```bash
  git status --short --branch
  git log --oneline --decorate 1373c306061d2f4a2cf1dd313df16f6453fa1939..HEAD
  git diff --stat 1373c306061d2f4a2cf1dd313df16f6453fa1939..HEAD
  git diff --check 1373c306061d2f4a2cf1dd313df16f6453fa1939..HEAD
  git worktree list --porcelain
  git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent \
    status --short --branch
  git -C ../workflow-language-phase-4-ordinary-loops-immutable-includes \
    status --short --branch
  ```

  Recompute the four preserved review-document hashes from Task 0 and require exact equality. The feature worktree may show only its two preserved untracked Phase 5 review files. Root must remain clean on `base`. Phase 4 must retain its two untracked review files unchanged.

- [ ] **Step 10: Stop for integration authorization.**

  Report:

  - immutable final candidate SHA and atomic remediation commit list;
  - finding-by-finding resolution evidence;
  - exact test/lint/Desktop/distribution/upstream/brand totals;
  - independent review verdicts and residual risks;
  - exact root, feature, and Phase 4 worktree state;
  - confirmation that no push, merge, tag, release, or brand-branch mutation occurred.

  Do not merge to `base`, push, tag, publish, delete worktrees/branches, or begin release work without new explicit authorization.

## Plan Completion Criteria

The plan is complete only when all of the following are simultaneously true:

1. Two distinct compatible v5 nodes can actually use `context: shared`, including after scheduler restart, while incompatible cache material blocks before transport.
2. V5 structured repair inherits the admitted route, exact runtime identity, option transport, and all remaining limits while staying fresh and extension-free.
3. Canonical provider tokens cannot be shadowed by aliases; canonical and alias collisions resolve deterministically with bounded diagnostics and unchanged native trust gating.
4. Same-trust-class endpoint drift blocks before all side effects for primary, repair, fallback, approval, and inline execution.
5. New v5 authorities use schema 2; experimental schema 1 fails closed; normalizer v5 and snapshot format 2 remain unchanged; v1-v4 snapshot/recovery behavior remains exact.
6. Redaction, cache stability, installed distribution, Desktop backend authority, customization ledger, upstream rehearsal, and branded regression gates are green.
7. Independent specification, code-quality/security, and final adversarial reviews return GO with no unresolved Critical or Important finding.
8. All user-owned untracked documents and parallel worktrees retain their original hashes/state.

## Execution Choice After Approval

After the user approves this reviewed plan, offer exactly these implementation modes:

1. **Subagent-Driven (recommended):** serial fresh implementer per task, with specification and code-quality review between tasks.
2. **Inline execution:** the primary agent follows the same task order, RED/GREEN commands, atomic commits, and review checkpoints in this context.

Neither choice authorizes merge, push, tagging, release publication, worktree cleanup, or brand propagation.
