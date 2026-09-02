# Stage 1 adversarial review

**Reviewer:** Codex (GPT-5)

**Date:** 2026-09-02

**Candidate:** `5cd581a64f6f87a293c747df1a48302fed5a4a22`
**Overall verdict:** **FAIL — BLOCK**

The immutable scope matched exactly. I found one critical credential-isolation defect and two important containment/liveness defects.

## Scope verification

All immutable facts matched:

| Fact | Observed |
|---|---|
| Checkout | Detached, clean |
| HEAD | `5cd581a64f6f87a293c747df1a48302fed5a4a22` |
| Candidate tree | `6fb1ace8544defb0041e1132fe7b6c91af0b4852` |
| Merge base | `13106bf39627e770bb693ec2a47a1fe701f28989` |
| Merge-base tree | `297a7e7a11994b3dfd77e65c444b67b02ae8390d` |
| Ancestry | Valid |
| Commit count | 37 |
| Changed paths | 57 |
| Diff totals | +18,535 / -181 |
| `git diff --check` | Exit 0, no output |

The five binding sources were read completely and in the required order. I did not read another review lane, reconciliation/remediation material, or `.superpowers/sdd/` progress files.

## Findings

| ID | Severity | Defect |
|---|---|---|
| HOFF-001 | **CRITICAL** | CLI fallback passes initiating-profile credentials into the destination-profile process |
| HOFF-002 | **IMPORTANT** | A dead CLI wrapper can leave a live descendant unsupervised after restart |
| WFHO-001 | **IMPORTANT** | Twenty unresolved cancellations can indefinitely starve observations and deadlines |

### HOFF-001 — CLI fallback leaks source credentials into the destination profile

1. **Defect**

   The CLI fallback starts its wrapper with the complete ambient environment:

   ```python
   env={**os.environ, "HERMES_HOME": str(self._source_home)}
   ```

   The wrapper then starts `hermes -p <destination>` without replacing that environment. Provider and API credentials belonging to the initiating process therefore survive into the destination process.

2. **Production locations**

   - Environment copy: [hermes_cli/handoff/local.py:961](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/handoff/local.py:961)
   - Wrapper starts destination CLI: [hermes_cli/handoff/local.py:432](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/handoff/local.py:432)
   - `-p` changes `HERMES_HOME`: [hermes_cli/main.py:787](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/main.py:787)
   - Target dotenv loading: [hermes_cli/main.py:1050](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/main.py:1050)
   - Missing dotenv values clear only behavioral keys, deliberately preserving shell-exported provider keys: [hermes_cli/env_loader.py:116](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/env_loader.py:116)
   - Secret lookup falls through to `os.environ`: [agent/secret_scope.py:149](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/agent/secret_scope.py:149)
   - Credential-pool environment fallback: [agent/credential_pool.py:3249](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/agent/credential_pool.py:3249)

3. **Violated invariants**

   Invariant 16 directly, and the credential-containment portion of invariant 6.

