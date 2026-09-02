# Stage 1 adversarial review reconciliation and remediation

**Date:** 2026-09-02

**Review merge base:** `13106bf39627e770bb693ec2a47a1fe701f28989`

**Immutable reviewed candidate:** `5cd581a64f6f87a293c747df1a48302fed5a4a22`

**Remediated code candidate:** `de4c584e6ac71f7329e9dfd852b18c866a584b08`

**Final disposition:** **PASS for Stage 1**

## Inputs and independence

Both reviewers received the same model-neutral prompt and reviewed separate,
detached, clean worktrees at the immutable candidate. Neither reviewer could
read the other lane or a reconciliation report.

- [Shared adversarial prompt](2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-code-review-prompt.md)
- [Codex report](2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-code-review-codex.md)
- [Claude report](2026-09-02-local-workflow-agent-handoff-stage-1-adversarial-code-review-claude.md)

Codex returned **BLOCK** with one CRITICAL and two IMPORTANT findings. Claude
returned **PASS**, but separately identified one flaky test assertion and six
candidate concerns that did not meet the prompt's proof standard. The
controller reproduced each promoted Codex finding against the real code path
before changing production code.

## Consolidated findings

| ID | Source | Severity | Controller decision | Disposition |
|---|---|---:|---|---|
| AR-01 | Codex `HOFF-001` | CRITICAL | **Confirmed** | Fixed in `5c4032fed8fc73c9bad27151ca45e10d7236ebd1` |
| AR-02 | Codex `HOFF-002` | IMPORTANT | **Confirmed** | Fixed in `5c4032fed8fc73c9bad27151ca45e10d7236ebd1` |
| AR-03 | Codex `WFHO-001` | IMPORTANT | **Confirmed** | Fixed in `746378df1cbdbb3ddf18c2748823d2a06b33d2ce` |
| AR-04 | Claude `TI-1` | Test integrity | **Confirmed; no product defect** | Fixed in `de4c584e6ac71f7329e9dfd852b18c866a584b08` |

### AR-01 — source credentials entered the destination CLI fallback

The fallback wrapper copied `os.environ`, so a provider or API credential
exported by the initiating profile remained available after `hermes -p`
selected the destination profile. A destination without its own credential
could therefore authenticate as the source profile.

The regression test set source credential canaries and captured the exact
environment passed to `ManagedProcessTree.spawn`.

```text
RED:  0 passed, 1 failed
      OPENAI_API_KEY and API_SERVER_KEY were present
GREEN: 1 passed, 0 failed
```

The fix reuses the existing
`hermes_subprocess_env(inherit_credentials=False)` boundary and adds only the
source `HERMES_HOME` needed by the wrapper. Destination credential resolution
still occurs inside the destination profile.

### AR-02 — restart reconciliation could abandon a live CLI descendant

After an initiator restart, a dead wrapper leader caused an immediate
`indeterminate` fold even when its recorded POSIX process group still contained
the destination child. Cleanup then removed the spool while that child could
continue provider calls or tool side effects without supervision.

The controller reproduced the failure with a real wrapper and descendant,
discarded the initiating channel's in-memory process tree, killed and reaped
only the wrapper, reopened the durable store, and reconciled.

```text
RED:  0 passed, 1 failed, 1 host-specific skip
      descendant remained live after the indeterminate fold
GREEN: 1 passed, 0 failed, 1 host-specific skip
      descendant process group was terminated
```

A second regression proved that process-lost spool bytes are retained until
the recorded group is known to be quiescent:

```text
RED:  0 passed, 1 failed
GREEN: 1 passed, 0 failed
```

The minimal fix extracts the already-existing POSIX group termination logic
inside `ManagedProcessTree`, permits its guarded use after the recorded leader
is absent, and defers spool deletion while group liveness remains ambiguous.
Invalid, own-process, reused-leader, Windows, and permission-error cases remain
fail-closed. Nonpositive group identifiers gained their own red/green contract
(`1 passed, 2 failed` before the guard; `3 passed, 0 failed` after it).

### AR-03 — persistent cancellation rows starved other handoff maintenance

Cancellation commands remain due until destination truth converges. With 20
such rows and a page size of 20, strict priority plus truncation excluded an
expired deadline and an ordinary observation on every pass.

The regression used the real `RunStore`, coordinator fence, 20 durable
cancellation commands, one expired deadline, and one due observation.

```text
RED:  0 passed, 1 failed
      expired deadline and observation were absent from the selected page
GREEN: 2 passed, 0 failed
      new persistent-page regression and prior ordering contract both passed
```

The fix reserves one slot for each nonempty cancellation, expired-deadline, and
ordinary-observation class when a truncated page has capacity, then fills the
remaining slots using the existing priority and urgency order. It adds no
scheduler, cursor, configuration, or persistent fairness state.

### AR-04 — cancellation E2E asserted an unrelated provider retry count

Claude observed one failure in seven runs of the cancellation E2E. The handoff
still created exactly one keyed destination Run, stopped that Run, and converged
truthfully to `cancelled`; only the pre-existing stream retry raced with the
interrupt and invoked the fake provider twice.

