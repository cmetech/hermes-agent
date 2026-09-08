# Workflow Package Marketplace — Adversarial Code and Product-Quality Review (Claude lane)

## 1. Reviewer, lane, and environment

- **Reviewer:** Claude (model `claude-fable-5`, Claude Fable 5), independent adversarial review lane **Claude**.
- **Date:** 2026-09-07.
- **Host:** macOS 26.5.1 (Darwin 25.5.0), Apple Silicon `arm64`.
- **Tooling:** repository `.venv` Python 3.11.16; Node v24.20.0; git 2.55.0; repository-local Vitest 4.1.10, tsc, tsx, and Playwright (all resolved and identity-validated by the repository merge gate — no npx, no installs, no network). All Python evidence ran through `scripts/run_tests.sh` with `HERMES_TEST_FILE_RETRIES=0`; pytest was never invoked directly. The two excluded suites (`tests/plugins/workflow/test_phase3_bash_lexer_security.py`, `tests/hermes_cli/test_persistent_session_recovery.py`) were not run or collected by me.
- **Independence:** I did not read `.superpowers/sdd/2026-09-03-workflow-package-marketplace/`, the Codex report, or any marketplace reconciliation/remediation report. Findings below were frozen from my own inspection, probes, and gate runs.

## 2. Immutable scope verification and input hashes

Verified in `/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-package-marketplace`:

- Branch: `feat/workflow-package-marketplace`; `git status --porcelain --untracked-files=no` empty (tracked checkout clean). Untracked at start: only the review prompt.
- `HEAD` = `38c702203abbe213049eea4731750133508c0960` (candidate commit — exact match).
- `HEAD^{tree}` = `642e9cb29e1e1a7cdc717df3e849be621f4cdf5a` (candidate tree — exact match).
- `git merge-base c1dc7a2… HEAD` = `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d` (feature start — exact match).
- `git rev-list --count` over the range = **131** commits; `git diff --shortstat` = **219 files changed, 184,919 insertions(+), 774 deletions(-)**; `git diff --check` clean (exit 0). All exactly as specified.

All nine binding-input SHA-256 digests were computed with `shasum -a 256` and matched the prompt exactly:

| Artifact | SHA-256 (verified match) |
| --- | --- |
| AGENTS.md | `a19cb8c30fb0f9a73089b98c9fc20ce59759817b5564f0bb9e8110822799cfb4` |
| apps/desktop/AGENTS.md | `4300a2e1e636a71bcec9d4211010f749ab78719f8b9ee8544c74f1f000be8ced` |
| apps/desktop/DESIGN.md | `390abf9aa4cf4418f542f91f782f48a1283621638c90bad5946c63382d10845c` |
| Marketplace design | `dd721b9f7b9370d8a5187d1b55c78658804c6706c5c9d3f2c2534ada411b7b6a` |
| Lifecycle recovery amendment | `6d5f812ca87f0f65b86dc9a75171e3786c6b7575a5fca830181a58104466a203` |
| HTTP compatibility amendment | `b14c9b28f1c092c63b61449e67b89f1445bccea739c920a28760ef7ef2f75a25` |
| Strict wire amendment | `07830657fe35928a9135d7aca932b06ff6d6c6f13232ab960ba38afddf851fb5` |
| Supervisor identity amendment | `d85aae7d6e11710ef8e65a338a7f34afea8ba97ad190003115acdc6f1ca4857c` |
| Lifecycle recovery plan | `cfbf33e209721716330abee0af5c9053753b4f37dfe864af92f54873d57df712` |

I additionally read the three later in-range amendments as candidate content: `2026-09-06-workflow-marketplace-inspection-digest-parity-amendment.md`, `2026-09-06-workflow-marketplace-transaction-workspace-isolation-amendment.md`, `2026-09-07-workflow-marketplace-inspection-admission-amendment.md`. No conflicts among the binding documents were found beyond the amendments' own explicitly stated supersessions (each amendment names its scope; none contradicts another inside that scope).

## 3. Verdict

**PASS.**

No CRITICAL, IMPORTANT, or MINOR finding survived the ten-element proof standard. I attempted to falsify the delivered claims across package/Git/trust/transaction safety, lifecycle admission and recovery, Electron identity and supervision, user-visible truth, UI/accessibility, and release tooling; every attempted break either failed closed as specified or was already prevented by an exact mechanism I could trace to its production code. Concerns that did not meet the proof standard are recorded in §18, not as findings.

## 4. Findings table

| ID | Severity | Title |
| --- | --- | --- |
| — | — | *No qualifying findings.* |

## 5. Ten-element proofs

Not applicable — no finding qualified. Everything I could not fully prove is listed under §16 (unverified evidence) and §18 (non-defect observations and unresolved questions), per the prompt's "be specific or be silent" rule.

## 6. Locked invariants (all 28)

