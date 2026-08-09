# Workflow Language Phase 5 adversarial remediation review

**Review date:** 2026-08-08

**Original candidate:** `1373c306061d2f4a2cf1dd313df16f6453fa1939`

**Remediation range reviewed:** `1373c306061d2f4a2cf1dd313df16f6453fa1939..d6960baa096be2d5d0cd66e5e8c7c69e04037046`

**Current verdict:** `BLOCK — remediation is implemented and locally verified; independent final review remains required`

## Status and history

The pre-adversarial GO in
`2026-08-06-workflow-language-phase-5-validation.md` remains valid evidence
for the exact candidate it tested, but it was superseded on 2026-08-07 by the
Fable adversarial review. That review reproduced two High and two Medium
defects and correctly issued `BLOCK`. This document does not erase or relabel
that history.

All four findings now have implementation and regression coverage. The
bounded Task 5 review found no new Critical, Important, or Minor defect in the
remediation range. The BLOCK is nevertheless retained until Task 6 completes
the full distribution, Desktop, upstream, branded-rehearsal, and fresh
independent-review gates.

## Finding closure

| Finding | Original RED evidence | Remediation commits | Current GREEN proof |
|---|---|---|---|
| F-1 High — v5 shared context was structurally dead | The independent review reproduced distinct node-salted intended-authority digests for semantically identical predecessor/current nodes, while admission still marked `context: shared` runnable. | `34de47885` restores a separate cross-node compatibility identity; `c32f5c7e4` persists and recovers the complete identity only from a unique winning attempt. | `phase5_shared_context_compatibility_digest()` excludes graph location while binding routes, provider decisions, tool policy, MCP, skills, hooks, inline agents, prompt, structured output, budget, sandbox, normalizer, authority schema, and the sealed closure (`plugins/workflow/execution_semantics.py:89-215`). The defensive matrix proves node-ID invariance and rejects eight cache-semantic mutation classes; real `RunStore` restart recovery proves no live identity invention. |
| F-2 High — structured repair bypassed sealed route/option authority | The independent trace showed the repair request omitted intended authority, exact runtime identity, reasoning configuration, and request overrides, allowing live route resolution before a post-call mismatch. | `ce7ccfbd6` derives repair by narrowing the admitted request and validates the admitted structured decision; `e4387d214` preserves terminal budget exhaustion. | `_phase5_structured_repair_request()` uses `dataclasses.replace` and clears only disallowed capabilities while retaining route, option, deadline, resource, attempt, cost, workdir, cancellation, and sandbox authorities (`plugins/workflow/executors/ai.py:421-499`). The new route-family test checks primary, repair, fallback, approval, and inline identities against the same six-field builder. |
| F-3 Medium — alias collisions bypassed precedence | The independent reproductions showed a user alias could silently shadow bundled `openrouter`, while a user canonical `claude` could be silently ignored behind a bundled alias. | `2aeb1ada1` establishes one canonical/alias namespace and diagnostics; `3ddd306fa`, `e33d589b4`, `f7c390b90`, `d4ddbc989`, and `d6960baa0` make public, endpoint, runtime, preflight, and compatibility resolution consume the same registry winner. | Canonical lookup wins before aliases; canonical claims displace aliases; alias claims against canonicals are rejected; alias-versus-alias uses origin precedence with bounded diagnostics (`providers/__init__.py:284-350`). The defensive test exercises alias claims from every registered origin. |
| F-4 Medium — same-trust-class endpoint drift passed | The independent trace showed runtime identity compared provider, model, API mode, trust class, and provenance, but not the normalized endpoint. | `dcd0a458c` through `7001ca3fe` add the six-field endpoint-bound identity and enforce it through worker preflight, agent construction, credential refresh, transport, and atomic client adoption; `3c4c70e87`, `b1ddafe67`, `8f5fbf04c`, `98bc4403f`, and `1761d273e` harden retry ownership, Anthropic publication, and diagnostic redaction. | The worker selects and compares the credential-free route before dotenv, SessionDB, MCP finalization, tool mutation, agent construction, or credential resolution (`agent/plugin_agent_worker.py:1500-1559`), then rechecks the resolved runtime (`:1682-1720`). `AIAgent._assert_execution_route_constraint()` reasserts the normalized endpoint at concrete transport seams. The defensive route-family and public-canary tests cover endpoint/provenance binding and non-disclosure. |

## Defensive suite and verification

All commands used retries disabled and the repository shared Python:

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_adversarial_remediation.py -q
```

Result: **1 file, 15 passed, 0 failed in 1.2s**.

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/plugins/workflow/test_phase5_adversarial_remediation.py tests/plugins/workflow/test_phase4_defensive_invariants.py tests/plugins/workflow/test_phase5_execution_context.py tests/plugins/workflow/test_phase5_hooks.py tests/plugins/workflow/test_phase5_mcp.py tests/plugins/workflow/test_node_skills.py tests/plugins/workflow/test_phase5_inline_limits.py tests/plugins/workflow/test_phase5_cost_budget.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_security_boundaries.py tests/plugins/workflow/test_phase5_surfaces.py -q
```

Result: **11 files, 199 passed, 0 failed in 3.9s**.

