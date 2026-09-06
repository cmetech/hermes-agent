# Authenticated Claude Fable adversarial review — Workflow Studio loop-group authoring contract

Date: 2026-09-04

## Reviewer and immutable scope

- Reviewer: Claude Fable 5 (`claude-fable-5`) through Claude Code 2.1.212.
- Claude session: `62196e8d-6fe8-46a9-9df7-157ec234de4b`.
- Duration: 942,207 ms over 77 turns.
- Review checkout:
  `/private/tmp/hermes-loop-contract-adversarial.UMK4h8/fable`.
- Candidate: `869c6519cf86e9df6a903851ccf9d2ee2fc427fa`.
- Candidate tree: `fd176c315a258d2d7dcc493442a174fba91d7a05`.
- Merge base: `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d`.
- Merge-base tree: `986b9b76f06b562ccc914318507c26dd95cb6d49`.
- Range: 11 commits, 17 paths, +3,886/-135.
- Checkout state: detached and clean at start and end; `git diff --check`
  clean.

This report was produced after the user authenticated the local Claude CLI.
The model reviewed the original immutable candidate independently. It did not
read the Codex report or the later reconciliation/remediation reports before
returning its verdict.

## Evidence limitation

Claude's `dontAsk` permission harness allowed read/search and immutable Git
inspection but denied Python, the repository test runner, Ruff, and the
Windows-footgun script. The model therefore made no test-pass claim. Its new
finding was a static packaging proof, subsequently assigned to an independent
Codex validator for live reproduction on the current remediated branch.

## Verdict

**BLOCK** — one Important finding, no Critical findings.

## Findings

| ID | Severity | Summary | Primary invariant |
|---|---:|---|---:|
| WS-1 | Important | A standard wheel-installed/venv console cannot build the Archon schema corpus because the Jira workflow resource is resolved below `site-packages`, while setuptools data files install it below `sys.prefix`. The existing installed-distribution test masks the mismatch by setting `PYTHONPATH` to a flattened sibling `--target` installation. | 17 |

## WS-1 proof

1. **Location.** `plugins/workflow/language_conformance.py` derives
   `_REPOSITORY_ROOT` from `Path(__file__).resolve().parents[2]`, then forms
   `_JIRA_DEFINITION` and `_JIRA_COMPANION` below that root. The Archon corpus
   reads both files. `plugins/workflow/schema_cli.py` and the early path in
   `hermes_cli/main.py` call that producer without handling
   `FileNotFoundError`.
2. **Packaging authority.** `pyproject.toml` ships `capabilities/**` through
   `[tool.setuptools.data-files]`. Those paths install relative to the Python
   prefix, not below `site-packages`. Existing
   `hermes_cli.capability_staging._repo_root()` explicitly documents and
   handles the same source-root-versus-`sys.prefix` distinction.
3. **Trigger.** Install the wheel into an ordinary virtual environment and
   run `hermes workflow schema-corpus --profile archon-2026-07 --json` from a
   non-repository directory with `PYTHONPATH` absent.
4. **Production path.** The installed module lives below
   `<prefix>/lib/pythonX.Y/site-packages/plugins/workflow/`, so its fixed
   parent calculation yields `site-packages`. It looks for
   `site-packages/capabilities/.../jira-defect-loop.yaml`; the wheel actually
   installed the file at `<prefix>/capabilities/.../jira-defect-loop.yaml`.
5. **Wrong result.** An uncaught `FileNotFoundError` exits nonzero and emits
   no corpus stdout. The legacy corpus does not read the Jira fixture, so the
   installed failure is Archon-specific and blocks the profile Workflow
   Studio needs.
6. **Why the current test misses it.** The fixture creates a `--target`
   install and a separate `--prefix` console, then sets `PYTHONPATH` to the
   target root. In that flattened layout the imported module and data files
   share one root, so the incorrect parent-relative lookup happens to work.
   The prefix console's own import/resource layout is never exercised.
7. **Smallest safe remediation.** Keep a tiny, side-effect-free resolver in
   `language_conformance.py`: use the source root when it contains
   `capabilities`; otherwise use `Path(sys.prefix).resolve()` when the
   packaged capability tree exists. Do not import capability staging into the
   early authoring-data path.
