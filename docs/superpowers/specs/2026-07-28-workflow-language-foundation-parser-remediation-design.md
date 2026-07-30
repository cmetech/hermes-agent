# Workflow Language Foundation Parser Remediation Design

**Date:** 2026-07-28

**Status:** Approved

**Audience:** Engineers implementing or reviewing Task 9 of the workflow
language foundation remediation

**Post-read action:** Implement and review parser-backed non-Python ownership
extraction without weakening the upstream customization ledger or the verified
Task 9 runner and evidence contracts.

## 1. Context

The upstream customization ledger protects Hermes behavior that can be lost
when upstream changes are merged into this fork. For workflow users, that
behavior includes compatibility validation, trusted execution, secure resume,
legacy behavior, and accurate Desktop run controls. The ledger names the files,
symbols, tests, merge decisions, and removal conditions that preserve those
guarantees.

The Task 9 checker compares an upstream change with that ledger. If upstream
touches a protected symbol or a file governed by `any_owned_file`, the merge
must receive an explicit preserve, adapt, or remove-as-upstream-equivalent
decision. This checker is release infrastructure; it is not on the runtime path
for a user's workflow. The checker, merge gate, and invariant runner are used
only for developer, CI, upstream-merge, and release verification. They do not
validate or execute user workflow YAML, and executable ledger entries are
trusted repository-controlled tests.

Task 9 successfully hardened Git revision authority, evidence schema truth,
bounded invariant execution, retries, process containment, detached committed
execution, dependency overlays, cache isolation, and cleanup. Its JS/TS and
Markdown ownership extraction crossed a different boundary: handwritten
lexical scanners began recreating the languages' grammars. Eleven fix rounds
continued to reveal valid syntax that those scanners interpreted incorrectly.
The latest known failure mistakes TypeScript `as of` and `satisfies of` type
contexts for a `for-of` delimiter despite the focused suite being green.

## 2. Decision

Replace handwritten JavaScript, TypeScript, JSX, TSX, and Markdown ownership
extraction with the pinned language parsers already used by the repository:

- TypeScript compiler API 6.0.3;
- Unified 11.0.5;
- Remark Parse 11.0.0; and
- Micromark 4.0.2.

Python remains the sole authority for Git revisions, committed blob selection,
manifest policy, changed-range calculation, overlap classification, report
construction, and merge decisions. A new Node helper is a pure parser service.
It receives source bytes and requested symbols from Python and returns verified
source spans. It never discovers files, reads checkout source, resolves Git
revisions, interprets the ledger, or makes merge-policy decisions.

An exact Git tree lookup that succeeds with zero entries is the only path-
absence result. Git tree or object failures remain terminal; they are never
converted to empty parser input.

There is no fallback to the handwritten JS/TS or Markdown scanners. If the
pinned parser boundary is unavailable or cannot prove a result, the checker
fails closed.

## 3. Goals and non-goals

### Goals

- Preserve the ledger's broad non-Python symbol meaning while excluding real
  comments.
- Parse exact committed bytes from each requested Git revision, never current
  checkout source.
- Return deterministic UTF-8 byte spans that Python can compare with exact
  changed ranges.
- Batch and deduplicate parser work without introducing persistent authority.
- Produce bounded, sanitized, fail-closed diagnostics.
- Retain the verified Task 9 runner, evidence, containment, and cleanup work.
- Keep the runner scoped to trusted repository verification tests and describe
  its platform containment boundary precisely.
- Remove the JS/TS and Markdown grammar state from the Python checker.

### Non-goals

- Do not parse or execute user workflow YAML.
- Do not change workflow language profiles, compatibility rules, runtime
  semantics, Desktop behavior, or prompt/tool surfaces.
- Do not redesign Python, JSON, YAML, TOML, shell, PowerShell, or CSS ownership
  extraction.
- Do not make `owned_invariants` searchable authority.
- Do not start Task 10 release evidence before redesigned Task 9 passes a fresh
  task review.
- Do not introduce a persistent parser cache, background service, or new core
  model tool.
