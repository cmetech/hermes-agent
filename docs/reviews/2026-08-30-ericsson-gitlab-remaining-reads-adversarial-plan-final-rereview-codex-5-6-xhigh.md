# Final blocker-only rereview — remaining Ericsson GitLab reads

## 1. Identity and verified hashes

- Reviewer: Codex GPT-5.6, xhigh reasoning, independent final rereview.
- Review date: 2026-08-30.
- Review mode: plan review only; no source, spec, plan, reconciliation, credential, or real-GitLab mutation/access.
- Independence: I read the exact final-rereview prompt and the reconciliation it authorizes, but no Claude report and no prior Codex report. I treated reconciliation statements as claims and checked them against the final plans and named source paths.
- Ericsson source inspected at `ericsson-capabilities` HEAD `0d7654d14db0afe0c688a752a2676d8cabe2f981` (`main`, clean).
- Hermes inspected at HEAD `4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944` (`base`).

All seven review inputs matched the prompt before review and again immediately before this report was written:

| SHA-256 | Input |
|---|---|
| `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` | `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` |
| `43a7463614f07c6609582d95eaf6875e345e248fa96c0fa0d101d70e97b0a085` | `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` |
| `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f` | `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` |
| `5f4257377e7ee79a992b962d76654bf6acf76e0aaa7c5149f1315ac9ef572ed6` | `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` |
| `7e2f5a10075b19ea65214e3d4e2e620adfec2e92b5465759d9dcc18c35535bac` | `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` |
| `cb3ba13d29c8d0bcb23c2dfcee21809090bcffeee1730589e35e572e3530cc37` | `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` |
| `ea6a54d16496c5ea969ff314e2c81c72b13277d8cb0e5789a405353211f486c5` | `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md` |

## 2. Verdict

**BLOCK**

There is one Important plan defect. The tag-listing slice does not specify or test the documented `message: null` shape returned for a normal lightweight Git tag. The listed gates can therefore all pass while the implementation rejects a valid GitLab response.

## 3. Critical/Important findings

| ID | Severity | Exact task/repository | Finding |
|---|---|---|---|
| R-01 | Important | Repository Discovery Task 3, `ericsson-capabilities` | Tag normalization does not require or test nullable `message`, although GitLab's documented List Tags response uses `message: null` for a lightweight tag. |

### R-01 — documented lightweight tags can invalidate the entire tag-list response

**Exact task/repository**

Repository Discovery, Task 3 (“Add tag listing without expanding release data”), in `ericsson-capabilities`.

**Source path, line, and symbol**

- Planned implementation symbol: `plugins/ericsson-gitlab/operations.py`, `GitLabOperations._normalize_tag` / `GitLabOperations.list_tags` (the symbols do not exist in the current source and are introduced by this task).
- Plan evidence: `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md:278-349`. In particular, the required tests say “bounded message, nullable creation time” at line 325, and the implementation says “redacted bounded message” at line 340. Neither requires a `None` branch for `message`; only `created_at` is called nullable.
- The Task 3 sample fixture supplies a string message, so it cannot distinguish a nullable implementation from a strict-string implementation.
- API evidence: GitLab's official Tags API, List project repository tags example, returns a lightweight tag with `"message": null`: <https://docs.gitlab.com/api/tags/>.

**Violated invariant**

A documented, ordinary read response must normalize successfully without leaking data or weakening strict validation. Optional metadata may be `null`; strict validation should reject malformed non-null values, not a documented null.

**Concrete failure**

An otherwise valid project can contain a lightweight tag. GitLab returns that entry with `message: null`. An implementer following the current plan can reasonably apply the task's “bounded message” rule as a mandatory bounded string. `_normalize_tag` then raises `invalid_remote_data`; because tag items are normalized while processing the list page, one lightweight tag can fail `gitlab_list_tags` rather than returning the page. The release/productivity skill later exposes tag listing as a neighboring read, so this is user-visible, not merely a fixture discrepancy.

**Why the current steps do not fix it**

