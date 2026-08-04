# Task 16 Report — Final Regression and Integration Gates

## Outcome

The Phase 3 candidate is green under the explicit ordinary-functionality
allowlists authorized by the user override. The final production identity is:

```text
commit  8a1fe704484bf63e0e84f536f7fb690a2f024ccf
tree    94f4fd4572b63ba6dd496213b603e67748b41b46
subject fix(workflow): keep schema startup read-only
branch  feat/workflow-language-phase-3-semantic-compatibility-resilience
```

The candidate preserves the published Phase 3 language and execution contract,
closes the ordinary concurrency/recovery defects exposed by final gates, keeps
the packaged workflow-schema command read-only before startup recovery, passes
the explicit Python/Desktop/schema/customization gates, and integrates cleanly
into disposable OTTO and LOOP24 rehearsals. No live branch, brand ref, shared
base checkout, or literal `main` ref was changed.

Per the absolute user override, no threat-model analysis, security-focused
review, exploit/adversarial validation, or security-oriented test suite is part
of this report. Independent final specification and quality reviews were run
only over the allowed ordinary scope; both passed with 0 Critical, 0 Important,
and 0 Minor findings at the exact production identity above.

## Retained Task 16 Corrections

Final gates produced six bounded atomic corrections:

- `812f9c1` — provider release/cancellation linearization.
- `b2f0d347` — live model-catalog fixtures isolated from the changing remote
  catalog.
- `6d7150c6` — journal projection validation preserves its load fast path and
  ordinary crash consistency.
- `e799ea7d` — schedule downgrade compatibility, append-only execution-context
  callbacks, and Desktop relationship assertions.
- `e5642598` — scheduled revalidation preserves exact bounded recovery artifact
  names and the merge gate respects the active test exclusions.
- `8a1fe704` — exact `workflow schema` startup candidates bypass only the early
  recovery mutation while normal and update startup retain it.

The final schema-startup correction changes only `hermes_cli/main.py` and the
existing `tests/plugins/workflow/test_cli.py`. It moves the already-established
dependency-free workflow/schema action authority ahead of early recovery,
preserves global version and one-shot precedence, and leaves the bounded
argparse/schema parser authoritative for output, help, profile handling, and
parse errors.

## Schema-Startup TDD Evidence

The regression creates a temporary early-recovery marker and records whether
the dependency-light recovery hook is invoked for five exact startup shapes.

- RED: schema success, help, and parse-error candidates all invoked early
  recovery; ordinary and update controls already retained it. Result: **2
  passed / 3 failed**. Log:
  `/private/tmp/hermes-task16-gates.eudnoW/schema-early-recovery-red-e564259.log`,
  SHA-256
  `a3357d0673bbc99649626109404a685e39872f77c6b3628a9f48eabbc2a542f0`.
- GREEN: all five exact cases passed. Log:
  `/private/tmp/hermes-task16-gates.eudnoW/schema-early-recovery-green-uncommitted.log`,
  SHA-256
  `8db3fb5baf5b7ab31874e6265ca16bc309991feb798e7c27508cc203dc2536a3`.
- Existing workflow CLI file: **83 passed / 0 failed**. Log:
  `/private/tmp/hermes-task16-gates.eudnoW/workflow-cli-schema-startup-uncommitted.log`,
  SHA-256
  `abd2bc6d2d10ed0e326b663dade52bde18e0a25512adda820b9cf081496e876e`.
- Pre-commit adjacent allowlist (`test_early_recovery.py`,
  `test_apply_profile_override.py`, `test_language_schema.py`, and
  `test_cli.py`): **4 files / 717 passed / 0 failed**, retries disabled. Log
  SHA-256
  `4cc81d611b8e7fb186056148a23a9050115ee82ab07503533dd566e1836b833a`.
- The same four-file allowlist at committed `8a1fe704`: **717 passed / 0
  failed**, retries disabled. Log:
  `/private/tmp/hermes-task16-gates.eudnoW/schema-cli-adjacent-four-8a1fe704.log`,
  SHA-256
  `4c139f6caebff5dbaa9baba622217d3a897e59e1db8d9c54288b242a376b57c1`.

Scoped Ruff lint and `git diff --check` passed before the atomic commit. The two
large legacy files are not globally Ruff-formatted, so no unrelated bulk
formatting was performed.

## Explicit Regression Evidence

### Focused Phase 3 allowlist

The user-approved focused allowlist excluded the mixed persistent-recovery file
and included only these nine ordinary files:

- `tests/plugins/workflow/test_phase3_language.py`
- `tests/plugins/workflow/test_phase3_execution_semantics.py`
- `tests/plugins/workflow/test_phase3_code_catalog.py`
- `tests/plugins/workflow/test_strict_output_references.py`
- `tests/plugins/workflow/test_phase3_conditions.py`
- `tests/plugins/workflow/test_phase3_resolution_waits.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`
- `tests/agent/test_plugin_agent.py`
- `tests/plugins/workflow/test_fault_injection.py`

