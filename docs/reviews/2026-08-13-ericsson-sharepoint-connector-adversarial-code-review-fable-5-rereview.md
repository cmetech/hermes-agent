# Adversarial code review (re-review) — Ericsson SharePoint connector remediation

**Reviewing model:** Claude Fable 5 (`claude-fable-5`), reviewer short name `fable-5`.
**Platform:** macOS (Darwin 25.5.0), local checkouts, no network.
**Review date:** 2026-08-13.
**Prior review:** `docs/reviews/2026-08-13-ericsson-sharepoint-connector-adversarial-code-review-fable-5.md`
(verdict: DO NOT ENTER TASK 12 — two HIGH: SP-H1 approval grain, SP-H2 retry deadline/cancel/Retry-After).
**Prompt:** `docs/reviews/2026-08-13-ericsson-sharepoint-connector-adversarial-code-review-prompt.md`.

This re-review verifies whether the remediation actually closes the two HIGH
defect classes (not merely that tests pass), and whether the additional changes
introduced any regression. No production code, tests, generated files, refs,
branches, or worktrees were modified. No standalone security/threat-model
workflow, live service, real credential, release, push, or brand mutation was
attempted. Reproductions and mutations ran in disposable detached worktrees under
a private `mktemp -d` and were reverted; those worktrees were removed after the
report was written. The only persistent write is this report.

---

## 1. Remediation candidates and identity (verified)

| Repository | Prior candidate | New remediation candidate | Relationship |
|---|---|---|---|
| `ericsson-capabilities` | `fdb83a7859456776556d99274284c01acc05de10` | `5a931bb047acd5367621db9a83890ca7dd0a67cd` | prior is ancestor; +4 commits |
| `hermes-agent` | `dea2900d19665ccd3119963fe8b60a0f529a9ba8` | `84b2a39e608076014a3574df998fcfe486c146fa` | prior is ancestor; +5 commits |

- New source commits: `3fea2d9` (bind write approvals to exact arguments),
  `70ceb5b` (propagate operation controls to Graph), `a3ebd07` (trust exact
  tenant copy monitors), `5a931bb` (preserve root and control invariants).
- New Hermes commits: `6c94c2092` (bound retry waits by operation controls),
  `b08253a5d` (validated external async monitors), `dcae80a04` (reject misaligned
  upload resume offsets), `4f80d91b9` (vendor remediation), `84b2a39e6`
  (ledger docs).
- **`vendoredFrom` = `5a931bb047acd5367621db9a83890ca7dd0a67cd`** (exact source
  remediation SHA).
- **Plugin byte-parity:** all 13 `plugins/ericsson-sharepoint/*` files
  byte-identical source↔vendor; source and Hermes file lists identical.
- **Connector-neutral:** `tools/microsoft_graph_client.py` contains no
  `ericsson`/`sharepoint`/tenant reference (the copy-monitor authority is a
  caller-injected `monitor_url_validator`, no connector id in generic code).
- **Neighbors:** no Jira/GitLab/Teams **production** byte changed in the new
  Hermes range; `apps/desktop/**` untouched (prior Desktop typecheck/lint/UI
  result stands).
- Source range `6b178d1..5a931bb` leaves `plugins/ericsson-jira`,
  `plugins/ericsson-gitlab`, and the Jira/GitLab skills/workflows unchanged.

Root checkouts preserved: `ericsson-capabilities` `main` @ `fdb83a7`,
`hermes-agent` `base` @ `aac5eb45`, untracked material intact. Remediation lives
on `fix/ericsson-sharepoint-review-remediation` in both repos; nothing merged or
pushed.

---

## 2. Overall verdict

## **READY FOR TASK 12**

Both HIGH findings are genuinely closed at the code level, each proven by an
independent adversarial reproduction and confirmed by a purpose-built regression
test that fails when the guard is reverted. The residual async-copy-monitor risk
I flagged last time is now handled defensively in code. No new CRITICAL or HIGH
defect was found. One pre-existing test-quality gap persists (the OData next-link
origin guard is correct but untested) — a MEDIUM-class note, not a blocker.

---

## 3. Finding-by-finding disposition

### SP-H1 (was HIGH) — write-approval grain → **RESOLVED**

`plugins/ericsson-sharepoint/__init__.py` `require_write_approval` now binds
`rule_key = f"{tool_name}:{sha256(canonical(args))}"` and puts the tool + exact
canonical arguments into `message` — the same shape as the accepted Jira
remediation.

- **Adversarial repro (no network):** two different `sharepoint_recycle_item`
  targets now produce **different** `rule_key`s
  (`sharepoint_recycle_item:27783e36…` vs `…:4d146e15…`) and the prompt message
  **contains the target** (`critical.xlsx`). The host approval cache keys on
  `plugin_rule:{rule_key}`, so a `[session]`/`[always]` decision no longer widens
  to other targets, and the first approval is target-bearing.
