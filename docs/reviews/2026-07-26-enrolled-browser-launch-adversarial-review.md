# Enrolled-browser launch wiring: adversarial code review

Date: 2026-07-26  
Reviewer: Codex  
Platform actually exercised: macOS (`darwin`), Python 3.11.15  
Verdict: **DO NOT SHIP**

Do not merge `base` into `otto` or `loop24`, and do not release this change. The
implementation does make an enrolled session reach `acquire()`, but it also
turns the enrolled CDP endpoint into process-global state. That lets an
explicitly ephemeral task drive the corporate browser, and it persists after
cleanup. Independently, the new default routes arbitrary external content
through the corporate identity, contrary to the still-live hard-isolation
contract. Both are release-blocking authority-boundary failures.

## 1. Scope and immutable refs

Reviewed repository:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

- Parent/design ref: `619ef6ea42d6b3c3873e6426ec612e48a110e116`
- Implementation ref: `3a458156c52037d85943e6ee5fb6f5ac05e221b8`
- Ancestry: the parent is an ancestor of the implementation; the range contains
  exactly one commit.
- Working branch during and after review: `base`.
- Brand refs used for detached merge simulation:
  - `otto`: `75b9c6510442cb2e7dade513b01c0023dfa73bc6`
  - `loop24`: `8f2e6ac8ba039e0f8289280d35fb335584ce1dee`
- Initial worktree state: `base...origin/base [ahead 2]`; the supplied review
  prompt was the only untracked file. It was not modified.

The range changes exactly six files (657 insertions, 7 deletions):

1. `docs/upstream-customizations/browser-profiles.yaml`
2. `tests/tools/test_browser_enrolled_launch.py`
3. `tests/tools/test_browser_profiles.py`
4. `tools/browser_profiles.py`
5. `tools/browser_session_registry.py`
6. `tools/browser_tool.py`

I read those files in full, plus the two designs, the implementation plan,
`tools/browser_session_manager.py`, the prior trust/session commits
`53bc3aa2f` and `4d5c77c25`, the customization README, `AGENTS.md`, and
`CLAUDE.md`.

## 2. Findings

Findings are sorted by severity. “Observed” means the reviewed production
function was executed with only external effects replaced; “inspection” means
the result follows directly from the cited branch/call graph.

