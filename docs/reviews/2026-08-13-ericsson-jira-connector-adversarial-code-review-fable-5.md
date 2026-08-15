# Adversarial code review — Ericsson Jira connector

**Reviewing model:** Claude Fable 5 (`claude-fable-5`), reviewer short name `fable-5`.
**Platform:** macOS (Darwin 25.5.0), local checkouts, no network.
**Review date:** 2026-08-13.
**Prompt:** `docs/reviews/2026-08-13-ericsson-jira-connector-adversarial-code-review-prompt.md`.

This is a functional-correctness adversarial review of the immutable Jira
connector candidate before Jira Task 10. No production code, tests, generated
files, refs, branches, or worktrees were modified. No standalone
security/threat-model workflow, live service, real credential, release, push, or
brand mutation was attempted. The only persistent write is this report.

---

## 1. Repository states, immutable SHAs, hashes, ranges, and counts (verified)

| Repository | Branch / HEAD | Tree | Status |
|---|---|---|---|
| `ericsson-capabilities` (root) | `main` @ `f52a131cc63643f995e9d125bfa3fc7fa865700f` | `48f06bd4828a96c4e03ec5ff1e634508e4dfe23c` | matches prompt; 2 preserved user files elsewhere untouched |
| `ericsson-capabilities` Jira worktree | `feat/ericsson-jira-connector` @ `f52a131c` | same | clean |
| `hermes-agent` (root) | `base` @ `7d35a7ec27707483dda7991f60f9d26aeda43389` | `7fa983c149c2887593addc219b43ff99774dfaf3` | matches prompt; untracked `.otto/`, `docs/*` preserved |
| `hermes-agent` Jira worktree | `feat/ericsson-jira-connector` @ `7d35a7ec` | same | clean |
| `loop_24` (legacy) | `main` @ `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6` | — | matches pin |

- **Ancestry verified:** `634ca3b..f52a131` and `dae405e..f52a131` (source);
  `d48f783..7d35a7ec` and `da59906..7d35a7ec` (Hermes). All `--is-ancestor` true.
- **Source range `634ca3b..f52a131`:** 12 commits, 47 changed files,
  4,597 insertions / 299 deletions — matches prompt. Trailing two commits are
  the GitLab corrections (`1784d8c`, `f52a131`).
- **Hermes range `d48f783..7d35a7ec`:** 5 commits, 31 changed files,
  2,567 insertions / 248 deletions — matches prompt.
- **Legacy delta `8ca26f8..fc3bf26`:** touches only
  `custom_components/ericsson_docgen/**` (3 files); no Jira component changed —
  verified, so `fc3bf26` is valid Jira behavior evidence.
- **Immutable input hashes — all three match; `REVIEW_INPUT_CHANGED` NOT raised:**
  design `93e2f4d2…1cc6`, Jira plan `205eb814…9a63`, behavior map `ce518bf1…ae21`.
- **`vendoredFrom` = `f52a131cc63643f995e9d125bfa3fc7fa865700f`** (matches the
  source candidate).

Changed-path classes reviewed (source): Jira production
(`plugins/ericsson-jira/*.py`, `config.schema.json`, `plugin.yaml`),
cross-connector prerequisite (`plugins/ericsson-gitlab/operations.py`),
skills/workflows/docs, generated catalog, tests. Hermes: generic
vendoring/staging + capability manifests + cross-surface tests only (no core
symbol change).

---

## 2. Overall verdict

## **DO NOT ENTER TASK 10**

Three HIGH findings block the immutable candidate. Two are deterministic code
defects proven on this platform (a Windows-default auth footgun; a coarse
comment-approval grain); one is a proven installed-distribution gap in which the
candidate's flagship Jira workflows never reach a profile. None is CRITICAL:
credentials are not disclosed or redirected, no unapproved remote mutation can
execute from a model path, source→vendor byte identity is exact, and no shared
Hermes/GitLab/Teams path is corrupted.

The connector's core is otherwise strong: origin normalization, the private-curl
transport's argv/secret containment and cleanup, the v3→v2 and Cloudflare-1010
classifiers, the host-minted args-bound admission, ADF work/output bounding, the
one-time lifecycle migration, and exact source→vendor byte parity all hold under
adversarial probing and mutation.

---

## 3. Findings (CRITICAL before HIGH)

| ID | Sev | Title | File / symbol | Task | Invariant |
|---|---|---|---|---|---|
| JIRA-H1 | HIGH | Windows default `curl_executable` disables **all** Jira tools out of the box | `plugins/ericsson-jira/auth.py:79,92-98` | 3 | 4, 8 |
| JIRA-H2 | HIGH | Comment-write approval uses a tool-name-only rule key + credential-blind prompt → session/always widens to arbitrary comments | `plugins/ericsson-jira/__init__.py:117-126` | 6 | 11 |
| JIRA-H3 | HIGH | Showcase + jira-to-gitlab workflows never staged to a profile; staged `my-tickets-summary` is the stale env-based copy | `hermes_cli/capability_staging.py:937-1027`, `capabilities/workflow-packages/ericsson/**` | 9 | 18, 14 |

No CRITICAL findings.

### JIRA-H1 (HIGH) — Windows-default `curl_executable` fails an unconditional absolute-path check, disabling every Jira tool on the Windows release target

1. **ID / severity:** JIRA-H1 / HIGH.
2. **Title:** The default `curl_executable` (`/usr/bin/curl`) is validated on
   every configuration regardless of transport mode, and is not an absolute path
   under Windows semantics, so `authentication_from_configuration` raises for a
   fresh Windows profile and all four Jira tools become unavailable.
3. **SHA / task:** source `f52a131c` (Hermes `7d35a7ec`), Task 3.
4. **Production site:** `plugins/ericsson-jira/auth.py:79`
   (`curl_executable = _setting(configuration, "curl_executable", "/usr/bin/curl")`)
   and `auth.py:92-98` (`... or not Path(curl_executable).is_absolute(): raise
   JiraError("invalid_configuration")`). This block runs before the auth-mode
   branch and independent of `transport` (`auth.py:78,90` set `transport`
   default `"auto"`, but the curl-path validation is unconditional).