- **Mutation SM1:** reverting `rule_key` to `tool_name` fails the new named test
  `test_write_approval_binds_cache_identity_and_prompt_to_exact_target`.
- Invariant 14 / decision 21 now hold.

### SP-H2 (was HIGH) — deadline/cancellation/Retry-After in retry sleeps → **RESOLVED**

The generic client (`tools/microsoft_graph_client.py`) now threads
`deadline`/`cancel_check` through `_request`, `iterate_pages`,
`collect_paginated`, `download_to_file`, `upload_small`, `upload_via_session`,
`_put_upload_chunk`, `poll_async_operation`, and `start_async_operation`; every
sleep goes through `_controlled_sleep`, which caps the delay at
`MAX_GRAPH_RETRY_DELAY_SECONDS = 60`, raises `MicrosoftGraphDeadlineError` when
the delay would exceed the remaining budget, and slices the wait into 0.25 s
cancellable increments. `_retry_delay` also caps `Retry-After` at 60 s, and
`_request_timeout` clamps the httpx timeout to the remaining budget. Every
SharePoint operation entry point (`resolve_url`, `get_item`, `list_items`,
`list_owned_sites`, `download`, and all five writes) now computes
`deadline = monotonic() + timeout_seconds` and passes it plus `cancel_check`
into the client.

- **Adversarial repro (no network):** `Retry-After: 3600` now sleeps 60 s, not
  3600 s (H2a); a 50 s retry under a 30 s deadline raises
  `MicrosoftGraphDeadlineError` instead of overshooting (H2b); a 60 s retry
  cancelled after the first 0.25 s slice raises `MicrosoftGraphCancelledError`
  within one slice (H2c).
- **Mutations:** MG2 (neuter the deadline clamp) and MG3 (neuter cancel slicing)
  both fail `test_microsoft_graph_client.py`; the new named tests are
  `test_retry_after_is_capped_by_graph_client_policy`,
  `test_deadline_rejects_retry_delay_that_exceeds_remaining_budget`,
  `test_cancellation_interrupts_request_retry_sleep`. MG1 (removing the cap in
  `_retry_delay` only) survives but is **benign** — `_controlled_sleep`
  re-caps unconditionally, and repro H2a proves the effective sleep is 60 s.
- Invariants 7 and 8 now hold; decision 6 holds. **Bonus fix:**
  `list_owned_sites_with_graph`'s per-group `except Exception` previously
  swallowed a cancel/deadline into a `remote_unavailable` warning and continued
  (silently defeating cancellation); it now re-raises control errors — a real
  correctness improvement beyond the reported finding.

### Residual (was UAT-only) — async-copy monitor origin → **HANDLED DEFENSIVELY**

`poll_async_operation` now: uses the bearer-carrying `_request` for a
**graph-origin** monitor; for a **non-graph** monitor requires a caller-supplied
`monitor_url_validator` and polls via `_request_external_monitor`, which sends
**no Authorization header**, **rejects redirects**, and retries under the same
controls. `copy_item_with_client` supplies
`_validate_copy_monitor_url(location, source["tenant_host"])`, constraining the
monitor to https + the **exact configured tenant host** + port 443 + no
userinfo/fragment + the SharePoint `_api/v2.[01]/monitor/<id>` path. A `202`
with no `Location`, or any monitoring uncertainty, is now classified
`MicrosoftGraphAmbiguousWriteError` (reconcile), never a false success.

- **Adversarial repro (no network):** valid tenant monitors accepted; off-tenant
  host, non-https, userinfo, wrong path, and fragment all rejected; a real
  tenant-origin monitor GET carries **`Authorization: None`** (bearer not
  forwarded); an off-tenant monitor after a `202` yields `ambiguous_write` with
  no cross-origin GET.
- **Mutation SM2:** neutering `_validate_copy_monitor_url` fails
  `test_w06_copy_polls_async_completion_once_without_duplicate_create`.
- Invariants 6, 12, 15 hold for the copy path. Whether real SharePoint returns a
  tenant-origin vs graph-origin monitor is now immaterial to safety; the live
  URL shape remains a functional UAT observation, not a security exposure.

### Additional hardening verified

- **Drive-root mutation rejection (was a §13 residual):** `_ITEM_SELECT` now
  requests the `root` facet and the projection exposes `is_drive_root`;
  `move`/`copy`/`recycle` reject `is_drive_root is True` instead of the old
  empty-id heuristic. **Mutation SM3** (force `is_drive_root` false) fails
  `test_drive_root_cannot_be_moved_copied_or_recycled`.
- **Upload resume-offset alignment (M5 survived in the first review):** now
  covered — **M5-redo** (drop the 320-KiB alignment check) fails
  `test_upload_session_rejects_misaligned_server_resume_offset`.
