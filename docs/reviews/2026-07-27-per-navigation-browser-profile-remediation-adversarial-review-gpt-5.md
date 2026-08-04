# Per-navigation browser profile remediation — adversarial review (GPT-5)

**Date:** 2026-07-27  
**Verdict:** **DO NOT SHIP** to `base` or any branded release branch

## 1. Scope, immutable refs, and platform

I reviewed the remediation at the pinned commit, not a branch name:

| Meaning | Ref reviewed |
|---|---|
| Approved design parent | `619ef6ea42d6b3c3873e6426ec612e48a110e116` |
| Rejected implementation | `3a458156c52037d85943e6ee5fb6f5ac05e221b8` |
| Remediation tip | `1ad34e42d442b6ba6fc928db9310f18f7a54d7e2` |
| Fork point | `f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a` |
| Current `base` used for merge simulation | `a34a50875b4b913af10226b8e6a10be883457a2a` |

The remediation range is exactly 25 commits and 26 files, with 5,231 insertions and 186 deletions. The fork-to-`base` range is exactly 54 commits. The merge base of the remediation tip and current `base` is the stated fork point.

Review and mutation work ran in detached worktrees. The shared checkout remained on `base`; its pre-existing modified and untracked review documents were not changed. The only shared-repository write from this review is this document.

Platform: macOS Darwin 25.5.0, arm64, Python 3.11.15, pytest 9.0.2 through `scripts/run_tests.sh`.

## 2. Executive verdict

The remediation correctly fixes many of the narrow regressions it names, and its focused tests are generally mutation-sensitive. It is nevertheless unsafe to merge:

1. `browser.cdp_url` remains an unguarded process-wide path to the enrolled browser. A bare session then loads attacker-controlled content with the corporate CDP endpoint while all enrolled-only SSRF guards are disabled for that key. This is a reproduced SSO/client-certificate disclosure boundary failure.
2. Exhausting alternate debug ports makes `find_free_debug_port()` return an enrolled port it deliberately skipped, without checking that fallback. Both connect surfaces can then discover and globally adopt an already-running corporate browser.
3. Different enrolled keys can concurrently run process-global `agent-browser close --all`. The second hygiene run can occur after the first browser has launched but before its `_active_sessions` record is published.
4. End-of-task cleanup can return while a racing enrolled acquire is in progress; the acquire then republishes the memo, handle, and registry authority after cleanup.

The first two paths can give untrusted content the corporate browser's live identity. They are release blockers independent of the green unit suite and clean textual merge.

## 3. Per-original-finding disposition

`MUTATION CAUGHT` means I changed the final production condition back toward the rejected behavior, ran the named test through `scripts/run_tests.sh`, observed the expected failure, and restored the file. `MUTATION MISSED` means the claimed enforcement did not notice removal of a load-bearing declaration.

