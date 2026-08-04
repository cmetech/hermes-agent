# Task 11 Quality and Security Closure Review 3

## Verdict

**FAIL.** Task 11 is not ready for quality/security closure.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 4 |
| Minor | 0 |

Fix Round 6 closes the exact leading plain-redirection, function/coprocess,
unquoted wrapper, whole-quoted assignment-argument, escaped-subscript, and
direct/joined quoted-heredoc examples added for the prior closure findings. It
does not close those grammar classes. A file-descriptor-prefixed heredoc still
loses command position, shell quote removal is not represented in wrapper and
assignment-builtin tracking, and a continuation between `<<` and `-` is
misparsed by the physical heredoc prepass. In addition, continuation removal
inside physical comment text can hide a real heredoc on the following line.

## Authenticated identity and scope

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree: `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Fix Round 6 base: `b02e3a999953c93bdb0712283b1becd17e66e867`
- Fix Round 6 base tree: `47843446f28820485b5f7a1b0f94e6e3ece477c6`
- Reviewed implementation commit:
  `58dc9defa31c7319b2a5e1c9730622c06609e9dd`
- Reviewed implementation tree:
  `f48d3565fb7e46640578135b9972482f57dde617`
- Checkout HEAD before this report:
  `b09f8e243203cf82ccdf496c79e1c53e552910ab`
- Checkout tree before this report:
  `b1fde53f6cf2ad0122ebbc15d91042976a623279`
- `58dc9defa..b09f8e243` changes only the retained Task 11 report. There is no
  production, test, harness, or ledger delta after the reviewed implementation
  tree.
- Authenticated Fix Round 6 package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-fix6-review.diff`
- Package SHA-256:
  `9795c3abbf60e1edcc57fb4651902d70e86b55580438b526ffcd7eb74d404738`
- The package diff body is byte-identical to
  `git diff -U10 b02e3a999..58dc9defa` (`cmp` exited 0); both bodies have
  SHA-256 `ba68ef65d163193ad39c4a9ce7ee7d8772a3334873dc84429fba3d9797fa1b60`.
- `git diff --check` was clean for Fix Round 6 and the complete Task 11
  implementation range. The worktree was clean before this permitted report
  write.

I read the complete root `AGENTS.md`, approved Phase 3 design and plan, their
review context, Task 11 brief/report, every retained Task 11 specification and
quality review/rereview, the Fix Round 6 package, the changed files in full,
and the live admission, scheduler, renderer, executor, and descriptor seams
needed to decide the retained findings. Defensive parser observations used
only benign strings and numeric expressions.

## Prior-finding dispositions

| Prior finding or contract | Disposition | Evidence |
|---|---|---|
| Original specification I-1 — Archon profile alone activated v3 | **ADDRESSED and unaffected** | `secure_v3` still requires Archon plus sealed normalizer v3. Fix Round 6 does not change executor gating. |
| Original specification I-2 — context authority absent/inconsistent at admission and scheduler | **ADDRESSED and unaffected** | Schema admission, strict resource rendering, scheduler preflight, and direct rendering still share `bash_output_references()` / `classify_bash_reference_spans()`. The remaining defects therefore propagate consistently rather than reflecting authority drift. |
| Original specification I-3 — native-Windows inline/read-failure coverage | **ADDRESSED and unaffected** | Fix Round 6 has no executor or managed-process delta. Native Windows execution remains unavailable on this Darwin host. |
| Original quality I-2 — strict grammar before escaped/comment filtering | **ADDRESSED and unaffected** | Literal decisions precede strict output parsing; Fix Round 6 additionally prevents an unsupported array range from overwriting an already-escaped decision. |
| Original quality I-3 — mutable verified spill inode | **ADDRESSED and unaffected** | Verified bytes remain detached into immutable snapshots, pathnames are removed, and only anonymous publication descriptors reach launch. |
| Original quality I-4 — pre-spawn descriptor ownership | **ADDRESSED and unaffected** | Renderer, publisher, executor, process, and stream cleanup paths have no Fix Round 6 delta. |
| N-1 — descriptor number close/reuse could substitute another readable object | **ADDRESSED and unaffected** | Path-free `InheritedDescriptorIdentity`, stable duplicate-before-compare pinning, aligned executor handoff, and `bash_spill_integrity` mapping are unchanged. |
| Q-1 — active continuation can form unsupported operators | **Reported examples addressed; class NOT CLOSED** | Joined `$(`, `${`, `$((`, `$[`, `[[`, bare `((`, and `<` + `<` cases remain rejected. Important I-3 below shows the sibling valid `<<` + continuation + `-` form is misparsed. |
| Q-2 / closure I-1 — indexed assignments through command-position forms | **Examples addressed; class NOT CLOSED** | Plain leading redirection, function/coprocess bodies, raw `command`/`builtin` prefixes, supported raw options, and fully quoted assignment arguments are covered. Important I-1 and I-2 show fd-heredoc and quote-removed equivalents still admit arithmetic subscripts. |
| Closure I-2 — escaped candidate overwritten by unsupported array range | **ADDRESSED** | `decide_range()` now preserves a prior literal `True` decision; direct and compound escaped scalar/output candidates remain literal and create no spill. |
| Closure I-3 — quoted-heredoc body continuation flattened globally | **Direct forms addressed; class NOT CLOSED** | Direct single/double/backslash-quoted delimiters and a continuation between the two `<` characters now preserve body bytes. Important I-3 shows `<<` followed by a continuation before `-` still loses the quoted-body rule. |