- **Control-error projection:** the plugin handler maps
  `SharePointCancelledError`→`cancelled`, `SharePointDeadlineError`→
  `limit_exceeded`, and translates the Graph control errors, with safe
  category-specific messages.

---

## 4. Backward-compatibility and regression checks

- All new client parameters are keyword-only with `None` defaults, so existing
  app-only callers (Teams `GraphCredentials.from_env` / `MicrosoftGraphToken
  Provider`) pass `deadline=None` → `_request_timeout` returns `self.timeout`
  unchanged. Teams suites (`test_teams.py`, `test_teams_dotenv_isolation.py`,
  `test_teams_pipeline_runtime_wiring.py`, `test_teams_pipeline_plugin.py`) green.
- Generic Graph production remains connector-neutral; the copy-monitor authority
  is injected by the caller, not embedded in core.
- Upstream ledger `microsoft-graph-connectors.yaml` updated to record the capped
  interruptible retry waits, the validated external monitor, bearer-safety, and
  non-replay invariants; `check_upstream_customizations.py` exit 0.

---

## 5. Deterministic gates (rerun from the remediation worktrees)

- **Source (`5a931bb`):** `pytest tests/test_sharepoint_*.py` → all pass
  (including the previously env-flaky `test_document_intake_compiles_with_real_
  archon_authority`, hardened); `test_teams_plugin.py` green;
  `build_catalog.py --check` clean; `validate_catalog.py` `{"ok": true}`;
  Jira/GitLab source paths unchanged in range.
- **Hermes (`84b2a39e`):** targeted 15-file suite → **passed, 0 failed**
  (graph client + large transfer baseline **33/33**, up from 26 — 7 new tests);
  `check_upstream_customizations.py` exit 0; `vendor-ericsson.test.mjs`
  **47/47**; `git diff --check` clean.
- **Desktop:** unchanged by the remediation — prior green typecheck/lint/UI
  result stands.

Mutation ledger (private detached worktrees, reverted; no commit):

| # | Mutation | Result |
|---|---|---|
| SM1 | Approval `rule_key` → `tool_name` | **caught** (`test_write_approval_binds_cache_identity_and_prompt_to_exact_target`) |
| SM2 | Neuter `_validate_copy_monitor_url` | **caught** (`test_w06_copy_polls_async_completion_once…`) |
| SM3 | Force `is_drive_root` false | **caught** (`test_drive_root_cannot_be_moved_copied_or_recycled`) |
| MG2 | Neuter `_controlled_sleep` deadline clamp | **caught** (graph client suite) |
| MG3 | Neuter cancel slicing | **caught** (graph client suite) |
| M5-redo | Drop 320-KiB resume-offset alignment | **caught** (`test_upload_session_rejects_misaligned_server_resume_offset`) |
| MG1 | Remove `Retry-After` cap in `_retry_delay` only | **survived — benign** (`_controlled_sleep` re-caps; repro H2a proves 60 s) |
| M1-redo | Drop OData next-link origin validation | **survived** — correct code, still untested (see §6) |
| MG4 | Remove `_request` loop-top control check | **survived — benign** (control still enforced post-token/post-request + in sleep clamp) |

---

## 6. Remaining non-blocking items

- **Test-gap (persists from the first review, MEDIUM-class, not a code defect):**
  the `iterate_pages` OData next-link origin guard (`_validate_graph_origin`) is
  present and correct but survives mutation M1 — no test asserts a cross-origin
  next link is rejected. Recommend adding one; the production behavior is safe.
- **Live-tenant UAT (functional, not security):** the real SharePoint copy
  monitor URL shape/origin, real delegated/app-only/Azure CLI identities, real
  tenant URL variants, CDN redirects, large-file resume, browser
  enrollment/audit under Conditional Access, Windows delegated-cache path
  semantics, and restart/upgrade persistence remain Task-12 installed-UAT items.
  The copy path is now safe regardless of which origin the monitor uses.

---

## 7. Confirmation of constraints honored

No standalone security/threat-model/security-review workflow, penetration test,
or exploit exercise was run. No live Microsoft/Ericsson service, real credential,
or malicious payload was used — every reproduction used `httpx.MockTransport`,
synthetic sentinel values, isolated temporary state, and no network. No
production code, test, generated file, ref, branch, or worktree under review was
modified; the two detached re-review worktrees and their private parent were
created under `mktemp -d`, used read-only against immutable Git objects and in
disposable copies for mutation, and removed after this report. No release, push,
PR, workflow dispatch, brand mutation, or Task 12 action was attempted. The only
persistent write is this report.

---

SHAREPOINT CANDIDATE IS READY FOR TASK 12 INSTALLED RELEASE VALIDATION.
