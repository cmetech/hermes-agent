# Adversarial plan-quality and porting-fidelity review — Ericsson connector plugins

Reviewing model: **Claude Fable 5** (`claude-fable-5`), reviewer short name `fable-5`.
Platform: macOS (Darwin 25.5.0), local checkouts. Review date: **2026-08-09**.

Review prompt: `docs/reviews/2026-08-09-ericsson-connector-plugins-adversarial-plan-review-prompt.md`.

---

## 1. Repository states, plan hashes, and evidence sources

### Repository states (recorded before substantive review, read-only)

| Repository | Branch | HEAD | Status |
| --- | --- | --- | --- |
| `hermes-agent` | `base` | `9f2df504f72f469c172a5c32e314f16e60350792` (== `origin/base`) | Matches expected. Untracked only: `docs/assessments/`, the review prompt itself. 43 linked worktrees present; none touched, none created. |
| `ericsson-capabilities` | `main` | `dae405ede7049b621e502d9259f97481c940a65b` | Matches expected. Exactly the two preserved user modifications present: `mcp/outlook-mcp/src/outlook_cli/__init__.py`, `plugins/ericsson-teams/graph_auth.py`. Neither was staged, altered, or cleaned. (The `graph_auth.py` diff was inspected read-only: 4 comment-only hunks, no behavioral change.) |
| `loop_24` (legacy) | `main` | `fc3bf26d64e05cc3703ee39e323bbf3c1eaa4cd6` | The prompt did not pin an expected SHA. **Drift vs. the assessments' baseline `8ca26f8`: exactly one commit** (`fc3bf26 "update document verification"`) touching only `custom_components/ericsson_docgen/{__init__,doc_editor,doc_verify}.py` — outside the four connectors' scope. Review inputs remain valid. |

### Immutable review inputs — SHA-256 verified, all match

| Artifact | Verified |
| --- | --- |
| `docs/superpowers/specs/2026-08-09-ericsson-connector-plugins-design.md` | `733fdfcc…78e2` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-gitlab-connector.md` | `67914e82…47d1` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-jira-connector.md` | `5b451b88…2bd4` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-sharepoint-connector.md` | `783faedc…21e2` ✔ |
| `docs/superpowers/plans/2026-08-09-ericsson-confluence-connector.md` | `40064456…4330` ✔ |

All five inputs were read completely.

### Evidence sources reviewed

- **Architecture docs (read completely):** `hermes-agent/AGENTS.md`, `apps/desktop/AGENTS.md`, `website/docs/user-guide/features/plugins.md`, `website/docs/developer-guide/plugins/index.md`, `ericsson-capabilities/AGENTS.md`, `ericsson-capabilities/docs/README.md`, `ericsson-capabilities/docs/configuration.md` (outline + targeted sections), `sets/ericsson.json` (complete).
- **Migration assessments (read completely):** `docs/assessments/loop24-migration/{README,legacy-workflow-portability,tool-capability-portability}.md`.
- **Official docs comparison (network, accessed 2026-08-09):** `https://hermes-agent.nousresearch.com/docs/developer-guide/plugins`. No skew on load-bearing facts: upstream also documents plugin skills as **excluded** from the `<available_skills>` index; upstream documents **no** `config.schema.json` descriptor and **no** setup-action API (both are new interfaces these plans introduce). Local code remains the implementation authority.
- **Hermes implementation paths inspected** (directly and via search agents whose leads were re-verified against code before use as evidence): `hermes_cli/plugins.py`, `plugins_cmd.py`, `subcommands/{tools,plugins}.py`, `tools_config.py`, `capability_staging.py`, `config.py`, `profiles.py`, `web_server.py`, `web_routers/`, `tools/{registry,skills_tool,browser_profiles}.py`, `agent/{prompt_builder,agent_init}.py`, `toolsets.py`, `model_tools.py`, `hermes_cli/kanban_db.py`, `cron/scheduler.py`, `tui_gateway/server.py`, `gateway/run.py`, `plugins/workflow/{schema,language,language_schema,compat,models,cli,api_admission,catalog_api,admission_service,executors/ai}.py`, `agent/plugin_agent_worker.py`, `tools/microsoft_graph_{auth,client}.py`, `agent/azure_identity_adapter.py`, `scripts/vendor-ericsson.mjs`, `capabilities/{ericsson.json,ericsson-vendored-paths.json}`, `skills/ericsson/confluence-research/scripts/*`, `apps/desktop/package.json` and settings components, upstream-customization ledgers under `docs/upstream-customizations/`.
- **Legacy LOOP24 paths inspected:** `utils/{sp_files,sp_audit,confluence_page}.py`, all of `custom_components/ericsson_gitlab/` (9 modules), `custom_components/ericsson_jira/` (6 modules), `custom_components/ericsson_parsers/{sharepoint_files_fetcher,confluence_fetcher}.py`, `custom_components/ericsson_utils/powershell_script_runner.py`, the Jira/GitLab/CI-auditor flow JSONs, and the flows tree layout.

Subagent reports were used as **leads only**; every fact cited in a finding below was reduced to direct file/line evidence or a recorded command result.

---

## 2. Overall verdict

## **BLOCK**

One Critical and five Important findings remain. The design's product decisions, release structure, security-exclusion posture, disabled-by-default lifecycle, and most per-connector premises are sound and well-grounded in the actual code; the blocking findings are concentrated in (a) the workflow-admission contract, (b) one wrong load-bearing command repeated in all four plans, (c) natural-language discoverability of plugin-owned skills, (d) a browser-port authority collision in the Confluence plan, and (e) a missing legacy behavior-freeze step in the GitLab plan.

---

## 3. Findings

### Findings table (by severity)

| ID | Sev | Title | Releases |
| --- | --- | --- | --- |
| CONN-PLAN-001 | CRITICAL | `requires.toolsets` is not an `archon-2026-07` construct, and production workflow admission never receives tool availability | 1–4 |
| CONN-PLAN-002 | IMPORTANT | Vendoring command uses the wrong environment variable (`SOURCE_REPO` vs `ERICSSON_CAPABILITIES_DIR`) in all four plans | 1–4 |
| CONN-PLAN-003 | IMPORTANT | Plugin-registered skills are invisible to natural-language discovery; no plan task makes connector guidance discoverable | 1–3 (4 partially mitigated) |
| CONN-PLAN-004 | IMPORTANT | Confluence "dedicated" CDP port default 9333 collides with the core enrolled-browser port authority | 4 |
| CONN-PLAN-005 | IMPORTANT | The GitLab plan contains no legacy behavior-freeze task and never reads a single `loop_24` file | 1 |
| CONN-PLAN-006 | IMPORTANT | `jira-defect-loop.yml` is scheduled despite the flow's Phase-6 (`loop_group`) classification, with no stated redesign | 2 |
| CONN-PLAN-007 | MINOR | Phase-0 drift-inventory command under-scopes the vendored surface | 1 |
| CONN-PLAN-008 | MINOR | Legacy `sp_audit.py` browser-based audit collection has no planned disposition | 3 |
| CONN-PLAN-009 | MINOR | SharePoint Task 2's "relevant Teams Graph tests discovered by `rg`" is a discover-at-execution placeholder | 3 |
| CONN-PLAN-010 | MINOR | Behavior maps never pin the legacy `loop_24` SHA; Ericsson docs pin a stale snapshot (`3f124f5`, 11 flows) | 1–4 |

---

### CONN-PLAN-001 — CRITICAL — `requires.toolsets` is not an `archon-2026-07` construct, and production admission never receives tool availability

