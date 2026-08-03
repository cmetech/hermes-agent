# Task 11 Report — Secure Large Bash Substitutions

## Outcome

Task 11 is complete on
`feat/workflow-language-phase-3-semantic-compatibility-resilience`.
Archon v3 Bash substitutions now preserve exact UTF-8 content across the
32,768-byte inline/spill boundary without reopening spill pathnames in the
shell. Large distinct values are materialized through verified read-only file
descriptors, consumed through Task 10's bounded inherited-descriptor seam, and
closed by the parent immediately after spawn.

The implementation is restricted to Archon v3. Legacy rendering retains its
8,192-character pathname-spill behavior, and the Phase 4 loop renderer remains
unchanged.

## Exact Commit Identity

- Starting HEAD / parent:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Implementation commit:
  `75a7523be9243eb3f300f7ec729ebb10beb03ba2`
- Commit subject: `feat(workflow): secure large bash substitutions`
- Resulting tree: `da272d5532cddb2425dab4327de4684c7b9fdf3c`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`

The implementation is one atomic commit with the required exact subject.

## Implementation

### Bounded renderer and lexer

- Added immutable `RenderedBashCommand` carrying the exact command, template
  and rendered SHA-256/UTF-8 sizes, spill aggregates/content digests, and the
  fixed descriptor-to-digest manifest.
- Added a bounded, iterative shell lexer with a 64-frame nesting ceiling.
- Admits ordinary unquoted, single-quoted, and double-quoted command-word
  references, including redirection-adjacent tokens.
- Ignores escaped references and real comment text, including line
  continuations and comments inside command substitution lexical scopes.
- Fails closed with `bash_reference_context_unsupported` for references in
  heredoc delimiters/bodies, command substitution, backticks, arithmetic or
  parameter expansion, and unterminated/ambiguous states.
- Uses the approved exact context replacement table for spilled values.

### Descriptor-authoritative spills

- Inline threshold: at most 32,768 UTF-8 bytes.
- Spill limits: at most 64 distinct files, 500,000 bytes per value, and
  2,000,000 total distinct spilled bytes.
- Repeated identical bytes reuse one descriptor and deterministic digest
  variable.
- Rejects NUL as `bash_substitution_nul` and count/byte overflow as
  `bash_substitution_limit` before materialization.
- Opens every existing directory component descriptor-relatively with
  no-follow semantics; symlinked intermediate or final components fail closed.
- Creates opaque indexed files with `O_EXCL`, `O_NOFOLLOW`, and mode `0600`,
  performs bounded full writes plus `fsync`, checks regular/single-link
  identity and size, reopens read-only through the verified directory
  descriptor, verifies identity/size/SHA-256, and rewinds.
- A host without the required POSIX descriptor primitives, including native
  Windows for a large value, fails before launch as `bash_spill_integrity`.
- The shell prologue reads only the inherited descriptor, preserves trailing
  newlines with the sentinel, and preserves `cat` failure status.

### Executor and evidence

- Archon v3 raw `VariableContext` instances are adapted through the existing
  strict renderer; legacy contexts remain on the legacy renderer.
- `/bin/sh -c` (or the existing Windows-gated Bash path) receives the exact
  immutable rendered command as `argv[-1]`.
- Only spill descriptors are handed to `ManagedProcessTree.spawn()`.
- Parent descriptors close on success, spawn rejection, timeout, launch
  failure, and integrity failure. Closed/corrupt launch descriptors produce
  the stable terminal `bash_spill_integrity` result rather than an executor
  crash.
- Archon attempt metadata contains only bounded digests, sizes, counts, and
  descriptor numbers; it contains no command text, values, or spill paths.
- Registered all four Bash codes in the v3 durable-code catalog:
  `bash_substitution_nul`, `bash_substitution_limit`,
  `bash_spill_integrity`, and `bash_reference_context_unsupported`.

## Strict TDD Evidence

The first real-shell boundary test was added before production edits:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_bash_e2e.py
```

Initial expected RED: 9 failures at 32,767/32,768/32,769 bytes across
unquoted/double/single contexts. Existing Bash E2E remained 11/11 green. The
failure was exact: large substitutions produced the legacy
`variable-<digest>.txt` pathname instead of the value bytes.

Subsequent focused RED/fix rounds established:

- unsafe shell contexts: 13 expected failures before the finite lexer;
- NUL/per-value/aggregate/evidence: 4 expected failures before bounds and
  evidence;
- closed descriptor, spawn-rejection cleanup, and 65-file bound: 3 expected
  failures;
- intermediate descriptor-chain symlink: 1 expected failure;
- durable catalog: 1 expected missing-code failure;
- host capability gate: 1 expected failure;
- line-continuation/nested-command comments: 2 expected failures.

Every RED was tied to the intended missing behavior; the corresponding
focused suites were returned to green before proceeding.

## Fresh Final Verification

