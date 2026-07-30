# Adversarial code-review prompt — Enrolled-browser launch wiring

Paste everything below the line into a fresh, capable model or coding agent
with read and shell access to this repository:

/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent

The reviewer must assess a single commit that wires the agent's browser tools to
launch the user's real installed browser for `enrolled` profiles. This is a
review task, not an implementation task. Do not modify source, rewrite history,
create releases, push, or disturb unrelated work. The only authorized repository
write is the final review document named in Required output.

---

## Role

You are a hostile principal-level reviewer of Python, browser automation,
CDP/subprocess lifecycles, SSRF and origin-trust boundaries, concurrency, and
upstream-merge preservation. Your job is to break this implementation, not to
bless it.

Assume every completion claim is unproven until you trace the production path
and either reproduce the behavior or establish the invariant from code. Test
filenames, mocks, green runs, mutation-check anecdotes, and confident commit
messages are not proof. This change is small — 657 insertions across 6 files —
so sampling is not acceptable. Read every changed line, and read the unchanged
surrounding code it now depends on.

Praise is not useful. If an area is safe, state exactly which code path,
interleaving, boundary, and test you checked. Do not stop at the first defect.

This change ships a security-relevant capability: it routes a coding agent's
browser automation through a corporate browser holding live SSO cookies and
machine client certificates. Treat "the agent drives the user's real browser" as
a privileged operation and review it as one.

## Repository and immutable review scope

Repository root:

/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent

Branch: `base` — this fork's development trunk, brand-neutral. Brand branches
(`otto`, `loop24`) are `base` plus a generated branding overlay. Nothing in this
change is emitter-owned, so it must behave identically on every brand; verify
that claim rather than assuming it.

| Meaning | Commit |
|---|---|
| Approved design, implementation starts after this commit | 619ef6ea42d6b3c3873e6426ec612e48a110e116 |
| Implementation under review | 3a458156c |

Primary review range:

    619ef6ea42d6b3c3873e6426ec612e48a110e116..3a458156c

At prompt creation this range contains 6 changed files, 657 insertions and 7
deletions. Verify those numbers yourself.

Two earlier commits establish the surface this change extends. Read them; do not
review them as if they were new, but do check whether this change breaks or
contradicts them:

| Meaning | Commit |
|---|---|
| Profile-aware session manager (`acquire`/`signin`/`eval`) | 53bc3aa2f |
| Origin-scoped SSRF trust seam | 4d5c77c25 |

Preserve unrelated local work. Start with `git status`. Use read-only commands or
detached temporary worktrees for test execution. Do not clean, reset, checkout
over, stash, or delete untracked files in the shared checkout. A prior session
lost work to a `git stash` that was interrupted before its `pop`; do not repeat
that.

## Sources of truth — read completely before reviewing code

1. `docs/plans/2026-07-26-consolidated-browser-automation-design.md` — the
   approved design for this change
2. `docs/plans/2026-07-20-persistent-enrolled-browser-session-design.md` — the
   earlier design that introduced profiles, trust, and the session manager
3. `docs/superpowers/plans/2026-07-25-browser-session-manager.md`
4. `docs/upstream-customizations/README.md`
5. `docs/upstream-customizations/browser-profiles.yaml` — especially the new
   `enrolled-browser-launch-wiring` entry, and the existing
   `enrolled-profile-seeding-and-toggle` entry
6. `AGENTS.md`
7. `CLAUDE.md`

The design is the design of record. Do not redesign the product because you
prefer another architecture. Report deviations, missing behavior, unsafe
implementation evidence, and contradictions *between* these documents — there is
at least one place where the 2026-07-20 design and the 2026-07-26 design pull in
different directions, and resolving that is part of your job, not a detail.

## Non-negotiable invariants

A violation of any item here is at least HIGH severity.

