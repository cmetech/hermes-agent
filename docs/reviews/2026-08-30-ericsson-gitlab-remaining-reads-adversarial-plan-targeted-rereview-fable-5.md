# Targeted adversarial rereview — remaining Ericsson GitLab reads (Claude Fable 5, xhigh)

## 1. Identity

- Reviewer: Claude Fable 5 (`claude-fable-5`), xhigh effort, independent
  targeted rereview per
  `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-targeted-rereview-prompt.md`.
- Date: 2026-08-30.
- Hermes checkout: `base` @ `4e8c3d61d2`; source checkout:
  `ericsson-capabilities` `main` @ `0d7654d14db0afe0c688a752a2676d8cabe2f981`
  (the Hermes vendored `operations.py` is byte-identical to source).
- Inputs were read from disk in full. No spec, plan, or source file was edited;
  this report is the only write.
- Live web access (WebFetch/WebSearch) was not permitted in this session; the
  one GitLab API fact used (Tags API `message: null`) is stated from the
  official Tags API reference (`docs.gitlab.com/api/tags/`) list/single-tag
  examples and is the same shape the prior Codex R-01 finding cited.

## 2. Frozen hash verification

Computed with `sha256sum` against the six files on disk; all six match.

| File | SHA-256 (observed) | Expected |
|---|---|---|
| `specs/…-ci-read-coverage-design.md` | `485d3dfe…04b6922` | match |
| `specs/…-repository-discovery-design.md` | `9fac07b9…7606fa6` | match |
| `specs/…-release-inbox-design.md` | `3a158cb2…bc01858f` | match |
| `plans/…-ci-read-coverage.md` | `9ec40e40…72534f04` | match |
| `plans/…-repository-discovery.md` | `b4795689…54d7007` | match |
| `plans/…-release-inbox.md` | `cb3ba13d…30cc37` | match |

Full digests: `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922`,
`9fac07b9d4fb6d93795e74b4caf1b7f83ddd7a883f286f9045673ccf57606fa6`,
`3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f`,
`9ec40e40053282cdf55a30ac23febcd820b3726a490f43dca68ee55d72534f04`,
`b47956899687ee4f270485a27f6c0418662533926b5ad144b1954d6f3f4d7007`,
`cb3ba13d29c8d0bcb23c2dfcee21809090bcffeee1730589e35e572e3530cc37`.

## 3. Required checks