1. **ID/severity:** CONN-PLAN-001, Critical.
2. **Title:** The workflow toolset-admission contract the design and all four plans build on does not exist in the current language or runtime.
3. **Affected:** All four releases. Design §"Cross-surface execution contract → Archon workflows" ("Workflows declare `requires.toolsets` and exact `allowed_tools`… Admission blocks when the plugin is disabled…"), design §"Skills and workflows"; GitLab Tasks 10 and 11, Jira Task 7, SharePoint Task 8, Confluence Tasks 6 and 8; review-prompt invariant 10.
4. **Plan text vs. evidence:** GitLab Task 10 Step 1: "Require workflows to use `archon-2026-07`, exact `requires.toolsets`, exact allowed tools…" (repeated in substance in the other three plans). Current source evidence: under `archon-2026-07`, `requires` is validated as a **flat string list** — `plugins/workflow/schema.py:1754-1756` (`_string_list(document["requires"])`) and `plugins/workflow/language_schema.py:1413` (`_field("definition", "requires", "array", "string_list")`); `grep` of `plugins/workflow/` finds **no** `toolsets` declaration anywhere. The mapping form (`requires: {toolsets: […], mcp_servers: […]}`) exists only in the **legacy-profile** source workflow `ericsson-capabilities/workflows/my-tickets-summary.yml:5-8`, which the repo's own portability assessment states "retains `hermes-legacy` behavior". Separately, availability blocking is keyed on `available_tools is not None` (`plugins/workflow/compat.py:733-741`, `unavailable_tool`, blocking) — but **no production admission entry point supplies it**: `plugins/workflow/cli.py:1126` (`assess_production_workflow_admission(compilation)`), `plugins/workflow/api_admission.py:350-354`, `plugins/workflow/catalog_api.py:1139-1143` all omit `available_tools`/`available_services`.
5. **Violated invariant/decision:** Invariant 10 ("Workflow declarations are exact… declare exact `requires.toolsets`") is unimplementable as written; invariant 2 (the plans name an interface that does not exist); approved product decision 5 (one implementation, every surface) is unverifiable for the workflow surface as planned.
6. **Realistic scenario:** The implementer authors `workflows/jira-to-gitlab.yml` as an `archon-2026-07` package with `requires: {toolsets: [ericsson-gitlab, ericsson-jira]}` because the plan's RED test demands exactly that. The source-repo test (string-level assertion) passes. On the Hermes side the archon validator rejects the mapping (`requires` must be a string list) — or, if the author writes a flat list instead, admission **admits** the workflow in a profile where the GitLab plugin is disabled, because no caller passes a live tool registry, and the run fails at execution with unknown-tool errors. GitLab Task 11's RED bullet "Archon admission blocks disabled/unready toolsets" cannot pass "through Tasks 3–6 without connector-specific production changes" as Task 11 Step 2 asserts, because the required generic seam (wiring an availability snapshot into production admission) is not planned in any task's file list — no plan touches any `plugins/workflow/` production file.
7. **Wrong result / consequence:** Either the flagship Jira→GitLab workflow fails admission during Windows UAT (UAT steps 9–10 unsatisfiable), or workflows silently admit against disabled/unconfigured connectors and fail mid-run — for an operator, a scheduled/board-driven workflow that "was accepted" dies at its first tool call with an unknown-tool diagnostic instead of a precise admission block.
8. **Why nothing else covers it:** Task 11 Step 2's stop-and-extend rule exists, but the same step asserts the tests "should pass through Tasks 3–6", which is demonstrably false for this bullet — the rule will fire late, mid-implementation, forcing unplanned generic workflow-runtime scope that the design's repository-boundary section never authorizes and the upstream ledger plan (Task 13 Step 2) does not enumerate. The workflow-orchestration ledger (`docs/upstream-customizations/workflow-orchestration.yaml`) governs existing seams, not this new one.
9. **Smallest correction:** (a) In all four plans' workflow tasks, replace "exact `requires.toolsets`" with the real contract: exact per-node `allowed_tools` (whose alias/availability checks are the actual blocking mechanism, `compat.py:718-749`) plus, optionally, the flat `requires: [service…]` list. (b) Add one explicitly scoped generic task to the GitLab plan (Release 1, the foundation release) that passes a live registry/tool-availability snapshot into `assess_production_workflow_admission` / the API admission callers via the **existing** `available_tools` seam, with its own upstream-ledger entry and invariant tests. (c) If structured `requires.toolsets` is genuinely wanted in the language, that is a separate workflow-language design authorization, not a connector-plan side effect.
10. **Required RED/verification:** Hermes-side RED (new or in `test_ericsson_connector_toolsets.py`): compile the authored connector workflow through the real archon validator and assert (i) it validates, and (ii) with the connector plugin disabled in the profile, the **production admission entry point** reports a blocking `unavailable_tool`/`required_service` finding and `runnable=False` with `next_actions=("doctor",)`. Source-side RED: assert the workflow YAML uses only constructs the archon schema accepts (run the validator, not a string grep).

---

### CONN-PLAN-002 — IMPORTANT — Vendoring command uses the wrong environment variable in all four plans

1. **ID/severity:** CONN-PLAN-002, Important.
2. **Title:** `SOURCE_REPO=<worktree> node scripts/vendor-ericsson.mjs` does not select the source; the script reads `ERICSSON_CAPABILITIES_DIR`.
3. **Affected:** GitLab Task 13 Step 1, Jira Task 9 Step 2, SharePoint Task 10 Step 2, Confluence Task 8 Step 2 — the load-bearing vendoring step of every release.
4. **Plan text vs. evidence:** All four plans: `SOURCE_REPO=<absolute-ericsson-worktree> node scripts/vendor-ericsson.mjs`. Evidence: `scripts/vendor-ericsson.mjs:1181-1184` — `const sourceDir = process.env.ERICSSON_CAPABILITIES_DIR || path.resolve(process.cwd(), '..', 'ericsson-capabilities')`, then `throw new Error(\`ericsson-capabilities not found at ${sourceDir} (set ERICSSON_CAPABILITIES_DIR)\`)`. `SOURCE_REPO` appears nowhere in the script or its tests.
5. **Violated invariant/decision:** Invariant 20 (plans are executable — exact commands); invariant 1 / product decision 2 (vendored bytes must come from the exact committed source revision).
6. **Realistic scenario:** Run from the Hermes worktree `.worktrees/ericsson-gitlab-connector`, the fallback resolves to `<hermes>/.worktrees/ericsson-capabilities`, which does not exist → hard error. The natural "fix" is to run from the Hermes root or rely on the default — which vendors the **root** `../ericsson-capabilities` checkout's committed HEAD (`main` @ `dae405e`), not the accepted connector worktree commit. The vendor completes "successfully" with the wrong revision.
7. **Wrong result / consequence:** A release built with none of the new connector source vendored (or a stale revision), while the plan's checklist reads green until the distribution tests fail — or, worse, UAT is reached with silently missing content if the distribution assertions are weaker than intended.
8. **Why nothing else covers it:** `vendoredFrom` verification ("verify… `vendoredFrom` full SHA", GitLab T13 Step 1) is stated as prose, not as a command comparing against the **recorded worktree SHA** from Task 12; an operator following the exact printed commands hits the failure first.
9. **Smallest correction:** Replace the env var in all four plans with `ERICSSON_CAPABILITIES_DIR=<absolute-ericsson-worktree>` and add one verification line: assert `capabilities/ericsson.json` `vendoredFrom` equals the exact SHA recorded at source closure.
10. **Required verification:** After vendoring: `python -c "import json;print(json.load(open('capabilities/ericsson.json'))['vendoredFrom'])"` must equal the recorded clean source-worktree SHA; the vendor test run (`node --test scripts/__tests__/vendor-ericsson.test.mjs`) stays as-is.

---

### CONN-PLAN-003 — IMPORTANT — Plugin-registered skills are invisible to natural-language discovery

1. **ID/severity:** CONN-PLAN-003, Important.
2. **Title:** `ctx.register_skill` skills never enter the system prompt's `<available_skills>` index, the Skills UI, or `skills.disabled`; the plans register all connector guidance this way and never plan a discovery path.
3. **Affected:** Releases 1–3 fully; Release 4 partially (its top-level compatibility router is index-visible). Design §"Skills and workflows" ("Connector-specific skills are registered by their plugins…"), §"Natural-language chat"; GitLab Task 10/11/14 (UAT 7 "Chat invokes the tools naturally"), Jira Task 7/10 (UAT 6 "Natural-language chat triggers Jira skills/tools"), SharePoint Task 8.
4. **Plan text vs. evidence:** The plans create plugin-owned skills (`plugins/ericsson-gitlab/skills/…`, `plugins/ericsson-jira/skills/…`, `plugins/ericsson-sharepoint/skills/…`) registered via the plugin context. Evidence: `hermes_cli/plugins.py:1281-1290` — a registered plugin skill "does **not** enter the flat `~/.hermes/skills/` tree and is **not** listed in the system prompt's `<available_skills>` index — plugin skills are opt-in explicit loads only." The index is built exclusively from skills directories: `tools/skills_tool.py:684,715-719` (`_find_all_skills` scans `$HERMES_HOME/skills` + `skills.external_dirs`) feeding `agent/prompt_builder.py:1770-1860`. The official developer guide (local and upstream, both checked 2026-08-09) states the same. Plugin skills are also invisible to the Skills settings page and `skills.disabled` toggling.
5. **Violated invariant/decision:** Product decision 6 / design goal 4 ("Let users invoke the same capabilities conversationally") and the design's own skill contract ("skills… are fully read into the current user turn" — a skill the model cannot discover is never read); UAT gates 6–7 presuppose discovery.
6. **Realistic scenario:** A user enables and configures `ericsson-gitlab`, opens a fresh conversation, and asks "review merge request 42 in project X". The tool schemas are visible, so the model may fumble through raw tool calls — but the `merge-request-review` skill (approval rules, review method, output contract, the moved-from-legacy reasoning that replaced `CodeReviewRunner`) never loads because nothing tells the model it exists. The design's central bet — legacy embedded LLM prompts replaced by *skill-guided* active-agent reasoning — silently degrades to unguided tool use.
7. **Wrong result / consequence:** Ported "reasoning" behavior (triage policy, fix-summary composition, MR review structure) is effectively lost at chat time even though every test that greps skill files passes; "disabling removes guidance from new sessions" holds vacuously because the guidance never appeared.
8. **Why nothing else covers it:** The Confluence plan's top-level compatibility router (index-visible, staged via `skills/ericsson/`) covers only Confluence; GitLab's cross-connector `skills/ericsson/jira-to-gitlab/SKILL.md` covers only that journey. Nothing covers gitlab-research/MR-review/CI, Jira ticket-research/defect-triage, or the SharePoint skills. GitLab Task 11's "enabled/configured plugin present in a fresh session" bullet does not specify that *skills* must be discoverable in the prompt index, so the test suite can pass with invisible skills.
9. **Smallest correction:** Choose and plan one of: (a) a generic, invariant-tested, upstream-ledgered extension that lists enabled plugins' registered skills in the `<available_skills>` index (respecting the prompt-cache rule — index content changes only affect fresh sessions); or (b) index-visible thin router/pointer skills under `skills/ericsson/` per connector (the pattern the Confluence plan and onboarding router already use) that direct the agent to load the namespaced plugin skills. Either way, add the chosen mechanism's files to the plan's file lists.
10. **Required RED:** In `test_ericsson_connector_surfaces.py`: with the connector enabled and configured, build a fresh session's skills prompt block and assert the connector guidance (router or namespaced skill) appears in the discoverable index; with the plugin disabled, assert it does not.

---

### CONN-PLAN-004 — IMPORTANT — Confluence CDP port 9333 collides with the core enrolled-browser authority

