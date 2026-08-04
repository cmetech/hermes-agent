# Task 11 Independent Specification Review 1

## Reviewed identities

- Base commit: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Head commit: `75a7523be9243eb3f300f7ec729ebb10beb03ba2`
- Head tree: `da272d5532cddb2425dab4327de4684c7b9fdf3c`
- Reviewed branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Reviewed package: `review-25fc0397a..75a7523be.diff`
- Worktree HEAD and tree matched the requested head and tree. The worktree was clean before this permitted report write.

## Verdict

**FAIL**

## Severity totals

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 3 |
| Minor | 0 |

## Scope and method

This was a read-only, goal-backward review of Task 11 against the complete Task
11 brief and approved Phase 3 design. I read the supplied one-commit diff in
full and read every changed production and test file in full. I traced the
renderer from substitution discovery through lexical classification,
materialization, `ManagedProcessTree.spawn()`, descriptor closure, result
metadata, and durable-code projection.

I inspected unchanged code only for concrete named risks exposed by the diff:

- `plugins/workflow/schema.py` for the required admission-time Bash-context
  decision and escaped/comment reference handling;
- `plugins/workflow/scheduler.py` and `plugins/workflow/executors/base.py` for
  the recorded-normalizer gating authority; and
- `tools/managed_process.py` for Task 10's explicit bounded
  `inherited_descriptors` seam, exact descriptor-number inheritance,
  start-new-session containment, and unrelated-handle isolation.

Per the review instruction, I did not rerun the report's claimed suites. The
findings below are established directly by control-flow and test-contract
inspection and did not require repeating the reported test commands. I made no
production, test, index, HEAD, branch, or worktree mutation.

## Strengths

- The byte authorities are exact: inline `32,768`, per-value `500,000`, at
  most `64` distinct spill files, and aggregate `2,000,000` bytes. UTF-8 bytes,
  not Python characters, drive every Task 11 threshold.
- Large duplicate values are deduplicated by exact bytes before descriptor
  assignment. Deterministic digest variables and opaque indexed filenames are
  used.
- The three required spilled replacement forms are implemented exactly at
  `plugins/workflow/bash_rendering.py:604`: unquoted
  `"${__HERMES_WF_SPILL_*}"`, already-double-quoted
  `${__HERMES_WF_SPILL_*}`, and already-single-quoted
  `'"${__HERMES_WF_SPILL_*}"'`.
- The prologue at `plugins/workflow/bash_rendering.py:589` appends and removes
  the `x` sentinel, records `cat` status before `printf`, and uses the inherited
  descriptor rather than a spill pathname.
- Spill creation is descriptor-relative, no-follow, exclusive, mode `0600`,
  bounded, fsynced, reopened through the directory descriptor, and checked for
  regular/single-link identity, exact size, digest, and rewind before launch.
- The executor passes only `RenderedBashCommand.inherited_descriptors` through
  Task 10's bounded seam. Task 10 pins exact child descriptor numbers, preserves
  `start_new_session`, and closes all unrelated handles. Task 11 closes its
  caller-owned descriptors after spawn and on the inspected pre-spawn failure
  paths.
- The exact rendered command is passed as the shell `argv[-1]`. Attempt
  evidence includes only bounded template/rendered sizes and digests, spill
  aggregates/content digests, and descriptor numbers; it contains neither
  values nor spill paths.
- Native-Windows large rendering fails before materialization and launch when
  the descriptor contract is unavailable. The Phase 4 loop caller keeps the
  existing renderer because `secure_v3` defaults false.
- All four Bash codes are registered for Archon normalizer v3 in the bounded
  durable catalog, and runtime tests exercise the NUL, limit, integrity, and
  unsupported-context emitters.
- The real-shell tests cover the 32,767/32,768/32,769 boundary, multibyte
  boundaries, all three quote contexts, empty and metacharacter-rich values,
  terminal `x`, trailing newlines, deduplication, pathname replacement, the
  descriptor count boundary, and legacy pathname behavior.
- No core tool, prompt/history mutation, MCP/skills node kind, Phase 4/5
  behavior, API/path/raw-value surface, or unrelated core capability was added.

## Findings

### Critical

None.

### Important

#### I-1 — Archon profile alone activates v3 behavior for recorded v1/v2 runs

**File/line:** `plugins/workflow/executors/bash.py:52-107` (changed),
`plugins/workflow/executors/base.py:32-84` and
`plugins/workflow/scheduler.py:3080-3171` (unchanged code inspected for this
specific gating risk), `plugins/workflow/resources.py:745-770`.

**Requirement:** Task 11 semantics are gated only on newly admitted
`archon-2026-07` normalizer v3. Existing admitted Archon v1/v2 runs must resume
with their recorded behavior; unversioned and Hermes legacy must remain exact.

**Defect:** Every new decision in `BashExecutor` uses only
`context.language_profile is ARCHON_2026_07`: the `variables-v3` directory,
`secure_v3=True`, construction of `RenderedBashCommand`, and Bash evidence.
`NodeExecutionContext` carries no normalizer version, even though the scheduler
has the sealed `package.language.normalizer_version`. For an admitted Archon
v1/v2 run, the scheduler constructs a v1/v2 `VariableContext`; then
`substitution_renderer()` returns that legacy context, and `BashExecutor` calls
its legacy `render_bash()` with the unsupported `secure_v3` keyword. The
resulting `TypeError` is converted by the scheduler to `executor_crash`. An old
Archon command without substitutions also acquires v3 evidence and path
selection solely from the profile.

