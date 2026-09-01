# Targeted adversarial rereview — remaining Ericsson GitLab read plans

Date: 2026-08-30

Act as an independent adversarial implementation-plan reviewer. Review the
current files from disk, not earlier report excerpts. Do not edit the plans.
Use repository source and authoritative GitLab API documentation to verify any
claim before reporting it.

## Frozen artifacts

- `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md`
  — SHA-256 `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922`
- `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md`
  — SHA-256 `9fac07b9d4fb6d93795e74b4caf1b7f83ddd7a883f286f9045673ccf57606fa6`
- `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md`
  — SHA-256 `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f`
- `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md`
  — SHA-256 `9ec40e40053282cdf55a30ac23febcd820b3726a490f43dca68ee55d72534f04`
- `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md`
  — SHA-256 `b47956899687ee4f270485a27f6c0418662533926b5ad144b1954d6f3f4d7007`
- `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md`
  — SHA-256 `cb3ba13d29c8d0bcb23c2dfcee21809090bcffeee1730589e35e572e3530cc37`

Authoritative source repository:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

Hermes repository:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

## Required checks

Verify these three corrections end to end:

1. Task 0's parity test can actually receive
   `ERICSSON_CAPABILITIES_DIR` and `ERICSSON_CAPABILITIES_EXPECTED_SHA`
   through Hermes `scripts/run_tests.sh` despite its `env -i` boundary, and the
   plan stages/tests that wrapper change.
2. Strict pipeline SHA validation does not break existing valid fixtures in
   `tests/test_gitlab_reads.py`; the plan owns, updates, runs, and commits that
   file while retaining an isolated invalid-short-SHA case.
3. The Tags API's documented `message: null` shape is accepted and tested,
   while malformed non-null values remain strict.

Also report any new Critical or Important regression introduced by these
amendments. Do not reopen earlier stylistic or Minor findings unless the
current text turns them into an implementation blocker. Webhooks remain
excluded.

## Output contract

Write a concise Markdown report with:

- reviewer/model identity;
- frozen hash verification;
- one table mapping each required check to `PASS` or `BLOCK` with exact
  evidence;
- any new Critical/Important findings with file/line and smallest correction;
- final verdict `PASS` only if no Critical or Important plan defect remains,
  otherwise `BLOCK`.
