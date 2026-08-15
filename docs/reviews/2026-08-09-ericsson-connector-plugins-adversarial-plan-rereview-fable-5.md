# Adversarial plan re-review — Ericsson connector plugins (revised design + four plans)

Reviewing model: **Claude Fable 5** (`claude-fable-5`), reviewer short name `fable-5`.
Platform: macOS (Darwin 25.5.0), local checkouts. Review date: **2026-08-09**.

Review prompt: `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-review-prompt.md` (revised re-review version).
Prior review: `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-review-fable-5.md` (verdict BLOCK, 1 Critical + 5 Important + 4 Minor).

---

## 1. Repository states, plan hashes, and evidence sources

### Repository states (read-only, recorded before substantive review)

| Repository | Branch | HEAD | Assessment |
| --- | --- | --- | --- |
| `hermes-agent` | `base` | `786f8dc0175410044000113233bec2bb610e7733` (== `origin/base`) | **Differs from the prompt's expectation** (`base` @ `9f2df504f`, checkout on `feature/npm-install-gate`). Investigated per the prompt's difference rule: the delta `9f2df504f..786f8dc0` is exactly the five npm-install-gate commits, touching only `scripts/install.ps1`, `scripts/install.sh`, one design doc, and a new `tests/test_install_npm_deps_gate.py` (538 insertions total; no local `feature/npm-install-gate` ref remains — the parallel work landed on `base` and was pushed). **Zero overlap with any file, module, seam, or test named by the design or plans**; every code fact cited below was re-validated as untouched by that range. Recorded as a state difference, not a finding, per the prompt's instruction not to treat the parallel work as one. Untracked: `docs/assessments/`, the prompt, and the prior review report. All 43 worktrees preserved; none created or touched. |
| `ericsson-capabilities` | `main` | `dae405ede7049b621e502d9259f97481c940a65b` | Matches expected. Exactly the two preserved user modifications present (`mcp/outlook-mcp/src/outlook_cli/__init__.py`, `plugins/ericsson-teams/graph_auth.py`); neither staged, altered, nor cleaned. |
| `loop_24` (legacy) | `main` | `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6` | Matches the SHA now pinned by all four plans. Relationship verified previously: `8ca26f8..fc3bf26` is one docgen-only commit; `3f124f5` is an ancestor. |

### Immutable review inputs — SHA-256 verified, all five match

| Artifact | Verified |
| --- | --- |
| `docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md` | `c00547aa…835a` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md` | `a9555841…baed` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md` | `3e03144f…bef4` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md` | `524ff2f1…41e2b` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-confluence-connector.md` | `ed3d495b…abd56` ✔ |

All five revised inputs were read completely and compared against the previously reviewed versions.

### Evidence sources

This re-review reuses the full evidence base of the prior review (architecture docs, official-docs comparison dated 2026-08-09, migration assessments, the Hermes implementation paths, and the complete legacy LOOP24 trace at `fc3bf26`), all of which remains valid at the new `base` because the intervening commits touch only the two installers. New verification performed for the revisions:

- `plugins/workflow/` admission seam: `assess_production_workflow_admission` keyword parameters `available_tools` / `available_services` (`admission_service.py:300-308`); `_phase5_admission_assessment` definition and its current no-kwargs call (`cli.py:1100, 1117-1126`); existence of `api_admission.py`, `catalog_api.py`, `showcase.py`, `models.py`, `store.py`, `scheduler.py` and of `tests/plugins/workflow/test_phase5_admission_parity.py`, `test_catalog_api.py`, `test_showcase_catalog.py`, `test_store.py`, `test_scheduler.py` (all exist).
- Store sealing machinery: `authenticated_definition_bytes`, `sealed_snapshot_digest`, atomic run-failure on authenticated package validation (`store.py:8052, 6015-6052, 16408-16433`).
- Archon node shape: nodes are a **sequence** of `- id:` entries with kind-bearing keys (`bash:`/`command:`/`prompt:`/`include:`/`loop:`) — `website/docs/user-guide/features/workflow-yaml-reference.md` examples; `plugins/workflow/language_schema.py:993, 1080-1090`.
- Established service-id vocabulary: the vendored companion already declares `required_services: [ericsson-jira, outlook]` (`capabilities/workflow-packages/ericsson/workflows/my-tickets-summary.hermes.yaml:7`).
- Core browser authority symbols named by the SharePoint/Confluence plans: `browser_profiles.get_profile` (`tools/browser_profiles.py:189`), `load_profiles` (`:138`), `browser_session_manager.acquire` (`:505`) and handle `release()` (`:446`) — all real.
- Teams regression files named by SharePoint Task 2: `tests/gateway/test_teams.py`, `test_teams_dotenv_isolation.py`, `test_teams_pipeline_runtime_wiring.py`, `tests/hermes_cli/test_teams_pipeline_plugin_cli.py`, `tests/plugins/test_teams_pipeline_plugin.py` — all exist.
- Vendor script env contract: `ERICSSON_CAPABILITIES_DIR` (`scripts/vendor-ericsson.mjs:1181-1184`) — the plans' corrected commands now match, and the `jq -r '.vendoredFrom'` equality gate is executable.
- Staging seed behavior (unchanged): `seed_baked_capabilities` unions manifest plugin names into `plugins.enabled` on every startup (`hermes_cli/capability_staging.py:803-825`), persisting entries in each profile's `config.yaml`.

