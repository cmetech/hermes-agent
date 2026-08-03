# Task 11 Independent Functional Specification Closure Review

## Verdict

**FAIL** — the two exact former examples are fixed, but one **Important**
functional gap remains in the same fail-closed lexer requirement: Bash
grammar-level array subscripts are still admitted as ordinary simple-token
positions. Task 11 is therefore not ready to hand off to Task 12.

Severity count: **0 Critical, 1 Important, 0 Minor**.

## Authenticated review scope

- Reviewed range: `25fc0397aafcd9b373169c189a2566a2fa570aff..fb866299d46031f16e6eb557891e4f216c3ca9c6`
- Base tree: `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Head tree: `a283230cc80f9b359fe2d999a331032a18e15305`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Authenticated package: `task-11-final-review.diff`
- Package SHA-256: `ed58a97524f61f16825622a2318ac5ac66a8f4d18bf80d5d43e0a8b079989f32`
- The package body beginning after its 32-line manifest is byte-identical to
  `git diff -U10 25fc0397a..fb866299d` (`cmp` succeeded); both diff bodies have
  SHA-256 `b1afdc05333019b15b61600a4255285b1ea1bda5bf389dd044e3fd078b224567`.
- The worktree was clean before review and remained clean after the test run.
  This report is the only authored artifact.

I read the repository `AGENTS.md`, approved Phase 3 design and plan, Task 11
brief and implementation report, every prior Task 11 specification and quality
review/rereview, and the applicable review and verification skill instructions.
I traced the complete authenticated diff and the live renderer, executor,
schema/admission, resource renderer, scheduler preflight, code catalog,
managed-process descriptor seam, merge-gate selection, and relevant tests.

## Important finding

### I-1 — Grammar-level Bash array subscripts are still admitted

**Locations:** `plugins/workflow/bash_rendering.py:548-553,638-671,876-886,907-953,1137-1153`; `plugins/workflow/resources.py:944-965`; `plugins/workflow/schema.py:1104-1116`; `plugins/workflow/scheduler.py:1522-1537`

The Fix Round 4 lexer now opens an arithmetic frame for top-level unquoted
`((` at `bash_rendering.py:666-671`. That closes the exact bare arithmetic
command and arithmetic `for ((...))` examples. It does not recognize the
sibling Bash grammar in an assignment such as
`items[$producer.output]=value` or `items[$USER_MESSAGE]=value`.

For either form, `[` and `]` reach the generic top-level character path at
`bash_rendering.py:876-886`. When the reference start is encountered, no frame
is active, so `bash_rendering.py:548-553` records an admitted unquoted context.
`bash_output_references()` consequently accepts the output form during schema
admission and scheduler preflight. The direct renderer collects the output or
scalar substitution and `render_v3_bash()` repeats the same successful
classification before launch. Value size is not involved in the decision, so
both inline and spill values follow the unsafe admission path.

A Bash indexed-array subscript is an arithmetic-evaluation grammar context,
not ordinary command-word text. The Task 11 replacement table therefore does
not establish exact-data semantics there; data may be arithmetically
reinterpreted on Bash-compatible hosts, while other `/bin/sh` implementations
may reject the same admitted command. Both outcomes violate the approved
contract, which requires unsupported grammar to fail before launch with
`bash_reference_context_unsupported`.

This is also explicitly unfinished work from the preceding quality rereview.
Its required correction covered bare `((...))`, `for ((...))`, **and sibling
grammar-level arithmetic/subscript contexts**, with output/scalar admission and
direct no-launch coverage at inline and spill sizes. Fix Round 4 added the
first two forms and their tests, but neither production handling nor tests for
the subscript part.

**Required correction:** extend the bounded lexer to recognize actual
grammar-level array-subscript positions, or conservatively reject a candidate
when that grammar cannot be proven to be an ordinary command word. Add benign
admission and direct-executor no-launch regressions for output and scalar
references at inline and spill sizes, and cover the scheduler authority as
appropriate. The rejection must happen before rendering, spill materialization,
or process launch.

## Former-finding disposition

| Prior concern | Disposition | Evidence |
|---|---|---|
| v3 gating and legacy preservation | **Closed** | `BashExecutor.execute()` selects the secure renderer only for Archon plus normalizer v3; historical normalizers and legacy retain the prior pathname behavior. |
| Admission/runtime lexer authority and literal-before-grammar ordering | **Closed** | `bash_output_references()` classifies output and recognized scalar candidate spans before strict output parsing; schema and scheduler use the same authority, while the direct renderer revalidates all actual substitutions. |
| Scheduler context-blind preflight | **Closed** | `_preflight_strict_node_references()` calls `bash_output_references()` for Bash nodes. |
| Command-substitution/case, legacy arithmetic, ANSI-C, conditional, heredoc, parameter, backtick, escape/comment, and line-continuation handling | **Closed for covered forms** | The bounded state machine and real behavioral tests cover these dialect and restoration cases. The array-subscript gap above prevents overall lexer closure. |
| Mutable verified inode / pathname race | **Closed** | Spill files use descriptor-relative exclusive creation, bounded writes, `fsync`, regular/single-link and identity/size/digest verification, then a bounded detached byte snapshot; path authority is removed before launch. |
| Descriptor and publisher cleanup on faults | **Closed** | Renderer and executor ownership guards cover construction, evidence, callbacks, output setup, spawn, publication, child exit, and close/reuse cases; focused fault tests passed. |
| Bare `((...))` and `for ((...))` scalar/output admission and execution | **Closed for those exact forms** | Top-level unquoted `((` now enters the arithmetic frame, and admission plus direct no-launch tests exercise output/scalar references at inline and spill sizes. The broader prior finding remains partially open because subscripts are untreated. |
| Descriptor-number reuse between rendering and Task 10 pinning | **Closed** | `_SpillTransport` captures `InheritedDescriptorIdentity`; `ManagedProcessTree.spawn()` accepts the generic aligned identity tuple, duplicates with `F_DUPFD_CLOEXEC`, verifies the pinned duplicate before child creation, and the workflow executor maps mismatch to `bash_spill_integrity`. |
| Native Windows large/inline branches | **Closed by source and focused tests** | Large values fail closed when descriptor transport is unavailable; inline rendering retains the exact platform-gated `bash -c` argv and managed containment. Native Windows execution was not available on this Darwin host. |

## Contract trace

| Task 11 contract | Result | Notes |
|---|---|---|
| v3-only activation; legacy and historical normalizers unchanged | **PASS** | Secure rendering requires Archon and normalizer v3. |
| UTF-8 byte boundary at 32,768; exact 32,767/32,768/32,769 behavior | **PASS** | Byte-based threshold and real-shell boundary tests cover all three quote contexts and multibyte data. |
| Empty/metacharacter/Unicode/terminal-`x`/trailing-newline preservation | **PASS** | Inline quoting and descriptor spill sentinel/status prologue preserve the covered values exactly. |
| Exact unquoted/double/single replacement table; inline/spill consistency | **PASS** | Spill replacements match the approved table; exact-command and repeated-value tests cover quote contexts and deduplication. |
| Fail-closed lexical proof, including line continuations and grammar-level contexts | **FAIL** | Line-continuation, heredoc, nesting, escape, comment, redirection, and former bare arithmetic cases are covered; array-subscript arithmetic remains admitted (I-1). |
| NUL, 500,000-byte value, 64-file, and 2,000,000-byte aggregate bounds | **PASS** | Stable codes and boundary/fault tests are present; repeated large values deduplicate by exact encoded bytes. |
| Descriptor-relative creation, no-follow/exclusive mode, verification, races, read failures | **PASS** | Implementation uses strict file verification followed by detached bounded publication and path-free descriptor identity binding. |
| Descriptor lifecycle, unrelated-handle isolation, and generic Task 10 seam | **PASS** | Generic optional identities do not introduce workflow concepts into `ManagedProcessTree`; pinning and cleanup remain bounded. |
| Exact `argv[-1]` command authority and bounded/private evidence | **PASS** | Metadata contains sizes, digests, counts, descriptor numbers, and digest mapping only—no values, command text, or spill paths. |
| Stable compatibility codes and merge-gate selection | **PASS, incomplete coverage** | All four Task 11 codes are cataloged for Archon v3 and the new suites are selected by the base gate, but no selected test exercises I-1. |
| Phase 4 `until_bash` and Task 12 persistent-session boundaries | **PASS** | This range does not implement Phase 4 loop-Bash semantics or Task 12 session behavior. |

## Fresh verification

Executed only the existing narrow benign suite through the canonical runner,
with retries disabled (never direct `pytest`):

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

Fresh result: **11 files, 484 tests passed, 0 failed in 14.1 seconds**.

The full base gate, installed-runtime gate, and Desktop suites reported in the
Task 11 implementation report were not rerun for this narrow independent
review; those remain historical claims. The fresh green suite does not alter
the FAIL verdict because it contains no grammar-level array-subscript case.

## Closure decision

Task 11 remains **open**. Close I-1 and add the missing behavioral regressions
before starting Task 12 or treating the Phase 3 Bash boundary as complete.