| # | Invariant (abbreviated) | Rating | Direct evidence |
| --- | --- | --- | --- |
| 1 | Strict, bounded, canonical manifests/catalogs | **PASS** | `contract.py`/`package.py`/`models.py` strict Pydantic (`extra="forbid"`, `strict=True`), NFC + casefold collision rejection (`package.py:227–261`), 512-file/1 MiB/8 MiB limits enforced (probe: `package_file_count_limit`, `package_file_size_limit` fired); duplicate-key rejection via `_strict_json`; `test_marketplace_contract.py` + vectors green in gate. |
| 2 | Exact identity; no case/Unicode/alias collision or cross-read | **PASS** | `PACKAGE_ID_PATTERN`/`SOURCE_NAME_PATTERN` are ASCII-lowercase-only (`models.py:33–34`) so no Unicode/case alias space exists; source names NFKC-casefolded then pattern-checked (`source_store.py:659–660`); installed identity is `source_key + package_id` with per-profile stores keyed by `canonical_profile_key` (resolved home). Cross-profile isolation exercised by `test_marketplace_admissions.py`, `test_marketplace_lifecycle_api.py` (green). |
| 3 | No credential/token/path leakage into public projections | **PASS** | Probe: `validate_credential_free_git_source` rejected userinfo in every form incl. percent-encoded and token-as-user (§14 P2); `_sanitize_result` redacted `confirmation_token`, nested `ACCESS_TOKEN`, `password`, and rewrote internal `repository_url` (§14 P1); `safe_git_error` + 4 KiB bound in `git.py:120–127`; V2 history token-free (`complete_read` pops `confirmation_token`, `lifecycle_state.py:851–858`); evicted receipts carry no body/fingerprint (`AdmissionEvicted` closed model). |
| 4 | Bounded, atomic, scoped source refresh with last-known-good cache | **PASS** | `git.py` budgets (files/bytes/traversal/output/temp), single-shot filter fallback, cleanup on every exit path (lines 696–721); catalog publication evidence captured at the atomic write boundary and uncertainty is monotonic (`lifecycle_state.py:254–366` — a fallback status write cannot clear `entered`); failed refresh preserves prior cache (`test_marketplace_catalog.py` green). |
| 5 | Validation binds bytes/digests/identity; replacement/symlink/TOCTOU fail closed | **PASS** | Probes (§14 P3/P4): symlink, fifo, nested root, nested `.git`, oversize, over-count, byte mutation, extra file, missing file, hard-linked extra, wrong expected digest — all rejected; transactions revalidate `load_distribution(expected_digest=…)` at every phase (`_TransactionPhases.validate`), directory identity `(st_dev, st_ino)` pinned via `O_NOFOLLOW` dir-fds (POSIX) / no-delete-share guards (Windows), with the portable threat boundary explicitly documented (workspace-isolation amendment §2.1). |
| 6 | Transactional install/update/remove; no partial mislabeled as rolled back | **PASS** | Journaled phase machine (`atomic_install`/`atomic_remove`); post-commit cleanup failure raises `transaction_rollback_failed` (never "unchanged") at `transactions.py:2543–2548`; rollback verified only after re-reading bytes+provenance+trust with recovery clear (`lifecycle_state.py:558–585`); fault-injection suites `test_marketplace_transactions.py`, `test_marketplace_lifecycle_state.py` (128✓) green. |
| 7 | Trust separate, byte-bound; single grant returns complete map | **PASS** | `grant_trust` re-verifies digest/review under the lock and captures the full trust snapshot inside `trust_store.trust_origin_many(observe=…)` (`service.py:2190–2270`) — the amendment's "final read outside the lock" defect is gone; effective digest binds distribution+path+closure with domain separation (`trust_binding.py`, probe P1); Desktop validates complete known map and selected-trusted (`use-package-trust-review.tsx:125–195`, codec). Install never grants trust (e2e proves `workflow_trust_required` after install). |
| 8 | Credential-free discriminated public subjects; safe recovery identity | **PASS** | `LifecycleSubject` closed union; direct-install selector is HMAC-SHA256 under an epoch-local process secret (`admissions.py:direct_selector_id`) exposing no URL/ref/path; evicted tombstone carries id/request/epoch/profile/kind/subject/selection only. |
| 9 | Bounded scoped content-bound admission IDs; replay finds original; no blind duplicate | **PASS** | Probe P1: replay returned same operation; changed body/kind → `marketplace_request_conflict`; cross-actor lookup returns nothing; expiry (5 min/30 s skew), 24 h receipt retention, 4,096 cap, epoch rejection all behaved; `lifecycle_api.start_operation` + registry `start` do receipt lookup before token authorization (`operations.py:974–991`); e2e "recovers a lost confirmation…" passed in a real browser against a real backend. |
| 10 | Exact operation/request/profile/kind/phase/subject/result correlation | **PASS** | Backend: `require_result_kind`, refresh source-name equality, strict publication revalidation (`_strict_public`); Desktop: `acceptSupervisedOperation` and `correlate()` require id+request+kind+subject+selection+profile+epoch equality before any watch update (`workflow-marketplace-supervision.ts:9–30`, `workflow-marketplace-lifecycle.ts:278–296,436–440`). |
| 11 | Backend owns truth; Desktop never derives from timestamps/latest/kind | **PASS** | Presentation derives versions only from `outcome.package_state` verified fields; "remains installed" requires equality with the authoritative review's old/current version (`package-lifecycle-presentation.ts:151–182`); supervisor never selects by recency (exact-ID lookups only, `scan`/`reconcileScope`). |
| 12 | Preparation tokens ephemeral | **PASS** | Token fetched only by explicit `getLifecycleReviewToken`; confirm body's token zeroed synchronously after the supervisor copies it into its private admission closure (`use-package-lifecycle.tsx:126–128`); backend vault memory-only; V2 list/get/admission history token-free; query keys contain no token (`query-keys.ts`). |
| 13 | Application supervisor outlives views; exact resume; origin-only reconcile | **PASS** | Supervisor initialized in `main.tsx` above Workflows; records keyed by full five-field binding + request/operation id (`supervisionKey`); rendered proof: Playwright "recovers a lost confirmation across close and tab return without bypassing failed state reads" (green, real backend). |
| 14 | Capability/epoch/principal/generation revalidated across every async seam | **PASS** | Electron: `expectedConnectionGeneration` asserted before dispatch and again in `finally` before return (`lifecycle-api-transport.ts:239–292` — a stale body/error is masked); backend: raw-header cardinality-then-constant-time principal check before any route work (`lifecycle_api.py:151–168`); renderer: branded WeakMap clock observations invalidated on any binding mismatch. |
| 15 | Generation exhaustion fails closed for main-process lifetime | **PASS** | `begin()` at `MAX_SAFE_INTEGER` sets permanent `exhausted`, clears claims/routes/leases, notifies, and every later allocation/dispatch throws the exhausted sentinel (`connection-generation.ts:130–147`); state lives in main-process closure — renderer reload cannot clear it; `connection-generation.test.ts` green in gate. |
| 16 | Guards/schedulers/listeners released without losing barriers | **PASS** | Scheduler frees IPC slots only on physical settlement (`createSupervisionScheduler`), abort listeners removed in every path (`waitForSupervision` cleanup); barriers are separate record state; start rejects at 128 unresolved barriers instead of evicting one (`supervisor.ts:1096–1098`). |
| 17 | Outcome states distinguished; no false rollback/version copy | **PASS** | `Outcome` union implemented verbatim; `rollback_failed`/`recovery_ambiguous` → recovery-required always (`lifecycle_state.py:_RECOVERY`); presentation renders the outcome-to-copy table exactly, incl. "Currently installed: X" vs "Version X remains installed" distinction. |
| 18 | Terminal success is a mutation barrier over stale cache | **PASS** | `advance()` cancels+invalidates before POST; `projectionFresh` requires post-barrier generation + un-invalidated success (`workflow-marketplace-reconciliation.ts`); test "keeps Install disabled after success when refetch fails" and rendered e2e failed-state-read scenario both green. |
| 19 | Ambiguous outcomes disable contradictory actions, claim nothing | **PASS** | `getPackageGate` returns busy/unknown/recovery_required for uncertain records or unsafe locked state; unsafe package-state observations fence the whole origin even with no local mutation record (`reconciliation.accept`); copy says "State could not be confirmed…". |
| 20 | Legacy read mode: explicit unsupported + no prior V2 binding + fresh fetch; no mutations | **PASS** | `legacyReadAttempt` requires `explicitLegacyUnsupported` (identity-rechecked unsupported probe) and `!current.lastBinding` (no prior V2 binding this app lifetime) (`workflow-marketplace-connection-binding.ts:102–111, 258–269`); V1 mutation routes are retired server-side regardless (`api.py:1265–1279`). |
| 21 | One Escape, one action; admission cannot be bypassed; predictable focus | **PASS** | `claimLifecycleEscape` preventDefault+stopPropagation; outer route checks `marketplaceEscapeIsOwned` (defaultPrevented + open-portal containment) before narrow-list navigation (`index.tsx:632`); admission busy locks Escape/outside-click/close (`install-review-dialog.tsx:292–321`); focus policy in `useLifecycleDialogFocus` (safe-close start, survive-or-heading). `lifecycle-accessibility.test.tsx`, `lifecycle-navigation.test.tsx` green. |
| 22 | Capability-gated, keyboard operable, SR-truthful, five locales, no premature success | **PASS** | `supervisor.supports(binding, [...])` gates every action; `role="status"`/`role="alert"` split matches severity; success copy rendered only from committed outcome with verified state; sampled `ar`, `ja`, `zh`, `zh-hant` marketplace strings are genuine translations (§14 P6); `languages.test.ts` green. |
| 23 | Narrow/zoom/RTL/reduced-motion/long-content/all states readable, no page overflow | **PASS** (rendered proof at the required extreme; residual cells source-level — see §11/§16) | Real-browser gate scenario asserts direction=rtl, 320 CSS px inner width, 200 % zoom, `prefers-reduced-motion` honored, `noHorizontalOverflow: true`, and V2 actions reachable; overflow/truncation classes present in all list/detail/review surfaces; state-copy coverage via jsdom suites. Theme cells and long-CJK rendering were not individually screenshotted (§16). |
| 24 | Release tooling offline, source-clean, proven local CLIs, receipts suppressed on failure | **PASS** | Gate validates dependency-root containment and exact CLI identities via `workflow_gate_clis.py` before work (`test_workflow_merge_gate.sh:150–215`); `HERMES_OFFLINE=1`, keys blanked; no npx/npm-install/network path exists in the script; receipt printed only after clean-tree check — observed empirically: failing run 1 emitted **no** `TESTED_BASE_SHA`, passing run 2 emitted it (§15). |
| 25 | Transaction workspaces: exact ownership, containment, cleanup, recovery provenance | **PASS** | Marketplace scratch roots moved under the private `marketplace/workflows/.staging`/`.quarantine` (`transactions.py:1115–1116`), 0700 + identity-pinned; cleanup validates current identity + exact `owner.json` bytes immediately before removal and retains on any mismatch (`_PreparationWorkspace.cleanup`, `_remove_owned_envelope`); `recover_transactions`/`inspect_recovery` refuse foreign/active/incomplete scopes; both RunStore initialization orders proven by `test_marketplace_installed_distribution_e2e.py` (green). |
| 26 | V1 catalog/run compatibility functional; V1 mutation starts retired; V2 authority not bypassable | **PASS** | V1 read/source routes kept with version-scoped `get_legacy`/`list_legacy`/`cancel_legacy`; own-V2-ID via V1 → bounded 409 before cancellation (`operations.py:1319–1330`); all nine V1 mutation routes 409 `marketplace_lifecycle_upgrade_required` after auth, before any admission (`api.py:1265–1279`); existing catalog/run suites green. |
| 27 | Installed distribution uses packaged files only | **PASS** | `MANIFEST.in`/`pyproject.toml` updated; `test_marketplace_installed_distribution_e2e.py` + `test_project_metadata.py` + `test_packaging_metadata.py` green inside the gate (clean temp-home install integration). |
| 28 | No regression to browsing/running, other profiles, transport, Git consumers, process registry, shutdown | **PASS** | Full workflow-plugin Python suite (7,368 tests, 0 failures on the sealed run), unchanged-caller suites green: `test_git_source.py`, `test_plugin_install_ref.py`, `test_plugins_cmd.py`, `test_capability_staging.py`, `test_workflow_dashboard_auth.py`, `test_managed_process.py` (80✓), `test_process_registry.py` (93✓); canonical Workflow UI Vitest suites green; Desktop build + Playwright green. |