Subagent reports were not used in this re-review; all evidence is direct.

---

## 2. Overall verdict

## **BLOCK**

All ten findings from the prior review are genuinely resolved — at the level of named files, real symbols, executable commands, and testable contracts, not prose (see §14). The revisions also *added* correct scope (SharePoint audit preservation, admission/resume revalidation, doc-pin honesty rules) rather than merely patching wording. Two new findings emerged from adversarial re-verification of the revised text: one Important (a real disabled-by-default gap on **upgraded** profiles that the plans' own test prohibitions would enshrine) and one Minor (an invalid illustrative YAML snippet in the plan's most delicate task). Under the prompt's verdict rules, one Important finding requires BLOCK. The Important finding is narrow and its correction is small; nothing else stands between these plans and READY.

---

## 3. Findings

| ID | Sev | Title | Release |
| --- | --- | --- | --- |
| RR-001 | IMPORTANT | Historically auto-seeded `plugins.enabled` entries leave `ericsson-jira` enabled on upgraded profiles; the plans forbid the one fixture that would catch it | 2 (seam in 1) |
| RR-002 | MINOR | GitLab Task 7's illustrative workflow fixture is not valid archon node shape | 1 |

### RR-001 — IMPORTANT — Auto-seeded enablement survives upgrade, defeating "disabled for every profile"

1. **ID/severity:** RR-001, Important.
2. **Title:** Historically auto-seeded `plugins.enabled: [ericsson-jira]` entries persist in upgraded profiles' `config.yaml`; no task removes or reclassifies them, and the Jira plan explicitly forbids the upgrade fixture that would expose the gap.
3. **Affected:** Release 2 (Jira), with the generic staging seam owned by Release 1 (GitLab Task 6). Design §"Authoritative architecture and documentation" ("Phase 0 does not infer an old explicit choice from historically auto-seeded state: all four connectors start disabled until the user explicitly enables them") and §"Jira connector → Migration posture" ("starts disabled for every profile"); GitLab Task 6 Step 1; Jira Task 2 Step 1, Task 9 Step 1; Jira UAT step 2; re-review invariant 4.
4. **Plan text vs. evidence:** Jira Task 9 Step 1: "Do not add historical-user migration fixtures or auto-enable behavior: the approved migration baseline is zero existing Jira users." GitLab Task 6 Step 1: "explicit enabled and disabled choices survive restaging and upgrades." Evidence: `hermes_cli/capability_staging.py:803-825` (`seed_baked_capabilities`, invoked from `run_brand_startup` on **every** CLI/serve start) has been unioning `workflow`, `ericsson-jira`, `ericsson-teams` (from `capabilities/ericsson.json` `plugins[]`) into `plugins.enabled` and **persisting the entries in each profile's `config.yaml`**. Deployed branded installations exist (release lineage through otto/loop24 v5.x is present in this checkout's release worktrees, and the workspace's release/UAT history documents real Windows installs), so upgraded profiles will carry the seeded entry regardless of whether anyone ever *used* Jira. A seeded entry is byte-indistinguishable from an explicit user enable. "Zero Jira users" is true of people; it is not true of config state.
5. **Violated invariant/decision:** Re-review invariant 4 ("disabled-by-default behavior for every profile (there are no Jira users to migrate)") and the design's own no-inference rule; approved product decision 3.
6. **Realistic scenario:** A Windows machine running any earlier branded release upgrades to v5.6.0. Its profile's `config.yaml` already lists `ericsson-jira` under `plugins.enabled` from prior startups. GitLab Task 6's staging change stops *adding* entries, but nothing removes the existing one. The migrated `kind: standalone` Jira plugin finds itself in `plugins.enabled` and loads — imported code, visible toolset, readiness prompting for configuration — on a profile whose user never chose it. Worse, Task 6's "explicit enabled choices survive upgrades" RED test **enshrines** this outcome as correct, because the test cannot distinguish seeded from explicit.
7. **Wrong result / consequence:** Every upgraded installation violates the release's headline contract ("present but not enabled") for Jira specifically; Jira UAT step 2 ("Every profile remains disabled until explicitly enabled; no legacy-user migration runs") passes on the fresh-install machine while failing silently on every real upgraded machine, which the UAT never exercises.
8. **Why nothing else covers it:** Task 6's "an old backend entry retains old behavior unless its source manifest explicitly migrates it" *could* be read as authorizing de-enablement at migration — but the Jira plan simultaneously requires "explicit enable survives" and forbids "historical-user migration fixtures", leaving two mutually incompatible readings with no test either way. The installed-distribution and surfaces suites use fresh profiles; the UAT upgrade step (10) validates preservation of the *new* install's own choices, not the pre-Release-2 seeded state.
9. **Smallest correction:** In GitLab Task 6 (generic, manifest-driven, no connector ids): when a manifest entry migrates a plugin id from the auto-seeded/backend lifecycle to standalone-disabled, staging **removes that id from `plugins.enabled`** unless a post-migration explicit-enable marker exists (the approved zero-user baseline makes unconditional removal safe at migration time; `plugins.disabled` continues to win; `workflow` is untouched because its manifest entry does not migrate). Amend Jira Task 9 Step 1 to permit exactly one upgraded-profile fixture exercising this rule.
10. **Required RED test:** A staging fixture whose old-profile `config.yaml` contains the historically seeded `plugins.enabled: [workflow, ericsson-jira, ericsson-teams]` plus the new manifest migrating `ericsson-jira` to standalone-disabled; assert after staging that `ericsson-jira` is not enabled, `workflow` remains enabled, `ericsson-teams` (still backend) is unaffected, and a subsequent explicit `hermes plugins enable ericsson-jira` persists across restaging.

