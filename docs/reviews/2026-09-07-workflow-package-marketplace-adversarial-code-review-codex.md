# Hermes Workflow Package Marketplace — independent Codex review

## 1. Reviewer, independence, and verdict

- Lane: Codex. Agent designation: GPT-5-based Codex; an exact deployment/model build identifier was not exposed and is not inferred.
- Date: 2026-09-07.
- Host: macOS 26.5.1 (25F80), Darwin arm64.
- Tools: repository-local Python 3.11.16; Node v24.20.0; Git 2.55.0; TypeScript 6.0.3; Vitest 4.1.10; tsx 4.23.1; Playwright 1.62.1; Electron 41.10.4.
- Method: final-source inspection, immutable feature-start comparisons, the prescribed gate without retries, disposable real-Git service probes, and rendered Electron probes using the candidate's isolated build and synthetic backend fixture.
- Independence: no other model, subagent, prior marketplace review, reconciliation, remediation, or SDD progress report was consulted. The Claude report appeared in a later status listing; its contents were not opened. Findings and verdict were reached independently.
- Verdict: **BLOCK**. Two Important and two Minor findings qualify. No Critical finding is claimed.

The green base gate is genuine evidence for the tests it ran. It does not refute the four counterexamples below. PASS entries in this report mean the inspected paths and bounded exercised cases supported the invariant; they are not a formal proof of every possible execution. UNPROVEN entries explicitly identify incomplete evidence rather than imply a defect.

## 2. Immutable scope and binding inputs

Review checkout:

/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-package-marketplace

| Item | Verified value |
| --- | --- |
| Feature start, B | c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d |
| Candidate, C | 38c702203abbe213049eea4731750133508c0960 |
| Candidate tree | 642e9cb29e1e1a7cdc717df3e849be621f4cdf5a |
| Branch | feat/workflow-package-marketplace |
| Merge base of B and HEAD | B |
| Range | B..C |
| Commits / paths | 131 / 219 |
| Diff statistics | 184,919 insertions; 774 deletions |
| Initial tracked status | Empty |
| Initial untracked status | Review prompt only |
| Range whitespace check | Exit 0 |
| Final pre-report tracked diff against HEAD | Empty; exit 0 |

All production file/line references below refer to **C**, not mutable base. The entire changed-path inventory was used to establish feature scope, including generic helpers and unchanged consumers. The review was not restricted to the candidate's final commits. It was not an exhaustive line-by-line proof of every generated fixture or every unrelated caller.

The required documents were read in the prescribed order. Their SHA-256 values matched before reliance and again at final verification:

| Artifact | SHA-256 |
| --- | --- |
| AGENTS.md | a19cb8c30fb0f9a73089b98c9fc20ce59759817b5564f0bb9e8110822799cfb4 |
| apps/desktop/AGENTS.md | 4300a2e1e636a71bcec9d4211010f749ab78719f8b9ee8544c74f1f000be8ced |
| apps/desktop/DESIGN.md | 390abf9aa4cf4418f542f91f782f48a1283621638c90bad5946c63382d10845c |
| 2026-09-03-workflow-package-marketplace-design.md | dd721b9f7b9370d8a5187d1b55c78658804c6706c5c9d3f2c2534ada411b7b6a |
| 2026-09-04-workflow-marketplace-lifecycle-recovery-amendment.md | 6d5f812ca87f0f65b86dc9a75171e3786c6b7575a5fca830181a58104466a203 |
| 2026-09-05-workflow-marketplace-http-version-compatibility.md | b14c9b28f1c092c63b61449e67b89f1445bccea739c920a28760ef7ef2f75a25 |
| 2026-09-05-workflow-marketplace-strict-wire-parity-amendment.md | 07830657fe35928a9135d7aca932b06ff6d6c6f13232ab960ba38afddf851fb5 |
| 2026-09-05-workflow-marketplace-supervisor-identity-binding-amendment.md | d85aae7d6e11710ef8e65a338a7f34afea8ba97ad190003115acdc6f1ca4857c |
| plans/2026-09-04-workflow-marketplace-lifecycle-recovery.md | cfbf33e209721716330abee0af5c9053753b4f37dfe864af92f54873d57df712 |

Also inspected the plan-linked approved inspection-digest-parity, transaction-workspace-isolation, and inspection-admission amendments dated September 6–7.

Authority conflicts were treated explicitly:

- Original design's unconditional restoration language does not override the recovery amendment's verified rollback / recovery-required / unknown distinctions.
- Strict-wire amendment's earlier capability field list is superseded by the identity-binding amendment adding principal binding.
- Inspection root distribution digest and per-workflow effective trust digest are intentionally different domains under the digest-parity amendment.
- Marketplace scratch roots are now under marketplace/workflows, not RunStore's workflows scratch roots. The workspace amendment expressly excludes hostile same-account replacement *inside* an unobservable syscall interval; no finding assumes that excluded protection.
- The September 7 asymmetric inspection admission rule supersedes blanket same-package exclusivity.
- There is an unresolved wording conflict between locked invariant 3's categorical prohibition on public “local paths” and strict-wire amendment acceptance matrix line 254 explicitly requiring Python-valid file repository identities to survive public GET/cancel validation. I did not turn the approved file-URL behavior into a credential-disclosure finding.

## 3. Findings

| ID | Severity | Finding |
| --- | --- | --- |
| CWM-001 | IMPORTANT | Registered-source update checks certify an obsolete cache as current and stop Desktop update preparation |
| CWM-002 | IMPORTANT | Browser release proof accepts and executes a wrong-version, wrong-bin Playwright identity |
| CWM-003 | MINOR | Valid long package/publisher names overflow and are clipped inside the list/detail panes |
| CWM-004 | MINOR | Review warnings expose raw diagnostic codes and English messages in Arabic UI |

### CWM-001 — IMPORTANT: cached “current” prevents a real available update

1. **Identity/severity:** CWM-001, Important. This is false update-availability truth in a core lifecycle path, not an installed-byte corruption claim.
2. **Title:** Registered-source checks use obsolete discovery metadata as authoritative current-version evidence.
3. **Immutable production location and callers:** plugins/workflow/marketplace/service.py:1970 and :1985; catalog.py:352 reads existing projections only. lifecycle_api.py:616 dispatches the check. Desktop use-package-lifecycle.tsx:292 starts update_check; :615 and :623 wire both Update package and Check for updates to that same path. package-lifecycle-presentation.ts:108–112 turns current into definitive copy and disables preparation. The advance-to-preparation effect around use-package-lifecycle.tsx:388 accepts only update_available. CLI caller: marketplace/cli.py:1465. These marketplace paths are new in the feature; there is no pre-feature marketplace caller to blame. The existing shared Button and Dialog primitives simply execute/render their supplied handlers/content.
4. **Contract:** Locked invariant 4 forbids making stale discovery data falsely authoritative. Recovery amendment terminal update-check semantics and cache-barrier section distinguish current from error/update_available; original design lines 250–252 allow retained cache for browsing, not a fresh-candidate guarantee. User-guide Updates and removal presents check/update without a prerequisite source refresh.
5. **Realistic trigger/path:**
   - Register company at a moving main branch containing support 1.0.0; refresh once.
   - Prepare and confirm company/support 1.0.0.
   - Publisher commits a valid 2.0.0 package, digest document, and index to main.
   - Open fresh package detail. service.inspect fetches current bytes and reports candidate 2.0.0/update_available.
   - Choose Update package. Its update_check takes the registered-source branch and reads the old cached index; it performs no Git fetch and does not refresh that index.
   - The successful, strictly valid result says current, with installed and candidate versions both 1.0.0. Desktop stops instead of preparing the update.