4. **Realistic trigger**

   On POSIX, Runs is authoritatively unavailable, so the task uses CLI fallback. The initiating gateway or shell has `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, or another provider credential exported. The destination profile does not configure that credential.

5. **Complete path**

   Workflow dispatch → CLI mechanism binding → wrapper spawned with source environment → wrapper invokes `hermes -p destination` → target dotenv contains no replacement credential → inherited provider credential remains → target credential pool consumes it.

6. **Concrete wrong result**

   The destination task authenticates and may incur usage against the source/default profile’s provider account. It should instead use only destination-profile credentials or fail closed.

7. **Synthetic reproduction**

   ```bash
   PYTHONDONTWRITEBYTECODE=1 \
   OPENROUTER_API_KEY=stage1-source-canary \
   API_SERVER_KEY=stage1-api-canary \
   /Users/coreyellis/.hermes/hermes-agent/venv/bin/python -c \
   'import os; from tools.environments.local import hermes_subprocess_env; current={**os.environ,"HERMES_HOME":"/synthetic/source"}; safe=hermes_subprocess_env(inherit_credentials=False); print({"candidate_copies_provider_key":current.get("OPENROUTER_API_KEY")=="stage1-source-canary","candidate_copies_api_key":current.get("API_SERVER_KEY")=="stage1-api-canary","existing_helper_strips_provider_key":"OPENROUTER_API_KEY" not in safe,"existing_helper_strips_api_key":"API_SERVER_KEY" not in safe})'
   ```

   Exit 0:

   ```text
   {'candidate_copies_provider_key': True,
    'candidate_copies_api_key': True,
    'existing_helper_strips_provider_key': True,
    'existing_helper_strips_api_key': True}
   ```

8. **Why current tests miss it**

   [test_local_cli.py:392](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tests/hermes_cli/handoff/test_local_cli.py:392) asserts only that `HERMES_HOME` is passed; it does not assert that provider/API credentials are absent.

   The real fallback test at [test_local_handoff_e2e.py:1056](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tests/plugins/workflow/test_local_handoff_e2e.py:1056) gives the target its own `OPENAI_API_KEY`, masking inheritance when the destination lacks a key.

9. **Minimal remediation and regression**

   Reuse the existing [hermes_subprocess_env(inherit_credentials=False)](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tools/environments/local.py:835), then explicitly set the source `HERMES_HOME` for the wrapper. Do not copy `os.environ` directly.

   Add a real POSIX fallback test with a source canary credential and no target credential. The target must neither observe nor use the canary and must fail closed. Retain a positive case proving a target-local key works.

---

### HOFF-002 — dead wrapper leaves a live descendant outside supervision

1. **Defect**

   After initiator restart, reconciliation treats a missing wrapper PID as `local_cli_process_lost` without checking or terminating the recorded POSIX process group. It then commits `indeterminate` and removes the spool even if the destination CLI descendant is still running.

2. **Production locations**

   - Wrapper starts destination child in its process group: [hermes_cli/handoff/local.py:432](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/handoff/local.py:432)
   - Observation checks only wrapper identity: [hermes_cli/handoff/local.py:984](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/handoff/local.py:984)
   - Indeterminate cleanup removes the spool: [hermes_cli/handoff/local.py:614](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/handoff/local.py:614)
   - Service calls cleanup after the fold commits: [hermes_cli/handoff/service.py:247](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/hermes_cli/handoff/service.py:247)
   - Existing process code recognizes that a process group can outlive its direct child: [tools/managed_process.py:1068](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tools/managed_process.py:1068)
   - Restart cleanup returns false once the leader PID is gone: [tools/managed_process.py:1291](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tools/managed_process.py:1291)

3. **Violated invariants**

   Invariants 6, 8, and 17.

4. **Realistic trigger**

   CLI fallback starts successfully. The initiating process restarts, losing its in-memory `ManagedProcessTree`. The wrapper subsequently crashes or is killed, while its destination `hermes` child remains alive in the recorded process group.

5. **Complete path**

   Wrapper and destination child start → initiator restarts → wrapper dies → coordinator observes no receipt → wrapper PID identity check fails → ledger becomes `indeterminate` → post-commit cleanup has no in-memory tree and unlinks the spool → destination child continues executing.

6. **Concrete wrong result**

   The descendant can continue provider calls or tool side effects after the initiator has lost supervision. The wrapper’s timeout no longer protects it, cancellation cannot reliably reach it, and no receipt can ever reconcile its result because the receipt-writing wrapper is dead and its spool has been removed.

7. **Synthetic reproduction**

   A bounded local probe killed only the wrapper and checked the recorded process group:

   ```text
   {"wrapper_identity_current": false,
    "recorded_group_still_active": true,
    "child_pid_positive": true}
   ```

   Exit 0. The probe then killed the synthetic process group itself.

8. **Why current tests miss it**

   - [test_local_cli.py:417](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tests/hermes_cli/handoff/test_local_cli.py:417) tests restart observation with a pre-existing receipt.
   - [test_local_cli.py:635](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tests/hermes_cli/handoff/test_local_cli.py:635) tests descendant cancellation through the same live channel, where the in-memory process tree still exists.
   - [test_local_cli.py:499](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tests/hermes_cli/handoff/test_local_cli.py:499) checks only that a dead/tampered PID becomes indeterminate; it leaves no live group.

9. **Minimal remediation and regression**

   On POSIX recovery, distinguish “leader absent” from “identity reused.” If the recorded leader is gone but the recorded process group still exists, terminate and wait for that group before declaring cleanup safe. Do not remove process-lost spools until group quiescence is established.

   Add a restart test that spawns a wrapper and descendant, recreates the channel, kills only the wrapper, and then reconciles or cancels. Assert the descendant is gone and the spool is retained until quiescence or a safe receipt fact.

---

### WFHO-001 — persistent cancellations starve other due work

1. **Defect**

   Maintenance selection assigns strict priorities—cancellations first, deadlines second, ordinary observations third—and truncates the result to 20. A cancellation with a durable command ID remains immediately due regardless of its future `next_observation_at`.

2. **Production locations**

   - Priority construction and `due[:max_items]`: [plugins/workflow/scheduler.py:1155](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/plugins/workflow/scheduler.py:1155)
   - Recorded cancellations treated as immediately due: [plugins/workflow/scheduler.py:1163](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/plugins/workflow/scheduler.py:1163)
   - Cancellation advancement: [plugins/workflow/scheduler.py:1205](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/plugins/workflow/scheduler.py:1205)
   - Coordinator page size of 20: [plugins/workflow/coordinator.py:434](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/plugins/workflow/coordinator.py:434)

3. **Violated invariants**

   Invariants 11 and 13.

4. **Realistic trigger**

   A run accumulates 20 assigned prompts whose destination is unavailable and whose cancellation attempts remain `cancelling` or `indeterminate`. Another handoff has already completed remotely and is due for observation, or another handoff’s deadline expires.

5. **Complete path**

   Each coordinator pass loads due waiting handoffs → all 20 persistent cancellations sort at priority 0 → page truncation excludes deadline and observation items → cancellation advancement defers them but retains their command IDs → next pass still treats all 20 as immediately due → lower-priority items remain excluded indefinitely.

6. **Concrete wrong result**

   A healthy completed result can remain unobserved forever, keeping the Workflow run `running`. A later expired deadline can likewise remain unactioned indefinitely.

7. **Deterministic selection replay**

   ```bash
   PYTHONDONTWRITEBYTECODE=1 \
   /Users/coreyellis/.hermes/hermes-agent/venv/bin/python -c \
   'rows=[(f"cancel-{i:02d}",0,100+i) for i in range(20)]+[("completed-normal",2,0)]; first=sorted(rows,key=lambda x:(x[1],x[2],x[0]))[:20]; second=sorted([(n,p,t+5 if p==0 else t) for n,p,t in rows],key=lambda x:(x[1],x[2],x[0]))[:20]; print({"first_contains_normal":any(n=="completed-normal" for n,_,_ in first),"next_contains_normal":any(n=="completed-normal" for n,_,_ in second),"selected_each_page":len(first)})'
   ```

   Exit 0:

   ```text
   {'first_contains_normal': False,
    'next_contains_normal': False,
    'selected_each_page': 20}
   ```

8. **Why current tests miss it**

   The fairness test at [test_coordinator.py:1550](/private/tmp/hermes-stage1-adversarial.qqhjQU/codex/tests/plugins/workflow/test_coordinator.py:1550) has only one cancellation and one deadline, and both terminalize on their first pass. It does not exercise a full page of persistent cancellation work.

9. **Minimal remediation and regression**

   Use bounded per-class reservation: take at least one due item from every nonempty cancellation/deadline/observation class, then fill remaining capacity by urgency. This requires no second scheduler or persistent fairness state.

   Test at least 20 unresolved cancellations plus an ordinary completed observation and an expired deadline. Across repeated pages, every nonempty class must advance within a bounded number of passes.

## Locked-invariant verdicts

| # | Verdict | Basis |
|---:|:---:|---|
| 1 | **PASS** | Canonical local endpoint parsing rejects host/userinfo/port/query/fragment/encoding/profile ambiguity; production Workflow preflight rejects self-targeting before staging. |
| 2 | **PASS** | Specs are task-mode, local/noninteractive, fingerprinted, content-bound, and conflicting stable-key reuse fails closed. |
| 3 | **PASS** | SQLite transitions, versions, lease epochs, legal phases, stale fences, and terminal immutability are transactionally checked. |
| 4 | **PASS** | Mechanism is bound before submission; attempts are durable before I/O and folds are fenced afterward. |
| 5 | **PASS** | Runs uses destination routing/key scope, disables proxies, checks loopback redirects, bounds reads, rejects malformed types/status, and applies an elapsed total deadline. |
| 6 | **FAIL** | HOFF-001 violates credential containment; HOFF-002 violates bounded process-tree containment. |
| 7 | **PASS** | Once submission may have occurred, recovery retains the bound mechanism and does not blindly resubmit or switch to CLI. |
| 8 | **FAIL** | HOFF-002 can leave an uncontrollable orphan whose destination truth cannot converge through the receipt path. |
| 9 | **PASS** | CLI/API/gateway/showcase/schedule production ingresses converge on snapshot preparation and assignment validation before run staging. |
| 10 | **PASS** | Waiting state persists handoff ID, generation, phase, version, and next observation; CAS checks reject stale generation/version and worker claims are released. |
| 11 | **FAIL** | WFHO-001 violates bounded fair selection. |
| 12 | **PASS** | Assigned execution reuses the existing renderer, authenticated predecessor outputs, structured validation, publication, and retry generation authority. |
| 13 | **FAIL** | WFHO-001 can indefinitely prevent a real expired deadline from reaching its action path. Concurrent authoritative success handling itself is preserved. |
| 14 | **PASS** | Notification uniqueness and acknowledgement include run, kind, destination, node, handoff, and generation identity. |
| 15 | **PASS** | Public evidence/projections are bounded and closed/redacted; private restart spec remains in the profile-local private database. |
| 16 | **FAIL** | HOFF-001 allows source/default credentials to reach the destination process. |
| 17 | **FAIL** | HOFF-002 can unlink the spool while a destination descendant remains alive and unsupervised. |
| 18 | **PASS** | Operator commands resolve the profile-local store, preserve command IDs, return bounded machine-readable errors, and perform only idempotent one-step advancement. |
| 19 | **PASS** | No core tool-schema or `message_agent` changes; ordinary prompt path remains separate; Bot Chat defaults remain unchanged. |
| 20 | **UNPROVEN** | Static package discovery and CLI/plugin registration look correct, but installed-wheel execution could not run because no available interpreter had pytest. No premature Stage 2 surface was found. |

## Verification ledger

### Immutable Git commands

All exited 0:

```bash
git status --short --branch
git rev-parse HEAD HEAD^{tree}
git rev-parse '13106bf39627e770bb693ec2a47a1fe701f28989^{tree}'
git merge-base 13106bf39627e770bb693ec2a47a1fe701f28989 HEAD
git merge-base --is-ancestor 13106bf39627e770bb693ec2a47a1fe701f28989 HEAD
git rev-list --count 13106bf39627e770bb693ec2a47a1fe701f28989..HEAD
git diff --check 13106bf39627e770bb693ec2a47a1fe701f28989..HEAD
git diff --name-status 13106bf39627e770bb693ec2a47a1fe701f28989..HEAD
```

`git diff --check` produced no output.

### Required Python command

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_handoff_cmd.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/tools/test_bot_turn_lock.py \
  tests/tools/test_bot_relay_windows_paths.py \
  tests/tools/test_bot_mode_dm.py \
  tests/gateway/test_api_server_run_idempotency.py
```

