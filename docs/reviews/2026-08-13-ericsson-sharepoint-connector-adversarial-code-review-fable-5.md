# Adversarial code review — Ericsson SharePoint connector

**Reviewing model:** Claude Fable 5 (`claude-fable-5`), reviewer short name `fable-5`.
**Platform:** macOS (Darwin 25.5.0), local checkouts, no network.
**Review date:** 2026-08-13.
**Prompt:** `docs/reviews/2026-08-13-ericsson-sharepoint-connector-adversarial-code-review-prompt.md`.

This is a functional-correctness adversarial review of the immutable SharePoint
connector candidate before SharePoint Task 12. No production code, tests,
generated files, refs, branches, or worktrees were modified. No standalone
security/threat-model workflow, live Microsoft/Ericsson service, real
credential, release, push, or brand mutation was attempted. The only persistent
write is this report. Reproductions and mutations ran in disposable detached
worktrees under a private `mktemp -d` and were reverted; those worktrees were
removed after the report was written.

---

## 1. Repository states, immutable SHAs, hashes, ranges, counts (verified)

| Repository | Branch / HEAD | Tree | Status |
|---|---|---|---|
| `ericsson-capabilities` (root) | `main` @ `fdb83a7859456776556d99274284c01acc05de10` | `73464cf97b51c6086d04201db0af49f0b5f3adbf` | matches prompt; 3 preserved GitLab/Jira worktrees untouched |
| `hermes-agent` (root) | `base` @ `aac5eb45420b4241d525d4deea21c2e41ff0f5da` | — | one doc-only commit ahead of the candidate; untracked `.otto/`, `docs/*` preserved |
| Hermes candidate under review | `dea2900d19665ccd3119963fe8b60a0f529a9ba8` | `fbb3637536c433fb32192597453b5fcbaa7ebaba` | immutable verdict target (parent of root tip) |
| `loop_24` (legacy) | pinned `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6` | — | matches pin |

- **Ancestry verified:** `6b178d1..fdb83a7` (source) and `911b7e7..dea2900d`
  (Hermes), both `--is-ancestor` true. `aac5eb45` (root tip) has `dea2900d` as
  parent.
- **Source range `6b178d1..fdb83a7`:** 14 commits, 48 changed files, 6,185
  insertions / 27 deletions — matches prompt. Trailing commits are the
  Jira/SharePoint sync merge (`5835546`), the disabled-by-default correction
  (`95f0ae8`), and the authenticated workflow-package correction (`fdb83a7`).
- **Hermes range `911b7e7..dea2900d`:** 8 commits, 37 changed files, 5,930
  insertions / 101 deletions — matches prompt.
- **Legacy byte-identity:** `utils/sp_files.py` (`4aac0bb`), `utils/sp_audit.py`
  (`b47bea8`), `custom_components/ericsson_parsers/sharepoint_files_fetcher.py`
  (`c119eee`) are byte-identical between `fc3bf26` and `8ca26f8` — the later
  snapshot is valid behavior evidence.
- **Immutable input hashes — all three match; `REVIEW_INPUT_CHANGED` NOT raised:**
  design `93e2f4d2…1cc6`, SharePoint plan `82b62c92…e2ea`, behavior map
  `b67f7628…5c2f`.
- **`vendoredFrom` = `fdb83a7859456776556d99274284c01acc05de10`** (exact source
  candidate).

Changed-path classes reviewed. **Source:** SharePoint production
(`plugins/ericsson-sharepoint/*.py`, `config.schema.json`, `plugin.yaml`),
plugin skills, router skill, workflow + sidecar, behavior map / flow / config /
onboarding docs, generated catalog, `sets/ericsson.json`, tests/fixtures.
**Hermes:** generic Graph production (`tools/microsoft_graph_identity.py` [new],
`tools/microsoft_graph_client.py`, `tools/microsoft_graph_auth.py`), vendored
plugin/skills/workflow bytes, capability manifests, two upstream ledgers,
cross-surface/distribution tests. No Jira/GitLab/Teams **production** byte
changed in the Hermes range (verified: `git diff --name-only … | grep -iE
'jira|gitlab|teams' | grep -v test` → none).

---

## 2. Overall verdict

## **DO NOT ENTER TASK 12**

Two HIGH findings block the immutable SharePoint candidate. Both are
deterministic code defects proven on this platform without a live service. None
is CRITICAL: credentials/tokens/cookies are not disclosed or redirected, the
Graph bearer is stripped before CDN redirect access, the browser authority stays
core-owned, source→vendor byte identity is exact (13/13 plugin blobs + skills +
5/5 workflow-package/sidecar files), no unapproved *first* mutation executes from
a model path, and no shared Graph/Teams/Jira/GitLab path is corrupted.

- **SP-H1 (HIGH):** the SharePoint write-approval hook keys the human-approval
  cache on the **tool name alone** and shows a **target-blind constant message**,
  so a single `[s]ession`/`[a]lways` decision auto-authorizes every subsequent
  `recycle`/`upload`/`move`/`copy`/`create_folder` on **any** item, and even the
  first approval hides which item is mutated. This regresses the accepted JIRA-H2
  remediation that lives in the **same candidate tree** (the Jira hook binds args
  into the rule key and shows the issue key/body).
- **SP-H2 (HIGH):** the connector's operation deadline and cooperative
  cancellation **do not reach the Graph client's retry sleeps**, and
  `Retry-After` is **uncapped** (the legacy code capped it at 60 s). A throttled
  operation (429/503 with `Retry-After`) sleeps far past its configured
  `timeout_seconds`, uninterruptible by cancellation — violating the named
  invariant that deadlines/cancellation reach "retry delays."

The connector's core is otherwise strong and survived adversarial probing and
mutation: strict tenant-host URL parsing, the redirect bearer-strip + host
constraint, `.part` atomicity and universal cleanup, the args-bound single-use
`PluginToolAdmission`, no-blind-retry on ambiguous writes, exact source→vendor
parity, the disabled-by-default correction, and the workflow-package
distribution fix (the Jira review's JIRA-H3 class does **not** recur here — all
five workflows including `sharepoint-document-intake` are in the digest-verified
package and are asserted by the installed-distribution e2e test).