### RR-002 — MINOR — Invalid illustrative fixture in GitLab Task 7

1. **ID/severity:** RR-002, Minor.
2. **Title:** The Task 7 Step 1 snippet uses `nodes:` as a mapping with `type: command`, which the archon schema does not accept.
3. **Affected:** Release 1, GitLab Task 7 Step 1 (the workflow-admission task).
4. **Plan text vs. evidence:** The snippet shows `nodes:\n  inspect:\n    type: command\n    allowed_tools: …`. Evidence: archon nodes are a **sequence** of `- id: <name>` entries whose kind is carried by the key (`command:`, `prompt:`, `bash:`, `include:`, `loop:`) — `website/docs/user-guide/features/workflow-yaml-reference.md` (nodes examples) and `plugins/workflow/language_schema.py:993, 1080-1090`; there is no `type` field. The `requires:` flat-list portion of the snippet is correct.
5. **Violated invariant:** Invariant 20 (exactness) at the documentation level.
6. **Realistic scenario:** The implementer copies the snippet as the fixture; compilation fails; time is spent distinguishing the intended RED (production callers omit availability inputs) from the accidental RED (invalid node shape) in the plan's most delicate task.
7. **Consequence:** A muddied RED signal exactly where clean causality matters most; risk of mis-attributing the failure to the admission seam.
8. **Why nothing else covers it:** The same step's requirement that fixtures be "real `archon-2026-07` YAML" exercised "through the public CLI admission" callers will eventually force the correction — but only after the confusion; no other text shows the correct shape.
9. **Smallest correction:** Fix the snippet to a valid list-form node (e.g. `- id: inspect` with a `command:`/`prompt:` key and `allowed_tools`).
10. **Acceptance criterion:** The fixture compiles cleanly under the current validator *before* the availability-omission RED is evaluated (compile-then-block ordering asserted in the test).

---

## 4. Design-requirement → plan traceability matrix

| Design requirement | Plan tasks | Coverage |
| --- | --- | --- |
| Phase 0 full-closure inventory (incl. vendored-paths, workflow packages, MCP, Outlook, manifest keys) | GitLab T1 Step 3 | Complete — command now enumerates every vendored surface incl. `jq` over the inventory |
| Legacy pinning + doc-snapshot refresh with per-flow `source_commit` honesty | GitLab T1 Step 4, T2; Jira/SP/Conf T1 | Complete |
| Decision 22 — flat `requires` + exact `allowed_tools`, no `requires.toolsets` | GitLab T7, T11; Jira T7; SP T9; Conf T6 | Complete (vocabulary matches the existing `required_services` practice) |
| Decision 23 — backend readiness/tool snapshots at every production admission path + resume/scheduler revalidation | GitLab T7 | Complete — files, symbols, kwargs, and parity tests all verified real; sealing rides the existing authenticated-package machinery with v1–v5 compatibility REDs |
| Decision 24 — index-visible router skills per connector | GitLab T11/T12, Jira T7/T9, SP T9, Conf T6/T8 + UAT steps | Complete — staging/index mechanics verified in prior review |
| Decision 25 — core owns the enrolled browser; no connector CDP authority | Conf T3 (+ its Read/use list), SP T4/T7 | Complete — named core APIs exist (`get_profile`, `acquire`/`release`); no raw port/default anywhere; collision refusal tested |
| Decision 26 — single-ticket showcase; defect loop deferred to Phase 6 | Jira T7 (workflow + deferral doc + rejection test), UAT 8/11 | Complete |
| Decision 27 — SharePoint owned-site + permission audit with independent readiness | SP T1 Step 2, T7, T9, UAT 9/10 | Complete — every `sp_audit.py` behavior row (owned groups/sites, users, admins, role assignments, groups/members, lists, subsites, limits, partial sites, CSV outputs, ownership-aware teardown) has a named disposition and test |
| Vendoring via `ERICSSON_CAPABILITIES_DIR` + `vendoredFrom` equality gate | GitLab T14, Jira T9, SP T11, Conf T8; design §branch boundaries | Complete — matches the script's actual env contract |
| Jira `auto` classifier as data | Design §Jira transport; Jira T3/T4 REDs | Complete — positive signature (normal native response + Cloudflare metadata + bounded 1010 marker) and exhaustive negative list, with explicit `transport: curl` escape |
| Zero-user Jira baseline | Design, Jira T2/T9, invariant 4 | **Partial** — fresh profiles covered; upgraded profiles with historically seeded enablement are not (RR-001) |
| Workflow-plugin enablement preserved through staging change | Design Phase 0 item 6; GitLab T2 RED, T6 Step 2 fixture | Complete |
| Everything carried over unchanged from v1 (descriptor foundation, storage/secrets, Desktop panel, GitLab/Jira/SP/Conf tool work, releases, rollback, UAT structure) | unchanged tasks | Complete as previously assessed |