1. **The bundled browser is never silently substituted.** `acquire()` raises
   `ProfileError` rather than falling back to agent-browser's bundled Chrome for
   Testing. A silent fallback would fail corporate mTLS and present as a broken
   connection rather than a misconfigured profile. No code path may reintroduce
   it, including exception handlers, `except Exception: return ""`, or a cached
   empty value.
2. **Chrome for Testing is excluded from `_enrolled_candidates()` on every
   platform.** It is a different binary and the one browser that cannot present
   machine certificates. The exclusion is the point of the list.
3. **The always-blocked cloud-metadata floor is checked first at every guard site
   and is never trusted**, regardless of profile, trust, or which browser is
   driving.
4. **Trust remains origin-scoped.** Launching the enrolled browser must not widen
   which origins a session may reach. An enrolled session reaches exactly the
   origins its profile lists, and nothing else.
5. **Toggle off is byte-for-byte today's behavior.** No `browser.default_profile`
   ⇒ no profile ⇒ existing throwaway path. `BROWSER_CDP_URL` and
   `browser.cdp_url` semantics and precedence are unchanged, so `/browser
   connect` is unaffected.
6. **`acquire()` runs once per session.** It executes `close --all` daemon
   hygiene, so a second call mid-session tears the browser down between
   `navigate` and `click`. A failed acquire must not be cached.
7. **Closing a session must never launch a browser.**
8. **The availability gate may only report available when the tool would actually
   work** — toggle on *and* executable resolves — mirroring the Termux branch's
   rule against advertising a tool that hangs until the command timeout on first
   use.
9. **The upstream browser SSRF and hybrid-routing suites pass unedited.** If they
   needed editing, the change altered a boundary it claimed not to touch.
10. **`tools/browser_tool.py` is the most upstream-churned file in this stack**
    (v0.19.0 changed 188 lines). The footprint added there must stay a thin
    delegating helper plus call-site swaps, and must be completely and accurately
    represented in the ledger.

## What the change claims to do

Three pieces, in an order the design calls a correctness constraint:

1. `tools/browser_profiles.py` `_enrolled_candidates()` — Chrome before Edge on
   all three platforms, real Windows Chrome paths added including the per-user
   `%LOCALAPPDATA%` install.
2. `tools/browser_tool.py` `_session_cdp_url(session_key)` — resolves the
   session's profile (explicit `bind()`, else `browser.default_profile`) and,
   when enrolled, returns `acquire(profile, session_key=…).cdp_url`; otherwise
   `_get_cdp_override()` unchanged. Swapped in at one launch call site,
   `_get_session_info`. Memoized per session key. Failures propagate.
3. `tools/browser_tool.py` `check_browser_requirements()` — early return for a
   launchable enrolled default profile, delegating to a new
   `default_profile_launchable()` in `tools/browser_session_registry.py`.

Supporting: `_session_browser_profile` and `_session_uses_enrolled_browser` are
pure predicates (no launch); `_forget_session_cdp_url` evicts the memo in
`_cleanup_single_browser_session`.

Do not take this summary as accurate. Verify each claim against the diff.

## Specific decisions to attack

These are the author's judgment calls. Each is a candidate defect. Do not accept
the stated rationale; test it. This list is a starting point and not a boundary —
findings outside it are welcome.

1. **The global environment side effect.** `acquire()` calls `_attach_cdp()`,
   which sets the *process-global* `os.environ["BROWSER_CDP_URL"]`. The author
   deliberately left `_is_local_mode()`, `_is_local_backend()`, and
   `_ensure_cdp_supervisor()` unswapped because that global makes
   `_get_cdp_override()` return the enrolled endpoint. Determine what happens
   with two concurrent sessions in one process — one enrolled, one not, or two
   enrolled profiles with different `cdp_port`s. Does a non-enrolled session
   inherit the enrolled browser, and does that carry SSO cookies or trust with
   it? Does an enrolled session's endpoint survive another session's cleanup?
   Consider the agent's real concurrency model (`task_id` per task, cleanup
   threads, the inactivity reaper) rather than a single-session mental model.