| ID | Severity | File and current line | Violated invariant | Concrete failure | Evidence | Minimal safe fix | Missing regression test |
|---|---|---|---|---|---|---|---|
| EBL-001 | CRITICAL | `tools/browser_session_manager.py:163`; `tools/browser_tool.py:589-591,661` | Browser identity and CDP ownership must be per session; an ephemeral task must never inherit an enrolled browser. | Task A acquires corporate CDP and writes it to `BROWSER_CDP_URL`. Explicitly-ephemeral task B then takes `_get_cdp_override()` and drives A's corporate browser. Cleanup does not clear the variable. Concurrent supervisor attachment can likewise bind task A's supervisor to the last task's endpoint. | Observed: binding=`default`, enrolled predicate=`False`, selected endpoint=`.../corporate`; the global remains after cleanup. | Remove `_attach_cdp()` as per-session state. Carry the resolved endpoint only in the session record and pass that record to local/backend/supervisor decisions. Keep `/browser connect` as a separate immutable user override; a process-global variable cannot safely model concurrent task state. | Two simultaneous enrolled/ephemeral tasks, different endpoints, barriers around acquire/supervisor, asserting each command and supervisor stays on its own endpoint before and after either cleanup. |
| EBL-002 | CRITICAL | `tools/browser_session_registry.py:63-81`; `tools/browser_tool.py:589-595,3010-3015` | The July 20 design and `BrowserSession` contract say an untrusted external site must never be driven through an enrolled profile. | A user asks the agent to read an attacker-controlled public page. Because the unbound task inherits `browser.default_profile`, the page is opened in the corporate browser. Page text prompt-injects the agent to navigate to a trusted internal origin, snapshot SSO-protected data, then send the data to a public collection URL. The trust seam authorizes the internal hop and public navigation remains allowed. | Inspection plus an executed profile-resolution probe: every unbound task resolves to the enrolled default regardless of destination. The July 26 design asks for external browsing but neither repeals nor mitigates the older isolation rule; the manager docstring and toggle ledger still assert it. | Select the browser profile per navigation trust domain: untrusted/public origins use ephemeral; only explicitly trusted origins use enrolled. Do not allow a single agent-controlled session to move from attacker content to enrolled authority. If product intent truly changes, first replace the threat model and add an explicit user-controlled authority transition. | Start on a malicious public page, attempt a model/tool transition to a trusted internal origin, and prove the public page's session never owns the enrolled endpoint or corporate cookies/certificates. |
| EBL-003 | HIGH | `tools/browser_tool.py:583-602` | `acquire()` must run once per session even under real task concurrency; its `close --all` hygiene must not tear down an in-flight session. | Two threads miss the memo, both call `acquire("enrolled", "same-task")`, receive different endpoints, and race to overwrite the cache. The second acquire runs daemon hygiene after the first started and can close the first daemon mid-navigation. | Observed with a two-party barrier: two acquire calls and two returned CDP URLs for one session key. | Serialize the entire miss/acquire/publish state per session key (single-flight/future or a per-key lock), publish only one result, and make cleanup coordinate with the in-flight acquire. | Barrier-driven concurrent calls for one key; assert one acquire, one endpoint, no second hygiene call, and defined cleanup-vs-acquire ordering. |
| EBL-004 | HIGH | `tools/browser_session_manager.py:88-94,120-124,280` | The browser actually attached must be the profile whose origins grant authority. | Profiles A and B share the default port 9222. A is already listening. Acquiring B accepts any HTTP response from `/json/version`, skips B's executable/user-data-dir entirely, then binds task B to B's trust while driving A's identity. An unrelated CDP listener has the same effect. | Observed: requesting B attached to 9222, did not call `resolve_executable`, and registered B's trust. | Give every profile a unique managed endpoint and verify browser identity before reuse (profile-owned launch token/process plus CDP metadata). Reject an occupied port whose owner cannot be proven. Validate duplicate configured ports at load time. | Launch/profile A on a port, acquire B with a different executable/data directory on that port, and require a loud mismatch rather than reuse/bind. Also test a non-CDP or foreign-CDP responder. |
| EBL-005 | HIGH | `tools/browser_tool.py:595,4623-4703`; `tools/browser_session_manager.py:194-200,280` | Closing/reaping must release all session authority; turning the enrolled default off must stop later acquisitions for that task. | `_session_cdp_url()` discards the returned `BrowserSession`, so `release()` is never called. Cleanup evicts only the memo; it does not unbind the registry or clear the global. The same task key therefore reacquires the enrolled browser after the default toggle is removed. | Observed: `bound_after_cleanup='corp'`, default=`None`, and the task reacquired the corporate endpoint; the global endpoint also survived. | Store the acquired session handle with `_active_sessions`; release/unbind it exactly once during cleanup and failed registration. Define toggle behavior and clear inherited bindings when authority is revoked. Do not rely on environment cleanup for EBL-001. | Real `acquire()` with process launch mocked below CDP, then cleanup/toggle-off/reuse of the same task key; assert no binding, no enrolled reacquire, and idempotent release. |
| EBL-006 | HIGH | `tools/browser_profiles.py:268-283`; `tools/browser_session_registry.py:84-105`; `tools/browser_tool.py:4948-4957` | The availability gate must not advertise a browser tool that deterministically fails on first use. | `resolve_executable()` checks only `exists()`, not regular-file/executable status. A mode-0644 file passes the gate and `acquire()` raises `PermissionError`. The early return also skips the required `agent-browser` CLI check. Empty `user_data_dir` and invalid port ranges similarly pass the gate and fail every acquire. | Observed: mode `0644`, `requirements_gate=True`, first acquire `PermissionError`. Separately, the gate returned true without calling `_find_agent_browser`, while `_run_browser_command` immediately failed because the CLI was absent. | Validate the whole enrolled path without launching: executable file and executable permission where applicable, usable non-empty data directory, valid unique port, and `agent-browser` command presence. Return a structured reason rather than a bare bool where possible. Invalidate the check cache on profile changes. | Non-executable file, directory-as-executable, missing CLI, empty/unwritable data dir, port 0/negative/>65535, and config/path deletion between check and acquire. Each must withhold or return an actionable unavailable result. |
| EBL-007 | HIGH | `docs/upstream-customizations/browser-profiles.yaml:100-108,141-183` | The merge ledger must enumerate every load-bearing symbol/call site and must not instruct a merger to preserve a security defect. | The entry omits `_session_cdp_lock`, `_session_cdp_urls`, `_get_session_info`, `_run_browser_command`, `_cleanup_single_browser_session`, and `check_browser_requirements`, although all carry new behavior. Its guidance explicitly says the process-global `BROWSER_CDP_URL` side effect keeps unswapped sites correct; EBL-001 proves the opposite. A whole-function upstream rewrite can drop launch, fast-fail, cleanup, or gate behavior while the checker still passes. | Inspection. The repository checker passes because it validates schema/existence, not complete semantic ownership. | List the state and every modified owner/call site, add tests mapped to each, and replace the global-side-effect guidance with the per-session contract. Add semantic checker coverage for declared diff symbols/call sites. | Mutation/merge test that rewrites each owner independently and requires the ledger's named suite to fail; checker test that rejects an added load-bearing symbol absent from `owned_symbols`. |
| EBL-008 | HIGH | `tools/browser_tool.py:3086-3097` | An enrolled profile must reach exactly its trusted origins, including an allowed redirect target; the metadata floor remains absolute. | A public SSO/login URL redirects to a trusted internal origin. The pre-navigation trust path is valid, but the post-redirect block checks only `_is_safe_url()` and blanks the page without consulting `_session_trusts_url()`. | Observed: trust decision for `https://wiki.corp.example/home` was true, yet navigation returned `Blocked: redirect landed on a private/internal address` and opened `about:blank`. | Mirror the pre-navigation ordering in the redirect guard: metadata floor first, then allow the final URL only when the same session trusts its exact origin. | Public login redirect to a trusted origin succeeds; redirect to an unlisted private origin fails; redirect to IMDS fails even if configured as trusted. |
| EBL-009 | MEDIUM | `tools/browser_tool.py:583-587,2188-2191` | A dead enrolled browser must recover without waiting for unrelated session inactivity cleanup. | User closes/crashes the browser or sleep/wake invalidates CDP. Both the memo and `_active_sessions` remain authoritative with no liveness probe, so every later command reuses the dead endpoint and activity refreshes prevent the idle reaper from repairing it. | Observed: after simulated browser death, the helper returned the same dead URL, made one total acquire, and performed zero liveness checks. | On a connection-class failure, atomically evict the active record/memo, release the binding as appropriate, probe/reacquire once, and retry only safe/idempotent setup. Add a cheap health check or generation owned by the managed process. | Kill the CDP browser after first navigation, then issue a second command and assert one bounded reacquire; cover crash and sleep/wake-like socket invalidation. |

