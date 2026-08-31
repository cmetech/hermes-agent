# Blocker-only adversarial rereview — remaining Ericsson GitLab reads

Review the amended plans independently. This is a plan review, not a code
review and not implementation work. Do not modify any source, spec, plan,
reconciliation, or existing review file.

## Objective

Determine whether the three amended implementation plans are now executable,
source-grounded, safe, and complete enough to implement the approved remaining
GitLab read-only surface without webhook support.

Return `BLOCK` if any Critical or Important defect remains. Return
`READY FOR IMPLEMENTATION` only if there are zero Critical/Important findings.
Minor polish does not block this rereview; list it separately and briefly.

## Immutable inputs

Before reviewing, verify these SHA-256 hashes. If any differs, return only
`REVIEW_INPUT_CHANGED` with the mismatched file and observed hash.

```text
485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922  docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md
9ebbd6ac94edc830c3b77500ad1380748b9f068e45c7e218080355cceb48eb7e  docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md
ce5722c1d269852e593d4ad48b87f30731306e2a3779da26f79c235c9887f25b  docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md
74bb2e593ea199795e435d6a49dd94330e09f412190cc66d86fdbf2291b1bbd1  docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md
25809584df8c1d4a2e00bc7c05e08d9160451d66b1b3950c8730108441acc9f6  docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md
5ab3dffa61c4d25e2513513cd565b49366c15be86e3d297413aad49ac1c877c6  docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md
79a4c8232d5900e0a670e651cdb0d18859f8ea9944fccd88dcd20750144a3094  docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md
```

Read all seven files in full. The reconciliation is a claimed disposition,
not evidence; verify each claim against the actual plans and source.

## Repositories and authority

- Hermes target/distribution repository:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`
- Authoritative source repository:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`
- Source must be implemented and integrated on source `main` before exact
  vendoring to Hermes `base`; literal Hermes `main` is synchronization-only.
- Preserve unrelated user-owned changes. Use read-only inspection only.
- Do not contact a real GitLab instance or expose credentials.
- For current API facts, use official GitLab docs/source only.

## Approved architecture and scope

- One ordinary LLM/tool-calling conversation.
- Stable permanent model tool array and byte-stable system prompt.
- Progressive disclosure: `gitlab` router skill → one qualified plugin skill →
  deferred description → read tool.
- No preliminary classifier request, model-written dispatch framework,
  category-specific tool-array swap, new core tool, or new dependency.
- Operations: CI job/pipeline/variable reads; branch/tag/project/code discovery;
  releases, To-Dos, and project-optional personal MR queues.
- Webhooks and every new write are excluded.

## Required blocker checks

Recheck at least these previously blocked areas:

1. Source/vendor reconciliation cannot delete or revert current Hermes-managed
   Ericsson behavior, and its parity test is implementable.
2. Every changed schema/descriptor/migration/onboarding inventory and
   hand-authored count claim is named in the correct task/file/commit.
3. Job, pipeline, release, tag, To-Do, and MR contracts match documented valid
   response/request shapes without weakening same-origin, redaction, bounds,
   or malformed-data handling.
4. Optional MR project parsing omits the field when absent and retains exact
   schema validation/description when present.
5. The live evaluator sees writes blocked before dispatch, never performs real
   GitLab I/O, does not copy the source `.env`, proves two explicit model
   families, and rejects incomplete or wrong-order multi-step/multi-intent
   routes.
6. Corpus `read_tools`, skill ownership, `intent_tools`, required intents, and
   accepted ordered sequences stay cumulative across all slices.
7. Each slice is integrated to source `main` and Hermes `base`, with exact SHA
   ancestry required before the next slice branches.
8. Hermes Python tests use `scripts/run_tests.sh`; source tests may use the
   source repository's pytest command.
9. TDD steps can actually go RED and GREEN with the listed files, including
   exact skill-inventory changes in Hermes.
10. Prompt caching and deferred-tool architecture remain unchanged.

Also look for new Critical/Important defects introduced by the amendments.
Do not reopen a resolved concern without new source evidence.

## Finding proof burden

For every Critical/Important finding provide:

1. ID and severity.
2. Exact spec/plan task and repository.
3. Exact source path and line/symbol evidence.
4. Violated requirement/invariant.
5. Concrete failure scenario and impact.
6. Why existing tests/steps do not catch or fix it.
7. Smallest plan correction.
8. Runnable RED/GREEN or acceptance command.

Unsupported hypotheticals are not findings. If an API detail cannot be proven
from official GitLab sources, label it `UNCHECKABLE` rather than inventing a
contract.

## Required report format

1. Review identity and verified input hashes.
2. Verdict: `BLOCK` or `READY FOR IMPLEMENTATION`.
3. Critical/Important findings table.
4. Full proof for each blocker.
5. Previously reported blocker disposition table (`RESOLVED`, `PARTIAL`, or
   `OPEN`) covering the consolidated items in the reconciliation.
6. Cross-plan dependency and integration audit.
7. Routing-evaluator soundness audit.
8. Non-blocking Minor notes, if any.
9. Final implementation gate statement.