Result: **9 files / 1,577 passed / 0 failed**, retries disabled. Log:
`/private/tmp/hermes-task16-gates.eudnoW/focused-phase3-e564259.log`, SHA-256
`aa9e53b8019243ad178bf5b7270a5a1e069cd163e0147a2ba6657e4670584c95`.
The final commit touched only CLI startup and its existing CLI test, so it did
not invalidate these Phase 3 execution results.

### Exact final base gate

The explicit base merge gate completed against final candidate `8a1fe704`
before the later generic ledger stage was stopped:

- Python: **56 explicitly listed files / 3,804 passed / 0 failed**.
- Installed distribution: **1 passed / 0 failed**.
- Desktop: **11 files / 159 passed / 0 failed**.
- Desktop typecheck: passed.
- Gate identity: `TESTED_BASE_SHA=8a1fe704484bf63e0e84f536f7fb690a2f024ccf`.

Log:
`/private/tmp/hermes-task16-gates.eudnoW/rehearsal-8a1fe704/base-gate.log`,
SHA-256
`3bd31aaf32d8cdf21ce179c63ac619eef2cace725735dffbcb71fdf558e199a0`.
This base-gate evidence is independent of, and completed before, the discarded
generic ledger stage.

### Desktop gates

The final Python-only startup correction did not invalidate the scoped Desktop
results:

- Typecheck passed; log SHA-256
  `64a0ca63365dfc9b3488972a1652ea1f63fa1e1b804e1b068cb63f2d05d7901a`.
- Three focused files: **114 passed / 0 failed**; log SHA-256
  `2416c741768be322faeaa6afe6c0f651a98c5ad95612e430b25d89ad2bed8508`.
- Scoped ESLint: **0 errors**, with 23 established warnings; log SHA-256
  `de421546eef46b91d3655f5428829f396938b4a55565727fd53cde16e00d7892`.
- Scoped Prettier: passed; log SHA-256
  `17aa973d3f004560237d9a95171210b0671deff23d61628eecf7322ff5938f20`.

### Schema, packaging, customization, and merge harnesses

- Six-file schema/installed/customization/merge/Desktop harness allowlist:
  **6 files / 1,033 passed / 0 failed**. Log SHA-256
  `c4b46cf5250e880255005eecc4026c09881d0c1f51bcec9e097984fe896def49`.
- Installed-distribution integration separately: **1 passed / 0 failed**.
  Log SHA-256
  `e0e76f6ea65b9302846c5e48e8395bfd9fe8df19b0b4eac9b7156d4e44cb6e07`.
- Strict customization checker: exit 0 with empty output, SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Merge contract: **49 passed / 0 failed**, log SHA-256
  `f84ef6288c22c125427299377b6dc43b98d7ef558fd49f58f1a5b840e1ecc6`.

## Upstream Decisions

The customization overlap checker required eight explicit decisions. The
evidence-backed set used for integration planning was:

```text
desktop-workflow-test-gate=adapt
plugin-agent-request-mcp-lifecycle=adapt
workflow-language-admission-pinning=preserve
workflow-language-schema-cli=adapt
workflow-language-desktop-status=preserve
workflow-language-desktop-capability-skew=preserve
workflow-language-regression-gates=adapt
workflow-parser-backed-symbol-ownership=preserve
```

The schema CLI adaptation is commit `8a1fe704`. No ledger baseline was advanced
from an incomplete rehearsal, and no entry was treated as upstream-equivalent.

## Independent Review Closure

- Final specification review: **PASS — 0 Critical / 0 Important / 0 Minor** at
  exact HEAD `8a1fe704484bf63e0e84f536f7fb690a2f024ccf`, tree
  `94f4fd4572b63ba6dd496213b603e67748b41b46`.
- Final quality review: **PASS — 0 Critical / 0 Important / 0 Minor** at the
  same exact identity. Its fresh ordinary regression selection passed **262 / 262**
  with retries disabled.
- Neither review used discarded broad-suite or ledger-stage results, inspected
  excluded files, or performed prohibited security/threat analysis or testing.

The specification review's fresh compact CLI probes measured 233,438 bytes for
Archon and 225,133 bytes for legacy, including the trailing newline. Those are
compact CLI serialization sizes, while Task 15's 249,509/240,669 figures are
default Python JSON serialization measurements. The semantic invariants remain
unchanged: normalizer v3/v2, 39/9 compatibility codes, reader/projection v2,
Archon-only extension boundary, and both envelopes below 256,000 bytes.

## Manual Integration-Only Rehearsal

The standard generic rehearsal was retired under the user override because its
ledger runner unconditionally unions every test path from every ledger entry
and has no supported entry-selection option. The replacement rehearsal did
only disposable Git integration, brand generation, offline lock regeneration,
and byte/ancestry checks. It ran no base/brand test gates, ledger invariants,
generic rehearsal report generator, or additional validation suite.