| # | Check | Result | Exact evidence |
|---|---|---|---|
| 1 | Task 0 parity test receives `ERICSSON_CAPABILITIES_DIR` / `ERICSSON_CAPABILITIES_EXPECTED_SHA` through `scripts/run_tests.sh` despite `env -i`; plan stages/tests the wrapper change | **PASS** | CI plan `:110-112` adds `scripts/run_tests.sh` and the parity test to Task 0 Files; `:159-163` "extend its explicit `_test_var` allowlist with exactly these two variable names" + a guard "that asserts the wrapper contains both names"; `:170-173` runs the parity test *through* `scripts/run_tests.sh` and stages `scripts/run_tests.sh`, the parity test and the snapshot together; `:175-179` runnable command. End-to-end path verified in source: `scripts/run_tests.sh:144-150` — the `for _test_var in …` loop appends `NAME=value` to `TEST_ENV` for any listed name via `${!_test_var}` indirection; `:299-316` `exec env -i … ${TEST_ENV[@]+"${TEST_ENV[@]}"} …` (`:303`) forwards it into `run_tests_parallel.py`; `scripts/run_tests_parallel.py:717-723` spawns each per-file pytest with `env=os.environ` (inherits the forwarded pair); `tests/conftest.py:453-468` autouse `_hermetic_environment` deletes only `_looks_like_credential(name)` matches (`:246-250`: exact `_CREDENTIAL_NAMES` `:158-243` or suffixes `:139-155` — `_API_KEY/_TOKEN/_SECRET/_PASSWORD/_CREDENTIALS/…_KEY`) and `_HERMES_BEHAVIORAL_VARS`; neither name ends in a listed suffix and `grep ERICSSON tests/conftest.py` is empty, so both survive into the test body. `PATH`/`HOME` are forwarded (`:300-301`), so the test's `git -C "$ERICSSON_CAPABILITIES_DIR" rev-parse HEAD` works exactly as the existing `tests/ericsson_connector_source.py:73-82` does. The wrapper is exercised by the same `scripts/run_tests.sh` invocation and the in-test guard; no existing test pins the allowlist (`git grep` for `_test_var`/`TEST_ENV`/`HERMES_TEST_IMAGE` in `tests/test_run_tests_parallel.py` is empty). |
| 2 | Strict pipeline SHA validation does not break existing valid fixtures in `tests/test_gitlab_reads.py`; plan owns/updates/runs/commits that file and keeps an isolated invalid-short-SHA case | **PASS** | CI plan Task 3 Files `:526-528` now lists `tests/test_gitlab_reads.py`; Step 1 `:595-600` names the exact fixtures (`"abc"`/`"def"`), orders the replacement *before* enabling the strict normalizer, keeps the foreign-origin fixture's SHA valid "so that case isolates URL validation", and requires "a separate short-SHA case that asserts `invalid_remote_data`"; Step 4 `:674-679` gate runs both files with `-k 'pipeline_jobs or merge_request_pipelines or list_pipelines or pipeline_list or read_pipeline'` (selects `test_list_pipelines_is_bounded…` `:671`, `test_pipeline_list_rejects_cross_origin…` `:686`, and the `test_read_pipeline_*` tests `:731-858`); Step 5 `:684-687` commits the file; Task 8 `:1260` and the full run `:1271` re-cover it. Source verification: the only short SHAs on a success path in either repository are `tests/test_gitlab_reads.py:677,678,697` (repo-wide search of `"sha": "` in `tests/` and `plugins/`; every other value is `"a"*40`/`"b"*40` or a deliberate `pytest.param` negative `:611-614,:779`); `read_pipeline` fixtures already use `"a" * 40` (`:716`). Walking the planned `_normalize_pipeline_summary` (CI plan `:617-647`) over the corrected fixtures: `id`/`iid` → `_remote_positive_int` (`operations.py:184-187`) OK; `ref` `"main"`/`"dev"` → `_validate_remote_ref` (`:168-171` via `_git_ref_is_valid` `:140-159`) OK; 40-hex → `_commit_sha` (`:316-326`) OK; `status`/`source` non-empty bounded OK; `"2026-08-09T00:00:00Z"` → `_rfc3339(remote=True)` (`:302-313`) normalizes to the same string; `web_url` `{ORIGIN}/x/y/-/pipelines/3` → `_same_origin_url` (`:202-217`) OK — so the valid-case assertions (`ids == [3, 2]`, `truncated`, `continuation == {"next_page": 3}`) hold, and the cross-origin case fails on `web_url` alone once its SHA is valid. Hermes-side tests never drive the vendored `list_pipelines` normalizer (`tests/cron/test_ericsson_gitlab_activity_digest.py` asserts prompt text only; no pipeline fixtures exist under `tests/cron`, `tests/hermes_cli`, `tests/plugins/workflow`), and `gitlab_inspect_ci` uses its own collectors, so no other existing fixture is affected. |
| 3 | Tags API documented `message: null` accepted and tested; malformed non-null values remain strict | **PASS** | Repository spec `:101-106`: "nullable bounded message … A null message is preserved as `None`; a non-null message is redacted and bounded, and any other shape is invalid remote data … does not require an undocumented `web_url`". Repository plan Task 3 Step 1 `:325-331` requires `test_list_tags_accepts_documented_null_message` asserting `message is None` **and** "a separate malformed non-null message case asserting `invalid_remote_data`", and forbids inventing a tag `web_url`; Step 3 `:341-348` implements "`None` for a null message or a redacted bounded string for a non-null message … Reject any other message shape as invalid remote data" and derives the URL from project path + percent-encoded tag name. This matches the official Tags API list/single examples (`"message": null` for a lightweight tag, no `web_url` member, `order_by` ∈ `name|updated|version`, `sort` ∈ `asc|desc`); the plan's `name|updated` subset and `asc|desc` are documented values. Spec and plan are consistent; the positive fixture at `:299-311` still uses a string message so both branches are covered by distinct tests. |

## 4. New Critical/Important findings

One Important finding. It is not one of the three corrected items and was not
reported in either prior final rereview; it is a consequence of the amended
Task 0 contract as it now stands.

### NEW-A — Important — the fail-never-skip parity test turns Hermes `base` CI red on every push