Exact Task 11 acceptance command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_performance_bounds.py
```

Fresh final result after all implementation edits:

```text
6 files, 197 tests passed, 0 failed
```

Per-file result:

- Task 11 Bash substitution: 56 passed.
- Phase 3 durable code catalog: 18 passed.
- Bash E2E: 11 passed.
- Managed process inheritance/containment: 76 passed, 1 platform skip.
- Security boundaries: 29 passed.
- Performance bounds: 7 passed.

Supplemental renderer regression command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_strict_output_references.py
```

Supplemental result: 2 files, 173 passed, 0 failed.

Static verification:

```text
../../.venv/bin/ruff check \
  plugins/workflow/bash_rendering.py \
  plugins/workflow/resources.py \
  plugins/workflow/executors/bash.py \
  plugins/workflow/language_schema.py \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_bash_e2e.py
git diff --check
git diff --cached --check
```

Result: all Ruff lint and whitespace checks passed. The two new files also pass
`ruff format --check`.

## Security and Compatibility Characterization

- Exact inline/spill behavior is tested at 32,767, 32,768, and 32,769 UTF-8
  bytes, including a multibyte boundary split.
- Empty values, spaces, quotes, dollar signs, backticks, globs, Unicode,
  terminal `x`, and trailing newlines are exact data in all three admitted
  quote contexts.
- Executable metacharacter payloads never become shell syntax.
- Path replacement/unlink between materialization and spawn still reads the
  verified original descriptor, never the replacement.
- Final and intermediate symlink/escape swaps fail before filesystem escape or
  launch.
- Descriptor closure before launch becomes a stable integrity failure, and
  parent descriptor ownership is closed after launch/rejection.
- The 64-file materializer boundary, 65-file rejection, exact 500,000-byte
  value boundary, exact 2,000,000-byte total boundary, and overflow rejection
  are covered.
- Descriptor evidence is exact and bounded, and descriptor numbers are proven
  closed in the parent after spawn.
- A scheduler E2E proves bounded Bash evidence survives the durable attempt
  metadata projection without command text.
- Legacy pathname spill behavior is explicitly retained.

## Files Changed

- `plugins/workflow/bash_rendering.py` (new)
- `plugins/workflow/executors/bash.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/resources.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py` (new)
- `tests/plugins/workflow/test_phase3_code_catalog.py`
- `tests/plugins/workflow/test_bash_e2e.py`

## Self-Review and Concerns

I rechecked prompt-cache and core-tool boundaries: the change is entirely
inside the existing workflow plugin/executor and adds no core model tool,
system-prompt mutation, environment setting, or API surface.

The plan listed `plugins/workflow/executors/base.py`, but Task 10 had already
provided the generic `NodeExecutionResult` metadata and inherited-descriptor
seams needed by this implementation. No additional generic base change was
necessary; adding one would have widened shared executor surface without a
consumer need.

No functional concerns remain within Task 11 scope.

## Handoff State

- Required implementation commit exists with the exact subject.
- `git branch --show-current` reports
  `feat/workflow-language-phase-3-semantic-compatibility-resilience`.
- `git status --short` is empty after the commit. This report is intentionally
  ignored by `.superpowers/sdd/.gitignore` and does not dirty the worktree.
- No push, merge, branch switch, worktree mutation, or Task 12 work was
  performed.

## Fix Round 1

### Findings fixed

- **I-1 — sealed normalizer gating:** `NodeExecutionContext` now carries the
  recorded normalizer version and `RunScheduler` passes the package's sealed
  value. `BashExecutor` uses one `secure_v3` predicate requiring both the
  Archon profile and normalizer v3 for the v3 spill directory, strict renderer,
  immutable command wrapper, Bash evidence, Windows large-value gate, and
  Task 11 error semantics. A real scheduler run covers admitted Archon v2 and
  v3; a historical four-field v1 projection is read through the scheduler's
  variable authority and executed through the real Bash executor. V1/v2 keep
  pathname-spill behavior and omit v3 Bash evidence.
- **I-2 — admission-time Bash context authority:** the bounded lexer now
  exposes `classify_bash_reference_spans()`. Archon v3 static validation calls
  it on explicit Bash surfaces before dependency/path checks, emits
  `bash_reference_context_unsupported` at `nodes[].bash`, and removes escaped
  and comment spans from the reference inventory. The strict runtime renderer
  uses the same classifier before resolving output values, so ignored literal
  references do not trigger dependency resolution; `render_v3_bash()` retains
  defense-in-depth classification for scalar variables and all substitutions.
- **I-3 — missing branch coverage:** added a Windows-gated inline executor test
  that captures the exact `[_find_bash(), "-c", rendered_command]` argv,
  verifies an empty inherited-descriptor set, and executes the real contained
  process path. Added a real `/bin/sh` test using a production-generated spill
  prologue whose inherited descriptor is changed to a read-failing directory
  descriptor after rendering; the shell exits nonzero despite `printf x`, and
  the descriptor and process tree are closed/reaped. Both tests passed before
  any I-3 production edit, confirming coverage gaps; no I-3 production change
  was justified.