## 7. Backend package / source / Git / trust / transaction matrix

| Boundary | Attack attempted | Result |
| --- | --- | --- |
| URL userinfo / tokens / percent-encoding | `user:pass@`, token-as-user, `x-access-token:`, `%75ser:%70ass@`, uppercase scheme+userinfo | All rejected `Git source identity contains credentials.` without echoing input (probe P2) |
| scp-like / schemes | `git@host:path` accepted (standard non-secret user); `eviluser:pw@host:path` rejected; `ftp://` rejected; leading/trailing whitespace rejected | Fail-closed |
| Sparse/checkout containment | traversal, absolute, backslash, NUL, `.git`, drive-prefix sparse paths | `_canonical_sparse_path` rejects each (`git.py:70–90`); index path components symlink-checked per level via `ls-tree` mode `120000` (`_preflight_index_components`) |
| Resource exhaustion | preflight `ls-tree` size/count before checkout; post-checkout scandir budgets; temp-storage and traversal-entry budgets; git output byte cap | All bounded with distinct diagnostic codes |
| Repository replacement / ref movement | resolved commit re-read after checkout must equal requested revision (`source_revision_mismatch`); prepare→confirm revalidates staged bytes against the bound digest; `installed_package_changed` on provenance drift | Fail-closed |
| Package content | symlink, fifo, hard-linked extra, nested root, nested `.git`, case collision, non-NFC, oversize, over-count, byte mutation, missing/extra file, publisher digest mismatch, wrong expected digest | All rejected (probes P3/P4; distinct codes) |
| Trust binding | traversal/`//`/backslash/absolute/`~`/NUL path or non-lowercase digest into `effective_marketplace_digest` | ValueError; domain-separated NUL-delimited HMAC-style canonical encoding prevents ambiguity (probe P1) |
| Transactions | fault at every journal phase (suite), cleanup-after-commit failure, provenance writer misbehavior, foreign `owner.json`, replaced directory identity | Committed-then-cleanup-failure → `transaction_rollback_failed` (recovery, never "unchanged"); unproven ownership always retained, never deleted |
| Two profiles / colliding IDs | Admission receipts, stores, staging, provenance, trust all keyed under the canonical profile home; cross-actor receipt lookups return nothing | No cross-scope read observed (probe P1 + suites) |