| ID | Status | Final-state evidence | Mutation evidence |
|---|---|---|---|
| EBL-001 | **MOVED** | The agent does pass `attach_global=False` at `tools/browser_tool.py:708`, so the enrolled acquire itself no longer writes the environment. However, `browser.cdp_url` independently supplies the same enrolled endpoint to a bare key; see CRIT-001. | Changed the agent call to `attach_global=True`. Both `test_agent_acquire_does_not_write_the_global` and `test_ephemeral_task_never_inherits_the_corporate_endpoint` failed. The original fix is covered, but the authority leak survives through another input. |
| EBL-002 | **MOVED** | Normal per-navigation routing and profile resolution are correct: trusted origins receive `::enrolled`, public/untrusted origins remain bare, and default-profile inheritance is restricted to enrolled keys. An active `browser.cdp_url` short-circuits that routing at `tools/browser_tool.py:1562-1563`, restoring the same result. | Changed the trusted-origin branch to return the bare task id. `test_trusted_origin_routes_to_enrolled` failed. The intended route is covered; CRIT-001 bypasses it before the route executes. |
| EBL-003 | **FIXED for the original same-key race; new cross-key race remains** | The per-key lock spans miss, acquire, and publish, with a double-check. | Replaced the shared per-key lock with a fresh lock per call. `test_concurrent_misses_acquire_once` failed with two acquires. HIGH-003 is a distinct different-key race around process-global hygiene. |
| EBL-004 | **FIXED under the stated local-process trust model** | Duplicate enrolled ports are rejected. Both pre-existing-listener reuse and post-launch readiness require the `DevToolsActivePort` target to match the live browser WebSocket target. Missing, late, malformed, wrong-port, and wrong-target files fail closed. | Removed the identity predicate from the reuse gate. `test_unproven_listener_is_not_reused` failed. Existing proof tests also cover the post-launch gate. A same-user process that can write the profile directory and serve the port can spoof the proof; that actor generally already has the user's filesystem authority, so I record it as residual risk rather than a new remote escalation. |
| EBL-005 | **PARTIALLY FIXED** | Sequential cleanup retains and releases the `BrowserSession`, expands a bare task to an existing enrolled sidecar, and unbinds it. Cleanup does not coordinate with an acquire that has not yet published `_active_sessions`; see HIGH-004. | Removed enrolled-sidecar expansion. `test_bare_cleanup_reaps_the_enrolled_binding` and `test_end_of_task_cleanup_still_reaps_it` failed. No test covers cleanup while acquire is blocked before publish. |
| EBL-006 | **PARTIALLY FIXED** | The CLI and executable checks are now ordered correctly; `_is_runnable` rejects directories and non-executable POSIX files; ports are range-checked. The data-directory check only proves that a computed string is absolute, not that the target is usable, and relative configured paths remain CWD-dependent; see MED-005. | Removed the `resolve_executable` gate. `test_unavailable_when_the_enrolled_browser_does_not_resolve` failed. A separate production-path probe showed the unmodified gate returning true for `<regular-file>/profile`, followed by `NotADirectoryError` at first acquire. |
| EBL-007 | **PARTIALLY FIXED** | The ledger now lists the previously omitted state, owners, guards, cleanup paths, and corrected per-session guidance. The checker still validates file coverage/schema, not semantic ownership completeness. | Removed `_navigation_session_key` from `owned_symbols`; `scripts/check_upstream_customizations.py` still exited 0. The content is currently adequate, but its claimed machine check cannot detect this security-significant omission. |
| EBL-008 | **FIXED** | The redirect guard evaluates the metadata floor first and then admits only the final origin trusted by the same enrolled session. | Removed the enrolled guard-forcing term from the post-redirect condition. `test_redirect_to_untrusted_private_blocked_for_an_enrolled_key` failed and returned success for the untrusted private destination. |
| EBL-009 | **FIXED, with intentionally limited recovery** | Connection-class failures evict the enrolled memo, handle, binding, and active record. The failed action is not retried; the next action reacquires, avoiding replay of non-idempotent operations. The marker set no longer treats a bare `websocket` mention as death. | Disabled the production hook while leaving the helper intact. `test_genuine_dead_endpoint_error_is_evicted_and_result_unchanged` failed. The benign-WebSocket test stayed as the false-positive guard. |
| BP-1 | **MOVED / PARTIALLY FIXED** | The shipped enrolled default moved from 9222 to 9333, and both CLI and RPC reject configured enrolled ports. `browser.cdp_url` bypasses both surfaces, and the alternate-port exhaustion fallback can return a reserved port; see CRIT-001 and CRIT-002. | Changed `DEFAULT_CDP_PORT` back to 9222; `test_module_default_is_not_the_connect_port` failed. Disabled `enrolled_port_refusal`; the CLI, RPC, and reserved-default mutation tests all failed. Neither mutation suite covers the two remaining doors. |

## 4. New findings

### CRIT-001 — `browser.cdp_url` bypasses per-navigation isolation and both enrolled-port refusals

**Location:** `tools/browser_tool.py:460-481`, `tools/browser_tool.py:660-695`, `tools/browser_tool.py:1537-1564`; seed path at `optional-skills/migration/openclaw-migration/scripts/openclaw_to_hermes.py:2656-2659`.