### RED / GREEN evidence

Required pre-production RED:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_bash_e2e.py
```

Result: exit 1; 68 passed and 16 failed. Ten unsafe output-reference Bash
contexts were admitted, four escaped/comment references were rejected as
undeclared dependencies, and the resumed Archon v2 Bash run failed instead of
succeeding. The first v1 full-store setup also stopped at the existing
historical four-field typed-publication admission constraint before reaching
the executor, so v1 coverage was moved to the scheduler's historical snapshot
reader plus the real executor rather than counting that unrelated setup error
as Task 11 RED evidence.

First focused GREEN after the sealed-version and admission fixes:

```text
same two-file command: 84 passed, 0 failed
```

Runtime ignored-reference follow-up RED/GREEN:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh tests/plugins/workflow/test_phase3_bash_substitution.py
RED: 72 passed, 2 failed with output_reference_not_declared_dependency
GREEN: 74 passed, 0 failed
```

I-3 coverage characterization on first run:

```text
same substitution-file command: 72 passed, 0 failed
```

Fresh final exact Task 11 acceptance command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_performance_bounds.py
```

Result: 6 files, 219 passed, 0 failed (one platform skip).

Supplemental resource/reference verification:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_strict_output_references.py
```

Result: 2 files, 173 passed, 0 failed.

Static verification:

```text
../../.venv/bin/ruff check <all eight changed production/test files>
../../.venv/bin/ruff format --check \
  plugins/workflow/bash_rendering.py \
  plugins/workflow/executors/base.py \
  plugins/workflow/executors/bash.py
git diff --check
git diff --cached --check
```

Result: Ruff lint passed, the directly format-clean production files passed,
and both whitespace checks passed. `resources.py`, `scheduler.py`, `schema.py`,
and the two retained test files have pre-existing whole-file formatter drift;
they were linted without mechanically reformatting unrelated lines.

### Files changed in Fix Round 1

- `plugins/workflow/bash_rendering.py`
- `plugins/workflow/executors/base.py`
- `plugins/workflow/executors/bash.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/schema.py`
- `tests/plugins/workflow/test_bash_e2e.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`

### Concerns and self-review

- This Darwin host cannot execute native Windows job-object containment. The
  Windows test selects the real Windows argv branch and then restores the host
  OS identity before executing the real POSIX process-tree containment path;
  native Windows CI remains the final platform authority.
- Current code cannot freshly admit an Archon v1 package through the modern
  typed-publication projection because v1 intentionally has the historical
  four-field language shape. Task 11 does not widen that unrelated store
  surface; v1 resume semantics are instead covered from that exact historical
  snapshot shape through scheduler variable reconstruction and real Bash
  execution.
- Self-review found no raw values/paths added to metadata, no legacy or Phase 4
  behavior change, no core tool/prompt-cache surface, and no Task 12 work.

## Fix Round 2

Implementation commit: `6a824d52a` —
`fix(workflow): align scheduler bash reference preflight`.

### Finding fixed

- **I-2 — scheduler preflight Bash authority:** Archon v3 Bash-node preflight
  now parses canonical output-reference tokens and filters them through the
  same `classify_bash_reference_spans()` authority already used by admission
  and runtime rendering before it loads producer outputs. Escaped and comment
  references therefore remain literal through the real `RunScheduler` path;
  every non-Bash strict preflight consumer retains its existing inventory and
  resolution semantics.
- Added a real store/admission/scheduler/executor regression with both escaped
  and comment-only `$producer.output` text, no declared dependency, and exact
  `/bin/sh` stdout proving the escaped token executes as literal data.

### RED / GREEN evidence

Required pre-production scheduler RED:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh tests/plugins/workflow/test_bash_e2e.py \
  -k literal_bash_output_references
```

Result: exit 1; 1 selected test failed after admission because scheduler
preflight returned `output_reference_missing` for undeclared `producer`.

Focused GREEN after the authority-sharing fix: the same command passed 1/1.

Fresh exact Task 11 acceptance command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_performance_bounds.py
```

Result: 6 files, 220 passed, 0 failed (one platform skip).

Supplemental resources/strict-reference command result: 2 files, 173 passed,
0 failed. `ruff check` passed for both changed code/test files, and
`git diff --check` passed. Whole-file `ruff format --check` still reports the
pre-existing formatter drift already documented for `scheduler.py` and
`test_bash_e2e.py`; the new blocks were aligned to Ruff's proposed formatting
without mechanically reformatting unrelated lines.

### Files changed

- `plugins/workflow/scheduler.py`
- `tests/plugins/workflow/test_bash_e2e.py`

### Concerns and self-review