6. **Wrong observable result/consequence:** The terminal copy is “Version 1.0.0 is current. No update was installed.” A newer valid candidate actually exists and has just been inspected. Repeating Update or Check repeats the result until the operator separately refreshes the source. Refresh state only reads local package state; it does not repair this discovery-cache problem. No wrong package bytes were installed in the probe.
7. **Bounded evidence:** Disposable real-Git Python probe, through scripts/run_tests.sh with retries zero, observed:

   ~~~json
   {
     "installed": "1.0.0",
     "inspection_version": "2.0.0",
     "inspection_status": "update_available",
     "check_status": "current",
     "check_candidate": "1.0.0",
     "fresh_prepare_candidate": "2.0.0"
   }
   ~~~

   The probe used the actual WorkflowMarketplaceService, _write_package/_write_index fixture publishers, real commits, and a temporary profile home. The only differing observation mechanism was cached check versus actual fresh inspection/preparation. No mocked service result supplied the contradictory versions. The UI consequence is a complete production-code trace, not a claimed screenshot of that dialog.
8. **Feature-start causality:** git ls-tree B for service.py and this marketplace UI returned no entries. Both the registered check branch and the two Desktop buttons selecting it were introduced in B..C. The direct-source branch in C, service.py:1915–1968, already refetches and does not have this particular defect.
9. **Why the gate misses it:** The moving-branch check test at test_marketplace_service.py:1347 exercises direct installation and correctly proves that branch refetches. It does not establish the registered-source equivalent without an explicit refresh. The three browser scenarios do not publish a new version and then click the registered-source Update button against an old catalog. Strict wire validation cannot detect a semantically stale but structurally valid current result.
10. **Smallest safe correction/regression:** Have the registered check establish current candidate truth through the same bounded, exact-source/ref fetch and validation used by update preparation, rather than certify cached search metadata. On fetch failure, return error rather than current. Add the exact publisher-moves-main sequence above to service/API tests and a Desktop test proving Update reaches a fresh review without an unrelated manual source refresh. Retain cancellation and per-operation budgets.

### CWM-002 — IMPORTANT: unproven Playwright can satisfy the browser gate

1. **Identity/severity:** CWM-002, Important. The release gate can claim browser proof without running the proven browser tool. This is not a claim that the installed Playwright on this host was malicious.
2. **Title:** local_playwright checks name and containment but not the locked version or declared executable.
3. **Immutable production location and callers:** scripts/workflow_gate_build.py:132–153, particularly :150–153. Its main at :232 chooses the returned path and :242–249 executes it; scripts/test_workflow_merge_gate.sh:595 starts that helper and its exit handling emits the receipt at :386. The existing ManagedProcessTree machinery owns the process but cannot establish the identity or work performed by a zero-exit script.
4. **Contract:** Locked invariant 24 requires proven local Node/TypeScript/Vitest/tsx/Playwright identities and receipt suppression when proof fails. Attack campaign F explicitly requires wrong-version and executable-identity fixtures.
5. **Realistic trigger/path:** A reused local dependency tree has a stale, incorrectly restored, or substituted @playwright/test package. Its manifest still has the expected package name, but version/bin do not match the pinned installation. local_playwright accepts its contained regular cli.js. Build completion is followed by executing that file; zero exit is sufficient for the helper's success. There is no independent browser-result check later in the gate.
6. **Wrong observable result/consequence:** The purported browser-proof step can execute a different local CLI and perform no browser scenarios. A clean source checkout plus green preceding checks can then reach TESTED_BASE_SHA without the claimed browser evidence.
7. **Bounded evidence:** In a private synthetic node_modules tree, not the repository's dependencies:

   ~~~json
   {"name":"@playwright/test","version":"0.0.0-review","bin":"elsewhere.js"}
   ~~~

   cli.js contained only:

   ~~~javascript
   process.stdout.write('WRONG_PLAYWRIGHT_EXECUTED');
   ~~~

   Calling the production local_playwright accepted it. The actual already-validated Node runtime then executed the returned path with the production browser arguments:

   ~~~text
   test e2e/workflow-marketplace-lifecycle.spec.ts e2e/workflow-marketplace-layout.spec.ts
   exit_code: 0
   stdout: WRONG_PLAYWRIGHT_EXECUTED
   ~~~

   Thus acceptance and execution are reproduced. The full forged gate was not run: the receipt consequence follows from the inspected helper/caller success path. The real gate used genuine Playwright 1.62.1 and really ran three scenarios.
8. **Feature-start causality:** scripts/workflow_gate_build.py does not exist at B. Its identity admission and receipt-bearing browser proof are candidate feature additions. This is not inherited package-manager behavior.
9. **Why the gate misses it:** test_workflow_gate_build.py:340–370 intentionally accepts a manifest containing only the name as the positive identity case. It covers malformed/name/link/file-kind cases, but not the missing version/bin proof. scripts/workflow_gate_clis.py has stronger pinned checks for TypeScript/Vitest/tsx; those checks do not validate Playwright.
10. **Smallest safe correction/regression:** Validate Playwright against the committed dependency identity, including exact version and expected bin mapping, strict duplicate-free manifest parsing, and contained regular executable files before work. Keep the resolved executable identity through launch. Add missing/wrong version, missing/wrong bin, duplicate manifest keys, and zero-exit canary cases. Assert rejection before browser launch and before any SHA receipt. Do not add an installer or package-runner fallback.

### CWM-003 — MINOR: package identity metadata is clipped