**Violated invariants:** 1 (a bare/ephemeral key never obtains corporate CDP), 2 (untrusted content never loads in the enrolled browser), and the security outcome of BP-1.

**Failure scenario:** configure an enrolled profile on 9333 and set `browser.cdp_url: http://127.0.0.1:9333`. `_get_cdp_override()` returns it. `_navigation_session_key()` sees any override and returns the bare key before origin routing. `_session_cdp_url()` then gives that bare key the configured endpoint. Because the key is not `::enrolled`, the six enrolled guard-forcing disjuncts do not activate. A public attacker page is therefore loaded by the corporate browser with live SSO cookies and client-certificate identity.

The parked ruling that “nothing seeds it” is false. The OpenClaw migration explicitly copies `browser.cdpUrl` into `browser.cdp_url`. The field is also part of Hermes configuration and status surfaces. Deliberate operator configuration does not make loading later attacker-controlled content with corporate authority safe; users cannot be expected to infer that this global field defeats the new trust boundary.

**Executed evidence:** with the real routing helpers and only configuration/CDP-network resolution mocked:

```text
{'override': 'http://127.0.0.1:9333',
 'navigation_key': 'victim',
 'endpoint': 'http://127.0.0.1:9333',
 'enrolled_guard_active': False}
```

**Minimal safe fix:** centralize CDP override classification. A config/env override that resolves to a local enrolled profile endpoint must never be usable by a bare key. Refuse it with the same actionable authority-boundary error, or require an explicit enrolled key whose exact destination origin is trusted. Preserve the documented env-over-config precedence for ordinary throwaway/remote CDP endpoints.

**Missing regression:** set `browser.cdp_url` to the configured enrolled endpoint, navigate a public URL, and assert no bare session receives the endpoint and no global guard suppression occurs. Exercise both a hand-written config and the OpenClaw migration output.

### CRIT-002 — exhausted alternate-port search returns the reserved enrolled port it skipped

**Location:** `hermes_cli/browser_connect.py:266-294`; consumers at `hermes_cli/cli_commands_mixin.py:1907-1926` and `tui_gateway/server.py:16537-16581`.

**Violated invariants:** 1 and 2; BP-1 authority boundary.

**Failure scenario:** default connect port 9222 is occupied by a non-CDP service. A configured enrolled profile owns 9223. The search correctly skips 9223, finds the remaining candidate range unavailable, then unconditionally returns `preferred + 1` — 9223 — without reapplying the refusal or verifying a bind. If the enrolled corporate browser is already listening there, `launch_chrome_debug(9223)`/readiness discovery can observe that existing listener and both connect surfaces then publish it as the process-global `BROWSER_CDP_URL`. Subsequent public navigation runs in the corporate browser.

**Executed evidence:** forcing the one candidate to be reserved produced:

```text
{'returned': 9223, 'reserved_port': 9223}
```

This is not limited to `attempts=1`; the same unchecked fallback occurs after the default ten candidates are exhausted.

**Minimal safe fix:** never return an unchecked fallback. Continue to a bindable non-reserved candidate, ask the kernel for an ephemeral loopback port and validate it against enrolled ownership, or return a loud failure. Recheck immediately before launch because port selection is inherently racy.

**Missing regression:** reserve `preferred + 1`, make every later candidate fail dual-stack bind, and assert the result is neither reserved nor unchecked. Drive both CLI and RPC end-to-end with an already-answering enrolled endpoint on that port and assert the environment is never published.

### HIGH-003 — different-key acquires can still run `close --all` after another enrolled launch starts

**Location:** `tools/browser_session_manager.py:74-116`, `tools/browser_session_manager.py:427-463`; publication gap at `tools/browser_tool.py:699-723` and `tools/browser_tool.py:2359-2417`.

**Violated invariant:** 7 (`acquire()` lifecycle isolation); deterministic mid-session teardown.