**Orphan design requirements:** none. **Plan work lacking design authority:** none — Task 7's workflow-runtime scope is now explicitly authorized by decisions 22–23 and ledgered in `workflow-orchestration.yaml` (named in the task's file list, consistent with the repo's ledger governance).

---

## 5. All-plan task coverage matrix

| Plan | Task | Coverage | Note |
| --- | --- | --- | --- |
| GitLab | 1 | complete | Legacy freeze added: reads all 9 `ericsson_gitlab` modules + 6 Jira modules; behavior map with the exact adjudication list (409/400, include forms, `$ref`, edpctl, slugs) |
| GitLab | 2 | complete | Doc-pin refresh with per-flow `source_commit` review rule; workflow-backend enablement asserted |
| GitLab | 3–5 | complete | Unchanged from v1 (previously verified) |
| GitLab | 6 | **partial** | Staging change + workflow fixture present; migration-time de-seeding of historically auto-enabled ids unspecified (RR-001) |
| GitLab | 7 | complete (minor blemish) | Real files/symbols/kwargs; production-caller RED premise verified exact; illustrative snippet invalid (RR-002) |
| GitLab | 8–11 | complete | Behavior-map-cited REDs; both 409 and 400 MR duplicate classes; router skill + manifest |
| GitLab | 12 | complete | Cron bullet now states the real (ambient scheduler profile) mechanism |
| GitLab | 13–15 | complete | Vendor env + `vendoredFrom` gate; UAT step 10 matches the real admission contract |
| Jira | 1 | complete | Pinned at `fc3bf26` with drift verification |
| Jira | 2 | **partial** | Fresh-profile disabled-by-default covered; upgraded-profile seeded entry unaddressed (RR-001) |
| Jira | 3–6 | complete | Classifier defined as data with negative cases |
| Jira | 7 | complete | Router, single-ticket showcase, deferral docs, parity-claim rejection test |
| Jira | 8–10 | complete (9 partial per RR-001) | UAT 2 unverifiable for upgraded installs as planned |
| SharePoint | 1 | complete | Full `sp_audit` disposition mandate; "nothing read may disappear without a disposition" |
| SharePoint | 2 | complete | Placeholder eliminated — five named Teams tests + adapter read-only |
| SharePoint | 3–6 | complete | Unchanged from v1 |
| SharePoint | 7 | complete | Owned-site + audit with core browser authority, independent readiness, ownership-aware teardown |
| SharePoint | 8–12 | complete | UAT 9/10 covers audit and browser-authority behavior |
| Confluence | 1–2 | complete | Provenance + parity unchanged; legacy pin added |
| Confluence | 3 | complete | Core authority; no raw port; collision refusal; legacy env fallbacks dropped deliberately |
| Confluence | 4–5 | complete | Unchanged |
| Confluence | 6 | complete | Router permanent; flat-requires workflow compilation; disabled/enabled index behavior tested |
| Confluence | 7–9 | complete | UAT 12 (concurrent session not cleared; collisions fail through core diagnostics) added |

---

## 6. Legacy behavior parity matrix

The prior review's per-connector parity matrices (built from the actual legacy trace at `fc3bf26`) remain the baseline; the revisions change dispositions as follows — every previously flagged row is now assigned:

| Connector | Previously unadjudicated rows | Now |
| --- | --- | --- |
| GitLab | 409-vs-400 MR duplicates; one-level include traversal dropping `remote:`/`template:`; `$ref`→`main` coercion; slug rules; edpctl mTLS defaults; caps | All explicitly listed as mandatory behavior-map decisions (T1 Step 4) with tests citing map rows (T8–T10, incl. both duplicate classes) |
| Jira | Multi-ticket defect loop (Phase 6) | Deferred by decision 26; single-ticket showcase truthfully scoped; parity claims rejected by test |
| SharePoint | `sp_audit` audit collection; anchor/template discovery; recursive filtering; credential-selection branches | All mandated dispositions (T1 Step 2); audit behavior preserved as two bounded tools (T7) with Graph for owned-site discovery and core-browser same-origin REST for what Graph cannot reproduce — a faithful adaptation of the legacy CDP mechanism |
| Confluence | Launch-path change (skill's self-managed Edge → core authority); legacy env fallbacks | Deliberate, documented adaptations; parity fixtures cover the deterministic logic; browser lifecycle behavior tested against core semantics |

No legacy behavior read during any freeze step may now disappear without a recorded disposition — stated as a rule in both the design and the SharePoint plan.

---

## 7. Plugin-architecture compliance matrix

Unchanged from the prior review for steps 1–8 and 10–13 (all verified against production code and still valid at `786f8dc0`). Step 9 (plugin-owned skill registration) is now **compliant**: the design explicitly preserves the intentional explicit-load-only rule and routes discovery through index-visible source-owned routers — the pattern the existing Confluence/onboarding skills already prove, using the verified staging → `$HERMES_HOME/skills` → `<available_skills>` pipeline. The one open lifecycle item is the RR-001 upgrade case in step 12 (disable/re-enable and upgrade behavior).

---

## 8. Per-connector client-surface matrix

| Surface | Verdict (all four connectors) | Change vs. prior review |
| --- | --- | --- |
| Interactive CLI | planned and evidenced | unchanged |
| TUI/dashboard chat | planned and evidenced | unchanged |
| Electron Desktop | planned and evidenced | unchanged |
| Gateway/API chat | planned but weak | unchanged (still only implicitly covered by `_get_platform_tools` genericity; no gateway-specific test named) |
| Kanban | planned and evidenced | unchanged |
| Cron | planned and evidenced | **upgraded** — plans/design now state the real ambient-scheduler-profile mechanism; the test bullet matches it |
| Archon workflow | planned and evidenced | **upgraded from missing** — flat `requires` + `allowed_tools`, availability snapshots wired into the four production callers, resume/scheduler revalidation, parity across CLI/REST/catalog/showcase, `allowed_tools: []` preserved |
| Installed brand | planned and evidenced | unchanged |
| NL skill discovery | planned and evidenced | **upgraded from missing** — routers indexed while disabled, qualified loads after enablement, tested per connector + UAT steps |

Lifecycle cases: all covered as before, plus disabled-router/enabled-qualified-skill transitions and workflow resume-after-change. The remaining uncovered lifecycle case is the RR-001 upgraded-profile seeded-enablement transition.

---

## 9. Verdicts on the twenty non-negotiable invariants

| # | Invariant | Verdict |
| --- | --- | --- |
| 1 | One source owner | **Holds as planned** — Phase 0 closure now enumerates the full vendored surface |
| 2 | Plugin architecture is genuine | **Holds** — every named interface now verified to exist or be an authorized, ledgered generic extension |
| 3 | Disabled means absent | **Holds** (verified previously; unchanged) |
| 4 | Enablement is exact; disabled-by-default for every profile; workflow stays enabled | **Partially violated** — fresh profiles and the workflow plugin are covered and tested; upgraded profiles retain historically seeded `ericsson-jira` enablement (RR-001) |
| 5 | Configuration has one authority | **Holds as planned** |
| 6 | The core remains narrow | **Holds** — T7 widens a generic seam with a Release-1 consumer, ledgered |
| 7 | Prompt caching remains stable | **Holds** — fresh-session snapshot semantics; router-index changes land on fresh sessions only |
| 8 | Skills and tools stay separate | **Holds** — routers are guidance-only, transport-free, tested |
| 9 | Every execution surface uses the same plugin | **Holds** — the workflow lane now genuinely shares availability facts with the plugin authority |
| 10 | Workflow declarations are exact (flat `requires`, exact tools, `[]` deny-all, admission blocks, no Phase-6 claims) | **Holds as planned** — verified against the real schema, the real admission kwargs, and the real production-caller omission; deferral and rejection tests present |
| 11 | Legacy behavior is accounted for | **Holds as planned** — all four behavior maps pinned, adjudication lists explicit, "nothing disappears without a disposition" |
| 12 | Writes are deliberate | **Holds as planned** (unchanged) |
| 13 | Retries do not change semantics | **Holds as planned** (unchanged) |
| 14 | Cancellation reaches the real work | **Holds as planned** (unchanged; browser cancellation now inherits core-authority semantics) |
| 15 | Performance is bounded | **Holds as planned** — audit adds per-category and aggregate row/byte/page limits with truncation warnings |
| 16 | Windows is first-class | **Holds as planned** (unchanged) |
| 17 | Packaging is real | **Holds as planned** (unchanged) |
| 18 | Upstream preservation is explicit | **Holds as planned** — T7 amends `workflow-orchestration.yaml`; Graph ledger unchanged |
| 19 | Releases are independent | **Holds** (unchanged) |
| 20 | Plans are executable | **Holds with one blemish** — file/command audit clean across all four revised plans (every newly named file and symbol verified); the RR-002 snippet is the only inexactness found |

---

## 10. Verdicts on the eighteen specific plan premises

| # | Premise | Verdict |
| --- | --- | --- |
| 1 | Disabled plugin exposes static config metadata without import | **supported** (unchanged) |
| 2 | One generic descriptor serves CLI and Desktop | **supported** (unchanged) |
| 3 | Existing profile config/credential helpers suffice | **supported** (unchanged) |
| 4 | `PluginContext` supports setup actions + registrations with minimal extension | **supported** (unchanged) |
| 5 | Same registrations reach all surfaces | **supported** — the workflow lane's availability wiring closes the prior caveat |
| 6 | Fresh-session behavior preserves prompt caching | **supported** (unchanged) |
| 7 | Source manifests express disabled standalone capabilities without breaking Jira/Teams | **supported**, with RR-001 as the one unhandled migration residue (a staging rule, not a manifest defect) |
| 8 | Vendoring carries descriptors and removes stale managed files | **supported** (unchanged; command now correct) |
| 9 | Direct GitLab REST covers legacy in-scope behavior | **supported** (unchanged) |
| 10 | Jira curl design preserves the proven Cloudflare case without a second result model | **supported** — the classifier is now data: normal native response + Cloudflare metadata + bounded error-1010 marker; exhaustive negative list; explicit `transport: curl` escape; single taxonomy across transports |
| 11 | v3-first/v2-fallback preserves Cloud and Server/DC | **supported** (unchanged) |
| 12 | Generic Graph extensions suffice without regressing Teams | **supported** — now with the five named real-import Teams regression suites |
| 13 | SharePoint tool boundary suffices for later document-generation skills | **supported** (unchanged) |
| 14 | Confluence skill decomposes into one library without behavioral loss | **supported** (unchanged; launch-path adaptation documented and tested) |
| 15 | SharePoint audit and Confluence reference the named core enrolled-browser authority without claiming a raw CDP port, colliding with 9333, or tearing down another session's browser, with explicit non-hanging unattended failure | **supported** — the named core APIs exist (`get_profile`, registry, `acquire`/`release`); both plans forbid connector-owned port/launcher/profile authority, test collision refusal and ownership-aware teardown, and keep `interactive_session_required` honesty |
| 16 | Four sequential releases + Windows UAT isolate failures | **supported** (unchanged) |
| 17 | Test commands cover installed branded behavior | **supported** (unchanged posture: installed-distribution suites + paired-brand builds at exact SHAs + installed UAT; branded-bundle byte checks remain a release-step/UAT responsibility) |
| 18 | Upstream records and merge rehearsal cover every generic change | **supported** — the admission seam is ledgered in the task that creates it |

---

## 11. Connector-specific edge-case and performance coverage assessment

The prior assessment stands, with these upgrades: the SharePoint audit now carries explicit per-category and aggregate row/byte/page limits, truncation warnings, and per-site partial/unreachable status ("rather than false completeness"); the include-traversal and duplicate-MR edge families are behavior-map-mandated with both response classes; workflow resume adds a defined `connector_capability_changed` failure with revalidation before provider/tool invocation; browser concurrency (two plugins or a human sharing an enrolled session) is delegated to the verified core single-flight/ownership semantics and tested from both plans' sides. Still uneven, as before: numeric defaults and enforcement layers for several bounds (tree recursion caps, include depth) are asserted but not named — acceptable at plan level since the behavior maps must record the legacy values; and no explicit cross-layer retry-multiplication test is named (the design forbids it; no plan asserts it end-to-end through a worker). Neither rises to a finding under the prompt's severity rules.

---

## 12. Strict-TDD command/file/ordering audit

- **Every newly named existing file verified present**, including all seven `plugins/workflow/*.py` production files and five workflow test files in GitLab T7, the five Teams regression suites in SharePoint T2, the three core browser-authority modules in SharePoint T4/T7 and Confluence T3, and the previously verified foundation files. No missing paths found in any of the four revised plans.
- **Symbols:** `_phase5_admission_assessment` (`cli.py:1117`) exists and demonstrably calls `assess_production_workflow_admission(compilation)` without availability kwargs (`cli.py:1100, 1126`) — GitLab T7's Expected RED is exactly true. The claimed kwargs exist on the target function (`admission_service.py:306-307`). `browser_profiles.get_profile` and `browser_session_manager.acquire` are exact.
- **Commands:** vendoring now uses the script's real `ERICSSON_CAPABILITIES_DIR` contract with an executable `jq` equality gate; all other commands unchanged from the previously verified set (harnesses, npm workspace scripts, catalog builders, merge-rehearsal script).
- **RED/GREEN ordering:** intact; new REDs (admission parity, browser authority, audit, router indexing, upgraded doc pins) precede their GREENs and name genuine current omissions.
- **Staging scope:** commit lists match file lists; SharePoint T2 no longer conditionally stages the Azure adapter (read/use unchanged, verify-only); Confluence T6's `git add` set covers its new files. One cosmetic duplicate (`tests/tools/test_microsoft_graph_auth.py` listed twice in SharePoint T2's file list) — no consequence, not a finding.
- **Placeholders:** the prior "relevant Teams Graph tests discovered by rg" placeholder is eliminated. No new placeholders found.
- **Sequencing:** T7 (admission seam) correctly precedes the first workflow authoring (T11) and the surface proofs (T12), and later releases consume it without re-opening core. No circular dependencies; no reliance on unshipped later connectors.
- **Defects:** RR-002 (invalid illustrative snippet) is the only executability defect found; RR-001 is a semantics gap, not a command/file defect.