- No functional concerns remain within the rereview finding. The fix changes
  only Archon v3 Bash-node preflight inventory and reuses the bounded Task 11
  classifier rather than adding another shell parser.
- No admission contract, direct executor behavior, legacy normalizer behavior,
  Phase 4 surface, metadata, core tool, prompt-cache, or Task 12 code changed.

## Fix Round 3

Implementation commit: `0afb27113e97278394d5e2b680cde66bbc02bb72` —
`fix(workflow): harden bash reference and spill boundaries`.

Implementation tree: `776a59d3d00750593af55b57cfd26f12c5a3cde4`.

### Quality/security findings fixed

- **I-1 — premature Bash nesting exit:** the bounded lexer now preserves the
  outer command-substitution frame across `case` patterns, scoped heredocs,
  legacy `$[...]` arithmetic, ANSI-C `$'...'`, `[[ ... ]]` conditionals,
  POSIX `name()` functions, Bash `function WORD` declarations (including
  hyphenated names), and direct/named `coproc` compound commands. Reserved-word
  state for `if`/`then`, `time`, braces, nested commands, conditionals, and
  coprocesses is restored before subsequent references are classified. All
  unsupported nested references fail before spawn for inline and spill values.
- **I-2 — strict parsing before lexical authority:** Bash surfaces now discover
  bounded reference-like candidates, apply escape/comment/shell-context
  authority first, and only then apply the strict v3 reference grammar. The
  shared `bash_output_references()` path is used by schema admission, direct
  rendering, and scheduler preflight. Strict parsing is confined to the
  admitted span, successful token coordinates and syntax-error offsets are
  rebased to the authored template, and malformed literal references remain
  literal. Candidate discovery stops at the next dollar in one forward scan,
  eliminating the dollar-dense quadratic path.
- **I-3 — mutable verified spill inode:** secure v3 rendering now snapshots the
  exact bytes while hashing the verified regular, single-link inode, unlinks
  the pathname, removes the spill directory, and gives the child only bounded
  anonymous pipe read descriptors. A retained same-inode writer can change only
  the orphaned materialization inode; it cannot alter the verified snapshot the
  child receives. Publication faults become terminal `bash_spill_integrity`
  failures rather than clean truncated EOF.
- **I-4 — incomplete descriptor ownership:** ownership begins immediately when
  rendering returns and is closed by an encompassing executor `finally`.
  Materialization, evidence, argv/environment/deadline construction, stream
  setup, spawn callbacks, process callbacks, artifact work, publisher errors,
  and all early returns are exception-safe. Read ends are released only after
  `ManagedProcessTree` pins and exec-confirms them; the sole non-daemon
  publisher is synchronously joined on every exit.

Additional rereview regressions cover `$$USER_MESSAGE` (the second dollar is
PID-expansion text, not a scalar substitution), POSIX/Bash function forms,
conditional ERE `)` and bounded `]]` recognition, direct/named coprocesses,
span over-read, exact malformed-producer reporting, and linear dollar-dense
candidate work. The final independent diff refresh reported no remaining
concrete Task 11 defect in the reference, spill, executor, or cleanup seams.

### RED / GREEN evidence

The required pre-production review RED was run before production edits:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_lexer_security.py \
  tests/plugins/workflow/test_phase3_bash_reference_ordering.py \
  tests/plugins/workflow/test_phase3_bash_descriptor_faults.py