## 8. Lifecycle admission / outcome / recovery matrix

| Scenario | Backend behavior verified | Desktop behavior verified |
| --- | --- | --- |
| Exact replay of same POST | Same operation returned, one worker, no second token consumption (probe + `test_marketplace_lifecycle_api.py` 105✓) | `start` copies body into private closure; replay by exact request ID |
| Changed body/kind/subject under same ID | `409 marketplace_request_conflict`, original metadata undisclosed | Closed-code mapping; conflict rendered as known non-admission ("another package action is already running"), never unknown-outcome (`conflictingPackagePresentation`) |
| Lost POST response | Receipt + `GET /admissions/{id}` discriminated found/evicted; window-bounded | 15 s admission timeout → `admission_unknown`, barrier retained, replay/lookup drives recovery; **rendered e2e proof** (lost confirmation across close/tab return) |
| Wrong operation/kind/subject/profile in response | Strict publication revalidation; `operation.id != requested` → 500 internal | `correlate`/`acceptSupervisedOperation` throw `marketplace_invalid_response`; watch never replaced |
| Eviction | Tombstone with safe identity, `outcome_unknown`, no fictional failure | Evicted never clears barrier by itself; fresh package-state read releases per rules |
| Post-commit projection failure | Failed/outcome-unknown or recovery-required; trust snapshot captured inside write lock so "trust was not granted" cannot be fabricated | Unknown/rollback outcomes can never claim a version (presentation table) |
| Cancellation racing commit | `enter_atomic` checkpoint; cancellation after atomic entry waits for real terminal truth; late cancellation cannot erase publication (source evidence monotonic) | Cancel waits for correlated terminal truth |
| Inspection vs lifecycle admission | Asymmetric matrix implemented privately in registry (`_start` reserves target only for non-inspect kinds, shared check still blocks reads behind lifecycle work, `operations.py:1108–1111`) | Same matrix mirrored in `supervisor.start` (inspection records never block; nonterminal lifecycle barrier blocks both; terminal barrier admits inspection) |
| Epoch change / restart | Old-epoch IDs rejected (`marketplace_epoch_changed`), receipts/vault/epoch lost, journals/provenance/trust survive; busy/unconfirmed until writers quiescent | Reprobe-on-401/403/principal-changed/not-found before eviction classification; historical outcome stays unknown |

