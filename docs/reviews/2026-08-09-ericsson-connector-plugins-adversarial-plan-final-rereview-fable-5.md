# Adversarial plan final re-review — Ericsson connector plugins

Reviewing model: **Claude Fable 5** (`claude-fable-5`), reviewer short name `fable-5`.
Platform: macOS (Darwin 25.5.0), local checkouts. Review date: **2026-08-09**.

Review prompt: `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-review-prompt.md` (final re-review version).
Prior reports: `…-adversarial-plan-review-fable-5.md` (BLOCK; 1 Critical, 5 Important, 4 Minor) and `…-adversarial-plan-rereview-fable-5.md` (BLOCK; 1 Important RR-001, 1 Minor RR-002).

---

## 1. Repository states, plan hashes, and evidence sources

### Repository states (read-only)

| Repository | Branch | HEAD | Assessment |
| --- | --- | --- | --- |
| `hermes-agent` | `base` | `786f8dc0175410044000113233bec2bb610e7733` (== `origin/base`) | **Matches the final prompt's expectation exactly** (the npm-install-gate merge is now the documented baseline; previously verified to touch only installer files/tests with zero connector-plan overlap). Untracked: `docs/assessments/`, the prompt, and the two prior review reports. All worktrees preserved; none created or touched. |
| `ericsson-capabilities` | `main` | `dae405ede7049b621e502d9259f97481c940a65b` | Matches expected. Exactly the two preserved user modifications present (`mcp/outlook-mcp/src/outlook_cli/__init__.py`, `plugins/ericsson-teams/graph_auth.py`); neither staged, altered, nor cleaned. |
| `loop_24` (legacy) | `main` | `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6` | Matches the SHA pinned by all four plans. |

### Immutable review inputs — SHA-256 verified, all five match

| Artifact | Verified | Changed since last review? |
| --- | --- | --- |
| `docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md` | `7f378086…8b29` ✔ | Yes — read completely |
| `docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md` | `b6fbd791…05ea` ✔ | Yes — read completely |
| `docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md` | `f1dce266…6a79` ✔ | Yes — read completely |
| `docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md` | `524ff2f1…41e2b` ✔ | **Byte-identical** to the version verified in the prior re-review (same hash) |
| `docs/superpowers/plans/2026-08-09-ericsson-confluence-connector.md` | `ed3d495b…abd56` ✔ | **Byte-identical** to the version verified in the prior re-review (same hash) |

### Evidence sources

The full evidence base of the two prior reviews carries forward and remains valid: the code baseline is the **same commit** (`786f8dc0`) against which every implementation fact was verified in the prior re-review (workflow admission seam kwargs and their current omission, archon node schema, staging auto-seed behavior at `capability_staging.py:803-825`, browser-authority symbols, Teams regression files, vendor-script env contract, plugin loader/skills-index behavior, secret/profile helpers, the complete legacy LOOP24 trace at `fc3bf26`, and the official-docs comparison dated 2026-08-09). No repository content changed except the three revised documents and the prompt. This final pass verified, from the revised texts against that code baseline:

- The design's new decision 28, Phase 0 item 6, `plugins.lifecycle_migrations_applied` storage rules, revised Jira migration posture, and the clarified compatibility rule 3 (pre-transition auto-seeded entries deliberately not treated as explicit).
- GitLab Task 2's manifest-level `lifecycleMigration` validation rules (standalone `enabled: false` objects only; exact `from: auto_seeded_backend`; duplicate-id/unknown-kind/misplaced-metadata rejection).
- GitLab Task 6's upgraded-profile fixture and one-time transition semantics (remove only the migrating id; `workflow` enabled and `ericsson-teams` untouched; `plugins.disabled` authoritative; marker recorded in the **same atomic config save** through the existing profile config writer; applied markers never rerun; post-marker explicit enable survives restaging; malformed/duplicate metadata fails closed) and its updated Expected RED (staging currently has no lifecycle-transition ledger — true, verified previously).
- Jira Task 2's declared stable migration id `ericsson-jira-backend-to-standalone-v1`; Jira Task 9's single upgraded-profile fixture ("configuration state—not users") with settings/secrets retained but not enabling; Jira UAT step 2's upgraded-profile clause; Task 9's instruction to use only the generic GitLab Task 6 mechanism with no Jira ids in core.
- GitLab Task 7's corrected fixture: `requires:` flat list plus list-form `nodes: - id: inspect` with a `prompt:` key and `allowed_tools` — valid against the archon node shape (`language_schema.py:993, 1080-1090`; nodes as `- id:` sequences with kind-bearing keys; `allowed_tools` a node string-list field per `schema.py:489-491`) — and the new compile-first ordering requirement ("Assert this fixture compiles successfully before asserting the intended RED … so schema failure cannot masquerade as admission failure").
- The prompt itself now encodes the corrected semantics (invariant 4, the Jira §6 attack item, premise 7), so verdicts below are against the updated contract.

No subagents were used in this final pass; all evidence is direct.

---

## 2. Overall verdict

## **READY FOR IMPLEMENTATION**

**There are no findings.** Both prior findings are fully and correctly resolved, the resolutions introduce no new defects that survive the ten-element proof standard, and the two unchanged plans are byte-identical to their previously verified versions. The one residual observation (a wording inconsistency about which file carries the migration id) fails element 8 of the finding standard because the plans' own tests catch it, and is recorded in §16.

### Resolution verification for the two prior findings

**RR-001 (Important) — resolved.** The correction is exactly the shape required, and stronger than the minimum:
- *Generic and connector-neutral:* the transition is manifest-declared (`lifecycleMigration`, `from: auto_seeded_backend`) and staging derives behavior solely from that metadata — "Do not hardcode Ericsson plugin ids" (GitLab T6) and "do not add Jira ids or Jira branches to core code" (Jira T9) are explicit.
- *Scoped:* the fixture demands removal of **only** the migrating id from a seeded `[workflow, ericsson-jira, ericsson-teams]` list, with `workflow` staying enabled and `ericsson-teams` untouched — closing the exact collateral risks I flagged.
- *One-time and explicit-preserving:* the marker (`plugins.lifecycle_migrations_applied`) is written in the same atomic config save, applied transitions never rerun, and a post-marker `hermes plugins enable ericsson-jira` is tested to survive restaging — resolving the previous "explicit enables survive upgrades" ambiguity by making post-marker enables the only explicit ones, exactly as design compatibility rule 3 now states.
- *Fail-closed:* malformed or duplicate transition metadata changes nothing, and `plugins.disabled` remains authoritative.
- *Tested where the gap lived:* the previously forbidden upgraded-profile fixture is now mandated in both GitLab Task 6 and Jira Task 9, and Jira UAT step 2 exercises a **real upgraded profile** (seeded entry removed once; retained settings do not enable; later explicit enable survives restart/restage).
- *Validated at the schema gate:* the Ericsson linter rejects transition metadata on string/backend entries, unknown kinds, and duplicate ids (GitLab T2), so the mechanism cannot be misapplied at the source.

**RR-002 (Minor) — resolved.** The Task 7 snippet is now valid archon shape (list-form node, `id`/`prompt`/`allowed_tools`, flat `requires`), and the step adds the precise acceptance criterion I specified: the fixture must compile cleanly **before** the availability-omission RED is evaluated, so a schema failure cannot masquerade as the intended admission failure.

---

## 3. Findings

**None.** No Critical, Important, or Minor finding survives the ten-element proof standard against the revised inputs.

---

## 4. Design-requirement → plan traceability matrix

Identical to the prior re-review's matrix (all rows Complete) with the two previously partial rows now closed:

| Design requirement | Plan tasks | Coverage |
| --- | --- | --- |
| Decision 28 / Phase 0 item 6 — one-time `auto_seeded_backend` lifecycle transition | GitLab T2 (manifest schema + linter), GitLab T6 (generic staging + fixture), Jira T2 (declared id), Jira T9 (upgraded-profile fixture), Jira UAT 2 | **Complete** |
| Zero-user Jira baseline with historical config-state cleanup | Design §Migration posture + compat 3; the tasks above | **Complete** — "users" and "config state" are now handled as the distinct things they are |
| All other rows (Phase 0 closure, legacy pinning, flat `requires` + admission snapshots, routers, core browser authority, single-ticket showcase, SharePoint audit, vendoring gates, workflow-plugin preservation, foundation/descriptor/Desktop/staging/release/rollback/UAT structure) | unchanged tasks | **Complete** — carried forward from the prior re-review, where each was verified against real files, symbols, and commands; the SharePoint and Confluence plans are byte-identical |

**Orphan design requirements:** none. **Plan work lacking design authority:** none — the lifecycle-transition mechanism is authorized by decision 28 and implemented generically.

---

## 5. All-plan task coverage matrix

All tasks now assess **complete**. Changes since the prior matrix: GitLab T6 partial→complete (lifecycle transition + fixture); GitLab T7 blemish removed (valid fixture + compile-first ordering); Jira T2 partial→complete (declared migration id; disabled-for-every-profile now true for upgraded profiles); Jira T9 partial→complete (upgraded-profile fixture added; generic-mechanism-only constraint). Every other task was already complete and is textually unchanged (GitLab T1, T3–T5, T8–T15; Jira T1, T3–T8, T10) or byte-identical (all SharePoint and Confluence tasks).

---

## 6. Legacy behavior parity matrix

Unchanged from the prior re-review: every previously flagged row across all four connectors has an assigned disposition, all four behavior maps pin `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6`, GitLab's map mandates the exact adjudication list (409/400 MR duplicates, include forms, `$ref` coercion, slug rules, edpctl/mTLS defaults, caps), SharePoint's map must dispose of every `sp_audit.py` behavior, the Jira defect loop is truthfully deferred to Phase 6 with a parity-claim rejection test, and Confluence parity is measured against the accepted skill with launch-path adaptation documented. Nothing read during behavior freezing may disappear without a recorded disposition.

---

## 7. Plugin-architecture compliance matrix

All thirteen lifecycle steps now compliant. Step 12 (disable/re-enable and upgrade behavior) — the one item left open in the prior re-review — is closed by the manifest-driven one-time transition, the applied-migrations ledger with atomic write semantics, the upgraded-profile fixtures, and the UAT clause. The transition metadata is validated at the source linter, consumed generically by staging, and stored as bounded internal state that the UI/CLI do not present as user-configurable (design §Storage), so no second lifecycle authority is created.

---

## 8. Per-connector client-surface matrix

Unchanged from the prior re-review: all surfaces **planned and evidenced** for all four connectors, except Gateway/API chat which remains **planned but weak** (covered implicitly by the shared `_get_platform_tools` construction path; no gateway-named test — noted in §16, not a finding). The previously open lifecycle case — the upgraded-profile seeded-enablement transition — is now covered by planned fixtures and UAT.

---

## 9. Verdicts on the twenty non-negotiable invariants