5. **Invariant / contract:** Invariant 4 ("supported auth/deployment mode is
   materially unusable" ⇒ HIGH), invariant 8, and the design's Windows-UAT
   premise (plan Task 10 step 3.3–3.4; UAT matrix rows 3–5). The curl executable
   is only meant to gate the **curl** transport (schema `visible_when:
   {transport: curl}`, advanced) — it must not disable native/auto.
6. **Trigger / state:** A fresh or upgraded Windows profile with `transport`
   left at the default `auto` (native primary) and `curl_executable` unset (so
   the default `/usr/bin/curl` applies). No curl transport is even requested.
7. **Wrong result / consequence:** On Windows `pathlib.Path` is `WindowsPath`;
   `WindowsPath("/usr/bin/curl").is_absolute()` is **False** (root without
   drive). `authentication_from_configuration` raises `invalid_configuration`;
   `tools.check_available` returns False (`tools.py:90-97`), so the plugin's
   `available()` check-fn returns False and none of `jira_my_tickets`,
   `jira_search_issues`, `jira_get_issue`, `jira_add_comment` are reachable — the
   connector is unusable on the v5.6.0 Windows target until the user manually
   sets a Windows absolute path into an **advanced, `transport:curl`-gated
   field** they never see in native mode.
8. **Direct evidence:** `PureWindowsPath('/usr/bin/curl').is_absolute()` → `False`;
   `PureWindowsPath(r'C:\Windows\System32\curl.exe').is_absolute()` → `True`
   (run under the source venv). Fresh-session surface test
   `tests/hermes_cli/test_ericsson_connector_surfaces.py:188-268` configures Jira
   with bearer + base_url and **no** `curl_executable`, then asserts all four
   tools become reachable — which only holds because CI runs POSIX
   (`/usr/bin/curl` is absolute there). The identical enabled+configured path
   yields an empty reachable-tool set on Windows.
9. **Why not already prevented:** No test exercises Windows path semantics; the
   entire suite runs POSIX, so the guard is validated only where the default
   passes (mutation §10 M7 below shows the relative-path rejection *is* tested,
   but only under POSIX, where `/usr/bin/curl` is absolute). Task 10 UAT would
   surface it at step 3–5, but this is a deterministic code defect established
   here without a live service, so it must be fixed before Task 10, not
   discovered during it.
10. **Smallest safe fix:** Validate `curl_executable` only when the effective
    transport can use curl (`transport in {"curl","auto"}` — or only `"curl"`),
    and/or make the default platform-aware (`shutil.which("curl")` /
    `C:\Windows\System32\curl.exe` on Windows). Do not validate a curl path when
    native transport will never spawn curl.
11. **Missing regression test:** A parametrized test that monkeypatches
    `auth.Path`/`pathlib` to `PureWindowsPath` (or runs the validation with a
    simulated Windows path resolver) and asserts that a default `/usr/bin/curl`
    with `transport in {auto, native}` yields a usable `JiraAuth` and
    `check_available is True`.

### JIRA-H2 (HIGH) — Comment-write approval is keyed on the tool name alone and shows no issue key/body, so one session/always approval authorizes arbitrary later comments

1. **ID / severity:** JIRA-H2 / HIGH.
2. **Title:** The plugin's `pre_tool_call` approval hook returns
   `rule_key = tool_name` and a constant, credential/target-blind message, so the
   host approval gate caches a single `[s]ession`/`[a]lways` decision under
   `plugin_rule:jira_add_comment` and short-circuits every subsequent
   comment-write to any issue key and body with no further human review.
3. **SHA / task:** source `f52a131c` (Hermes `7d35a7ec`), Task 6.
4. **Production site:** `plugins/ericsson-jira/__init__.py:117-126`
   (`require_write_approval` returns `{"action":"approve","message":"Approve
   Ericsson Jira comment","rule_key": tool_name}`). Consumed by
   `hermes_cli/plugins.py:3005-3009` → `tools/approval.py:request_tool_approval`
   (`3318-3402`): with a non-empty `rule_key`, `key_suffix = rule_key` and
   `pattern_key = "plugin_rule:jira_add_comment"` (`approval.py:3368-3376`); a
   cached approval short-circuits via `is_approved(session_key, pattern_key)`
   (`approval.py:3037`).
5. **Invariant / contract:** Invariant 11 ("interactive approval … covers the
   exact current tool invocation, issue key, and body"); specific decision 16
   (approval "cannot be reused, caller-authored, **widened from tool name
   alone**, or bypassed"); design decision 10 (interactive writes require
   explicit approval). Hermes' own docstring warns that `rule_key = tool_name`
   is "too coarse — one always would blanket every rule on that tool"
   (`approval.py:3366-3367`); the plugin sets exactly that.
6. **Trigger / state:** In interactive chat/CLI, the user answers `[s]ession` or
   `[a]lways` to a first `jira_add_comment` approval (a natural choice while
   triaging several tickets). Thereafter the model may call `jira_add_comment`
   for any issue key/body.
7. **Wrong result / consequence:** Every subsequent comment write is auto-approved
   with no human seeing the target/body; each still mints a fresh args-bound
   `PluginToolAdmission`, so the audit trail *looks* legitimate while the human
   never reviewed the specific write. Even the first approval is credential/target
   blind — the gate renders only `description="Approve Ericsson Jira comment"` and
   `display_target="<jira_add_comment> (plugin approval rule)"`; the issue key and
   comment body are never shown. This is exactly the "widened from tool name
   alone" case decision 16 forbids.
8. **Direct evidence:** Traced end-to-end through the real Hermes approval code
   in the candidate worktree (`plugins.py:2938-3062`, `approval.py:3318-3402`,
   `3036-3040`). The plugin message/`rule_key` constants are literal at
   `__init__.py:120-124`. The workflow showcase path is *not* affected: its
   `approve-comment` node renders `ticket_key` + `proposed_comment`
   (`workflows/jira-single-ticket-showcase.yml:53-57`), so the exposure is the
   direct chat/CLI/skill write path.
9. **Why not already prevented:** The args-bound, single-use admission
   (`arguments_sha256` + `claim_once`) makes each *mechanical* dispatch exact, but
   the *human authority* that unlocks it is cached on the tool name; the binding
   gives false assurance. Tests assert a single call is gated and that
   caller-injected `tool_admission` is rejected, but none assert that a second,
   different-body write in the same session re-prompts a human, nor that the
   prompt text contains the key/body.
10. **Smallest safe fix:** In `require_write_approval`, omit `rule_key` (so the
    host derives `tool_name + sha256(reason)` grain) **and** put the exact issue
    key + comment body into `message`, making both the grain and the human prompt
    per-write — matching what the showcase's `approval` node already does.
11. **Missing regression test:** A test that approves `[s]ession` for
    `jira_add_comment` on `ABC-1`/body-A, then asserts a second call with
    `ABC-2`/body-B re-invokes the approval gate (not auto-approved), and asserts
    the rendered approval message contains the issue key and body.

### JIRA-H3 (HIGH) — The Jira showcase and jira-to-gitlab workflows are never staged into a profile, and the one workflow that is staged is the stale env-based copy

1. **ID / severity:** JIRA-H3 / HIGH.
2. **Title:** Capability staging copies only `manifest["workflowPackages"]`, not
   the loose `manifest["workflows"]` list; the vendored workflow package contains
   only `inbox-digest` + an old `my-tickets-summary`, so
   `jira-single-ticket-showcase` and `jira-to-gitlab` never reach
   `$HERMES_HOME/workflows/`, and the `my-tickets-summary` that is staged is the
   stale copy requiring `outlook` + `JIRA_BASE_URL`/`JIRA_PAT` env.
3. **SHA / task:** Hermes `7d35a7ec` (source `f52a131c`), Task 9.
4. **Production site:** `hermes_cli/capability_staging.py:958-1027`
   (`seed_baked_capabilities`, iterates only `manifest.get("workflowPackages")`;
   its docstring at `:942` falsely claims it "copies workflows to
   $HERMES_HOME/workflows/") and `stage_bundle:802-808` (installed/wheel path,
   same — only `workflowPackages`). The vendored package
   `capabilities/workflow-packages/ericsson/workflows/` holds only
   `inbox-digest.yaml`, `my-tickets-summary.yaml`, `my-tickets-summary.hermes.yaml`.
   Workflow discovery scans only `<workdir>/.hermes/workflows` and
   `<hermes_home>/workflows` (`plugins/workflow/discovery.py:100-102`,
   `catalog_api.py:581-593`) — never the bundled `capabilities/workflows/`.
5. **Invariant / contract:** Invariant 18 ("installed … workflow packages …
   operate from installed bytes"), invariant 14 (workflow contracts executable),
   design decision 26 and plan Task 7 / Task 9 Step 1 / UAT step 8 (the
   single-ticket showcase must admit "through real workflow admission … when
   ready, blocks before run creation when disabled/unready").
6. **Trigger / state:** Any fresh or baked profile (`seed_baked_capabilities`) or
   installed wheel (`stage_bundle`).
7. **Wrong result / consequence:** `$HERMES_HOME/workflows/` receives only the
   `ericsson` package (`inbox-digest`, `my-tickets-summary`). The flagship
   `jira-single-ticket-showcase` and the cross-connector `jira-to-gitlab`
   workflows are absent and undiscoverable, so installed UAT step 8 cannot be
   performed. Separately, the staged `my-tickets-summary.hermes.yaml` is
   byte-different from the candidate's updated loose copy: the staged one declares
   `required_services: [ericsson-jira, outlook]`, `required_secrets:
   [JIRA_BASE_URL, JIRA_PAT]`, `outward_action_nodes: [send]`,
   `overlap_policy: queue` — env keys the connector no longer uses (removed from
   the Keys page) and an `outlook` service that is not a configurable connector in
   this candidate, so the staged digest is non-functional as a Jira summary.
8. **Direct evidence:** Fresh-profile experiment (temp `HERMES_HOME`,
   `seed_baked_capabilities`) produced only
   `ericsson/workflows/{inbox-digest.yaml, my-tickets-summary.yaml,
   my-tickets-summary.hermes.yaml}` — no showcase, no jira-to-gitlab. The showcase
   admission test loads the file from `repo_root/capabilities/workflows/…`
   (`tests/plugins/workflow/test_ericsson_connector_toolsets.py:45-47`), i.e. from
   source, not from a staged profile — so it proves compilation, not
   distribution. `git diff` of loose vs package `my-tickets-summary.hermes.yaml`
   at `7d35a7ec` shows the staged package copy still carries the
   `required_services/required_secrets` env model.
9. **Why not already prevented:** Surfaces/toolsets tests hand-copy the repo
   workflow files into fixture directories; no test asserts distribution reaches a
   profile or doctors the staged package on a fresh profile. The loose-workflow
   staging loop was removed 2026-07-17 (pre-Jira), so the gap is latent
   infrastructure the Jira candidate silently relies on — its own deliverables do
   not land.
10. **Smallest safe fix:** Add `jira-single-ticket-showcase` and `jira-to-gitlab`
    (plus the updated `my-tickets-summary`) to the digest-verified workflow
    package `capabilities/workflow-packages/ericsson/` (regenerate `digests.json`)
    so `stage_bundle`/`seed_baked_capabilities` deliver them; or restore loose
    `manifest["workflows"]` staging in both staging paths. Correct the false
    docstring at `capability_staging.py:942`.
11. **Missing regression test:** A distribution test that runs
    `seed_baked_capabilities`/`stage_bundle` into a temp `HERMES_HOME` and asserts
    `discover_workflows(...)` surfaces `jira-single-ticket-showcase`,
    `jira-to-gitlab`, and a `my-tickets-summary` whose `requires` is flat
    `[ericsson-jira]` with no `JIRA_BASE_URL`/`JIRA_PAT`/`outlook` requirement.

---

## 4. Task 1–9 traceability matrix

| Task | Concern | Verdict | Note |
|---|---|---|---|
| 1 | Frozen legacy behavior map + exact source identity | **proven** | Legacy pin `fc3bf26` verified docs-only delta; map hash matches; source identity exact. Map has minor fidelity gaps (see §7). |
| 2 | Standalone descriptor, configuration, lifecycle metadata | **proven** | `kind: standalone`, `enabled:false`, `lifecycleMigration` present and schema-validated. |
| 3 | Typed auth, normalized endpoint, native client, retry, REST compat | **contradicted** | Sound design, but JIRA-H1 (Windows curl default) breaks the supported native mode on the release target. |
| 4 | Private bounded curl transport | **proven** | argv/secret containment, cleanup, bounds-during-collection for body/headers all hold (residual: stdout/env, §13). |
| 5 | Reads, search, pagination, ADF/plain normalization, GitLab links | **proven (with bounded gaps)** | Normalization/bounding solid; truncation-signal + pagination-completeness gaps are MEDIUM (§5, §7). |
| 6 | Comment validation, approval, versioned bodies, reconciliation, no ambiguous replay | **contradicted** | No-blind-retry + versioned bodies + args-bound admission correct; JIRA-H2 (approval grain) violates exact-write authority. |
| 7 | Skills, router, workflows, onboarding, Phase 6 deferral | **proven (source)** | Skills/router/deferral honest and clean; the workflows themselves are correct — their **distribution** fails (JIRA-H3). |
| 8 | Source closure, neighboring regressions, exact clean source SHA | **proven** | `git diff --check` clean; GitLab corrections necessary; exact SHA `f52a131c`. |
| 9 | Vendoring, lifecycle migration, cross-surface, Desktop, packaging | **contradicted** | Byte parity + migration + Desktop projection exact; JIRA-H3 (workflow distribution) is the Task-9 defect. |

---

## 5. Verdict on the eighteen non-negotiable invariants

1. **Source ownership / byte identity** — **holds.** 12/12 Jira plugin blobs and
   111/111 managed files byte-identical source↔vendor; `vendoredFrom` exact;
   sidecars present; no stale files.
2. **Disabled means absent** — **holds.** Disabled standalone is never imported;
   tools/plugin-skills absent; router discoverable; enable affects fresh sessions
   only (verified by surfaces test + independent trace).
3. **Lifecycle migration exact / one-time** — **holds.** Generic
   manifest-driven; only the migrating id stripped from `enabled`; workflow/Teams
   untouched; marker atomic; settings/secrets retained; later explicit enable
   survives restage (test + staging code).
4. **One profile-scoped config authority** — **partially violated (JIRA-H1).**
   Config/secrets model is correct and profile-scoped; but the curl-executable
   validation makes the native mode unusable on Windows. Also a readiness/`Ready`
   footgun when an inactive-mode secret persists (MEDIUM, §13).
5. **Origin / authority never drift** — **holds.** `_origin` rejects
   userinfo/path/query/fragment/bad-port; resources are internal constants + strict
   `_ISSUE_KEY`; redirects not followed (native `follow_redirects=False`, curl
   `proto-redir -all`, `trust_env=False`/`--proxy ""`); no user input reaches the
   path segment.
6. **Auth / private data remain private** — **holds for active sinks.**
   `JiraAuth.__repr__` redacts; `JiraError` carries only category strings; httpx
   exceptions re-raised `from None`; results are `_redact`-scrubbed bounded dicts;
   curl auth/body in 0600 files, not argv. Latent: `dataclasses.asdict(JiraAuth)`
   would expose the token, but is never called (§13).
7. **REST / transport fallback narrow** — **holds.** v3→v2 only on the exact
   bounded 404 classifier; native→curl only on the exact Cloudflare-1010 403;
   TLS/DNS/timeout/auth/permission/generic never divert (mutation-confirmed §10).
8. **Bounds before uncontrolled work** — **partially violated.** curl body/headers
   bounded during collection; ADF bounded by node/char/depth. But the **native**
   response is fully buffered (`response.content`) before the 1 MiB check
   (`client.py:205`), and curl stdout/stderr use `communicate()` (bounded only
   post-hoc). Realistic consequence is MEDIUM (trusted authenticated origin under
   TLS), so not a standalone HIGH — recorded in §13.
9. **Cancellation / deadlines reach real work** — **holds within bound.** curl
   polls cancel every ≤50 ms and terminates/reaps/cleans on every boundary. Native
   is not cancel-responsive mid-request but is bounded by the per-request timeout
   (≤120 s) and the shared absolute deadline; retry sleep (≤5 s) is not
   cancel-polled. Bounded ⇒ compliant; responsiveness gap is MEDIUM (§13).
10. **Reads have stable cross-version meaning** — **partially violated (MEDIUM).**
    ADF/plain normalization is faithful and non-inventing; but `jira_my_tickets`
    discards `truncated`/`warnings`, and the pagination loop can break on a
    defaulted/omitted `total` or an empty intermediate page with `truncated=False`
    — silently presenting a partial set as complete (bounded by priority ordering;
    §7, §13).
11. **Comment authority binds the exact write** — **violated (JIRA-H2).**
    Args-bound admission is exact, but interactive approval is widened from the
    tool name alone and the prompt is target/body-blind.
12. **Write reconciliation truthful** — **holds.** No blind ambiguous retry
    (mutation/trace confirmed); `duplicate`/`created`/`conflict`/`permission`
    reported per evidence; a lost-response create is reconciled to
    `duplicate+reconciled`, never false `created`. Minor: reconciliation is
    body-only (author-blind) and single-page (LOW, §13).
13. **Skills do reasoning; tools do integration** — **holds.** No curl,
    credentials, second client, or hidden LLM in any skill; no connector-local
    model call anywhere in the plugin.
14. **Workflow contracts executable and honest** — **partially violated
    (JIRA-H3).** As authored the workflows compile and admit correctly (flat
    `requires`, `allowed_tools:[]` enforced, rejection blocks the write); but the
    showcase/jira-to-gitlab are not distributed, so the installed contract is not
    executable.
15. **Every surface uses one registered plugin** — **holds.** CLI/Desktop/
    gateway/Kanban/cron/workflow resolve the profile-scoped registered tool;
    Desktop is a backend projection with no Jira-specific parsing.
16. **Compatibility / exclusions truthful** — **holds.** Public tool names +
    useful fields retained; no create/transition/assign/edit/attachment tool;
    no false Phase-6 parity claim; SharePoint absent.
17. **Generic Hermes changes remain generic** — **holds.** Vendor/lifecycle
    changes hardcode no Jira id; GitLab/Teams/workflow handling unchanged;
    sidecars preserved; no core model-tool widening; no existing-conversation
    prompt mutation.
18. **Installed / future-merge paths preserved** — **partially violated
    (JIRA-H3).** Inventories/parity/ledger/merge-rehearsal are clean and
    SharePoint-free; but installed workflow bytes for the Jira deliverables are
    absent, and one staged workflow is stale.

---

## 6. Verdict on the twenty-two specific implementation decisions

1. Schema `required`/`visible_when`/readiness/opaque config → **supported.**
   Bearer works with inactive basic fields; basic works with inactive PAT state;
   truly-ambiguous active secrets rejected (`auth.py:102-115`). Caveat: a residual
   *active* secret from the other mode wedges readiness (MEDIUM, §13).
2. `_origin()` single origin across all boundary cases → **supported** (one LOW:
   `"https://"` normalizes to host `https` instead of clean reject).
3. `JiraAuth` containment across helpers/repr/exceptions/results → **supported**
   for all reachable sinks; `asdict`/`astuple` latent-only (§13).
4. Native retries share one absolute deadline, honor/cap Retry-After, no
   fallback multiplication → **supported.** Worst case
   `4 × (1 + max_retries)` HTTP ops (12 default, 20 max), time-bounded by the
   shared deadline. Cancellation gap in retry sleep is MEDIUM.
5. Exact v3-unsupported classifier → **not established.** The classifier
   (`is_rest_version_unsupported`) is tight and cannot be triggered by ordinary
   404/auth/permission/malformed (mutation-confirmed), **but** it keys on two
   invented sentinel strings ("REST API v3 endpoint is not
   available/unsupported") whose match against a real Jira Server/DC v3-missing
   404 is unevidenced. If they do not match, `rest_api_version:auto` fails all
   reads/writes against a v3-less Server — a residual UAT risk (§13, top item).
6. Cloudflare-1010 one meaning across client/transport → **supported.** Two
   byte-identical copies; production uses the transport copy; the client copy is
   test-only (collapse recommended, LOW).
7. Explicit curl and auto-fallback share auth/body/result/deadline/status →
   **supported** (parallel implementations, same driver); minor drifts: native
   retries connection errors while curl surfaces `deadline`; env not scrubbed for
   the curl child (§13).
8. Curl executable validation true at launch, Windows paths, no unintended exe,
   no config-path leak → **partially supported.** Windows spelling/`shell=False`
   argv[0] resolution and diagnostics-non-leak hold; TOCTOU between construction
   and launch is real (MEDIUM, gated by root-owned approved paths, §13). Note the
   separate JIRA-H1 default-value defect.
9. Curl stdout/stderr/headers/body bounded **during** collection → **partially
   contradicted.** Header/body via files with size-stat before read (bounded);
   stdout/stderr via `communicate()` bounded only after buffering (MEDIUM, gated
   by the pinned trusted curl, §13).
10. Timeout/cancellation terminate/reap/close/remove on every boundary →
    **supported** (spawn failure, timeout, cancel, parser failure all cleaned;
    mutation-confirmed §10).
11. Curl header/status parsing robust (interim/redirect/malformed) →
    **supported.** Last-block selection cross-checked against `write-out
    %{http_code}`; no `-L`; malformed/oversized/spoofed blocks → `invalid_remote_data`.
12. Search pagination / total / filters / truncation cannot silently omit →
    **contradicted (MEDIUM).** Filters run in-loop (correct), endless/lying total
    bounded, failures propagate; but omitted/defaulted `total` and empty
    intermediate pages break with `truncated=False`, and `my_tickets` discards the
    signal entirely (§7, §13).
13. ADF bounded by aggregate work/output + depth, no leakage/quadratic/invention
    → **supported** with one robustness bug: `marks: null` raises `TypeError`
    (classified to `invalid_input` at the tool boundary, so not a crash;
    MEDIUM/uncertain trigger, §13).
14. GitLab URL recognition accepts intended + cleanup, no arbitrary trust →
    **supported.** Lookalikes are captured into `gitlab_urls` (evidence) but the
    downstream GitLab resolver rejects any non-configured origin, so trust holds;
    comment-sourced URLs are not extracted (parity gap, §7).
15. Comment reconciliation searches enough history to justify `duplicate` →
    **partially supported.** Newest-100 single page; an older duplicate beyond
    page 1 is missed and author is not checked (LOW; the no-retry guarantee
    holds).
16. Approval/admission bound to exact key/body, not reused/caller-authored/
    widened/bypassed → **contradicted (JIRA-H2).** Args-bound admission is exact;
    interactive approval is widened from the tool name alone and is target-blind.
17. Single-ticket workflow enforces one-key + rejection via compiler/runtime →
    **supported** (as authored): flat `requires`, `all_success` gating, rejection
    cancels the run, `allowed_tools:[]` enforced. (Distribution is the separate
    JIRA-H3 issue.)
18. Fresh-session enable/disable/skill/router/profile/workers/cron/workflow one
    authority → **supported.** All traverse the profile-scoped config/toolset
    authority; no process-global Jira env read remains.
19. One-time `auto_seeded_backend` transition distinguishes historical vs
    explicit, survives restage, touches no other plugin/profile → **supported**
    (test + code). LOW: a pre-upgrade *explicit* enable is indistinguishable and
    cleared once (accepted per design; settings/secrets preserved).
20. Vendoring preserves managed bytes/sidecars, removes only stale, exact
    provenance, SharePoint absent → **supported.** 111/111 parity; 0 SharePoint in
    tree/delta; provenance exact.
21. Two GitLab corrections required, behavior-preserving, byte-present where
    required → **supported.** `groups/` canonicalization and `order_by:path`
    descendant fix are real-server correctness repairs; `ericsson-gitlab`
    operations blob identical in both repos; needed by the Jira→GitLab path.
22. Load-bearing tests fail if the guard is removed → **mostly supported.**
    Seven mutations of high-risk guards were caught (§10); the Windows
    curl-default contract is **untested** (POSIX-only), which is how JIRA-H1
    survived.

---

## 7. Legacy / current / final behavior parity matrix

| Behavior | Legacy (`fc3bf26`) | Final candidate | Disposition |
|---|---|---|---|
| List assigned tickets | `JiraAssignedTicketsFetcher._search_issues`, JQL `assignee=currentUser() ORDER BY updated DESC`, `max_issues=0`=all | `jira_my_tickets` → `search_issues`, JQL adds `resolution=Unresolved ORDER BY priority DESC, updated DESC`, default 25 | safely adapted (per map); **truncation signal dropped** (MEDIUM) |
| Read one ticket | baseline `get_issue` fields + last-5 comments | `jira_get_issue` superset, normalized ADF, redacted comments | preserved + additive |
| Add comment | `JiraTicketUpdater`/`_post_triage_comment`, always POST v2 plain, creds+body in argv | `jira_add_comment` v3 ADF / v2 plain, approval, dry-run, reconcile, private-file transport | safely adapted; **approval grain** (JIRA-H2) |
| General search | none (fixed to current user) | new bounded `jira_search_issues` with explicit JQL/fields | new bounded surface |
| Auth bearer/basic | duplicated bearer/basic; updater unvalidated | single typed `auth.py`, mutual-exclusion validation | preserved/adapted |
| Origin normalization | `_normalize_base_url` trims + prepends https | `_origin` strict single origin, rejects userinfo/path/query/fragment | safely adapted (stricter) |
| curl for Cloudflare-1010 | mandatory curl, argv secrets, follows redirects | native primary; curl only on exact 1010; secrets in 0600 files; no redirects | preserved reason, safer mechanics |
| v3→v2 fallback | fall back on **any** v3 exception | fall back only on exact bounded 404 sentinel strings | narrowed; **strings unevidenced vs real Server** (§13) |
| 3xx handling | classified as SSO/expired-PAT auth failure | `invalid_remote_data` (redirects not followed) | diagnostic downgrade (LOW) |
| GitLab URL discovery | whole raw issue JSON incl. comments/custom fields | description/environment/summary only | narrowed; **comment-sourced links dropped** (MEDIUM parity gap) |
| ADF flatten | recursive text + dedupe | bounded ADF→markdown (links/mentions/tables/lists/code) | safely adapted |
| Comment result | `{ok,id}` (+ legacy loop feedback) | `{ok,id,created,duplicate,reconciled,dry_run}` | `{ok,id}` preserved + additive |
| Triage LLM / thresholds / model | embedded Ollama prompt + thresholds | excluded from plugin; skill guidance only | intentionally excluded |
| Selector first-item fallback | falls back to first ticket | strict `_ISSUE_KEY`, explicit failure | safely adapted |
| Multi-ticket loop / aggregation | `FixSummaryComposer`, loop | deferred to Phase 6, honestly disclaimed | deferred (as documented) |

`jira_my_tickets`/`jira_get_issue`/`jira_add_comment` retain their useful
compatibility contracts; `jira_search_issues` is the new bounded surface. The
behavior map is largely faithful but misstates comment-sourced GitLab-URL
discovery (MAP:76/78) and truncation warnings for `my_tickets` (MAP:62/77), and
does not disclose the 3xx-diagnosis downgrade — documentation-fidelity gaps, not
findings.

---

## 8. Lifecycle / profile / surface matrix

| State / surface | Behavior | Verdict |
|---|---|---|
| Fresh profile | Jira present, disabled, not imported, tools absent, router visible | correct |
| Explicitly disabled | tools/plugin-skills absent; qualified skill load fails | correct |
| Configured but disabled | config panel can show/enable via static inventory without import | correct |
| Enabled + incomplete | readiness diagnostics, tools gated by `check_available` | correct (POSIX); **broken on Windows via JIRA-H1** |
| Enabled + ready (POSIX) | all 4 tools reachable; secret not echoed | correct |
| Upgraded auto-seeded | Jira de-seeded once, workflow/Teams retained, settings/secrets kept, marker atomic | correct (test-verified) |
| Explicitly re-enabled | survives restart + restage | correct |
| Separate profile | profile-scoped; no cross-profile leakage | correct |
| CLI/TUI, Desktop, gateway/API, Kanban, cron | one registered profile-scoped tool; Desktop is backend projection | correct |
| Workflow admission | `requires:[ericsson-jira]` gates on ready-service snapshot; blocks before run creation | correct **as authored**; **showcase not installed (JIRA-H3)** |
| Existing conversation | tool/prompt prefix stable; changes affect fresh sessions | correct |

---

## 9. Source-to-vendor provenance and byte-parity

- **`vendoredFrom` = `f52a131cc63643f995e9d125bfa3fc7fa865700f`** (exact).
- **12/12 Jira plugin files** byte-identical (independent blob-OID compare):
  `__init__.py, auth.py, client.py, models.py, operations.py, tools.py,
  transport.py, jira_tools.py, config.schema.json, plugin.yaml`, both plugin
  skills. Corroborated by the vendoring sub-review's 111/111 managed-file parity.
- **Policy sidecars** (`*.hermes.yaml` for my-tickets-summary,
  jira-single-ticket-showcase, jira-to-gitlab) present in the Hermes candidate.
- **Stale-file handling:** +5 inventory additions, 0 removals in range — correct.
- **GitLab prerequisite corrections:** `ericsson-gitlab/operations.py` blob
  identical source↔vendor; both corrections present and necessary for the
  Jira→GitLab path.
- **SharePoint:** 0 occurrences in the Jira delta and 0 in Jira-relevant tree
  paths (tree-wide hits are pre-existing docs + a synthetic fixture plugin id).
- **Merge `9b61c5cf1`:** empty diff vs the candidate parent within
  `plugins/ericsson-jira/**`, the vendor script, and capabilities — no surprise
  resolution weakened an earlier invariant.

Caveat (JIRA-H3): byte-parity is exact, but the vendored **package** is missing
the new workflows and carries a stale `my-tickets-summary`, so "faithful
vendoring" here faithfully reproduces an incomplete/stale distribution surface.

---

## 10. Tests and mutation-quality assessment

Deterministic gates (rerun from the candidate worktrees):

- Source: `pytest tests/test_jira_*.py` → **131 passed**; `build_catalog.py
  --check` clean; `validate_catalog.py` `{"ok": true}`; `git diff --check`
  `634ca3b..f52a131` clean.
- Hermes: focused suite (10 files) → **239 passed**;
  `check_upstream_customizations.py` → exit 0; `node --test
  vendor-ericsson.test.mjs` → **47 passed**; `git diff --check
  d48f783..7d35a7ec` clean.
- Desktop: `typecheck` clean; `lint` 0 errors (167 warnings, pre-existing);
  `test:ui plugin-toolset-config-panel` → **14 passed**.

Mutation checks (private detached worktree at `f52a131c`, reverted after each; no
commit):

| # | Mutation | Result |
|---|---|---|
| M1 | `_has_write_admission` → always True | **caught** — `test_registered_comment_requires_exact_host_admission_before_configuration` fails |
| M2 | `transport._cloudflare_1010` → always True | **caught** — `test_auto_never_falls_back_for_unclassified_failures` (4 cases) fails |
| M3 | `is_rest_version_unsupported` → always True | **caught** — 5 client tests fail (write-ambiguity, malformed, exact-classifier) |
| M4 | `operations._redact` → identity | **caught** — `test_get_issue_normalizes_adf_context_and_safe_comment_projection` fails |
| M5 | Authorization into curl argv | **caught** — `test_private_config_keeps_secret_and_body_out_of_argv_and_cleans_up` fails |
| M6 | Disable temp-dir cleanup | **caught** — 4 curl-transport cleanup/timeout tests fail |
| M7 | Remove `curl_executable` `is_absolute` check | **caught** — `test_missing_ambiguous…[settings12]` fails, but **only under POSIX**; no test exercises the Windows-default failure (this is how JIRA-H1 survives) |

Load-bearing guards for REST/Cloudflare fallback, argv/secret secrecy, output
cleanup, redaction, and write admission are genuinely tested (fail on mutation).
The gap is platform coverage: the curl-executable contract is validated only
under POSIX path semantics, and no distribution test asserts workflows reach a
profile (JIRA-H3). Tests use real imports, temp profiles, real staging, real
subprocesses (fake curl), and the real workflow compiler — not fixture-only
assertions — except the two structural gaps above.

---

## 11. Verification ledger (every command / result / evidence type)

| Command (abbreviated) | Dir | Result | Type |
|---|---|---|---|
| `git status/worktree/cat-file/diff --stat/log` ×both repos | roots | states/counts match prompt | execution |
| `git merge-base --is-ancestor` ×4 | roots | all ancestry true | execution |
| `shasum -a 256` on design/plan/map | root | 3/3 match | execution |
| legacy `git diff 8ca26f8..fc3bf26` | loop_24 | docgen-only | execution |
| source `pytest tests/test_jira_*.py` + catalog build/validate + `diff --check` | src wt | 131 passed, clean | execution |
| Hermes focused suite + `check_upstream_customizations` + node vendor test + `diff --check` | hermes wt | 239 passed / exit 0 / 47 passed / clean | execution |
| Desktop typecheck/lint/test:ui | hermes root | clean / 0 err / 14 passed | execution |
| Read all `plugins/ericsson-jira/*.py`, schema, plugin.yaml, skills, workflows | — | full trace | inspection |
| `PureWindowsPath('/usr/bin/curl').is_absolute()` | src venv | False (True for `C:\…\curl.exe`) | execution |
| grep Jira tests for windows/is_absolute/curl_executable | src wt | no Windows coverage | inspection |
| Read Hermes `plugins.py:2938-3062`, `approval.py:3318-3402,3036-3040` | hermes wt | approval widening confirmed | inspection |
| byte-parity 12 Jira blobs source↔vendor | both | 12/12 OK | execution |
| ls-tree workflow package + sidecars; SharePoint grep | hermes wt | package lacks showcase; 0 sharepoint | execution |
| `seed_baked_capabilities` into temp HERMES_HOME | mktemp | only `ericsson` package staged | execution |
| Read `discovery.py`/`catalog_api.py` scan roots | hermes wt | profile+project only | inspection |
| diff loose vs package `my-tickets-summary.hermes.yaml` | hermes wt | staged copy carries stale env model | execution |
| Read `capability_staging.py` migration + `_merge_plugin_manifest_defaults` | hermes wt | generic, one-time, correct | inspection |
| 7 mutation checks in detached worktree | mktemp wt | M1–M6 caught; M7 POSIX-only | mutation |
| worktree remove + status re-check | roots | repos clean, at candidate SHAs | execution |

Four read-only evidence-gathering sub-agents (legacy parity; auth/origin/native;
curl transport; reads/normalization; comments/workflows; lifecycle/vendoring)
produced leads; every load-bearing claim in this report was reduced to direct
code, command, or mutation evidence I ran myself.

---

## 12. What was verified safe (adversarial cases attempted)

- **Origin escape:** trailing slash, `/jira` path suffix, missing scheme, http,
  port, IPv6, IDNA, userinfo, fragment, query, encoded issue keys, absolute-URL
  key — all rejected or confined; no cross-origin request constructed.
- **Secret leakage:** repr/str/format, `JiraError.args`, httpx exception chains,
  result serialization, curl argv (`ps`-visible), curl config path — no sentinel
  leaked; `_redact` scrubs remote text.
- **Transport fallback:** 401/403/generic-403/500-with-1010-body/CF-without-1010/
  ordinary-404/two-message-404/wrong-content-type — none diverted (mutation +
  synthetic).
- **Curl transport:** TOCTOU symlink swap (gated by root-owned paths); unbounded
  stdout flood; spawn failure; timeout past deadline; malformed/interim/duplicate/
  oversized header blocks; status-spoof vs `write-out`; redirect blocks — cleanup
  and classification held; the `write-out` cross-check defeats header spoofing.
- **Reads:** empty/short/duplicate/malformed pages; lying/omitted `total`;
  filter-removes-whole-page; ADF depth/width/unknown/malformed/huge nodes; hostile
  hrefs (`javascript:`, userinfo); GitLab lookalikes — no invention, no raw-object
  leak, bounded work; the completeness gaps in §5/§7 are the exceptions.
- **Comments:** caller-injected `approved`/`tool_admission`; stale/reused approval
  for a different body; duplicate clicks; concurrent identical comment; conflict;
  lost-response-after-success; dry-run (zero remote I/O); v3/v2 body shapes — the
  args-bound admission and no-blind-retry held; JIRA-H2 is the human-grain
  exception.
- **Lifecycle/vendoring:** fresh/upgraded/disabled/re-enabled/restaged/
  separate-profile; byte parity; SharePoint absence; merge resolution; Desktop
  projection — all held except JIRA-H3.

---

## 13. Residual installed-UAT-only and MEDIUM risks (not HIGH findings)

1. **v3→v2 classifier sentinel strings (top residual).** `is_rest_version_
   unsupported` keys on "REST API v3 endpoint is not available/unsupported"
   (`client.py:31-36`). These are not known Jira wire messages; against a v3-less
   Jira Server/DC (the legacy `eteamproject.ericsson.net` class) the fallback may
   never fire and `rest_api_version:auto` may fail all reads/writes with
   `not_found`. Legacy fell back on any v3 error. **Task 10 must confirm on a real
   Server whether `auto` works or whether `rest_api_version:2` must be documented
   as the Server default.** Cannot be established without a live service.
2. **Native response fully buffered before the 1 MiB check** (`transport.py:444`
   → `client.py:205`); curl stdout/stderr via `communicate()` bounded only
   post-hoc. Realistic consequence is MEDIUM (trusted authenticated origin under
   TLS; a legitimately large search returns `capacity`). Invariant 8's letter is
   violated for the native path; fix by streaming with an early abort.
3. **Cancellation not polled during the native request or the retry sleep**
   (bounded by ≤120 s per-request timeout + shared deadline). MEDIUM
   responsiveness gap.
4. **Curl TOCTOU** between validation and launch (`transport.py:113` vs `336`) —
   an approved path swapped to a symlink runs the swapped exe with the private
   config; gated by root-owned default approved paths. MEDIUM defense-in-depth.
5. **Curl child env not scrubbed** — `CURL_CA_BUNDLE`/`SSLKEYLOGFILE` honored by
   the child while native uses `trust_env=False`. MEDIUM (needs hostile env).
6. **Readiness `Ready` with a residual inactive-mode secret** — setting `pat`
   then switching to basic (or vice versa) leaves both secrets; `auth.py` fails
   closed but readiness reports Ready and the conflicting field is hidden. MEDIUM
   usability wedge.
7. **`marks: null` in an ADF text node** raises `TypeError` (`operations.py:97`),
   classified to `invalid_input` at the tool boundary (`__init__.py:94-103`) —
   not a crash, but a read of such a ticket returns an error. Uncertain trigger
   realism. MEDIUM/uncertain.
8. **Truncation/pagination completeness** — `my_tickets` drops
   `truncated`/`warnings`; the loop can break on a defaulted/omitted `total` or an
   empty intermediate page with `truncated=False`; no cross-page dedup. Bounded by
   priority-DESC ordering (top-N by priority still shown). MEDIUM.
9. **Comment reconciliation** is body-only (author-blind) and single newest-100
   page — an older duplicate or a concurrent identical comment by another actor
   is mis-attributed/missed. LOW; the no-retry guarantee holds.
10. **GitLab URLs in comments/custom fields not extracted** into `gitlab_urls`
    (description/environment/summary only) — a legacy-compatible ticket whose link
    lives only in a comment yields no project via `jira_get_issue`. MEDIUM parity
    gap; the model can still read the comment text.
11. **Installed-Windows-only** items that cannot run here: native curl path
    resolution/permissions, real bearer/basic deployments, real Cloudflare-1010,
    process/log inspection, Desktop configuration rendering, restart/upgrade state.

---

## 14. Security-exclusion confirmation

No standalone threat-model, security-audit, security-review, penetration-test,
vulnerability-scanner, or exploit-development activity was invoked or attempted.
No real credentials, malicious payloads, or live Jira/GitLab/Ericsson/Microsoft
services were used; no network request was made. All reproductions used benign
synthetic data, fake transports/executables, and isolated temporary state under
`mktemp -d`. No release, push, tag, brand mutation, ref advance, or Task 10 action
was performed. The only persistent write is this report. Both repositories remain
on their exact candidate SHAs (`f52a131c` / `7d35a7ec`) with no tracked change and
every pre-existing worktree preserved; the one temporary detached worktree and all
temp directories I created were removed.

---

## Findings recap

Three HIGH, zero CRITICAL:

- **JIRA-H1** — Windows-default `curl_executable` disables all Jira tools on the
  release target.
- **JIRA-H2** — Comment-write approval widens from the tool name alone and is
  target/body-blind.
- **JIRA-H3** — The Jira showcase and jira-to-gitlab workflows are never staged
  to a profile; the staged `my-tickets-summary` is the stale env-based copy.

JIRA CANDIDATE MUST NOT ENTER TASK 10 UNTIL ALL CRITICAL AND HIGH FINDINGS ARE RESOLVED.