## 9. Electron identity / supervisor / cache / navigation matrix

| Seam | Verified mechanism |
| --- | --- |
| Native generation, pre-dispatch | `validateLifecycleRequest` requires positive safe-integer generation on exact V2 paths only; non-lifecycle paths reject lifecycle-only fields; `assertCurrent` before route resolution and again after resolution |
| Native generation, pre-return | `finally { assertCurrent() }` masks any stale success **or error** body (`lifecycle-api-transport.ts:289–292`) |
| Principal header ownership | Case-insensitive collision stripping in both `requestHeaders` and `mergeNativeRequestHeaders`; exactly one native-owned value injected; renderer cannot smuggle arbitrary headers; backend counts raw wire occurrences and constant-time compares |
| URL normalization | canonical path check pre- and post-URL construction; encoded spellings that decode into the V2 namespace rejected; fragments/`\`/control chars rejected; hash and non-http(s) rejected at final boundary |
| Exhaustion | Permanent in-closure flag; clears claims/routes/leases exactly once; every later `begin`/assert throws exhausted sentinel; coordinator exhausts all scopes and requires restart; no reprobe |
| Reconnect / reconfiguration | `probe()` quarantines presentation ('probing'), resolves descriptor → capabilities → descriptor identity re-check; same authority → `last_observed`, changed authority → cancel + **remove** colliding query root before publication (privacy purge) |
| Stale query results | QueryClient `cancelQueries` on quarantine; `projectionFresh` requires fetch-started-after-barrier generation; late old responses cannot fill behind newest read ticket (`beginPackageRead` order ownership) |
| Strict Mode / disposal | Supervisor is app-lifetime singleton from `main.tsx`; scheduler slots freed only on settlement; abort listeners removed on all paths; visibility hidden → suspend + quarantine, visible → reconcile |
| Clock observation | WeakMap-branded to the exact five-field binding; monotonic-only issuance; ±/discontinuity/300 s bounds; observation deleted on any failure or mismatch |

## 10. UI state → copy / action / focus matrix (as implemented and tested)

| State | Copy (authoritative source) | Actions | Focus/Escape |
| --- | --- | --- | --- |
| Preparing / progress | Phase label from closed table (`marketplacePhaseLabel`, unknown wire phase → "Unknown" label, never raw enum), `role="status"` | Close (always, admission excepted), Cancel when cancellable | Focus starts on Close; Escape closes; close never cancels backend |
| Review (install/update) | Exact identity, commits, digests, file changes, risks, blockers/advisories | Confirm disabled on blockers; confirm guarded against double-fire | Escape claimed by dialog; admission lock blocks Escape/outside/X for ≤15 s |
| Committed install/update | "Installed/Updated version X" + "Installation does not grant trust." (+ trust-required suffix) from verified `outcome.package_state` only | Review trust (capability-gated + fresh state) | Focus retained/heading fallback |
| Committed remove | "Package removal completed" only when verified state is `absent` | Return to surviving list | Fallback focus chain (surviving action → list → heading) |
| Known unchanged w/ verified state | "Version X remains installed" only if verified version equals the authoritative review's old/current version, else "Currently installed: X" | Prepare again | — |
| Known unchanged w/o state proof | "This attempt made no package changes." (no version) | Refresh state | — |
| Rollback verified | "Changes were rolled back." + actual state | Refresh | — |
| Recovery required | "State could not be confirmed. Package recovery is required." `role="alert"` | Recovery guidance (`package-state` / `recover-packages` commands documented), Refresh | — |
| Unknown (lost/evicted/invalid/restart) | "State could not be confirmed. The operation may have completed." `role="alert"` | Retry status / lookup; contradictory mutations disabled by gate | — |
| Admission conflict (pre-admission) | "Another package action is already running. No change was started." | Refresh + later retry | — |
| Check error / orphaned | "Could not check for updates." / "The installed package's source is unavailable." | Retry check / Manage Sources | — |
| Busy/reconciling package | Gate returns busy/reconciling; stale metadata labeled last observed | Mutations non-actionable | — |

## 11. Visual design, styling, accessibility, responsive, localization, adversarial UX

**Rendered evidence (real Chromium via the gate's Playwright phase, isolated build, synthetic backend fixtures):**

- `workflow-marketplace-layout.spec.ts` — asserts, in one rendered run: 320 CSS px inner width, 200 % zoom via the app's own zoom API, `document.documentElement.dir = 'rtl'`, `prefers-reduced-motion: reduce` honored, **no page-level horizontal overflow**, and V2 package actions still reachable. Passed (3.8 s).
- `workflow-marketplace-lifecycle.spec.ts` — real backend: install only after explicit confirmation (verified bytes), and lost-confirmation recovery across dialog close/tab return without bypassing failed state reads. Passed (8.5 s, 3.5 s).

No screenshots were persisted by these specs; I did not author new spec files (the only authorized repository write is this report), so no screenshot paths are available — the rendered assertions above are the evidence.

**Source-level assessment against the presentation matrix:**

1. **Information hierarchy** — list/detail split (`OverlaySplitLayout`-consistent grid, `sm:grid-cols-[minmax(15rem,0.8fr)_minmax(0,1.2fr)]`), page heading with `headingRef` focus target, source controls and search in a single header row; installed state/version/trust rendered from verified state only. Sound.
2. **Action hierarchy** — one `Button` primitive throughout; confirm is primary, Close secondary, destructive flows use review dialogs with blockers disabling confirm; disabled controls carry explanatory copy (busy/unsupported/recovery states each have distinct strings). No native `title=` on buttons (checked; the `title` hits are React props on `ErrorState`/sections).
3. **Dialog composition** — Radix `Dialog` primitive with `DialogHeader/Description/Footer`, independent scroll body, `w-[min(92vw,54rem)]`, `motion-reduce:animate-none`, close-X suppressed only during bounded admission. Escape/`onInteractOutside` locked only while admission is pending (≤15 s), matching amendment §9.
4. **Responsive** — narrow mode swaps to list⇄detail navigation with its own Escape rule; rendered proof of no horizontal overflow at the 320 px/200 % extreme; overflow classes (`truncate`, `break-all`, `min-w-0`, `overflow-y-auto`) present across list, detail (17 occurrences), and review sections.
5. **RTL & localization** — logical properties (`ms-2` etc.), rendered RTL pass; all five catalogs (`ar`, `en`, `ja`, `zh`, `zh-hant`) carry genuine, meaning-preserving translations of the controlled lifecycle vocabulary (sampled: unconfirmed, conflict, recovery-required, no-trust-on-install, version-remains — §14 P6); protocol values are never translated (closed label tables in `controlled-copy.ts` map wire values → localized copy, unknown → safe "Unknown" label).
6. **Accessibility** — dialog names via `DialogTitle`, `aria` roles `status` vs `alert` split by severity; focus starts on safe Close, is trapped, restored via `onCloseAutoFocus` override with fallback chain; `useLifecycleDialogFocus` refocuses only when a dialog-owned node became unusable (external navigation keeps ownership); jsdom suites `lifecycle-accessibility.test.tsx` and `lifecycle-navigation.test.tsx` cover portal/document Escape, focus after success/failure/cancel/disappearance. Passed.
7. **Motion & feedback** — progress is phase+percent text under `role="status"`, never a completion claim; reduced motion rendered-verified; no `transition-all` on hot paths in the marketplace surfaces.
8. **Cognitive load** — copy uses user language ("State could not be confirmed", "Installation does not grant trust", "Another package action is already running. No change was started."); no transaction codes reach copy; retry-safety is explicit per state.
9. **State continuity** — close/reopen, tab switch, profile switch, disconnect/reconnect, and package disappearance covered by supervisor + navigation tests and the rendered lost-confirmation scenario; background completion never opens dialogs or steals focus (origin-scope-only reconciliation).
10. **Visual consistency** — zero raw colors/hex/rgba in marketplace components (audited); tokens (`--ui-text-*`, `--ui-stroke-*`) and shared primitives (`Button`, `Dialog`, `SearchField`, `ErrorState`, `EmptyState`, `Loader`, `ConfirmDialog` for source deletion) throughout.

**Pragmatism filter output:** no REAL DEFECT and no VALID LOW-PRIORITY ISSUE met the proof standard (exact state+viewport+impact with rendered or equivalent evidence). Items I could not render are listed as UNPROVEN runtime cells in §16, not converted into findings.

## 12. Release / offline / process ownership assessment

- The merge gate validates dependency-root containment (worktree/shared/invocation roots only, dangling links rejected), then proves exact local CLI identities (`workflow_gate_clis.py` resolves tsc/vitest/tsx inside the allowed roots by real path containment and package identity) **before** any work. `HERMES_OFFLINE=1`, provider keys blanked. No npx/npm-install/yarn/pnpm/network fallback exists anywhere in the script.
- Receipt discipline verified empirically: run 1 failed (see §15) and printed **no** `TESTED_BASE_SHA`; run 2 passed and printed `TESTED_BASE_SHA=38c702203abbe213049eea4731750133508c0960` (the exact candidate commit). A dirty tracked tree refuses sealing (`test_workflow_merge_gate.sh:410`).
- Process ownership changes (`tools/managed_process.py`, `tools/process_registry.py`) are narrow and fail-closed: setsid group identity preserved when the leader exits before capture; `_build_systemd_scope_argv` now returns `None` when `systemd-run` is absent instead of silently granting scope authority; post-spawn setup failure under a scope stops the unit, reaps the direct wrapper, and **raises** if either fails rather than leaking. Unit suites (80✓/93✓) green; live systemd behavior is Linux-only (§16).
- Gate self-tests (`tests/scripts/test_workflow_merge_gate.py`, 166✓; `test_workflow_gate_build.py`, 50✓) exercise missing/malformed/linked/escaping fixture cases.

## 13. Test integrity and unchanged-caller assessment

- Backend/Desktop wire parity is generated, not hand-shaped: fixtures come from real temporary-Git/service/registry scenarios (`generate_workflow_marketplace_lifecycle_fixtures.py`), `--check` byte-drift enforced in the gate, and the V1 inspection fixture is produced through a genuine V1 registry admission with **distinct** root/workflow digests (14C2P), so the historical hand-mock hazard (equal-digest fixtures hiding decoder defects) is closed for the surfaces that matter.
- Load-bearing mocks: Desktop transport mocks feed raw fixture JSON to the **real** helpers/decoders; the lifecycle test harness uses a real QueryClient/supervisor. I found one place where a hand-written strictness rule exceeds the backend contract (V1 review decoders' workflow-digest equality, §18) — it has no production path and therefore no demonstrated defect.
- Unchanged callers audited: `hermes_cli/git_source.py` additions are pure extensions consumed by both the plugin installer and marketplace (existing `test_git_source.py`/`test_plugin_install_ref.py`/`test_plugins_cmd.py` green); `plugins/workflow/discovery.py` is manifest-aware via the marketplace discovery module while loose discovery keeps its shape (suite green); `utils.py` gained no-follow/atomic helpers with their own suite (`test_atomic_write_text_nofollow.py`); shared dialog (`confirm-dialog.tsx`), `search-field`, workflow header, and locale types changes all covered by their existing suites in the gate.
- The excluded suites were not run; nothing in the candidate imports them.

## 14. Top adversarial probes and observable results

All probes ran from `/tmp` scratch scripts importing the candidate with `HERMES_HOME` pointed at throwaway temp homes; all probe processes and temp directories were removed afterward.

- **P1 — admission/idempotency/digest/sanitizer probe:** 29 checks, all behaved: replay→same operation; changed body/kind→conflict; cross-actor isolation; 5-minute window and 24-hour receipt expiry; old-epoch rejection; `effective_marketplace_digest` rejects traversal/`//`/backslash/absolute/NUL/uppercase; `lifecycle_relative_path` rejects newline/U+2028/drive/`.GIT`/non-NFC/trailing-slash/1025-cp and accepts U+FEFF (per strict-wire amendment) and 1,024 cp; `_sanitize_result` redacts nested tokens/passwords and internal repo URLs.
- **P2 — credential-free Git identity probe:** every credential-bearing form rejected (userinfo with/without password, percent-encoded, token-as-user, uppercase, ssh `root:pw@`); `git@host:path` and plain https/ssh/file accepted; whitespace-wrapped and unknown schemes rejected.
- **P3 — hostile synthetic package:** symlinked member, symlinked directory, fifo, nested package root, nested `.git`, >1 MiB file, >512 files, publisher digest mismatch — each rejected with its specific diagnostic; even my "clean" hand-built manifest was rejected by strict schema (fail-closed by default).
- **P4 — real fixture mutations:** clean fixture accepted (digest `c24e87da0d7407fc…`, 7 covered files); hard-linked extra (internal and cross-directory), single-byte mutation, extra undeclared file, missing declared file, and wrong `expected_digest` all → `package_digest_mismatch`.
- **P5 — gate flake bisection:** the one no-tests-ran file from gate run 1 reran green in isolation (7✓, 6.1 s) — see §15/§17.
- **P6 — locale truthfulness sampling:** controlled lifecycle strings in `ar`/`ja`/`zh`/`zh-hant` are real translations, not English placeholders; interpolation functions present per locale.

