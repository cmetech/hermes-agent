# Workflow Language Phase 5 adversarial remediation review

**Review date:** 2026-08-09

**Original candidate:** `1373c306061d2f4a2cf1dd313df16f6453fa1939`

**Remediation range reviewed:** `1373c306061d2f4a2cf1dd313df16f6453fa1939..5fa4f998c5eeda65d6abdfb7eb42c144c924b487`

**Current verdict:** `GO — remediation, convergence review, and final non-publishing gates complete`

## Status and history

The pre-adversarial GO in
`2026-08-06-workflow-language-phase-5-validation.md` remains valid evidence
for the exact candidate it tested, but it was superseded on 2026-08-07 by the
Fable adversarial review. That review reproduced two High and two Medium
defects and correctly issued `BLOCK`. This document does not erase or relabel
that history.

All four findings now have implementation and regression coverage. A later
cross-domain convergence review deliberately widened the inspection to public
projection, provider identity, request construction, durable recovery, and
zero-call boundaries. Its six confirmed defects and two immediate sibling
defects were fixed and independently re-reviewed to GO before any final full
or release gate ran. Task 6 then completed the full distribution, Desktop,
upstream, and branded-rehearsal gates at the exact immutable candidate above.

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

## Final convergence review closure

The first bounded finding review was not treated as the end of discovery. A
frozen cross-domain review panel inspected the complete candidate at
`2938a4bee2d511b731697a123eb30f857d458c3e` and returned one Critical, two
High, and three Medium defects:

| Finding | Closure |
|---|---|
| Cross-scope Desktop notification leasing and receipts | `95235782c` makes lease, acknowledgement, failure, dismissal, retry, reset, and pruning scope-bound while retaining explicit unrestricted local administration. |
| Replayable inline-child approval authority | `07ea302cb` moves outward action consumption to one authenticated request-tree broker so fallback and inline children cannot replay a copied scalar. |
| Released recovery with absent/malformed execution authority | `6042a8a74` validates the complete persisted authority before reconstructing grants and fails closed without transport or projection mutation. |
| Structured-output catalog response-model mismatch | `95235782c` aligns the closed backend projection and REST model while preserving unknown-field rejection. |
| Equivalent endpoint spellings produced route-fingerprint drift | `07ea302cb`, then sibling repair `257cfd287`, canonicalize endpoint case, default ports, query ordering, and slash placement before identity construction. |
| Prelaunch deadline expiry consumed an attempt | `6042a8a74` records complete zero-effect metadata and preserves the durable attempt ledger at all five pretransport boundaries. |

The bounded re-review at `4e852ea7d` returned GO for public surfaces and
execution authority, and found two Important identity siblings: a cleared
fallback scalar could still consume through its inherited broker descriptor,
and `/v1/?q=x` differed from `/v1?q=x`. `257cfd287` fixed both. The identity
reviewer then returned GO with no Critical or Important finding. The three
independent correctness/compatibility domains were therefore all GO before
the final full validation pass.

The subsequent workflow gate exposed four concrete regression classes rather
than new architecture defects: closed error-code projection expectations,
missing bounded `payload_truncated`, positional `NodeExecutionContext`
compatibility, and overly broad typed-publication IDs. `460c329ec` and
`a18c66081` closed them; `4b5a1ca78` completed the merge inventory. The first
full Python run then found two stale test fixtures and one non-reproducing
process-reaping timing failure. `5fa4f998c` corrected the fixtures by building
a complete sealed budget identity and exercising the relay boundary on a real
uninitialized `AIAgent`. The process test passed independently and in the
final full run.

Per explicit user direction, no separate threat-focused review was run. This
does not replace or weaken the completed correctness, compatibility,
isolation, redaction, package, Desktop, upstream, or release-regression gates.

## Final exact-candidate verification

Every Python command below used `HERMES_TEST_FILE_RETRIES=0` and
`scripts/run_tests.sh`:

- Focused Phase 5 closure: **718 passed, 0 failed**.
- Complete workflow plugin suite after the final workflow fixes: **5,261
  passed, 0 failed**.
- Installed-distribution integration: **3 passed, 0 failed**.
- Merge/customization/upstream/Desktop inventory gates: **49/49, 275/275,
  104/104, and 2/2**.
- Full Python suite at `5fa4f998c`: **2,802 files, 32,995 passed, 0 failed in
  715.1s**, 14 workers.
- Exact changed-file Ruff gate: clean.

Desktop at the same candidate passed **499 files with one skipped; 4,748 tests
with two skipped**. TypeScript typecheck passed. ESLint completed with zero
errors and 162 established warnings.

The clean detached base release gate passed **3,816/3,816** selected Python
tests, **3/3** installed-distribution tests, and **173/173** Desktop release
tests. The combined upstream/descriptor rehearsal exited zero against
`origin/main`, `origin/loop24`, and `origin/otto`. Separate LOOP24 and OTTO
rehearsals recorded only passed command results, no failed ledger entry test,
`contains_tested_base: true`, and `generic_runtime_matches_base: true`.

Before/after comparisons were empty for local refs, source remote refs, brand
release-repository refs, worktrees, branch/status, tags, and release metadata.
The release JSON hashes remained:

- LOOP24: `d9938240ba634ba18b03ddad1f4cf633b5638912a7302949e848487c2b3ba187`.
- OTTO: `f8e700e7d19b1d9901cbf314b1af4edef11e00134e4b671146a1b4e98b06428b`.

The unchanged entries remain `LOOP24 Desktop v5.3.0` published
`2026-08-07T01:17:38Z` and `OTTO Desktop v5.3.0` published
`2026-08-07T01:18:00Z`. The temporary evidence directory was reviewed and
then removed; no persistent branch, ref, tag, release, or repository was
mutated.

## Residual risks

- Provider discovery fixtures retain registry/probe module state within a
  single test file. Unique provider slugs plus per-file process isolation
  prevent observed cross-file contamination; this remains the deferred
  non-blocking Minor from the Task 4 review.
- An explicitly installed user provider plugin may legitimately register a
  canonical provider named `auto`. It remains non-bundled and fail-closed for
  native capability trust. Ledger wording that “unregistered `auto` remains
  unresolved” applies only when no such registration exists.
- This review ran on macOS. Native Windows and Linux coverage remains delegated
  to established CI.

## Preservation state

- Feature branch: `feat/workflow-language-phase-5-provider-portability`.
- Exact tested candidate HEAD: `5fa4f998c5eeda65d6abdfb7eb42c144c924b487`.
- `base` and `origin/base`: `cff7875049a7f369c2eae758503c63b6467c4433`.
- `origin/otto`: `3cf9a3a89f01133ceb4e0cbf79123e632bfeab5c`.
- `origin/loop24`: `6e15b6611edcf88a2bb0569beffe977e035b088f`.
- Literal `main`, brand refs, tags, releases, and release repositories were not
  modified.
- The preserved Fable review and prompt remain untracked and unchanged at
  SHA-256 `d0841822040d9bca881073df6d7ff5f601296b23c095e9c95be37d7d492d4c8e`
  and `c5ba7321addf733b1c1dbb76f1218c662ac5ad49d56cbf1b0dca7c6f3d11b528`.

## Disposition

F-1 through F-4 and the later convergence findings are **implemented,
independently re-reviewed, and fully verified**. The historical 2026-08-07
BLOCK is cleared. Phase 5 adversarial remediation is **GO** with no unresolved
Critical or Important finding and no remaining local validation gate.