8. **Required regression.** Build and install the wheel into an ordinary
   virtual environment, run its own console with `PYTHONPATH` removed from a
   non-repository cwd, prove the imported workflow module is inside that
   venv, and assert Archon corpus exit 0, empty stderr, bounded valid JSON, and
   deterministic bytes.

## Invariant matrix returned by Claude

| # | Verdict | Concise basis/qualification |
|---:|:---:|---|
| 1 | PASS | Exact candidate/tree/base/range and clean detached checkout verified. |
| 2 | PASS (static) | Archon selects v6; legacy selects v2; replay was not executed. |
| 3 | PASS (static) | YAML/loader authority remains singular; corpus is data-only. |
| 4 | PASS (static) | Work publication/admission share authorities; boundaries not executed by Claude. |
| 5 | PASS (static) | Claude found the intended scoped-DAG rejection branches. |
| 6 | PASS (static) | Current/previous/outer surfaces and group predicates were traced. |
| 7 | PASS (static) | Primary sink follows first terminal in authored order. |
| 8 | PASS (static) | Conservative tri-state policy accepts possible/unknown and rejects impossible. |
| 9 | PASS (static) | Published structured-path strategies were compared row by row. |
| 10 | PASS (static) | Semantic metadata is additive and attached at validator branches. |
| 11 | PASS (static) | Claude traced the scoped companion path, with gaps discussed below. |
| 12 | UNPROVEN | Full corpus/compiler agreement could not be executed. |
| 13 | PASS (static) | Corpus coverage is present in source; installed delivery is affected by WS-1. |
| 14 | PASS (static) | Case/byte bounds fail closed before printing. |
| 15 | PASS (static) | Only schema/schema-corpus use early startup. |
| 16 | PASS (static) | Early imports are bounded and side-effect-free. |
| 17 | FAIL | WS-1: normal installed Archon corpus resource resolution fails. |
| 18 | PASS (qualified) | Claude noted the candidate legacy `kind`/digest change but interpreted byte identity too loosely. |
| 19 | PASS (static) | Merge-gate and three-OS CI references were counted. |
| 20 | PASS (qualified) | No scheduler/executor/store/provider effects; WS-1 qualifies Phase B usability. |

## Reconciliation cautions

This authenticated review is independent evidence, not a replacement for the
Codex review or the earlier report. Three of its static rulings do not rebut
already reproduced findings:

- It did not identify the candidate's incomplete interpolation surface,
  collision/first-iteration publication, or over-broad slash classifier.
- It treated the explicit legacy byte-identity requirement as ordinary
  generation determinism, despite the candidate's measured byte/digest drift.
- It described scoped DAG admission as passing without separating the known
  pre-existing duplicate-dependency behavior from candidate-caused defects.

Those omissions and severity judgments are rejected by the controller's
reconciliation because the earlier bounded reproductions remain valid. WS-1
is additive to, not in conflict with, LGR-001 through LGR-006.

## Verification ledger

Claude successfully verified the immutable Git facts, changed-path inventory,
range totals, merge base/tree, and clean checkout. Its attempts to execute
`scripts/run_tests.sh`, Python probes, Ruff, and the Windows audit were denied
by the Claude permission harness and are not counted as passes.

After Claude returned, an independent Codex validator performed the live
wheel/venv reproduction on the current branch. That evidence is recorded in
the updated reconciliation/remediation record rather than attributed to
Claude.

## Residual uncertainty

- Claude performed no native Windows execution.
- Claude did not dynamically execute corpus determinism, snapshot replay, or
  startup containment tests.
- `$LOOP_PREV` on a non-applicable group gate surface and compact policy-string
  ergonomics were noted as residual documentation risks, not qualifying
  candidate findings.
- The candidate's duplicated retry literal was noted as future drift risk,
  not a current defect.

## Final checkout state

The detached Claude review checkout remained clean at candidate
`869c6519cf86e9df6a903851ccf9d2ee2fc427fa`, tree
`fd176c315a258d2d7dcc493442a174fba91d7a05`. Claude made no repository,
branch, worktree, network-service, or external-system changes.