---

## 13. Packaging, vendoring, upstream preservation, release, rollback, Windows UAT

Unchanged from the prior review's positive assessment, now strengthened: the vendoring command matches the script; the `vendoredFrom`-equality gate is a hard fail; Phase 0's inventory covers the complete vendored closure (including the previously missed workflow packages, `mcp-servers.yaml`, and `plugins/outlook-mcp`); rollback text now correctly keeps the router discoverable with enable-first guidance after a connector is disabled; UAT matrices were updated to the real admission contract (flat `requires`, block-before-run-creation) and to browser-authority behavior (no raw port exposed; concurrent sessions preserved; collisions fail through core diagnostics); the Confluence unattended-UAT honesty rule is retained verbatim. The one packaging-adjacent gap is RR-001 (upgrade-path enablement residue).

---

## 14. What was verified complete — prior-finding resolution

Each prior finding was re-verified against the revised documents *and* the current code, not accepted from the change summary:

| Prior finding | Status | Verification |
| --- | --- | --- |
| CONN-PLAN-001 (Critical) — `requires.toolsets` / availability-blind admission | **Resolved** | Design decisions 22–23; GitLab T7 names the real four production callers, the real omitting symbol, the real kwargs, parity/store/scheduler tests (all files exist), resume revalidation, and the workflow-orchestration ledger; all other plans' workflow tasks re-specified to flat `requires` + exact `allowed_tools`, matching the existing `required_services` vocabulary |
| CONN-PLAN-002 — vendor env var | **Resolved** | `ERICSSON_CAPABILITIES_DIR` in all four plans + design; `jq` `vendoredFrom` equality gate; matches `vendor-ericsson.mjs:1181-1184` |
| CONN-PLAN-003 — skill discovery | **Resolved** | Decision 24; per-connector router skills (`skills/ericsson/{gitlab,jira,sharepoint}/SKILL.md`, Confluence router made permanent), manifest updates, index-visibility REDs while disabled, qualified-load tests after enablement, UAT steps |
| CONN-PLAN-004 — CDP 9333 collision | **Resolved** | Decision 25; Confluence T3 and SharePoint T4/T7 delegate to `browser_profiles`/`browser_session_registry`/`browser_session_manager` (symbols verified), forbid any connector port/launcher/profile authority, drop the legacy env fallbacks, and test collision refusal + ownership-aware teardown + UAT 12 |
| CONN-PLAN-005 — GitLab legacy freeze missing | **Resolved** | GitLab T1 reads all 15 relevant legacy modules, creates the behavior map with the exact adjudication list, pins `fc3bf26`, and gates production tasks on complete rows; T8–T10 REDs cite map rows and cover both MR-duplicate response classes |
| CONN-PLAN-006 — defect-loop Phase-6 conflict | **Resolved** | Decision 26; single-ticket showcase workflow + deferral recorded in `docs/flows/jira-defect-loop.md` + a test rejecting any multi-ticket-parity claim + UAT 11 |
| CONN-PLAN-007 — Phase-0 inventory scope | **Resolved** | T1 Step 3 enumerates the full closure incl. `jq` over `ericsson-vendored-paths.json`; dead `workflows` token removed |
| CONN-PLAN-008 — `sp_audit` disposition | **Resolved (exceeded)** | Decision 27 preserves the capability as product scope; T1 mandates dispositions for every audit behavior; T7 implements/tests it |
| CONN-PLAN-009 — `rg` placeholder | **Resolved** | Five named Teams regression files (all exist); adapter read-only |
| CONN-PLAN-010 — legacy pin / doc skew | **Resolved** | `fc3bf26` pinned in all four maps with drift verification; GitLab T2 refreshes `AGENTS.md`/`CLAUDE.md`/`docs/README.md` to the 30-flow inventory with a per-flow `source_commit` honesty rule |