**Interleaving:** task A and task B use different enrolled keys, so their per-key locks do not serialize. A's hygiene sees `_active_sessions` empty and completes, then A launches its browser. Before `_session_cdp_url()` returns and `_get_session_info()` publishes A into `_active_sessions`, B's hygiene also sees the table empty and runs process-global `agent-browser close --all`. B can tear down A's newly launched/in-flight daemon. The current “skip if live” predicate sees neither in-flight reservations nor memo/handle state.

**Executed evidence:** a barrier-controlled production `acquire()` path with browser launch and subprocess effects stubbed recorded:

```text
{'hygiene_calls': ['A', 'B'],
 'second_close_all_ran_after_first_launch_started': True,
 'both_acquires_finished': True}
```

The existing hygiene test covers an already-published live session, not this publication window.

**Minimal safe fix:** introduce a process-wide acquire coordinator with explicit in-flight reservations. Hygiene for one acquire must treat every other acquisition that has passed hygiene as live, and the reservation must survive until the browser session is atomically published or released on failure. A global lock released before `_active_sessions` publication is insufficient unless the reservation is visible to the next owner.

**Missing regression:** two different keys; A passes hygiene and blocks after launch; B starts acquire; assert B does not run `close --all` and does not tear down A. Include failure cleanup of the reservation.

### HIGH-004 — cleanup can return before an in-flight acquire republishes enrolled authority

**Location:** `tools/browser_tool.py:699-723`, `tools/browser_tool.py:2408-2417`, `tools/browser_tool.py:4777-4833`, `tools/browser_tool.py:4928-4935`.

**Violated invariant:** end-of-task form of invariant 1 and EBL-005's release requirement.

**Interleaving:** `race::enrolled` is blocked inside `acquire()` and is not yet present in `_active_sessions`. `cleanup_browser("race")` checks only already-active sidecars, so it cleans the bare key and returns. The acquire then binds the registry and publishes `_session_cdp_urls` and `_session_handles`; a normal `_get_session_info` caller can subsequently publish `_active_sessions` as well. The task has ended, but enrolled authority now exists after its reaper ran.

**Executed evidence:** a blocked acquire followed by real bare-task cleanup produced:

```text
{'state_when_cleanup_returned':
    {'memo': False, 'handle': False, 'binding': None},
 'state_after_racing_acquire_published':
    {'memo': True, 'handle': True, 'binding': 'corp'}}
```

**Minimal safe fix:** coordinate cleanup with the same per-key lifecycle state as acquire. A task cleanup should mark a generation/tombstone, include the enrolled key even when it is only in-flight, wait or cancel safely, and force an acquire completing against an obsolete generation to release rather than publish.

**Missing regression:** block acquire before bind/publish, invoke bare end-of-task cleanup, unblock acquire, and assert no memo, handle, active record, registry binding, or last-active binding exists. Repeat for failure and shutdown cleanup.

### MED-005 — enrolled data-directory validation is neither usable nor stable for relative configuration

**Location:** `tools/browser_session_registry.py:107-151`, `tools/browser_profiles.py:280-307`, `tools/browser_session_manager.py:294-303`.

**Affected claim:** EBL-006 availability and specific decision 9.

Two independent problems remain:

1. `default_profile_launchable()` checks only that the resolved value is nonempty and absolute. It does not establish that an existing target is a directory or that the nearest existing parent is writable/searchable. The first acquire performs the real `os.makedirs()` and can fail deterministically.
2. A configured relative value is passed to `os.path.abspath()`, anchoring it to the process CWD. It is syntactically absolute afterward but changes between classic CLI, `hermes serve`, Desktop, and any launcher with a different CWD. `${HERMES_HOME}` and empty values are stable; arbitrary relative values are not.

**Executed evidence:** the unmodified gate accepted `<temporary-regular-file>/profile`, then first acquire raised `NotADirectoryError`. Resolving `relative-profile` from two CWDs returned two different absolute paths (`stable: False`).

**Minimal safe fix:** reject configured relative paths or anchor all of them under `get_hermes_home()`. Validate an existing target is a directory and writable/searchable, or validate the nearest existing parent can create it. Keep acquire failure loud because the filesystem can still change after the gate.

**Missing regression:** regular-file ancestor, unwritable directory/parent, existing non-directory target, and the same relative config under two CWDs for CLI/serve/Desktop.