2. **The acquire race.** The memo lock is released before `acquire()` is called
   and reacquired after. Establish whether two threads can both miss the cache
   and both launch, and what a second `close --all` does to the first thread's
   browser. Then decide whether the memoization invariant (item 6) actually
   holds under concurrency or only under the single-threaded tests that assert
   it.
3. **Memo staleness.** The cached CDP URL has no TTL and no liveness probe. Work
   out what happens when the user closes the browser window, the browser crashes,
   or the machine sleeps, and whether anything re-acquires before the session is
   reaped.
4. **Exception propagation from `_get_session_info`.** `_run_browser_command`
   wraps it in `try/except` and returns an error dict. `browser_navigate` (near
   line 3046) does not. Trace what an operator actually sees when the enrolled
   browser cannot launch, on each path, and whether any path converts a
   `ProfileError` into a silent empty result or an unhandled traceback.
5. **External browsing through a corporate identity.** With
   `browser.default_profile: enrolled`, every unbound session — including one
   navigating an arbitrary external site — now drives the corporate browser
   holding live SSO cookies and machine certificates. The 2026-07-20 design's §5
   states the hard isolation rule that "an untrusted external site must never be
   driven through an enrolled profile", and `BrowserSession`'s own docstring
   repeats it; the `enrolled-profile-seeding-and-toggle` ledger entry gives the
   same reason for seeding the toggle empty. The 2026-07-26 design asks for one
   capability covering internal *and* external. Determine whether the shipped
   code violates §5, whether the newer design knowingly supersedes it, and what
   the actual exposure is — cookie theft via a malicious external page, CSRF
   against internal apps from an attacker-controlled tab, credential reuse. This
   is the highest-value question in the review. Answer it with a concrete attack
   path or a concrete reason there is none.
6. **Gate/launch TOCTOU.** `check_browser_requirements()` reports available when
   the executable resolves; the acquire happens later. Assess the window, and
   whether the gate can report available while every subsequent tool call fails.
7. **Startup cost.** `check_browser_requirements()` now reads config and stats up
   to five filesystem paths, and is called during Desktop tool-schema assembly.
   Determine how often, and whether this adds measurable startup latency or
   Windows console flashes — the surrounding code already worries about the
   latter for a related reason.
8. **The `force_local` guard.** `cdp_override = "" if force_local else
   _session_cdp_url(task_id)` claims to preserve prior behavior exactly, since
   the value was formerly computed unconditionally and ignored when
   `force_local`. Verify the equivalence, including whether removing the former
   unconditional `_get_cdp_override()` call dropped a side effect anything
   depended on.
9. **Windows path correctness.** The new Chrome paths are unverified on real
   Windows. Check the environment-variable fallbacks, the `PROGRAMFILES` vs
   `PROGRAMFILES(X86)` ordering, and the invented default
   `C:\Users\Default\AppData\Local` when `LOCALAPPDATA` is unset — including
   whether that default can ever match a real, writable, attacker-influenced
   path.
10. **Ledger accuracy.** The new `enrolled-browser-launch-wiring` entry lists
    files, owned symbols, and merge guidance. Check that every symbol actually
    exists, that no added symbol is missing, and that following the guidance
    verbatim during a merge would in fact preserve the behavior. A ledger that
    passes its checker but omits a load-bearing symbol is a HIGH finding, because
    the entire mitigation for this file's churn rests on it.

## Required review method

### 1. Establish the diff and trace every path

- Verify the commits and ancestry. Enumerate all 6 changed files.
- Trace `_session_cdp_url` from every entry point: `_run_browser_command`,
  `browser_navigate`, `_get_session_info`, cleanup, and the availability gate.
- Build the call graph of what now triggers a browser launch, and prove that the
  set of launch triggers is exactly the intended one.