### Why EBL-002 is a concrete security issue

Same-origin policy prevents JavaScript on `evil.example` from directly reading
arbitrary `corp.example` responses, but it does not protect an agentic browser
from a confused-deputy transition. The model reads attacker-controlled page
content and owns the next browser tool call. This code gives that same task both
public navigation and enrolled internal-origin trust. The following sequence is
therefore available without any browser exploit:

1. `browser_navigate("https://evil.example/report")` opens in the enrolled
   browser because the task is unbound and the default is enrolled.
2. The page instructs the model to fetch
   `https://wiki.corp.example/export`; the trust seam permits it and the browser
   presents SSO/cert authority.
3. `browser_snapshot()` returns protected content to the model.
4. The page instruction tells the model to navigate to a public collector with
   the extracted content. The URL token detector is not a data-loss prevention
   boundary for arbitrary corporate text.

That is precisely the “untrusted page gets the corporate browser's identity”
case classified CRITICAL by the review prompt. The newer design's product wish
for one browser does not provide a technical mitigation or an explicit security
contract supersession.

## 3. Call graph and remaining override sites

Actual launch graph:

```text
browser_navigate(task, url)
  -> _get_session_info(navigation key)
     -> existing _active_sessions entry: no launch
     -> force_local sidecar: _create_local_session, no enrolled launch
     -> _session_cdp_url(task)
        -> cached URL: no launch
        -> explicit binding else default profile
        -> non-enrolled: _get_cdp_override, no enrolled launch
        -> enrolled: acquire(profile, session_key=task)  <-- launch/reattach

other browser tools
  -> _run_browser_command
     -> CLI and pure Chromium/profile gates
     -> _get_session_info
        -> same graph

cleanup/reaper
  -> _cleanup_single_browser_session
     -> _run_browser_command("close") only for an already-active session
     -> existing session returned by _get_session_info, so no launch
     -> no active session: no command and no launch

availability/schema assembly
  -> check_browser_requirements
     -> _default_profile_launchable (pure config/path checks; no launch)
```