### MED-006 — enrolled-port refusal is hostname-blind and blocks unrelated remote CDP endpoints

**Location:** `hermes_cli/browser_connect.py:78-121`; callers at `hermes_cli/cli_commands_mixin.py:1836-1869` and `tui_gateway/server.py:16479-16497`.

**Affected decision:** 5, user-visible port behavior.

The helper accepts only an integer port. Both callers parse a hostname but discard it before the check. Consequently, a remote endpoint such as `wss://cdp.vendor.example:9333/devtools/browser/...` is refused solely because the local enrolled browser reserves 9333 on `127.0.0.1`. That remote endpoint cannot collide with the local listener. The stock no-profile reservation also makes this fail on a fresh install, while the message tells the user to change a profile that may not exist.

**Minimal safe fix:** pass the parsed hostname/address into the central check. Reserve enrolled ports only for endpoints that can name the local enrolled listener (loopback literals, localhost, and carefully resolved local aliases). Keep the refusal before any cleanup or environment mutation.

**Missing regression:** remote `https`/`wss` CDP on 9333 is permitted; IPv4, IPv6, and localhost forms of the local enrolled endpoint remain refused on both surfaces.

### MED-007 — same-key wait can exceed the browser command timeout by roughly fivefold

**Location:** `tools/browser_tool.py:699-708`; `tools/browser_session_manager.py:41-48`, `tools/browser_session_manager.py:272-330`.

**Affected decision:** 8.

The per-key lock has no timeout and encloses hygiene (15 seconds), the initial identity probe (2 seconds), spawn, and 30 readiness iterations. A slow or stateful local listener can make `_cdp_alive` approach its 2-second timeout and then make identity proof approach another 2 seconds on each iteration. Including the 0.5-second sleep, the path can approach about 152 seconds before raising. A same-key caller waits for all of this before it even reaches `_run_browser_command`, whose default command timeout is 30 seconds. This is bounded, but the bound is not the user-visible command timeout and can wedge concurrent work long enough to appear hung.

**Minimal safe fix:** use one wall-clock acquisition deadline, pass remaining time into hygiene/probes/readiness, and expose a single-flight result/failure that same-key waiters can await with the same deadline. Do not merely add a lock timeout that leaves a launch running unowned.

**Missing regression:** alternating slow liveness/identity responses plus two same-key callers; both must fail within the documented acquisition/command bound with no duplicate launch.

### LOW-008 — ledger checker cannot enforce owned-symbol completeness

**Location:** `scripts/check_upstream_customizations.py:104-159`, `docs/upstream-customizations/browser-profiles.yaml`.

The ledger content is materially improved, but its checker accepts any list of nonempty symbol names. Removing the load-bearing `_navigation_session_key` declaration still exits 0. This does not currently remove runtime protection, but it leaves the highest-churn merge contract dependent on manual reviewer memory.

**Minimal safe fix:** compare changed Python definitions/assigned state/call-site owners in the coverage range with declared ownership, with explicit exclusions for mechanical changes, or maintain a checker fixture that removes every security-critical declared owner and must fail.

**Missing regression:** the exact `_navigation_session_key` removal mutation, plus each of the six guard owner removals.

### LOW-009 — per-key lock storage is intentionally race-safe but not actually bounded

**Location:** `tools/browser_tool.py:495-516`.

The comment's race analysis is correct: blindly popping a lock can let a waiter retain the old lock while a new caller creates and enters a different lock. However, “bounded by the number of distinct session keys the process ever sees” is not a fixed bound in long-running `hermes serve`; task identifiers can grow without limit. This is a small per-key memory leak, not a release blocker.

**Minimal safe fix:** use a ref-counted lock/single-flight entry whose count covers owner plus waiters, removing it only under the table lock after completion and zero references. A weak-reference scheme alone is insufficient unless waiters retain the same entry.

**Missing regression:** high-cardinality completed keys leave the coordinator near zero while a cleanup/acquire/waiter stress test still proves single-flight.

