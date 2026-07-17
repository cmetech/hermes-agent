# Upstream customization ledger

This directory records minimal changes to upstream-owned Hermes surfaces that
support edge capabilities. Each manifest identifies exact files and symbols,
the independent commit boundary, regression tests, merge guidance, and the
upstream commit against which the behavior was last verified.

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

An `owned_symbol` or `possible_upstream_equivalent` result requires an explicit
review acknowledgement in the report. Textual merge cleanliness is never proof
that the recorded behavior survived. Baselines advance only through the
controlled upstream-merge workflow after the named tests pass.