- Identify every remaining `_get_cdp_override()` call site and justify, for each,
  why it was or was not swapped. An unswapped site that needed swapping is a
  finding; a swapped site that should not have been is also a finding.

### 2. Attack profile resolution and trust

- Confirm resolution order (`bind()` wins, else `default_profile`) matches
  `session_trusts_url` exactly. A divergence between which profile *launches* and
  which profile *grants trust* is a serious finding: it would mean one browser
  driving with another's trust.
- Test unknown profile names, ephemeral profiles named as default, malformed
  config, a `default_profile` naming a profile that does not exist, and config
  that changes between the gate check and the acquire.
- Verify the ephemeral `default` profile still cannot be promoted to enrolled or
  granted origins through any new path.
- Re-run the guard sites from the trust seam and confirm launching changed none
  of their decisions.

### 3. Attack concurrency and lifecycle

- Two or more sessions, mixed enrolled/ephemeral, concurrent acquire, concurrent
  cleanup, cleanup racing acquire, reaper racing a live navigation.
- Establish whether the memo, the environment variable, the session registry, and
  `_active_sessions` can disagree, and what an operator sees when they do.
- Kill the browser out from under a live session. Kill the agent and restart.
  Sleep/wake the machine.
- Verify `close` never launches, including via the inactivity reaper and
  `cleanup_all_browsers`.

### 4. Attack the candidate list and executable resolution

- Verify Chrome-before-Edge on all three platforms and the total absence of
  Chrome for Testing, headless shell, Playwright, and agent-browser paths.
- Attempt to reach the bundled browser anyway: via an explicit `executable`
  config value, a symlink, `PATH` manipulation, or environment variables the
  candidate list interpolates.
- Consider whether an attacker who can write to any interpolated path or to a
  candidate location gains code execution as the user, and what trust boundary
  that crosses.

### 5. Audit test quality

- Every new test monkeypatches `acquire`. Determine what is therefore unproven:
  real launch, real CDP attach, real daemon hygiene, real failure modes.
- Identify tests that assert their own fixtures, tests that cannot fail, and
  assertions on implementation detail rather than behavior.
- The author reports mutation-checking five tests by reverting each production
  change and observing targeted failures. Re-run that exercise independently; do
  not accept the report.
- Name the highest-risk untested path.
- Distinguish a real defect from a test gap, but treat an unproven
  security-critical requirement as a blocker.

### 6. Verify the preservation and non-regression claims

- Confirm the browser SSRF and hybrid-routing suites are genuinely unedited in
  this range, and that they pass.
- Validate the ledger with the repository's own checker.
- Assess whether a realistic upstream merge that rewrites `browser_tool.py`
  would silently drop any of this with no conflict and no failing test. Name
  which test would catch each piece; where none would, say so.

## Required commands and evidence

Follow `AGENTS.md`: **use `scripts/run_tests.sh`, not `pytest` directly.** The
script enforces CI parity (xdist workers, in-tree subprocess isolation). The
author verified primarily with direct `pytest` and only confirmed under
`run_tests.sh` afterward; re-verify properly, and report any discrepancy.

Start with:

    git status --short --branch
    git cat-file -e 619ef6ea42d6b3c3873e6426ec612e48a110e116^{commit}
    git cat-file -e 3a458156c^{commit}
    git diff --stat 619ef6ea42d6b3c3873e6426ec612e48a110e116..3a458156c
    git diff --name-status 619ef6ea42d6b3c3873e6426ec612e48a110e116..3a458156c
    git diff 619ef6ea42d6b3c3873e6426ec612e48a110e116..3a458156c

At minimum run and report:

    python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml
    scripts/run_tests.sh tests/tools/ -k browser
    scripts/run_tests.sh tests/tools/test_browser_enrolled_launch.py
    scripts/run_tests.sh tests/tools/test_browser_profiles.py

**Claims to verify, not to accept:**

