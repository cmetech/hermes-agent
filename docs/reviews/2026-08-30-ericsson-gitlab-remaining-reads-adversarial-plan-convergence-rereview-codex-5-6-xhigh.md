# Convergence rereview — remaining Ericsson GitLab read plans

## Identity and frozen hashes

- Reviewer/model: Codex GPT-5.6 Sol, xhigh reasoning.
- Review date: 2026-08-30.
- Source inspected at `ericsson-capabilities/main` `0d7654d14db0afe0c688a752a2676d8cabe2f981`; Hermes inspected at `base` `4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944`.
- Review method: current frozen files and repository source were re-read; the prior Fable report supplied the NEW-A claim only, not its disposition.

All hashes matched before review and immediately before this report:

| Artifact | Verified SHA-256 |
|---|---|
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `e80f75ae8af2a8e64b6f37017fd732da3504ea84805248394460a0c24eba6a30` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `c0fe22fb9ecdfa97bf3addadaaa69a3a3e4c97edc1325410c18b30b51c4af028` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `8269698319f777f6e6ee808c9cd91e2a10bfc57b036124108de9f54b989263e7` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `f50b88b603a54807b4cf8a8f8fa2c7ed789908135ce726e0f6642b9b424f0888` |

## Source-backed convergence checks

| Check | Result | Evidence |
|---|---|---|
| Fable NEW-A: default Hermes CI must not fail on the external-source parity test | **PASS** | CI Task 0 now requires the parity module to carry `pytest.mark.integration`, requires every explicit invocation to opt in, and retains fail-not-skip behavior only when selected (`CI plan:155-164`). Hermes config excludes integration tests by default (`pyproject.toml:533-547`). A fully marker-filtered file exits pytest with code 5; the per-file controller deliberately converts that to success (`scripts/run_tests_parallel.py:781-789`). The whole-run zero-test guard remains intact (`:1510-1529`), but normal CI runs the full suite via bare `scripts/run_tests.sh` (`.github/workflows/tests.yml:90-100`), so many non-integration tests are collected. Thus the parity file is deselected without turning `base` CI red. |
| All eight explicit parity gates opt in and provide both inputs | **PASS** | Exact invocations occur four times in the CI plan (`:179-181`, `:261-263`, `:1432-1434`, `:1507-1509`), twice in Repository Discovery (`:146-148`, `:786-788`), and twice in Release/Inbox (`:154-156`, `:954-956`). Every command supplies `ERICSSON_CAPABILITIES_DIR`, supplies `ERICSSON_CAPABILITIES_EXPECTED_SHA`, and calls `scripts/run_tests.sh -q -m integration ...test_ericsson_vendor_parity.py`. Repository-wide search found exactly these eight explicit parity commands. |
| Explicit selection remains fail-closed; `-m integration` reaches pytest | **PASS** | Task 0 says the test fails rather than skips if either input is absent (`CI plan:160-164`); the marker changes selection, not test-body semantics. The runner recognizes `-m` as a pytest value flag and forwards both tokens (`scripts/run_tests_parallel.py:1190-1211`, `:1233-1247`). A local no-network probe with an existing integration-marked module confirmed `scripts/run_tests.sh -q -m integration ...` overrides the configured `not integration` selection. The planned parity test therefore runs under the eight gates and fails if explicitly selected without either or both inputs. |
| Required inputs still cross `env -i` | **PASS** | Task 0 still requires adding exactly both names to the wrapper's `_test_var` allowlist and stages/tests that wrapper change (`CI plan:162-176`). Current wrapper mechanics build `TEST_ENV` before stripping (`scripts/run_tests.sh:126-150`) and inject it into normal `env -i` execution (`:298-316`); the per-file child inherits `os.environ` (`scripts/run_tests_parallel.py:717-724`). The pytest hermetic fixture removes credential-shaped and enumerated `HERMES_*` variables only (`tests/conftest.py:133-250`, `:453-468`), so neither Ericsson input is erased. |
| Clarification: short-SHA negative is selected by the focused gate | **PASS** | CI Task 3 now names `test_pipeline_list_rejects_short_sha_as_invalid_remote_data` (`CI plan:598-604`). Its name matches the Step 4 `pipeline_list` selector, which runs both `test_gitlab_ci.py` and `test_gitlab_reads.py` (`:678-684`), before the test file is committed (`:686-691`). |
| Clarification: tag messages are redacted before truncation | **PASS** | The repository design now states redaction then truncation (`repository design:101-106`). Task 3 requires an over-bound regression proving that order (`repository plan:325-332`) and directs `_normalize_tag` to call redaction before truncating to `_MAX_TAG_MESSAGE` (`:340-350`). This matches the existing safety pattern in `GitLabOperations.job_log`, which redacts the complete text before applying a presentation bound (`ericsson-capabilities/plugins/ericsson-gitlab/operations.py:417-426`). Null remains `None`, and malformed non-string/non-null values remain `invalid_remote_data`. |

## New Critical/Important findings

None. The amendments change test selection and normalization order without adding a write, webhook, dependency, core tool, dynamic tool-array swap, source/vendor inversion, or uncovered commit path.

## Final verdict

**PASS**

Fable NEW-A is resolved, both clarifications are executable and gated, and no Critical or Important plan defect remains in the frozen artifacts.