Task 3 explicitly calls out and tests nullable `created_at`, but not nullable `message`. Its positive fixture uses a string message, its malformed cases concern other fields, and both focused and full-suite gates can pass with a strict-string normalizer. The repository design likewise says only “bounded tag message,” so no later task supplies the missing contract.

**Smallest correction**

Amend Repository Discovery Task 3 (and its design contract) to state that `message` is nullable. Require `_normalize_tag` to preserve `None`, and to redact/bound/validate only non-null string messages. Add one positive test using the official lightweight-tag shape and assert that the result contains `message is None`. Keep malformed non-null messages strict.

**Runnable RED/GREEN**

Add the required test as `test_list_tags_accepts_documented_null_message`, then run in the plan's source-worktree shell:

```bash
cd "$SOURCE_WT"
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_exploration.py \
  -k 'list_tags and documented_null_message'
```

- RED: after the current Task 3 wording is implemented with a strict bounded-string message normalizer, the test fails on `message: null` (or the call returns `invalid_remote_data`).
- GREEN: after the explicit nullable branch is added, the test passes and still exercises no real GitLab I/O.

## 4. Prior-blocker disposition

| # | Mandatory regression check | Disposition | Independent evidence |
|---|---|---|---|
| 1 | Optional MR positional preserves `SUPPRESS` | **RESOLVED** | Release plan lines 702-715 remove `dest`, but remove `default` only for required/repeatable positionals; an optional nonrepeatable positional retains the descriptor's `argparse.SUPPRESS`. The planned absent-positional assertion locks canonical `{}` behavior. This corrects current `parser.py:213-247`, whose final cleanup removes the default for every positional. |
| 2 | Backward-compatible MR test passes before extension | **RESOLVED** | Release Task 5 Step 1, lines 460-483, adds and explicitly runs the existing-call characterization before the failing global/native-scope tests and implementation. |
| 3 | Every fixed inventory is named, gated, and committed | **RESOLVED** | CI Tasks 5-6 name source plugin, CLI-port, descriptor, docs, onboarding, and Hermes parity inventories. Release Task 8 additionally names and gates `tests/hermes_cli/test_ericsson_connector_distribution.py`, `tests/hermes_cli/test_ericsson_connector_surfaces.py`, and `tests/plugins/workflow/test_installed_distribution_e2e.py` (lines 870-916, 947-956), then commits their updates. I checked the current exact inventories at those paths and the source exact-count/set tests. |
| 4 | `gitlab_read_pipeline` owner; cumulative `intent_tools` | **RESOLVED** | CI Task 7 explicitly assigns `gitlab_read_pipeline`, creates top-level `read_tools` as the complete registered-read union, creates nonempty `intent_tools`, validates every mapping as a subset, and makes later slices append to both (`CI plan:1057-1214`; Repository plan:644-661; Release plan:808-818). The routing runner consumes both maps (`CI plan:1333-1381`). |
| 5 | Baselines precede later tests; `-k` selects intended tests | **RESOLVED** | Each slice baseline is before its test creation and references only current files. Focused source gates name selectors introduced in the corresponding task, and full source gates follow. One redundant selector term is noted under Minor notes, but it does not make the focused gate empty or hide the changed behavior. |
| 6 | Visible-project search uses documented Search API fields only | **RESOLVED** | Repository Task 5's fixture/normalizer is restricted to `id`, `name`, `path_with_namespace`, `description`, `default_branch`, `last_activity_at`, and `web_url` (`Repository plan:459-518`), matching the official project-scope Search API example. The prior invented fields are gone. |
| 7 | Parity receives source directory and exact SHA; fails closed | **RESOLVED** | CI Task 0 makes both `ERICSSON_CAPABILITIES_DIR` and `ERICSSON_CAPABILITIES_EXPECTED_SHA` mandatory and derives comparisons from the vendor ledger (`CI plan:102-183`). Every parity invocation in all three plans supplies both values and the integrated source SHA; omission/mismatch is a failure rather than a skip. |
| 8 | Matching native scope plus `@me` emits native scope only | **RESOLVED** | Release Task 5 requires each matching combination to send only native `scope`, omit the actor parameter, and make no `/user` request (`Release plan:544-594`). Contradictory combinations are separately rejected. |
| 9 | Direct pytest only in source; Hermes wrapper in Hermes | **RESOLVED** | All direct `"$SOURCE_PY" -m pytest` commands run against the source worktree. Hermes tests consistently use `scripts/run_tests.sh`, including fixed-inventory, routing, installed-distribution, and parity gates. |
| 10 | Routing evaluator safety and adversarial coverage | **RESOLVED** | CI Task 9 constrains tool names from corpus `read_tools`, captures/rejects any pre-approval write attempt, injects only named fake provider credentials, sets `copy_auth=False`, forbids source `.env`/`auth.json` copying, uses recorded tools rather than GitLab dispatch, exercises exact model namespaces for two model families, and rejects missing, extra, or wrong-order routes (`CI plan:1327-1404`). |