1. **ID/severity:** CONN-PLAN-004, Important.
2. **Title:** The plan bakes "dedicated CDP port default `9333`" into its RED tests, but 9333 is the port the core enrolled-browser profile system claims and defends.
3. **Affected:** Release 4. Design §"Confluence connector → Authentication and transport" ("dedicated protected browser profile and CDP port `9333`, separate from general browser automation"); Confluence Task 3 (RED list: "dedicated CDP port default `9333`", "cross-process profile/CDP lock").
4. **Plan text vs. evidence:** Evidence: `tools/browser_profiles.py:49` — `DEFAULT_CDP_PORT = 9333`, with the surrounding comment explaining that the **enrolled corporate profile** owns this port and that `hermes_cli.browser_connect` "refuses to connect to any port in `enrolled_cdp_ports()`, which always contains this value". The current skill uses the same default (`skills/ericsson/confluence-research/scripts/backends.py:37`, `CONFLUENCE_CDP_PORT` default 9333). The vendored `capabilities/ericsson.json` ships `configDefaults` seeding `browser.profiles` trusted origins for `*.ericsson.com`/`*.ericsson.net` — i.e., the same deployments are expected to configure the **core** enrolled profile. Per the repo's browser-profiles ledger (`docs/upstream-customizations/browser-profiles.yaml`) the core stack single-flights enrolled acquisition, rejects a second enrolled profile claiming an already-claimed `cdp_port`, and verifies `/json/version` browser identity fail-closed. The plan's file list contains no `tools/browser_profiles.py`, `tools/browser_session_registry.py`, or `tools/browser_session_manager.py` coexistence work and never mentions `enrolled_cdp_ports()`.
5. **Violated invariant/decision:** The design's own isolation claim ("separate from general browser automation") is self-contradictory at the default; review-prompt §10 (concurrent operations sharing a browser profile/port) is unaddressed.
6. **Realistic scenario:** A user has the shipped `browser.profiles` enrolled profile configured (seeded trusted origins make this the expected state) and later enables the Confluence plugin at its default. Two independent lifecycle managers now contend for one listener: the plugin either attaches to the user's **real** enrolled corporate browser (defeating "dedicated protected profile" — its "clean browser/cookie flush" on `clear_session` would flush the user's actual corporate session), or launches its own Edge on a port the core registry believes it owns, tripping identity checks/port refusals in whichever stack loses the race.
7. **Wrong result / consequence:** Intermittent Confluence tool failures, core browser tools refusing to operate, or — worst — the plugin operating and clearing the user's live corporate SSO session; on Windows UAT this presents as unreproducible browser flakiness.
8. **Why nothing else covers it:** Task 3's "cross-process profile/CDP lock with bounded acquisition" is plugin-internal; it cannot see the core registry's claims. No existing test crosses the two stacks.
9. **Smallest correction:** Pick a distinct default port (and profile directory) for the plugin **or** explicitly route the plugin's endpoint ownership through the core seam (`browser_profiles` port claims / `enrolled_endpoint_owner`), and state which in the plan; add the relevant core files to the plan's read list and a coexistence test to Task 3.
10. **Required RED:** A test that configures a core enrolled profile on 9333 and asserts the plugin either (a) selects its distinct default without contention, or (b) registers/consults the core claim registry and refuses with a precise diagnostic — never silently attaches to the enrolled listener.

---

### CONN-PLAN-005 — IMPORTANT — GitLab plan has no legacy behavior-freeze task

1. **ID/severity:** CONN-PLAN-005, Important.
2. **Title:** The GitLab plan never reads a single `loop_24` file and creates no behavior map, unlike all three sibling plans.
3. **Affected:** Release 1. GitLab Tasks 1, 7, 8, 9; design §"GitLab connector → Preserved behavior"; invariant 11.
4. **Plan text vs. evidence:** The plan's only legacy references are prose: Task 7 Step 1 "Port behavior tests from the legacy project resolver, file reader/fetcher, and CI collector" (line 443) and Task 10 "Do not port the legacy `CodeReviewRunner`" (line 607). No `Read:` entry anywhere names a `loop_24` path; Task 1's `gitlab-baseline.md` is defined as a **manifest/vendor drift** document, not a behavior map. Contrast: Jira Task 1 reads `loop_24/custom_components/ericsson_jira/*.py` and creates `jira-behavior-map.md`; SharePoint Task 1 reads `sp_files.py`/`sp_audit.py`/fetcher and creates `sharepoint-behavior-map.md`; Confluence Task 1 reads `confluence_page.py`/fetcher and creates `confluence-behavior-map.md`. The legacy GitLab surface is the largest of the four (9 modules in `custom_components/ericsson_gitlab/` plus GitLab logic in 5 Jira modules), and carries exactly the load-bearing details the port must decide on: 30-char branch slug truncation and `<prefix>/<TICKET-KEY>-<slug>` shape (`gitlab_branch_creator.py`), MR duplicate recovery keyed on **HTTP 409 only** while real GitLab also returns 400 for duplicates (`gitlab_mr_creator.py`), one-level-only CI include resolution that silently drops `remote:`/`template:`/list-valued `file:` and coerces `$`-refs to `main` (`gitlab_cicd_collector.py:_resolve_includes_shallow`), silent mTLS fallback to `~/.config/edpctl/auth/client{,-key}.pem`, `ALL`/`RECENT`(10-day)/exact branch specs, `X-Total` pipeline counting, 80 KB/20-file caps, commit-pusher HEAD pre-flight and create/update coercion, and zero retry/rate-limit handling.
5. **Violated invariant/decision:** Invariant 11 ("Every in-scope LOOP24 operation is explicitly preserved, intentionally adapted, or explicitly excluded with rationale") has no artifact through which it can be checked for GitLab; invariant 20 (tasks name the files they read).
6. **Realistic scenario:** The Task 7/8/9 implementer writes "ported" behavior tests from the design's summary bullets rather than the legacy code, and reasonable-looking choices silently diverge: e.g., duplicate-MR recovery implemented for 409 only (reproducing the legacy blind spot the plan should have consciously adjudicated), or include handling that differs from the CI auditor's downstream expectations.
7. **Wrong result / consequence:** Silent behavior narrowing that no reviewer can detect, because there is no frozen inventory to diff the tests against — precisely the failure class the other three plans' Task 1 exists to prevent, in the release with the largest legacy surface.
8. **Why nothing else covers it:** The design's "Preserved behavior" bullets are summaries, not an inventory (they omit, e.g., the 409-vs-400 nuance, the include-form drops, the `$ref` coercion, and the edpctl cert default); the assessments are explicitly "leads, not proof."
9. **Smallest correction:** Add to GitLab Task 1 (or a Task 1b): `Read:` the nine `custom_components/ericsson_gitlab/*.py` modules and the GitLab-touching Jira modules; `Create: docs/connector-porting/gitlab-behavior-map.md` recording per-behavior disposition (preserve/adapt/exclude + rationale + UAT impact) and the exact legacy SHA; require Task 7/8/9 REDs to cite map entries.
10. **Required verification:** Task 12's review step gains a checklist item: every behavior-map row has a disposition and a covering test or an explicit exclusion.

---

### CONN-PLAN-006 — IMPORTANT — `jira-defect-loop.yml` is planned against a flow classified as Phase-6-dependent, with no stated redesign

1. **ID/severity:** CONN-PLAN-006, Important.
2. **Title:** The Jira plan schedules authoring a defect-loop workflow that the project's own portability assessment says cannot be faithfully expressed without `loop_group`, and the plan does not say what shape it will take instead.
3. **Affected:** Release 2. Jira Task 7 (`Create: workflows/jira-defect-loop.yml`); design §"Jira connector → Preserved behavior" ("Defect triage… through skills and workflows"); invariant 11.
4. **Plan text vs. evidence:** Task 7 requires workflows to use "no unsupported workflow-language features". Evidence: `docs/assessments/loop24-migration/legacy-workflow-portability.md` classifies `Issue_ JIRA Defect Triage` as **Phase 6** ("Needs item iteration…"); `loop_group` is explicitly deferred — `plugins/workflow/language_schema.py:3071-3075` lists it under `later_archon_features`, and `tests/plugins/workflow/test_phase4_includes.py:152` proves the shape is rejected. The legacy flow (19 nodes, `LoopComponent` feedback through selector→branch→commit→MR→review→comment per ticket) was traced in `flows/development/Issue_ JIRA Defect Triage.json`.
5. **Violated invariant/decision:** Invariant 11 — a repackaging that silently narrows the loop to something else without a documented decision; the assessment's own instruction that batch-vs-`loop_group` "is a per-flow decision, not a blanket optimization".
6. **Realistic scenario:** The implementer, forced by the "no unsupported features" test, writes a single-ticket workflow (or an unbounded prompt-driven loop) and names it `jira-defect-loop`. Tests pass; the shipped artifact does not do what the legacy defect loop did, and no document records that the narrowing was chosen, why, or its UAT impact.
7. **Wrong result / consequence:** The Release-2 UAT accepts a "defect loop" that processes one ticket, and the real multi-ticket triage capability silently drops out of the migration with no tombstone.
8. **Why nothing else covers it:** The behavior map (Task 1) records the legacy loop but does not force a workflow-shape decision; Task 7's RED tests check only for the absence of unsupported features.
9. **Smallest correction:** State the redesign in the plan: e.g., "single-ticket `jira-defect-loop` workflow + a defect-triage *skill* that iterates conversationally; the multi-ticket batch port is deferred to Phase 6 `loop_group` and recorded as such in `docs/flows/jira-defect-loop.md` with UAT impact." Any other conscious choice is acceptable — it must just be written and tested.
10. **Required RED/acceptance:** A test asserting `jira-defect-loop.yml` implements the documented shape (e.g., exactly one ticket input contract), and a `docs/flows/jira-defect-loop.md` assertion that the Phase-6 deferral and its consequence are recorded.

---

### CONN-PLAN-007 — MINOR — Phase-0 drift-inventory command under-scopes the vendored surface