The assertion on `len(provider.calls) == 1` was removed. The test retains the
load-bearing destination Run identity, keyed-admission, stop, ledger, and
terminal-state assertions.

After the change, the test passed **7 of 7** independent executions with
`HERMES_TEST_FILE_RETRIES=0`.

## Claude candidates not promoted

| Candidate | Decision | Reason |
|---|---|---|
| Definitive failed handoff permits a policy-authorized retry generation | Design-conforming | The accepted Stage 1 plan explicitly assigns generation authority after a definitive outcome and an allowed Workflow retry. |
| Foreground execution parks at `waiting_handoff` | Design-conforming | The foreground lease is released for coordinator adoption; this is the existing durable foreground-to-background wait behavior. |
| Reconcile may replay the keyed Runs POST after cancellation | Design-conforming | The same idempotency key is required to discover a possibly admitted Run ID so that the destination can then be stopped; no blind resubmission occurs. |
| Remote `stopping` without a local cancel fact is not projected | Safe bounded behavior | The observation is not allowed to invent local cancellation authority, and the scheduler floors the next attempt at five seconds. |
| A dismissed notification is not resurfaced for the same handoff generation | Design-conforming | Notification identity intentionally includes the exact handoff and generation; dismissal acknowledges that identity. |
| Notification migration rescans nullable non-handoff rows | No proved defect | The path is outside the hot loop and no wrong result or violated performance bound was demonstrated. |

Claude also noted two non-defects: omitting raw CLI stderr is a stricter
evidence-containment choice than the plan wording, and the test runner can
false-green in a worktree without pytest. The controller used the repository
`.venv`, observed real collection counts, and made no Stage 1 change for either.

## Remediation commits

| Commit | Exact responsibility |
|---|---|
| `5c4032fed8fc73c9bad27151ca45e10d7236ebd1` | Strip initiator credentials; terminate safely identifiable orphaned POSIX groups; retain spool until quiescence; add real and focused regressions. |
| `746378df1cbdbb3ddf18c2748823d2a06b33d2ce` | Reserve bounded handoff-maintenance capacity across due-work classes; add the persistent cancellation page regression. |
| `de4c584e6ac71f7329e9dfd852b18c866a584b08` | Remove the provider retry race assertion while retaining product-level cancellation proofs. |

## Verification

All Stage 1 verification below ran against the remediated code candidate with
test-file retries disabled.

### Complete affected files

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_local_cli.py \
  tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_local_handoff_e2e.py -q
```

Result: **166 passed, 0 failed, 7 host-specific skips** across four files.

### Exact adversarial Stage 1 gate

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff \
  tests/hermes_cli/test_handoff_cmd.py \
  tests/hermes_cli/test_peer_cmd.py \
  tests/hermes_cli/test_plugin_background_services.py \
  tests/tools/test_bot_turn_lock.py \
  tests/tools/test_bot_relay_windows_paths.py \
  tests/tools/test_bot_mode_dm.py \
  tests/gateway/test_api_server_run_idempotency.py \
  tests/gateway/test_api_server_runs.py \
  tests/plugins/workflow/test_local_handoff_e2e.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_notifications.py -q
```

Result: **1,281 passed, 0 failed, 7 host-specific skips** across 19 files.

### Installed distribution

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration -k \
  extracted_wheel_registers_workflow_cli_from_a_clean_home -q
```

Result: **1 passed, 0 failed, 5 deselected**.

### Static checks

`ruff check` passed for every changed Python file. It emitted one pre-existing
invalid-`noqa` warning at `tools/managed_process.py:969`. `git diff --check`
also passed.

### Additional whole-Workflow diagnostic

An extra, non-required `tests/plugins/workflow -q` run with retries disabled
finished with **5,919 passed, 1 failed, 5 skipped**, plus one macOS process bus
error after all four tests in `test_scheduling_middleware_e2e.py` had printed as
passed. The single assertion failure was the previously documented
load-sensitive hard-maximum recovery test; it passed immediately in isolation
(**1 passed, 0 failed**).

The bus error reproduced with the middleware file alone after its tests
completed. Its fatal stacks are in SQLite initialization from long-lived web
application background threads. The middleware test, web-server lifecycle,
hosted-room store, and session schema are all unchanged from the Stage 1 merge
base. This is an unrelated macOS test-lifecycle defect and does not contradict
the green, required Stage 1 gate. It remains separate follow-up work; no
speculative Stage 1 change was made for it.

## Residual platform coverage

The new real orphan-process test ran and passed on macOS. Its Linux twin was
host-skipped locally and remains assigned to the Linux CI lane. Windows CLI
fallback remains disabled by Stage 1 design; Windows-marked cases remain
assigned to the Windows lane.

## Final disposition

All three proved Stage 1 product defects and the one proved test-integrity
defect are resolved. No rejected candidate was silently converted into scope,
and no Stage 2 surface was added. The remediated Stage 1 candidate passes its
required adversarial and installed-distribution gates.
