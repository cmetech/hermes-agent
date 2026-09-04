# Workflow Studio loop-group Hermes contract — final verification

Date: 2026-09-04

## Outcome

The Hermes Phase A authoring-contract branch has passed its final scoped
review and verification gates. It remains intentionally unmerged and unpushed
pending user review/approval before Workflow Studio Phase B begins.

Final code state under verification:

- Branch: `feat/workflow-studio-loop-group-contract`
- Merge base: `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d`
- Code HEAD: `79196298e432f1713a11387dbc6bc83613330f31`
- Code tree: `850c132f522637f21c1198f4482cf6302180dac1`
- Range at code HEAD: 18 commits, 25 files, +7,530/-233

## Adversarial-review disposition

Separate review evidence is preserved in:

- `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-codex-5-6.md`
- `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-fable-5-authenticated.md`
- `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-reconciliation.md`
- `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-remediation.md`

The authenticated Claude invocation used model `claude-fable-5`, session
`62196e8d-6fe8-46a9-9df7-157ec234de4b`, and reviewed the original immutable
candidate in a detached clean checkout. Its permission harness denied Python
and test execution, so those commands are not represented as passing.

The complete process confirmed and resolved nine candidate-relevant findings:

1. incomplete public interpolation surfaces;
2. missing collision precedence and first-iteration semantics;
3. over-broad scoped companion classification;
4. legacy contract byte drift;
5. raw retry diagnostic regression;
6. ambiguous tri-state structured-path composition;
7. dead/driftable public work-factor authority;
8. normal wheel-installed Archon corpus resource failure; and
9. partial command/prompt retry mappings undercounting executable attempts.

The duplicate-dependency admission behavior and raw-string retry `ValueError`
remain documented pre-existing debt outside this Hermes Phase A boundary.
Workflow Studio Phase B must prevent visually creating duplicate dependencies
while preserving imported YAML.

## Fresh final verification

The final affected workflow language/schema/compatibility/corpus/CLI battery
ran once at code HEAD through `scripts/run_tests.sh`:

```text
11 files: 1,247 passed, 0 failed
```

The reviewed installed-resource fix was verified on the final code through a
real wheel-built ordinary venv with `PYTHONPATH` absent:

```text
installed-distribution integration: 5 passed, 0 failed
language conformance: 16 passed, 0 failed
schema-corpus CLI selection: 5 passed, 0 failed
```

The final FCR-001 controller battery passed 852 tests across Phase 6, language
schema, conformance, and installed-distribution files. The scoped reviewer
also ran 20 focused work-factor/diagnostic/boundary tests successfully.

Static and Git checks:

```text
repository-wide Ruff: passed
git diff --check merge-base..HEAD: passed
targeted Ty comparison: base 41 diagnostics; feature 40 diagnostics
```

The normalized Ty difference is exactly one pre-existing
`semantic_rule_descriptors` return-inference diagnostic removed by the typed
local list boundary. No branch-only targeted diagnostic remains.

The full repository suite was not rerun after the final targeted changes. Its
single earlier branch run reported the already documented global baseline of
51,061 passed, 346 failed, and 662 skipped; no workflow test file failed in
that run. This report does not claim the global repository suite is green.

## Final artifacts

CLI JSON includes one trailing newline; canonical measurements exclude it.

| Artifact | Canonical bytes | Canonical SHA-256 | Embedded contract digest |
|---|---:|---|---|
| Archon contract | 283,522 | `171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc` | `sha256:fb25d0cd4749774f2db5b38e376071159a011f326eeca9cbc88847ddbfdd0fa0` |
| Legacy contract | 226,976 | `f4de08444f110fb8c4e2b36d9246df4194b8a70dde8bae495a381d757fdf6e07` | `sha256:692c22a06fa61aa1949d25fa4ce12b3345cac3fadd7f562558c3f5394d35617f` |
| Archon corpus (48 cases) | 126,533 | `882fa27166c022918bc9c1c9462725b50578d7f861f2296b5d03d309e50a8718` | `sha256:fb25d0cd4749774f2db5b38e376071159a011f326eeca9cbc88847ddbfdd0fa0` |
| Legacy corpus (11 cases) | 7,265 | `f5fdb6fb9424b673357d5df10dc7c6af55b685d15707dc34ddc5746d902f7ee0` | `sha256:692c22a06fa61aa1949d25fa4ce12b3345cac3fadd7f562558c3f5394d35617f` |

The Archon contract remains within its 284,000-byte usable ceiling and retains
the separate 4,000-byte reserve, but only 478 usable bytes remain. This is a
real capacity constraint for future contract additions.

## Platform limits

The final Windows-footgun audit returned nonzero with 33 known findings across
15 changed-file-scope files. It is not represented as passing. The final
resource/work-factor changes did not add an audit match, but no native Windows
installed-app execution was performed. Native Windows remains unproven.

## Handoff boundary

No scheduler, executor, persistence, provider, Git, or Workflow Studio product
implementation was changed beyond the narrowly authorized admission-bound
correction required to make the published work formula match sealed executable
retry semantics. No merge, push, release, or Workflow Studio Phase B work has
been performed.