1. **ID/severity:** CONN-PLAN-007, Minor.
2. **Title:** GitLab Task 1 Step 3's exact `git ls-files` list misses three vendored surfaces and names a nonexistent path.
3. **Affected:** Release 1 / internal Phase 0; design §"Internal Phase 0" step 1 ("Inventory every source and vendored capability path").
4. **Plan text vs. evidence:** The command lists `capabilities/ericsson.json capabilities/ericsson-vendored-paths.json plugins/ericsson-* skills/ericsson workflows`. Evidence: Hermes has **no** tracked top-level `workflows/` (`git ls-files workflows` → empty), while the vendored inventory (`capabilities/ericsson-vendored-paths.json`) includes `capabilities/mcp-servers.yaml`, `capabilities/workflow-packages/ericsson`, and `plugins/outlook-mcp` — none matched by the command. Real drift the inventory must catch is demonstrable today: the vendored manifest (`capabilities/ericsson.json`) has keys the source manifest lacks (`workflowPackages`, `configDefaults`, `mcpServersFile`, plus `plugins/workflow` in `plugins[]`), and the vendored inventory omits two skills the source manifest lists (`workflow-orchestrator`, `workflow-builder`); `skills/ericsson/confluence-research` exists in Hermes with **no** source owner.
5. **Violated invariant:** Invariant 20 (exact commands); Phase-0 completeness.
6. **Scenario:** The baseline doc is written from the command's output and omits the workflow-package/mcp-servers/outlook drift; a later release trips over unreconciled ownership (e.g., who owns `my-tickets-summary.hermes.yaml`, which exists only vendored).
7. **Consequence:** Phase 0's core promise ("each difference must be classified and documented") is not met for three surfaces.
8. **Not covered elsewhere:** Task 6's vendor-script work touches mechanics, not the ownership classification.
9. **Smallest correction:** Replace the path list with: every entry of `capabilities/ericsson-vendored-paths.json` plus `capabilities/*.json`, `capabilities/mcp-servers.yaml`, `capabilities/workflow-packages/`, `plugins/outlook-mcp`, `skills/ericsson` (and drop the dead `workflows` token); require the baseline doc to cover every key of **both** manifests.
10. **Verification:** Baseline doc review checklist: one row per vendored-inventory entry and per manifest key, each with a disposition.

---

### CONN-PLAN-008 — MINOR — `sp_audit.py` audit collection has no planned disposition

1. **ID/severity:** CONN-PLAN-008, Minor.
2. **Title:** The SharePoint plan reads `sp_audit.py` but its behavior-map checklist never assigns a disposition to the audit commands.
3. **Affected:** Release 3, SharePoint Task 1 Step 2; invariant 11.
4. **Plan text vs. evidence:** Task 1 Step 2 enumerates URL shapes, CRUD, auth order, pagination, batching, async copy — file-transfer behavior only. Evidence: `utils/sp_audit.py` (985 lines) implements `collect-my-sites` (Graph) and `collect-users` (SharePoint REST executed **inside a signed-in Edge tab over CDP :9222**), consumed via `sp_audit.ps1` by the PowerShell-runner component; it also owns the credential chain `sp_files.py` imports (`get_credential`: shared MSAL cache → `AzureCliCredential` → interactive MSAL, `SP_NONINTERACTIVE` gate).
5. **Violated invariant:** Invariant 11 — legacy behavior with no planned disposition.
6. **Scenario:** The behavior map ships covering `sp_files.py` only; months later someone asks where site/user audit collection went and there is no recorded exclusion or rationale.
7. **Consequence:** An in-scope-looking legacy capability disappears without a tombstone; the review prompt explicitly names "audit utilities" as required inspection.
8. **Not covered elsewhere:** Design non-goals do not mention audit collection.
9. **Smallest correction:** Add "audit collection (`collect-my-sites`, `collect-users`) disposition" to Task 1 Step 2's required map contents — most plausibly "deliberately excluded; rationale; UAT impact", since no planned tool covers it.
10. **Verification:** Behavior-map review checklist row.

---

### CONN-PLAN-009 — MINOR — "relevant Teams Graph tests discovered by `rg`" placeholder

1. **ID/severity:** CONN-PLAN-009, Minor.
2. **Title:** SharePoint Task 2's file list includes a discover-at-execution placeholder the plans' own standard disallows.
3. **Affected:** Release 3, SharePoint Task 2.
4. **Plan text vs. evidence:** "Modify: relevant Teams Graph tests discovered by `rg -n "MicrosoftGraph|graph_auth" tests plugins`". The named concrete candidates exist and are enumerable today: `tests/tools/test_microsoft_graph_auth.py`, `tests/tools/test_microsoft_graph_client.py`, `tests/agent/test_azure_identity_adapter.py`, `plugins/platforms/teams/adapter.py` consumers, `plugins/teams_pipeline/*`.
5. **Violated invariant:** Invariant 20 ("placeholders… eliminated or bounded by an explicit stop and review rule").
6. **Scenario:** Staging scope drifts — the `rg` net catches `plugins/teams_pipeline` files the task has no authority over, or misses a consumer.
7. **Consequence:** Either over-broad commits or missed Teams regression coverage.
8. **Not covered elsewhere:** Task 2's commit list is explicit but the modify list is not.
9. **Smallest correction:** Enumerate the files now (they are stable), or add "stop and update this reviewed file list before editing any file the search surfaces".
10. **Verification:** Commit staging list equals the enumerated set.

---

### CONN-PLAN-010 — MINOR — Behavior maps never pin the legacy SHA; Ericsson docs pin a stale snapshot

1. **ID/severity:** CONN-PLAN-010, Minor.
2. **Title:** No plan records which `loop_24` commit its behavior map traced, and the Ericsson repo's documented pin is two generations stale.
3. **Affected:** All releases; `ericsson-capabilities/AGENTS.md:98-100` and `docs/README.md` pin `3f124f5` and claim "11 JSON flows", while the migration assessments used `8ca26f8` (30 active flows) and the live checkout is `fc3bf26` (verified: `3f124f5` and `8ca26f8` are both ancestors of `fc3bf26`; `8ca26f8..fc3bf26` is one docgen-only commit).
4. **Plan text vs. evidence:** The behavior-map tasks (Jira T1, SharePoint T1, Confluence T1) specify contents but not a legacy-SHA provenance field; GitLab has no map at all (CONN-PLAN-005). Phase 0's "Reconcile source documentation and manifest versions" and GitLab Task 2's `AGENTS.md` edit are scoped to the backend-plugin claim, not the flow-inventory pin.
5. **Violated invariant:** Documentation-requirements section of the design ("LOOP24 flow-port status pages") + reproducibility of invariant-11 checks.
6. **Scenario:** The legacy repo moves during the four-release program; a Release-3 reviewer cannot tell which snapshot the SharePoint map froze.
7. **Consequence:** Behavior parity becomes unauditable over time; the stale "11 flows" claim misleads future maintainers.
8. **Not covered elsewhere:** No task owns the docs' snapshot line.
9. **Smallest correction:** Each behavior map records the exact `loop_24` SHA traced; GitLab Task 2's `AGENTS.md`/docs edit also refreshes the snapshot pin and flow count.
10. **Verification:** Map header field + docs diff in the same commit.

---

## 4. Design-requirement → plan traceability matrix

| Design requirement (section) | Plan tasks | Coverage |
| --- | --- | --- |
| Phase 0: inventory / ownership / manifest reconcile / staging change / old-manifest proof / byte checks | GitLab T1, T2, T6, T13 | **Partial** — inventory command under-scoped (CONN-PLAN-007); otherwise mapped |
| Static configuration descriptor (parse, fail-closed, storage classes) | GitLab T3 | Complete |
| Profile settings + write-only secrets + readiness + setup actions | GitLab T4 | Complete (builds on real `plugins.entries`, `save_env_value*`, `is_set` patterns — verified present) |
| Backend HTTP APIs + Desktop panel, backend-authoritative | GitLab T5 | Complete (no backend-plugin config UI exists today — genuinely new, verified) |
| Disabled-by-default staging preserving explicit choices | GitLab T6 | Complete (current auto-enable confirmed at `capability_staging.py:643-648, 819-825`) |
| GitLab client/reads/CI/writes/skills/workflows | GitLab T7–T10 | **Partial** — no legacy freeze (CONN-PLAN-005); workflow contract (CONN-PLAN-001) |
| Cross-surface + installed distribution proof | GitLab T11, T13 | **Partial** — admission bullet unimplementable as written (CONN-PLAN-001); skills-discovery untested (CONN-PLAN-003) |
| Jira standalone migration + auth/transport/curl/search/comments | Jira T1–T6 | Complete (RED premises verified: current plugin is `kind: backend`, v2-only, env-configured, no curl, no ADF) |
| Jira skills/workflows/migration docs | Jira T7, T9 | **Partial** — defect-loop shape (CONN-PLAN-006); workflow contract (CONN-PLAN-001) |
| Generic Graph delegated identity + transfer primitives, Teams invariant | SharePoint T2–T3 | Complete (core module confirmed app-only-only; upload-session/async-poll confirmed absent; Teams platform + `teams_pipeline` consumers identified) |
| SharePoint plugin config/resolution/reads/writes/boundary | SharePoint T4–T7 | Complete |
| SharePoint skills/workflows/docs | SharePoint T8 | **Partial** — CONN-PLAN-001, -003 |
| Confluence source reconcile + library extraction + parity | Confluence T1–T2 | Complete (skill genuinely absent from source repo; sync/INDEX/manifest features confirmed in the accepted skill) |
| Confluence browser isolation/config/setup actions | Confluence T3 | **Partial** — port-authority collision (CONN-PLAN-004) |
| Confluence tools/skills/router/workflow | Confluence T4–T6 | **Partial** — CONN-PLAN-001 (workflow); router mitigates CONN-PLAN-003 for this connector |
| Vendoring, ledger, closure, release, UAT, rollback (all releases) | GitLab T12–T14, Jira T8–T10, SP T9–T11, Conf T7–T9 | **Partial** — vendor command (CONN-PLAN-002); otherwise mapped |
| Data protection/evidence rules | Distributed across all RED lists | Complete (as plan requirements) |
| Documentation requirements | T10/T7/T8/T6 + flow docs | **Partial** — snapshot pin (CONN-PLAN-010) |