- That all 654 browser-selected tests in `tests/tools/` pass.
- That the failures elsewhere in `tests/tools/` are pre-existing. The author
  claims 28 failures reproduce identically at `619ef6ea4` in a clean worktree
  (`test_base_environment`, `test_command_guards`, `test_cross_profile_guard`,
  `test_file_staleness`, `test_file_state_registry`, `test_file_tools`,
  `test_file_tools_cwd_resolution`, `test_line_ending_preservation`,
  `test_patch_failure_tracking`, `test_skill_bundle_provenance`), with 3 more
  appearing only in full-suite ordering. Reproduce this independently before
  accepting it, and specifically confirm `test_cross_profile_guard` is unrelated
  to browser profiles despite its name.
- That the change is brand-neutral. Verify on `otto` and `loop24` from detached
  worktrees. Note the per-brand test rule in `CLAUDE.md`:
  `tests/hermes_cli/test_skin_engine.py` is `otto`-only and
  `tests/test_hermes_constants.py` is `base`-only; failures from running either
  on the wrong branch are not regressions.

For concurrency, lifecycle, and security claims, write small temporary
reproduction scripts outside tracked source. Show exact commands and outputs for
every finding. Remove only your own temporary files and worktrees.

**On hardware verification:** the design states tests cannot prove this change
and a managed corporate machine must. No hardware verification has been
performed. Do not treat any hardware claim in the design or commit message as
evidence, and state explicitly which risks remain unverifiable without that
machine — particularly Windows Chrome paths, real CDP attach against a
policy-managed browser, client-certificate presentation, and whether the
certificate picker and integrated-auth prompts appear once or on every launch.

## Severity

- **CRITICAL**: credential or SSO-session disclosure, arbitrary unauthorized
  execution, cross-origin authority breach, silent substitution of the unmanaged
  browser where the managed one was required, or a path that grants an untrusted
  page the corporate browser's identity.
- **HIGH**: violation of a load-bearing invariant above, a deterministic race
  causing duplicate launch or mid-session teardown, a trust/launch mismatch,
  false success, an availability gate that advertises a non-working tool, or a
  ledger omission that would let a merge silently revert a security-relevant
  behavior.
- **MEDIUM**: bounded correctness, recovery, operability, or performance defect
  with a realistic production trigger.
- **LOW**: narrow maintainability, diagnostics, documentation, or test-quality
  problem that does not presently violate an invariant.

Do not inflate severity without a concrete failure path. Do not downgrade a race
merely because reproducing its interleaving is difficult.

## Required output

Write the review to:

`docs/reviews/2026-07-26-enrolled-browser-launch-adversarial-review.md`

It must contain:

1. Scope and immutable refs actually reviewed.
2. A verdict: SHIP, CONDITIONAL, or DO NOT SHIP — for merging `base` into the
   brand branches and releasing.
3. Findings table sorted by severity, each with a stable ID, file and current
   line, the violated invariant, a concrete failure scenario or interleaving,
   observed or reasoned evidence, a minimal safe fix, and the missing regression
   test.
4. A verdict on each of the ten "specific decisions to attack", explicitly
   including a reasoned answer to item 5 (external browsing through the corporate
   identity) with a concrete attack path or a concrete reason there is none.
5. Concrete reproductions for the top findings — exact inputs, thread ordering,
   commands, and wrong result.
6. What was verified safe and why, covering every review dimension. No generic
   statements.
7. Verification evidence: every command, pass/fail/skip, platform, and whether
   the result came from real execution or inspection.
8. Required remediation before merge and release, ordered by risk.
9. Residual risks, especially native Windows behavior and everything that
   requires the managed corporate machine.

If you find no defects in a dimension, explain the exact adversarial cases you
tried and why the implementation resisted them. Do not accept comments,
docstrings, test names, ledger entries, or green runs as substitutes for
evidence. Be specific or be silent.