## 15. Verification command ledger (exact)

All from the review worktree unless noted; `$WT` = the worktree root.

1. `git status --short --branch` / `git status --porcelain --untracked-files=no` / `git branch --show-current` / `git rev-parse HEAD HEAD^{tree}` / `git merge-base c1dc7a2… HEAD` / `git rev-list --count …` / `git diff --shortstat …` / `git diff --name-only …` / `git diff --check …` — all matched the immutable scope; `--check` exit 0.
2. `shasum -a 256` over the nine binding inputs — all matched (§2).
3. **Gate run 1:** `env -u WORKFLOW_MERGE_GATE_FAST HERMES_TEST_FILE_RETRIES=0 scripts/test_workflow_merge_gate.sh --phase base` → **exit 1**. Python phase: 2,485 files, **7,368 tests passed, 0 failed**, but `tests/plugins/workflow/test_ai_extensions_middleware_e2e.py` reported "no tests ran (collection/import error, timeout before collection)" with a faulthandler dump characteristic of the per-file timeout under full 9-worker load. No receipt emitted (correct suppression). Duration ≈ 47 min.
4. `HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_ai_extensions_middleware_e2e.py` → **7✓, 0 failed, 6.1 s** (twice; second run to capture the summary line). File is byte-identical to feature start (`git diff --name-only <start>..HEAD -- <file>` empty; last touched by upstream merge `59de9bfea2`).
5. **Gate run 2 (sealed):** same command, machine otherwise idle → **exit 0**. Python phase: 2,485 files, 7,368+ tests, 0 failed (marketplace highlights: `test_marketplace_api.py` 569✓, `test_marketplace_service.py` 635✓, `test_marketplace_lifecycle_api.py` 105✓, `test_marketplace_lifecycle_state.py` 128✓, gate self-tests 166✓); generators/inventory checks; Vitest UI + Electron suites; typecheck; source-clean isolated Desktop build (`assert-dist-built` ✓); Playwright: **3/3 passed** (layout 320 px/200 %/RTL/reduced-motion 3.8 s; real-backend install 8.5 s; lost-confirmation recovery 3.5 s). Receipt: **`TESTED_BASE_SHA=38c702203abbe213049eea4731750133508c0960`**.
6. Probes P1–P4, P6 (§14) via `$WT/.venv/bin/python /tmp/mp-review-probes/probe{1..4}.py` — outputs recorded above; probe scripts and temp homes deleted afterward; `ps` sweep confirmed zero leftover pytest/vitest/playwright/systemd-run descendants.