**Orphan design requirements:** none found — every design section maps to at least one task.
**Plan work lacking design authority:** none found — no task exceeds the design's scope. (The *missing* authority runs the other way: the workflow-admission seam needed by CONN-PLAN-001 has no design authorization yet.)

---

## 5. All-plan task coverage matrix

| Plan | Task | Coverage | Note |
| --- | --- | --- | --- |
| GitLab | 1 | **partial** | Drift inventory under-scoped (007); no legacy reads (005) |
| GitLab | 2 | complete | Manifest premises verified (string entries, linter, `kind: backend` claim in AGENTS.md) |
| GitLab | 3 | complete | All named files exist; RED premise (no descriptor mechanism) verified locally and upstream |
| GitLab | 4 | complete | Storage/secret/CLI seams verified real |
| GitLab | 5 | complete | Router pattern, desktop files, npm scripts verified |
| GitLab | 6 | complete | Auto-enable RED premise verified; note `plugins/workflow` must remain auto-enabled (see §16) |
| GitLab | 7–9 | **partial** | Sound structure; legacy grounding missing (005) |
| GitLab | 10 | **contradicted** | `requires.toolsets` (001); skills discovery (003) |
| GitLab | 11 | **contradicted** | Admission bullet cannot pass through T3–T6 as asserted (001) |
| GitLab | 12 | complete | |
| GitLab | 13 | **partial** | Vendor env var (002) |
| GitLab | 14 | complete | Release/UAT/stop structure sound; paired-brand rule honored |
| Jira | 1 | complete | Legacy paths exist and were verified |
| Jira | 2 | complete | RED premises verified (`kind: backend`, two env requirements) |
| Jira | 3–5 | complete | v2-only/env-global RED verified; fixtures grounded |
| Jira | 4 (curl) | complete | Legacy Cloudflare/JA3 rationale verified verbatim in `jira_assigned_tickets_fetcher.py:143-148`; auto-classifier definition left open (§16) |
| Jira | 6 | complete | |
| Jira | 7 | **partial** | Defect-loop shape (006); workflow contract (001) |
| Jira | 8 | complete | |
| Jira | 9 | **partial** | Vendor env var (002); migration-policy semantics open (§16) |
| Jira | 10 | complete | |
| SharePoint | 1 | **partial** | `sp_audit` disposition (008) |
| SharePoint | 2 | **partial** | Placeholder (009); otherwise verified (app-only-only core confirmed) |
| SharePoint | 3 | complete | Upload-session/async-poll genuinely absent — additive |
| SharePoint | 4–7 | complete | Legacy URL/upload/recycle facts verified in `sp_files.py` |
| SharePoint | 8 | **partial** | 001, 003 |
| SharePoint | 9 | complete | |
| SharePoint | 10 | **partial** | Vendor env var (002) |
| SharePoint | 11 | complete | |
| Confluence | 1 | complete | Skill absent from source repo — RED verified |
| Confluence | 2 | complete | Library parity fixtures grounded in the accepted skill (INDEX/.manifest/version-skip verified) |
| Confluence | 3 | **contradicted** | Port 9333 default (004) |
| Confluence | 4–5 | complete | |
| Confluence | 6 | **partial** | 001 (workflow); router handles discovery for this connector |
| Confluence | 7 | complete | |
| Confluence | 8 | **partial** | Vendor env var (002) |
| Confluence | 9 | complete | Honest unattended-UAT step is a strength |

---

## 6. Legacy behavior parity matrix

Dispositions verified against actual legacy code (`loop_24` @ `fc3bf26`; drift from the assessment baseline is docgen-only).

### GitLab (legacy: `custom_components/ericsson_gitlab/` — 9 modules; GitLab logic in 5 Jira modules)

| Legacy behavior | Planned disposition | Assessment |
| --- | --- | --- |
| Project resolve from Jira URL / nested namespace / numeric id | Preserve (`gitlab_resolve_project`) | Planned; **untraced** (005) |
| Tree walk + bounded base64 file reads (80 KB / 20 files caps) | Preserve with bounds | Planned; caps not cited (005) |
| Slashed-ref resolution (longest-match against branches+tags) | Preserve | Planned (T7 RED "slash-containing refs") |
| Branch create: slug ≤30 chars, `<prefix>/<KEY>-<slug>`, GET-then-reuse idempotency | Preserve | Planned; exact slug contract untraced (005) |
| Atomic multi-action commit + per-file HEAD pre-flight create/update coercion | Preserve + optimistic checks | Planned (T9) |
| MR create; duplicate recovery on **409 only** (legacy blind spot: GitLab also returns 400) | Preserve + reconcile | Planned; the 400 case needs the behavior map to adjudicate (005) |
| MR diff fetch for review | Preserve (`gitlab_read_merge_request`) | Planned |
| CI collect: pipelines (`X-Total`), branch specs ALL/RECENT(10d)/exact, `.gitlab-ci.yml`, **one-level** includes (drops `remote:`/`template:`/list `file:`, `$ref`→`main`), variable **metadata only**, ancestor-group walk | Preserve within explicit bounds (`gitlab_inspect_ci`) | Planned; include-form drops and `$ref` coercion need explicit disposition (005) |
| mTLS: silent default `~/.config/edpctl/auth/client{,-key}.pem` | "optional mTLS/CA settings" | Planned; default-path behavior needs a decision (005) |
| Embedded Ollama LLM review (`CodeReviewRunner`) + triage prompts | **Move to skills** (explicitly not ported) | Correctly excluded per approved architecture |
| No retry/rate-limit handling anywhere | **Deliberately adapted** (bounded retries added) | Correct adaptation |

### Jira (legacy: `custom_components/ericsson_jira/`; current source plugin: 3 tools, v2, env-config)

| Legacy/current behavior | Planned disposition | Assessment |
| --- | --- | --- |
| Bearer PAT + basic email/token | Preserve | Planned + tested (T3) |
| v3→v2 search fallback (legacy); v2-only current plugin | Preserve as classified fallback | Planned; classifier definition open (§16) |
| curl transport for Cloudflare JA3/1010 (legacy comment verified verbatim); Windows `cp1252` workaround; `shutil.which("curl")` | Preserve as bounded compat transport; secrets out of argv (legacy passed headers on argv — deliberately hardened) | Planned (T4) — a conscious, documented improvement |
| ADF recursive flatten; current plugin JSON-dumps dicts | Preserve/normalize properly | Planned (T5, `test_jira_adf.py`) |
| GitLab URL regex + punctuation cleanup + dedupe | Preserve | Planned |
| Fixed assigned/unresolved JQL; filters (types/priorities/labels/thresholds) | Preserve + `jira_search_issues` | Planned |
| Triage LLM + fix-summary composer + updater comment (loop-safe, never raises) | Move reasoning to skills; keep deterministic comment write | Planned (T6/T7); defect-loop shape open (006) |
| Legacy updater latent bugs (SecretStr unwrap missing; malformed UA header) | Not preserved (rewrite) | Correct — bugs need not be preserved |

### SharePoint (legacy: `utils/sp_files.py`, `sp_audit.py`, `sharepoint_files_fetcher.py`)

| Legacy behavior | Planned disposition | Assessment |
| --- | --- | --- |
| UI URL parsing incl. `/:w:/r/`-style mode segments, sites/teams prefixes, default-library aliases | Preserve (`sharepoint_resolve_url`) | Planned + grounded (T5 cites `sp_files.py` cases) |
| Credential chain: shared MSAL cache → Azure CLI → interactive (no app-only); `SP_NONINTERACTIVE` | Superseded by generic Graph modes incl. app-only (from Hermes core) | Planned adaptation, coherent |
| 4 MiB simple / 10 MiB (320 KiB-aligned) chunked upload sessions | Preserve via generic primitives | Planned (T3/T7) |
| Async copy: POST + monitor URL, **no polling** | Adapted: add polling | Planned — an improvement, correctly framed |
| Recycle-bin delete, `--yes` gate, library-root refusal | Preserve recycle semantics | Planned (decision 15) |
| Retry on 429/503 with Retry-After | Preserve (already in core client) | Verified present in `microsoft_graph_client.py` |
| Bearer-not-forwarded-to-CDN download nuance | Streaming download via core client | Core client streams; nuance worth a map row |
| Recursive fetch/filters/anchor-file discovery (fetcher) | Bounded recursion planned; anchor-file discovery **not named** | Map must dispose of anchor-file/template discovery explicitly |
| `sp_audit` site/user audit collection (browser CDP :9222) | **No disposition** | Gap (008) |

### Confluence (legacy: `utils/confluence_page.py` + fetcher; accepted baseline: `skills/ericsson/confluence-research`)

| Behavior | Planned disposition | Assessment |
| --- | --- | --- |
| Same-origin `fetch()` in signed-in Edge over CDP; no cookies in Python | Preserve | Planned; port authority issue (004) |
| Cloud/DC REST-root derivation; URL/id/space+title targeting; batch CSV | Preserve (tools) | Planned |
| Legacy: hardcoded Edge path, port 9222, `DETACHED_PROCESS`, Windows-only | Superseded by the accepted skill's discovery + 9333 + engines | Correct baseline choice (skill > legacy); collision remains (004) |
| Sync/mirror (INDEX.md, `.manifest.json`, version-skip, `fetched: 0`) — **absent in legacy**, present in accepted skill | Preserve from skill | Verified in skill scripts; parity fixtures grounded |
| Attachments metadata/download bounds | Preserve | Planned |
| Write operations | Excluded (read-only release) | Explicit non-goal, tested ("No Confluence write tools exist") |
| Unattended enrolled-session reuse | Honestly gated (`interactive_session_required` until UAT proves it) | Exemplary honesty; keep |

---

## 7. Plugin-architecture compliance matrix

Traced through current production code:

| Lifecycle step | Current mechanism (verified) | Plan compliance |
| --- | --- | --- |
| 1. Source manifest + vendoring | `sets/ericsson.json` (string entries) → `vendor-ericsson.mjs` (manifest-driven, committed-index snapshot, managed destinations) | Compliant; command bug (002) |
| 2. Installed plugin discovery | `PluginManager._discover_and_load_inner` (`plugins.py:1636-1795`); kinds `{standalone, backend, exclusive, platform, model-provider}` | Compliant — `kind: standalone` is real |
| 3. Disabled static catalog projection | Manifests parsed without import; disabled/not-enabled plugins never imported (`plugins.py:1709-1714, 1773-1788`) | Compliant; descriptor mechanism is a genuine, well-shaped addition |
| 4. Configuration descriptor parsing | Does not exist (verified locally + upstream docs) | New generic module planned (T3) — correct placement |
| 5. Profile settings + credential persistence | Profile = `HERMES_HOME`; `plugins.entries.<id>` exists; `.env` secret store with `is_set` projection (`web_server.py:7500-7530`), `save_env_value_secure`, per-toolset env allowlists (`web_routers/tools.py:557-609`) | Compliant — extends real patterns |
| 6. Enable/disable precedence | `plugins.disabled` wins (checked first); `plugins.enabled` opt-in; staging currently auto-unions enabled (`capability_staging.py:643-648, 819-825`) | Plan T6's RED premise verified genuine |
| 7. Import + `PluginContext` registration | `register(ctx)`; `register_tool/skill/cli_command/command/hook` exist; **no `register_setup_action`** (verified) | New ctx method planned — correct seam per the repo's own rule ("expand the generic plugin surface") |
| 8. Toolset/schema construction | `register_tool(toolset=…)` → registry → `_get_platform_tools` folds plugin toolsets in (default-enabled unless known-absent) | Compliant — no core edits needed for tools to appear |
| 9. Plugin-owned skill registration | `register_skill` exists but is index-invisible | **Gap** (003) |
| 10. Fresh agent/session construction | Toolsets snapshotted at `AIAgent` construction (`agent/agent_init.py:851`); config changes land next session | Compliant; supports prompt-cache invariant |
| 11. Readiness + setup actions | No existing generic readiness/setup surface for backend plugins (desktop plugin UI is Electron-plugins-only — verified) | New, correctly generic |
| 12. Disable/re-enable + upgrade | `hermes plugins enable/disable`; staging marker patterns exist (`.brand-defaults-seeded.json` precedent) | Compliant |
| 13. Installed package lookup | `test_installed_distribution_e2e.py` exists; plans extend it | Compliant |

Connector-neutrality: no plan introduces connector ids into core production files; the plans repeatedly forbid it and route everything through manifest metadata — consistent with the "no plugin-specific logic in core" rule in `AGENTS.md`.

---

## 8. Per-connector client-surface matrix

Mechanisms verified: interactive CLI (`cli_agent_setup_mixin` → `_get_platform_tools(cfg,"cli")`), TUI/Desktop gateway (`tui_gateway/server.py:4188, 6446`), messaging gateway (`gateway/run.py:19450, 24248`), Kanban worker (re-spawn `hermes -p <assignee> chat -q` with `HERMES_HOME` repointed and toolsets pinned from the assignee profile — `kanban_db.py:9166-9361`), cron (in-process, ambient scheduler profile, `disabled = ["cronjob","messaging","clarify"]` — `cron/scheduler.py:160-181, 3489-3521`), workflows (separate `allowed_tools` lane via `PluginAgentRunRequest`).

Ratings apply to all four connectors unless noted:

| Surface | Verdict | Notes |
| --- | --- | --- |
| Interactive CLI | **planned and evidenced** | Discover/configure/enable/status/auth verbs planned on real parsers; readiness diagnostics planned (T4) |
| TUI/dashboard chat | **planned and evidenced** | Same agent path; no parallel client |
| Electron Desktop | **planned and evidenced** | New backend-authoritative panel; no independent resolution (matches desktop authority rules) |
| Gateway/API chat | **planned but weak** | Same construction path exists; plans never name a gateway-specific test — covered only implicitly by `_get_platform_tools` genericity |
| Kanban | **planned and evidenced** | Executing-profile inheritance is real and already regression-guarded (`test_kanban_worker_spawn_toolsets.py`) |
| Cron | **planned but weak** | "Cron uses its configured profile" (T11) — mechanism is the *scheduler process's ambient profile*, not per-job profile selection; the planned test must assert the real mechanism. Interactive-toolset exclusion is real |
| Archon workflow | **missing (as written)** | CONN-PLAN-001 — declaration key nonexistent; production admission availability-blind |
| Installed brand | **planned and evidenced** | `test_installed_distribution_e2e` + paired-brand release gates + Windows UAT; branded-byte verification at release steps |
| Confluence-specific: unattended surfaces | **planned and evidenced** | `interactive_session_required` honesty gate is explicit and UAT-verified |
| Skills discovery in chat (all connectors) | **missing** | CONN-PLAN-003 (Confluence: mitigated via router) |

Lifecycle cases (present-disabled, disabled-configured, enabled-incomplete, enabled-ready, change-during-conversation, fresh-after-change, restart/upgrade, profile switch, rollback): all appear in the plans' RED lists or UAT matrices; the fresh-session mechanism they rely on is real (toolset snapshot at agent construction).

---

## 9. Verdicts on the twenty non-negotiable invariants

| # | Invariant | Verdict |
| --- | --- | --- |
| 1 | One source owner | **Holds as planned** (Phase 0 + reconciliation tasks), with the inventory-scope defect (007) and the currently-unowned `capabilities/workflow-packages/ericsson` + `confluence-research` drift that Phase 0 must classify |
| 2 | Plugin architecture is genuine | **Partially violated as written** — the plans' plugin/config/staging architecture is genuine and well-grounded, but the workflow declaration interface they mandate does not exist (001) |
| 3 | Disabled means absent | **Holds** — verified: disabled/not-enabled plugins are never imported; descriptor display is data-only |
| 4 | Enablement is exact | **Holds as planned** — staging auto-enable premise verified; migration policy semantics need pinning (§16) |
| 5 | Configuration has one authority | **Holds as planned** — extends real profile/`.env`/`is_set` seams; Desktop stays a projection |
| 6 | The core remains narrow | **Holds** — generic-only core changes, consistent with the footprint ladder |
| 7 | Prompt caching remains stable | **Holds** — fresh-session snapshot mechanism verified |
| 8 | Skills and tools stay separate | **Holds in design; discovery gap** (003) undermines the skills half in practice |
| 9 | Every execution surface uses the same plugin | **Holds** for chat/desktop/gateway/kanban/cron; **fails as written** for workflows (001) |
| 10 | Workflow declarations are exact | **Violated as written** (001) — `requires.toolsets` does not exist; `allowed_tools: []` deny-all meaning IS real and preserved (verified `executors/ai.py:1456-1460`, `compat.py:751-763`) |
| 11 | Legacy behavior is accounted for | **At risk** — GitLab has no freeze artifact (005); `sp_audit` (008); defect-loop (006) |
| 12 | Writes are deliberate | **Holds as planned** — host-supplied approval facts, no caller-authored `approved: true`, dry-runs |
| 13 | Retries do not change semantics | **Holds as planned** — no-ambiguous-write-retry appears in every write RED list |
| 14 | Cancellation reaches the real work | **Holds as planned** — planned per transport (curl child kill, Graph deadlines, browser lifecycle) |
| 15 | Performance is bounded | **Holds as planned** — bounds named per connector; enforcement-layer naming is uneven (§11) |
| 16 | Windows is first-class | **Holds as planned** — curl discovery, Edge, installed UAT; grounded in real legacy Windows evidence |
| 17 | Packaging is real | **Holds as planned** — installed-wheel/distribution tests + branded builds |
| 18 | Upstream preservation is explicit | **Holds as planned** — ledger entries + `check_upstream_customizations.py` + merge rehearsal in every closure; the 001 correction will need its own entry |
| 19 | Releases are independent | **Holds** — gates, patch versions, rollback, no forward dependencies found |
| 20 | Plans are executable | **Violated in spots** — vendor command (002), placeholder (009), GitLab legacy reads (005); otherwise the file/command audit came back clean (§12) |

---

## 10. Verdicts on the eighteen specific plan premises