**Impact:** Previously admitted Archon v1/v2 Bash runs cannot reliably resume
with their sealed semantics. This is a direct compatibility regression at the
durable-version boundary and violates the global gating constraint.

**Correction:** Carry the recorded normalizer version explicitly in
`NodeExecutionContext` (or an equally explicit sealed execution authority),
pass it from the scheduler, and define one `secure_v3` predicate requiring both
Archon profile and normalizer version 3. Use that predicate for the spill
directory, renderer selection, command wrapper, evidence, Windows gate, and
all error semantics. Add scheduler-level resume coverage for Archon v1 and v2
Bash nodes as well as v3.

#### I-2 — The shell-context lexer is not part of admission, and admission does not ignore escaped/comment output references

**File/line:** `plugins/workflow/bash_rendering.py:520-531`,
`plugins/workflow/executors/bash.py:60-98` (changed), and
`plugins/workflow/schema.py:1080-1114` (unchanged code inspected for this
specific admission risk).

**Requirement:** A recognized v3 Bash output reference in a heredoc
delimiter/body, command substitution, backticks, arithmetic expansion,
parameter expansion, or ambiguous shell state must block admission with
`bash_reference_context_unsupported`. Escaped references and comment text must
be ignored. Runtime checking remains necessary for dynamic scalar variables.

**Defect:** `_reference_contexts()` is reachable only from `render_v3_bash()`
inside the executor. The admission validator still feeds every Bash output
reference directly from context-blind `iter_output_references()` into the
dependency/path checks. Consequently, an unsafe `$producer.output` with a
declared dependency is accepted, trusted, snapshotted, and only rejected after
the node is claimed. Conversely, an escaped or comment-only output reference
is still treated as a real dependency during admission and can be rejected as
`output_reference_not_declared_dependency`, even though the runtime lexer
would ignore it. The new tests use only `$USER_MESSAGE` and call the executor
directly, so they cannot expose either admission defect.

**Impact:** Admission evidence is false, unsafe packages cross the immutable
promotion boundary, failures consume runtime scheduling/claim machinery, and
literal escaped/comment Bash text is incompatibly rejected. The required
admission/runtime division and stable-code emitter coverage are incomplete.

**Correction:** Expose a value-independent, bounded Bash span classifier from
the lexer and invoke it from v3 static validation before dependency/path
validation. Remove escaped/comment spans from the Bash reference inventory,
emit `bash_reference_context_unsupported` at the exact Bash source surface for
unsupported output-reference spans, and retain the executor check for scalar
variables and defense in depth. Add load/admission tests using real
`$producer.output` references for every context plus escaped/comment cases.

#### I-3 — Required native-Windows inline and shell-read-failure branches are not behaviorally tested

**File/line:**
`tests/plugins/workflow/test_phase3_bash_substitution.py:453-482` and
`tests/plugins/workflow/test_phase3_bash_substitution.py:552-583`.

**Requirement:** The Task 11 test contract requires native-Windows large-value
fail-closed behavior **and exact inline Bash command construction/containment**.
It also requires inherited-descriptor read failure and proves that capturing
`cat` status prevents the sentinel `printf` from masking failure.

**Defect:** The only Windows-specific Task 11 test calls `render_v3_bash()`
after monkeypatching `_NATIVE_WINDOWS`; it proves the large-value renderer
gate but never calls `BashExecutor`, `_find_bash`, or
`ManagedProcessTree.spawn()` for an inline value. The descriptor-failure test
closes the descriptor before `spawn()`, so Task 10 rejects it during
pre-launch pinning; it never executes the generated `/bin/sh` prologue and
therefore does not prove that a real `cat` read failure propagates rather than
being masked by `printf x`. The exact prologue string assertion is useful but
is not behavioral read-failure evidence.

**Impact:** Regressions in Windows inline argv construction, empty descriptor
inheritance/containment, or prologue failure propagation can pass every
reported Task 11 test. These are explicit acceptance branches, not optional
edge coverage.

**Correction:** Add a Windows-platform executor test that captures the exact
`[_find_bash(), "-c", rendered_command]` argv, asserts an empty inherited
descriptor set, and exercises the existing containment path for an inline
value. Add a real `/bin/sh` failure test in which the prologue's descriptor
read fails after launch and assert the shell exits nonzero despite `printf x`;
also assert descriptor/process cleanup on that path.

### Minor

None.

## Cannot-verify items

- I did not independently rerun the claimed `197` focused tests or the `173`
  supplemental tests, as instructed. Their pass counts remain report claims,
  not evidence generated by this review.
- This POSIX checkout cannot directly demonstrate native-Windows inline Bash
  construction or Windows job containment. The supplied test package also
  lacks that required behavioral test, as recorded in I-3.
- No external filesystem capable of forcing an in-flight regular-file read
  error was exercised. The supplied tests validate pre-launch closure but not
  the post-launch `cat`-status branch, also recorded in I-3.

All other Task 11 requirement families were verifiable from the exact diff,
full changed files, and the narrowly inspected unchanged authorities.

## Final assessment

The descriptor-authoritative materialization, exact content-preserving shell
rendering, numeric limits, bounded evidence, explicit inherited-handle use,
parent closure, and legacy Hermes path are well implemented. However, Task 11
cannot pass specification review while v3 semantics are keyed to profile
rather than the sealed normalizer, the unsafe-context decision occurs after
admission and conflicts with escaped/comment handling, and two explicit
failure/platform acceptance branches remain unproved. Fix I-1 and I-2 and add
the missing I-3 behavioral coverage before re-review.
