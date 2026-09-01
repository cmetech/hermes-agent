# Convergence rereview — remaining Ericsson GitLab reads (Claude Fable 5, xhigh)

## 1. Identity

- Reviewer: Claude Fable 5 (`claude-fable-5`), xhigh effort, independent
  convergence rereview per
  `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-convergence-rereview-prompt.md`.
- Date: 2026-08-30. Hermes `base` @ `4e8c3d61d2`; source
  `ericsson-capabilities` `main` @ `0d7654d14db0afe0c688a752a2676d8cabe2f981`.
- Scope: recheck of my prior finding NEW-A (targeted rereview) against the
  newly amended artifacts, plus the two non-blocking clarifications. All files
  were read from disk; no spec, plan, or source file was edited; this report is
  the only write.

## 2. Frozen hash verification

Computed with `sha256sum`; all six match the prompt.

| File | Observed SHA-256 | Result |
|---|---|---|
| `specs/…-ci-read-coverage-design.md` | `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` | match (unchanged since prior round) |
| `specs/…-repository-discovery-design.md` | `e80f75ae8af2a8e64b6f37017fd732da3504ea84805248394460a0c24eba6a30` | match (amended) |
| `specs/…-release-inbox-design.md` | `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f` | match (unchanged) |
| `plans/…-ci-read-coverage.md` | `c0fe22fb9ecdfa97bf3addadaaa69a3a3e4c97edc1325410c18b30b51c4af028` | match (amended) |
| `plans/…-repository-discovery.md` | `8269698319f777f6e6ee808c9cd91e2a10bfc57b036124108de9f54b989263e7` | match (amended) |
| `plans/…-release-inbox.md` | `f50b88b603a54807b4cf8a8f8fa2c7ed789908135ce726e0f6642b9b424f0888` | match (amended) |

## 3. Required check — NEW-A disposition

| Sub-check | Result | Source-backed evidence |
|---|---|---|
| Parity module marked `pytest.mark.integration`; default CI deselects it without failing the per-file runner | **PASS** | CI plan `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:158-160`: "Mark the module `pytest.mark.integration` so default Hermes CI deselects this external-source gate". Default CI is a bare `scripts/run_tests.sh` (`.github/workflows/tests.yml:90-100`, called from `ci.yaml:100-101` on every push to `base`, `ci.yaml:17-45`); every per-file pytest inherits `addopts = "-m 'not integration'"` (`pyproject.toml:547`), so a fully marked module collects zero items → pytest exit 5, which `scripts/run_tests_parallel.py:642-645` explicitly treats as a pass ("every test in the file was skipped or filtered by a marker (e.g. `-m 'not integration'`) … not a failure mode"). `tests/conftest.py:1252-1286` `pytest_collection_modifyitems` only handles host-OS marks and `requires_wal`; it neither skips nor rewrites `integration`-marked items, so the deselection is purely the marker. |
| All eight explicit parity commands use `-m integration` and supply both inputs | **PASS** | Exactly eight commands, each `ERICSSON_CAPABILITIES_DIR=… ERICSSON_CAPABILITIES_EXPECTED_SHA=… scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py`: CI plan `:179-181` (Task 0 §3), `:261-263` (Task 1 §3), `:1432-1434` (Task 9 §5), `:1507-1509` (Task 10 §1); repository plan `:146-148` (Task 1 §2), `:786-788` (Task 8 §3); release plan `:154-156` (Task 1 §2), `:954-956` (Task 8 §4). CI plan `:160` also states the rule ("every explicit parity command must use `-m integration`"). A repo-wide `git grep` for `ERICSSON_CAPABILITIES` across the three plans finds no other parity invocation. The flag reaches pytest: `scripts/run_tests.sh:299-316` forwards bare flags, and `run_tests_parallel.py:1206-1211` lists `-m` in `PYTEST_VALUE_FLAGS` so `integration` is peeled as its value rather than mistaken for a path; each file then runs as `python -m pytest <file> -q -m integration --basetemp=…` (`:705-712`). pytest's `-m` is `action="store"` (`.venv/lib/python3.11/site-packages/_pytest/mark/__init__.py:110-118`) and `addopts` are prepended to the command line, so the command-line `-m integration` overrides the ini `-m 'not integration'` — the same mechanism the wrapper's own ledger path relies on (`scripts/run_tests.sh:266-275`) and `tests/tools/test_browser_supervisor.py:21` documents. |
| When explicitly selected without either input, the test still fails rather than skips | **PASS** | CI plan `:160-161`: "When selected, the test fails rather than skips if either input is absent." Under `-m integration` the module's tests are selected (`markexpr="integration"`), so an absent input reaches the test body and fails; there is no skip path in the contract. (Note the wording is correctly "when selected": the absence check belongs inside the test functions, not at import, or a deselected collection would still error — see §5.) |
| `scripts/run_tests.sh` still forwards both inputs across `env -i` | **PASS** | Unchanged from the targeted rereview: CI plan `:161-165` extends the explicit `_test_var` allowlist with exactly the two names and adds the guard test; `scripts/run_tests.sh:144-150` builds `TEST_ENV` from that list via `${!_test_var}` indirection, `:303` passes `${TEST_ENV[@]+"${TEST_ENV[@]}"}` into `exec env -i`, `run_tests_parallel.py:717-723` spawns each file with `env=os.environ`, and `tests/conftest.py:246-250`/`:139-155`/`:158-243` blank only credential-shaped names (neither `…_DIR` nor `…_EXPECTED_SHA` matches; no `ERICSSON` entry exists in conftest). `:173-176` still runs the parity test through the wrapper and stages `scripts/run_tests.sh`, the test, and the snapshot together. |
| The correction introduces no new Critical or Important plan defect | **PASS** | The amendment is confined to: CI plan Task 0 §3 wording (`:158-161`) and the eight command lines; the repository plan's two parity commands and Task 3 Step 1/3 message sentences (`:330-332`, `:347-349`); the repository spec's tag paragraph (`:101-107`); and the release plan's two parity commands (`:156`, `:956` — the surrounding line numbers are unchanged from the prior version, consistent with an inline flag insertion only). Re-reading each site: no gate lost a file, no test was weakened, no command changed except by the added flag, and the `integration` marker deselects only this module (no other Ericsson test is marked). The `-m integration` form does not disturb the other Hermes gates, which remain separate `scripts/run_tests.sh -q <files>` commands without the flag. |