- Do not ledger deliberately daemonizing programs that create a new POSIX
  session and reparent before the runner can observe them.

## 4. Components and responsibilities

### Python checker

The existing customization checker continues to:

1. resolve requested revisions to commits;
2. validate repository-relative paths against the requested Git tree;
3. read old and new Git blob bytes in batches;
4. collect and deduplicate the strict symbols relevant to each blob;
5. calculate exact changed byte ranges;
6. call the parser adapter for JS/TS and Markdown blobs;
7. validate every returned span;
8. classify `owned_symbol`, `same_file`,
   `possible_upstream_equivalent`, or `none`; and
9. derive `decision_required` from the existing overlap policy.

Python-side parsing remains in place for Python and structured data. Existing
language-specific handling outside JS/TS and Markdown remains outside this
change.

Exact non-Python changed-byte ranges come from Git's character-token porcelain
word diff, not Python's quadratic `SequenceMatcher(..., autojunk=False)` path.
Python invokes Git directly with exact resolved revisions and paths, parses the
old/new byte cursors itself, and fails closed if the diff subprocess exceeds 60
seconds or 64 MiB of combined output.

### Python parser adapter

A small adapter inside the checker owns batching, subprocess lifecycle,
protocol validation, timeout and output bounds, error translation, and
invocation-local caching. It exposes a conceptual interface equivalent to:

```python
parse_non_python_symbol_spans(requests) -> mapping[request_id, symbol, spans]
```

The adapter groups identical work by committed blob OID, language, and sorted
symbol set. Old and new sides that point to the same blob are parsed once.

### Node parser helper

A checked-in helper under `scripts/` accepts newline-delimited JSON on standard
input and writes newline-delimited JSON on standard output. Each request
contains only:

- a request identifier;
- a repository-relative display path and explicit language kind;
- base64-encoded committed blob bytes; and
- a bounded list of strict symbols.

The helper imports the pinned parsers, decodes UTF-8 strictly, parses the
provided source, finds requested symbol occurrences in parser-recognized
semantic regions, converts parser offsets to UTF-8 byte offsets, and returns
sorted unique half-open spans. It performs no filesystem or network discovery.

## 5. Broad symbol contract

The new implementation preserves the checker's current broad intent: a strict
symbol may be an identifier or an exact bounded occurrence inside meaningful
literal or text content. It is not limited to declarations.

### JavaScript and TypeScript

The helper chooses `ScriptKind` from the explicit language kind and parses with
`createSourceFile`. Searchable syntax includes:

- identifiers and private identifiers;
- declaration and property names;
- property-access and qualified-name expressions;
- string and regular-expression literals;
- template heads, middles, tails, and substitutions;
- JSX tag, attribute, and text content; and
- other parser-recognized literal nodes containing an exact requested symbol.

Comments and trivia never satisfy ownership. For an unqualified symbol, exact
matching retains the existing identifier boundary of ASCII letters, digits,
underscore, and dollar sign. For `Owner.member`, a hit may come from the
corresponding owner/member syntax relationship, an exact dotted expression, or
the exact value inside another searchable semantic token. This preserves
current routes, translation keys, fixtures, constants, types, and member
contracts.

When a qualified relationship is syntactically present but not contiguous in
source, such as `Owner /* trivia */ . member` or a scoped declaration, the
helper records the individual identifier-token spans for `Owner.member`.
Whitespace and comment bytes between those components are never included, so
trivia-only edits cannot become owned-symbol overlaps.

Any TypeScript parse diagnostic is terminal. The checker must not derive
ownership from a recovered partial tree that the parser reports as malformed.

### Markdown

Unified and Remark Parse produce a CommonMark mdast backed by Micromark.
Searchable content includes:

- ordinary prose and headings;
- emphasis and other inline text;
- link and image labels, destinations, titles, and definitions;
- inline code and fenced or indented code blocks;
- list and blockquote contents regardless of valid CommonMark indentation;
- autolinks and character references; and
- non-comment inline or block HTML.

