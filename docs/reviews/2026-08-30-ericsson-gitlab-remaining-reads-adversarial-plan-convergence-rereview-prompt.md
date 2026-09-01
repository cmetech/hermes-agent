# Convergence rereview — remaining Ericsson GitLab read plans

Date: 2026-08-30

Read the current files from disk and independently verify the frozen hashes.
Do not edit specs or plans. Inspect repository source rather than trusting the
orchestrator's disposition.

## Frozen artifacts

- `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md`
  — `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922`
- `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md`
  — `e80f75ae8af2a8e64b6f37017fd732da3504ea84805248394460a0c24eba6a30`
- `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md`
  — `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f`
- `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md`
  — `c0fe22fb9ecdfa97bf3addadaaa69a3a3e4c97edc1325410c18b30b51c4af028`
- `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md`
  — `8269698319f777f6e6ee808c9cd91e2a10bfc57b036124108de9f54b989263e7`
- `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md`
  — `f50b88b603a54807b4cf8a8f8fa2c7ed789908135ce726e0f6642b9b424f0888`

## Required check

Recheck Claude Fable finding `NEW-A` from
`docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-targeted-rereview-fable-5.md`:

- the planned parity module is marked `pytest.mark.integration` so default
  Hermes CI deselects it without failing the per-file runner;
- all eight explicit parity commands across the three plans use
  `-m integration` and provide both required inputs;
- when explicitly selected without either input, the test still fails rather
  than skips;
- `scripts/run_tests.sh` still forwards both inputs across `env -i`; and
- the correction introduces no new Critical or Important plan defect.

Also verify the two non-blocking clarifications: the short-SHA negative test's
name matches its focused selector, and tag-message strings are redacted before
being truncated to the bound.

## Output

Write a concise Markdown report with model identity, full hash verification,
source-backed PASS/BLOCK evidence for the required check, any new
Critical/Important finding, and a final `PASS` or `BLOCK`. Return `PASS` only
when no Critical or Important defect remains. Webhooks remain excluded.
