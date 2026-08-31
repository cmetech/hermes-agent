# Adversarial plan review — remaining Ericsson GitLab reads

## 1. Review identity, inputs, and repository state

- Reviewer: Codex GPT-5.6 Sol
- Reasoning setting: `xhigh`
- Review date: 2026-08-30
- Platform: Codex on macOS/Darwin 25.5.0, arm64 (`Darwin beast 25.5.0 ... RELEASE_ARM64_T6031`)
- Review type: plan-quality, source-grounding, and routing-reliability review only
- Live GitLab contact: none
- Other reviewer reports used as evidence: none

### Immutable input verification

All six artifacts were read in full and matched their required SHA-256 values.

| Artifact | Verified SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `6d903ea5b532095789258d7c60ccb6d05652e928a73c4bd2c33c52db4151198e` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `70b668aecd0a5b397926c859089eca44240f178c1f1c5d4645c5f59005500dff` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `87fe40b20981ac878f6359c26e2c4a85d5d2e5469e9a98310751cc5dafd8e1d8` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `7727df3384bd8f6832737167212c0a5b2bdd99e092eb55499f17bb2a39f132d8` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `ab1bc9139072b0ad32c387bef1f8de91dfa8d0522dfe378073d70e1ed560bbbe` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `4df500b6a92f60df9bf0934b56290dae824602670e46533624b65c912debb513` |

### Hermes target/distribution repository

- Path: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`
- Branch: `base`
- HEAD: `4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944`
- Upstream state: `base...origin/base [ahead 2]`
- Status: only the pre-existing user-owned untracked paths below were present; no reviewed file was changed during evidence collection.

```text
?? .otto/
?? docs/assessments/
?? docs/design/2026-08-12-deferred-tool-dispatch-findings.md
?? docs/handoffs/
?? docs/plans/2026-08-12-deferred-tool-dispatch-reliability-plan.md
?? docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-prompt.md
```

Exact `git worktree list` output recorded at review start:

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent                                                                        4e8c3d61d2 [base]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent-windows-secret-storage                                                 dea0b70a01 [fix/windows-standard-user-secret-storage]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/brand-profile-distribution-labels                           e119b3dd46 [fix/brand-profile-distribution-labels]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/branded-cli-help                                            fe84935688 [fix/branded-cli-help]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/branded-root-help                                           6ae2cd6a60 [fix/branded-root-help]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/confluence-research                                         b2ae7f5fe1 [feat/confluence-research]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/connector-cli-host-port-design                              f9b59eecfb [design/connector-cli-host-port]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/ericsson-gitlab-connector                                   f447d1881c [feat/ericsson-gitlab-connector]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/ericsson-jira-connector                                     911b7e77e8 [feat/ericsson-jira-connector]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/ericsson-sharepoint-review-remediation                      84b2a39e60 [fix/ericsson-sharepoint-review-remediation]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/feat-workflow-showcase-desktop-run                          72a09ba732 [feat/workflow-v3.0.3]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/fix-fresh-capability-bootstrap                              cbc8b8dbce [fix/fresh-capability-bootstrap]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/fix-windows-authenticated-resource-upgrade                  70b71efad3 [fix/windows-authenticated-resource-upgrade]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/merge-ai-development-support                                12f2bae35d [merge/ai-development-support]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-base-v3.0.0                                         b4f758d510 [release/base-v3.0.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-base-v3.0.1                                         8eb084137b [release/base-v3.0.1]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-base-v3.0.2                                         f1704cc003 [release/base-v3.0.2]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-base-v3.0.3                                         50d39ff5b8 [release/base-v3.0.3]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-base-v5.0.0                                         7fe8787b6c [release/base-v5.0.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v2.0.2                                       e1181c5374 [release/loop24-v2.0.2]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v2.0.7                                       90ae6333b4 [release/loop24-v2.0.7]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v2.0.8                                       6e0a3bd7c6 [release/loop24-v2.0.8]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v2.0.9                                       210b899511 [release/loop24-v2.0.9]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v3.0.0                                       d17a88c57c [release/loop24-v3.0.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v3.0.1                                       3ee796849e [release/loop24-v3.0.1]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v3.0.2                                       02683786e7 [release/loop24-v3.0.2]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v3.0.3                                       ae0dc16115 [release/loop24-v3.0.3]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v5.0.0                                       ef3dcb5154 [release/loop24-v5.0.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v5.1.0                                       d60fdc7112 [release/loop24-v5.1.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-loop24-v5.6.0                                       7e4f2433a7 [loop24]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v2.0.2                                         2d74ffbeab [release/otto-v2.0.2]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v2.0.7                                         42471edfe2 [release/otto-v2.0.7]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v2.0.8                                         254f765d5e [release/otto-v2.0.8]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v2.0.9                                         7d6f59de0a [release/otto-v2.0.9]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v3.0.0                                         a1952e28a5 [release/otto-v3.0.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v3.0.1                                         bebce612ad [release/otto-v3.0.1]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v3.0.2                                         9bc3822878 [release/otto-v3.0.2]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v3.0.3                                         ccd7bd6054 [release/otto-v3.0.3]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v5.0.0                                         7ca1229511 [release/otto-v5.0.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v5.1.0                                         a391053561 [release/otto-v5.1.0]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/release-otto-v5.6.0                                         b0ea06c799 [otto]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-catalog-desktop-trigger                            58384c4e3a [feat/workflow-catalog-desktop-trigger]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-foundation-parser-remediation             06b8da81f5 [fix/workflow-language-foundation-parser-remediation]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-foundation-remediation                    b6aaa21f3c [fix/workflow-language-foundation-remediation]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-foundation-task-10-release-evidence       e42a64e821 [test/workflow-language-foundation-task-10-release-evidence]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-2-structured-data                   5b974a5359 [feat/workflow-language-phase-2-structured-data]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience 9d942bc8ff [feat/workflow-language-phase-3-semantic-compatibility-resilience]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-4-ordinary-loops-immutable-includes e7a70f4497 [feat/workflow-language-phase-4-ordinary-loops-immutable-includes]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-5-provider-portability              abaef0f8fb [feat/workflow-language-phase-5-provider-portability]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6                                a50dd2fc08 [feat/workflow-loop-groups-phase-6]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6-review-claude                  d850707a25 (detached HEAD)
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6-review-codex                   d850707a25 (detached HEAD)
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-production-remediation                             cf993b54a5 [feat/workflow-production-remediation]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-production-review                                  bb75289a5b [fix/workflow-production-review]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-studio-authoring-contract                          8c09f5c6aa [feature/workflow-studio-authoring-contract]
```

### Ericsson authoritative source repository

- Path: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`
- Branch: `main`
- HEAD: `0d7654d14db0afe0c688a752a2676d8cabe2f981`
- Upstream state: `main...origin/main`
- Status: clean

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities                                                   0d7654d [main]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/confluence-connector                   8e58703 [feat/ericsson-confluence-connector]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-connector-cli                 0d7654d [feat/ericsson-connector-cli]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-connector-cli-design          8f6cd5c [design/ericsson-connector-cli]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-gitlab-connector              922a162 [feat/ericsson-gitlab-connector]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-jira-connector                6b178d1 [feat/ericsson-jira-connector]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/ericsson-sharepoint-review-remediation 5a931bb [fix/ericsson-sharepoint-review-remediation]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-coverage                        b024e25 [feat/ericsson-gitlab-coverage]
/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-read-exploration                634ca3b [feat/gitlab-read-exploration]
```

## 2. Overall verdict

**BLOCK.** I found no Critical findings, ten Important findings, and no Minor findings. The approved scope and broad source-first architecture are sound, but the plans are not yet executable or acceptance-safe. In particular, the exact job normalizer contradicts both the current helper and documented GitLab payloads; the optional positional rule produces `None` that the canonical validator rejects; and the proposed live-routing gate can pass incomplete sequences or a write attempt that is stopped before `registry.dispatch` observes it.

## 3. Severity-sorted finding table