1. **Identity/severity:** CWM-003, Minor. Reproducible bounded readability/reflow defect; no hidden destructive action or authority breach is inferred.
2. **Title:** Long valid display and publisher values escape their text cells.
3. **Immutable production location and callers:** apps/desktop/src/app/workflows/marketplace/package-detail.tsx:139, :205; package-list.tsx:103–104 renders the publisher through Badge. Marketplace index.tsx:555 invokes the detail component inside its scroll container. Existing unchanged Badge/RowButton primitives do not provide wrapping for arbitrary long content; their callers must bound it. The new package detail heading and publisher cell omit the wrapping applied to repository/path cells nearby.
4. **Contract:** Locked invariant 23 and recovery amendment §9 require readable supported narrow layouts and 200% reflow. Review campaign E explicitly includes long package/source/workflow names and prohibits clipped content. This is an observable result, not a preference for a CSS class.
5. **Realistic trigger/path:** A publisher uses a legal unbroken automation-package or organization identifier. The probe set displayName to OperationalAutomationPackageIdentifier repeated three times and publisher to CorporateAutomationEngineeringDivision repeated three times. Both are 111 characters and passed the actual manifest/index/service validation. Select that package from a real refreshed catalog.
6. **Wrong observable result/consequence:** List publisher content crosses the row's content width; the detail title and publisher require horizontal scrolling/clipping instead of readable reflow. The full identity cannot be read at a glance even in the wide split view. The narrow view also clips it. Root/body overflow metrics remain green because clipping occurs within scroll containers.
7. **Rendered evidence:**
   - Exact primary coordinate: 320 × 900 CSS px, 100% zoom, English/LTR, built-in default OTTO appearance in light mode, verified not-installed package detail, pointer selection.
   - Also reproduced at 768 and 1440 CSS px, light/dark, and Arabic/RTL. The completed probes also exercised 200% zoom with the real app zoom IPC, but the 100% captures are the clearest raster evidence for this finding.
   - At 320 CSS px: detail h2 clientWidth 244, scrollWidth 1001; publisher dd clientWidth 136, scrollWidth 699. Root/body width both 320.
   - At 1440 CSS px: detail region/heading width 602 against 1001 content pixels; publisher cell 494 against 699; list option 410 against 654.
   - Screenshot: /tmp/hermes-marketplace-codex-review.wMaN1c/ui-en-320-100-light.png.
   - SHA-256: 752bf1fef0e26dc1596335098cb512ec05fe3bf283aa00535554c42a9f0a8c3a.
   - Arabic wide screenshot: /tmp/hermes-marketplace-codex-review.wMaN1c/ui-ar-1440-100-light.png; SHA-256 c582c19d7a3d2a245f8aa1cd4606273b29c25946a2e321542221d464e45510a1.
   - Screenshots were viewed, then deleted with the disposable evidence directory as required. Measurements and reproduction inputs are preserved here.
8. **Feature-start causality:** Both marketplace components are absent at B. Shared Badge/RowButton styling was not changed by this feature; the new unbounded text placements introduce the defect.
9. **Why the gate misses it:** Existing rendered layout fixture uses short Laptop Support/example-company values. Its document/body width checks cannot detect this contained overflow. The independent probe demonstrated why a green page-level check is insufficient.
10. **Smallest safe correction/regression:** Bound and wrap unbroken metadata within its cell, with a deliberate accessible truncation/reveal policy where a list badge should stay compact. Do not “fix” this by hiding more overflow. Add legal maximum/long unbroken names to rendered list/detail tests at 320/768/1440, LTR/RTL, 100%/200%; assert cell/content readability as well as root dimensions.

### CWM-004 — MINOR: diagnostic presentation bypasses localization

1. **Identity/severity:** CWM-004, Minor. Localized review affordances work, but warning comprehension is unnecessarily English/protocol-dependent.
2. **Title:** Raw codes and backend English remain the primary review warning text.
3. **Immutable production location and callers:** apps/desktop/src/app/workflows/marketplace/review-sections.tsx:60–63 prints item.code in a Badge and item.message verbatim. install-review-dialog.tsx:211–212 supplies the review diagnostics; WorkflowRiskReview at review-sections.tsx:166 repeats the pattern for compatibility. Package detail uses raw messages at package-detail.tsx:283 and :295. Backend service.py:1575–1576 constructs missing-runtime/tool/secret messages in English. The unchanged locale provider selects Arabic correctly; the unchanged Badge merely renders supplied text.
4. **Contract:** Locked invariant 22 and campaign E.5/E.8 require localized, user-language actions/reviews and no raw enum/error-code presentation requiring operator interpretation. The finding concerns controlled Hermes diagnostics, not publisher-authored names or descriptions.
5. **Realistic trigger/path:** Select Arabic in the backend display configuration, open a valid workflow package with a normal legacy-language advisory and unmet declared requirements, then keyboard-open installation review. The actual service generates diagnostics; strict decoding accepts them; the UI interpolates code/message directly instead of using the locale.
6. **Wrong observable result/consequence:** Arabic headings surround warnings such as “missing_runtime destination does not currently provide runtime 'uv'” and “legacy_language_profile workflow uses permissive Hermes legacy language semantics.” An Arabic-reading operator must interpret English and internal diagnostic identifiers to understand the review. Unknown/missing capability availability itself is not adjudicated by this finding.
7. **Rendered/semantic evidence:**
   - Detail: 1440 × 900 CSS px, 100%, actual ar locale and RTL, default OTTO light appearance, verified candidate, pointer-opened detail. Screenshot/hash are the Arabic wide artifact in CWM-003.
   - Review: 320 × 450 CSS px, 200%, ar/RTL, system mode resolving dark, reduced motion, installation preparation completed, keyboard Enter on Install package.
   - /tmp/hermes-marketplace-codex-review.wMaN1c/dialog-ar-aria.txt, SHA-256 ad8c19fec6461c379f2a2c9061505fb828a6f26435e1795ab7849dc6d8591535.
   - Actual accessibility snapshot excerpt:

   ~~~text
   dialog "مراجعة التثبيت"
     region "إرشادات"
       listitem: legacy_language_profile workflow uses permissive Hermes legacy language semantics
       listitem: missing_runtime destination does not currently provide runtime 'uv'
       listitem: missing_secret destination does not currently provide secret 'SUPPORT_TOKEN'
       listitem: missing_tool destination does not currently provide tool 'git'
   ~~~

   The snapshot and screenshots were temporary and removed after inspection. The corresponding localized headings, buttons, change labels, and document direction were present; this was not an English-locale test with only dir changed.
8. **Feature-start causality:** Marketplace review-sections.tsx, package-detail.tsx, and service.py are new at C relative to B. Some backend language diagnostics predate the feature, but the new marketplace's primary review presentation directly exposing them is the candidate defect. Existing untranslated ordinary Workflow-shell labels are separately excluded below.
9. **Why the gate misses it:** Controlled-copy coverage verifies phase/change/severity mappings and catalogs, not the diagnostics rendered verbatim here. The existing layout browser scenario changes document direction while retaining English copy. The independent actual-Arabic fixture exposes this gap.
10. **Smallest safe correction/regression:** Present supported diagnostic meanings through all five locale catalogs, using safe structured parameters rather than parsing English sentences. Use an honest localized generic explanation for unsupported diagnostic shapes; raw codes can remain optional technical detail rather than the primary warning. Add Arabic/Japanese/Chinese review and detail assertions driven by backend-generated diagnostics, preserving identifiers/requirements without translating protocol values.

## 4. Locked-invariant ratings