---

## 3. Findings (CRITICAL before HIGH)

| ID | Sev | Title | File / symbol | Task | Invariant |
|---|---|---|---|---|---|
| SP-H1 | HIGH | Write approval is tool-name-grained + target-blind → one session/always approval widens to arbitrary recycle/upload/move/copy | `plugins/ericsson-sharepoint/__init__.py:65-72` | 8 | 14 |
| SP-H2 | HIGH | Operation deadline + cancellation never reach Graph retry sleeps; `Retry-After` uncapped → operation hangs past its timeout, uninterruptible | `tools/microsoft_graph_client.py:621-707,299-303,524-526`; `agent/retry_utils.py:69-73` | 3 | 7, 8 |

No CRITICAL findings.

### SP-H1 (HIGH) — SharePoint write approval is keyed on the tool name alone and shows no target, so one session/always approval authorizes arbitrary later mutations

1. **ID / severity:** SP-H1 / HIGH.
2. **Title:** `require_write_approval` returns `rule_key = tool_name` and a
   constant, target-blind message for every SharePoint write, so the host
   approval gate caches one `[s]ession`/`[a]lways` decision under
   `plugin_rule:sharepoint_recycle_item` (etc.) and short-circuits every
   subsequent write of that tool to any URL/body with no further human review;
   even the first approval never shows the item being mutated.
3. **SHA / task:** Hermes `dea2900d` (source `fdb83a7`), Task 8.
4. **Production site:** `plugins/ericsson-sharepoint/__init__.py:65-72`:
   ```python
   def require_write_approval(tool_name, _arguments, **_kwargs):
       if tool_name not in _WRITE_TOOLS:
           return None
       return {"action": "approve",
               "message": "Approve Ericsson SharePoint mutation",
               "rule_key": tool_name}
   ```
   `_arguments` (which carries `url`/`source_url`/`folder_url`/`name`) is
   received and discarded. Consumed by
   `hermes_cli/plugins.py:resolve_pre_tool_admission` →
   `tools/approval.py:request_tool_approval`, where a non-empty `rule_key`
   becomes `pattern_key = f"plugin_rule:{rule_key}"` and a cached
   `[s]ession`/`[a]lways` approval short-circuits via `is_approved(...)`.