## 5. Verdict on the ten specific decisions

| # | Decision | Verdict |
|---|---|---|
| 1 | `browser.cdp_url` second door | **Rejected; CRIT-001.** It reproduces BP-1 and is seeded by the OpenClaw migration. “Deliberate” configuration is not an adequate authority boundary. |
| 2 | `DevToolsActivePort` identity proof | **Conditionally sound and fail-closed at both gates.** Missing/late/malformed/mismatched data never reuses or accepts readiness. It can be spoofed by a process that can write the profile directory and impersonate local CDP; that remains a local same-user trust assumption. Real managed Edge/Chrome behavior is unverified. |
| 3 | Skip hygiene while a live session exists | **The harm trade is correct, implementation incomplete.** Avoiding known cross-session teardown is preferable to clearing a wedged daemon. The gate ignores in-flight acquisitions, producing HIGH-003. A genuinely wedged daemon in a busy process now yields a bounded failure, not a deadlock by itself. |
| 4 | Per-turn cleanup spares enrolled sidecars | **Sequential behavior is correct; concurrent behavior is not.** The per-turn hook passes `keep_enrolled=True`, and normal end-of-task cleanup passes false. HIGH-004 permits post-cleanup publication. |
| 5 | Port refusal scope/message | **Security placement is correct, scope is overbroad.** Both surfaces check before cleanup/env mutation and give a useful authority explanation. Hostname-blind refusal blocks unrelated remote endpoints and the stock 9333 message can reference a nonexistent profile; see MED-006. |
| 6 | Six guard disjuncts | **All six current tests are mutation-sensitive.** Independently deleting the helper, sensitive-query, pre-navigation, redirect, snapshot, or vision disjunct failed its named enrolled test. Metadata stayed first in inspected conditions. CRIT-001 bypasses all six by retaining a bare key. |
| 7 | Deliberately unpruned key locks | **Race reasoning accepted; growth claim qualified.** Naive prune is unsafe, but lifetime cardinality is unbounded in a server. LOW-009. |
| 8 | Lock held across slow acquire | **Bounded but operationally excessive.** Worst-case acquisition can approach ~152 seconds before a 30-second browser command begins; MED-007. No deadlock was found in the lock ordering itself. |
| 9 | `${HERMES_HOME}` expansion | **Token/empty paths fixed; arbitrary relative paths not fixed.** Token expansion uses the resolver correctly across surfaces. Relative configured paths still depend on CWD; MED-005. |
| 10 | Test quality | **Substantially improved, not complete.** All six SSRF mutations and the main original fixes were caught. Missing interaction tests allowed CRIT-001/002 and HIGH-003/004. The customization checker missed removal of a critical owned symbol. |

## 6. Highest-risk concrete reproductions

All snippets below were executed at the remediation tip with destructive external effects stubbed below the production decision point.

### 6.1 Config override gives a bare key the corporate endpoint

Configuration: `browser.cdp_url=http://127.0.0.1:9333`; public destination `https://attacker.example/`.

```text
override             = http://127.0.0.1:9333
navigation key       = victim
session endpoint     = http://127.0.0.1:9333
enrolled guard active= False
```

### 6.2 Reserved fallback is returned after being skipped

```text
preferred=9222, attempts=1, refusal(9223)='reserved'
find_free_debug_port(...) -> 9223
```

The default ten-attempt path has the same final `return preferred + 1` after exhaustion.

### 6.3 Cross-key hygiene runs after another launch begins

```text
A: hygiene sees [] -> close --all -> starts launch -> blocks
B: hygiene still sees [] -> close --all
observed hygiene calls: ['A', 'B']
```

### 6.4 Cleanup loses a race with acquire publication

```text
cleanup_browser('race') returned:
  memo=False, handle=False, binding=None
blocked acquire then completed:
  memo=True, handle=True, binding='corp'
```

## 7. Areas that resisted attack