```

Result: exit 1; 7 passed and 21 failed. The failures reproduced premature
shell-frame exits, parse-before-literal filtering, verified-inode mutation,
and descriptor/publisher ownership faults. The first corrected focused set
passed 44/44, and its then-current combined acceptance set passed 439/439.

Subsequent adversarial rereview cycles were also kept RED before each fix:

- Dollar-dense discovery, POSIX function state, `$$`, and bounded-span
  authority: 3 files, 38 passed / 7 failed; GREEN 45/45. The RED performance
  case performed about 537 million indexed reads and took 96.9 seconds; GREEN
  completed the same file set in 14.1 seconds.
- Alternate Bash functions, `[[` regex punctuation, and strict-error offset
  rebasing: 2 files, 40 passed / 11 failed; GREEN 51/51.
- Direct and named coprocess prefixes: 1 file, 45 passed / 6 failed; GREEN
  51/51.

Fresh final acceptance command after all fixes and formatting:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_bash_lexer_security.py \
  tests/plugins/workflow/test_phase3_bash_reference_ordering.py \
  tests/plugins/workflow/test_phase3_bash_descriptor_faults.py
```

Result: 11 files, 467 passed, 0 failed in 15.2 seconds.

Static verification:

```text
../../.venv/bin/ruff check <all eleven changed production/test files>
../../.venv/bin/ruff format --check \
  plugins/workflow/bash_rendering.py \
  plugins/workflow/executors/bash.py \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_bash_descriptor_faults.py \
  tests/plugins/workflow/test_phase3_bash_lexer_security.py \
  tests/plugins/workflow/test_phase3_bash_reference_ordering.py
git diff --check
git diff --cached --check
```

Result: Ruff lint passed, all six directly format-clean files passed, and both
whitespace checks passed. The remaining retained large files have the same
pre-existing whole-file formatter drift documented in earlier rounds; their
new hunks were linted without mechanically rewriting unrelated lines.

### Files changed in Fix Round 3

- `plugins/workflow/bash_rendering.py`
- `plugins/workflow/executors/bash.py`
- `plugins/workflow/language_schema.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/scheduler.py`
- `plugins/workflow/schema.py`
- `tests/plugins/workflow/test_performance_bounds.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`
- `tests/plugins/workflow/test_phase3_bash_descriptor_faults.py`
- `tests/plugins/workflow/test_phase3_bash_lexer_security.py`
- `tests/plugins/workflow/test_phase3_bash_reference_ordering.py`

### Phase boundary and concerns

- No loop-specific production or test file changed. The approved Phase 3
  design explicitly leaves `loop.until_bash` on its existing legacy
  materialization path until Phase 4, and the parent ruling kept that surface
  out of Task 11. A reviewer observed that the legacy loop pre-render can place
  newline-bearing data across a shell-comment boundary. This is pre-existing,
  not introduced by Task 11: `plugins/workflow/executors/loop.py` has identical
  blob `af564a2a2350d84bfc4922e3863955f61891f61f` at the pre-Task-11 handoff
  `25fc0397a` and pre-fix HEAD `6a824d52a`, and `git diff --quiet` returns zero.
  Its path traces to Task 7 commits `47d0aa741`/`e400cc410`; Task 11's optional
  `secure_v3=False` resource branch preserves that unchanged caller. The
  observation is recorded for Phase 4 rather than silently expanding scope.
- This Darwin host cannot execute native Windows descriptor inheritance and
  job-object containment. Existing platform gates and Windows-path tests remain
  the available local evidence; native Windows CI is still the platform
  authority.
- No functional Task 11 concern remains after the final clean rereview. No
  legacy normalizer contract, Phase 4 implementation, Task 12 surface, core
  tool schema, prompt cache, raw value/path metadata, or release branch changed.

## Fix Round 4

Fix Round 4 closed the two independent-review findings without broadening the
workflow core. Commit `fb866299d46031f16e6eb557891e4f216c3ca9c6`
(`fix(workflow): bind Bash substitutions to safe contexts`) contains the
tracked implementation, tests, customization ledger, and release-gate pinning.

### RED evidence and root causes

The first production-free RED used the mandatory isolated runner:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/tools/test_managed_process.py
```

Result: exit 1; 153 passed and 12 failed. The failures covered two admission
cases, eight direct-executor combinations (scalar/output reference, arithmetic
command/`for ((...))`, inline/spill-sized value), the exact readable-regular-file
descriptor-number-reuse attack, and the absent generic expected-identity API.
The attack reproduced the defect exactly: the child read
`b"readable replacement bytes"` while Bash evidence still carried the original
snapshot digest.

A supplemental admission RED then exposed that static validation classified
only output candidates: 88 passed and 2 scalar-admission cases failed. This was
fixed by validating the same bounded set of runtime-substituted scalar spans
alongside output candidates while continuing to return only parsed output
references. Unknown shell variables remain outside substitution authority.

The two root causes were:

- top-level unquoted `((` never opened the existing arithmetic lexer frame, so
  references in bare arithmetic commands and arithmetic `for` clauses were
  treated as ordinary simple-token positions;
- Task 10 pinned whatever object occupied a nominated descriptor number at
  spawn time, but had no path-free caller expectation with which to detect a
  close-and-reuse between verified publication setup and pinning.

### Fixes

- The Bash lexer now opens its bounded arithmetic frame for top-level unquoted
  `((`, and admission validates both recognized scalar substitutions and output
  references. Quoted punctuation remains ordinary data.
- `InheritedDescriptorIdentity` captures only device, inode, and file type.
  `ManagedProcessTree.spawn` accepts an optional aligned identity sequence and
  compares it against each race-safe `F_DUPFD_CLOEXEC` pin before child
  creation. The seam has no workflow imports, values, paths, or evidence.
- Spill transports capture identities immediately after `os.pipe()` and carry
  them privately through `RenderedBashCommand` into the generic spawn seam.
  Mismatch maps to `bash_spill_integrity`; the managed-process primitive still
  preserves caller descriptor ownership and closes every internal pin.
- The exact reuse regression proves the outcome is either original bytes or
  `bash_spill_integrity`, never replacement bytes paired with original evidence.
- The base release gate now selects all four Task 11 Bash security suites
  exactly once; the previously selected managed-process and process-registry
  suites are asserted explicitly.

### GREEN and authoritative gate evidence

Focused GREEN after all fixes:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/tools/test_managed_process.py
```

Result: 2 files, 168 passed, 0 failed.

The required six-file Task 11 contract passed 238/238. The expanded eleven-file
acceptance command from Fix Round 3 passed 484/484, including the real `/bin/sh`
contracts, strict-reference/resource regressions, lexer security, reference
ordering, descriptor faults, and performance bounds.

Static verification passed Ruff lint for every changed Python file, Ruff format
for the directly format-clean files, `bash -n` for the release gate, and both
staged and unstaged whitespace checks. Retained large files were not
mechanically reformatted across their pre-existing whole-file drift.

Clean-commit customization and release gates:

```text
../../.venv/bin/python scripts/check_upstream_customizations.py \
  --strict --base-ref HEAD
scripts/test_workflow_merge_gate.sh --phase base
```

The strict checker exited 0. The canonical base gate passed 2,761 Python tests
across 57 files, the installed-distribution integration test 1/1, and Desktop
155/155 across 11 files. It sealed
`TESTED_BASE_SHA=fb866299d46031f16e6eb557891e4f216c3ca9c6`.

### Files changed and residual risk

- `docs/upstream-customizations/workflow-orchestration.yaml`
- `plugins/workflow/bash_rendering.py`
- `plugins/workflow/executors/bash.py`
- `scripts/test_workflow_merge_gate.sh`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`
- `tests/scripts/test_workflow_merge_gate.py`
- `tests/tools/test_managed_process.py`
- `tools/managed_process.py`

The host is Darwin, so native Windows descriptor behavior was not executable
locally. The generic nonempty-inheritance Windows fail-closed contract remains
covered by its existing tests and native CI authority. No Task 12+, Phase 4,
literal `main`, base checkout, plugin tool surface, prompt cache, workflow value,
or filesystem-path evidence changed.

## Fix Round 5 — logical Bash continuations and array subscripts

### RED evidence and root causes

The first focused wrapper run added scalar/output admission checks, direct
inline/spill no-launch checks, scheduler-preflight checks, physical-coordinate
checks, and a linear-read bound for the two remaining review findings. It
failed 71 of 187 tests: every joined logical-operator and indexed-array case
was admitted by the physical-only lexer.

Two compatibility-driven RED cycles then prevented over-correction. Ordinary
argument text resembling `[[ ... ]]` or `items[...]` failed 6 cases before
top-level command-position tracking was added. An escaped even-length
backslash run failed 6 cases because the initial logicalizer erased a real
newline, and compound-assignment value text failed 3 cases because every `[`
inside the compound was initially treated as an element subscript.

The root causes were:

- Bash removes an active backslash-newline before operator recognition, while
  the lexer previously classified only physical adjacent characters;
- indexed-array assignment subscripts are arithmetic contexts even though
  their brackets were previously treated as ordinary punctuation; and
- safe classification requires distinguishing command/assignment positions,
  escaped-backslash parity, and `[subscript]=` at compound element-word starts.

### Fixes

- `classify_bash_reference_spans()` now validates physical spans, constructs a
  bounded linear logical stream with physical-to-logical boundary mapping, and
  maps admitted decisions back to the original coordinates. The scan honors
  backslash-run parity, so only active continuations erase their newline.
- The existing bounded classifier remains the shared authority used by static
  admission, scheduler preflight, inline rendering, and spill rendering. It now
  recognizes joined bare arithmetic, command/arithmetic/parameter expansions,
  legacy arithmetic, conditionals, heredocs, and sibling operator prefixes.
- Top-level indexed assignments, augmented assignments, compound assignments,
  and compound append assignments mark references in their subscript ranges as
  unsupported before rendering, spill publication, or process launch.
- Command-position and compound-element boundaries preserve quoted punctuation,
  ordinary argument brackets/conditionals, embedded bracket text in compound
  values, escaped references, continued comments, and unrelated continuations.

### GREEN and static evidence

The final focused Bash substitution file passed 186/186. The required exact
six-file Task 11 command passed 334/334, and the expanded eleven-file suite
passed 581/581. A dedicated affected-surface run covering resources, the serial
and parallel schedulers, and performance bounds passed 101/101.

Ruff lint passed for all four changed Python files. Ruff format validation
passed for the three files that were format-clean on entry; the retained
performance file was not mechanically reformatted across pre-existing drift.
`git diff --check` passed. No customization ledger or release-gate file changed,
so the strict customization/base gate was not rerun.

### Files changed and residual risk

- `plugins/workflow/bash_rendering.py`
- `tests/plugins/workflow/test_performance_bounds.py`
- `tests/plugins/workflow/test_phase3_bash_reference_ordering.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`

The real shell execution evidence was collected on Darwin with `/bin/sh`;
Windows skips those platform-specific executions. The admission, scheduler,
no-launch, coordinate, and complexity contracts are platform-neutral. No Task
12+, Phase 4, release gate, customization ledger, literal `main`, base checkout,
tool surface, or prompt-cache behavior changed.

## Fix Round 6 — complete indexed contexts and physical heredoc semantics

Commit `58dc9defa` (`fix(workflow): close Bash context classification gaps`)
closes the remaining quality-closure findings with one production lexer change
and focused behavioral regressions.

### RED evidence and root causes

The production-free focused run through the mandatory wrapper reported 207
passed and 122 failed across the substitution and performance files. The RED
covered admission, scheduler preflight, and direct inline/spill execution for
leading redirections, function and coprocess bodies, assignment-builtin
prefixes, quoted assignment arguments, escaped subscript candidates, and
quoted here-document body continuations. The bounded-read regression also
failed before implementation.

Three requested shell-compatibility probes added after the first implementation
produced a second clean RED: the substitution file reported 321 passed and 48
failed for `command -p`, `command --`, `builtin --`, a quoted here-document
whose `<<` operator is joined by an active continuation, and an unquoted
delimiter split by an active continuation. Here-document-looking text on a
continued comment was already green and remains covered.

The remaining causes were:

- top-level command tracking consumed leading redirection operands and wrapper
  options as command words instead of preserving assignment-command position;
- literal escaped scalar/output candidates could be overwritten later by an
  enclosing unsupported subscript range;
- global continuation removal incorrectly changed physical bytes inside
  single-, double-, and backslash-quoted here-document bodies; and
- an active continuation joining a delimiter token was incorrectly treated as
  delimiter quoting.

### Fixes

- Top-level command state now preserves position across leading redirection
  operands, `function`, direct/named `coproc`, `command`/`builtin` prefixes,
  their supported option separators, and assignment builtins. Quoted
  `declare`/`typeset`/`local`/`readonly`/`export` arguments share the indexed
  assignment rejection path. Ordinary arguments and compound value bracket
  text remain data.
- Literal escaped candidates win over later unsupported-range marking, so
  direct and compound subscript-looking words neither acquire dependencies nor
  substitute or spill their escaped scalar/output text.
- A shared bounded delimiter parser identifies quote removal once. The physical
  logicalizer preserves backslash-newline bytes only in quoted here-document
  bodies, including a logically joined `<<`; active removal remains unchanged
  elsewhere and in unquoted bodies. Physical reference offsets continue to map
  to the authored template.

### GREEN and static evidence

All tests used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`:

- focused substitution + performance: 2 files, 379 passed;
- exact required Task 11 contract: 6 files, 518 passed;
- expanded acceptance set: 11 files, 765 passed; and
- affected resources, serial/parallel schedulers, and performance: 4 files,
  102 passed.

Ruff lint passed for all three changed Python files. Ruff format validation
passed for the two format-clean files; the retained performance file was not
mechanically rewritten across its pre-existing whole-file drift. Both staged
and unstaged whitespace checks passed before the commit.

### Files changed and residual risk

- `plugins/workflow/bash_rendering.py`
- `tests/plugins/workflow/test_performance_bounds.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`

The real-shell contracts ran on Darwin and retain the existing Windows skip;
admission, scheduler, no-launch, offset, and complexity coverage is
platform-neutral. No Task 12+, Phase 4, release gate, customization ledger,
base checkout, literal `main`, model-tool surface, prompt cache, raw value/path
evidence, or legacy workflow behavior changed. No known functional concern
remains in the Fix Round 6 scope.

## Fix Round 7 — quote removal, descriptor heredocs, and physical input semantics

Commit `edd0f9ce4` (`fix(workflow): close bash parser compatibility gaps`)
closes the four retained Task 11 closure-review findings in the bounded Bash
classifier and its behavioral contract tests.

### RED evidence and root causes

Before any production edit, the authoritative three-file wrapper run reported
436 passed and 236 failed. The failures covered file-descriptor-prefixed
heredocs, quote-removed command words and assignment-builtin operands, joined
`<<-` operators and multiple heredocs, and physical comment termination across
admission, scheduler preflight, and direct inline/spill no-launch contracts.

The root causes were:

- a numeric descriptor immediately before `<<` remained recorded as the
  top-level command word, so subsequent references were classified as
  arguments instead of command-position content;
- wrapper names, wrapper options, assignment-builtin names, and assignment
  operands were compared in their authored form rather than after shell quote
  removal;
- the physical heredoc pre-scan recognized only a subset of active
  continuation joins and did not consistently queue `<<-` or multiple
  heredocs while retaining authored-coordinate mapping; and
- the logicalizer erased backslash-newline inside physical comments even
  though Bash comments terminate at the physical newline and preserve that
  text literally.

### Fixes

- Descriptor words `0` through `9` are cleared when they prefix `<<` or
  `<<-`, including leading/multiple redirections, so command position remains
  correct without widening ordinary numeric-word behavior.
- A bounded quote-removal helper retains source coordinates while dequoting
  single-, double-, backslash-, and concatenated shell words. Wrapper and
  assignment-builtin recognition now uses the dequoted word, and indexed
  assignment subscripts map rejection back to the authored range even when
  the name, option, or assignment word is quoted or escaped.
- The physical heredoc parser now follows active continuations at every
  operator boundary, including between the two `<` characters and around the
  `-` in `<<-`; it queues multiple delimiters, preserves tab-stripping
  semantics, and keeps quoted bodies and physical offsets stable.
- Continuation preservation now covers physical comment text through its
  physical newline as well as quoted heredoc bodies. False heredoc-looking
  tokens in quotes, arithmetic, conditionals, and comments remain data.
- End-of-input finalization applies the same top-level quote-removal and
  assignment checks when a shell word ends at EOF.

### GREEN and static evidence

All tests used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`:

- final focused amended files: 3 files, 752 passed;
- exact required Task 11 contract: 6 files, 883 passed;
- expanded acceptance set: 11 files, 1,131 passed; and
- affected resources, serial/parallel schedulers, and performance: 4 files,
  104 passed.

Ruff lint passed for all four changed Python files. Ruff format validation
passed for the three files that were format-clean on entry; the retained
performance file was not mechanically rewritten across its pre-existing
whole-file drift. Staged and unstaged whitespace checks passed before commit.

### Files changed and residual risk

- `plugins/workflow/bash_rendering.py`
- `tests/plugins/workflow/test_performance_bounds.py`
- `tests/plugins/workflow/test_phase3_bash_reference_ordering.py`
- `tests/plugins/workflow/test_phase3_bash_substitution.py`

The implementation remains a deliberately bounded classifier rather than a
general Bash parser; the four retained compatibility classes are covered by
matrix, admission, scheduler, no-launch, physical-offset, false-positive, and
linear-read contracts. Real-shell execution ran on Darwin with the existing
Windows platform skip; the classifier and scheduler contracts are
platform-neutral. No Task 12+, Phase 4, release gate, customization ledger,
base checkout, literal `main`, model-tool surface, prompt cache, raw value/path
evidence, or legacy workflow behavior changed. No known functional concern
remains in the Fix Round 7 scope.

## Fix Round 8 — unified Bash parser phase state

Implementation commit: `7fdcbee8158aa57bef00ace43fab94e82cf566e8`
(tree `7e0d50d38b3171ad5e6b3dcfc03ad16a4800555a`).

### Root cause and RED evidence

The retained failures were one architectural class rather than independent
tokens: a shallow physical pre-scan guessed continuation, comment, quote, and
heredoc behavior before a separate richer logical classifier reconstructed
nesting and command state. The two authorities diverged whenever Bash phases
interacted.

Before any production edit, retry-disabled wrapper tests proved RED:

- process substitution, phase re-entry, and prior-continuation comments:
  8 passed and 71 failed; and
- proactive here-string, extglob, and brace-expansion siblings: 4 passed and
  86 failed.

### Correction

- Removed the divergent physical preservation scan and whole-template logical
  rewrite from reference classification.
- Made the bounded classifier operate on authored source with direct physical
  offsets and continuation-aware token matching.
- Kept one contextual quote/frame/comment/heredoc state, including command,
  arithmetic, parameter, backtick, process-substitution, extglob, and brace
  boundaries.
- Added maximal-munch redirection recognition so `<<<` is not reconsidered as
  overlapping `<<`, while ordinary and quoted literal contexts retain their
  approved behavior.
- Kept operator probes first-character guarded so the existing linear-read
  bound remains enforced.

### GREEN and static evidence

All Python tests used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`:

- combined new regression set: 169 passed;
- full substitution plus authored-offset surfaces: 940 passed;
- exact required six-file Task 11 gate: 1,078 passed;
- exact Closure Review 4 eleven-file set: 1,338 passed, with the existing
  native-Windows managed-process platform skip; and
- affected resources, serial/parallel schedulers, and performance: 105 passed.

The root controller independently reran the exact eleven-file set after the
commit: 1,338 passed and 0 failed in 21.6 seconds with retries disabled.
Ruff, retained formatting checks, and staged/unstaged whitespace checks pass.

### Authenticated review packages

- Fix Round 8 code-only package:
  `task-11-fix8-review.diff`, SHA-256
  `33c6a551ee786927e92f484d0a0867cfe5dbede3f7b02e032cb6efc88029f6a3`.
- Complete Task 11 code package:
  `task-11-final-review-5.diff`, SHA-256
  `2cde354a98ab67d47cb4e053e703b5ebf921d7f3c41a968b79687970d662623d`.

Both package bodies were verified byte-identical to their corresponding Git
diff ranges. Task 12 and Phase 4 remain untouched pending independent Task 11
specification and quality closure.