5. **Invariant / contract:** Invariant 14 ("interactive approval … covers the
   exact tool and current arguments for upload, folder creation, move, copy, or
   recycle. … a sibling invocation cannot widen it"); specific decision 21
   ("bound to the exact current write tool and arguments and cannot be widened
   from a tool name alone"); design decision 10. The **same candidate tree**
   already implements the correct pattern for Jira: `plugins/ericsson-jira/
   __init__.py:118-140` binds `sha256(canonical_args)` into `rule_key` and puts
   the issue key + body into `message` — the accepted JIRA-H2 remediation. The
   SharePoint hook regressed it.
6. **Trigger / state:** In interactive chat/CLI a user answers `[s]ession` or
   `[a]lways` to a first SharePoint write approval (a natural choice while
   reorganizing a document set). Thereafter the model may call the same write
   tool for any target with no human seeing it.
7. **Wrong result / consequence:** Every later `sharepoint_recycle_item`,
   `sharepoint_move_item`, `sharepoint_copy_item`, `sharepoint_upload`, or
   `sharepoint_create_folder` of that tool is auto-approved. These are
   destructive/relocating tenant mutations (recycle-bin delete, cross-site move,
   overwrite upload). Each still mints a fresh args-bound `PluginToolAdmission`,
   so the audit trail *looks* legitimate while no human reviewed the specific
   object. Even the first approval is target-blind — the gate renders only
   `"Approve Ericsson SharePoint mutation"`; the item URL/name never appears —
   so the operator cannot see *which* file is being recycled or moved. This is
   exactly the "widened from a tool name alone" case decision 21 forbids, on
   higher-consequence operations than the Jira comment that JIRA-H2 (HIGH)
   covered.
8. **Direct evidence:** Executable repro against the immutable vendored plugin
   bytes (blob-identical to source), `repro_approval.py`, no network:
   ```
   recycle A directive: {'action':'approve','message':'Approve Ericsson SharePoint mutation','rule_key':'sharepoint_recycle_item'}
   recycle B directive: {'action':'approve','message':'Approve Ericsson SharePoint mutation','rule_key':'sharepoint_recycle_item'}
   host approval pattern-key: pattern_key = f"plugin_rule:{key_suffix}"
   jira rule_key A: jira_add_comment:23e574b6…   jira rule_key B: jira_add_comment:27306d1e…  (differ; message contains ABC-1)
   ```
   Two entirely different recycle targets produce an identical `rule_key` and an
   identical target-blind message; the Jira hook in the same tree produces
   per-args keys and a target-bearing message.
9. **Why not already prevented:** The args-bound, single-use admission
   (`arguments_sha256` + `claim_once`, `hermes_cli/plugins.py:2766-2789`) makes
   each *mechanical* dispatch exact, which gives false assurance — but the
   *human authority* that unlocks it is cached on the tool name. `test_sharepoint_
   writes.py::test_w08_all_writes_require_exact_backend_admission_and_reject_
   argument_claims` asserts a single write is gated and caller-injected admission
   is rejected (mutation M4 confirms it fails when the check is bypassed), but no
   test asserts a second, different-target write in the same session re-prompts
   a human, nor that the prompt text contains the URL/name. Task 12 UAT step 10
   would not catch it (it checks approval occurs, not its grain).
10. **Smallest safe fix:** In `require_write_approval`, mirror the Jira hook:
    derive `rule_key = f"{tool_name}:{sha256(canonical(args))}"` (per-write
    grain) **and** put the exact target (URL / source_url / folder_url + name)
    into `message`, so both the cache grain and the human prompt are per-write.
    No SharePoint scope widening.
11. **Missing regression test:** Approve `[s]ession` for `sharepoint_recycle_item`
    on URL-A, then assert a second call with URL-B re-invokes the approval gate
    (not auto-approved), and assert the rendered message contains the item URL.

### SP-H2 (HIGH) — The operation deadline and cancellation never reach the Graph client's retry sleeps, and `Retry-After` is uncapped, so a throttled write/read hangs past its configured timeout

1. **ID / severity:** SP-H2 / HIGH.
2. **Title:** `MicrosoftGraphClient._request` (used by resolve, `get_item`,
   listing/owned-site pagination, `create_folder`, `move`, `recycle`, and
   upload-session creation) takes **no deadline and no cancel_check**, and its
   retry sleep uses the **raw, uncapped** `Retry-After` value. The connector
   computes `deadline = time.monotonic() + config.timeout_seconds` and threads it
   only into `_control` checks *between* awaits, never into the HTTP retry sleep,
   so a 429/503 with a large `Retry-After` sleeps far past the configured timeout
   and cannot be interrupted by cancellation. `download_to_file`,
   `poll_async_operation`, and `_put_upload_chunk` have the same sleep gap.
3. **SHA / task:** Hermes `dea2900d` (source `fdb83a7`), Task 3.
4. **Production site:** `tools/microsoft_graph_client.py:621-631` (`_request`
   signature — no `deadline`/`cancel_check`), `:698-701` (retry sleep with no
   control check), `:299-303` (download retry sleeps
   `api_error.retry_after_seconds` uncapped, no control check), `:524-526`
   (`_put_upload_chunk` sleep), `:557-561` (`poll_async_operation` sleeps
   `retry_after` without clamping to `deadline`). `agent/retry_utils.py:69-73`:
   `parse_retry_after_seconds` returns `max(0.0, float(text))` — **no cap**. The
   frozen legacy behavior capped it: `utils/sp_files.py:85`
   `return min(float(ra), 60.0)`. `operations.py` never passes a deadline into
   any `_request`-based call (`iterate_pages`, `get_json`, `post_json`,
   `patch_json`, `delete`).
5. **Invariant / contract:** Invariant 8 ("Cancellation/deadline controls reach
   HTTP streaming, **retry delays**, pagination, upload chunks, async polling …")
   and invariant 7 ("polling attempts … are bounded in production"); behavior-map
   G-03 freezes "**bounded** exponential/`Retry-After` delay"; design "Failure,
   retries, and cancellation: Cancellation propagates through … HTTP/Graph
   client … Cleanup occurs on every exit path." Specific decision 6 ("`Retry-After`
   … cannot multiply across pagination, redirects, upload sessions, or polling").
6. **Trigger / state:** Ordinary Microsoft Graph/SharePoint throttling. Graph
   routinely emits `429`/`503` with a `Retry-After` header; under sustained
   throttling or service degradation the value can be large. The operation is
   bounded by the operator to `timeout_seconds` (default 60), and the user
   expects cancellation/interrupt to work.
7. **Wrong result / consequence:** A single throttled page/request sleeps the raw
   header value (e.g. `Retry-After: 3600` → 3600 s), up to `max_retries` (default
   3) times, inside an operation the operator bounded to 60 s — and neither the
   connector's deadline nor an interrupt (`is_interrupted`) reaches the sleep, so
   the agent/Kanban worker/cron job is unresponsive for the full duration.
   Because `_request` has no deadline parameter at all, the dominant operation
   paths (resolve, list, all writes) never observe the deadline during retries;
   `poll_async_operation` has a deadline but sleeps past it. The legacy 60 s cap
   was removed, so the hang is unbounded from the client side.
8. **Direct evidence:** Executable repro `repro_graph.py` (httpx.MockTransport,
   no network), against the immutable client:
   ```
   R2  _request retry sleeps with Retry-After 3600 -> [3600.0, 3600.0]
       (timeout_seconds bound and cancellation are absent from the _request retry path)
   R2b poll sleeps with deadline=5s and Retry-After 86400 -> [86400.0, 86400.0, 86400.0]
       (poll_async_operation sleeps a full day past a 5-second deadline)
   ```
   `_request`'s signature confirms no `deadline`/`cancel_check`;
   `parse_retry_after_seconds` returns the raw value; the legacy code capped at
   60 s.
9. **Why not already prevented:** The connector *computes* a deadline and passes
   it into `_control` between awaits and into `download_to_file`/`poll` body
   iteration, giving the appearance of a bound; but the HTTP retry sleep — the
   one place a throttled request actually waits — is never gated. No test
   asserts that a `Retry-After` sleep is clamped to the deadline or interrupted
   by cancellation (the large-transfer suite exercises retries with small
   synthetic delays only). Task 12 UAT would surface it only if a live tenant
   happened to throttle with a large header during the session.
10. **Smallest safe fix:** Thread `deadline`/`cancel_check` into `_request` and
    clamp every retry sleep to `min(retry_after, max(0, deadline - clock()))`,
    re-checking `_check_control` before and after the sleep; reinstate a hard cap
    on `Retry-After` (the legacy 60 s, or the operation's remaining budget).
    Apply the same clamp in `_put_upload_chunk` and `poll_async_operation`. Have
    the SharePoint operations pass their computed deadline into every
    `_request`-based call.
11. **Missing regression test:** A test that drives a 429 with `Retry-After:
    100000` under a fake clock and a `deadline`/`cancel_check`, and asserts the
    client raises a deadline/cancel error within the bound rather than sleeping
    the full header value; and a mutation that removes the clamp must fail it.

---

## 4. Task 1–11 traceability matrix

| Task | Concern | Verdict | Note |
|---|---|---|---|
| 1 | Frozen legacy SharePoint + current Graph/Teams behavior | **proven** | Behavior map hash matches; legacy files byte-identical across the two pins; dispositions cover sp_files/sp_audit/fetcher rows. |
| 2 | Generic Graph identity, cache, readiness, app-only compat | **proven** | `auto`/`delegated_msal`/`app_only`/`azure_cli` selection deterministic; POSIX no-follow/atomic/private cache; app-only preserved; Teams suite green. |
| 3 | Bounded Graph download/upload/async primitives | **contradicted** | Redirect bearer-strip, `.part` atomicity, offset validation, no-blind-restart correct; SP-H2 (deadline/cancel/Retry-After) violates the bound/cancel invariants. |
| 4 | Standalone descriptor, config, setup actions, independent readiness | **proven** | `kind: standalone`, `enabled:false`; Graph-ready vs `browser_enrollment_required` independent; setup actions delegate to core browser authority; secret write-only. |
| 5 | Tenant URL, site, drive, path, DriveItem resolution | **proven** | Strict host allowlist, UI-prefix strip, percent/UTF-8/segment safety, default-vs-named drive with ambiguity rejection, share-id and id/path fallback. |
| 6 | Bounded listing/filtering/recursion/downloads/local artifacts | **proven** | Depth/item/page/byte/cycle bounds + truncation warnings; symlink/traversal/device/name rejection; relative-path evidence; one-op interactive auth exact (M7 caught). |
| 7 | Owned-site discovery + browser-backed audit | **proven** | Graph owned-site pagination bounded with per-group partial warnings; audit same-origin, core-owned session, category/aggregate truncation status (M8 caught). |
| 8 | Approval-aware writes | **contradicted** | No-blind-retry + ambiguous reconciliation + args-bound admission correct (M4 caught); SP-H1 (approval grain/target-blindness) violates exact-write authority. |
| 9 | Skills, router, workflow, onboarding, UAT | **proven** | Skills carry no client/credentials; router thin/discoverable; workflow flat-requires + exact tools + stops at acquisition; real Archon compile passes from source root. |
| 10 | Source closure, Teams invariant, corrections | **proven** | Teams suite green; corrections (Azure CLI enable, ambiguous-write no-retry, outcome surfacing, disabled-default, workflow package) are behavior-preserving. |
| 11 | Jira sync, regeneration, vendoring, installed surfaces, merge | **proven** | 13/13 plugin + skills + 5/5 workflow-package/sidecar byte-identical; `vendoredFrom` exact; no Jira/GitLab/Teams production byte changed; ledgers present; gates green. |

---

## 5. Verdict on the 21 non-negotiable invariants

1. **Source ownership / byte identity** — **holds.** `vendoredFrom` =
   `fdb83a7`; 13/13 plugin blobs, sharepoint skill, catalog, and all five
   workflow-package/sidecar files byte-identical source↔vendor; installed
   distribution e2e asserts the files land.
2. **Disabled means absent** — **holds.** `enabled:false` standalone; executable
   code not imported until enabled; static config + thin router remain
   discoverable; changes affect fresh conversations only.
3. **One profile-scoped config authority** — **holds.** `SharePointConfiguration`
   resolves settings/secret/roots/bounds/browser from the executing profile;
   `client_secret` write-only; credentials never imply enablement.
4. **Auth selection deterministic / non-interactive by default** — **holds.**
   `select_auth_mode` picks only fully-configured modes; partial → readiness
   error; Azure CLI gated on `azure_cli_enabled`; operations use
   `interactive_allowed=False`; interactive only via the explicit setup action.
5. **Identity material private / isolated** — **holds.** `GraphIdentityConfig`
   `client_secret` `repr=False`; token-cache errors carry no bytes; token-request
   errors redact the exact secret (`_redact_value`); repr/results carry no
   token/cookie/CDP/profile-path; audit JS returns no raw payloads.
6. **Generic Graph request authority never drifts** — **holds.** `_resolve_url`/
   `_validate_graph_origin` pin scheme+host+port for initial URLs, next links,
   upload URLs, and async monitor locations; the Graph bearer is stripped before
   the CDN redirect (M2 caught).
7. **Bounds apply before uncontrolled work** — **partially violated (SP-H2).**
   Pages/items/depth/bytes/rows/sites/chunks are bounded during collection; but
   retry-sleep duration is unbounded from the client side (uncapped
   `Retry-After`).
8. **Cancellation and deadlines reach real work** — **violated (SP-H2).**
   Streaming/pagination/staging/cleanup are covered, but retry delays in
   `_request`/`download`/`poll`/`upload-chunk` are not reached by
   deadline/cancellation.
9. **SharePoint identity is exact** — **holds.** Host allowlist, UI-prefix +
   single-letter-mode strip, malformed-percent/UTF-8/control/`.`/`..` rejection,
   default-vs-named drive with ambiguity rejection, share-token and drive/item-id
   fallback; a folder vs file is distinguished by facet.
10. **Reads and listings truthful** — **holds.** Stable relative paths, in-loop
    filtering, cycle/dup handling, explicit `truncated`/`warnings`, malformed-row
    rejection, safe tenant-scoped web URLs, no raw payloads.
11. **Local file boundaries exact** — **holds.** Download confined to configured
    root or a single-use interactive root; upload reads only its root; symlink
    components, traversal, device/special files, unsafe names, absolute-path
    projection, and unattended expansion are rejected (M7 caught). Note: `move`/
    `recycle` root-rejection keys on empty item-id rather than root-ness (§13,
    remote-rejected, not a finding).
12. **Redirected downloads confidential / atomic** — **holds.** Redirect narrow
    (https/no-userinfo/port-443/no-fragment), bearer stripped, byte-bounded,
    `.part` + `os.replace`, universal cleanup (M2, M3 caught).
13. **Resumable uploads never guess progress** — **holds.** 320-KiB alignment,
    `Content-Range`, monotonic/aligned/in-range resume offset, chunk/session
    bounds, terminal-vs-ambiguous handling, no blind restart; source re-staged
    and stability-checked.
14. **Write authority binds the exact mutation** — **violated (SP-H1).**
    Args-bound admission is exact, but interactive approval is widened from the
    tool name alone and is target-blind.
15. **Write recovery truthful / non-replaying** — **holds.** `retry_ambiguous=
    False` on every write; transport failure → `AmbiguousWrite`; no unobserved
    object reported created/moved/copied/recycled/uploaded.
16. **Browser authority core-owned** — **holds.** Uses the configured enrolled
    profile via `browser_profiles`/`registry`/`manager.acquire(attach_global=
    False)`; trusted-origin validated; releases only its owned key; no raw
    port/profile/process claim (M8 caught).
17. **Audit readiness / results independent + truthful** — **holds.** Missing
    enrollment blocks only `sharepoint_audit_permissions`; Graph tools keep Graph
    readiness; per-category/site complete/partial/truncated/unreachable status;
    a failed category cannot become empty-complete.
18. **Skills reason; tools integrate** — **holds.** No Graph/browser client,
    credential, or hidden model call in any skill; no connector-local LLM.
19. **Workflow contracts executable / honest** — **holds.** Flat
    `requires:[ericsson-sharepoint]`, exact `allowed_tools`, stops after
    acquisition, no parse/generate claim; real Archon compile passes; package is
    digest-verified and distributed (JIRA-H3 class does not recur).
20. **Every installed surface uses one plugin** — **holds.** CLI/Desktop/
    gateway/Kanban/cron/Archon resolve the profile-scoped registered tool;
    Desktop is a backend projection.
21. **Generic + neighboring behavior preserved** — **holds.** No connector id in
    generic Graph code; app-only/Teams compatible; no Jira/GitLab/Teams
    production byte changed; recycle-only (no permanent delete); ledgers +
    customization gate green.

---

## 6. Verdict on the 27 specific implementation decisions

1. `auto`/delegated/app-only/Azure-CLI reject partial/ambiguous, correct
   scopes/authority → **supported.** `_missing_fields` per mode; `authority`
   tenant-qualified; app-only/Azure-CLI require exactly one scope.
2. Delegated cache size/private/no-follow/atomic/corruption/account/refresh/
   isolation on POSIX + portable → **supported.** POSIX `O_NOFOLLOW`/`O_DIRECTORY`
   + uid/mode checks + atomic `os.replace` + fsync; corruption → cache error;
   portable path symlink-guarded. Residual: portable (Windows) path relies on
   `is_symlink`/`is_file` rather than fd-level guards — Windows-UAT item (§13).
3. Interactive auth updates only the intended cache; readiness/ops never
   interact → **supported.** `authenticate_interactively` gated on
   `interactive_allowed`; operations pass `False`.
4. Azure CLI only when permitted, reuses the adapter, no token-store copy →
   **supported.** `AzureCliMicrosoftGraphTokenProvider` builds through
   `agent.azure_identity_adapter`; `azure_cli_enabled` gate.
5. `from_env()` / `MicrosoftGraphTokenProvider` app-only callers unchanged →
   **supported.** Teams suite green; `from_env`/app-only path untouched except an
   additive secret-redaction line in error text.
6. 401 refresh, transient/429/5xx, `Retry-After`, cancel, one deadline cannot
   multiply → **contradicted (SP-H2).** Retry classification correct, but
   deadline/cancel do not reach the sleep and `Retry-After` is uncapped.
7. OData next links opaque after Graph-origin validation; first-page params not
   reapplied; loops detected → **supported (test-gap).** `iterate_pages`
   validates origin and drops params after page 1; listing tracks a `visited`
   set. Mutation M1 (drop next-link origin check) survived the Graph suite — the
   guard is correct but untested (§10, §13).
8. Redirect status/location validation, bearer strip, CDN constraint, aggregate
   bound, cleanup on every exception → **supported** (M2, M3 caught).
9. Upload alignment/`Content-Range`/monotonic offset/expiration/terminal/local
   stability/no blind restart → **supported.** Mutation M5 (drop offset
   alignment) survived the Graph suite — correct but weakly covered (§10, §13).
10. Async copy `Location`/origin/`Retry-After`/transitions/deadline/cancel/
    unknown-not-success → **partially supported / not established.** Origin +
    terminal-status handling correct and a 202 timeout is not turned into
    success; but the monitor-origin is constrained to the **graph** origin,
    whereas real SharePoint copy monitors are frequently returned on the tenant
    `*.sharepoint.com` origin — if so, `sharepoint_copy_item` fails after the
    copy is accepted (residual UAT risk, §13); and the poll sleep ignores the
    deadline (SP-H2).
11. URL parsing across legacy/root/`sites`/`teams`/encoded/malformed/userinfo/
    port/IDNA/query/fragment/lookalike → **supported.** Table-driven rejections;
    `_decode_segment` blocks malformed percent/control/UTF-8; host allowlist.
12. Default-drive aliases + underscore-internal work; named matching bounded,
    ambiguity rejected → **supported.** `_DEFAULT_LIBRARIES`/`startswith("_")`;
    named match on name or decoded webUrl tail; `len(matches)!=1` → ambiguous.
13. Drive/item ids + path fallback encode independently, not confused → **supported.**
    `_safe_id` blocks `/\?#\x00`/control; per-segment `quote(safe="")`.
14. Recursive listing enforces all limits + stable paths/filters/order/warnings
    → **supported.** BFS with depth/item/page/byte/cycle bounds and dedup
    `warn`.
15. Remote names/local paths cannot escape via traversal/Unicode/symlink/special
    /rename/absolute projection → **supported** (M7 caught). `_safe_filename` +
    `_reject_symlink_components` + `os.link(follow_symlinks=False)` + relative
    evidence.
16. One-op interactive authorization exact, unforgeable, unavailable to
    unattended → **supported.** `OneOperationFileAuthorization.consume` rejects
    unattended, checks single-use + tool-name + within-root (M7 caught).
17. Owned-site pagination bounded, retains successes after per-group failure,
    partial warnings → **supported.** Bounded pages/sites/bytes; per-group
    `remote_unavailable` warning; `partial`/`truncated`/`complete` status.
18. Browser enroll/acquire/nav/eval/release use configured profile/origin, don't
    steal reused/parallel sessions → **supported.** `attach_global=False`, owned
    key, release-only-owned (M8 caught).
19. Browser REST same-origin bounded next links; scripts/cookies/raw/CDP/paths
    don't escape → **supported.** JS enforces `absolute.origin===location.origin`,
    bounded `MAX_PAGES`; only normalized fields returned.
20. Audit category normalization + aggregate status across users/admins/roles/
    groups/lists/subsites/metadata → **supported.** Distinguishes complete/
    partial/truncated/unreachable; roles→group discovery→members chained.
21. Approval/admitted authority bound to exact write tool + args, not widened/
    bypassed → **contradicted (SP-H1).** Args-bound admission exact; interactive
    approval widened from tool name alone and target-blind.
22. Upload staging rechecks authorized regular file, detects mutation, bounds
    copy, removes staging, no absolute path → **supported.** fd + `lstat`
    dev/ino recheck, size recheck, staged copy in a temp dir, cleanup on every
    path.
23. Folder `exist_ok`/conflict/move identity/cross-drive-tenant/ETag/recycle-root
    /copy destination match intent → **supported (one weak guard).** Tenant
    equality checked; conflict behavior mapped; ETag → `If-Match`; but move/
    recycle root-rejection keys on empty item-id, which a resolved root does not
    have (§13; remote-rejected).
24. All ambiguous write paths non-retryable at plugin + generic layers →
    **supported.** `retry_ambiguous=False` on every write; outer `write_operation`
    maps `MicrosoftGraphAmbiguousWriteError` → `SharePointAmbiguousWriteError`.
25. Source-shaped + packaged workflows byte-consistent, discoverable, admit-when-
    ready, exact tools, stop after acquisition → **supported.** Loose + package
    + sidecar byte-identical; digest-verified; installed-distribution e2e asserts
    presence.
26. Vendoring preserves managed bytes/sidecars, removes only stale, exact
    provenance, no Jira/GitLab byte change → **supported.** 13/13 + 5/5 parity;
    `vendoredFrom` exact; vendor suite 47/47.
27. Tests fail when the real guard is removed → **mostly supported.** M4, M7, M8,
    M2, M3 caught; but the next-link-origin guard (M1) and the upload resume-offset
    alignment (M5) survive mutation under the Graph suite, and no test covers the
    SP-H1 approval grain or the SP-H2 deadline/Retry-After bound (§10).

---

## 7. Legacy / current / final parity matrix

| Behavior | Legacy (`fc3bf26`) | Final candidate | Disposition |
|---|---|---|---|
| URL → site/drive/item | `parse_sp_url` accepts any host/scheme/userinfo/fragment, strips UI prefix | `parse_sharepoint_url` https + tenant-allowlist + reject userinfo/fragment/malformed | safely adapted (stricter) |
| Default vs named drive | `_resolve_drive` default libs + `_`-internal; first name/webUrl match | same, but ambiguous match rejected, enumeration bounded | safely adapted |
| Drive/item id addressing | `_driveId`/`_driveItemId` avoid path-encoding | `get_item(drive_id,item_id)` + `_safe_id` | preserved |
| List folder | `_list_children` `$top=200`, follows all pages | bounded `sharepoint_list_items`, page/item/byte/depth limits | safely adapted |
| Recursive walk | `_walk` unbounded DFS | bounded BFS + cycle/depth/warnings | safely adapted (bounded) |
| Name/extension filter | comma globs, basename-or-relpath, `max_files` | in-loop `fnmatch`/suffix filter, bounded | preserved |
| Download | `_download_to` `/content`, 302 to CDN without bearer, 1 MiB chunks | `download_to_file` bearer-stripped redirect, host-constrained, byte-bounded, atomic | preserved reason, safer mechanics |
| Batch download | `cmd_batch_download` absolute paths, no aggregate bound | bounded per-op download, relative evidence, digest/size | safely adapted |
| Small/large upload | `_upload_small` / `_upload_large` full-file in memory, `replace`, unvalidated offsets | streamed chunks, aligned/validated offsets, conflict policy, no blind restart | safely adapted |
| mkdir | `cmd_mkdir` `exist_ok`→`replace` | `create_folder_with_client` same, ETag, no-retry | preserved |
| mv | `cmd_mv` rename/move, reject source root by empty path | `move_item_with_client` tenant/drive validation, ETag; root check on empty id (§13) | adapted (weak root guard) |
| cp (async) | `cmd_cp` reports 202 Location, never polls | `copy_item_with_client` polls host-constrained monitor, deadline/cancel | adapted; **monitor-origin/deadline risk** (SP-H2, §13) |
| rm | `cmd_rm --yes`, reject library root, DELETE→recycle | `sharepoint_recycle_item` approval, no permanent delete; root check on empty id | adapted (recycle-only) |
| Auth chain | silent MSAL → az CLI → interactive (SP_NONINTERACTIVE) | `auto`/`delegated_msal`/`azure_cli`/`app_only`, explicit interactive setup | safely adapted |
| Retry / Retry-After | `_retry_wait` capped at **60 s** | uncapped `parse_retry_after_seconds`, deadline/cancel absent from sleep | **regressed** (SP-H2) |
| Owned sites | `cmd_collect_my_sites` opaque pages, skip failures | `sharepoint_list_owned_sites` bounded, per-group partial warnings | safely adapted |
| Audit (users/roles/groups/lists/subsites/metadata) | `_*_JS` over fixed Edge:9222, empty-on-failure marks collected | same-origin JS via core enrolled profile, complete/partial/truncated/unreachable | safely adapted (status fixed) |
| Browser lifecycle | `_ensure_edge`/`_shutdown_edge` fixed exe/port/profile, tree-kill | core-owned acquire/release, reused sessions preserved | safely adapted |
| Redaction | absolute paths/log/JS errors leak | relative artifact paths, no CDP/profile/cookie/script | safely adapted |
| Parser handoff | Docling/parsers extract content | excluded; workflow stops after acquisition | intentionally excluded (X-02) |

---

## 8. Lifecycle / profile / surface matrix

| State / surface | Behavior | Verdict |
|---|---|---|
| Fresh profile | SharePoint present, disabled, not imported, tools absent, router visible | correct |
| Explicitly disabled | tools/plugin-skills absent; qualified skill load fails | correct |
| Configured but disabled | static config panel via descriptor without import | correct |
| Graph-ready / browser-unenrolled | Graph tools + owned-sites reachable; only `sharepoint_audit_permissions` hidden | correct |
| Each complete auth mode | `auto`/`delegated`/`app_only`/`azure_cli` deterministic; secret write-only | correct |
| Partial/ambiguous mode | readiness `configuration_required`/`interactive_auth_required`; no interaction | correct |
| Enabled + ready | tools reachable; approval hook active | correct **but SP-H1 approval grain** |
| Throttled operation | 429/503 Retry-After | **SP-H2: hangs past timeout, uncancellable** |
| CLI/TUI, Desktop, gateway/API, Kanban, cron | one registered profile-scoped tool; Desktop backend projection | correct |
| Workflow admission | flat `requires:[ericsson-sharepoint]`, exact tools, stops at acquisition, distributed | correct |
| Existing conversation | tool/prompt prefix stable; changes affect fresh sessions | correct |

---

## 9. Source-to-vendor provenance and byte-parity

- **`vendoredFrom` = `fdb83a7859456776556d99274284c01acc05de10`** (exact).
- **13/13 plugin files** byte-identical (independent blob-OID compare):
  `__init__.py, audit.py, auth.py, client.py, config.schema.json, models.py,
  operations.py, plugin.yaml, tools.py, url_parser.py`, three plugin skills. No
  extra/missing files (source and Hermes file lists identical).
- **Sharepoint router skill** + **onboarding `sharepoint-tools.md`** + generated
  **`catalog.json`** byte-identical.
- **Workflow distribution:** loose `capabilities/workflows/sharepoint-document-
  intake.{yml,hermes.yaml}`, package
  `capabilities/workflow-packages/ericsson/workflows/sharepoint-document-intake.
  {yaml,hermes.yaml}`, and `digests.json` byte-identical source↔vendor; the
  package holds all five workflows; installed-distribution e2e asserts the bytes
  land. **The JIRA-H3 "workflows never staged" class does not recur.**
- **Policy sidecars** present (`overlap_policy: forbid`, `language_
  compatibility: archon-2026-07`).
- **Neighboring bytes:** no Jira/GitLab/Teams **production** file changed in the
  Hermes range; generic Graph code contains no `ericsson`/`sharepoint` id.
- **Ledgers:** `microsoft-graph-connectors.yaml` (new, connector-neutral,
  owned-symbol list) and `plugin-configuration.yaml` (additive fresh-manager
  boundary) present; `check_upstream_customizations.py` exit 0.

---

## 10. Tests and mutation-quality assessment

Deterministic gates (rerun from the candidate worktrees):

- **Source:** `pytest tests/test_sharepoint_*.py` → all pass **except**
  `test_sharepoint_workflows.py::test_document_intake_compiles_with_real_archon_
  authority`, which fails only from the detached worktree with a `StopIteration`
  (its `next(... path.name == "ericsson-capabilities")` discovery cannot find a
  parent dir named `ericsson-capabilities` under the private `.worktrees/…/source`
  path). Re-run from the real source root (`main` @ `fdb83a7`, which *is* named
  `ericsson-capabilities`) → **passes**. Environment-dependent test harness, not
  a candidate defect. `test_teams_plugin.py` green; `build_catalog.py --check`
  clean; `validate_catalog.py` `{"ok": true}`; `git diff --check` shows only
  benign new-blank-line-at-EOF (not a defect).
- **Hermes:** focused suite (15 files) → **250 passed, 0 failed**;
  `check_upstream_customizations.py` exit 0; `node --test vendor-ericsson.test.
  mjs` → **47 passed**; `git diff --check 911b7e7..dea2900d` benign EOF only.
- **Desktop:** `typecheck` clean; `lint` 0 errors; `test:ui plugin-toolset-
  config-panel` → **14 passed** (re-run from the real root; the detached worktree
  cannot resolve `react/jsx-dev-runtime` because it has no local `node_modules`,
  an environment artifact, not a candidate defect).

Mutation checks (private detached worktrees, reverted after each; no commit):

| # | Mutation | Result |
|---|---|---|
| M1 | Drop `_validate_graph_origin(next_url)` in `iterate_pages` | **survived** — Graph suite still 26/26; next-link origin guard is correct but untested |
| M2 | Forward `Authorization: Bearer` to the download redirect host | **caught** — large-transfer suite fails |
| M3 | Drop the final `.part` cleanup (`finally: pass`) | **caught** — large-transfer suite fails |
| M4 | `_has_write_admission` → always True | **caught** — `test_w08_all_writes_require_exact_backend_admission_and_reject_argument_claims` fails |
| M5 | Drop `offset % GRAPH_UPLOAD_FRAGMENT_BYTES` alignment on resume offset | **survived** — Graph suite still 26/26; offset alignment weakly covered |
| M7 | `_is_within` → always True (download/upload boundary) | **caught** — `test_external_root_requires_single_use_interactive_authorization`, `test_unattended_caller_cannot_expand_file_boundary` fail |
| M8 | Remove the audit site-origin check (`_safe_site`) | **caught** — `test_b14_limits_are_explicit_and_same_origin_is_mandatory` fails |

Load-bearing guards for redirect bearer-strip, `.part` cleanup, write admission,
local-root/symlink enforcement, and audit same-origin are genuinely tested. The
gaps: the next-link-origin guard (M1) and upload resume-offset alignment (M5)
survive mutation; and no test covers the SP-H1 approval grain or the SP-H2
deadline/Retry-After bound — which is how both findings survive a green suite.
Tests otherwise use real imports, temp profiles, real staging, fake streaming
transports, real browser-session doubles, the real workflow compiler, and
installed-distribution layouts — not fixture-only assertions.

---

## 11. Verification ledger (command / result / evidence type)

| Command (abbreviated) | Dir | Result | Type |
|---|---|---|---|
| `git status/worktree/rev-parse/merge-base/diff --stat/log` (both repos) | roots | ranges/ancestry/counts match prompt | inspection |
| `git cat-file`/`rev-parse …^{tree}` candidate SHAs | roots | commits/trees exist, match | inspection |
| legacy 3-file blob compare `fc3bf26` vs `8ca26f8` | loop_24 | byte-identical | inspection |
| `shasum -a 256` design/plan + `git show …:behavior-map \| shasum` | roots | all three match | inspection |
| `worktree add --detach` at both candidate SHAs under `mktemp -d` | private | HEADs verified | inspection |
| `pytest tests/test_sharepoint_*.py`, `test_teams_plugin.py`, catalog build/validate, `diff --check` | source WT | pass (1 env-dependent skip re-passed at root) | execution |
| `run_tests.sh` 15 Hermes files, `check_upstream_customizations.py`, `vendor-ericsson.test.mjs`, `diff --check` | hermes WT | 250 pass / exit 0 / 47 pass / benign | execution |
| Desktop `typecheck`/`lint`/`test:ui` | root | clean / 14 pass | execution |
| plugin vendor byte-parity script (13 plugin + skills + 5 workflow files) | roots | ALL PARITY PASSED | inspection |
| `repro_graph.py` (R1/R2/R2b async-copy origin + Retry-After) | hermes WT | R2/R2b confirmed | execution (synthetic, no network) |
| `repro_approval.py` (SP-H1 hook + host cache key + Jira contrast) | hermes WT | confirmed | execution (synthetic) |
| Mutations M1–M8 (reverted) | detached WTs | per §10 | mutation |
| generic-graph connector-id grep; Jira/GitLab/Teams byte grep | hermes WT | none | inspection |

---

## 12. What was verified safe (adversarial cases attempted)

- Two different recycle targets through the approval hook → identical cache key
  and target-blind message (SP-H1 confirmed); Jira hook in the same tree binds
  args (contrast confirmed).
- 429 with `Retry-After: 3600` through `_request`, and `Retry-After: 86400` with
  `deadline=5s` through `poll_async_operation` → full sleeps, deadline/cancel not
  observed (SP-H2 confirmed).
- Download redirect to a CDN host with an injected bearer → caught by test (M2);
  dropped `.part` cleanup → caught (M3).
- Boundary bypass (traversal/symlink/external root, unattended expansion) →
  rejected; `_is_within` neutralized → caught (M7).
- Audit site-origin removal → caught (M8); audit JS enforces same-origin and
  returns only normalized fields.
- Ambiguous write (transport failure) on upload/create/move/copy/recycle →
  `AmbiguousWrite`, never blind-retried; no unobserved object reported.
- Async copy monitor on the **graph** origin completes; on a **sharepoint**
  origin it raises a generic error (origin guard active — see §13 for the
  real-monitor-origin question).
- Disabled-by-default correction present (`enabled:false`); no permanent-delete
  tool; no connector-local LLM; no Jira/GitLab/Teams production byte changed.

---

## 13. Residual installed-UAT-only risks (not code findings)

- **Async-copy monitor origin (highest residual).** `poll_async_operation`
  constrains the monitor to the **graph** origin, and the tests assume a
  `graph.microsoft.com` monitor. Real Microsoft Graph SharePoint/OneDrive `copy`
  operations frequently return the monitor `Location` on the tenant
  `*.sharepoint.com` origin. If so, `sharepoint_copy_item` will fail after the
  copy is accepted server-side (the origin guard rejects the monitor). Cannot be
  confirmed offline; must be exercised against a live tenant in Task 12
  (UAT step 6/copy). If confirmed live, it becomes a HIGH.
- **`move`/`recycle` root-rejection predicate.** Both guard on an empty item id
  (`source["item"]["id"] in {"", None}`), but a resolved library-root DriveItem
  has a real id, so the guard does not fire for a root URL; Graph rejects
  deleting/moving a drive root remotely, so the defense-in-depth is weakened but
  not exploitable offline. Worth a real-tenant check and a cheap fix (reject when
  `item_path`/root-ness indicates a library root).
- **Windows delegated cache.** The portable (`os.name != "posix"`) cache path
  uses `is_symlink`/`is_file` rather than fd-level `O_NOFOLLOW` guards; Windows
  file semantics (junctions, share modes, atomic replace) need live validation.
- Real delegated MSAL / app-only / Azure CLI identities, real tenant URL
  variants, CDN redirects, large-file upload resume, browser enrollment/audit
  under Conditional Access, Desktop rendering, and restart/upgrade enablement
  persistence remain live-UAT items per the plan.

---

## 14. Confirmation of constraints honored

No standalone security/threat-model/security-review workflow, penetration test,
vulnerability scanner, or exploit exercise was run. No live Microsoft/Ericsson
service, real credential, or malicious payload was used — every reproduction used
`httpx.MockTransport`, synthetic sentinel values, isolated temporary state, and
no network. No production code, test, generated file, Git ref, branch, or
worktree under review was modified; the two detached review worktrees and their
private parent were created under `mktemp -d`, used read-only against immutable
Git objects for tracing and in disposable copies for mutation, and removed after
this report. No release, push, PR, workflow dispatch, brand mutation, or Task 12
action was attempted. The only persistent write is this report.

---

SHAREPOINT CANDIDATE MUST NOT ENTER TASK 12 UNTIL ALL CRITICAL AND HIGH FINDINGS ARE RESOLVED.