| # | Result | Direct evidence / qualification |
| --- | --- | --- |
| 1 | PASS | Strict models forbid extras/type coercion; duplicate-key parser, membership and digest checks in package.py:833–999; contract/vector generation check and package/contract suites passed. |
| 2 | PASS | Exact source_key/package_id and canonical-home ownership traced through stores, API, binding and query keys; real two-profile/provenance tests passed. Search casefolding is not installation identity. |
| 3 | UNPROVEN | Token-free operation/public-subject paths and credential rejection/redaction tests support the bounded cases. File repository identity conflicts with the invariant's categorical local-path wording; no universal leakage assertion is made. |
| 4 | FAIL | Atomic cache replacement/last-good retention are supported, but CWM-001 promotes obsolete cache metadata to definitive current update truth. |
| 5 | UNPROVEN | Descriptor file reads, whole-distribution membership, exact commit/ref checks, executable-closure trust binding and real hostile-package tests passed on macOS. Native Windows reparse/handle behavior and all replacement permutations were not independently proved. |
| 6 | UNPROVEN | atomic_install/remove, rollback journals and recovery paths were traced; 442 transaction tests passed. Exhaustive real crash/power-loss/SQLite cuts on all supported filesystems were not reproduced independently. |
| 7 | PASS | effective_marketplace_digest binds distribution/path/closure; trust adapter compares complete inventory and exact trust map, then requires selected workflow(s) trusted. Real integration separates installation and grant. |
| 8 | PASS | V2 discriminated subjects constructed from validated inputs; direct selector uses opaque binding; private registry target is not copied into public subjects. Strict subject/API tests passed. |
| 9 | PASS | Receipt replay precedes authorization/capacity/conflict; actor/home/body binding and bounded request window traced. Lost-response browser scenario and admission conflict/replay suites passed. |
| 10 | PASS | Registry publication and exact API get/cancel validate IDs; codecs/supervisor correlate request/kind/subject/profile/result rather than latest operation. Generated parity and mismatch tests passed. |
| 11 | PASS | Desktop outcome/version/trust folds use backend outcomes and locked state rather than kind/timestamp guesses. CWM-001 is a wrong backend check fact, not a renderer-derived installed version. |
| 12 | PASS | Separate admin review-token endpoint; hook-local refs cleared on close/unmount; confirmation body copied to bounded private replay closure then cleared; history/query projections omit token. |
| 13 | PASS | Application QueryClient-lifetime supervisor, not view-owned polling; exact-operation recovery survived close/tab return in real Electron gate. Binding-keyed adapters detach on scope change. |
| 14 | PASS | Main route lease and generation checked before resolve, after resolve/OAuth await, and finally after response/error; renderer five-field binding checks and deterministic race tests passed. Live remote/auth sessions were not used. |
| 15 | PASS | Main-lifetime exhausted flag clears claims/routes and never resets; singleton main.ts:1458 broadcasts exhausted. Exhaustion/queued-generation tests passed. |
| 16 | UNPROVEN | Admission deadlines, scheduler bounds, ref cleanup and finite guards inspected; tested terminal/conflict/navigation paths pass. Exhaustive timer/listener/resource behavior under long-lived arbitrary scope churn remains unproved. |
| 17 | PASS | lifecycle_state.py records entry/verified rollback/recovery evidence under lock; presentation distinguishes committed, unchanged, cancelled, recovery and unknown. Invalid/evicted data is not synthesized as rollback. |
| 18 | PASS | Reconciliation generations and projection receipts fence pre-barrier queries; failed refetch remains non-actionable in supervisor/UI tests and lost-response browser scenario. |
| 19 | PASS | Unconfirmed/busy/recovery locked state prevents contradictory actions; fresh exact local state and required projection evidence reopen controls. No definitive version is supplied by null state in inspected folds. |
| 20 | PASS | Explicit unsupported + no previous V2 binding + post-transition exact catalog evidence required. Fetch/manual-cache/route/principal changes revoke the legacy attempt. Contract/UI tests passed. |
| 21 | PASS | Lifecycle Escape is claimed before parent navigation; safe Close focus and origin restoration exercised by gate and independent en/ar keyboard review probes. Not every disappearance/failure focus path was rendered independently. |
| 22 | FAIL | CWM-004. Capability gates and truthful terminal action folds otherwise supported; all five lifecycle label catalogs contain actual translations. |
| 23 | FAIL | CWM-003. Root width can be correct while important metadata clips within panes. Additional state/skin/assistive-technology limits are listed below. |
| 24 | FAIL | CWM-002. Genuine local gate/build/browser passed, but tool identity admission is insufficient to substantiate the general release claim. |
| 25 | UNPROVEN | Marketplace transaction marker/phase checks and disjoint RunStore roots are supported on this host. Generic Git scratch path cleanup and every native replacement/quiescence case were not proved to the categorical invariant. |
| 26 | PASS | V1 reads remain explicitly compatible; retired V1 mutations do not regain V2 authority. Catalog/run and HTTP eligibility suites passed, with post-transition renderer read fencing. |
| 27 | PASS | Required installed-distribution integration executed from temporary installed files; package metadata/contract checks passed. This is not native installer/publication validation. |
| 28 | UNPROVEN | Gate covers important Workflow/catalog/Git/process/transport regressions, and generic callers were inventoried. Full unrelated application behavior, native platforms, shutdown permutations and current-base integration were not exhaustively validated. |

## 5. Backend package/source/Git/trust/transaction matrix

| Surface / attack | Evidence and assessment |
| --- | --- |
| Strict manifests/index/digests | Real package and contract suites passed. Reader authenticates all included files, not just manifest workflow members; wrong digest, extra/missing coverage, duplicate JSON and unsupported structure are rejected. |
| Paths, Unicode, case, links | Canonical relative-path/domain checks, casefold collision handling and descriptor-anchored reads inspected. File identity is compared before/open/after read. Native Windows runtime remains UNPROVEN. |
| URL/source authority | Credential-free identity validation, shared sanitizer, canonical source store, ref/path validation and origin scrubbing traced. Synthetic file repositories only; no live authentication attempt. |
| Refresh publication | Source snapshot and replacement are lock-scoped; publication rechecks configured source identity. Failed refresh preserves last verified catalog. CWM-001 concerns downstream use of that cache, not partial cache replacement. |
| Git fetch | Bounded output/time/storage, sparse path preflight, exact revision verification and limited partial-clone fallback inspected. No submodule execution or package script execution was observed. |
| Install | Real preparation/confirmation binds digest/identity/commit; explicit confirm required. Candidate swap and provenance write have journals and rollback paths. Real browser install completed only after confirm. |
| Update | Prepare refetches and rejects version regression/same-version changed bytes. Registered update check is defective per CWM-001. |
| Remove | Uses installed provenance/verified bytes, backup move, provenance removal, trust revocation and retirement phases. Real browser removes while earlier read-only inspection remains pending. |
| Trust all / one | Separate grant; exact effective/risk digests; complete map remains distinct from selected grant. Unknown/missing/duplicate workflow-map rejection supported by codec and adapter tests. |
| Independent grants | Revoke-origin does not imply globally untrusted; current complete trust state is used. Existing real integration explicitly exercises another profile and foreign/manual grants. |
| Transaction workspace | New private marketplace roots do not overlap RunStore roots. Markers, journal paths and phase authority are checked; foreign artifacts are not guessed into valid transaction evidence. |
| Recovery cuts | Install/remove rollback and retire-pending logic inspected; recovery distinguishes candidate committed evidence from prior-byte restoration. Passing injected cuts are not a claim of exhaustive power-loss testing. |
| Discovery/run integration | Provenance-backed binding is required for installed marketplace trust; copied bytes in another home/project do not borrow that installation binding. Existing loose workflows remain a separate discovery case. |
| Installed distribution | Required packaged-filesystem proof and installed metadata checks passed. The local test extraction/build was part of the prescribed gate, not publication or an installation into the user's active application. |

