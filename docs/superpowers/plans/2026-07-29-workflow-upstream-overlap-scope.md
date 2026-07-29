# Workflow Upstream Overlap Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict upstream overlap classification to files both upstream and the customization ledger changed, then produce clean Task 10 rehearsal evidence without increasing parser limits.

**Architecture:** `classify_upstream_overlaps` resolves the upstream range once, intersects its changed paths with the ledger's declared file inventory, and sends only parser-backed intersection paths to `_collect_overlap_parser_requests`. Parser symbols are unioned per path from entries that declare that path; entry classification no longer searches unrelated upstream files or emits `possible_upstream_equivalent`. The evidence schema and rehearsal tests expose only `none`, `same_file`, and `owned_symbol`, while post-merge invariants retain responsibility for behavioral compatibility outside direct file overlap.

**Tech Stack:** Python 3.11, Git object plumbing, pytest, YAML, Draft 2020-12 JSON Schema, Bash, the existing bounded Node parser helper.

## Global Constraints

- Work only on `test/workflow-language-foundation-task-10-release-evidence` in `.worktrees/workflow-language-foundation-task-10-release-evidence`.
- Preserve real `main`, `base`, `otto`, and `loop24` refs; do not push, publish, open a PR, or build a release.
- Analyze only `upstream_changed_paths & ledger_owned_paths`.
- For each candidate parser path, request only symbols belonging to entries that declare that path.
- New classifications are exactly `none`, `same_file`, and `owned_symbol`; never emit `possible_upstream_equivalent`.
- Retain `remove-as-upstream-equivalent` as a valid explicit human decision for an intersecting-file overlap.
- Preserve exact committed Git bytes, half-open UTF-8 spans, atomic reports, and all parser fail-closed behavior.
- Do not raise the 4 MiB blob, 16 MiB batch input, 16 MiB parser output, 60-second parser, 64 MiB Git-diff output, or 60-second Git-diff limits.
- Preserve `any_owned_file` and `owned_symbol` overlap-policy behavior within intersecting files.
- Preserve brand reconciliation, invariant execution, evidence validation, ref immutability, and cleanup checks.
- Follow strict RED-GREEN TDD. Every production/schema behavior change requires an observed failing test first.
- Do not retry a failed release gate or rehearsal. Record and stop on any terminal failure or required overlap decision.

---

### Task 1: Bound Classifier and Parser Work to Owned-File Intersections

**Files:**
- Modify: `scripts/check_upstream_customizations.py:2278-2490`
- Modify: `tests/scripts/test_check_upstream_customizations.py:1217-1510`

**Interfaces:**
- Consumes: ledger entries with `files` and `owned_symbols`, resolved Git endpoints, and `_parser_language(path)`.
- Produces: `_collect_overlap_parser_requests(entries, repo, left, right, changed_paths)` where `changed_paths` is already the ledger-owned intersection and each request contains only that path's owning-entry symbols.
- Produces: `classify_upstream_overlaps(...)` results whose `classification` is exactly `none`, `same_file`, or `owned_symbol`.
- Preserves: `classify_upstream_overlap(entry, repo, diff_range)` as the one-entry compatibility wrapper.

- [ ] **Step 1: Replace the unrelated-equivalence expectation with the approved boundary**

Rename `test_overlap_classification_distinguishes_file_symbol_and_equivalent` to `test_overlap_classification_distinguishes_file_symbol_and_unrelated_path`. Keep its owned-symbol and same-file assertions, then make the final assertion exact:

```python
third = _git(repo, "rev-parse", "HEAD")
(repo / "replacement.py").write_text("class Owned:\n    pass\n")
_git(repo, "add", ".")
_git(repo, "commit", "-m", "unrelated matching public name")
result = classify_upstream_overlap(entry, repo, f"{third}..HEAD")
assert result["classification"] == "none"
assert result["decision_required"] is False
```