| # | Premise | Verdict |
| --- | --- | --- |
| 1 | Disabled plugin exposes static config metadata without import | **supported** — manifests already parse without import; descriptor is data-only; fail-closed rules planned |
| 2 | One generic descriptor serves CLI and Desktop without a second framework | **supported** — backend-authoritative APIs over existing router/`is_set` patterns |
| 3 | Existing profile config/credential helpers suffice | **supported** — profile=HERMES_HOME, `plugins.entries`, `save_env_value_secure`, `_profile_scope` all verified |
| 4 | `PluginContext` supports setup actions + skill/tool registration with minimal extension | **supported** — context exists; `register_setup_action` is a pattern-consistent addition |
| 5 | Same registrations reach chat, Desktop, gateway, Kanban, cron, workflows | **supported for six of seven** — chat/TUI/Desktop/gateway/Kanban/cron verified through `_get_platform_tools` + registry; the workflow lane is availability-blind at admission (001) |
| 6 | Fresh-session behavior preserves prompt caching | **supported** — toolset snapshot at agent construction; `cache_fingerprint_stable` machinery |
| 7 | Source manifests can express disabled standalone capabilities without breaking Jira/Teams | **supported** — string entries retained, object form additive; caveat: the generic staging change must not stop auto-enabling `plugins/workflow` (see §16) |
| 8 | Current vendoring can carry descriptors and remove stale managed files | **supported** — `managedDestinations`/`reconcileManagedPaths` exist; committed-index snapshot prevents dirty-tree leakage |
| 9 | Direct GitLab REST covers legacy in-scope behavior | **supported** — legacy is 100 % REST v4 via `requests`; no `glab`, no `git` subprocess anywhere (verified by repo-wide search) |
| 10 | Jira curl design preserves the proven Cloudflare case without a second result model | **insufficiently established** — the legacy JA3/1010 rationale is real and verbatim; but legacy used curl *unconditionally* for search, and the plans' `auto` mode depends on an unspecified classifier for "the known Cloudflare/TLS-fingerprint failure" observed from native httpx. Define the classifier (status + `cf-ray`/error-1010 signature) before implementation; UAT step 5 is the backstop |
| 11 | v3-first/v2-fallback preserves Cloud and Server/DC | **supported** — legacy already probes v3→v2 for search; plan adds classification discipline; comment-write v3 ADF/v2 plain shapes are tested |
| 12 | Generic Graph identity/transfer extensions suffice without regressing Teams | **supported** — core module is app-only-only (verified), consumed by the gateway Teams platform and `teams_pipeline`; the Ericsson Teams plugin's separate MSAL device-code stack is untouched; upload-session/async-poll are genuinely absent so additions are additive |
| 13 | SharePoint tool boundary suffices for later document-generation skills | **supported** — parsing/generation explicitly out of connector scope, matching the assessment's ownership table |
| 14 | The Confluence skill decomposes into one library without behavioral loss | **supported** — the skill's scripts are already modular (`backends/confluence_api/artifacts/storage_to_md`); sync/INDEX/manifest features verified present to fixture against |
| 15 | A dedicated enrolled browser session can be operated from Desktop with explicit unattended failure | **insufficiently established** — the attached-mode mechanism is proven by the accepted skill and the unattended honesty gate is excellent, but the "dedicated" isolation claim collides with the core enrolled-port authority at the chosen default (004) |
| 16 | Four sequential releases with mandatory Windows UAT isolate failures | **supported** — independent gates, patch-version policy, per-release rollback, regression smoke |
| 17 | Proposed test commands cover installed branded behavior | **supported** — `test_installed_distribution_e2e` + paired-brand builds at exact SHAs + installed Windows UAT; residual: branded-bundle content checks live in the release steps/UAT rather than automated suites |
| 18 | Upstream customization records and merge rehearsal cover every generic change | **supported** — new ledger files planned (plugin-configuration, microsoft-graph-connectors) and `check_upstream_customizations.py` + `scripts/test_workflow_upstream_merge.sh` (both exist) run in every closure; the 001 correction must be added to this coverage |

---

## 11. Connector-specific edge-case and performance coverage assessment

- **GitLab:** pagination/truncation/empty-repo/binary/oversize cases are in T7–T8 RED lists; rate limiting and retry bounds are named (an *adaptation* — legacy had none, and the plan correctly adds them). Not yet named anywhere: the enforcement layer and defaults for tree-recursion caps and include-depth bounds ("explicit bounds" is asserted, the numbers/config keys are not). The legacy include-form drops (`remote:`, `template:`) need an explicit disposition.
- **Jira:** ADF unknown/malformed nodes, pagination, deleted users, empty results, and no-ambiguous-write-retry are covered. The curl transport's bounds (output caps, deadline, child-kill) are the strongest-specified section of any plan. Gap: the `auto` classifier (premise 10).
- **SharePoint:** chunk alignment, resume offsets, expired sessions, ambiguous completion, bounded chunk count, `.part` cleanup, Retry-After, cancellation — comprehensively listed; the plan correctly requires no-blind-restart after ambiguous completion. Gap: no explicit test that recursion/aggregate byte ceilings are enforced *before* work starts (order of enforcement unstated).
- **Confluence:** enumerate-before-materialize, page-count thresholds, per-file and aggregate limits, second-unchanged-sync `fetched: 0`, partial-failure visibility, atomic manifest — all present and grounded in the real skill. Gap: concurrent-operation behavior when two sessions target the same browser profile/port (subsumed by 004).
- **Cross-cutting:** retry-multiplication across plugin/worker/cron/workflow layers is asserted as an invariant in the design but no plan names a test for it; recommend one explicit test per write tool (retry budget observed end-to-end through a worker).
- Performance thresholds: the plans bound *sizes and counts* well; none names a latency/throughput threshold. Acceptable for this scope, but "realistic performance thresholds rather than tests that merely complete" is only partially met.

---

## 12. Strict-TDD command/file/ordering audit

**Existence audit — every existing file named by the plans was checked; results:**

- Hermes: all named production files exist (`hermes_cli/{plugins,plugins_cmd,config,main,tools_config,web_models,web_server}.py`, `hermes_cli/subcommands/tools.py`, `hermes_cli/web_routers/` pattern, `capability_staging.py`, `scripts/vendor-ericsson.mjs` + test, `tools/microsoft_graph_{auth,client}.py`, `agent/azure_identity_adapter.py`). All named existing test files exist (`test_plugins.py`, `test_plugins_cmd.py`, `test_plugins_cmd_list.py`, `test_plugin_cli_registration.py`, `test_tools_config.py`, `test_capability_staging.py`, `test_startup_plugin_gating.py`, `test_kanban_worker_spawn_toolsets.py`, `tests/cron/test_cron_profile_isolation.py`, `tests/plugins/workflow/test_installed_distribution_e2e.py`, `tests/tools/test_microsoft_graph_{auth,client}.py`, `tests/agent/test_azure_identity_adapter.py`). Desktop files exist (`toolset-config-panel.tsx` + test, `src/hermes.ts`, `src/types/hermes.ts`, `settings/index.tsx`, `i18n/{types,en}.ts`).
- Ericsson source: `sets/ericsson.json`, `scripts/lint_manifest.py`, `tests/test_{manifest,jira_plugin,teams_plugin,onboarding_catalog,reference_workflows}.py`, `plugins/ericsson-jira/{__init__,jira_tools,plugin.yaml}`, `workflows/my-tickets-summary.yml`, all four named `docs/flows/*.md`, `docs/{configuration,README}.md`, `build_catalog.py` (supports `--check`), `validate_catalog.py` — all exist. `.venv/bin/python` exists (3.13.13) with pytest 9.1.1 + httpx/respx/msal/jsonschema installed; the plans' `.venv/bin/python -m pytest … -q` invocations are runnable as written. `docs/connector-porting/` does not exist (all four plans create it — consistent).
- Legacy: every `loop_24` path named by Jira/SharePoint/Confluence Task 1 exists.
- Commands: `scripts/run_tests.sh` ✔, `scripts/check_upstream_customizations.py` ✔, `scripts/test_workflow_upstream_merge.sh` ✔ (distinct from `test_workflow_merge_gate.sh`, also present), `npm --workspace apps/desktop run {test:ui,typecheck,lint}` ✔ (scripts verified in `apps/desktop/package.json`; root `package.json` declares `apps/*` workspaces).

**Defects found:**

1. Vendor env var wrong in all four plans (CONN-PLAN-002).
2. GitLab plan has no legacy `Read:` entries (CONN-PLAN-005).
3. GitLab T1 Step 3's `ls-files` path list includes nonexistent `workflows` and omits three vendored surfaces (CONN-PLAN-007).
4. SharePoint T2 placeholder (CONN-PLAN-009).
5. GitLab T10/Jira T7/SharePoint T8/Confluence T6 RED contracts embed the nonexistent `requires.toolsets` (CONN-PLAN-001) — RED tests written to that contract would freeze a wrong interface.

**Ordering/RED-GREEN discipline:** otherwise sound. REDs precede GREENs with stated failure reasons that I verified are genuine (descriptor mechanism absent; setup actions absent; staging auto-enables — `capability_staging.py:643-648, 819-825`; Jira plugin `kind: backend`/two env vars; Graph app-only-only; upload-session/async-poll absent; Confluence skill absent from source). Staging scopes list task-owned files and exclude the preserved dirty files. Generated files (catalog.json, vendored manifests) are regenerated-and-checked, not hand-edited. Full-suite/Desktop/merge-rehearsal/brand gates sit at release boundaries. Release/push/merge steps require separate authorization. No plan uses literal `main`. No forward dependency on an unimplemented later-task helper was found (the Jira/SharePoint/Confluence plans correctly consume only the GitLab-release foundation).

**Change-detector check:** the plans generally demand relational assertions; no test freezing counts/versions is mandated. GitLab T2's "exactly one entry per plugin path" is a uniqueness relation, not a count — acceptable.

---

## 13. Packaging, vendoring, upstream preservation, release, rollback, Windows UAT

- **Source-first:** every plan authors in `ericsson-capabilities` worktrees, closes with a clean committed SHA, then vendors — compliant with the workspace's branch-placement invariant. The vendor script's committed-index snapshot prevents dirty-file leakage (and therefore also protects the two preserved user files).
- **Vendoring:** mechanism supports managed-path reconciliation; Confluence T8 correctly removes stale vendored transport through the inventory, not manual deletion. Blocking defect: the command itself (002).
- **Upstream preservation:** ledger entries planned per release; `check_upstream_customizations.py` and the merge-rehearsal script are real and wired into every closure. The 001 correction adds a new generic seam that must join this coverage.
- **Release independence/rollback:** paired-brand dispatch at exact SHAs matches the workspace's release runbook (correct repos `cmetech/otto`/`cmetech/loop24`, `prerelease=false`, brand discovery from `brands/*.json`); patch-version policy present; per-release operational rollback (disable plugin, retain config, preserve snapshots) is consistent with verified enable/disable semantics; every release ends by returning the checkout to clean `base`.
- **Windows UAT:** each release has a concrete, numbered UAT matrix including negative checks (no variable values, no tokens in logs, no write tools for Confluence). The Confluence unattended-UAT honesty rule (report truthfully; do not weaken Conditional Access to pass) is exactly right and should be kept verbatim.

---

## 14. What was verified complete (and why)