Thus the intended trigger is first creation of a non-sidecar enrolled session.
The implementation does not achieve “once”: EBL-003 adds a duplicate trigger
under concurrency. It also loses the session handle required to release it.

Every remaining `_get_cdp_override()` production use was checked:

- `_session_cdp_url(None)` and the non-enrolled fallback: preserving explicit
  `/browser connect` and `browser.cdp_url` is correct only if enrolled acquire
  stops mutating the same global; currently it causes EBL-001.
- `_ensure_cdp_supervisor`: preferring the process-global override over the
  task's active `cdp_url` is wrong under concurrency (EBL-001).
- `_is_local_mode` and `_is_local_backend`: both become process-global after
  one enrolled acquire, changing guards for unrelated tasks (EBL-001).
- `_navigation_session_key`: a global enrolled endpoint can suppress an
  unrelated task's hybrid local sidecar; this is another consequence of
  EBL-001.
- `check_browser_requirements`: retaining the explicit user override branch is
  correct; the new earlier enrolled-return semantics are incomplete (EBL-006).

## 4. Verdict on the ten specific decisions

| # | Decision | Verdict | Reason |
|---|---|---|---|
| 1 | Global environment side effect | **FAIL** | EBL-001 was reproduced with an explicitly ephemeral task; cleanup leaves the endpoint behind. Different enrolled ports also race through one global. |
| 2 | Acquire race | **FAIL** | EBL-003: the lock protects only dictionary access, not miss/acquire/publish. Two threads acquired twice. |
| 3 | Memo staleness | **FAIL** | EBL-009: no TTL, generation, probe, or error-triggered eviction exists. |
| 4 | Exception propagation | **PASS, with inconsistent diagnostics** | `_run_browser_command` converts launch errors into an actionable error dict. `browser_navigate`'s first direct `_get_session_info` can raise, but registry dispatch catches it as `Tool execution failed: ProfileError: ...`; direct Python callers see the exception. No silent fallback or empty success was found, so this is not a separate release blocker. |
| 5 | External browsing through corporate identity | **FAIL — CRITICAL** | EBL-002. Public attacker content and trusted internal navigation share one agent task and one enrolled authority. The concrete prompt-injection/confused-deputy path is above. |
| 6 | Gate/launch TOCTOU | **FAIL** | Path/config changes can invalidate a cached success, and EBL-006 shows stronger deterministic false positives even without a race. |
| 7 | Startup cost | **PASS for cost, FAIL for correctness under #6** | Inspection found one config read and at most five `exists()` calls, no subprocess and no Windows console launch. Registry check functions are TTL-cached. This cost is negligible compared with schema assembly; the returned answer is not trustworthy. |
| 8 | `force_local` guard | **PASS** | The removed unconditional `_get_cdp_override()` had no required mutation; skipping it avoids discovery I/O. Sidecars still force `_create_local_session` and the unedited hybrid suite passes. The process-global contamination in #1 is separate. |
| 9 | Windows paths | **UNVERIFIED / CONDITIONAL** | Static order is Chrome machine-wide, Chrome x86, Chrome per-user, then Edge; environment names are syntactically correct. The invented `C:\Users\Default\AppData\Local` fallback is not evidence of a real current-user install. No native Windows or managed-fleet run occurred. Explicit paths and symlinks intentionally bypass the auto list; no signer/product identity is validated. |
| 10 | Ledger accuracy | **FAIL** | EBL-007. The checker passes despite omitted state/call-site owners and guidance that preserves EBL-001. |