- [ ] **Step 2: Make the shared parser-pass test assert path-local requests**

Retain all three changed files in `test_classify_all_entries_uses_one_shared_parser_resolution_pass`, including unrelated `equivalent.js`. Replace the request expectations with:

```python
assert len(calls) == 1
assert [(request.path, request.symbols) for request in calls[0]] == [
    ("first.js", ("FirstToken",)),
    ("first.js", ("FirstToken",)),
    ("second.md", ("SecondToken",)),
    ("second.md", ("SecondToken",)),
]
assert [request.request_id for request in calls[0]] == [
    "request-00000000",
    "request-00000001",
    "request-00000002",
    "request-00000003",
]
assert [item["classification"] for item in overlaps] == [
    "owned_symbol",
    "same_file",
]
```

The duplicate path rows are the left and right endpoint requests in deterministic path/revision order.

- [ ] **Step 3: Add a scale-shaped request-boundary regression**

Add `test_overlap_parser_requests_ignore_many_unrelated_upstream_files`. Build a repository with declared `owned.ts` containing `OwnedToken` and 64 unrelated `unrelated-N.ts` files containing the same token. Commit the baseline, change all 65 files, monkeypatch `_run_parser_batch` using the existing real-byte recording pattern, and assert:

```python
assert result[0]["classification"] == "owned_symbol"
assert len(calls) == 1
assert [(request.path, request.symbols) for request in calls[0]] == [
    ("owned.ts", ("OwnedToken",)),
    ("owned.ts", ("OwnedToken",)),
]
assert not any(request.path.startswith("unrelated-") for request in calls[0])
```

Use 64 files rather than generating enough output to hit the safety cap; this test asserts the selection invariant directly and remains fast.

- [ ] **Step 4: Add an upstream-created custom-path control**

Add `test_overlap_checks_upstream_addition_at_declared_custom_path`. Create an entry dictionary before `custom.ts` exists, capture `left`, add and commit `custom.ts` containing `const OwnedToken = true;`, and assert:

```python
result = classify_upstream_overlap(entry, repo, f"{left}..HEAD")
assert result["classification"] == "owned_symbol"
assert result["decision_required"] is True
```

- [ ] **Step 5: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q \
  -k 'file_symbol_and_unrelated_path or shared_parser_resolution_pass or ignore_many_unrelated or upstream_addition_at_declared'
```

Expected: the unrelated-path test reports `possible_upstream_equivalent`; the shared-pass test sees six requests and global symbol unions; the scale test sees unrelated requests. The addition control may already pass and must remain green.

- [ ] **Step 6: Scope parser requests per candidate path**

Replace the global symbol union in `_collect_overlap_parser_requests` with:

```python
symbols_by_path: dict[str, set[str]] = {}
for entry in entries:
    entry_symbols = _strict_owned_symbols(entry)
    for path in sorted(set(entry["files"]) & changed_paths):
        if _parser_language(path) is not None:
            symbols_by_path.setdefault(path, set()).update(entry_symbols)
```

Iterate `for path in sorted(symbols_by_path)`, preserve left/right request order, and set `symbols=tuple(sorted(symbols_by_path[path]))`. Resolve `language = _parser_language(path)` and assert it is not `None` before constructing each request.

- [ ] **Step 7: Restrict classification to the ledger intersection**

In `classify_upstream_overlaps`, compute:

```python
changed = {path for _status, paths in changes for path in paths}
ledger_owned_paths = {
    path
    for entry in entries
    for path in entry["files"]
}
candidate_paths = changed & ledger_owned_paths
```

Pass `candidate_paths` to `_collect_overlap_parser_requests` and build parser changed ranges only for `candidate_paths`. Keep `owned_symbol` and `same_file`; replace the unrelated-path branch with:

```python
else:
    classification = "none"
    rationale = "no ledger-owned file overlap detected"
