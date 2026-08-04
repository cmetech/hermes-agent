# Adversarial code-review prompt — Per-navigation browser profile REMEDIATION

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to this repository:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

This reviews the **remediation** of an implementation you already rejected. It
is a review task, not an implementation task. Do not modify source, rewrite
history, advance or delete refs, push, open a PR, or disturb unrelated work.
The only authorized repository write is the final review document named in
Required output.

---

## Role

You are a hostile principal-level reviewer of Python, browser automation,
CDP/subprocess lifecycles, SSRF and origin-trust boundaries, concurrency,
filesystem race resistance, and upstream-merge preservation.

**Your job is to break the FIXES, not to re-derive the original findings.**
A prior review of `3a458156c` returned DO NOT SHIP with nine findings
(EBL-001..EBL-009). Twenty-five commits later, all nine are claimed fixed and
a further whole-branch review found and fixed one more CRITICAL. That entire
remediation was reviewed only by the author's own subagents — same model
family, same prompts, same blind spots. You are the first independent look at
it.

Assume every fix is unproven until you trace it and either reproduce the
behavior or establish the invariant from code. Mutation-check anything
security-relevant: revert the fix, confirm a test fails, restore it. Test
names, green runs, ledger entries, commit messages, and the author's own
review verdicts are not proof.

Reactive fixes are where second-order problems hide. Several of these were
written in response to a finding rather than designed: `attach_global`, the
`DevToolsActivePort` identity proof, and the daemon-hygiene skip each trade
one property for another. Attack the trades.

Praise is not useful. If an area is safe, state which code path, interleaving,
and boundary you checked.

## Repository and immutable review scope

Pin to commits. The branch name is unreliable — `base` has advanced 54 commits
underneath this work since it forked.