## 6. Lifecycle admission/outcome/recovery matrix

| Sequence | Observed/inspected truth | Remaining limit |
| --- | --- | --- |
| Prepare → review | Successful read-only preparation yields immutable review facts; token retrieved separately after exact correlation. | Every maximum-sized review was not rendered. |
| Confirm POST → response lost | Exact request receipt lookup recovers original operation; no new mutation ID is automatically invented. | Real gate exercises one confirmation-loss sequence, not every transport cut. |
| Replay with changed body/kind/subject | Receipt requires same content-bound request and rejects conflict before duplicate work. | No defect reproduced. |
| Confirm token invalid/expired/foreign | Authorize before new admission; exact replay checked first. | No token was logged in independent evidence. |
| Pending/running cancellation | Cancellation request is not terminal proof; atomic entry cannot be erased by later cancellation. | Not every cancel timing rendered independently. |
| Successful read/check/prepare | known_unchanged/read_only; meaningful result is still inspected. | CWM-001 shows valid schema alone cannot prove check freshness. |
| Successful install/update | Committed plus confirmed locked state; installation does not grant trust. | Not a statement that historical success is current after later mutation. |
| Successful remove | Committed absence; stale rows cannot supply Remove/Update/Trust authority. | Package disappearance focus paths mainly component/gate evidence. |
| Verified rollback | Prior distribution/provenance must match locked state before “remains installed” copy. Actual trust map is retained separately. | Real crash exhaustiveness UNPROVEN. |
| Rollback failed / ambiguous ownership | recovery_required, not “restored.” | Native filesystem recovery limits above. |
| Invalid terminal / missing status / eviction | Unknown/tombstone and retained reconciliation barrier, not guessed success or rollback. | Long-lived churn resource bound not independently established. |
| Trust-one grant returns full package | Full inventory/path/digest/trust map checked; selected workflow must be present and trusted. | No defect reproduced. |
| Inspection before mutation | Observation does not own mutation exclusivity; its old generation cannot refresh newer action truth. | Real gate overlaps inspection/removal. |
| Mutation before inspection | Known conflict/non-admission; no automatic queue or invented uncertain mutation. | Cross-process worst-case scheduling not exhaustive. |
| Backend restart | New epoch rejects old replay authority; durable package state/journals govern recovery. | Separate native remote backend restart was not performed. |

## 7. Electron, supervisor, cache, and navigation matrix

| Boundary | Assessment |
| --- | --- |
| Native descriptor allocation | Monotonic safe-integer generation; invalidated scope gets a new claim; exhausted main lifetime cannot roll over. |
| Registry/shared-primary/dedicated routes | routeKey plus native lease captured before awaits. Registry/legacy configuration publication retires affected authority. Main callers at :11438 and :16483 use the new boundary. |
| OAuth/token/session changes | Invalidation precedes replacement session publication; final response/error is rechecked. Actual live OAuth/cookie/SSH sessions deliberately not contacted. |
| Header ownership | Main strips saved reserved-header collisions and injects only its validated expected principal header; backend raw singleton binding check is separate from native generation. |
| URL and redirect seams | Native lifecycle namespace/path normalization guards inspected; generic and structured collectors preserve adapter timeouts and lifecycle restrictions. |
| Renderer binding | Exact connection ID, native generation, profile, principal and registry epoch; old attempt cannot publish into new binding. |
| Supervisor lifetime | Memory-only store initialized above Workflows views; dialog closure removes attachment/token, not backend work. Real navigation-return proof passed. |
| Watch correlation | Exact request and operation IDs, kind, phase, subject and result shape; no recency adoption. |
| Terminal cache barrier | Advance/cancel/invalidate before mutation and on terminal uncertainty; old query receipts cannot satisfy new generation. |
| Locked package state | Busy/recovery/unconfirmed fences actions even when the UI retains earlier metadata. |
| Local-only recovery | Failed remote detail does not itself destroy local Remove eligibility when exact local state is sufficient. |
| Legacy mode | Explicit unsupported receipt plus no prior V2 and a fresh exact catalog fetch; manual cache insertion is not a fetch receipt. |
| Strict Mode and cleanup | Provider/adapter setup-cleanup designed to preserve supervisor ownership and clear view references. Component tests passed; arbitrary lifetime churn remains UNPROVEN. |
| Disconnect/profile return | Old transport polling is suspended, secrets detached, origin barriers retained. Deterministic tests support exact rebind; no live multi-host experiment was performed. |

## 8. UI state → copy/action/focus matrix

“Rendered” means actual Electron execution; “component/source” is deliberately not represented as native browser proof.

| State | Authoritative fact and visible copy | Actions / navigation / focus | Evidence |
| --- | --- | --- | --- |
| Initial load / scope probe | No current authority yet; loading/refreshing state | Package actions fenced; no implied installed version | Component/source |
| Empty search | Exact empty result; empty-state text | Search/clear/source controls remain available | Independent en/ar rendered probe |
| Maximum displayed page | 50 actual package options | Roving selection and scrollable list; selected details open | Independent en/ar render; not maximum whole repository |
| Retained search metadata | “Last observed search metadata” | Browsing allowed; cannot authorize a candidate by itself | Rendered; CWM-001 is separate check bug |
| Fresh absent package | “Package is absent.” / Arabic equivalent | Install becomes available after required fresh evidence | Independent render |
| Fresh installed package | Current locked version/trust | Update/check, Remove, and separately gated Trust | Real gate/component/source |
| Update check current | Backend check result, “Version X is current. No update was installed.” | No auto preparation | CWM-001 code path + real service counterexample |
| Update available | Backend candidate status | Advances to new preparation only through exact watch | Component/source |
| Install review ready | Candidate commit/digest/requirements/risks | Safe Close focus; explicit Confirm; Escape closes one layer | Independent keyboard en/ar; gate |
| Confirmation admission | Admission is unresolved, not installed | Finite admission lock; close/outside/Escape cannot bypass it | Component/source; gate confirmation |
| Running | Phase/progress, not success | Close detaches; Cancel requests cancellation; navigation preserves watch | Real gate plus component/source |
| Committed install/update | Exact terminal version; no implicit trust | Fresh local/projection reconciliation required for next action | Gate install; update component/source |
| Committed removal | “Package removal completed.” | Stale group not authoritative; surviving focus fallback | Gate removal + component/source |
| Trust review/grant | Per-workflow actual trust; selected action separate from full map | Explicit grant; all/one selection; independent capability check | Gate separate trust plus component/source |
| Cancelled before commit | Explicit cancelled-before-commit outcome | Refresh/prepare only after normal readiness; no stale-version assertion | Component/source |
| Known unchanged / verified rollback | No-write or verified prior state; version only with matching proof | Guards released; new preparation possible | Component/source + Python faults |
| Recovery required | “State could not be confirmed. Package recovery is required.” | Contradictory actions fenced; read state and backend/profile recovery guidance | Component/source; not independently rendered |
| Lost admission response | “State could not be confirmed. The operation may have completed.” | Exact lookup/retry, not blind duplicate confirm | Real gate |
| Invalid terminal / evicted | Unknown, not failure-as-rollback | Retain barrier until exact current-state reconciliation | Component/source |
| Failed state/cache refresh | Last observed/uncertain; no definitive fresh installed claim | Retry/Refresh state; affected actions remain disabled | Real gate failed-state sequence |
| Offline/disconnected | No usable binding | No cross-scope actions or token; backend work can continue | Component/source; native offline visual UNPROVEN |
| Explicit unsupported | Upgrade/read-only compatibility copy | No package mutation; catalog actions only after approved fresh transition | Component/source |
| Package disappears / scope changes | Old metadata is not current state | Detach dialog/token; same-scope surviving action/search/heading fallback | Gate removal/navigation; exhaustive focus paths UNPROVEN |