```

Set decision authority only from remaining classes:

```python
"decision_required": bool(
    classification == "owned_symbol"
    or (
        classification == "same_file"
        and entry.get("overlap_policy", "owned_symbol") == "any_owned_file"
    )
),
```

- [ ] **Step 8: Run focused and complete checker tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q \
  -k 'file_symbol_and_unrelated_path or shared_parser_resolution_pass or ignore_many_unrelated or upstream_addition_at_declared'
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q
```

Expected: focused controls and the complete checker file pass with pristine output.

- [ ] **Step 9: Run static verification and commit**

Run:

```bash
.venv/bin/ruff check scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
.venv/bin/python -m py_compile scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
git diff --check
git status --short
```

Expected: all checks exit 0; tracked changes are limited to the checker and its test file. Commit:

```bash
git add scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
git commit -m "fix(workflow): bound overlap checks to owned files"
```

---

### Task 2: Align Evidence, Documentation, and Rehearsal Contracts

**Files:**
- Modify: `docs/upstream-customizations/merge-evidence.schema.json:280-345`
- Modify: `docs/upstream-customizations/README.md:45-70`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml:4080-4205`
- Modify: `tests/scripts/test_workflow_upstream_merge.py:40-70,510-575,2680-2720`

**Interfaces:**
- Consumes: Task 1 overlap classes `none`, `same_file`, and `owned_symbol`.
- Produces: merge-evidence schema and rehearsal behavior that reject `possible_upstream_equivalent` while retaining the decision string `remove-as-upstream-equivalent`.
- Produces: documentation and ledger invariants that assign outside-file compatibility to post-merge tests rather than repository-wide matching-name inference.

- [ ] **Step 1: Write failing synthetic-classification and rehearsal tests**

In `test_synthetic_overlap_classes_cover_continue_and_stop_cases`, change the final assertion for `replacement.py` to:

```python
assert classify_upstream_overlap(
    entry, repo, f"{equivalent}..HEAD"
)["classification"] == "none"
```

Remove `("upstream-equivalent", False)` from `test_rehearsal_requires_explicit_decision_for_decision_required_overlap`. Add:

```python
def test_rehearsal_does_not_require_decision_for_unrelated_matching_symbol(
    tmp_path: Path,
) -> None:
    repo = _synthetic_rehearsal_repo(tmp_path, "upstream-equivalent")
    refs_before = _git(repo, "show-ref", "--heads")
    report = tmp_path / "report-upstream-equivalent"

    result = _run_synthetic(repo, report)

    assert result.returncode == 0, result.stderr
    assert _git(repo, "show-ref", "--heads") == refs_before
    evidence = json.loads((report / "merge-evidence.json").read_text())
    assert evidence["entries"][0]["overlap_class"] == "none"
    assert evidence["entries"][0]["decision_required"] is False
    assert evidence["entries"][0]["decision"] == "not-required"
```

- [ ] **Step 2: Write the failing schema control**

Remove the valid `possible_upstream_equivalent` row from `test_evidence_accepts_only_derived_decision_states`. Add this formerly valid state to the rejection parameters:

```python
("possible_upstream_equivalent", "owned_symbol", True, "adapt"),
```

Retain the existing contradictory possible-equivalent row as a second rejection control. This proves the class itself, not merely its decision tuple, is invalid.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_workflow_upstream_merge.py -q \
  -k 'synthetic_overlap_classes or unrelated_matching_symbol or evidence_accepts_only or evidence_rejects_self_asserted'
```

Expected after Task 1: the classifier/rehearsal assertions pass, but the schema still accepts the correctly formed former class, so the new schema rejection fails for the expected reason.

- [ ] **Step 4: Narrow the evidence schema without removing the human decision**

Change `entry.properties.overlap_class.enum` to:

```json
["none", "same_file", "owned_symbol"]
```

Change the first `entry.oneOf` branch to:

```json
"overlap_class": {"const": "owned_symbol"}
```