- **Normal per-navigation routing:** with no global override, a trusted exact origin routes to `task::enrolled`; public and unlisted private origins remain bare. Explicit ephemeral binding wins over the default and does not gain enrolled trust.
- **Metadata floor and origin scoping:** pre-navigation, redirect, snapshot, vision, sensitive-query, and eval paths all kept cloud metadata absolute and admitted only the exact configured origin. Wildcards remain strict subdomain matches.
- **Six SSRF guard sites:** each independent mutation failed the intended test; this closes the prior review's “five tests never exercise their site” problem.
- **No silent browser fallback:** failed/unresolvable enrolled acquire raises; failures are not cached. Removing the routing branch or executable check failed tests.
- **Same-key single-flight:** replacing the shared key lock with fresh locks caused two acquires and failed the concurrency regression.
- **Endpoint identity:** foreign listener reuse and post-launch readiness both require profile-dir proof. Wrong target, port, missing file, bad JSON, and missing WebSocket URL fail closed.
- **Sequential cleanup/toggle-off:** a normally published enrolled sidecar is released and unbound by bare end-of-task cleanup. Per-turn cleanup preserves it intentionally and keeps last-active routing coherent.
- **Dead endpoint behavior:** a genuine transport-death marker evicts all tracked authority without retrying the failed action; an ordinary message containing `websocket` does not evict.
- **No launch on close:** cleanup/profile predicates are pure; closing an absent session does not call acquire.
- **Suffix audit:** production `::local` special cases are confined to `tools/browser_tool.py`; `_bare_task_id_for_session_key`, `force_local`, navigation, action routing, and cleanup have explicit enrolled handling. I found no remaining production site that strips only `::local` while treating `::enrolled` as bare.
- **Protected upstream suites:** none of the eleven named SSRF/hybrid/profile tests is modified in the remediation range. The browser-selected suite passes them unedited.

## 8. Verification evidence

### 8.1 Inspection and repository checks

| Command/check | Result | Kind |
|---|---|---|
| `git status --short --branch` in shared checkout | `base...origin/base [ahead 58]`; pre-existing one modified and six untracked review docs recorded and preserved | Inspection |
| `git cat-file -e` for rejected and remediation refs | Both exist | Inspection |
| remediation `git diff --shortstat` / commit count | 26 files, +5231/-186; 25 commits | Inspection |
| `git rev-list --count fork..base` | 54 | Inspection |
| `git merge-base base remediation` | exact fork `f61b8adb...` | Inspection |
| protected-test `git diff --name-only` | No output for all eleven named protected tests | Inspection |
| full source-of-truth documents and ignored progress ledger | Read completely | Inspection |

An initial detached-worktree test attempt selected the fallback venv at `~/.hermes/hermes-agent/venv`, which lacked pytest and failed collection. I then linked the repository's existing `.venv` into the detached worktrees, as permitted by `AGENTS.md`, and reran. The collection failure was an environment-selection issue, not product evidence.

### 8.2 Clean execution at remediation tip

| Command | Result |
|---|---|
| `scripts/run_tests.sh tests/tools/ -k browser` | **767 passed, 0 failed**, 351 files |
| `scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py` | **66 passed, 0 failed** |
| grouped runner invocation for `test_browser_enrolled_launch.py`, `test_browser_enrolled_port_guard.py`, `test_browser_session_manager.py` | **76 passed, 0 failed** (24 + 17 + 35) |
| `scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml` via repository `.venv` | exit 0, no output |
| config-override production probe | bare key received 9333, enrolled guard false |
| alternate-port fallback probe | returned reserved 9223 |
| barrier-controlled cross-key acquire probe | second hygiene ran after first launch began |
| cleanup/acquire race probe | memo/handle/binding appeared after cleanup returned |
| data-directory availability probe | gate true; first acquire `NotADirectoryError` |
| two-CWD relative-path probe | two different absolute directories; `stable=False` |

### 8.3 Mutation execution

Every production mutation was restored; the mutation worktree ended clean.