- **Hash and state discipline:** all five inputs hash-matched; all three repos matched expectations; the only unexpected state (legacy one-commit drift) was traced and shown out of scope.
- **The disabled-by-default foundation premise:** current staging genuinely auto-enables manifest plugins on every startup (two code paths), and `plugins.disabled` genuinely wins — the plans' central Phase-0 change is aimed at a real, verified behavior.
- **The descriptor/setup-action novelty:** verified absent locally *and* in upstream docs — the plans correctly treat these as new generic interfaces rather than assuming them.
- **Surface construction paths:** interactive CLI, TUI/Desktop gateway, messaging gateway, Kanban worker (profile re-spawn with pinned toolsets), and cron all resolve toolsets through one production choke point (`_get_platform_tools`) that folds in plugin toolsets — the "one implementation, every surface" decision is architecturally real for six of seven surfaces.
- **Prompt-cache invariant:** toolsets are snapshotted at agent construction; config changes affect only new sessions — the fresh-conversation rule the plans rely on is enforced by construction.
- **Legacy grounding of the Jira/SharePoint/Confluence plans:** each Task 1 reads the correct, existing legacy files; the load-bearing legacy facts (Cloudflare JA3 curl rationale, `/:w:/r/` URL handling, chunked-upload constants, browser-in-Edge Confluence transport) were confirmed verbatim in legacy code.
- **Confluence baseline choice:** measuring parity against the accepted Hermes skill (which has sync/INDEX/manifest features the legacy tool lacks) is the right call and the fixtures are derivable from real code.
- **Test-harness reality:** both repos' test harnesses run as the plans invoke them; every named existing test file exists.

---

## 15. Required plan corrections, ordered by severity and dependency

1. **(CONN-PLAN-001, Critical — blocks all four plans' workflow tasks.)** Re-specify the workflow contract: exact per-node `allowed_tools` (+ optional flat `requires:` list) instead of `requires.toolsets`; add one explicitly scoped, ledgered generic task in the GitLab release wiring a live tool-availability snapshot into production workflow admission; change the workflow RED tests to run the real archon validator and the real production admission entry point.
2. **(CONN-PLAN-002, Important — blocks every release's vendoring step.)** Fix the vendor command to `ERICSSON_CAPABILITIES_DIR=<worktree>` in all four plans and add the `vendoredFrom == recorded source SHA` verification line.
3. **(CONN-PLAN-003, Important — blocks the natural-language acceptance path for releases 1–3.)** Choose the skills-discovery mechanism (generic index inclusion, or per-connector index-visible router skills) and add its files + RED tests to the GitLab, Jira, and SharePoint plans.
4. **(CONN-PLAN-005, Important — do before GitLab Task 7.)** Add the GitLab legacy behavior-freeze task (named `loop_24` reads + `gitlab-behavior-map.md` with dispositions and the legacy SHA).
5. **(CONN-PLAN-004, Important — Confluence only, but decide during Release 1 foundation if the answer is a generic port-claim seam.)** Resolve the CDP-port/profile authority: distinct default or explicit core-registry integration; add the coexistence test.
6. **(CONN-PLAN-006, Important.)** State and test the `jira-defect-loop.yml` redesign (or defer the workflow to Phase 6 and ship the skill), and record the narrowing in the flow doc.
7. **(CONN-PLAN-007/-008/-009/-010, Minor.)** Widen the Phase-0 inventory command; add the `sp_audit` disposition row; enumerate the Teams-test file list; pin the legacy SHA in each behavior map and refresh the Ericsson docs' snapshot line.

---

## 16. Unresolved product decisions, unverified premises, and evidence needed

1. **Jira migration semantics of "explicitly configured users."** Today `ericsson-jira` is in every existing profile's `plugins.enabled` because staging auto-seeded it — those entries are indistinguishable from deliberate user enables. Decide: on migration to standalone, does "preserve existing explicitly configured users" mean (a) keep enabled wherever the seeded entry exists, (b) keep enabled only where `JIRA_PAT`/`JIRA_BASE_URL` are set, or (c) something else? Evidence needed: a written policy in Jira Task 2, plus fixtures for seeded-but-unconfigured profiles.
2. **`plugins/workflow` must survive the staging change.** The generic "stop auto-enabling manifest plugins" change (GitLab T6) must not un-enable `plugins/workflow` (listed in `capabilities/ericsson.json` `plugins[]` and auto-enabled today) — the entire workflow subsystem rides on it. The plan's "old backend entry retains old behavior unless its source manifest explicitly migrates it" bullet covers this *if* implemented correctly; add an explicit fixture naming `workflow`.
3. **The Jira `auto`-transport Cloudflare classifier** (premise 10): define the exact native-response signature (status, `cf-ray`/error-1010 markers) that triggers curl fallback, and what happens when curl is not configured.
4. **Cron profile semantics:** the design says jobs run "in its configured profile"; the mechanism is the scheduler process's ambient profile (no per-job switch). Either align the design/test wording or plan a per-job profile feature (not currently authorized).
5. **Vendored-manifest schema ownership:** whether `capabilities/ericsson.json`'s Hermes-only keys (`workflowPackages`, `configDefaults`, `mcpServersFile`) are generated by the vendor script or hand-maintained was not fully traced; Phase 0's classification must decide their source of truth. Evidence needed: trace of `vendor-ericsson.mjs`'s manifest-emission path during Task 1.
6. **Anchor-file/template discovery** (legacy SharePoint fetcher) and **CI include forms `remote:`/`template:`**: explicit preserve/adapt/exclude decisions belong in the respective behavior maps.

---

## 17. Command and evidence ledger

All commands ran read-only from `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` unless noted. No repository content was modified; no worktree was created; no test suite was executed (per the prompt's plan-review scope); the two preserved Ericsson files were not touched. The only write was this report.

| # | Command (abbreviated) | Dir | Result / evidence type |
| --- | --- | --- | --- |
| 1 | `git status/branch/rev-parse/worktree list` (×3 repos) | each repo | States recorded above — execution |
| 2 | `shasum -a 256` on the five inputs | hermes | All match — execution |
| 3 | `wc -l` on inputs and docs | hermes | Sizing — execution |
| 4 | `git log/diff --stat 8ca26f8..HEAD` | loop_24 | 1 docgen-only commit — execution |
| 5 | `git cat-file -t 3f124f5; merge-base --is-ancestor` | loop_24 | Ancestor confirmed — execution |
| 6 | `/bin/ls` existence sweeps (scripts, tests, subcommands, web_routers, desktop files, i18n) | hermes | All plan-named files exist — execution |
| 7 | `grep -n '"test:ui"\|"typecheck"\|"lint"' apps/desktop/package.json`; root `workspaces` | hermes | Scripts + workspaces verified — inspection |
| 8 | `grep register_skill hermes_cli/plugins.py`; reads of `plugins.py:352-1290` | hermes | `PluginContext` methods; no `register_setup_action`; plugin skills index-excluded — inspection |
| 9 | Reads of `tools/skills_tool.py:684-1080`, `agent/prompt_builder.py:1770-1860` | hermes | `<available_skills>` built from skills dirs only — inspection |
| 10 | `grep enabled hermes_cli/capability_staging.py`; reads of `:643-648, 819-825` | hermes | Auto-enable confirmed — inspection |
| 11 | Reads of `plugins/workflow/{schema.py:1748-1762, language_schema.py:1413, compat.py:560-770}`; grep `requires`/`toolsets`/`loop_group` | hermes | `requires` = string list; no `toolsets`; `loop_group` deferred — inspection |
| 12 | `grep requires` + read `workflows/my-tickets-summary.yml`; read `capabilities/workflow-packages/ericsson/workflows/my-tickets-summary.hermes.yaml` | ericsson / hermes | Mapping form is legacy-profile only; companion uses `required_services` — inspection |
| 13 | `sed -n 1170,1189p scripts/vendor-ericsson.mjs`; grep `SOURCE_REPO` | hermes | Env var is `ERICSSON_CAPABILITIES_DIR`; `SOURCE_REPO` absent — inspection |
| 14 | `grep app_only/client_credentials/from_env tools/microsoft_graph_auth.py`; consumer grep | hermes | App-only-only; consumers: teams platform adapter, teams_pipeline — inspection |
| 15 | `grep 9333` in skill scripts + `tools/browser_profiles.py:40-80` | hermes | Port-9333 authority collision — inspection |
| 16 | `grep INDEX.md/manifest/fetched skills/ericsson/confluence-research/scripts/*` | hermes | Sync/INDEX/manifest features present — inspection |
| 17 | `grep loop_24/legacy` in the GitLab plan | hermes | Zero legacy file references — inspection |
| 18 | `grep save_env_value/get_env_value hermes_cli/config.py`; `profiles.py` function list | hermes | Secret/profile helpers real — inspection |
| 19 | Four parallel read-only survey agents (Hermes plugin architecture; Ericsson repo; legacy loop_24; Hermes surfaces/staging) | all repos | Leads — every fact used above was re-verified by direct inspection (rows 6–18) or is quoted with file:line from the agents' verbatim reads of named files |
| 20 | WebFetch `hermes-agent.nousresearch.com/docs/developer-guide/plugins` (2026-08-09) | network | Doc-skew comparison — documentation comparison |

Durations were not meaningful (all sub-second except the survey agents, 3–8 minutes each).

---

## 18. Security-exclusion confirmation

No standalone threat-model, security-audit, security-review, penetration-testing, or vulnerability-scanning skill or workflow was invoked or attempted at any point in this review. No exploit search, payload construction, credential probing, or authentication bypass was performed; no real credentials were used; no live Ericsson/GitLab/Jira/Microsoft/Confluence service was contacted (the single network request was to the public Hermes documentation site for the doc-skew comparison the prompt requests). Authentication and configuration behavior was reviewed strictly as product functionality. The plans' own deterministic security-sensitive tests (trusted origins, redaction, secret handling, subprocess bounds) were assessed for plan completeness only and were not executed.

---

IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.