## 5. Concrete reproductions

These commands were run from the reviewed checkout. Network/process launch was
stubbed only where the test was about state selection or interleaving.

### 5.1 Process-global endpoint crosses into an ephemeral task (EBL-001)

Input state: `external-task` explicitly bound to `default`; enrolled default is
`corp`; an earlier corporate acquire has called `_attach_cdp()`.

```text
$ .venv/bin/python -c '<bind external-task=default; _attach_cdp(corporate); call _session_cdp_url>'
{'binding': 'default', 'uses_enrolled_predicate': False,
 'selected_cdp': 'ws://127.0.0.1:9222/devtools/browser/corporate',
 'global': 'ws://127.0.0.1:9222/devtools/browser/corporate'}
```

Wrong result: launch selection and trust/profile selection disagree. The task
is declared ephemeral but drives the corporate endpoint.

### 5.2 Two concurrent misses acquire twice (EBL-003)

Exact interleaving: T1 and T2 both read an empty memo; both enter the patched
`acquire`; a two-party barrier holds both until both calls are recorded; each
returns a distinct URL; each publishes.

```text
$ .venv/bin/python -c '<two threads + Barrier(2); both call _session_cdp_url("same-task")>'
{'acquire_calls': [('enrolled', 'same-task'), ('enrolled', 'same-task')],
 'thread_results': ['ws://127.0.0.1:9222/devtools/browser/1',
                    'ws://127.0.0.1:9222/devtools/browser/2'],
 'cached': {'same-task': 'ws://127.0.0.1:9222/devtools/browser/1'}}
```

Wrong result: `acquire()` is not once-per-session. In production both calls run
`close --all`.

### 5.3 Cleanup does not revoke authority (EBL-005)

```text
$ .venv/bin/python -c '<real acquire binding; cleanup task; remove default toggle; reuse task>'
{'first': 'ws://127.0.0.1:9222/devtools/browser/corp',
 'bound_before_cleanup': 'corp', 'bound_after_cleanup': 'corp',
 'default_after_toggle': None,
 'reacquired_after_toggle_off': 'ws://127.0.0.1:9222/devtools/browser/corp',
 'global_after_cleanup': 'ws://127.0.0.1:9222/devtools/browser/corp'}
```

Wrong result: both registry authority and process-global routing survive close.

### 5.4 Existing port substitutes another profile (EBL-004)

Two enrolled profiles used different executable/data-dir/trust configuration
but the same `cdp_port=9222`; `_cdp_alive` represented profile A already
listening. `resolve_executable` was patched to raise if identity was checked.

```text
$ .venv/bin/python -c '<acquire profile_b while profile_a CDP answers on 9222>'
{'requested_profile': 'profile_b', 'bound_profile': 'profile_b',
 'attached_endpoint': 'http://127.0.0.1:9222',
 'executable_checked': False, 'trusted_b': True, 'trusted_a': False}
```