## 9. Visual design, styling, accessibility, and localization

### Executed presentation matrix

The disposable renderer probe reused scripts/workflow_gate_build.py's exact-commit isolated build, and apps/desktop/e2e/fixtures.ts's synthetic backend/home. It injected only a temporary extra test into the private build checkout. No production/UI file was changed.

| Axis | Actual exercise |
| --- | --- |
| Widths | 320, 768, 1440 CSS px, verified with innerWidth |
| Zoom | 100% and 200%; final expanded run used the real hermesDesktop.zoom.setPercent IPC |
| Height | 900 CSS px at 100%; 450 CSS px at 200% |
| Locale/direction | Actual backend display.language en and ar, with document lang/dir observed; not dir-only simulation |
| Appearance | Default built-in OTTO skin in light and dark modes; system mode switched to each resolved appearance |
| Motion | Normal at 100%; prefers-reduced-motion at 200%; existing gate also asserts dialog animation suppression |
| Content | One-item catalog in the first successful run; 50-item displayed page in expanded run; legal long metadata; actual multi-line diagnostics; empty search |
| Input | Pointer selection; keyboard Enter opens installation preparation; Escape closes once and restores Install focus; gate tests deeper Tab order and viewport reachability |
| Semantics | Actual Playwright accessibility snapshots for Arabic/English detail, review and empty state |
| Result | Two expanded native browser tests passed in 35.8 seconds; observations revealed CWM-003/004, not an assertion of visual cleanliness |

The 200% Electron screenshot rasters were visibly cropped/scaled differently from the CSS geometry. I did not use those raster edges to allege hidden actions or a keyboard trap. The 100% screenshots, measured element overflow, 200% DOM assertions, and keyboard/focus results are distinguished rather than conflated.

### Product-quality assessment

- The flat list/detail hierarchy, metadata headings, provenance sections, and shared Button/Badge typography are generally recognizable as the surrounding Workflows application.
- Candidate and installed identities are explicitly separated. Digests and exact commits are reviewable instead of replaced by friendly version labels.
- Install/update, removal confirmation, and trust are separate decisions. Removal has an explicit reviewed scope. Closing progress does not claim cancellation.
- Safe Close initial focus, one-Escape ownership, and focus return passed exercised review paths. The shared long dialog scroll mechanism keeps keyboard-focused controls brought into view.
- Warnings can dominate a dense technical review. Density alone is not a finding. CWM-004 is the objective problem: controlled warning content remains untranslated and includes protocol codes.
- At 320 CSS px the header and back/detail pattern reflow; no document-level horizontal overflow was measured. CWM-003 shows why that does not establish readable contents.
- No contrast-ratio compliance claim is made from visual inspection alone. Focus was observable in exercised flows; VoiceOver/NVDA announcement timing, duplicate live announcements under every failure transition, and measured contrast across all skins remain UNPROVEN.
- All five catalogs contain meaningful translated lifecycle phases/change/severity/outcome labels. Japanese, Simplified Chinese and Traditional Chinese long-form native rendering was not performed.
- Every built-in alternate skin and arbitrary user-installed theme was not rendered. The executed appearance claim is light/dark/system for the default skin, not universal theme validation.
- Missing-package, total-error, explicit-unsupported, offline, recovery-required and unknown-outcome *visual variants* were not independently rendered across the width/zoom/locale matrix. Component/source coverage is not substituted for that missing evidence.
- No feature request or aesthetic preference is included in the findings.

## 10. Release, offline, and process ownership

The prescribed base gate completed with:

~~~text
TESTED_BASE_SHA=38c702203abbe213049eea4731750133508c0960
~~~

The gate's generators/checkers, Python inventory, installed-filesystem integration, TypeScript check, Vitest suites, isolated Desktop build and three browser scenarios completed. No fast mode or per-file automatic retry was used.

The review performed no network lookup, dependency download, live-service/model call, publication, or installation into the active development/application environment. The prescribed gate includes its own temporary installed-distribution extraction/build proof. Browser fixtures use synthetic local loopback services and Git file-URL rewrites, as directed by the prompt; they do not contact the fixture HTTPS hostname.

Process observations:

- ProcessRunner owns launched trees via ManagedProcessTree, checks reaped/quiescent state before deleting its exact marker/inode-bound private build root, and propagates failure.
- ManagedProcessTree's changed POSIX path retains the setsid process-group identity when the leader exits before live capture.
- ProcessRegistry's changed systemd path no longer claims scope authority when late resolution fails, and its setup-failure cleanup separates scope teardown from direct wrapper reaping.
- Native Linux systemd and Windows job/reparse behavior were not exercised on this host.
- Required gate and all three independent UI build invocations exited; final process-table search found no matching review/build/E2E descendants.
- Own evidence root was verified as a private 0700 directory owned by coreyellis, explicitly removed, and absence checked.
- Standard test-runner duration/bytecode/tool caches are distinct from production source. The mandated runner reports refreshing its ignored test_durations.json cache and shared bytecode cache. They were not falsely represented as byte-for-byte unchanged, nor deleted as if pre-existing shared caches belonged exclusively to this review.
- CWM-002 blocks the broader claim that every accepted browser executable has proven local identity, despite the genuine run here.

## 11. Test integrity and unchanged consumers

Generated fixtures were treated as executable contract evidence, not accepted because of their insertion count. The generator's real temporary Git/service admissions, strict public shapes and digest-domain separation were examined; --check reproduced the committed artifacts. Desktop parity tests ran against generated data. No fixture was rewritten to fit a renderer assumption.

Specific load-bearing gaps demonstrated by findings:

| Gap | Demonstrated production consequence |
| --- | --- |
| Direct-source moving-ref check test does not cover registered cache check | CWM-001 |
| Playwright positive fixture permits name-only manifest | CWM-002 |
| Short strings plus root-only overflow assertion | CWM-003 |
| English with dir=rtl and controlled-label tests omit raw diagnostic body | CWM-004 |