- **File/lines:** CI plan
  `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:154-159`
  ("a deterministic Hermes test that *requires* `ERICSSON_CAPABILITIES_DIR` and
  `ERICSSON_CAPABILITIES_EXPECTED_SHA` … It fails, never skips, when either
  input is absent") together with `:172-173` ("Commit and integrate that exact
  snapshot on Hermes `base`"). No task marks or otherwise excludes the test
  from default discovery.
- **Why it fails:** `.github/workflows/ci.yaml:17-45` runs on every push to
  `base` (and every PR) and calls `./.github/workflows/tests.yml`
  (`ci.yaml:100-101`), whose step is a bare `scripts/run_tests.sh`
  (`tests.yml:90-100`) — discovery of all of `tests/**` except the
  `integration`/`e2e`/`docker` *directories* (`run_tests_parallel.py:349`).
  `tests/hermes_cli/test_ericsson_vendor_parity.py` is therefore collected;
  the private source checkout is not present and neither variable is set, so
  the test fails by design on every CI run of `base`, permanently. The
  `addopts = -m 'not integration'` deselection (`pyproject.toml:535-547`) only
  helps a test that carries the `integration` marker, which the plan does not
  add. The repo's existing precedent for this exact input pair skips when both
  are absent and fails only on partial/mismatched input
  (`tests/ericsson_connector_source.py:115-137`).
- **Impact:** the fork re-enabled Python CI on `base` specifically to catch
  drift (`ci.yaml:20-45`); a deterministic red file removes that signal for
  every later slice and every unrelated change. The implementer has no
  plan-authorized fix — skipping violates "never skips", and unmarking CI is
  not theirs to do — the same "left to choose" situation the prior NEW-02
  identified.
- **Smallest correction (CI plan Task 0 Step 3):** keep the fail-closed body,
  but declare `pytestmark = pytest.mark.integration` in
  `tests/hermes_cli/test_ericsson_vendor_parity.py` so default discovery
  deselects it, and append `-m integration` to the eight explicit parity
  invocations (CI plan Task 0 §3 `:175-179`, Task 1 §3 `:258-260`, Task 9 §5
  `:1428-1430`, Task 10 §1 `:1503-1505`; repository plan Task 1 §2 `:146-148`,
  Task 8 §3 `:784-786`; release plan Task 1 §2 `:154-156`, Task 8 §4
  `:954-956`). The wrapper forwards `-m <mark>` as a pytest value flag
  (`run_tests_parallel.py:1206-1211`), its own ledger path already appends
  `-m integration` for an integration-marked file (`scripts/run_tests.sh:266-275`),
  and `tests/tools/test_browser_supervisor.py:21` documents the same usage. A
  command-line `-m` overrides the `addopts` value, so the explicit gates still
  run the test and it still fails — never skips — when invoked without its
  inputs. (Alternative, if the marker route is rejected: mirror
  `tests/ericsson_connector_source.py:117-125` — skip only when *both* inputs
  are absent, fail on partial or mismatched input — but that changes the
  accepted fail-closed contract, so the marker is the smaller change.)
- **RED (after Task 0 as written, in `$HERMES_WT`, no ERICSSON vars set — this
  is exactly what `tests.yml:100` executes):**

```bash
cd "$HERMES_WT"
scripts/run_tests.sh -q tests/hermes_cli/test_ericsson_vendor_parity.py   # expect: FAIL (inputs absent)
```

- **GREEN (after the correction):**

```bash
cd "$HERMES_WT"
scripts/run_tests.sh -q tests/hermes_cli/test_ericsson_vendor_parity.py   # expect: deselected / no tests ran, runner exit 0
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" ERICSSON_CAPABILITIES_EXPECTED_SHA="$SOURCE_SHA" \
  scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py   # expect: PASS
scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py   # expect: FAIL — fail-closed preserved
```

No Critical finding. No other Important regression was found in the amended
CI plan, repository plan, or repository spec (the three files whose hashes
changed since the final rereview); the release plan and the CI/release specs
are byte-identical to the previously reviewed versions.

## 5. Non-blocking notes (not reopened; listed only for the implementer)

1. CI plan Task 3 Step 1 does not name the new short-SHA negative test; name it
   with `list_pipelines`/`pipeline_list` so the Step 4 `-k` expression selects
   it (otherwise it is first exercised by Task 8's full run).
2. Repository plan Task 3 says a non-null tag message is "bounded"
   (`_MAX_TAG_MESSAGE = 8192`) but not whether an over-long message is
   truncated or rejected; either is safe — state one and test it.
3. Naming drift remains between `ERICSSON_CAPABILITIES_EXPECTED_SHA` (plans)
   and the existing `ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA`
   (`tests/ericsson_connector_source.py:16`); cosmetic.

## 6. Verdict

**BLOCK.** All three required checks pass end to end on source evidence
(wrapper allowlist → `env -i` → per-file `env=os.environ` → conftest does not
blank the pair; the three short-SHA fixtures are the only affected ones and the
plan owns, corrects, gates, and commits them; nullable tag `message` is
specified, tested positively, and kept strict for malformed non-null values).
One Important plan defect remains (NEW-A): the fail-never-skip parity test is
collected by the default `scripts/run_tests.sh` run that `base` CI executes on
every push, so Task 0 as written commits a permanently red test to `base`. The
correction is one marker plus `-m integration` on the eight explicit parity
invocations.