Wrong result: B's trust is attached to an endpoint whose profile identity was
never established.

### 5.5 Availability false success (EBL-006)

```text
$ .venv/bin/python -c '<enrolled explicit executable is an existing mode-0644 file>'
{'mode': '0o644',
 'resolved': '/var/folders/.../not-executable',
 'requirements_gate': True,
 'first_acquire': "PermissionError: [Errno 13] Permission denied: '.../not-executable'"}
```

A separate executed probe made `_find_agent_browser` raise. The gate returned
`True` without calling it; the first `_run_browser_command` returned
`{'success': False, 'error': 'agent-browser absent'}`.

### 5.6 Trusted redirect is denied (EBL-008)

```text
$ .venv/bin/python -c '<public login succeeds with final URL wiki.corp; trust(wiki.corp)=True>'
{'trusted_redirect_target': 'https://wiki.corp.example/home',
 'trust_decision': True,
 'result': {'success': False,
            'error': 'Blocked: redirect landed on a private/internal address'},
 'opens': ['https://login.example/start', 'about:blank']}
```

### 5.7 Dead endpoint stays cached (EBL-009)

```text
$ .venv/bin/python -c '<acquire once; simulate dead browser; request same task again>'
{'first': 'ws://127.0.0.1:9222/dead',
 'after_browser_death': 'ws://127.0.0.1:9222/dead',
 'acquire_calls': 1, 'liveness_checks': 0}
```

### 5.8 Ledger omission (EBL-007)

```text
$ python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml
# exit 0, no output

$ git diff --unified=0 619ef6ea4..3a458156c -- tools/browser_tool.py \
    tools/browser_profiles.py tools/browser_session_registry.py
# Added state includes _session_cdp_lock and _session_cdp_urls; modified owners
# include _get_session_info, _run_browser_command,
# _cleanup_single_browser_session, and check_browser_requirements.
```

None of those state/owner names appears in the entry's `owned_symbols`. The
checker therefore cannot establish the completeness property the ledger claims.

## 6. What resisted attack

- **Profile resolution order:** both `_session_browser_profile` and
  `session_trusts_url` use explicit `profile_for(session_key)` first, then
  `default_profile_name()`. Unknown names and missing profiles resolve to no
  enrolled launch/no trust. This part is aligned; EBL-001 bypasses it through
  the separate global override.
- **Ephemeral built-in profile:** config cannot promote the reserved `default`
  profile to enrolled or grant it trusted origins. An explicitly-bound
  ephemeral session also makes both pure profile predicates deny enrolled
  status/trust. Again, global routing violates the outcome after that decision.
- **Malformed trust configuration:** non-list `trusted_origins` is rejected
  fail-closed and logs a warning. Unknown profile/default names fail closed.
- **Metadata floor:** the unconditional IMDS block remains before the enrolled
  trust exception. The unedited SSRF suites passed. EBL-008's proposed fix must
  preserve that ordering.
- **Candidate auto-list:** inspection and parameterized tests confirm
  Chrome-before-Edge on Windows/macOS/Linux and no auto candidate containing
  Chrome for Testing, headless shell, Playwright, or agent-browser. `PATH` is
  not consulted. Explicit `executable` and symlinks can intentionally select
  arbitrary files; that is operator/config authority, but it means tests do not
  prove browser product identity.
- **No launch on close:** the gate uses the pure enrolled predicate. Cleanup
  calls close only while `_active_sessions` still contains the task, so
  `_get_session_info` returns it rather than acquiring. No-active-session
  cleanup does not call the command runner. The lifecycle still fails to revoke
  the binding (EBL-005).
- **Hybrid sidecars:** `force_local` skips enrolled resolution and calls the
  local sidecar path. The old unconditional override lookup had no required
  mutation. All 23 hybrid-routing tests passed unedited.