Generic consumer inventory/assessment:

- Shared Git extraction is consumed by plugins_cmd, marketplace catalog/service/fetch/store/model/API paths and fixture generation. Plugin install/update wrappers were compared to B for resolver/error/exact-revision behavior; no additional qualifying plugin regression was proved.
- ManagedProcessTree consumers include Workflow bash/script executors, RunStore recovery, ProcessRegistry, local CLI handoff, and the build helper. The new immediate-leader-exit fallback is conditional on start_new_session. Their full arbitrary shutdown behavior is not asserted from one helper test.
- ProcessRegistry scope builder feeds both PTY and pipe fallback; its new optional return is checked by both callers.
- atomic_write_text's new no_follow path is opt-in; ordinary callers retain the prior branch. Existing uses across CLI/profile/config, workflow stores, gateway/tool code were inventoried; no blanket migration was assumed.
- Both generic and structured Electron API channels were traced through main's lifecycle interception and generic fallback. Structured channel tests verify additive behavior; generation validation does not constitute a rewrite of every non-marketplace endpoint.
- Query-key consumers include Marketplace, Installed, ordinary Workflow catalog/run controls, binding quarantine and reconciliation. The operation store is application-lifetime; query cache entries alone are not action authorization.
- Shared ConfirmDialog consumers include confirmation host, settings/connections, cron, bots, messaging, webhooks, profiles, skills, chat/session/project/file actions, Kanban, command center and source deletion. Its candidate change guards late completion after unmount/Strict Mode, rather than adopting lifecycle-review Escape behavior for all dialogs.
- Shared SearchField consumers include catalog/header, page-search shell, settings/provider/credential/keybind screens, overlays, chat/sidebar, command center and bots. Its optional searchbox role does not change default caller behavior.
- Badge, Button, RowButton and Dialog primitives relevant to the findings are unchanged in B..C; caller layout/content is the issue.
- Locale types/providers serve the entire application. Existing ordinary Workflow-shell English fallbacks seen in Arabic are not separately reported as a new marketplace diagnostic defect.
- Full generic consumer runtime coverage remains limited to the gate inventory and inspected paths. A file-count or test-name match alone was not treated as proof.

## 12. Adversarial probes and reproducibility

### Service counterexample

The disposable Python probe imports the actual service plus test fixture publishers, creates a real Git repository with local identity configuration, and performs:

~~~python
_write_package(repo, "support", version="1.0.0")
_write_index(repo)
# git init --initial-branch=main; configure synthetic identity; add; commit
service.add_source(WorkflowMarketplaceSource(
    name="company", repositoryUrl=repo.as_uri(), ref="main"
))
service.refresh_source("company")
review = service.prepare_install(
    InstallRequest(identifier="company/support"), actor="review"
)
installed = service.confirm_install(review.confirmation_token, actor="review")
_write_package(repo, "support", version="2.0.0")
_write_index(repo)
# git add; commit -- the same configured main branch advances
inspection = service.inspect("company/support")
check = service.check_updates(installed.identity)[0]
fresh_review = service.prepare_update(installed.identity, actor="review")
assert inspection.version == fresh_review.candidate_version == "2.0.0"
assert inspection.update_status == "update_available"
assert check.status == "current" and check.candidate_version == "1.0.0"
~~~

The service's home, publisher checkout, staging and trust data were all temporary. GIT_CONFIG_GLOBAL=/dev/null and GIT_CONFIG_NOSYSTEM=1 prevented borrowing user Git configuration. This is an observational counterexample test: a pass means the defect was reproduced.

### Tool identity counterexample

Create the manifest and cli.js shown in CWM-002 under temporary node_modules/@playwright/test. Call scripts.workflow_gate_build.local_playwright against that root, then run the returned file using the already-proven Node executable and the gate's two test-file arguments. It returns zero and the canary, not a browser test result.

### Rendered counterexample/probe

Reuse setupMockBackend({prepareHermesHome, extraDisplayConfig: "  language: ar"}) or en from the candidate E2E fixture. In prepareHermesHome, publish the normal service fixture package after setting the two legal long manifest values specified in CWM-003 and regenerating digests/index. Register/refresh company using a synthetic HTTPS identity rewritten through temporary Git configuration to the local file repository. For the expanded probe, publish 49 additional normal packages, producing the actual UI page limit of 50.

Navigate Workflows → Marketplace, select long-package, set each width/zoom/mode, record element clientWidth/scrollWidth and accessibility snapshots. Keyboard-open installation preparation, wait for Confirm install, Escape once and assert focus returns to Install package. Return to the 50-row list, search for a guaranteed no-match string, and verify zero options.

Temporary probe source hashes, recorded before cleanup:

| Probe | SHA-256 |
| --- | --- |
| test_independent_probes.py, final two-test version | e2c37129ce7cd3c50ff541cf907f5d73a5f0267b2c9b8a68fffa16867b037f44 |
| review-ui.spec.ts, expanded final version | 826561616f50e91a9a8146069260aad808b56753e4d0e7a90b1e87bfd07bc9c9 |

These disposable files and screenshots are intentionally no longer present. The inputs, paths, outcomes and relevant source locations needed to reconstruct the probes are documented here.

## 13. Verification command ledger

Working directory was the review checkout unless specified. B and C below stand for the full immutable SHAs in §2; the actual Git invocations used those full values.

| Command | Result |
| --- | --- |
| git status --short --branch | Expected feature branch; initially prompt only untracked |
| git status --porcelain --untracked-files=no | Empty initially and after tests |
| git branch --show-current | feat/workflow-package-marketplace |
| git rev-parse HEAD HEAD^{tree} | Exact C/tree, repeatedly verified |
| git merge-base c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d HEAD | B |
| git rev-list --count c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD | 131 |
| git diff --shortstat c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD | Exact expected statistics |
| git diff --name-only c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD | Full 219-path scope inventory |
| git diff --check c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD | Exit 0 |
| shasum -a 256 [the nine complete input paths in §2] | All nine exact hashes above; checked again before report |
| env -u WORKFLOW_MERGE_GATE_FAST HERMES_TEST_FILE_RETRIES=0 scripts/test_workflow_merge_gate.sh --phase base | Exit 0; exact TESTED_BASE_SHA=C |
| HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh /tmp/hermes-marketplace-codex-review.wMaN1c/test_independent_probes.py -s -q | First version: 2 passed, 0 failed, 2.0 s; final strengthened version: 2 passed, 0 failed, 1.5 s; retries zero |
| PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/hermes-marketplace-codex-review.wMaN1c/run_ui.py > /tmp/hermes-marketplace-codex-review.wMaN1c/ui-run.log 2>&1 | Exit 1: two defects in the disposable harness selectors, not candidate failures |
| Same run_ui.py command, output ui-run-2.log | Exit 0; 2 native browser probes passed, 28.3 s |
| Same run_ui.py command, output ui-run-3.log | Exit 0; expanded 50-row/system/keyboard/empty probe, 2 passed, 35.8 s |
| git ls-tree B -- [finding production paths] | No pre-feature marketplace service/UI/build helper entries |
| git diff B C -- [generic helpers/shared primitives] | Used for exact candidate versus baseline attribution; read-only |
| git diff --exit-code HEAD -- | Exit 0 before report |
| command -v node npm git; node --version; .venv/bin/python --version; git --version; sw_vers | Versions/host in §1 |
| node -p 'JSON.stringify(Object.fromEntries(["typescript","vitest","tsx","@playwright/test","electron"].map(p=>[p,require(p+"/package.json").version])))' | Exact installed local tool versions in §1 |
| ps -axo pid,ppid,pgid,command with rg '[/]hermes-workflow-gate-build-|[/]hermes-e2e-|[/]hermes-marketplace-codex-review\.' | No matches; rg exit 1 denotes none found |
| stat -f '%HT %Su %Lp %i' /tmp/hermes-marketplace-codex-review.wMaN1c | Directory, coreyellis, 700, inode 54761505 |
| rm -r /tmp/hermes-marketplace-codex-review.wMaN1c | Explicit validated disposable root removed |
| test ! -e /tmp/hermes-marketplace-codex-review.wMaN1c | Exit 0 |
| Final git status / rev-parse / tracked diff | See §16 |

