# Upstream customization ledger

This directory records minimal changes to upstream-owned Hermes surfaces that
support edge capabilities. Each manifest identifies exact files and symbols,
the independent commit boundary, regression tests, merge guidance, and the
upstream commit against which the behavior was last verified.

`owned_symbols` is reserved for bounded, exact identifiers that the checker
can locate in a declared file at the committed `HEAD`; dirty checkout bytes and
comments never satisfy ownership. Python identifiers are resolved through the
AST with qualified owner identity preserved; owned definition spans include all
stacked decorator lines. JSON, YAML, and TOML are syntax-validated with their
native safe parsers and search only recursive string keys and values; malformed
documents fail validation. Traversal is alias-cycle safe, decoded escape values
map back to their source spans, and block/multiline string spans stop before the
following syntax. PowerShell, POSIX shell, and CSS use bounded lexical scanners.
TypeScript/JavaScript and Markdown use the pinned parser helper: their broad
symbol meaning includes semantic syntax while excluding comments. Non-comment
HTML, JS/TS literals, and Markdown literals, code, and prose remain searchable.
Parser requests use only exact committed Git revision blob bytes, never dirty
worktree bytes, and encode those bytes as canonical base64. Returned symbol
spans and Git-changed ranges are half-open UTF-8 byte offsets, so overlap
checks are byte-exact. A repository path is absent at a revision only when an
exact Git tree lookup succeeds with zero entries; tree lookup and object-read
failures are terminal rather than silently treated as empty content. The root
`package.json` and lockfile must provide the exactly attested TypeScript 6.0.3,
unified 11.0.5, remark-parse 11.0.0, and micromark 4.0.2 dependencies. Each
parser request is at most 4 MiB, each sequential batch is at most 16 MiB,
parser output is at most 16 MiB, and parser execution is limited to 60 seconds.
Parser transport, validation, and parse errors fail closed with no heuristic or
lexical fallback. A report replaces prior evidence atomically only after
complete successful parser classification. The separate character-token Git
diff is likewise fail-closed and independently limited to 64 MiB of output and
60 seconds of execution. Behavioral prose belongs in the optional bounded
`owned_invariants` list. New or migrated entries may select
`overlap_policy: owned_symbol` (the compatibility default) or
`overlap_policy: any_owned_file`. The latter makes every change to a declared
file decision-required even when no exact symbol span changed, and is required
for security, admission, exact-byte authority, Desktop capability, schema, and
release-gate seams.

Validate feature-diff coverage:

```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --diff RANGE
```

Classify a new upstream range and write review evidence:

```bash
python3 scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --upstream-diff RANGE --report overlap-report.json
```

For `--upstream-diff`, the checker first intersects upstream-changed paths
with ledger-declared files. Parser requests contain only that intersection and
the symbols owned by entries that declare each path. Compatibility outside
those paths is verified by post-merge invariants.

Every report row marked `decision_required` requires an explicit `preserve`,
`adapt`, or `remove-as-upstream-equivalent` decision. This includes
`owned_symbol` results plus `same_file` results governed by `any_owned_file`; a
prior acknowledgement never substitutes for the current decision. Textual
merge cleanliness is never proof that the recorded behavior survived. Baselines
advance only through the controlled upstream-merge workflow after the named
tests pass.

Two-dot ranges are literal Git revision sets: `A..B` examines commits reachable
from `B` but not `A`, even when the tips diverge. Triple-dot ranges compare the
merge base of `A` and `B` to `B`. Symbol overlap always reads the resolved left
and right commit blobs, never the current checkout.

The checker, merge gate, and invariant runner are developer, CI,
upstream-merge, and release verification tools. They are not loaded by the
normal Hermes agent runtime and do not validate or execute user workflow YAML.
Executable ledger entries are trusted, repository-controlled tests; programs
that deliberately daemonize or escape containment are not valid ledger tests.

Executable ledger invariants run in fresh process groups with at most two files
in flight per runtime. Each attempt has a bounded timeout and bounded stdout and
stderr capture. Only an ordinary test failure is retried once; timeouts,
signals, infrastructure failures, and cancellation are terminal. Evidence
records the exact attempt sequence, output truncation, signal number when
applicable, and whether a failed first attempt passed on retry. On POSIX,
teardown owns and signals the original process group, observes descendants
while their ancestry is visible, records their PID/create-time identities, and
revalidates those identities before signaling descendants that later leave the
group. A small per-attempt supervisor remains the unreaped group leader until
cleanup, so the original numeric process group cannot be reused and a
grandchild that stays in that group after an intermediate exits remains
contained. Cleanup uncertainty detected inside this boundary is a terminal
infrastructure error. Portable POSIX, including Darwin, cannot guarantee
cleanup of a deliberately daemonizing child that creates a new session and
reparents before it can be observed; that hostile pattern is outside the
trusted ledger contract and must not be ledgered. Windows attempts are assigned
to a kill-on-close Job Object before a blocked bootstrap may launch the test
command, and cleanup terminates that kernel-owned job tree. Reused unrelated
process identities are never intentionally targeted.

`--base-ref` execution also seals the Node dependency view before any invariant
starts. The live checkout may supply root and Desktop `node_modules` only as
links to external dependency roots. The runner audits at most 250,000 entries
within 60 seconds, rejects broken or escaping links, and materializes only the
directory ancestors needed to control symlinks. Audited third-party subtrees
remain linked inside those external roots; workspace and project links are
remapped to their corresponding committed paths in the detached tested
checkout. Vite's explicit `.vite` and `.vite-temp` cache directories are empty
writable directories inside each disposable view, so result and temporary
caching cannot mutate either external dependency root. Root identities,
non-cache entry sets and identities, link targets, and the constructed view
are revalidated after every invariant group, including the final group. A
retryable failed attempt is also revalidated before its retry, so dependency
drift is terminal rather than a flaky pass. Setup failure is terminal, and the
dependency view disappears with the detached worktree on every exit path.
