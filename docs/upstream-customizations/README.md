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
documents fail validation. PowerShell, POSIX shell, TypeScript/JavaScript, CSS,
and Markdown use separate bounded lexical scanners so their real comments do
not satisfy ownership while strings, template text and expressions, parameter
trims, here-documents, and code spans remain searchable. Behavioral prose
belongs in the optional bounded `owned_invariants` list. New or migrated entries
may select `overlap_policy: owned_symbol` (the compatibility default) or
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

Every report row marked `decision_required` requires an explicit `preserve`,
`adapt`, or `remove-as-upstream-equivalent` decision. This includes
`owned_symbol` and `possible_upstream_equivalent` results plus `same_file`
results governed by `any_owned_file`; a prior acknowledgement never substitutes
for the current decision. Textual merge cleanliness is never proof that the
recorded behavior survived. Baselines advance only through the controlled
upstream-merge workflow after the named tests pass.

Two-dot ranges are literal Git revision sets: `A..B` examines commits reachable
from `B` but not `A`, even when the tips diverge. Triple-dot ranges compare the
merge base of `A` and `B` to `B`. Symbol overlap always reads the resolved left
and right commit blobs, never the current checkout.

Executable ledger invariants run in fresh process groups with at most two files
in flight per runtime. Each attempt has a bounded timeout and bounded stdout and
stderr capture. Only an ordinary test failure is retried once; timeouts,
signals, infrastructure failures, and cancellation are terminal. Evidence
records the exact attempt sequence, output truncation, signal number when
applicable, and whether a failed first attempt passed on retry. Teardown tracks
the original process-group member identities and escalates after the grace
period even when the leader exited, so signal-resistant descendants cannot
escape and a reused unrelated process group is never targeted.