| Meaning | Commit |
|---|---|
| Approved design; implementation starts after | `619ef6ea42d6b3c3873e6426ec612e48a110e116` |
| **Rejected implementation** (prior review's subject) | `3a458156c52037d85943e6ee5fb6f5ac05e221b8` |
| **Remediation tip — the subject of THIS review** | `1ad34e42d442b6ba6fc928db9310f18f7a54d7e2` |
| Fork point from `base` | `f61b8adb7fe059361dbd34b9a5f1c5ce5b925b0a` |
| `base` as of this prompt (NOT merged) | `a34a50875b4b913af10226b8e6a10be883457a2a` |

Primary review range — the remediation:

```text
3a458156c52037d85943e6ee5fb6f5ac05e221b8..1ad34e42d442b6ba6fc928db9310f18f7a54d7e2
```

25 commits, 26 files, ~5,231 insertions / 186 deletions at prompt creation.
Verify those numbers.

Also review the **final state** of the full delivery, since a later fix may
have changed an earlier one's contract:

```text
619ef6ea42d6b3c3873e6426ec612e48a110e116..1ad34e42d442b6ba6fc928db9310f18f7a54d7e2
```

**Merge risk is in scope.** This has NOT been merged. `base` has gained 54
commits (a large workflow-language subsystem) since the fork. Those commits do
not touch browser files, but assess whether the merged result would be sound —
particularly `agent/chat_completion_helpers.py` and `tui_gateway/server.py`,
which this work modifies and which the workflow subsystem also touches. A
clean textual merge is not proof.

Preserve unrelated local work. Start with `git status`. Use read-only commands
or detached temporary worktrees. Do not clean, reset, stash, switch the shared
checkout, or remove worktrees you did not create. **Do not `git stash`** — an
interrupted stash in an earlier session lost work here.

## Sources of truth — read completely before reviewing code

1. `docs/reviews/2026-07-26-enrolled-browser-launch-adversarial-review.md` —
   the prior review you are checking the remediation of
2. `docs/plans/2026-07-26-per-navigation-browser-profile-design.md` — the
   approved design of record for the remediation
3. `docs/plans/2026-07-26-consolidated-browser-automation-design.md` — the
   original design
4. `docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md` —
   note §5's hard isolation rule, which the remediation is meant to honor
5. `docs/superpowers/plans/2026-07-26-per-navigation-browser-profile.md` — the
   11-task implementation plan
6. `docs/upstream-customizations/browser-profiles.yaml` — the merge ledger
7. `AGENTS.md`, `CLAUDE.md`

A local, git-ignored progress ledger may exist at
`.superpowers/sdd/2026-07-26-per-navigation-browser-profile/progress.md`. If
present it records every deferred minor and parked ruling. Read it; treat it
as claims, not evidence.

## What the remediation claims

The browser is chosen **per navigation**. An origin the enrolled profile
explicitly trusts routes to a `<task_id>::enrolled` session key and the user's
real installed browser; everything else — public pages, untrusted private
addresses — stays on the bare key and agent-browser's disposable bundled
browser.

Each prior finding and its claimed fix:

| ID | Original defect | Claimed fix |
|---|---|---|
| EBL-001 | `acquire()` wrote process-global `os.environ["BROWSER_CDP_URL"]`, so an explicitly ephemeral task inherited the corporate browser and kept it after cleanup | `acquire(..., attach_global=True)`; the agent passes `False`; endpoint carried in the session record |
| EBL-002 | `default_profile: enrolled` routed EVERY unbound session through the corporate browser, so attacker-controlled public pages loaded with live SSO cookies and machine certs | Per-navigation routing in `_navigation_session_key`; `_session_browser_profile` resolves the default profile only for an `::enrolled` key or explicit bind; `session_trusts_url` likewise |
| EBL-003 | Two threads could both miss the memo and both `acquire()`, and `acquire()` runs `close --all` hygiene | Per-key lock held across miss→acquire→publish, with a double-check |
| EBL-004 | `_ensure_enrolled_cdp` reused any listener answering on the port, binding one profile's trust to another's browser | Duplicate enrolled `cdp_port` rejected at config load; endpoint identity proven via `DevToolsActivePort` |
| EBL-005 | The returned `BrowserSession` was discarded, so `release()` never ran and the registry binding survived cleanup and toggle-off | Handle retained in `_session_handles`, released on cleanup; `cleanup_browser` reaps the `::enrolled` sidecar |
| EBL-006 | The availability gate skipped the agent-browser CLI check and accepted any existing path as an executable | Enrolled early return moved after the CLI check; `_is_runnable` requires a regular executable file (POSIX X_OK only); data dir and port validated |
| EBL-008 | The post-redirect guard ignored `_session_trusts_url`, blocking legitimate internal destinations | Trust term added last in the condition, metadata floor still first |
| EBL-009 | A dead browser's endpoint stayed memoized forever | `_DEAD_CDP_MARKERS` + `_evict_dead_enrolled_session`, evict-only, no in-place retry |
| BP-1 | (Found later, by the author's own final review.) The shipped default `cdp_port` was 9222 — the same port `/browser connect` discovers and auto-launches on — so connect attached to the corporate browser and set the global override, reinstating EBL-002 on stock config | Default moved to 9333; both connect surfaces (CLI + `browser.manage` RPC) refuse a port matching any configured enrolled profile |

Additionally: six SSRF guard sites carry
`(not _is_local_backend() or _is_enrolled_session_key(<key>))`, which is the
only thing keeping the guard active for an enrolled session once the global
env var is gone.

**Do not take this table as accurate.** Verify each claim against the diff.

## Specific decisions to attack

These are the author's judgment calls and the fix wave's own disclosed
concerns. Each is a candidate defect.

1. **`browser.cdp_url` is an unguarded second door to BP-1.** `_get_cdp_override`
   reads it with the same precedence as the env var, and
   `_navigation_session_key` returns the bare key whenever ANY override is
   live. Setting `browser.cdp_url` to the enrolled browser reproduces the full
   BP-1 leak with no refusal. Parked as "narrower than BP-1 — requires
   deliberate configuration, nothing seeds it." Judge that ruling. Is there a
   config, skill, doc, or code path that sets it?
2. **The identity proof depends on a Chromium implementation detail.**
   `DevToolsActivePort` in the profile's `user_data_dir`, matched against the
   live `webSocketDebuggerUrl`. Determine what happens if a browser stops
   writing it, writes it late, or writes it for a different port; whether the
   proof can be spoofed by anything that can write into the profile dir; and
   whether the failure is genuinely closed at BOTH the reuse gate and the
   post-launch readiness poll.
3. **`_run_daemon_hygiene` now skips `close --all` whenever any live session
   exists.** The author states a wedged daemon is no longer cleared in a busy
   process and calls the residual bounded. Assess: can this deadlock, wedge, or
   silently degrade a long-running `hermes serve`? Was the original blast
   radius (tearing down every concurrent session) actually the greater harm?
4. **Per-turn cleanup now spares enrolled sidecars** (`keep_enrolled`). Verify
   end-of-task reaping still fires — that was EBL-005's fix — and that an
   enrolled session cannot now outlive the task that created it.
5. **The port refusal is a user-visible behavior change.** `/browser connect`
   to a reserved port now fails, including 9333 on a stock install with no
   enrolled profile configured. Judge whether the refusal is correctly scoped
   and whether its message is actionable.
6. **The six guard disjuncts are the top silent-revert risk.** Dropping one
   loses origin-scoping with no build error. The author claims each is now
   covered by a named test after an earlier round found five of six had ZERO
   coverage. Mutation-check all six independently.
7. **`_session_cdp_keylocks` is deliberately unpruned.** The stated reason is
   that a naive prune reintroduces EBL-003 — popping a lock while a thread
   blocks on it lets the next caller create a fresh one and re-enter
   `acquire()`. Verify that reasoning and the growth bound.
8. **The key lock is held across a slow `acquire()`** — hygiene, identity
   probe, readiness poll, CDP resolution — estimated at ~2 minutes worst case
   with no lock timeout. Assess whether a concurrent same-key caller can be
   starved past a command timeout.
9. **`${HERMES_HOME}` expansion.** A fix routes the enrolled `user_data_dir`
   through the home resolver because the env var is not exported on the CLI
   path. Verify the persistent profile now lands in an absolute, stable
   location on CLI, `hermes serve`, and Desktop, and that no path is created
   relative to CWD.
10. **Test quality.** Nine defects in this work originated in test code, not
    production code — including a regression test that passed with the fix
    fully reverted, and a concurrency test that could pass for the wrong
    reason. Sample aggressively for tests that cannot fail, assert their own
    fixtures, or would not catch the regression they are named for.

## Non-negotiable invariants

A violation of any item is at least HIGH.

1. **A bare or explicitly ephemeral session key never obtains the corporate CDP
   endpoint**, by any path, at any time, including after cleanup.
2. **Untrusted content is never loaded by the enrolled browser.**
3. **No silent fallback to the bundled browser for a trusted origin.** An
   unresolvable enrolled browser raises.
4. **The always-blocked cloud-metadata floor is evaluated first at every guard
   site and is never trusted**, under any profile.
5. **Trust is origin-scoped.** An enrolled session reaches exactly the origins
   its profile lists.
6. **Toggle off is byte-for-byte today's behavior**, and `/browser connect` and
   `browser.cdp_url` keep their documented precedence.
7. **`acquire()` runs once per session** and a failed acquire is not cached.
8. **Closing a session never launches a browser.**
9. **The upstream browser SSRF and hybrid-routing suites pass UNEDITED.** These
   must not appear as modified in the diff: `test_browser_ssrf_local.py`,
   `test_browser_eval_ssrf.py`, `test_browser_console_ssrf.py`,
   `test_browser_snapshot_ssrf.py`, `test_browser_get_images_ssrf.py`,
   `test_browser_private_page_action_guard.py`,
   `test_browser_hybrid_routing.py`,
   `test_browser_camofox_private_page_guard.py`,
   `test_browser_profile_trust_seam.py`, `test_browser_session_signin.py`,
   `test_browser_lightpanda.py`.
10. **`tools/browser_tool.py` is the most upstream-churned file in this fork**
    (one upstream release changed 188 lines). The ledger must let a future
    merger preserve this correctly.

## Required review method

1. **Verify the fixes.** For each row in the claims table, trace the final
   state and mutation-check it. Report any fix that is partial, that moved the
   defect rather than removing it, or whose test would not catch its
   reintroduction.
2. **Attack the interactions.** Eleven tasks plus a fix wave touched
   overlapping code. Look for a later fix weakening an earlier one, two fixes
   individually correct but jointly wrong, or an invariant that holds per-task
   but not end-to-end.
3. **Complete the `::enrolled` audit.** Introducing the suffix created an
   obligation across everything that special-cases `::local`. Known handled:
   the six guard disjuncts, `cleanup_browser` expansion,
   `_bare_task_id_for_session_key`, `force_local`, `auto_local_this_nav`.
   Search for any remaining site assuming a key is either bare or `::local`.
4. **Concurrency end-to-end.** Per-key locks, `_session_handles`,
   `_session_cdp_urls`, `_active_sessions`, the registry, and the inactivity
   reaper now interact. Find a deadlock, a lost wakeup, or state that can
   disagree across those five structures.
5. **Attack `/browser connect` and the port guard.** Both surfaces. Hand-set
   ports, ports discovered rather than supplied, the confluence skill's own
   port, and any path that reaches `_attach_cdp` or writes the env var.
6. **Merge durability.** Would following the ledger's `merge_guidance` actually
   preserve this after an upstream rewrite of `browser_tool.py`? Name anything
   load-bearing that a whole-file rewrite would drop with no failing test.

## Required commands and evidence

Follow `AGENTS.md`: **use `scripts/run_tests.sh`, never `pytest` directly.**
The runner enforces per-file subprocess isolation; direct pytest produces
phantom cross-file failures — an earlier session reported 28 non-existent
pre-existing failures that way. Node-id syntax (`file.py::Class`) does not work
with this runner; use `-k ClassName`.

```bash
git status --short --branch
git cat-file -e 3a458156c52037d85943e6ee5fb6f5ac05e221b8^{commit}
git cat-file -e 1ad34e42d442b6ba6fc928db9310f18f7a54d7e2^{commit}
git diff --stat 3a458156c52037d85943e6ee5fb6f5ac05e221b8..1ad34e42d442b6ba6fc928db9310f18f7a54d7e2
git log --reverse --oneline 3a458156c52037d85943e6ee5fb6f5ac05e221b8..1ad34e42d442b6ba6fc928db9310f18f7a54d7e2
```

At minimum run and report:

```bash
scripts/run_tests.sh tests/tools/ -k browser
scripts/run_tests.sh tests/tools/test_browser_enrolled_routing.py
scripts/run_tests.sh tests/tools/test_browser_enrolled_launch.py
scripts/run_tests.sh tests/tools/test_browser_enrolled_port_guard.py
scripts/run_tests.sh tests/tools/test_browser_session_manager.py
./venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/browser-profiles.yaml
```

**Claims to verify, not accept:**

- All browser-selected tests in `tests/tools/` pass at the remediation tip.
- Failures elsewhere in `tests/tools/` are pre-existing. Confirm each at
  `619ef6ea4` **in the same checkout** before attributing it. Note specifically
  that `tests/hermes_cli/test_tui_npm_install.py` failures are
  checkout-state-dependent (node_modules/workspace state), not commit-dependent
  — they reproduce at the design parent in a populated checkout and vanish in a
  fresh worktree.
- The change is brand-neutral. Per-brand test rule in `CLAUDE.md`:
  `tests/hermes_cli/test_skin_engine.py` is `otto`-only,
  `tests/test_hermes_constants.py` is `base`-only; failures from running either
  on the wrong branch are not regressions.

**On hardware:** nothing here has run against real corporate Chrome or Edge on
a managed Windows machine. The identity proof, the port move, client-certificate
presentation, and the persistent profile directory are unit-tested only. Do not
treat any hardware claim as evidence; state explicitly which risks remain
unverifiable without that machine.

## Severity

- **CRITICAL**: SSO-session or credential disclosure; untrusted content
  obtaining the corporate browser's identity; silent substitution of the
  unmanaged browser where the managed one was required; arbitrary unauthorized
  execution.
- **HIGH**: violation of a non-negotiable invariant; a fix that moved rather
  than removed its defect; a deterministic race causing duplicate launch or
  mid-session teardown; a trust/launch mismatch; a ledger omission capable of
  silently losing a security behavior in an upstream merge.
- **MEDIUM**: bounded correctness, recovery, operability, or performance defect
  with a realistic trigger.
- **LOW**: narrow maintainability, diagnostics, or test-quality problem not
  currently violating an invariant.

Do not inflate severity without a concrete failure path. Do not downgrade a
race because its interleaving is difficult.

## Required output

Write the review to:

`docs/reviews/2026-07-27-per-navigation-browser-profile-remediation-adversarial-review-<model_name>.md`

where `<model_name>` is the model performing the review.

It must contain:

1. Scope and immutable refs actually reviewed, and the platform.
2. A verdict — SHIP, CONDITIONAL, or DO NOT SHIP — for merging this into
   `base` and then to the branded release branches.
3. A **per-original-finding table**: for each of EBL-001..EBL-009 and BP-1,
   state FIXED / PARTIALLY FIXED / MOVED / NOT FIXED, with the evidence and the
   mutation you ran.
4. New findings by severity, each with file and current line, the violated
   invariant, a concrete failure scenario or interleaving, evidence, a minimal
   safe fix, and the missing regression test.
5. A verdict on each of the ten "specific decisions to attack", explicitly
   including a ruling on `browser.cdp_url` as a second door to BP-1.
6. Concrete reproductions for the highest-risk findings.
7. What you verified safe and the adversarial cases you tried.
8. Verification evidence: every command, result, platform, and whether it came
   from execution, mutation, or inspection.
9. Merge-risk assessment against `base` at `a34a50875`.
10. Residual risks, especially native Windows behavior and anything needing the
    managed corporate machine.

If you find no defect in an area, explain the adversarial cases you attempted
and why the implementation resisted them. Do not accept comments, docstrings,
test names, ledger entries, prior reviews, or green runs as substitutes for
evidence. Be specific or be silent.