**Exit 1 before collection:**

```text
▶ skipping venv without pytest: /Users/coreyellis/.hermes/hermes-agent/venv
error: no virtualenv with pytest found in .../.venv or .../venv,
       and HERMES_PYTHON is not a python with pytest
```

A broader plan-targeted selection and the installed-distribution smoke command failed identically before collection.

Consequences:

- No pytest test ran.
- No pytest skips or warnings were produced.
- No retries occurred; `HERMES_TEST_FILE_RETRIES=0`.
- The failures were environment/tooling failures, not passing or failing test results.
- I did not install dependencies or use the network.

## Limitations and final status

- Host: `Darwin`. Native Windows behavior is **UNPROVEN**; static inspection shows CLI fallback is rejected there.
- Linux-specific native execution was not available.
- Installed-wheel runtime registration is **UNPROVEN** because the required test environment lacked pytest.
- One initial here-document probe was blocked by read-only temporary-file creation before Python ran; it was repeated safely with `python -c` and is not relied upon otherwise.
- No production code, tests, generated files, Git state, branches, refs, or worktrees were modified.

Final clean-status proof:

```text
## HEAD (no branch)
5cd581a64f6f87a293c747df1a48302fed5a4a22
6fb1ace8544defb0041e1132fe7b6c91af0b4852
```

Final `git diff --check` exited 0 with no output.