Resolved live refs before the rehearsal:

```text
origin/main  b7a05b6b6f509d14f708a2fe7b7c1d3559396ef6
candidate    8a1fe704484bf63e0e84f536f7fb690a2f024ccf
otto         75b9c6510442cb2e7dade513b01c0023dfa73bc6
loop24       8f2e6ac8ba039e0f8289280d35fb335584ce1dee
```

Neutral base rehearsal:

- Detached merge of `origin/main` into the candidate reported `Already up to
  date` with no conflicts or residue.
- Tested head/tree remained exactly `8a1fe704...` / `94f4fd45...`.
- Both candidate and upstream ancestry checks passed.

OTTO rehearsal:

- Detached merge completed with no conflicts; the generated-file conflict
  exception was not needed.
- Brand generator and offline package-lock regeneration both succeeded.
- Disposable head/tree:
  `11b67468c3a00543faa72a0fe2f4de273a569304` /
  `2570bfbce75e5d58d6bf0ce44f32c4d54fcee87b`.
- Candidate ancestry passed; the generic runtime-path diff against the
  candidate was empty.
- `brands/otto.json` SHA-256:
  `4c211158f73b319fd0bf1a987874a373d43ff869a1a9ff617184a13fe2291599`.

LOOP24 rehearsal:

- Detached merge completed with no conflicts; the generated-file conflict
  exception was not needed.
- Brand generator and offline package-lock regeneration both succeeded.
- Disposable head/tree:
  `2f5f98b87faab8bbcc40722c95701be6820f1719` /
  `9865ffe6a1aed959eb788ebd2d591aa07c30178b`.
- Candidate ancestry passed; the generic runtime-path diff against the
  candidate was empty.
- `brands/loop24.json` SHA-256:
  `df698ddcb957d15e7264308aef6d68d6ce304e3366df0032572ff7f0da599efa`.

Disposable evidence directory:
`/private/tmp/hermes-task16-manual-rehearsal.X0eqvM/`.

```text
otto-brand.log   4c2af94661bbff6f539516067dd6af4235390193e122a50ffe57a42acf7357ac
otto-lock.log    8eef91d808a7c7d453bae75177fdac03bbfc8826e7f63c550539c53507795b9b
loop24-merge.log ff29fe5024f813757d5179bf3daab72dae2f63d562a11b5eeeaf06471fcc06bb
loop24-brand.log dfe177860695cb0eb27f26b618c3bf6fed32a109717b9c06d597cb129935b0c3
loop24-lock.log  3d5cf0f1f1aa7670eb5a97c0c9ddba1407dadda3cde60e58711726dc5473426c
```

All three exact disposable worktrees were removed. Pre/post live worktree
inventories were byte-identical with SHA-256
`e4c2ff264ae22fc6c00303abbe0668586019c7937b24e542611c3f2201dd45da`.
The candidate checkout remained clean on the feature branch, and `origin/main`,
`otto`, and `loop24` still resolved to the exact pre-rehearsal commits above.

## Superseded and Discarded Attempts

Two classes of earlier evidence are intentionally not completion evidence:

1. Broad discovered-file suite attempts selected user-excluded files. They
   were stopped/superseded, and their results are discarded. In particular,
   `/private/tmp/hermes-task16-gates.eudnoW/canonical-ordinary-python-e799ea7.log`
   (SHA-256
   `ce7a31666dc0fab99c39c9f0e4f9b81129881c661eb89445341b065f13ec477e`)
   is retained only as a deviation record, not as a pass/fail claim. The full
   mixed persistent-session recovery file was not rerun.
2. The first standard eight-decision rehearsal was stopped during the generic
   ledger stage as soon as its unconditional selection behavior was identified.
   All ledger-stage output is discarded and no generic rehearsal completion is
   claimed. The completed ordinary base gate is recorded separately above; the
   integration claim comes only from the later manual integration-only
   rehearsal.

No standard broad suite or generic rehearsal was rerun after the override.

## Final Controller State

- Production candidate: `8a1fe704484bf63e0e84f536f7fb690a2f024ccf`.
- Production tree: `94f4fd4572b63ba6dd496213b603e67748b41b46`.
- Feature checkout: clean.
- Shared base checkout: remained on `base`; its user-owned modifications were
  not touched.
- Literal `main`: not checked out or mutated.
- Brand refs: not advanced.
- Push/publication/release propagation: not performed.
- Independent final Task 16 specification and quality reviews: PASS with
  0 Critical, 0 Important, and 0 Minor findings at the exact production tree.

This report records Phase 3 implementation, gate, and independent-review
completion under the active user override. It does not authorize integration,
push, publication, branch deletion, worktree deletion outside the exact
disposable rehearsal paths, or release activity.
