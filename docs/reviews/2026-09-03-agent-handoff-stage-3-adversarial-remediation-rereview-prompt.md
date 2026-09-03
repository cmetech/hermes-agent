# Adversarial remediation re-review prompt — Agent Handoff Stage 3

Use this prompt in one fresh Claude session and one fresh Codex session. Review
the same clean detached checkout independently. This is review, not
implementation.

Do not modify code, tests, documents, Git state, refs, or worktrees. Do not use
live credentials, inference, services, or external network. Use only bounded
synthetic probes in temporary paths. Return one Markdown report to stdout.

## Immutable scope

- Original reviewed candidate: `2affe5e02307475274cb3d72c24af59f72682945`
- Remediated candidate: `8986696272f5c48edfc302f89d07f1e4e424a614`
- Remediated tree: `405b47910aea03e176c460029aabfedfd9f1fa3a`
- Remediation range: `2affe5e02307475274cb3d72c24af59f72682945..8986696272f5c48edfc302f89d07f1e4e424a614`
- Range commits: 3
- Range paths: 5
- Range diff: `+450/-1`; 397 inserted lines are the shared original review
  prompt, not production behavior.

Verify these facts and stop with `SCOPE ERROR` if they differ. The checkout
must be detached and clean before and after review.

Read completely, in order:

1. `docs/proposals/2026-09-01-shared-agent-handoff-facade-workflow-bot-mode-consolidated.md`
2. `docs/assessments/2026-09-02-agent-handoff-stage-3-implementation-readiness.md`
3. `docs/superpowers/plans/2026-09-02-bot-mode-desktop-agent-handoff-stage-3.md`
4. `docs/reviews/2026-09-03-agent-handoff-stage-3-adversarial-code-review-prompt.md`
5. the complete remediation diff and every production caller of its changed
   functions

Do not read the pre-existing Stage 3 review, either independent reviewer
report, or the reconciliation before reaching your verdict.

## Findings to re-review

### S3-HANDOFF-001 — obsolete attention duplicated terminal wake

The original candidate retained both a pending `needs_input` wake and a later
terminal wake. Both delivery IDs loaded the current terminal snapshot and
started two identical model turns. The remediation atomically acknowledges
older unacknowledged delivery projections when a newer attention event commits,
while retaining the old source event and delivery row as evidence.

Prove or falsify all of the following:

- `needs_input -> active -> succeeded`, with the host absent until terminal,
  yields one due delivery, one current Needs Attention row, and one terminal
  model wake;
- the older row remains queryable and its delivery truth is not rewritten;
- an older claimed delivery is fenced if a newer attention event supersedes it;
- replay of either observation cannot erase or duplicate the current delivery;
- acknowledgement, failed wake, attention-only policy, restart recovery,
  host/profile filtering, attempt limits, and one-delivery transcript replay
  remain correct; and
- the change does not suppress a later terminal return after an earlier return
  was already accepted by its consumer.

### S3-HANDOFF-002 — malformed YAML fell through to compatibility transport

The original candidate used the merged/LKG config loader, so syntactically
malformed YAML looked like an empty directory and could select a colliding
local compatibility target. The remediation validates the initiating
profile's raw file before directory or compatibility resolution and maps read
or parse failure to the resolver's closed `ValueError` contract.

Prove or falsify all of the following:

- syntactically malformed and unreadable config fail before local, peer,
  bare-peer, or relay fallback and before any handoff, subprocess, peer DM,
  relay, warning-backup, or other transport side effect;
- missing config and valid minimal config retain the documented empty-directory
  compatibility behavior;
- valid semantic directory errors still fail closed;
- named profiles read only their own directory and peer registry; and
- Bot and Desktop callers map the closed resolver error without leaking raw
  YAML, paths, credentials, or peer URLs.

## Binding clarification for the disputed model-contract report

The original shared prompt over-constrained invariant 1. The accepted Stage 3
plan says the return route is **optional**, and the implementation-readiness
assessment requires only that Bot/Desktop creation expose no deadline until a
consumer-neutral deadline policy exists. It does not require the shared
`HandoffSpec` type to reject every deadline or route-less internal conversation.

The live production constructors in `tools/bot_mode_dm.py` and
`tui_gateway/methods_agent_handoff.py` must still pass `deadline_at=None` and a
closed host-derived Bot/operator route. Report a defect only if a realistic
production caller can violate that accepted consumer boundary; do not promote
the prompt's stronger wording over the accepted plan.

## Required verification

```bash
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
"$HERMES_PYTHON" -c 'import hermes_cli.handoff, pathlib; print(pathlib.Path(hermes_cli.handoff.__file__).resolve())'

git status --short --branch
git rev-parse HEAD HEAD^{tree}
git rev-list --count 2affe5e02307475274cb3d72c24af59f72682945..HEAD
git diff --check 2affe5e02307475274cb3d72c24af59f72682945..HEAD
git diff --name-status 2affe5e02307475274cb3d72c24af59f72682945..HEAD

HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/hermes_cli/handoff/test_store.py \
  tests/hermes_cli/handoff/test_directory.py \
  tests/hermes_cli/handoff/test_supervisor.py \
  tests/hermes_cli/handoff/test_bot_conversation_e2e.py \
  tests/hermes_cli/handoff/test_bot_return_recovery_e2e.py \
  tests/gateway/test_completion_delivery.py \
  tests/tui_gateway/test_agent_handoff_methods.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/tools/test_bot_mode_dm.py \
  tests/hermes_cli/test_config_read_guard.py -q
```

Run additional deterministic probes needed to evaluate the bullets above.
Unavailable OS lanes or Desktop dependencies are limitations, not automatic
product findings.

## Proof and output standard

Return `BLOCK` only for a reproducible CRITICAL or IMPORTANT production defect
caused or left open by the remediation. A finding must include exact source and
caller, realistic trigger, wrong result, reproduction or rigorous interleaving,
why tests miss it, and the smallest root fix. Do not report style, desired
refactoring, speculative hardening, or a test gap without wrong production
behavior.

Return:

1. reviewer, exact model/version, date, scope verification;
2. `PASS` or `BLOCK` verdict;
3. findings with complete proof, if any;
4. a bullet-by-bullet disposition for both remediated findings and the binding
   clarification;
5. exact commands and results;
6. residual platform/dependency limits; and
7. final detached clean-worktree proof.
