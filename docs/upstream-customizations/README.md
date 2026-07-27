# Upstream customization ledger

This directory records minimal changes to upstream-owned Hermes surfaces that
support edge capabilities. Each manifest identifies exact files and symbols,
the independent commit boundary, regression tests, merge guidance, and the
upstream commit against which the behavior was last verified.

`owned_symbols` is reserved for bounded, exact identifiers that the checker
can locate in a declared file at `HEAD`; behavioral prose belongs in the
optional bounded `owned_invariants` list. New or migrated entries may select
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

Every report row marked `decision_required` requires an explicit `preserve`,
`adapt`, or `remove-as-upstream-equivalent` decision. This includes
`owned_symbol` and `possible_upstream_equivalent` results plus `same_file`
results governed by `any_owned_file`; a prior acknowledgement never substitutes
for the current decision. Textual merge cleanliness is never proof that the
recorded behavior survived. Baselines advance only through the controlled
upstream-merge workflow after the named tests pass.