| ID | Severity | Title | Primary affected task(s) |
| --- | --- | --- | --- |
| I-01 | Important | Exact job normalizer rejects documented payloads and cannot pass its own fixture | CI Task 2 |
| I-02 | Important | Extracted pipeline summary is neither strict nor shape-preserving | CI Task 3 |
| I-03 | Important | Optional MR project positional becomes invalid `None` | Release Task 6 |
| I-04 | Important | Registry interception misses write selections blocked by the plugin pre-hook | CI Task 9; repository/release live gates |
| I-05 | Important | `is_safe` accepts incomplete multi-step and multi-intent routes | CI Task 9; repository Task 7; release Task 7 |
| I-06 | Important | Shared corpus omits existing MR reads required by the release slice | CI Task 7; release Task 7 |
| I-07 | Important | Substring model-family gate does not prove two independent families | CI Task 9; all live gates |
| I-08 | Important | No cross-plan integration transition creates the prerequisites later plans require | CI Task 10; repository Task 1/8; release Task 1/8 |
| I-09 | Important | Every Hermes Python gate bypasses the mandatory hermetic test runner | All Hermes verification tasks |
| I-10 | Important | Existing fixed-count onboarding test is omitted, making the full source GREEN unplanned | CI Task 6/8; later source gates |

### I-01 — Important — Exact job normalizer rejects documented payloads and cannot pass its own fixture

