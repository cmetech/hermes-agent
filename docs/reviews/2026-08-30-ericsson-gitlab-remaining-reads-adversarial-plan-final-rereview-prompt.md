# Final blocker-only rereview — remaining Ericsson GitLab reads

Independently review the final amended plans. This is plan review only. Do not
implement or modify source/spec/plan/reconciliation/prior-review files. A single
new report file is the only authorized write.

## Gate

Return `BLOCK` for any Critical or Important plan defect. Return
`READY FOR IMPLEMENTATION` only with zero Critical/Important findings. Minor
polish is non-blocking and must be listed separately.

Verify all hashes before review; on mismatch return only
`REVIEW_INPUT_CHANGED` and the mismatch:

```text
485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922  docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md
43a7463614f07c6609582d95eaf6875e345e248fa96c0fa0d101d70e97b0a085  docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md
3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f  docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md
5f4257377e7ee79a992b962d76654bf6acf76e0aaa7c5149f1315ac9ef572ed6  docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md
7e2f5a10075b19ea65214e3d4e2e620adfec2e92b5465759d9dcc18c35535bac  docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md
cb3ba13d29c8d0bcb23c2dfcee21809090bcffeee1730589e35e572e3530cc37  docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md
ea6a54d16496c5ea969ff314e2c81c72b13277d8cb0e5789a405353211f486c5  docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md
```

Read all seven in full. Treat reconciliation as a claim, not evidence. Inspect
the current source repository at
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`
and Hermes at
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`.
Use official GitLab docs/source only for API facts. Do not contact a real
GitLab instance or read credentials.

## Approved boundary

- One ordinary LLM/tool-calling conversation with a stable permanent tool
  array and byte-stable system prompt.
- Progressive disclosure through router skill, qualified plugin skill,
  deferred descriptions, then read tools.
- No preliminary classifier request, dynamic tool-array swap, new core tool,
  new dependency, webhook support, or new write.
- Source-first on `ericsson-capabilities/main`, exact vendoring to Hermes
  `base`, and exact SHA ancestry between the three slices.

## Mandatory regression checks

Verify the prior rereview blockers are truly resolved:

1. optional MR positional preserves `SUPPRESS` after final parser cleanup;
2. backward-compat MR test passes before extension;
3. all source/Hermes fixed inventories, including installed-distribution e2e,
   are named, gated, and committed;
4. `gitlab_read_pipeline` has a skill owner and `intent_tools` is created,
   validated, cumulative, and consumed;
5. baselines do not reference later-created tests and all `-k` expressions
   select intended tests;
6. visible-project search uses only documented Search API fields;
7. every parity invocation supplies source directory and exact SHA and the
   parity test fails closed;
8. native personal scope plus matching `@me` emits only the native scope;
9. direct pytest is used only in the source repo; Hermes uses
   `scripts/run_tests.sh`;
10. routing evaluator still detects pre-dispatch writes, performs no real
    GitLab I/O, copies neither source `.env` nor `auth.json`, proves two model
    families, and rejects incomplete/wrong-order routes.

Also search for new blockers introduced by the corrections. Do not reopen an
item without new source evidence.

## Proof and report

For each blocker provide ID/severity, exact task/repository, source
path+line/symbol, violated invariant, concrete failure, why current steps do
not fix it, smallest correction, and runnable RED/GREEN command.

Report sections:

1. Identity and verified hashes.
2. Verdict.
3. Critical/Important table and full proofs.
4. Prior-blocker disposition (`RESOLVED`, `PARTIAL`, `OPEN`).
5. Cross-plan/integration audit.
6. Routing-evaluator audit.
7. Minor notes.
8. Final gate statement.