No prior blocker is reopened. R-01 is new API-shape evidence in the amended tag-listing task.

## 5. Cross-plan/integration audit

- **Slice ancestry and vendoring:** the CI slice starts from a clean source/vendor reconciliation, then each repository/release slice requires exact SHA ancestry. Vendor parity is fail-closed and run after each vendoring step. No plan asks Hermes to become the source of truth.
- **Stable conversation/tool boundary:** all capability remains in the existing Ericsson plugin/router/skill path. The plans add no core tool, dependency, classifier call, dynamic tool swap, webhook, or write operation. The permanent tool array and system prompt remain stable.
- **Cumulative routing contract:** CI creates the complete `read_tools` and `intent_tools` contract; repository and release append their tools and intents rather than replacing earlier mappings. Skill ownership covers all reads, including the formerly unowned pipeline detail read.
- **Inventory propagation:** source registry/CLI/descriptor/docs inventories are updated in their source tasks; Hermes distribution/surface/installed-distribution inventories are updated after exact vendoring. The installed-wheel e2e inventory is both in the focused gate and in the commit step.
- **Cross-slice defect impact:** R-01 originates in Repository Task 3 and survives exact vendoring. Release Task 8 validates presence and routing, not the missing documented null shape, so no downstream gate repairs it.
- **Secrets and writes:** planned tests use fake credentials, avoid real GitLab, and add reads only. No source `.env`, `auth.json`, PAT, or tool output is authorized to enter committed artifacts.

## 6. Routing-evaluator audit

| Property | Result | Evidence |
|---|---|---|
| Detect a write before approval/dispatch | PASS | Transcript-boundary logic records a hard failure immediately for any write-shaped call, including before read-route completion. |
| No real GitLab I/O | PASS | The evaluator records proposed tool calls and returns deterministic synthetic results; it does not dispatch GitLab tools. |
| No source `.env` or `auth.json` copy | PASS | Harness extension accepts explicit provider credential keys, writes only named fake values, and uses `copy_auth=False`; planned artifact assertions reject credential/tool-output leakage. |
| Two model families | PASS | The plan names exact live-test model namespaces from two distinct families and keeps opt-in gating. |
| Complete and ordered routes | PASS | Required ordered paths come from corpus cases; missing, extra, out-of-order, unknown, or write calls fail. |
| Cumulative intent/tool contract consumed | PASS | Runner validates `required_intent` through `intent_tools`, validates routes against `read_tools`, and the later slices extend those same maps. |

I found no Critical/Important routing-evaluator defect.

## 7. Minor notes

1. CI Task 3's focused selector includes `read_pipeline`, but current `read_pipeline` tests are in `tests/test_gitlab_reads.py`, while the command targets only `tests/test_gitlab_ci.py` (`CI plan:660-661`). The same expression still selects the newly required `list_pipelines` and merge-request-pipeline characterization tests, and Task 3 does not change `read_pipeline`, so this is non-blocking. Removing the redundant term or adding the existing test file would make the gate's stated coverage more literal.

## 8. Final gate statement

**BLOCK.** R-01 is an Important executable-contract gap: a documented, commonplace lightweight-tag response can be rejected while every planned test remains green. Amend the Repository Task 3 design/plan and add the nullable-message regression. Apart from R-01, I found no other Critical or Important defect, and all ten prior blockers are resolved.