**NEW-A: RESOLVED.** Task 0 no longer commits a test that is red under the
default `base` CI run, and every explicit parity gate still fails closed.

## 4. Non-blocking clarifications (both verified)

| Item | Result | Evidence |
|---|---|---|
| Short-SHA negative test name matches its focused selector | **PASS** | CI plan `:602-604` names it `test_pipeline_list_rejects_short_sha_as_invalid_remote_data`; Step 4 `:681-683` selects `-k 'pipeline_jobs or merge_request_pipelines or list_pipelines or pipeline_list or read_pipeline'` over `tests/test_gitlab_ci.py tests/test_gitlab_reads.py` — `pipeline_list` is a substring of the new name, so it executes in the focused gate. Step 5 `:689` still commits `tests/test_gitlab_reads.py`. |
| Tag-message strings are redacted before truncation to the bound | **PASS** | Repository spec `:104-106`: "a non-null message is redacted and truncated to the documented bound, and any other shape is invalid remote data". Repository plan `:330-331` requires "an over-bound string case asserting redaction occurs before truncation to `_MAX_TAG_MESSAGE`", and `:347-349` implements "Redact a string before truncating it to `_MAX_TAG_MESSAGE`; reject any other message shape as invalid remote data." Order is now explicit and tested (redact-then-truncate cannot leave a partial secret at the cut). The null-message test (`:328-330`) and the malformed non-null case remain. |

## 5. New Critical/Important findings

None. Two implementation cautions, non-blocking:

1. Keep the "fails when either input is absent" check inside the test
   functions (as `:160-161` says, "when selected"). A module-level
   `pytest.fail`/raise would run at collection, before marker deselection, and
   would make the default CI run error again.
2. Because the whole module is `integration`-marked, the wrapper-allowlist
   guard test is also deselected in default CI; it is still executed at every
   one of the eight parity gates, which is where a stripped allowlist would
   bite first, so this is acceptable as written.

## 6. Verdict

**PASS.** NEW-A is resolved on source evidence: the parity module is
`integration`-marked so the default `scripts/run_tests.sh` run that `base` CI
executes deselects it (exit 5 → pass in the per-file runner), all eight
explicit parity commands carry `-m integration` with both inputs, the wrapper
still forwards both inputs across `env -i`, and an explicitly selected run
without inputs still fails rather than skips. Both non-blocking clarifications
are now specified. No Critical or Important plan defect remains; webhooks remain
excluded.