Parser-classified HTML comments are excluded. Fence markers, container
indentation, tabs, lazy continuation, and list termination are parser concerns,
not Python scanner state. Markdown is otherwise searched broadly so documented
contracts and report artifacts retain their existing ownership meaning.

### Span rules

Every span is `[start_byte, end_byte)` against the exact original UTF-8 blob.
The helper converts JavaScript UTF-16 offsets before returning them. Python
rejects negative, reversed, out-of-bounds, non-codepoint-boundary, duplicate,
or unsorted spans. Spans for different symbols may overlap legitimately.

## 6. Protocol, batching, and bounds

The helper emits a protocol/version record containing:

- helper protocol version; and
- loaded versions of TypeScript, Unified, Remark Parse, and Micromark.

Python accepts only the supported protocol and the exact versions declared by
the root package. Parser requests and results carry request identifiers so
ordering cannot associate a response with the wrong blob.

The production bounds are:

- 4 MiB maximum decoded source per blob, preserving the current checker cap;
- 16 MiB maximum decoded source across one helper batch;
- 16 MiB maximum helper output per batch;
- 60-second wall-clock limit per batch; and
- sequential parsing inside one helper process.

The separate Git character-diff subprocess has a 60-second wall-clock limit
and a 64 MiB combined-output limit. This is not helper protocol output and does
not weaken the helper's 16 MiB output bound.

The current ledger contains 84 unique JS/TS/Markdown files totaling roughly
1.9 MiB, with the largest below 400 KiB. These limits leave substantial growth
room while bounding memory, output, and execution. Larger future ledgers are
split into additional deterministic batches rather than granted unbounded
input.

Caching exists only for the lifetime of one checker invocation and is keyed by
blob OID, language, sorted symbols, helper protocol, and parser versions. No
on-disk cache may become release authority.

## 7. Failure behavior

The checker terminates without a trusted overlap report when:

- Node is missing or cannot start;
- a parser package is missing;
- the loaded parser versions differ from the pinned versions;
- source bytes are invalid UTF-8;
- JS/TS has parse diagnostics;
- a batch exceeds its input, output, or time bound;
- the helper exits nonzero or by signal;
- the protocol or response shape is invalid;
- a response is missing, duplicated, or assigned to the wrong request; or
- a returned span violates the span rules.

Diagnostics include only a stable category, repository-relative path, and a
short sanitized detail. They do not contain source text, secrets, arbitrary
parser output, or absolute paths. Report files are written atomically only
after every request succeeds. A previous report is never treated as current
evidence after a parser failure.

There is no keyword, delimiter, brace-depth, blank-line, or other heuristic
fallback.

## 8. Toolchain ownership and detached gates

The root package directly owns exact development dependencies for all four
parser packages. Relying on Desktop ownership or transitive installation is
forbidden even if module resolution happens to succeed locally. The lockfile
records installation integrity, and the helper handshake proves that the
loaded versions match the declared toolchain.

The base merge gate currently invokes the checker before making the shared
root dependency tree available in a detached worktree. It must provision the
validated root `node_modules` link before the checker runs. The gate does not
download or auto-install dependencies. Missing dependencies produce the
bounded fail-closed diagnostic.

The invariant runner's sealed dependency overlay, revalidation, Vite cache
isolation, retry semantics, and cleanup remain unchanged. Windows attempts use
a kernel-enforced kill-on-close Job Object. POSIX attempts own a fresh process
group and additionally track descendants observed through verified
PID/create-time identities before they reparent. This covers supported
success, failure, retry, timeout, signal, and cancellation cleanup, with
detected uncertainty failing closed. Portable POSIX, including Darwin, cannot
guarantee cleanup of a hostile child that creates a new session and reparents
before observation; that pattern is outside the trusted ledger-test contract.
The parser helper is part of the checker boundary and does not read through the
invariant runner's detached source tree.

## 9. Migration and branch strategy

The existing `fix/workflow-language-foundation-remediation` branch and its
worktree remain frozen at `b6aaa21f3c6a1e29632ad559d157e82327fd0a95` as the
complete Task 9 evidence record.