| Mutation | Observed result |
|---|---|
| agent acquire `attach_global=False -> True` | 2 targeted failures |
| trusted navigation returns bare key | 1 targeted failure |
| per-key lock replaced with fresh lock | 1 targeted failure; two acquires observed |
| identity proof removed from reuse | 1 targeted failure |
| enrolled sidecar removed from bare cleanup | 2 targeted failures |
| executable gate removed | 1 targeted failure |
| ledger `_navigation_session_key` owner removed | **checker still passed** |
| redirect enrolled disjunct removed | 1 targeted failure |
| dead-endpoint hook disabled | 1 targeted failure |
| default enrolled port `9333 -> 9222` | 1 targeted failure |
| enrolled-port refusal disabled | 3 targeted failures across helper/CLI/RPC |
| eval/helper guard disjunct removed | targeted enrolled-guard failure |
| sensitive-query guard disjunct removed | targeted enrolled query failure |
| pre-navigation guard disjunct removed | targeted enrolled private-navigation failure |
| redirect guard disjunct removed | targeted enrolled redirect failure |
| snapshot guard disjunct removed | targeted enrolled snapshot failure |
| vision guard disjunct removed | targeted enrolled screenshot failure |

## 9. Merge-risk assessment against `base` at `a34a50875`

I created a detached worktree at current `base` and ran `git merge --no-commit --no-ff 1ad34e42...`.

- The merge completed with no textual conflicts.
- `git diff --cached --check` passed.
- On the merged tree, `scripts/run_tests.sh tests/tools/ -k browser` passed **767/767**.
- On the merged tree, `scripts/run_tests.sh tests/test_tui_gateway_server.py -k browser` passed **22/22**.
- On the merged tree, CLI browser-connect plus brand-default seed tests passed **33/33**.
- The customization checker passed on the merged tree.

The current `base` advancement therefore does not create a newly observed semantic conflict in `agent/chat_completion_helpers.py` or `tui_gateway/server.py`. The helper continues to spare enrolled sidecars only during per-turn cleanup, while the final task path uses ordinary cleanup. The RPC guard remains before cleanup/environment publication.

Merge durability is still only partly automated. The ledger's prose and owner list are currently detailed enough for a careful union merge, and the six security guard tests would catch their individual loss. The checker cannot detect deletion of `_navigation_session_key` from the ownership contract, and no existing suite catches the four interaction defects in this review. A clean merge is therefore not proof of a safe merged result.

## 10. Residual and hardware-dependent risks

- No real managed corporate Chrome or Edge was launched during this review. Client-certificate presentation, SSO persistence, browser-product behavior, and Conditional Access remain unverified on the target machine.
- Native Windows remains unverified: detached process flags, `npx.cmd`, Edge single-instance forwarding, profile locking, `DevToolsActivePort` timing/removal, antivirus/EDR interference, IPv6 loopback behavior, and sleep/wake recovery all need managed-hardware testing.
- `DevToolsActivePort` is a Chromium implementation detail. The code fails closed if a managed browser version stops writing it or writes it late, but that becomes an availability failure after a potentially long wait. Test real Chrome and Edge versions and headed/headless reuse.
- A same-user local process that can alter the enrolled profile directory can forge the port/target proof. The design presently treats the user's local account and profile directory as trusted.
- Port selection remains subject to a bind-to-launch TOCTOU even after CRIT-002 is fixed; the launch/readiness identity check must be the final authority, not the preliminary free-port result.
- EBL-009 intentionally recovers on the next action rather than retrying the failed one. Users may need to repeat a non-idempotent action after a crash; automatic replay would be less safe.

## 11. Release gate

Do not merge or promote until, at minimum:

1. every config/env/connect path centrally refuses or safely scopes a local enrolled endpoint for bare keys;
2. alternate-port exhaustion can never return an enrolled or unchecked port;
3. daemon hygiene accounts for different-key in-flight acquisitions through atomic publication;
4. cleanup coordinates with in-flight acquire and prevents post-task state publication;
5. regression tests reproduce those four exact scenarios and fail with the current code;
6. data-directory validation/path anchoring and hostname-aware port refusal are corrected or explicitly resolved before release; and
7. the resulting merged tree is rerun through the browser, CLI, RPC, ledger, and mutation suites, followed by managed Windows corporate-browser validation.