The first UI harness incorrectly matched two nested regions and used the wrong Arabic tab string. Those were corrected only in the disposable probe. They are not product findings, and no test retry transformed them into a hidden green result.

Required gate evidence:

| Stage | Result |
| --- | --- |
| Generated contract/fixture checks | Passed |
| Authorized Python inventory | 106 files; 7,375 passed; 0 failed; 49 skipped; 238.4 s |
| Additional installed-distribution invocation | 1 file; 3 passed; 0 failed; 16.3 s |
| Canonical Workflow UI | 11 files; 229 passed; 6.77 s |
| Marketplace contract/UI | 19 files; 1,856 passed; 23.01 s |
| Electron project | 5 files; 111 passed |
| Structured-channel tsx tests | 2 passed |
| TypeScript no-emit check | Passed |
| Isolated Desktop build | Passed from C |
| Browser scenarios | 3 passed; 24.2 s |
| Receipt | TESTED_BASE_SHA=38c702203abbe213049eea4731750133508c0960 |

The two excluded suites were neither run nor intentionally collected. No direct pytest command was invoked by the reviewer; Python tests used scripts/run_tests.sh. No npx/install/download fallback was invoked by the reviewer.

## 14. Baseline attribution, warnings, and unavailable evidence

| Observation | Attribution / disposition |
| --- | --- |
| CWM-001–004 | Introduced feature paths absent at immutable B; specific causality documented above |
| Vite native configLoader warning for __dirname | Existing build configuration, not a demonstrated marketplace defect; successful build retained warning |
| advancedChunks deprecation, ineffective dynamic import, brand plugin timing | Build warnings, not product failures; no false claim of full build-warning cleanliness |
| Private build install-stamp says dirty | Brand generation deliberately changes the isolated build tree before stamping. Source checkout stayed tracked-clean. Not treated as candidate source mutation |
| NO_COLOR/FORCE_COLOR warning | Test process environment warning; no browser failure |
| Large generated/bundled assets | Not a finding by count/size alone; no quantified new marketplace performance regression established |
| Initial independent UI selector failures | Reviewer probe defects, not baseline or candidate defects |
| Ordinary Workflow-shell English in Arabic | Existing shared locale fallback/content, separate from new marketplace diagnostics; not charged as an additional candidate finding |
| Native Linux | UNPROVEN runtime; gate explicitly skipped linux_only cases on Darwin |
| Native Windows | UNPROVEN runtime; gate explicitly skipped windows_only cases on Darwin |
| Current-base integration | UNVERIFIED. No merge, simulated merge, branch/ref change or base reconciliation performed |
| Full-repository formatter/lint/checker | Not independently run beyond the required gate; no cleanliness or baseline-debt assertion |
| Live Git credentials, OAuth, SSH, remote profiles | Deliberately not exercised; synthetic local evidence only |
| Exhaustive installed desktop/native installer | UNPROVEN. Isolated build and installed-filesystem proof are not signing, installer or publication validation |
| Every state × locale × skin × width | UNPROVEN as a full Cartesian product; executed and unexecuted subsets are explicitly separated |

## 15. Unresolved questions and non-defects

Unresolved, **not findings**:

1. Clarify the scope of “local paths never public” relative to explicitly approved file repository identities. Do not infer a security fix that breaks the later wire amendment.
2. WorkflowMarketplaceApiContext's default service factory supplies none of the available_* capability sets; the default sets are empty, and rendered review therefore reports missing Git/runtime/secret requirements. Whether each declared tool name denotes an OS executable or a separate workflow capability inventory needs authoritative resolution before classifying availability truth. This review does not count it as an additional proved defect.
3. Git scratch cleanup at marketplace/git.py:100 is path-based. I did not establish a complete realistic foreign-replacement trigger outside the approved same-account threat exclusion with preserved ownership/quiescence evidence, so invariant 25 remains UNPROVEN rather than reporting speculative unsafe deletion.
4. Long-lived renderer scope/projection maps deserve a bounded churn proof. No realistic systemic exhaustion sequence was established here.
5. Full native assistive-technology announcements and measured multi-skin contrast need runtime evidence beyond DOM semantics/screenshots.

Not defects/feature requests:

- A persistent cross-application operation dashboard, Desktop direct-install form, source-authentication manager, automatic update background polling, and GUI transaction-recovery execution are not required additions to this release.
- Technical digests/commits in an explicit automation-package review are not inherently poor UX.
- Separate trust after installation is intentional, not redundant confirmation.
- A historical row remaining visible with an explicit last-observed label is not itself stale authority.
- A close action preserving backend work is intentional; cancellation must remain a separate request.
- No aesthetic redesign or generic refactor is requested.

## 16. Final scope and cleanup attestation

No production code, repository test, dependency manifest, configuration, generated contract fixture, branch, ref, history or worktree was intentionally changed. No fix was implemented. Private build copies and synthetic repositories were disposable test resources, not development worktrees or branch operations.

The only authored repository artifact is this Codex report. The required tooling's normal ignored caches are disclosed in §10 rather than hidden under a stronger filesystem-cleanliness claim.

Final checks accompanying the report write:

~~~text
branch: feat/workflow-package-marketplace
HEAD: 38c702203abbe213049eea4731750133508c0960
tree: 642e9cb29e1e1a7cdc717df3e849be621f4cdf5a
git diff --exit-code HEAD --: clean
git status --porcelain --untracked-files=no: empty

Permitted untracked paths:
docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-prompt.md
docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-claude.md
docs/reviews/2026-09-07-workflow-package-marketplace-adversarial-code-review-codex.md
~~~

The Claude path was not read or written. The private review evidence directory, screenshots and probe sources were removed after their results/hashes were recorded; those disposable files are not recoverable from this report, but their reproduction inputs and observations are included above. Final process inspection found no matching owned build/fixture descendants.

BLOCK — CRITICAL OR IMPORTANT FINDINGS REQUIRE REMEDIATION.