Parser remediation proceeds on the sibling
`fix/workflow-language-foundation-parser-remediation` branch created from that
exact commit. Starting from the verified tip preserves the runner and evidence
work without attempting to reconstruct interleaved changes from the Task 8
boundary. The final Task 9 review nevertheless receives the complete range
from the Task 8 boundary `5f5596d21` through the parser-remediation tip, so it
reviews the resulting Task 9 contract rather than only the replacement commit.

The parser redesign explicitly expands Task 9's authorized tracked scope to:

- the root package manifest and lockfile;
- the new parser helper;
- the customization checker and base merge gate;
- parser/checker behavioral tests;
- the customization ledger and its README; and
- only those existing Task 9 files whose contracts genuinely require an
  accompanying update.

No production file is edited merely to preserve the previous scanner shape.
The handwritten JS/TS and Markdown scanners and their now-dead support code are
deleted after parser-backed behavior tests pass.

## 10. Test strategy

Every production change starts with a failing behavioral test.

### Helper protocol tests

Execute the real Node helper through Python subprocess tests. Cover handshake,
all supported language kinds, batching, UTF-8 byte offsets, duplicate blob
deduplication, deterministic ordering, malformed requests, malformed results,
missing results, process exit, timeout, and input/output bounds.

### Grammar behavior tests

Route the adversarial cases accumulated through Task 9 rounds 1-11 through the
real parser boundary. Preserve positive and negative controls for:

- regex versus division contexts;
- `for`, `for-in`, `for-of`, and `for-await-of`;
- nested functions, arrows, classes, destructuring, and control headers;
- TypeScript non-null, `as of`, and `satisfies of` contexts;
- strings, templates, JSX, properties, labels, and restricted statements;
- CommonMark fences in lists and blockquotes;
- tabs, multi-digit list markers, blank continuation lines, and container exit;
- inline and block HTML comments; and
- searchable inline code, fenced code, links, prose, and non-comment HTML.

Fixtures that claim valid JS/TS are accepted by the pinned TypeScript parser.
Tests assert ownership relationships and returned spans, not implementation
keywords or source-code snapshots.

### Git authority tests

Synthetic repositories prove that:

- both old and new parser inputs are exact committed blobs;
- dirty checkout bytes cannot satisfy or alter ownership;
- a historical requested revision controls path and symbol authority;
- only an exact successful zero-entry tree lookup means absent, while tree and
  present-object failures propagate;
- identical old/new OIDs are parsed once;
- non-ASCII content maps to correct changed byte ranges; and
- a parser failure prevents report replacement and merge decisions.

### Acceptance evidence

Before Task 9 can be marked complete:

1. all strict live-ledger symbols validate through the new boundary;
2. the exact three-file no-retry meta-gate passes;
3. strict current and historical source probes pass;
4. the committed-byte live ledger passes without terminal or hidden flaky
   results;
5. the parser runs successfully in the detached base merge-gate worktree;
6. no parser, runner, supervisor, watchdog, or in-contract trusted-test
   descendant remains;
7. the tracked worktree is clean after tests; and
8. a fresh Task 9 reviewer reports both spec compliance and task quality
   approved.

Task 10 remains blocked until all eight conditions hold.

## 11. Alternatives rejected

### Narrow all non-Python entries to `any_owned_file`

This would be sound but materially weaker. It would make every change to a
declared JS/TS/Markdown file decision-required, lose symbol-level precision,
and reduce possible-upstream-equivalent discovery. The current ledger has 40
entries spanning 84 unique JS/TS/Markdown files, so the review burden would be
substantial. The narrower contract remains an emergency fallback only with
explicit human approval.

### Parse JS/TS but make Markdown file-level only

This removes the most complex lexical-goal bugs but weakens all Markdown
ownership entries and leaves two different non-Python contracts to explain.
The already-installed CommonMark parser makes that compromise unnecessary.

### Continue patching the handwritten scanners

Eleven rounds demonstrate that this does not converge. Each patch encodes
another small part of a real language grammar while leaving the next valid
construct unmodeled. Continuing would expand high-risk release infrastructure
without reaching a defensible correctness boundary.