A green gate is treated as evidence, not proof; the adversarial conclusions above rest on the code inspection and probes, with the gate corroborating.

## 16. Unverified evidence (explicit)

- **Native Windows:** UNPROVEN at runtime — Windows directory-guard paths (`_open_windows_directory_guard`, no-delete-share pinning, retained-artifact cleanup refusal), `O_NOINHERIT`/`O_BINARY` behavior, and Windows Electron behavior were reviewed in source and via their unit tests only; no Windows host was available.
- **Native Linux / systemd:** UNPROVEN at runtime — systemd scope creation/teardown and the new fail-closed scope-authority changes were verified by unit tests and inspection only; no live systemd host.
- **Current-base integration:** UNVERIFIED, per prompt — `base` was context only; I did not merge or simulate a merge. Read-only observation: `base` has advanced past the recorded prompt-creation SHA; any conflict against today's `base` is an integration task, not a candidate defect, and I found no violation of the candidate's own binding contracts stemming from it.
- **UI matrix cells without rendered proof:** per-theme (light/dark/system) rendering of every lifecycle state, long-CJK and maximum-length translated labels at 320 px, and maximum-bounded list rendering were assessed at source level (tokens, truncation classes, jsdom assertions) but not individually rendered/screenshotted. The rendered evidence that does exist (§11) covers the required extreme coordinates and the two full lifecycle flows.
- **Large generated fixture corpora** were verified through the generator's `--check` (byte-exact regeneration) and differential suites inside the gate rather than by line-by-line human reading; I inspected their producers and consumers instead.