The new suite is pinned into the base workflow merge gate. The gate first
failed as intended because the new file was absent from its explicit inventory.
After the load-bearing inclusion and exact assertion were added:

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_workflow_merge_gate.py -q
```

Result: **1 file, 49 passed, 0 failed in 47.0s**.

The same gate also reproduced that the Phase 5 sealed-runtime ledger entry
contained prose in `owned_symbols`. Each sequential checker failure was
replaced with its traced exact identifier; no invariant, ownership scope, or
`last_verified_upstream` value was advanced. The standalone customization
checker then exited zero.

Final focused ledger/gate verification:

```bash
HERMES_TEST_FILE_RETRIES=0 HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py -q
```

Result: **2 files, 324 passed, 0 failed in 85.8s**.

## Bounded defensive review

The review was performed once over `1373c306..d6960baa` and found no new
candidate defect:

- **Trust-boundary ordering:** exact six-field credential-free identity is
  checked before mutable runtime setup (`plugin_agent_worker.py:1500-1559`),
  rechecked after constrained credential resolution (`:1682-1720`), and held
  privately by `AIAgent` through transport.
- **URL credentials and diagnostics:** public capability routes include only
  bounded display identifiers and omit endpoint/provenance values
  (`plugins/workflow/provider_authority.py:343-405`). Attempt evidence selects
  a closed field set and never projects the private runtime identity
  (`plugins/workflow/evidence.py:420-466`). The canary test covers capability,
  evidence, notification, REST, Desktop-backend serialization, and retirement
  logging.
- **Alias provenance:** loader-authored provenance participates in both
  canonical and alias arbitration; canonical tokens cannot be shadowed and
  all losing paths emit bounded codes (`providers/__init__.py:284-350`).
- **Cache identity:** the compatibility digest binds all model-visible and
  provider-dependent semantics but deliberately excludes node ID
  (`execution_semantics.py:89-215`). Node-bound intended authority remains
  separate.
- **Budget and attempt ownership:** fallback and repair retain the same private
  cost and provider-attempt authorities. Reservations verify prefix,
  cancellation, deadlines, and the shared attempt grant before transport
  (`plugin_agent_worker.py:2021-2173`, `:2210-2293`). They do not reset budget
  or attempts.
- **MCP teardown:** worker-owned hooks, callbacks, session DB, inline tool,
  MCP servers, loaders, tool-search config, and timeout overrides unwind in
  nested `finally` blocks (`plugin_agent_worker.py:2635-2670`).
- **Cancellation:** cancellation is checked before attempt reservation and
  fallback; child execution receives the same cancellation event. Sealed
  credential publication is serialized against interrupt in `AIAgent`.
- **Crash recovery:** scheduler reconstruction accepts the identity triple
  only from exactly one succeeded attempt with three lowercase SHA-256 values
  (`plugins/workflow/scheduler.py:1945-2000`). It never resolves live config.

## Compatibility and redaction proof

- Normalizer v5 and snapshot format 2 remain unchanged. The internal provider
  authority/resolver schema boundary remains the previously approved v5-only
  schema 2; no v1-v4 snapshot activation changed.
- The closure includes Phase 4 defensive invariants and proves shared reuse
  crosses node IDs only when all cache semantics match.
- `allowed_tools: []`, skills-as-current-user-turn, bounded inline authority,
  MCP isolation/teardown, dormant budget/sandbox behavior, and backend-authored
  Desktop projections remain covered by the 199-test closure.
- The public-canary test supplies a credential, credential-bearing URL and
  query, endpoint and registration digest values, prompt, command, provider
  payload, feedback, and temporary absolute path. None appears in the tested
  public projections or candidate-safe retirement log.

## Residual risks and pending gates

- Provider discovery fixtures retain registry/probe module state within a
  single test file. Unique provider slugs plus per-file process isolation
  prevent observed cross-file contamination; this remains the deferred
  non-blocking Minor from the Task 4 review.
- An explicitly installed user provider plugin may legitimately register a
  canonical provider named `auto`. It remains non-bundled and fail-closed for
  native capability trust. Ledger wording that “unregistered `auto` remains
  unresolved” applies only when no such registration exists.
- Task 5 did not rerun the full Python suite, installed-wheel integration,
  complete Desktop suite, upstream rehearsal, or external-state-audited
  branded rehearsals. Those are Task 6 gates and must pass at the final exact
  candidate before the 2026-08-07 BLOCK can be cleared.
- This review ran on macOS. Native Windows and Linux coverage remains delegated
  to the established CI and Task 6 portability/rehearsal gates.

## Preservation state

- Feature branch: `feat/workflow-language-phase-5-provider-portability`.
- Pre-Task-5 HEAD: `d6960baa096be2d5d0cd66e5e8c7c69e04037046`.
- `base` and `origin/base`: `cff7875049a7f369c2eae758503c63b6467c4433`.
- `origin/otto`: `3cf9a3a89f01133ceb4e0cbf79123e632bfeab5c`.
- `origin/loop24`: `6e15b6611edcf88a2bb0569beffe977e035b088f`.
- Literal `main`, brand refs, tags, releases, and release repositories were not
  modified.
- The preserved Fable review and prompt remain untracked and unchanged at
  SHA-256 `d0841822040d9bca881073df6d7ff5f601296b23c095e9c95be37d7d492d4c8e`
  and `c5ba7321addf733b1c1dbb76f1218c662ac5ad49d56cbf1b0dca7c6f3d11b528`.

## Disposition

F-1 through F-4 are **implemented and locally verified** with no new bounded
review finding. The historical `BLOCK` remains open pending Task 6's fresh
independent specification-compliance and code-quality verdicts plus the full
distribution, Desktop, upstream, and branded-release regression gates.