Do not change the `decision` enum; `remove-as-upstream-equivalent` remains valid for `owned_symbol` and `same_file` with `any_owned_file`.

- [ ] **Step 5: Update the operator contract and ledger invariants**

In the README, state that `--upstream-diff` first intersects upstream-changed paths with ledger-declared files, parser requests contain only that intersection and path-owning symbols, and compatibility outside those paths is verified by post-merge invariants. Replace the decision paragraph with:

```text
Every report row marked decision_required requires an explicit preserve, adapt,
or remove-as-upstream-equivalent decision. This includes owned_symbol results
plus same_file results governed by any_owned_file; a prior acknowledgement
never substitutes for the current decision.
```

In `workflow-rehearsal-executed-invariant-evidence.owned_invariants`, add exactly:

```yaml
  - upstream overlap parsing is limited to upstream-changed paths that intersect ledger-declared files
  - each parser-backed overlap path receives only symbols owned by entries that declare that path
  - compatibility outside directly intersecting files is established by post-merge invariants, not repository-wide matching-name inference
```

Do not add a new `owned_symbol`; `classify_upstream_overlaps` owns the behavior.

- [ ] **Step 6: Run focused and full contract tests to verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_workflow_upstream_merge.py -q \
  -k 'synthetic_overlap_classes or unrelated_matching_symbol or evidence_accepts_only or evidence_rejects_self_asserted'
.venv/bin/python -m pytest \
  tests/scripts/test_check_upstream_customizations.py \
  tests/scripts/test_workflow_upstream_merge.py -q
```

Expected: focused controls and both complete files pass with pristine output.

- [ ] **Step 7: Run static checks and commit the contract**

Run:

```bash
.venv/bin/python -c 'import json; from pathlib import Path; from jsonschema import Draft202012Validator; schema=json.loads(Path("docs/upstream-customizations/merge-evidence.schema.json").read_text()); Draft202012Validator.check_schema(schema); print("schema ok")'
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --strict --base-ref HEAD
.venv/bin/ruff check tests/scripts/test_workflow_upstream_merge.py
.venv/bin/python -m py_compile tests/scripts/test_workflow_upstream_merge.py
git diff --check
git status --short
```

Expected: schema, strict validation, lint, compilation, and diff checks pass. Commit:

```bash
git add docs/upstream-customizations/merge-evidence.schema.json \
  docs/upstream-customizations/README.md \
  docs/upstream-customizations/workflow-orchestration.yaml \
  tests/scripts/test_workflow_upstream_merge.py