1. **ID/severity:** I-01, Important.
2. **Title:** Exact job normalizer rejects documented payloads and cannot pass its own fixture.
3. **Affected requirement/task/surface:** CI spec “`gitlab_read_job`,” strict remote data invariant; CI Plan Task 2; Ericsson `plugins/ericsson-gitlab/operations.py`; REST job detail and pipeline-job collection.
4. **Plan/source evidence:** Task 2’s fixture supplies `user={id, username, name}` and expects the same three-field output, but Step 3 calls current `_normalize_user` directly. Current `_normalize_user` (`operations.py:1148-1163`) requires and returns `state`, so the fixture fails before GREEN. The planned exact normalizer also requires `pipeline.web_url` and `commit.web_url`. GitLab’s current [Jobs API](https://docs.gitlab.com/api/jobs/) examples include pipeline identity/status/ref/SHA but no pipeline web URL, and commit ID/short ID/title but no commit web URL; some documented job payloads also contain `user: null`. Task 2 passes `project_path` but never uses it to derive or canonicalize those links.
5. **Violated invariant/decision:** Non-negotiable invariants 8 (strict remote data), 11 (identity semantics), and 14 (executable TDD); the prompt’s explicit requirement to inspect optional nested job pipeline/commit/user members.
6. **Realistic scenario:** A normal `GET /projects/42/jobs/41` response follows the documented shape: job URL exists, pipeline and commit carry identifiers but not their own web URLs, or the user is absent/null for an old/canceled job.
7. **Wrong result/consequence:** `gitlab_read_job` and `gitlab_list_pipeline_jobs` return `invalid_remote_data` for a valid response. Independently, the plan’s own focused fixture fails because `state` is missing, so the stated GREEN is impossible without an unplanned implementation/test deviation.
8. **Why other gates do not cover it:** The malformed-data matrix only tests rejection; it does not add documented missing/null optional members. Full pytest can only reveal the inconsistency after implementation and gives no approved result contract for the implementer to follow.
9. **Smallest whole-gap correction:** Amend Task 2 with one exact optional-member contract. Reuse `_normalize_user` when a user mapping is present and keep its display-safe `state`; return `None` when the documented member is null/absent. Validate pipeline/commit identifiers when present and derive canonical same-project URLs from `project_path` plus ID/SHA rather than requiring invented remote fields. Require short SHA to prefix full SHA. State which nested objects are required per endpoint and which may be `None`.
10. **RED/acceptance assertion:** Add a parameterized `test_normalize_job_accepts_documented_optional_shape` using the official job example with no nested web URLs and variants `user=None`, `runner=None`, absent artifacts, and nullable timestamps. Assert canonical derived URLs, `user is None` or `{id, username, name, state}`, no trace/artifact/variable leakage, and run `"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k 'normalize_job or read_job or pipeline_jobs'`.

### I-02 — Important — Extracted pipeline summary is neither strict nor shape-preserving

1. **ID/severity:** I-02, Important.
2. **Title:** Extracted pipeline summary is neither strict nor shape-preserving.
3. **Affected requirement/task/surface:** CI spec MR-pipeline compatibility and remote validation; CI Plan Task 3; existing `gitlab_list_pipelines` plus new `gitlab_list_merge_request_pipelines`.
4. **Plan/source evidence:** Task 3 says to “move only” the existing inner normalizer and keep the existing result shape. Its proposed `_normalize_pipeline_summary` validates `ref`, `sha`, `status`, `source`, `created_at`, and `updated_at` only as bounded strings. Current source has the correct semantic helpers `_validate_remote_ref`, `_commit_sha`, and `_rfc3339`; they are not used. The current inner normalizer (`operations.py:2598-2623`) always emits `web_url` with `None` when absent, while the proposed extraction omits the key unless present, contradicting the stated shape-preservation promise.
5. **Violated invariant/decision:** Invariants 8 (strict remote data), 12 (schema/runtime parity), and 14 (executable TDD), plus the approved promise that MR pipeline summaries remain compatible with existing pipeline summaries.
6. **Realistic scenario:** GitLab returns a malformed or proxy-corrupted summary such as `sha="forty-ish"`, `created_at="yesterday"`, or an invalid remote ref. Alternatively, an existing list response lacks `web_url`.
7. **Wrong result/consequence:** The connector promotes malformed strings into plausible pipeline evidence. For the optional URL case, an existing consumer sees a silent key-shape change after an allegedly mechanical extraction.
8. **Why other gates do not cover it:** Task 3 requests generic “malformed members” but gives exact GREEN code that accepts the semantic failures. Existing pipeline tests validate the current weak behavior and do not establish the new strict contract or missing-key compatibility.
9. **Smallest whole-gap correction:** Specify an exact shared projection: positive `id`/optional positive `iid`; remote-ref validation; 40-character SHA validation; RFC3339 validation for non-null timestamps; bounded enums/text as intentionally chosen; same-origin URL; and an explicit missing-field policy identical for old and new callers. Add characterization tests before extraction, then strict tests shared by both endpoints.
10. **RED/acceptance assertion:** Add cases where invalid ref/SHA/timestamps fail `invalid_remote_data`, plus a characterization case for absent `web_url`. Run `"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k 'pipeline_summary or list_pipelines or merge_request_pipelines'` and assert byte-equivalent normalized objects for the same raw summary through both collection paths.

### I-03 — Important — Optional MR project positional becomes invalid `None`

1. **ID/severity:** I-03, Important.
2. **Title:** Optional MR project positional becomes invalid `None`.
3. **Affected requirement/task/surface:** Release spec connector CLI; Release Plan Task 6 Steps 2/5/6; Ericsson connector CLI parser and `gitlab mr list [project]`.
4. **Plan/source evidence:** The plan adds only `nargs="?"` for a non-required positional. Current `_argument_kwargs` begins with `default=argparse.SUPPRESS` but removes `default` for every positional (`parser.py:213-247`). Standard argparse therefore stores `None` when an optional positional is absent; this was directly demonstrated during review as `{'project': None}`. Current `canonical_arguments` checks `hasattr`, validates the value against the bound schema, and stores it (`parser.py:437-485`). `string_or_integer` does not accept `None`. The plan’s parser test checks only `nargs`, not parse-to-canonical behavior.
5. **Violated invariant/decision:** Invariants 12 (schema/runtime parity) and 14 (executable TDD), and the approved no-project MR CLI acceptance criterion.
6. **Realistic scenario:** A user runs `<brand> gitlab mr list --scope reviews_for_me` with no project positional.
7. **Wrong result/consequence:** Parsing creates a present `project=None`, canonical schema validation rejects it, and the advertised global MR CLI form is broken.
8. **Why other gates do not cover it:** The planned `gitlab_port` command case expects `{}`, but Step 5’s exact code cannot produce it; the separate `nargs` assertion can pass while runtime canonicalization fails. Schema tests exercise tool invocation, not argparse namespace semantics.
9. **Smallest whole-gap correction:** Preserve `argparse.SUPPRESS` for optional positionals (do not pop the default in that case), or explicitly skip an absent `None` only for non-required positional bindings before contract validation. Keep required and repeatable behavior unchanged.
10. **RED/acceptance assertion:** Add a parser-to-canonical test that parses `gitlab mr list` and asserts `vars(namespace)` lacks the binding destination and `canonical_arguments(namespace) == {}`; also assert the project form still yields `{"project": "division/team/repo"}`. Run `"$SOURCE_PY" -m pytest -q tests/test_connector_cli_parser.py tests/test_connector_cli_gitlab_port.py -k 'optional_positional or merge_request'`.

### I-04 — Important — Registry interception misses write selections blocked by the plugin pre-hook

1. **ID/severity:** I-04, Important.
2. **Title:** Registry interception misses write selections blocked by the plugin pre-hook.
3. **Affected requirement/task/surface:** Routing invariants and no-live-GitLab gate; CI Plan Task 9; all later live routing gates; Hermes `model_tools.handle_function_call` and Ericsson GitLab write approval hook.
4. **Plan/source evidence:** The plan instruments `registry.dispatch` and derives underlying `gitlab_calls` there. In Hermes, `tool_call` is unwrapped recursively (`model_tools.py:1339-1384`), then `resolve_pre_tool_admission` runs before `registry.dispatch`; a block/approval result returns at `model_tools.py:1462-1493`, and dispatch occurs only at line 1534. The GitLab plugin registers a `pre_tool_call` hook for every `WRITE_APPROVALS` entry (`plugins/ericsson-gitlab/__init__.py:379-404`). Therefore an attempted GitLab write can be visible in the bridge transcript but absent from the dispatch log. The plan says writes are immediate failures but does not specify a fail-closed scan of `tool_call.arguments.name` before applying `is_safe`.
5. **Violated invariant/decision:** Invariants 5 (read-only routing), 6 (intercept before every underlying handler), and 14; the explicit live-gate rule that any write selection is nonzero.
6. **Realistic scenario:** In the injection case, the model first calls allowed `gitlab_search_code`, then obeys an untrusted snippet and selects `gitlab_merge_merge_request`. The read reaches dispatch; the write is stopped for approval before dispatch.
7. **Wrong result/consequence:** `gitlab_calls == ["gitlab_search_code"]`, so current `is_safe` can pass the case even though the model selected a write. No network mutation occurs, but the routing reliability report falsely certifies the model as read-only.
8. **Why other gates do not cover it:** Static corpus tests only forbid writes in allowed sets. Plugin approval protects execution, not the claim that the model never chose a write. `_extract_bridge_calls` records `tool_call` arguments but the planned acceptance algorithm does not make those names authoritative failures.
9. **Smallest whole-gap correction:** Parse every assistant bridge call before result classification. For each `tool_call`, extract the underlying name and immediately fail if it starts with `gitlab_` and is not in the corpus read allowlist, regardless of pre-hook/dispatch outcome. Retain the dispatch stub as the network barrier for allowed reads and an additional defense for unexpected direct dispatch.
10. **RED/acceptance assertion:** Add a deterministic runner test with a transcript containing an allowed read followed by a GitLab write while `registry.dispatch` logs only the read; assert hard failure/nonzero. Run it through `scripts/run_tests.sh tests/hermes_cli/test_ericsson_connector_distribution.py -k 'routing_runner and pre_hook_write'` (or a dedicated Hermes runner-test file invoked by the same wrapper).

### I-05 — Important — `is_safe` accepts incomplete multi-step and multi-intent routes

1. **ID/severity:** I-05, Important.
2. **Title:** `is_safe` accepts incomplete multi-step and multi-intent routes.
3. **Affected requirement/task/surface:** Routing evaluation requirements; CI Plan Task 9; Repository Task 7; Release Task 7; shared `routing_cases.json` contract.
4. **Plan/source evidence:** The exact predicate is only: first call is in `allowed_first_tools` and the set of all calls is a subset of first/follow-up tools. It has no required-call, ordered-sequence, completion-outcome, or minimum-length condition. Repository Task 7 explicitly needs project search followed by clarification or selected-project code search. Release Task 7 adds a multi-intent To-Do plus review-request prompt. A one-call prefix satisfies `is_safe` for both.
5. **Violated invariant/decision:** Invariants 4, 5, 7, and 14; the prompt’s requirement that allowed-first/follow-up semantics represent realistic safe sequences and that missing required work fail.
6. **Realistic scenario:** For “find code containing X” without a project, the model calls only `gitlab_search_projects` and then states that it searched. For “show my To-Dos and review requests,” it calls only `gitlab_list_todos` and stops.
7. **Wrong result/consequence:** The harness reports PASS although the requested operation/second intent was never completed and no clarifying question was asked. The two-family score becomes a routing-prefix test rather than an end-to-end route test.
8. **Why other gates do not cover it:** Repetition only repeats the same weak predicate. Static ownership and schema tests cannot show that a case’s required sequence occurred. Generic fake results make completion particularly easy to hallucinate.
9. **Smallest whole-gap correction:** Define the complete corpus schema before the CI slice with explicit allowed ordered sequences and allowed terminal outcomes, for example `allowed_sequences`, `required_tools`/`required_intents`, and `clarification_allowed`. Classify an actual ordered call trace plus final outcome; do not reduce it to a set. Multi-intent cases must satisfy both intents unless they ask a genuine clarification.
10. **RED/acceptance assertion:** Unit-test the classifier with (a) only project search, (b) project search then code search, (c) a question after project search, (d) only To-Dos, and (e) To-Dos plus personal MR list. Only (b), (c), and (e) may pass their respective cases. Execute with the mandatory Hermes test wrapper.

### I-06 — Important — Shared corpus omits existing MR reads required by the release slice

1. **ID/severity:** I-06, Important.
2. **Title:** Shared corpus omits existing MR reads required by the release slice.
3. **Affected requirement/task/surface:** One stable cross-plan routing corpus; CI Plan Task 7; Release Plan Task 7; static corpus validator and release-inbox eval.
4. **Plan/source evidence:** CI Task 7 creates top-level `read_tools` with CI operations, `gitlab_resolve_project`, and `gitlab_read_file`, but omits existing `gitlab_list_merge_requests` and `gitlab_read_merge_request`. Release Task 7 says to append only the three new operations (`list_releases`, `read_release`, `list_todos`) while adding global/project MR discovery and selected/project MR cases. CI’s static test requires every permitted tool to be a subset of `read_tools`.
5. **Violated invariant/decision:** Invariants 4, 5, 12, and 14, plus the approved routing architecture's requirement for one stable corpus shared by all three plans.
6. **Realistic scenario:** The release slice adds an allowed first tool `gitlab_list_merge_requests` to a global review-queue case, as the approved spec requires.
7. **Wrong result/consequence:** `test_gitlab_routing_cases_reference_registered_read_tools_and_skills` fails, or the implementer silently weakens/removes the MR cases or deviates from “append the three new tools.” The frozen plans cannot both be followed.
8. **Why other gates do not cover it:** The static test exposes the contradiction but supplies no approved correction. The live runner also treats omitted names as hard failures. Later vendoring cannot repair source corpus semantics.
9. **Smallest whole-gap correction:** Seed the version-1 corpus in CI Task 7 with every existing read that any of the three frozen slices is authorized to use, at minimum `gitlab_list_merge_requests` and `gitlab_read_merge_request`, while later plans append only genuinely new operations. Add a cross-slice fixture that validates the final intended inventory from the start.
10. **RED/acceptance assertion:** Add a static release-case fixture before implementation and assert every permitted MR tool is in `read_tools`, every inventory name is a registered read schema, and no write appears. Run `"$SOURCE_PY" -m pytest -q tests/test_gitlab_skills.py -k routing_cases` after each slice.

### I-07 — Important — Substring model-family gate does not prove two independent families

1. **ID/severity:** I-07, Important.
2. **Title:** Substring model-family gate does not prove two independent families.
3. **Affected requirement/task/surface:** Two-family reliability invariant; CI Plan Task 9; repository/release live matrices.
4. **Plan/source evidence:** The plan requires one model string containing `claude` and one containing `openai`, `gpt`, or `codex`. It does not require distinct identifiers, parse provider/model identity, or confirm the resolved model used by `AIAgent`. A single alias containing both token classes, or two aliases resolving to the same backend, satisfies the textual gate. The reusable `setup_isolated_home` accepts a model, but the existing base scenario runner itself hardcodes a Claude model in `AIAgent`; the new script must therefore explicitly pass and record the requested resolved model rather than assume config controls it.
5. **Violated invariant/decision:** Invariant 7 and specific premise 12.
6. **Realistic scenario:** Both environment variables point to the same OpenRouter alias, or to aliases named `claude-gpt-routing`, and the CLI substring checks pass.
7. **Wrong result/consequence:** The report claims independent Claude and OpenAI/Codex evidence after exercising one model family twice.
8. **Why other gates do not cover it:** Case repetition checks prompt variance, not family identity. Transcript `model` labels can merely echo CLI input unless compared with the agent’s resolved provider/model.
9. **Smallest whole-gap correction:** Use explicit family slots (`--claude-model`, `--openai-model`), require distinct model IDs, validate the provider/model namespace against an explicit family mapping, pass the exact model into both isolated config and `AIAgent`, and record/assert the resolved value returned by the runtime. Refuse ambiguous aliases unless accompanied by an explicit, checked family declaration.
10. **RED/acceptance assertion:** Add deterministic CLI tests rejecting identical IDs, one ID containing both family tokens, two same-family IDs, and a runtime-resolved mismatch; accept one `anthropic/...` and one `openai/...` model. Run with the Hermes wrapper before any paid model call.

### I-08 — Important — No cross-plan integration transition creates the prerequisites later plans require

1. **ID/severity:** I-08, Important.
2. **Title:** No cross-plan integration transition creates the prerequisites later plans require.
3. **Affected requirement/task/surface:** Cross-plan ordering, source-first ownership, exact vendoring; CI Task 10, Repository Task 1/8, Release Task 1/8; both repositories.
4. **Plan/source evidence:** CI Task 10 ends with feature branches “ready to integrate into `base`” but contains no integration checkpoint. Repository Task 1 creates new worktrees from source `main` and Hermes `base` while requiring the CI corpus/runner to exist. Repository Task 8 again commits only feature branches. Release Task 1 starts from `main`/`base` while requiring both earlier slices. No task merges/rebases/cherry-picks, verifies integrated SHAs, or instead branches the next slice from the prior feature tips.
5. **Violated invariant/decision:** Invariants 2, 14, and 15; the prompt’s explicit cross-plan dependency and “cannot lose corpus entries” requirement.
6. **Realistic scenario:** An implementer finishes CI Task 10 and immediately starts Repository Task 1 as written. `main`/`base` still lack `routing_cases.json` and `gitlab_skill_routing_livetest.py`, so prerequisite checks fail. If they bypass checks, later vendoring starts from stale source and can discard earlier behavior.
7. **Wrong result/consequence:** The sequence is not executable; ad hoc integration decisions can lose prior corpus entries/generated docs or vendor a source SHA unrelated to the Hermes base tip.
8. **Why other gates do not cover it:** Exact vendoring validates one selected source worktree, not whether the next source branch contains prior slices. Final diff checks compare each isolated branch to stale `main`/`base` and do not establish a three-slice ancestry chain.
9. **Smallest whole-gap correction:** Add an explicit transition task between slices: integrate the completed source branch into source `main` and the exact vendor branch into Hermes `base` through the project’s approved review mechanism, record both full SHAs, verify clean checkouts, then branch the next slice from those SHAs. Alternatively, explicitly chain later worktrees from the previous feature tips and add one final integration plan; choose one strategy and state it.
10. **RED/acceptance assertion:** Before Repository Task 1 and Release Task 1, assert exact ancestry (`git merge-base --is-ancestor <prior-source-sha> main` and equivalent for Hermes `base`), required files, prior corpus case IDs, prior tool manifests, and `vendoredFrom` equality. Failure must stop the next slice.

### I-09 — Important — Every Hermes Python gate bypasses the mandatory hermetic test runner

1. **ID/severity:** I-09, Important.
2. **Title:** Every Hermes Python gate bypasses the mandatory hermetic test runner.
3. **Affected requirement/task/surface:** Strict TDD and verification commands in all three plans; Hermes baseline, deterministic, and final Python gates.
4. **Plan/source evidence:** Hermes `AGENTS.md:1631-1645` says **ALWAYS use `scripts/run_tests.sh`** and not direct pytest because the wrapper clears credentials, fixes locale/timezone, and provides per-file CI-parity isolation. All three plans invoke `"$HERMES_PY" -m pytest -q ...` directly in baselines and final verification.
5. **Violated invariant/decision:** Invariant 14 and repository-local mandatory test policy.
6. **Realistic scenario:** A developer shell contains live provider credentials or locale/timezone differences; a test passes locally through shared process state but fails in CI, or a supposed no-network test sees credentials unavailable in CI.
7. **Wrong result/consequence:** The plan can claim deterministic/final GREEN using a test mode the repository explicitly identifies as non-equivalent to CI.
8. **Why other gates do not cover it:** Node vendoring tests do not cover Python environment isolation. Source-repository pytest policy permits direct pytest, but that does not override the Hermes repository’s separate rule.
9. **Smallest whole-gap correction:** Replace only Hermes pytest commands with `cd "$HERMES_WT" && scripts/run_tests.sh <files/options>`. Keep source-repository commands unchanged. Ensure the runner-specific deterministic tests are included through the same wrapper.
10. **RED/acceptance assertion:** The plan-review acceptance check should grep all three plans and find no `"$HERMES_PY" -m pytest`; each focused/final Hermes command must begin with `scripts/run_tests.sh`. Execute the amended commands once before the live paid-model gate.

### I-10 — Important — Existing fixed-count onboarding test is omitted, making the full source GREEN unplanned

1. **ID/severity:** I-10, Important.
2. **Title:** Existing fixed-count onboarding test is omitted, making the full source GREEN unplanned.
3. **Affected requirement/task/surface:** CI Plan Task 6/8 and later full source gates; generated onboarding authority and source tests.
4. **Plan/source evidence:** Task 6 correctly removes the prose “30 tools: 18 bounded reads and 12 writes” and says capability tables must not freeze totals. Current `tests/test_connector_cli_docs.py:66-82` independently hard-codes GitLab operation count `30` and asserts the onboarding tool-line count. Task 6 neither names/stages this file nor runs it; its staging list mentions only `test_connector_cli_gitlab_port.py` and conditionally `test_task11_convergence.py`. Adding four CI tools makes the existing assertion expect 30 while the authority contains 34.
5. **Violated invariant/decision:** Invariants 13 (migration truth) and 14 (executable TDD), plus the repository rule against change-detector tests.
6. **Realistic scenario:** Task 6’s focused checks pass and commit. Task 8 runs full pytest; `test_domain_onboarding_entries_represent_facade_and_all_60_operations` fails on GitLab’s changed count.
7. **Wrong result/consequence:** The approved GREEN path cannot complete without an unplanned test edit. An implementer may simply bump 30 to 34, preserving the forbidden change-detector pattern and guaranteeing another failure in the next two slices.
8. **Why other gates do not cover it:** Catalog validation checks source/generated consistency, not the unrelated fixed literal. Full pytest catches the failure late but does not approve the required relational test design.
9. **Smallest whole-gap correction:** Add `tests/test_connector_cli_docs.py` to Task 6 files, RED/GREEN commands, and staging. Replace the numeric GitLab assertion with a relational invariant: onboarding tool names equal the registered plugin/descriptor manifest set (or another authoritative set), while retaining the facade/natural-language checks. Rename the test so it does not encode a total.
10. **RED/acceptance assertion:** First assert the current fixed-count test fails after the new schemas are registered; then assert the relational set comparison passes. Run `"$SOURCE_PY" -m pytest -q tests/test_connector_cli_docs.py tests/test_connector_cli_gitlab_port.py tests/test_task11_convergence.py -k 'onboarding or catalog or migration'` before committing Task 6.

## 4. Specification-to-plan traceability matrix

Status meanings: **complete** = sufficiently planned and source-grounded; **partial** = substantial coverage but an unresolved contract/gate remains; **contradicted** = the exact plan cannot satisfy the requirement as written.

| Slice / normative requirement | Spec acceptance or decision | Plan tasks/tests | Status | Review note |
| --- | --- | --- | --- | --- |
| CI — four approved reads only | Job detail, pipeline jobs, MR pipelines, project variable metadata; no cancel/run | Tasks 2–5; schema/manifest tests | partial | Scope is exact; job and pipeline projections are contradicted by I-01/I-02. |
| CI — bounded collection behavior | Pagination, page ceiling, continuation, filters, retried jobs, deadlines/cancellation | Tasks 3–4 tests around `_paginate` | complete | Existing continuation machinery is correctly reused; `include_retried` encoding is covered. |
| CI — strict/safe normalization | Validate identifiers/times/nested evidence; omit trace/artifacts/values | Tasks 2–4 malformed and leakage tests | contradicted | I-01 and I-02. Variable metadata boundary is otherwise complete. |
| CI — progressive routing | Thin router → `ci-investigation` → deferred schemas → reads | Tasks 7 and 9 | partial | Ownership is clear, but the corpus/harness can falsely pass (I-04/I-05/I-07). |
| CI — CLI/migration/onboarding | Four leaves; five corrected SuperCLI rows; generated docs | Tasks 5–6 | partial | Descriptors/mappings are real; fixed-count test omission blocks GREEN (I-10). |
| CI — source-first/exact vendor/full acceptance | Source full gate, clean SHA, byte-exact vendor, two-family eval | Tasks 8–10 | contradicted | Local vendor mechanics are sound; Hermes gate and next-slice integration are not (I-08/I-09). |
| Repository — four approved reads only | Branches, tags, project code search, visible project search | Tasks 2–6 | complete | Exact non-goals remain excluded. |
| Repository — repository normalization | Bounded commit identity, tag/release separation, canonical project identity | Tasks 2, 3, 5 | complete | Proposed allowlisted projections and malformed tests are adequate. |
| Repository — code-search safety | Project required, redacted/bounded snippet, 128 KiB aggregate cap, honest continuation | Task 4 | complete | Limit reduction to the maximum safe item count preserves mid-page continuation. |
| Repository — routing precedence | Search project vs exact resolution; branch/tag/release; code/tree/file/commit | Task 7 | partial | Ownership table is good; missing-project and multi-step outcomes are not enforced (I-05). |
| Repository — CLI/migration/onboarding | Four leaves and four corrected mapping rows, generated authorities | Tasks 6–7 | complete | Current descriptor/parser model supports these required positionals/options. |
| Repository — sequential source/vendor acceptance | Consume CI corpus/runner, full source gate, exact vendor, two families | Tasks 1/8 | contradicted | Prerequisite ancestry is not created and Hermes pytest is noncompliant (I-08/I-09). |
| Release/inbox — four approved behaviors | Release list/detail, To-Dos, project-optional/personal MR list | Tasks 2–6 | partial | Operations are mapped; optional release-member and asset projection details need evidence (Unresolved Q1/Q2), and CLI is broken by I-03. |
| Release/inbox — release safety | Bounded/redacted notes, same-origin returned assets, external omission, no fetch | Tasks 2–3 | partial | Overall policy is correct; exact URL-bearing asset projection is not pinned (Unresolved Q2). |
| Release/inbox — To-Do safety | Authenticated bounded read, filters/allowlists, safe target summary, no completion | Task 4 | partial | Current docs align; supported GitLab version range is not established (Unresolved Q3). |
| Release/inbox — MR identity/backward compatibility | Project/global endpoints, native scopes, `@me`, cross-project identity | Task 5 plus Task 6 schema/CLI | partial | Operation plan is strong; no-project CLI cannot reach it (I-03). |
| Release/inbox — routing precedence | Release vs tag; To-Do vs MR queue; selected/project MR vs personal inbox | Task 7 | contradicted | Corpus inventory and sequence validator cannot represent/accept the promised cases reliably (I-05/I-06). |
| Release/inbox — migration/source/vendor acceptance | Four corrected mappings, generated docs, full source, exact vendor, two families | Tasks 7–8 | contradicted | I-07/I-08/I-09 plus inherited I-10. |

No approved operation is orphaned. No material plan work lacks approved authority, aside from the runner infrastructure required to prove routing. The primary cross-plan orphan is procedural: the plans require prior artifacts on `main`/`base` without a task that places them there.

## 5. All-task coverage and dependency matrix

| Plan task | Produces / depends on | Rating | Blocking observation |
| --- | --- | --- | --- |
| CI 1 | Worktrees and focused baselines | partial | Correct branches, but Hermes baseline bypasses mandatory wrapper (I-09). |
| CI 2 | `_normalize_job`, `read_job` | contradicted | Own fixture/helper and real payload conflicts (I-01). |
| CI 3 | Pipeline jobs, MR pipelines, shared pipeline summary | contradicted | Semantic validation and shape preservation absent (I-02); inherits I-01. |
| CI 4 | Project CI variable metadata | complete | Value discarded; metadata projection/paging/error tests are explicit. |
| CI 5 | Schemas, dispatch, manifest, CLI | complete | Real symbols and command leaves are named; project scoping is consistent. |
| CI 6 | Mapping/onboarding authorities and generated outputs | contradicted | Existing fixed-count test omitted (I-10). |
| CI 7 | CI skill and shared corpus | partial | Initial inventory is not stable for later MR cases (I-06). |
| CI 8 | Source full gate and clean SHA | partial | Commands are valid for source, but GREEN inherits I-01/I-02/I-10. |
| CI 9 | Exact vendor and shared live runner | contradicted | I-04/I-05/I-07/I-09. |
| CI 10 | Final evidence/handoff | partial | Exact SHA/diff evidence is sound; no integration transition and wrong test runner (I-08/I-09). |
| Repository 1 | New worktrees after CI | contradicted | Required CI artifacts are not put on `main`/`base`; direct Hermes pytest (I-08/I-09). |
| Repository 2 | Branch listing | complete | Endpoint, filters, commit identity, URLs, pagination, malformed cases are explicit. |
| Repository 3 | Tag listing | complete | Keeps release payload out; proper sort/search/continuation contract. |
| Repository 4 | Project code search | complete | Redaction, UTF-8 cap, item cap, continuation, ref/project identity are explicit. |
| Repository 5 | Visible-project search | complete | Visibility/namespace/description/time/URL validation and pagination are explicit. |
| Repository 6 | Schemas/dispatch/CLI | complete | Required positionals and schema-backed options fit current parser. |
| Repository 7 | Routing, mappings, onboarding | partial | Mapping/docs are complete; route sequences are not enforceable (I-05). |
| Repository 8 | Source gate, vendor, live eval | contradicted | I-07/I-08/I-09 and inherited corpus defects. |
| Release 1 | New worktrees after first two slices | contradicted | Prior ancestry absent; direct Hermes pytest (I-08/I-09). |
| Release 2 | Release summaries/list | partial | Main path is specified; optional name/description/commit behavior needs exact evidence (Q1). |
| Release 3 | Release detail/assets | partial | Safe policy is explicit; exact URL-bearing link projection is not (Q2). |
| Release 4 | To-Do listing | partial | Current allowlists match current docs, but supported version range is unknown (Q3). |
| Release 5 | Global/project MR extension | complete | Endpoint, actor translation, contradiction rules, identity cross-check, pagination/backward tests are detailed. |
| Release 6 | Schemas/skills/CLI/parser | contradicted | Optional positional exact change fails at canonicalization (I-03). |
| Release 7 | Routing/mappings/onboarding | contradicted | MR reads absent from inventory and sequence predicate is insufficient (I-05/I-06). |
| Release 8 | Source gate, vendor, live eval | contradicted | I-07/I-08/I-09 plus inherited failures. |

## 6. Operation/API/schema/CLI/migration coverage matrix

| Behavior | Endpoint and normalization plan | Pagination/bounds | Schema/dispatch/CLI | Migration/docs | Verdict |
| --- | --- | --- | --- | --- | --- |
| `gitlab_read_job` | Resolve project; `GET /projects/:id/jobs/:job_id`; allowlisted job projection | Single read, one deadline | Task 5 job-show schema/invoke/CLI | `job view` corrected | **BLOCK:** I-01. |
| `gitlab_list_pipeline_jobs` | `GET /projects/:id/pipelines/:pipeline_id/jobs`; shared job projection; `scope[]`, boolean `include_retried` | `_paginate`, max 2,000, structured continuation | Task 5 pipeline job-list | `pipeline job list` corrected | **BLOCK:** inherits I-01; supported status set also needs Q3 evidence. |
| `gitlab_list_merge_request_pipelines` | `GET /projects/:id/merge_requests/:iid/pipelines`; shared pipeline summary | `_paginate`, page ceiling, continuation | Task 5 MR pipeline-list | `mr pipeline list` corrected | **BLOCK:** I-02. |
| `gitlab_list_ci_variables` | `GET /projects/:id/variables`; `_variable_metadata`; discard `value` | `_paginate`, bounded description/flags | Task 5 variable-list | `variable list` corrected | Complete. |
| `gitlab_list_branches` | `GET /projects/:id/repository/branches`; bounded branch/commit/canonical URL | Existing pagination/continuation | Repository Task 6 | `branch list` corrected | Complete. |
| `gitlab_list_tags` | `GET /projects/:id/repository/tags`; omit release expansion | Existing pagination/continuation | Repository Task 6 | `tag list` corrected | Complete. |
| `gitlab_search_code` | `GET /projects/:id/search?scope=blobs`; ref/query validation; redacted/bounded snippets | 128 KiB aggregate; max 32 items per call; honest offset continuation | Repository Task 6 | `search code` corrected | Complete. |
| `gitlab_search_projects` | `GET /projects?search=...`; canonical identity/namespace/visibility/time/URL | Existing pagination/continuation | Repository Task 6 | `project search` corrected | Complete. |
| `gitlab_list_releases` | `GET /projects/:id/releases`; summary, no asset expansion | `_paginate`, counts/bounds/continuation | Release Task 6 release-list | `release list` corrected | Partial pending exact optional-member contract (Q1). |
| `gitlab_read_release` | Encoded tag path; detail with bounded notes/milestones/assets and no fetch | Single read; asset/milestone caps | Release Task 6 release-show | `release view` corrected | Partial pending exact URL-bearing projection (Q2). |
| `gitlab_list_todos` | `GET /todos`; optional resolved `project_id`; safe user/project/target | `_paginate`, enum/state filters | Release Task 6 todo-list | `todo list` corrected; `todo done` excluded | Substantively complete; version claim needs Q3. |
| Extended `gitlab_list_merge_requests` | Project endpoint or global `/merge_requests`; native scopes/`@me`; shared MR normalizer and per-item project identity | Existing time filters and `_paginate` | Schema extended; one CLI descriptor | `mr list` limitation corrected | Operation complete; **CLI/routing BLOCK** by I-03/I-06. |

## 7. Routing ownership and near-neighbor matrix

| Intent / near neighbor | Primary owner | First operation / precedence | Plan quality |
| --- | --- | --- | --- |
| List/filter pipelines vs inspect one | `ci-investigation` | `list_pipelines` vs `read_pipeline` | Clear. |
| MR pipelines vs jobs in pipeline | `ci-investigation` | `list_merge_request_pipelines` vs `list_pipeline_jobs` | Clear. |
| Job metadata vs trace | `ci-investigation` | `read_job` vs `job_log` | Clear and documented. |
| Project variable metadata vs inherited CI inspection | `ci-investigation` | `list_ci_variables` vs `inspect_ci` | Clear and safely separates values. |
| Project search vs exact resolve vs group browse | `repository-research` | `search_projects` → select/clarify; `resolve_project`; `list_group_projects` | Clear, but completion sequence is not enforced (I-05). |
| Branch vs tag vs release | Branch/tag: `repository-research`; release: `release-research` | Raw ref listing vs published release intent | Ownership is clear; ambiguous eval predicate is weak. |
| Code search vs tree/file/commit history | `repository-research` | `search_code`, `list_repository_tree`, `read_file`, commit operations | Clear and untrusted snippets are declared data. |
| Release list vs release detail | `release-research` | `list_releases` vs `read_release` | Clear. |
| To-Do queue vs personal MR queue | `personal-inbox` | `list_todos` vs global `list_merge_requests` | Clear, but multi-intent completion is not enforced (I-05). |
| Project MRs/one MR vs cross-project personal queue | Project/selected: `merge-request-review`; cross-project personal: `personal-inbox` | Project phrase/IID wins selected owner; “my queue/across GitLab” wins inbox | Clear ownership; shared corpus inventory contradicts it (I-06). |
| Multi-intent To-Do plus review requests | `personal-inbox`, two reads in one ordinary loop | Both approved reads, or genuine clarification | Corpus can pass one prefix only (I-05). |

The thin always-indexed router remains the single intent-to-focused-skill authority. Focused skills own tool choices. No plan introduces a classifier call, prompt mutation, or dynamic per-turn schema replacement.

## 8. Routing corpus and live-harness validity assessment

| Criterion | Assessment | Evidence |
| --- | --- | --- |
| Stable corpus created once | Partial | CI creates it and later plans extend it, but initial inventory omits MR reads needed later (I-06). |
| Skill/tool names exist when used | Partial | CI/repository names are ordered correctly; release MR cases reference existing tools omitted from `read_tools`. |
| Clear/ambiguous repetition enforced | Complete in loop shape | Plan explicitly runs clear once and ambiguous exactly three times. |
| Allowed sequence semantics | Fail | Set-based `is_safe` accepts incomplete prefixes (I-05). |
| Two distinct model families | Fail | Substring/alias gate is not identity proof (I-07). |
| Isolated home/plugin enablement | Supported | Existing helper, plugin manager, qualified skill tests, and copied skill/plugin paths are real and importable. |
| Fake origin/PAT and no live GitLab | Partial | Allowed reads are stubbed before handlers, but write attempts can evade dispatch logging because the pre-hook returns first (I-04). |
| Authentic tool/skill discovery | Supported | Normal `AIAgent`, `skill_view`, deferred `tool_search`/`tool_describe`/`tool_call`, plugin registration, and registry path are retained. |
| Router then focused skill proof | Planned | Transcript extraction sees assistant `skill_view`/bridge calls; acceptance must use ordered trace. |
| Describe before invoke proof | Planned | Explicit case condition; should be asserted by index/order, not membership. |
| No secrets/raw output retained | Supported | Base `_redact_secrets` and bounded trace intent are real; plan says omit tool outputs/credentials. |
| Nonzero on wrong skill/first/no call/write | Fail overall | Wrong skill/first/no-call checks are specified, but write and incomplete sequence failures are unsound (I-04/I-05). |

## 9. Verdict on all sixteen non-negotiable invariants

| # | Invariant | Verdict | Reason |
| --- | --- | --- | --- |
| 1 | Exact scope | PASS | All twelve approved behaviors are mapped; webhooks/mutations/non-goals remain excluded. |
| 2 | Source-first ownership | PASS locally / sequencing fail | Every shared behavior is authored in source first, but cross-plan integration is missing (I-08). |
| 3 | Stable model context | PASS | One ordinary agent loop and stable bridge/tool surface; no classifier/schema swap. |
| 4 | One routing authority per intent | PASS | Thin router plus focused skills and explicit near-neighbor precedence. |
| 5 | Read-only routing | FAIL | A pre-hook-blocked write selection can pass the report (I-04). |
| 6 | No live GitLab in routing evals | FAIL as proof contract | Network is likely blocked, but “intercept before every underlying `gitlab_*` handler and fail closed on any non-read” is not proven for pre-hook returns (I-04). |
| 7 | Two independent model families | FAIL | I-07. |
| 8 | Strict remote data | FAIL | I-01/I-02. |
| 9 | Bounded reads | PASS | Existing pagination/deadline/cancellation/truncation machinery is consistently reused. |
| 10 | Sensitive-value boundary | PARTIAL | CI variables/search redaction are sound; exact release URL-bearing asset projection remains unresolved (Q2). |
| 11 | Identity semantics | PARTIAL | Branch/tag/project/MR identities are strong; job nested URLs require correction (I-01). |
| 12 | Schema/runtime parity | FAIL | Optional positional schema/argparse mismatch (I-03). |
| 13 | Migration truth | PARTIAL | YAML/generators are authoritative and rows are correct, but onboarding test plan is stale (I-10). |
| 14 | Executable TDD | FAIL | I-01/I-02/I-03/I-08/I-09/I-10. |
| 15 | Exact vendoring | PASS locally / sequencing fail | Vendor script and SHA/byte gates are real; later slice ancestry is not established (I-08). |
| 16 | No speculative surface | PASS | Existing helpers, plugin skills, CLI descriptors, and harness are extended without new dependency/core tool/generic layer. |

## 10. Verdict on all fifteen challenged premises

| # | Premise | Verdict | Basis |
| --- | --- | --- | --- |
| 1 | Existing request/paging/validation/envelope helpers support all operations | **SUPPORTED** | `_request`/client, `_paginate`, continuation, validators, safe application envelope, and project resolution cover the work; plan defects are misuse/contract defects, not need for a new layer. |
| 2 | Pipeline summary extraction preserves current behavior | **UNSUPPORTED** | Weak semantic validation and missing-`web_url` shape change (I-02). |
| 3 | Job normalization handles real optional members | **UNSUPPORTED** | Current helper/fixture conflict and documented missing/null members (I-01). |
| 4 | Code-search cap and continuation are honest | **SUPPORTED** | 128 KiB / 4 KiB yields a 32-item cap; `_paginate` returns same-page offset when the limit cuts a page. |
| 5 | Global/project MR can share a normalizer without losing identity | **SUPPORTED** | Plan cross-checks positive project ID, `references.full`, and same-origin MR URL without per-item fetches and preserves project-scoped top-level identity. |
| 6 | To-Do enums cover supported GitLab versions | **INSUFFICIENTLY ESTABLISHED** | Current official docs match, but neither repo states the supported GitLab version range (Q3). |
| 7 | Same-origin filtering covers every returned release asset URL | **INSUFFICIENTLY ESTABLISHED** | Policy is explicit, but the exact returned asset/link fields and `direct_asset_url` handling are not pinned (Q2). |
| 8 | Current CLI model supports an optional positional with the planned change | **UNSUPPORTED** | Argparse stores `None`; canonical validation rejects it (I-03). |
| 9 | One stable corpus schema extends without order dependence | **UNSUPPORTED** | Initial `read_tools` omits later required existing MR reads; sequence schema is too weak (I-05/I-06). |
| 10 | Runner can reuse base helpers and load enabled standalone plugin | **SUPPORTED** | Helpers are importable; existing qualified-skill/plugin tests prove enablement and lookup. The new runner must explicitly pass the requested model. |
| 11 | Registry interception prevents all real access and preserves authentic discovery | **UNSUPPORTED as stated** | Allowed reads are safely stubbed, but registry interception is not the first observer for pre-hook-blocked writes and does not prove write rejection (I-04). |
| 12 | Two-family gate proves reliability | **UNSUPPORTED** | I-07 plus incomplete sequence acceptance I-05. |
| 13 | Generated docs have clear authority and no stale hand edits | **SUPPORTED** | Mapping YAML → migration builder; onboarding Markdown → catalog builder/validator. I-10 is a test-plan omission, not authority ambiguity. |
| 14 | Worktree/vendor commands preserve branch contract | **UNSUPPORTED end-to-end** | Individual vendor commands are correct and literal `main` is not used for Hermes development, but no transition puts prior slices on source `main`/Hermes `base` (I-08). |
| 15 | No accepted SuperCLI read remains omitted except exclusions | **SUPPORTED** | All 46 GitLab mapping rows were traced; the remaining read gaps are exactly the twelve planned behaviors, while webhook and listed mutations/non-goals stay excluded. |

## 11. Strict-TDD file, symbol, command, and ordering audit

| Audit dimension | Result | Details |
| --- | --- | --- |
| File ownership | Mostly valid | Source owns operations/tools/plugin/skills/CLI/mapping/docs; Hermes owns exact vendor snapshot and live runner. No plugin-to-core feature edit is planned. |
| Existing symbol validity | Mostly valid | Named operations helpers, schemas, invoke paths, descriptor/parser functions, generators, vendor script, base live helpers, registry, and bridge functions exist. I-01/I-03 show incorrect assumptions about their exact behavior. |
| RED causality | Partial | Most REDs fail on absent operations/schemas as stated. Job fixture fails for an additional pre-existing helper reason; optional positional `nargs` test can pass while canonical runtime remains broken. |
| GREEN completeness | Fail | Exact job/pipeline/parser code cannot satisfy stated contracts; corpus classifier cannot satisfy end-to-end routing proof. |
| Focused source commands | Valid | Source AGENTS permits `.venv/bin/python -m pytest`; named files exist. Task 6 omits `test_connector_cli_docs.py`. |
| Full source command | Valid but late failure | Full pytest is real and will catch I-10 after the focused docs commit. |
| Hermes commands | Invalid per repository policy | Direct pytest must be replaced by `scripts/run_tests.sh` (I-09). |
| Node vendoring commands | Valid | `vendor-ericsson.mjs` enforces clean full-history source, full SHA, managed inventory, and committed snapshot bytes; tests cover provenance and dirt. |
| Commit scopes | Mostly narrow | Source commits are atomic by operation group; generated authority/output are staged together. Missing test file and cross-plan integration are exceptions. |
| Ignored live output | Planned safely | Runner outputs are directed to ignored/uncommitted paths and no transcript is staged. |
| Worktree/base safety | Locally valid | Hermes feature branches originate at `base`; literal `main` is not used for development. Cross-slice ancestry remains missing. |
| Task ordering within slices | Mostly valid | Operations precede schemas, skills, docs, source gate, then vendor. |
| Task ordering across slices | Fail | I-08. |

## 12. Verified-complete areas

- **Scope accounting is complete.** The SuperCLI 0.14.1 authority contains 46 GitLab rows. The plans correct branch list, job view, MR diff, MR pipelines, pipeline jobs, project search, release list/view, code search, tags, To-Dos, and variable list while deliberately retaining exclusions for webhook listing and all named writes/non-goals.
- **Source-first ownership is correctly chosen.** Connector operations, schemas, skills, CLI descriptors, mapping YAML, onboarding reference, and their tests are all authored in `ericsson-capabilities` before vendoring.
- **The narrow-waist architecture is preserved.** No core model tool, dependency, arbitrary URL fetcher, classifier call, or dynamic tool-array mutation is proposed.
- **Pagination primitives are sufficient.** `_paginate`, `_continuation_source`, deadline/cancellation propagation, and continuation construction support the three slices. The code-search aggregate cap remains honest with mid-page offsets.
- **CI-variable value safety is well planned.** The operation reuses `_variable_metadata`, constructs an allowlisted result, never copies `value`, and includes leakage assertions.
- **Repository discovery operations are implementation-ready in isolation.** Branch/tag separation, code-search caps/redaction, project visibility identity, schemas, CLI leaves, and mapping rows are concrete and source-grounded.
- **Global/project MR operation design is strong.** It preserves the existing project path, uses native global scopes, resolves `@me` once, rejects contradictory actor/scope combinations, and derives cross-project identity without an N+1 fetch loop.
- **Documentation authority is clear.** The migration Markdown is generated from mapping YAML. The onboarding catalog is generated from checked-in capability references and validated. The plans correctly avoid hand-editing generated authority.
- **Vendoring machinery is strong.** The current script rejects dirty/shallow/rewritten source state, records a full 40-character source commit, snapshots committed bytes, limits destinations to managed inventory, and has extensive Node tests.
- **Routing ownership prose is coherent.** CI, repository, release, personal inbox, and selected/project MR ownership is nonduplicative and preserves prompt caching.

## 13. Required corrections ordered by severity and dependency

All are Important; order below minimizes rework.

1. **Fix the shared contracts before any operation implementation:** amend CI Task 2’s documented job optional-member/user/URL projection (I-01) and Task 3’s exact pipeline summary semantics/shape characterization (I-02).
2. **Define the final routing-corpus schema up front:** include all three slices’ existing authorized reads, ordered allowed sequences/terminal outcomes, and multi-intent requirements (I-05/I-06).
3. **Make the runner fail closed at the assistant selection boundary:** scan every bridge `tool_call` underlying name before dispatch-based classification; retain registry stubbing as the network barrier (I-04).
4. **Replace the family alias heuristic:** use explicit, distinct family slots and verify the resolved model identity passed into `AIAgent` (I-07).
5. **Correct optional positional omission semantics:** preserve `argparse.SUPPRESS` or skip optional absent values before schema validation, with parser-to-canonical RED tests (I-03).
6. **Repair the docs test plan:** replace the GitLab literal count with a relational manifest/onboarding invariant and include the file in Task 6 RED/GREEN/staging (I-10).
7. **Add explicit cross-slice integration checkpoints:** later source/Hermes worktrees must start from verified prior full SHAs on `main`/`base`, or an explicitly chained feature-tip strategy (I-08).
8. **Replace all Hermes direct pytest invocations:** use `scripts/run_tests.sh` for baseline, deterministic, and final Python gates (I-09).
9. **Resolve the release/version questions below in the plan text and RED fixtures** before implementing their normalizers; do not leave those API decisions to the implementer.

## 14. Unresolved questions and exact evidence needed

These concerns do not meet the finding proof standard yet; they must nevertheless be resolved before claiming full plan completeness.

### Q1 — Optional release summary/detail members

The release plan tests only populated `name`, `description`, author, and commit, while the spec says commit identity “when present” and the current official [Project release API](https://docs.gitlab.com/api/releases/) makes release name and description optional at creation. Evidence needed: checked-in response fixtures from every supported GitLab version (or an authoritative response schema) showing missing/null behavior for name, description, author, commit, timestamps, milestones, and assets; then exact output `None`/absence rules and RED tests.

### Q2 — Exact release asset projection and URL admission

The plan says to return bounded source/link entries and filter external URLs, but does not list the exact output keys or state whether `direct_asset_url`/`direct_asset_path` is returned, derived, or omitted. GitLab supports link URLs that may be external and permanent direct paths. Evidence needed: official supported-version payload fixtures plus a specified allowlist for source/link fields; tests where `url` and every other returned URL-bearing field independently use same-origin, external, credentialed, query, fragment, malformed, and relative values. No returned URL may bypass `_same_origin_url`/canonical derivation.

### Q3 — Supported GitLab version range for To-Do and job-status enums

Current official [To-Do API](https://docs.gitlab.com/api/todos/) values match the frozen To-Do lists, but neither repository declares the GitLab version range the connector supports. Current Jobs API documentation also includes status vocabulary that can evolve. Evidence needed: an explicit supported GitLab version floor/ceiling or a versioned compatibility table derived from official docs/fixtures, followed by exact filter and remote-value behavior for values added outside the common set. Unknown remote values must fail clearly without silently fabricating facts.

## 15. Source-grounding ledger

`VERIFIED` means read directly in the checked-out repositories; `MISSING` below denotes an explicitly future artifact, not an implementation defect.

| Repository path / symbol | Status | Evidence used |
| --- | --- | --- |
| Hermes `AGENTS.md` | VERIFIED | Read completely; branch and mandatory test-runner rules. |
| Ericsson `AGENTS.md` | VERIFIED | Read completely; source test/vendor expectations. |
| Six frozen spec/plan files | VERIFIED | Read completely; hashes matched. |
| `plugins/ericsson-gitlab/operations.py` | VERIFIED | Request paths, validators, `_same_origin_url`, `_canonical_remote_url`, `_continuation_source`, `_paginate`, `_normalize_user`, `_normalize_merge_request`, `list_merge_requests`, `list_pipelines`, `_variable_metadata`, redaction, commit/ref/time helpers. |
| `plugins/ericsson-gitlab/tools.py` | VERIFIED | Existing schemas and invoke dispatch inventory. |
| `plugins/ericsson-gitlab/client.py` | VERIFIED | JSON/page request, deadlines, safe error boundary. |
| `plugins/ericsson-gitlab/models.py` | VERIFIED | Authentication/config/result model contracts. |
| `plugins/ericsson-gitlab/application.py` | VERIFIED | Safe success/error envelope. |
| `plugins/ericsson-gitlab/__init__.py` | VERIFIED | `_PLUGIN_SKILLS`, plugin registration, write approval pre-hook. |
| `plugins/ericsson-gitlab/_common/` | VERIFIED | Shared plugin helpers inspected. |
| `plugins/ericsson-gitlab/plugin.yaml` | VERIFIED | Existing 30-tool manifest and configuration. |
| Existing four GitLab plugin `skills/*/SKILL.md` | VERIFIED | `repository-research`, `merge-request-review`, `ci-investigation`, `gitlab-activity-digest` read completely. |
| `skills/ericsson/gitlab/SKILL.md` | VERIFIED | Thin always-indexed router and current ownership. |
| `skills/ericsson/onboard-ericsson-capabilities/references/capabilities/gitlab-tools.md` | VERIFIED | Editable onboarding authority and stale fixed prose. |
| Onboarding `build_catalog.py`, `catalog_lib.py`, `validate_catalog.py`, `catalog.json` | VERIFIED | Source-to-generated catalog contract. |
| `plugins/ericsson-connector-cli/descriptors.py` | VERIFIED | GitLab descriptors and `_GITLAB_PROJECT_OPERATIONS`. |
| `plugins/ericsson-connector-cli/parser.py` | VERIFIED | `_argument_kwargs`, argparse defaults, `canonical_arguments`, contract validation. |
| `plugins/ericsson-connector-cli/mappings/supercli-0.14.1.yaml` | VERIFIED | All GitLab source rows and current dispositions. |
| `plugins/ericsson-connector-cli/scripts/build_migration_docs.py` | VERIFIED | YAML authority and generated Markdown/check mode. |
| `docs/cli-migration/supercli-0.14.1.md` | VERIFIED | Current generated output and stale read rows. |
| `docs/connector-porting/gitlab-baseline.md`, `gitlab-behavior-map.md` | VERIFIED | Existing scope/history and behavior authority. |
| Required Ericsson GitLab/client/CLI test files from the prompt | VERIFIED | Helpers, inventories, parser/migration/docs tests, fixed-count test, and current behavior coverage inspected. |
| `sets/ericsson.json`, `docs/README.md`, `docs/configuration.md` | VERIFIED | Vendored source inventory and connector documentation. |
| Future `plugins/ericsson-gitlab/routing_cases.json` | MISSING (expected) | CI Task 7 creates it; plan contract reviewed. |
| Future `release-research` and `personal-inbox` skills | MISSING (expected) | Release Task 6 creates them. |
| Hermes `scripts/vendor-ericsson.mjs` | VERIFIED | Clean-source resolution, provenance, managed inventory, byte copy, transaction behavior. |
| Hermes `scripts/__tests__/vendor-ericsson.test.mjs` | VERIFIED | Full SHA, byte parity, dirt, ignored bytes, source-root, and provenance tests. |
| Hermes `capabilities/ericsson.json`, `ericsson-vendored-paths.json` | VERIFIED | Current vendored source SHA/inventory. |
| Hermes vendored GitLab/connector-CLI/router paths | VERIFIED | Current source-distribution parity surface. |
| Hermes Ericsson distribution/surface/toolset tests | VERIFIED | Plugin enablement, qualified skill lookup, deferred toolsets, managed parity. |
| Hermes `scripts/tool_search_livetest.py` | VERIFIED | Importable helpers, isolated home, module reset, dispatch log, transcript extraction, redaction. |
| Hermes `scripts/LIVETEST_README.md` | VERIFIED | Current live-test invocation/output guidance. |
| Hermes `model_tools.handle_function_call` | VERIFIED | Bridge unwrapping, pre-hook order, registry dispatch order. |
| Hermes `tools/registry.py` | VERIFIED | Registry registration/dispatch path. |
| Hermes `tools/tool_search.py` | VERIFIED | Deferred catalog, search/describe/call bridge behavior. |
| Hermes `tools/skills_tool.py`, plugin manager/skill loader, `agent/skill_commands.py` | VERIFIED | Bundled and qualified plugin skill discovery behavior. |
| Hermes `run_agent.py` ordinary loop | VERIFIED | Stable conversation/tool loop used by the proposed runner. |
| Future Hermes `scripts/gitlab_skill_routing_livetest.py` | MISSING (expected) | CI Task 9 creates it; exact proposed predicate reviewed. |
| Official GitLab Jobs/Releases/To-Do docs | VERIFIED REMOTE DOCUMENTATION | Read-only official documentation, not a live GitLab instance; remote claims are separately identified above. |

## 16. Exact command and evidence ledger

### Commands actually run

No command contacted a GitLab instance, created a worktree/branch, ran vendoring, staged, committed, or changed a repository ref.

| Purpose | Exact command or command family actually run | Result |
| --- | --- | --- |
| Repository identity/state | `git branch --show-current`; `git rev-parse HEAD`; `git status --short --branch`; `git worktree list` in each repository | States recorded in Section 1. |
| Immutable hashes | `sha256sum docs/superpowers/specs/2026-08-30-ericsson-gitlab-{ci-read-coverage-design,repository-discovery-design,release-inbox-design}.md docs/superpowers/plans/2026-08-30-ericsson-gitlab-{ci-read-coverage,repository-discovery,release-inbox}.md` | All six matched. |
| Platform | `uname -a` | Darwin 25.5.0 arm64. |
| Full/targeted file reads | `sed -n ...`, `nl -ba ...`, `rg -n ...`, `find ... -name SKILL.md -print | sort`, and `rg --files` over the exact paths listed in the source-grounding ledger | Direct source evidence collected; no writes. |
| SuperCLI row inventory | `rg -n "source_command: super-cli gitlab ..." plugins/ericsson-connector-cli/mappings/supercli-0.14.1.yaml` plus structured YAML inspection | All 46 GitLab rows accounted for. |
| Optional positional behavior | `python3 - <<'PY' ... p.add_argument('project', nargs='?'); print(vars(p.parse_args([]))) ... PY` | Printed `{'project': None}`. |
| Output-file preservation | `test -e docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-codex-5-6-xhigh.md; printf '%s\n' $?` | Returned `1` before report creation. |
| Official remote documentation | Read-only web search/open/find restricted to `docs.gitlab.com/api/jobs/`, `docs.gitlab.com/api/releases/`, and `docs.gitlab.com/api/todos/` | Documented payload/query/enum evidence; no GitLab API/server request. |

No pytest or Node suite was executed: the challenged premises were resolved by direct source, plan text, standard-library argparse behavior, and official response documentation. This avoids presenting current pre-feature tests as proof of future behavior.

### Commands merely inspected in the plans, not run

- Every proposed `"$SOURCE_PY" -m pytest ...` focused/full source command.
- Every proposed `"$HERMES_PY" -m pytest ...` command; these are rejected by I-09.
- Every proposed `scripts/run_tests.sh ...` correction.
- `plugins/ericsson-connector-cli/scripts/build_migration_docs.py` write/check commands.
- Onboarding `build_catalog.py`, `validate_catalog.py` write/check commands.
- `node scripts/vendor-ericsson.mjs` and `node --test scripts/__tests__/vendor-ericsson.test.mjs`.
- All paid-model `scripts/gitlab_skill_routing_livetest.py --model ...` commands.
- All `git worktree add`, `git add`, `git commit`, diff-against-branch, integration, and ancestry commands printed by the plans or recommended by this review.

IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.