## Findings

### Critical

None.

### Important

#### I-1 — An fd-prefixed heredoc consumes command position and admits an arithmetic subscript

**Locations:** `plugins/workflow/bash_rendering.py:646-658,723-769,847-879,1041-1064,1275-1290`; missing matrix at `tests/plugins/workflow/test_phase3_bash_substitution.py:48-66,296-342,833-900`.

`begin_heredoc()` recognizes and jumps over the complete heredoc operator and
delimiter before the leading-redirection state at lines 1051-1064 can run. If
the operator has a numeric fd prefix, the scanner has already opened a
top-level word at that digit. At the next separator, `finish_top_level_word()`
therefore treats the raw `2<<EOF` span as the command name and sets
`top_level_command_position = False`. A following indexed assignment on the
same command line is then treated as ordinary argument text.

Benign production observation:

```text
template:   2<<EOF items[$USER_MESSAGE]=9
            body
            EOF
            printf '%s' "${items[2]}"
classifier: ((13, 26, None),)  # admitted unquoted
value:      1+1
/bin/bash:  exit 0, stdout "9"
```

Bash interpreted the admitted value as arithmetic index `2`. This is the same
fail-open grammar class as the prior indexed-assignment finding. It affects
scalar and output references and both inline and spill values because the
shared classification decision precedes resolution and size handling.

Treat a heredoc operator/delimiter as a leading redirection operand, clear a
numeric fd word, and preserve command position just as for other redirections.
Add admission, scheduler, and direct no-launch regressions for fd 0-9,
multiple leading redirections, `<<`/`<<-`, spaced/joined delimiters, and
inline/spill scalar/output references.

#### I-2 — Raw-word tracking ignores quote removal for wrappers, builtins, options, and assignment arguments

**Locations:** `plugins/workflow/bash_rendering.py:669-769,904-920`; coverage gap at `tests/plugins/workflow/test_phase3_bash_substitution.py:48-66,296-342,1237-1310`.

`finish_top_level_word()` compares the authored raw slice directly with
`command`, `builtin`, their option separators, and the assignment-builtin
names. The shell first removes quotes and escapes. The quoted assignment-range
logic has the same mismatch: it requires raw `=` or `+=` immediately after
the closing `]`, although quote removal can join the bracketed name to an
equals sign outside the quoted fragment.

Benign production observations all returned an admitted double-quoted span;
after replacing the reference with `1+1`, `/bin/bash` exited 0 and printed
`9` from `items[2]`:

```text
command "declare" -a "items[$USER_MESSAGE]=9"
command "--" declare -a "items[$USER_MESSAGE]=9"
"command" declare -a "items[$USER_MESSAGE]=9"
declare -a "items[$USER_MESSAGE]"=9
```

Thus the value is not preserved as approved simple-token data; it reaches
Bash arithmetic grammar. The current tests cover raw wrapper/builtin words,
raw `-p`/`--`, and an equals sign inside one quoted assignment argument, but
not their quote-removed equivalents or concatenated quoted fragments.

Track a bounded quote-removed shell word for command-position decisions while
retaining physical coordinates for reference replacement. Apply it to wrapper
names, supported option separators, assignment-builtin names, and the complete
assignment argument. Add single-, double-, backslash-, and concatenated-quote
variants through admission, scheduler, and direct inline/spill no-launch
paths, while retaining ordinary quoted argument text.

#### I-3 — A continuation between `<<` and `-` makes a valid quoted heredoc look unterminated

**Locations:** `plugins/workflow/bash_rendering.py:364-418,421-506,509-549`; coverage gap at `tests/plugins/workflow/test_phase3_bash_substitution.py:74-78,343-430,1001-1137`.

The physical heredoc prepass checks for `-` immediately after the two physical
`<` bytes, before its delimiter loop removes backslash-newline pairs. It has a
special path for a continuation between the two `<` characters, but not for a
continuation between `<<` and `-`. The prepass consequently treats `-EOF` as
delimiter text instead of recognizing logical operator `<<-` with quoted
delimiter `EOF`. It fails to mark the body as quote-protected, so the global
logicalizer removes the body's literal backslash-newline and the main lexer
later reports an unterminated heredoc.

Benign production observation:

```text
template:   cat <<\
            -'EOF' >/dev/null
            body\
            EOF
            printf '%s' "$USER_MESSAGE"
classifier: bash_reference_context_unsupported — unterminated here-document
value:      1+1
/bin/bash:  exit 0, stdout "1+1"
```