git commit -m "fix(workflow): align evidence with owned-file overlap"
```

- [ ] **Step 8: Run committed strict validation**

Run:

```bash
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --strict --base-ref HEAD
```

Expected: exit 0 against the committed contract.

---

### Task 3: Regenerate Task 10 Release Evidence

**Files:**
- Create ignored evidence: `.superpowers/sdd/2026-07-29-workflow-language-foundation-task-10-release-evidence/evidence/*`
- Create ignored report: `.superpowers/sdd/2026-07-29-workflow-language-foundation-task-10-release-evidence/task-10-remediation-report.md`
- Preserve tracked source at the committed Task 2 tip.

**Interfaces:**
- Consumes: committed Task 1 and Task 2 changes, literal `main`, current committed `HEAD`, `otto`, and `loop24`.
- Produces: schema-valid `merge-evidence.json`, single-attempt invariant evidence, brand equivalence, ancestry proof, immutable-ref proof, and cleanup proof.

- [ ] **Step 1: Seal the new candidate and pre-run inventory**

Record the exact branch, `HEAD`, `main`, `otto`, `loop24`, tracked status, remediation ancestry, and `git worktree list --porcelain` in the report before running gates. Require the branch to remain `test/workflow-language-foundation-task-10-release-evidence`, tracked status to be clean, and `.venv/bin/python -m pytest --version` to report the provisioned pytest environment.

- [ ] **Step 2: Run the exact no-retry acceptance once**

Run:

```bash
scripts/run_tests.sh --file-retries 0 \
  tests/scripts/test_check_upstream_customizations.py \
  tests/scripts/test_workflow_merge_gate.py \
  tests/scripts/test_workflow_upstream_merge.py
```

Expected: every file passes its first attempt with zero file retries. Record exact per-file and total counts and duration. On failure, stop without retry.

- [ ] **Step 3: Run strict committed-ledger validation once**

Run:

```bash
SHARED_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
"$SHARED_ROOT/.venv/bin/python" scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --strict --base-ref HEAD
```

Expected: exit 0. On failure, stop without rehearsal.

- [ ] **Step 4: Run the controlled rehearsal once**

Confirm the evidence directory contains no prior `overlap.json` or `merge-evidence.json`, then run:

```bash
scripts/test_workflow_upstream_merge.sh \
  --upstream-ref main \
  --base-ref HEAD \
  --brand-ref otto \
  --brand-ref loop24 \
  --report-dir .superpowers/sdd/2026-07-29-workflow-language-foundation-task-10-release-evidence/evidence
```

Expected: exit 0 and print the evidence JSON path. If an intersecting entry requires `preserve`, `adapt`, or `remove-as-upstream-equivalent`, inspect `overlap.json` and stop for human approval; do not invent a decision.

- [ ] **Step 5: Validate schema and semantic evidence independently**

Run Draft 2020-12 validation and exact semantic assertions:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator

root = Path('.superpowers/sdd/2026-07-29-workflow-language-foundation-task-10-release-evidence/evidence')
evidence = json.loads((root / 'merge-evidence.json').read_text())
schema = json.loads(Path('docs/upstream-customizations/merge-evidence.schema.json').read_text())
Draft202012Validator(schema).validate(evidence)
executed = [test for entry in evidence['entries'] for test in entry['tests'] if test['kind'] == 'executed']
references = [test for entry in evidence['entries'] for test in entry['tests'] if test['kind'] == 'reference']
assert executed
assert all(test['result'] == 'passed' for test in executed)
assert all(test['flaky_on_first_attempt'] is False for test in executed)
assert all(len(test['attempts']) == 1 for test in executed)
assert all(test['attempts'][0]['result'] == 'passed' for test in executed)
assert all(test['attempts'][0]['output_truncated'] is False for test in executed)
assert all(test['reason'] == 'non-executable invariant reference' for test in references)
assert {brand['ref'] for brand in evidence['brands']} == {'otto', 'loop24'}
assert all(brand['contains_tested_base'] for brand in evidence['brands'])
assert all(brand['generic_runtime_matches_base'] for brand in evidence['brands'])
assert all(command['result'] == 'passed' for command in evidence['commands'])
assert evidence['final_ancestry'] is True
print(len(evidence['entries']), len(executed), len(references), len(evidence['commands']))
PY
```

Search the serialized evidence for credential/environment/prompt/workflow-input material using the schema's safe-text rules and report only whether any forbidden field was found; do not print environment values.

- [ ] **Step 6: Prove ref immutability and cleanup**

Compare the four refs and worktree inventory byte-for-byte with the Step 1 snapshot. Confirm tracked and staged diffs are empty. Inspect relevant processes and temporary paths for rehearsal, checker, ledger runner, parser helper, pytest, supervisor, watchdog, and `workflow-ledger-*` residue. Record the approved Darwin/Windows and pre-observation POSIX containment limitations exactly.

- [ ] **Step 7: Complete the release-evidence report**

Write the chronological command transcript, exact outputs/counts, schema and semantic assertions, evidence paths, platform limitations, refs, worktrees, processes, temporary paths, final branch/HEAD/status, and self-review to `task-10-remediation-report.md`. Do not create a commit for ignored release evidence.
