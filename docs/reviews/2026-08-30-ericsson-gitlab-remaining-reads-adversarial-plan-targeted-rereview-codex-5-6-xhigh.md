# Targeted adversarial rereview — remaining Ericsson GitLab read plans

## Identity and frozen inputs

- Reviewer/model: Codex GPT-5.6 Sol, xhigh reasoning.
- Review date: 2026-08-30.
- Review basis: current frozen artifacts read from disk, current source at `ericsson-capabilities/main` (`0d7654d14db0afe0c688a752a2676d8cabe2f981`), and current Hermes `base` (`4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944`). Earlier reports were not used as evidence.

All six hashes matched before review and again before report creation:

| Artifact | Verified SHA-256 |
|---|---|
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `9fac07b9d4fb6d93795e74b4caf1b7f83ddd7a883f286f9045673ccf57606fa6` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `9ec40e40053282cdf55a30ac23febcd820b3726a490f43dca68ee55d72534f04` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `b47956899687ee4f270485a27f6c0418662533926b5ad144b1954d6f3f4d7007` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `cb3ba13d29c8d0bcb23c2dfcee21809090bcffeee1730589e35e572e3530cc37` |

## Required corrections

| Check | Result | Exact evidence |
|---|---|---|
| 1. Parity inputs cross Hermes `scripts/run_tests.sh`'s `env -i` boundary; wrapper change is staged and tested | **PASS** | Current `scripts/run_tests.sh:144-150` builds the explicit `TEST_ENV` allowlist before environment stripping, and line 303 injects that array into the normal `env -i` execution. `scripts/run_tests_parallel.py:717-724` passes `os.environ` to each per-file pytest child. `tests/conftest.py:461-468` removes credential-shaped and enumerated behavioral `HERMES_*` variables only; neither required Ericsson variable matches those filters. CI Task 0 Step 3 (`CI plan:150-178`) adds exactly `ERICSSON_CAPABILITIES_DIR` and `ERICSSON_CAPABILITIES_EXPECTED_SHA` to `_test_var`, requires a guard for both names, runs the parity test through the wrapper with both values, stages `scripts/run_tests.sh` and the parity test together, and integrates that commit before Task 1. Because the parity test fails rather than skips when either input is absent, the wrapper invocation is itself an end-to-end propagation test, not merely a textual guard. |
| 2. Strict pipeline SHA validation preserves valid `test_gitlab_reads.py` fixtures and retains an isolated short-SHA rejection | **PASS** | Current source has exactly the three affected placeholders at `tests/test_gitlab_reads.py:677`, `:678`, and `:697`; the first two are the valid paginated list and the third is the foreign-URL isolation case. `_commit_sha` requires 40 hex characters for full remote SHAs (`operations.py:316-326`). CI Task 3 explicitly replaces all three placeholders with 40-character hexadecimal SHAs before enabling the shared normalizer, keeps the foreign-origin fixture's SHA valid, and adds a separate short-SHA `invalid_remote_data` case (`CI plan:595-600`). The GREEN gate runs both `tests/test_gitlab_ci.py` and `tests/test_gitlab_reads.py` with selectors covering `list_pipelines` and `pipeline_list` (`:674-680`); the commit stages `tests/test_gitlab_reads.py` (`:682-687`); Task 8 then runs the whole file and the complete source suite (`:1251-1274`). No existing assertion depends on the placeholder SHA text. |
| 3. Documented Tags API `message: null` is accepted while malformed non-null messages remain strict | **PASS** | The revised repository design explicitly preserves null as `None`, bounds/redacts non-null strings, and rejects every other shape (`repository design:101-106`). Repository Task 3 adds the named positive `test_list_tags_accepts_documented_null_message`, asserts `message is None`, and adds a separate malformed non-null rejection (`repository plan:325-331`). Its implementation contract repeats the null/string/other three-way branch (`:339-348`), and the focused test is run before the task commits operation and test together (`:333-356`). This matches GitLab's official Tags API, which documents lightweight tags with both `message: null` and `created_at: null`: <https://docs.gitlab.com/api/tags/>. |

## New Critical/Important findings

None. The amendments do not introduce a new core tool, dependency, write, webhook surface, dynamic tool-array change, source/vendor inversion, or test/commit ownership gap.

## Final verdict

**PASS**

All three required corrections are executable end to end, and no Critical or Important implementation-plan defect remains in the frozen artifacts.