- **Failure instead of bundled fallback:** an `acquire` `ProfileError` is not
  cached and no fallback branch substitutes bundled Chromium. Tool dispatch
  surfaces an error. Real process/CDP behavior remains hardware-unverified.
- **Brand neutrality:** both current brand heads accepted a no-commit merge of
  `3a458156c` without conflicts, and each merged tree passed all 654
  browser-selected tests. No emitter-owned file is in the six-file range.

## 7. Test-quality audit and mutation checks

The new tests are useful unit wiring tests but do not establish the production
contract. Every launch test replaces `browser_session_manager.acquire()` with
`_AcquireSpy`. Therefore they do not exercise daemon hygiene, process launch,
CDP readiness/discovery, `_attach_cdp`, registry binding, `BrowserSession.release`,
port collision, executable permissions, or cross-task state. The cleanup tests'
spy never binds the registry, so they cannot fail when cleanup omits `unbind()`.
The availability fixture forces `_find_agent_browser` to succeed, hiding the
missing-CLI false positive, and its “executable” test file is not executable.

The highest-risk untested path is two real task IDs concurrently acquiring and
using mixed enrolled/ephemeral profiles through actual manager/attach/cleanup
state. That single gap contains EBL-001, EBL-003, and EBL-005.

I independently ran five mutations in an isolated worktree. Each intended test
did fail:

| Mutation | Target test | Result |
|---|---|---|
| Put Edge before Chrome on macOS | `test_chrome_is_preferred_over_edge` | 1 failed, 2 passed |
| Restore `_get_session_info` to `_get_cdp_override()` | `test_enrolled_session_is_built_on_the_acquired_endpoint` | failed: override URL used |
| Remove enrolled exemption from Chromium fast-fail | `test_enrolled_session_passes_the_chromium_gate` | failed: missing Chromium |
| Remove memo eviction from cleanup | `test_reaping_a_session_drops_its_memoized_url` | failed: one acquire, expected two |
| Remove availability early return | `test_available_when_the_enrolled_browser_resolves` | failed: false, expected true |

This confirms those tests are sensitive to five advertised edits. It does not
validate the real manager integration or any adversarial concurrency/lifecycle
property.

## 8. Verification evidence

All test commands used `scripts/run_tests.sh`, as required. An initial sandboxed
browser-selected run produced 11 local socket-bind setup errors; it was rerun
outside the sandbox and is not counted as a product failure.

| Command / check | Result | Evidence type |
|---|---|---|
| `git status --short --branch`; `cat-file`; ancestry; range diff/stat/name-status | Correct refs, one-commit range, six changed files | Executed |
| `python3 scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/browser-profiles.yaml` | PASS, exit 0 | Executed |
| `scripts/run_tests.sh tests/tools/test_browser_enrolled_launch.py` | PASS, 20/20 | Executed |
| `scripts/run_tests.sh tests/tools/test_browser_profiles.py` | PASS, 38/38 | Executed |
| `scripts/run_tests.sh tests/tools/ -k browser` | PASS, 654/654 across 349 files | Executed outside sandbox |
| Same browser-selected command on detached `otto` merge result | PASS, 654/654 | Executed outside sandbox |
| Same browser-selected command on detached `loop24` merge result | PASS, 654/654 | Executed outside sandbox |
| `scripts/run_tests.sh tests/hermes_cli/test_skin_engine.py` on merged `otto` | PASS, 33/33 | Executed |
| `scripts/run_tests.sh tests/test_hermes_constants.py` on `base` | PASS, 128/128 | Executed |
| Full `scripts/run_tests.sh tests/tools/` at implementation | 8,772 passed, 3 failed; 7 files passed on retry | Executed outside sandbox |
| Full tools suite at immutable parent | 8,742 passed, same 3 failed; 3 files passed on retry | Executed outside sandbox |
| Three common failures | `test_approved_command_clean_slate`, `test_mcp_stdio_init_timeout`, `test_mcp_tool_issue_948`; timing-sensitive and outside changed surface | Executed at both refs |
| Ten files claimed in prompt to contain 28 pre-existing failures | Claim not reproduced. Parent focused run passed 213/213. At HEAD, nine files passed; `test_base_environment` hit its known atomic-snapshot concurrency failure. The identical blob failed when rerun alone at both refs. | Executed at both refs + blob equality inspection |
| `test_cross_profile_guard.py` specifically | PASS, 12/12 at both refs; file concerns terminal/file home isolation, not browser profiles | Executed + inspected |
| Five mutation checks | Each targeted test failed as expected | Executed in isolated detached worktree |
| Adversarial race/global/cleanup/port/gate/redirect/staleness probes | Wrong results shown in section 5 | Executed against production functions with external effects stubbed |
| Native Windows / managed corporate browser | NOT RUN | Hardware unavailable |