All twenty invariants: **Holds as planned.** Changes from the prior re-review: invariant 4 (now including the prompt's upgraded-profile clause: the transition clears historical auto-seeded Jira enablement exactly once, preserves post-marker explicit enables, and leaves the workflow and Teams backends enabled/unchanged) moves from *partially violated* to **holds as planned**, verified against the fixture specifications in GitLab T6 and Jira T9 and UAT step 2; invariant 20 moves from *holds with one blemish* to **holds** (the invalid snippet is corrected; the file/command audit across the three revised documents found no new defects — the only newly named artifacts are new-file fixtures and the config key, which is additive and safely ignored by older builds per design compatibility rule 5). Invariants 1–3, 5–19 are unchanged and carry their prior verified verdicts.

---

## 10. Verdicts on the eighteen specific plan premises

All eighteen premises: **supported.** Changes from the prior re-review: premise 7 — now extended by the prompt to include "a bounded one-time `auto_seeded_backend` lifecycle transition without breaking workflow/Teams deployment or clearing later explicit enables" — is **supported**: the manifest object form is additive beside string entries, the linter constrains the metadata to standalone-disabled objects, staging behavior is derived solely from manifest metadata through the existing profile config writer, and the workflow/Teams preservation plus explicit-enable persistence are exactly what the T6/T9 fixtures assert. All other premises are unchanged from their previously verified supported verdicts (1–6, 8–18), including premise 10's data-defined curl classifier and premise 15's core browser authority.

---

## 11. Connector-specific edge-case and performance coverage assessment

Unchanged from the prior re-review, plus the new transition edge-cases are well covered: unseen vs. already-applied transitions, malformed/duplicate metadata, mixed old/new manifest entries, disabled-wins interaction, and atomic single-save marker+removal. The previously noted secondary unevennesses (numeric bounds recorded at behavior-map time; no single named cross-layer retry-multiplication test) stand as implementation-review items, not findings.

---

## 12. Strict-TDD command/file/ordering audit

The three revised documents introduce no new file or command references beyond: the `lifecycleMigration` manifest metadata (new schema surface owned by the existing `sets/ericsson.json` + `scripts/lint_manifest.py` + `tests/test_manifest.py`, all verified to exist), the `plugins.lifecycle_migrations_applied` config key (additive), the migration id string, and the corrected YAML fixture. All previously verified files, symbols, harnesses, and commands are unchanged. RED/GREEN ordering is intact; the new REDs (linter metadata rules, staging transition fixture, upgraded-profile fixture) precede their GREENs and name genuine current omissions (the absence of any lifecycle-transition ledger in `capability_staging.py` was verified in the prior reviews). Staging scopes and commit boundaries are unchanged and correct. The compile-before-RED ordering in Task 7 removes the last executability blemish. No placeholders. Release/push/merge steps still require separate authorization; literal `main` is never used.

---

## 13. Packaging, vendoring, upstream preservation, release, rollback, Windows UAT

Unchanged from the prior re-review's positive assessment. The lifecycle transition adds no packaging surface: it rides the existing manifest→vendor→staging pipeline (vendor tests extended to validate descriptor/migration metadata in T6), and the config key is internal state that older builds safely ignore. Jira UAT step 2 now includes the real upgraded-profile validation, which is the correct final gate for the RR-001 class.

---

## 14. What was verified complete and why

- **Hashes and states:** all five input hashes match the final prompt's table; the SharePoint and Confluence plans are byte-identical to versions already reviewed in full; all three repository states match the prompt's updated expectations; the preserved Ericsson files remain untouched.
- **RR-001 resolution:** verified at every level it must exist — product decision (design 28), Phase 0 obligation (item 6), storage semantics (§Storage), migration posture, compatibility rule 3, source-schema validation (GitLab T2), generic staging implementation contract and fixture (GitLab T6), connector declaration (Jira T2), installed-lifecycle fixture (Jira T9), and UAT (Jira step 2). The semantics are one-time, atomic, scoped, fail-closed, disabled-wins, and explicit-preserving — and the mechanism is connector-neutral with the Ericsson ids appearing only in manifest data and test fixtures.
- **RR-002 resolution:** the fixture is valid against the real archon node schema and the step orders compile-success before the intended admission RED.
- **No regression in the unchanged surface:** everything previously verified (admission seam, browser authority, routers, classifier, vendoring, behavior maps, showcase deferral, doc pins) is textually unchanged or byte-identical, and the code baseline is the same commit.

---

## 15. Required plan corrections

**None.**

---

## 16. Unresolved product decisions, unverified premises, and evidence needed

No unresolved product decisions remain. Three non-blocking observations for implementation-time attention, none satisfying the finding standard:

1. **Migration-id carrier wording (self-catching).** Jira Task 2 says "`plugin.yaml` declares stable lifecycle migration id" while GitLab Task 2's linter validates `lifecycleMigration` on the **manifest** plugin object (`sets/ericsson.json`), which is what Hermes staging consumes. If an implementer put the metadata only in `plugin.yaml`, GitLab T2's manifest tests and Jira T9's staging fixture (which requires the marker to be recorded after staging the manifest) would both fail, forcing the correct placement — so element 8 of the finding standard is not met. Recommended at implementation: treat the manifest object as the canonical carrier and mirror into `plugin.yaml` only if desired for documentation.
2. **Store snapshot optional-extension mechanics** (carried forward): machinery verified to exist; the additive-extension representation is guarded by Task 7's v1–v5 compatibility REDs and the Task 12 stop-and-extend rule.
3. **Gateway/API chat surface** (carried forward): implicitly covered by the shared construction path; one gateway-platform assertion in the surfaces suite would close it cheaply.

---

## 17. Command and evidence ledger

All commands ran read-only from `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`; no repository content was modified except this report; no test suite was executed; no temporary files were created; no network request was made in this final pass.

| # | Command (abbreviated) | Result / type |
| --- | --- | --- |
| 1 | `git status/branch/rev-parse` ×3 repos (incl. `base`, `origin/base`) | All states match the final prompt — execution |
| 2 | `shasum -a 256` on the five inputs | All five match; SharePoint/Confluence hashes unchanged from prior re-review — execution |
| 3 | `grep`/`sed` extraction of the final prompt's hash table, state expectations, output path, invariant 4, Jira §6 item, premise 7 | Prompt contract recorded — inspection |
| 4 | `wc -l` on the three changed documents | +29/+28/+13 lines, consistent with the located edits — execution |
| 5 | `grep -n 'auto_seeded_backend\|lifecycle_migrations_applied\|one-time'` across the three documents | All edit sites located — inspection |
| 6 | Reads of design decision 28, Phase 0 item 6, §Storage, §Migration posture, §Compatibility 3 (design lines 165–195, 288–300, 383–397, 558–582, 838–855, 74–76) | RR-001 design-level resolution verified — inspection |
| 7 | Reads of GitLab T2 Step 1 (lines 152–200) and T6 (lines 428–505) | Linter rules + staging fixture/semantics verified — inspection |
| 8 | Read of GitLab T7 fixture + compile-first clause (lines 528–575) | RR-002 resolution verified against the archon schema facts established in the prior re-review — inspection |
| 9 | Reads of Jira T2 (lines 98–140), T9 Step 1–2 (lines 470–505), UAT step 2 (lines 550–562) | Declared id, upgraded-profile fixture, generic-mechanism-only constraint, UAT clause verified — inspection |
| 10 | Prior-review evidence base (same code baseline `786f8dc0`: staging seed sites, admission seam, node schema, browser authority, vendor env contract, legacy trace at `fc3bf26`, official-docs comparison dated 2026-08-09) | Carried forward unchanged — inspection/execution/legacy tracing/documentation comparison |

---

## 18. Security-exclusion confirmation

No standalone threat-model, security-audit, security-review, penetration-testing, or vulnerability-scanning skill or workflow was invoked or attempted at any point in this final re-review. No exploit search, payload construction, credential probing, or authentication-bypass attempt was made; no real credentials were used; no live Ericsson/GitLab/Jira/Microsoft/Confluence service was contacted; no network request was made. Authentication, enrollment, migration, and readiness behavior was reviewed strictly as product functionality, and the plans' deterministic security-sensitive tests were assessed for completeness only.

---

THE REVIEWED DESIGN AND FOUR PLANS ARE READY FOR IMPLEMENTATION.