This is a valid quoted `<<-` heredoc followed by an approved double-quoted
simple-token reference, so the rejection is a compatibility regression in the
explicit continuation/heredoc contract.

Parse the complete redirection operator on the continuation-normalized stream
before delimiter quote removal, while preserving an exact physical boundary
map and physical quoted-body bytes. Cover continuations at every boundary in
`<<-` (between either `<`, before `-`, after `-`, and inside an unquoted
delimiter), split delimiter tokens, multiple queued heredocs, tab stripping,
and references after the final body through admission, scheduler, and direct
inline/spill execution.

#### I-4 — Comment logicalization hides a real following heredoc and admits its body reference

**Locations:** `plugins/workflow/bash_rendering.py:421-506,509-549`; shared consumers at `plugins/workflow/bash_rendering.py:1322-1368,1552-1568`, `plugins/workflow/schema.py:1105`, `plugins/workflow/scheduler.py:1530`, and `plugins/workflow/resources.py:801,961`.

The physical heredoc prepass ends a `#` comment at its physical newline and can
therefore locate a heredoc beginning on the next line. The subsequent global
logicalizer, however, removes an odd-parity backslash-newline outside a quoted
heredoc body without preserving physical comment state. That joins the next
line into the classifier's comment and prevents the authoritative lexer from
queueing the real heredoc.

Confirmed benign example:

```sh
# ignored \
<<'EOF'
printf '%s' "$USER_MESSAGE"
EOF
```

The production classifier admits the body reference as a double-quoted simple
token. The same result was confirmed for unquoted, single-quoted,
double-quoted, and backslash-quoted delimiters. Bash instead ends the comment
at the first physical newline, treats the second line as a real heredoc
operator, and treats the reference line as body text. This is an
under-rejection: quoted bodies may be rewritten despite disabling expansion,
and unquoted bodies are shell expansion surfaces. Scalar/output and
inline/spill paths all share the wrong decision.

Make continuation removal phase-aware for physical comments as well as quoted
heredoc bodies, or replace the prepass/logicalizer split with one bounded
stateful stream. Add all delimiter quote forms through admission, scheduler,
and direct scalar/output inline/spill paths; rejection must occur before
resolution, spill/output creation, or launch.

### Minor

None.

## Other quality, security, compatibility, and privacy assessment

- The shared classifier gives consistent admission, scheduler, and rendering
  outcomes; the three findings are not bypasses between duplicated parsers.
- Escaped candidates retain precedence over unsupported array ranges, and the
  added direct/compound escaped cases remain literal without dependencies or
  spills.
- Direct quoted heredocs, the tested joined `<` + `<` operator, unquoted active
  body continuations, split unquoted delimiter, the existing continued-comment
  fixture, and physical coordinate mapping are materially improved. I-4 shows
  that physical comment state is still lost when a real heredoc follows.
- The physical/logical passes remain bounded forward scans in the exercised
  cases. The new quoted-heredoc linear-read regression passed. No separate
  superlinear path was established in this review.
- UTF-8 inline/per-value/aggregate bounds, exact quote replacement table,
  deduplication, immutable snapshot publication, descriptor identity, parent
  closure, native-Windows large-value gate, and exact `argv[-1]` evidence have
  no adverse Fix Round 6 delta.
- Evidence remains limited to sizes, counts, descriptor numbers, and digests;
  no command text, value, spill path, descriptor identity, session data, or
  provider data is projected.
- Legacy, admitted Archon v1/v2, and Phase 4 Loop execution remain outside the
  secure-v3 branch. No model tool, prompt prefix/history, API endpoint,
  environment setting, Task 12 session behavior, or release branch changed.
- Native Windows Job Object and descriptor behavior could not be executed on
  this Darwin host; Fix Round 6 does not modify that seam.

## Fresh test assessment

Only the repository wrapper was used, with retries disabled:

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

Fresh result: **11 files, 765 tests passed, 0 failed, 1 platform skip, 15.9
seconds, 14 workers**. Retries were disabled, so no file retry or flaky pass
was available.

The suite is strong for the enumerated Fix Round 6 forms, exact content/bounds,
descriptor faults, legacy/version gating, shared authority, and linear-read
canaries. Its green result does not cover the fd-heredoc, quote-removed word,
joined-`<<-`, or physical-comment-before-heredoc cases above. The
classifier-versus-`/bin/bash` observations were benign diagnostic commands,
not direct test-framework invocations.

## Closure recommendation

Do not close Task 11 and do not advance to Task 12 on this implementation
tree. Keep N-1 and the earlier descriptor/lifecycle/privacy corrections
closed. Repair I-1 through I-4 in a bounded fix round, add scalar/output
admission, scheduler, and direct inline/spill regressions, rerun the exact
eleven-file acceptance set through `scripts/run_tests.sh` with retries
disabled, and obtain another independent closure review against one exact
implementation tree.