Also verified complete: the state difference on `base` is fully bounded to installer files; the workflow-plugin enablement fixture exists (GitLab T6 Step 2); cron wording now matches the real mechanism everywhere it appears.

---

## 15. Required plan corrections, ordered

1. **(RR-001, Important — Release 1 seam, Release 2 exposure.)** Add the migration-time de-seeding rule to GitLab Task 6 (generic: a manifest entry migrating from auto-seeded/backend staging to standalone-disabled removes its id from `plugins.enabled`; `workflow` untouched; `plugins.disabled` still wins) and amend Jira Task 9 to include the single upgraded-profile fixture. Update Jira UAT step 2 to include one upgraded-install check or an explicit waiver.
2. **(RR-002, Minor.)** Correct the GitLab Task 7 snippet to valid archon list-form node syntax.

---

## 16. Unresolved product decisions, unverified premises, evidence needed

1. **Store snapshot optional-extension mechanics (partially verified).** Task 7 seals connector fingerprints "using the existing authenticated package/snapshot extension seam" without a new normalizer/snapshot version. The authenticated-package and sealed-digest machinery exists (`store.py:8052, 6015-6052, 16408-16433`), and the plan's own v1–v5 compatibility REDs guard the assumption; the exact additive-extension representation inside the store was not independently proven and should be confirmed in Task 7's first RED cycle (the plan's stop-and-extend rule in Task 12 Step 2 covers a surprise).
2. **Gateway/API chat surface** remains implicitly covered (same `_get_platform_tools` construction) with no gateway-named test; acceptable, but one line in the surfaces suite asserting a gateway-platform session receives the connector toolset would close it.
3. **Numeric bounds provenance:** several caps (tree recursion, include depth/count) are bound-by-contract with values to be recorded from the legacy maps; verify at behavior-map review that each bound names its default, config key, and enforcement layer.
4. **Cross-layer retry multiplication:** forbidden by the design, not asserted end-to-end by any single named test; consider one worker-path test per write tool during implementation review.
5. **Zero-Jira-users baseline** is an approved product fact accepted as given; RR-001 addresses its config-state residue, not the fact itself.

---

## 17. Command and evidence ledger

All commands ran read-only from `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` unless noted; no repository content was modified except this report; no test suite was executed; the preserved Ericsson files were untouched; no temporary files were created.

| # | Command (abbreviated) | Result / type |
| --- | --- | --- |
| 1 | `git status/branch/rev-parse` ×3 repos + `rev-parse base origin/base` | States recorded; `base`==`origin/base`==`786f8dc0` — execution |
| 2 | `shasum -a 256` on the five revised inputs | All five match the re-review prompt — execution |
| 3 | `git log --oneline 9f2df504f..786f8dc0` + `git diff --stat` | 5 commits; installers + 1 doc + 1 test only — execution |
| 4 | `wc -l` on revised inputs | Sizing — execution |
| 5 | Read of the revised prompt + all five revised inputs (complete) | Inspection |
| 6 | `/bin/ls` on `plugins/workflow/{cli,api_admission,catalog_api,showcase,models,store,scheduler}.py` and the five named workflow test files | All exist — execution |
| 7 | `grep _phase5_admission_assessment plugins/workflow/cli.py`; `grep -A8 'def assess_production_workflow_admission' admission_service.py`; `sed -n 1117,1150p cli.py` | Symbol real; kwargs `available_tools`/`available_services` real; current call omits them — inspection |
| 8 | `grep 'kind:/type:/nodes:' workflow-yaml-reference.md` + `language_schema.py` node-kind keys | Node shape is list-form with kind keys; no `type` field — inspection |
| 9 | `grep -n extension\|authenticated\|sealed plugins/workflow/store.py` | Authenticated-package/sealed-digest machinery located — inspection |
| 10 | `/bin/ls` five Teams test files; `grep 'def get_profile/def acquire/def release'` in browser modules | All exist; `get_profile` at `browser_profiles.py:189`, `acquire` at `browser_session_manager.py:505` — execution/inspection |
| 11 | `grep required_services capabilities/workflow-packages/ericsson/workflows/*.hermes.yaml` | `[ericsson-jira, outlook]` — existing service-id vocabulary — inspection |
| 12 | Prior-review evidence base (repo states, hashes, legacy trace at `fc3bf26`, staging/loader/skills/Graph/browser code reads, official-docs fetch dated 2026-08-09) | Carried forward; validity at `786f8dc0` established by ledger row 3 — inspection/execution/legacy tracing/documentation comparison |

---

## 18. Security-exclusion confirmation

No standalone threat-model, security-audit, security-review, penetration-testing, or vulnerability-scanning skill or workflow was invoked or attempted during this re-review. No exploit search, payload construction, credential probing, or authentication-bypass attempt was made; no real credentials were used; no live Ericsson/GitLab/Jira/Microsoft/Confluence service was contacted; no network request was made at all in this re-review (the official-docs comparison was performed in the prior review on the same date and carried forward). Authentication, enrollment, and readiness behavior was reviewed strictly as product functionality; the plans' deterministic security-sensitive tests were assessed for completeness only and not executed.

---

IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.