## 17. Candidate vs. feature-start attribution for every failure/warning

| Event | Attribution |
| --- | --- |
| Gate run 1 exit 1 (`test_ai_extensions_middleware_e2e.py`, no tests ran / pre-collection timeout) | **Environment/load, not candidate.** File byte-identical to feature start; 0 of 7,368 executed tests failed; file passes in isolation in 6.1 s; recurrence absent in the sealed run. Retries were disabled per review instructions, so a single timeout fails the run by design. The candidate neither touches this file nor worsens its runtime. |
| pytest faulthandler dump in gate run 1 log | Same event as above (timeout diagnostics), not a crash in candidate code. |
| `zsh: no matches found` / `=== not found` noise in my own compound commands | Reviewer shell quoting; no repository effect. |
| DESIGN.md i18n bullet lists four locales while five exist | **Pre-existing doc drift** — `ar.ts` was added before feature start (`5b6990e7a0`); the candidate correctly updated all five locales and did not touch (and could not touch — hash-pinned) DESIGN.md. Not worsened by this feature. |

No other failure or warning was observed in any verification run.

## 18. Non-defect observations, feature requests, unresolved questions (not findings)

1. **Dead-but-overstrict V1 review decoders.** `workflow-marketplace-codec.ts` `reviewsMatchAssessment` (line 1772, applied at 2375/2484) requires every workflow review item's `package_digest` to equal the candidate distribution digest. The backend populates review items with per-workflow *effective* trust digests (`service.py:1509` via the marketplace binding created at review time, `service.py:1352–1372`), which generally differ from the root digest — the same semantic that 14C2P fixed for the *detail* decoder. This cannot fire in production because V1 mutation starts are retired before admission (`api.py:1265–1279`) so no V1 operation can ever carry an `install_review`/`update_review` result, and the V2 path uses the generated lifecycle codec (real-backend Playwright install passed through it). Without a production path this fails the proof standard; I recommend deleting or aligning the unreachable V1 review decoders during the next cleanup so a future V1-observation change cannot resurrect the wrong rule.
2. **HTTPS URL query/fragment acceptance.** `validate_credential_free_git_source` accepts `https://…/r.git?x=1` and `…#frag`; normalization (`resolve_git_source`/`canonical_git_source`) and re-validation happen downstream at the direct-install boundary. I could not produce a credential leak, identity collision, or containment escape from it (dot-segment URLs create *distinct* source keys, i.e., benign aliasing splits, never cross-reads). Question for the maintainers, not a defect: is rejecting query strings outright on https sources preferable for identity hygiene?
3. **Hidden-document handling quarantines presentation, not just polling.** `supervisor` `offVisibility` suspends (and thereby quarantines) owned scopes when the document hides, with a reprobe on return. This is strictly safer than the spec's minimum ("pause polling while hidden") at the cost of a probe on every tab return. Deliberate-looking; noting for awareness only.
4. **Feature request (out of approved scope):** the recovery-required dialog gives the exact `package-state`/`recover-packages` commands in copy; a copy-to-clipboard affordance for those commands would help the keyboard-only operator persona. The approved design does not require it.
5. **Unresolved question:** gate run 1's per-file timeout under full parallelism suggests the fixed per-file timeout is tight for this machine class under load; whether to raise it or lower default parallelism is a repo-level (not candidate) decision.

## 19. Final tracked worktree status

After writing this report (the only repository write performed by this review):

```
git status --porcelain --untracked-files=no   → (empty; exit 0)
git status --short                            → ?? docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-prompt.md
                                                ?? docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-claude.md
git rev-parse HEAD                            → 38c702203abbe213049eea4731750133508c0960
```

Tracked files untouched; branch, HEAD, and tree unchanged; the only new file is this authorized lane report (plus the pre-existing untracked prompt). All probe temp directories and background processes created by this review were removed/verified quiescent.

---

PASS — NO QUALIFYING ADVERSARIAL FINDINGS.