The claimed 28 failures should not be copied into release notes as fact: they
did not reproduce under the canonical runner in this review. The full-suite
three-failure equality is the valid baseline comparison observed here.

The SSRF and hybrid files are unedited in the reviewed range. Their selected
tests pass as part of the 654-test runs. The temporary worktrees were removed;
the main checkout remained on `base`.

## 9. Required remediation before merge/release

Ordered by risk:

1. Restore the untrusted/enrolled authority boundary (EBL-002). Public content
   must not share an enrolled browser/session with trusted internal access.
2. Eliminate process-global per-session CDP state (EBL-001). Make every guard,
   command, supervisor, and hybrid-routing decision consume task-owned state.
3. Implement single-flight acquire and defined cleanup/acquire coordination
   (EBL-003).
4. Prove CDP endpoint ownership/profile identity and reject port collisions
   (EBL-004).
5. Retain and release `BrowserSession`; unbind on cleanup/failure and define
   immediate toggle-off behavior (EBL-005).
6. Make availability validate the actual required chain, including executable
   status, CLI, data directory, and port (EBL-006).
7. Fix trusted redirect parity while keeping the metadata floor first
   (EBL-008).
8. Add dead-endpoint recovery (EBL-009).
9. Correct and strengthen the customization ledger (EBL-007).
10. Add real integration tests beneath the process/CDP boundary, then repeat
    the canonical base and both-brand matrices.
11. Before release, run the managed-machine protocol below. Unit tests cannot
    substitute for it.

## 10. Residual and hardware-only risks

No managed corporate hardware was used. The following remain unverified even
after the code defects above are fixed:

- Native Windows resolution of `PROGRAMFILES`, `PROGRAMFILES(X86)`, and
  `LOCALAPPDATA`, including per-user Chrome and the questionable
  `C:\Users\Default\AppData\Local` fallback.
- Creation/permissions/locking of the configured persistent user-data-dir on a
  policy-managed Windows host.
- Real detached Chrome/Edge launch and CDP attach under enterprise policy,
  including whether remote debugging is disabled or constrained.
- Whether the exact attached browser uses the OS client-certificate store and
  presents the expected certificate to the internal site.
- Whether the certificate picker, Windows integrated-auth prompt, or Conditional
  Access UI appears once, every launch, or in headless mode.
- Cookie/SSO lifetime across agent restart, browser crash, sleep/wake, and
  headed-to-headless reuse.
- Behavior when a user already has Chrome open with the default profile, port
  9222 is occupied, or enterprise policy forbids the requested data directory.
- Whether Chrome and Edge on the target fleet are equally managed. Static
  candidate order and a dated comment are not fleet evidence.

A release candidate needs an end-to-end run on the target managed Windows
machine: clean toggle-off baseline, enrolled launch, certificate-authenticated
internal read/form action, public-page isolation, concurrent mixed sessions,
cleanup/toggle-off, crash/restart, sleep/wake, and port-collision recovery. Record
the executable identity, profile directory, CDP owner, prompt frequency, and
network origin transitions. Until then, all claims about real certificate and
SSO behavior remain hypotheses.
