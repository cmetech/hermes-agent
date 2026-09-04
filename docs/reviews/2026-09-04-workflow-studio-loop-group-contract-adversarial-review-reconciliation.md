# Workflow Studio loop-group contract adversarial review reconciliation

Date: 2026-09-04

## Immutable review scope

- Base commit: `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d`
- Candidate commit: `869c6519cf86e9df6a903851ccf9d2ee2fc427fa`
- Base tree: `986b9b76f06b562ccc914318507c26dd95cb6d49`
- Candidate tree: `fd176c315a258d2d7dcc493442a174fba91d7a05`
- Range: 11 commits, 17 files, +3,886/-135
- Codex review: `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-codex-5-6.md`
- Claude Fable review: `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-fable-5.md`
- Authenticated Claude Fable rerun:
  `2026-09-04-workflow-studio-loop-group-contract-adversarial-review-fable-5-authenticated.md`

The reviewers worked in separate detached worktrees pinned to the candidate commit. Neither reviewer read the other's report. The controller then assigned the findings to two independent validation agents, which traced the relevant production call paths and compared bounded probes against the merge base. No finding below is accepted merely because a reviewer asserted it.

## Consolidated verdict

The original candidate does not pass review. Seven unique candidate-caused
defects are confirmed: six Important and one Minor. The authenticated Claude
Fable rerun added one installed-distribution defect after the first
reconciliation. One additional duplicate-dependency concern is real but
pre-existing and outside the approved Hermes Phase A runtime-change boundary.

| Consolidated ID | Source finding | Severity | Ruling | Required disposition |
|---|---|---:|---|---|
| LGR-001 | Codex LGC-001 | Important | Confirmed | Publish an exact machine-readable interpolation surface from the same declarative authority used by runtime enumeration, including wildcard agent/hook paths and literal/reference distinctions. |
| LGR-002 | Codex LGC-002 | Important | Confirmed | Publish body-over-outer producer precedence and first-iteration `$LOOP_PREV` behavior; add collision and first-iteration conformance cases. |
| LGR-003 | Codex LGC-003 | Important | Confirmed | Assign scoped companion semantic codes only to exact `group/child` references with two non-empty reference-safe segments. |
| LGR-004 | Codex LGC-004; Fable LGWF-2 | Important | Confirmed and deduplicated | Keep pre-v6/legacy rule descriptors byte-identical; add `kind` only to the Phase 6 projection and guard the legacy bytes/digest. |
| LGR-005 | Fable LGWF-1 | Important | Confirmed | Stop applying strict loop-work arithmetic to raw, not-yet-normalized retry values; restore baseline diagnostics without default substitution or broader validation-order changes. |
| LGR-006 | Fable LGWF-3 | Minor | Confirmed | Publish structured tri-state evaluation order and terminal `$ref` behavior; add public-corpus cases for the two ambiguous accepted forms. |
| LGR-007 | Authenticated Fable WS-1 | Important | Confirmed by independent live reproduction on the remediated branch | Resolve packaged Jira authoring resources from the source tree or `sys.prefix`; test an ordinary wheel-installed venv console with `PYTHONPATH` absent. |
| LGR-D01 | Codex duplicate dependency concern | Deferred | Pre-existing | Do not change Hermes admission in Phase A. Workflow Studio must prevent creation of duplicate visual dependencies while preserving imported YAML. |

## Evidence and remediation boundaries

### LGR-001 — interpolation surface is not machine-readable

The published scoped-reference rule describes a whole body node, while runtime interpolation privately enumerates narrower paths such as `systemPrompt`, agent description/prompt, and hook response fields. Literal model fields are not scanned. A Studio implementation would therefore need to copy private Hermes knowledge.

The repair must use one declarative field-path inventory for both runtime enumeration and contract publication. Tests must prove that published paths match actual enumeration and distinguish reference-capable fields from literal fields. This is a publication/refactor change, not a new interpolation feature.

### LGR-002 — collision precedence and first-iteration behavior are omitted

Runtime lookup gives a loop-body node precedence over an outer node with the same ID. On the first iteration, a known whole `$LOOP_PREV` reference resolves to the empty string, whereas a structured previous-output path fails; unknown producers also fail. These behaviors are not completely encoded in the public contract.

The repair must publish those rules and add executable conformance cases, including an outer/body ID collision. Runtime behavior must remain unchanged.

### LGR-003 — scoped semantic-code classifier is too broad

The candidate uses slash presence as the classifier, so `group/child/extra` and unrelated slash-containing values can receive a scoped-reference semantic code even though the public syntax is exactly `group/child`.

The repair must recognize exactly one slash with two non-empty, reference-safe identifiers. Tests must cover valid scoped references, unknown valid scoped references, no slash, multiple slashes, empty segments, and assignment syntax.

### LGR-004 — legacy contract identity regressed

Both reviewers independently found the same defect. Candidate legacy canonical JSON is 227,103 bytes with digest `sha256:a9eb070e857ae859a486fa8862025528c9c0dfe93d2258b0340be71dbd3516d7`; the merge-base output is 226,976 bytes with digest `sha256:692c22a06fa61aa1949d25fa4ce12b3345cac3fadd7f562558c3f5394d35617f`. Four legacy rules gained `kind`.

The repair must project `kind` only for Phase 6 and preserve the exact pre-v6 canonical bytes and digest. Every Phase 6 rule must still have `kind`.

### LGR-005 — raw retry values bypass the established diagnostic

For loop-group children with `retry.max_attempts` equal to `true`, `-1`, or `1.5`, the merge base raises `WorkflowValidationError` with code `archon_retry_invalid` at `nodes[0].retry.max_attempts`. The candidate instead raises a bare `ValueError`, which the CLI exposes as generic `invalid_request` without the useful path/issues.

The trigger is strict work-factor arithmetic during structural loop-group normalization, before the existing Archon retry normalization. The string value `"nope"` already raised a bare `ValueError` in the merge base and is not a candidate regression.

The safe Phase A repair is exact restoration of the raw structural calculation, or otherwise avoiding the strict shared helper on raw nodes while keeping strict work/capacity checks on already-normalized inputs. It must not substitute default retry values, invent fictitious work, reorder the broader validation pipeline, or expand into the pre-existing string-diagnostic debt. Parameterized library and CLI regressions must coexist with the 4,096/4,097 bounds tests.

### LGR-006 — tri-state policy composition is ambiguous

Runtime accepts both of these forms, in the candidate and merge base:

1. A schema with non-empty `patternProperties`, `additionalProperties: false`, and an unmatched requested path.
2. A terminal property whose schema is a `$ref` resolving to `false`.

The public policy names the individual strategies but does not state their composition order or that `$ref` resolution applies to the current schema only while path segments remain. A reasonable independent reader can therefore reject inputs Hermes accepts.

The repair must publish structured evaluation metadata for object lookup order, terminal handling, and `$ref` applicability. Both forms must be accepted conformance-corpus cases exercised through an independent public-contract interpreter rather than private `_v3_*` functions.

### LGR-007 — an ordinary installed console cannot resolve the Archon corpus resource

The authenticated Claude Fable rerun traced the Archon-only Jira fixture from
`plugins/workflow/language_conformance.py` into the wheel packaging layout.
The module originally assumed `Path(__file__).resolve().parents[2]` was always
the repository root. In an ordinary virtual environment that expression is
`site-packages`, while setuptools installs the `capabilities/**` data files
under `sys.prefix`.

An independent Codex validator built the current branch from a clean archive,
installed the wheel into a fresh venv, removed `PYTHONPATH`, and ran the
installed console from a non-repository directory. It reproduced exit 1,
zero stdout, and an uncaught `FileNotFoundError` below
`site-packages/capabilities`; the actual Jira YAML existed below
`sys.prefix/capabilities`. The earlier installed-distribution test masked the
defect by importing from a flattened sibling `--target` install through
`PYTHONPATH`.

The repair must keep source checkout behavior, fall back to `sys.prefix` for
wheel data files, retain the early dependency-neutral read-only boundary, and
prove the normal installed module/resource origins without borrowing the
source tree or target install. It must not duplicate or regenerate the Jira
workflow.

## Reviewer disagreements

The first Fable report did not report LGR-001 through LGR-003 and described its
scoped-reference checks as passing. That absence is not a rebuttal: independent
validation reproduced the incomplete publication and over-broad slash
classifier. Codex did not report LGR-005 or LGR-006; independent validation
reproduced both. The first reviewers agreed independently on LGR-004, although
Codex rated it Important and Fable rated it Minor. The consolidated severity
is Important because byte-identical legacy output is an explicit compatibility
requirement.

The authenticated Fable rerun found LGR-007 but missed several already
reproduced candidate defects and interpreted the legacy byte-identity
requirement too loosely. Those omissions do not reverse the earlier evidence.
Conversely, LGR-007 is not rejected merely because neither first report found
it: the independent normal-venv reproduction confirms its production path and
wrong observable result.

## Deferred duplicate-dependency concern

Both candidate and merge base accept `depends_on: [a, a]`. Changing Hermes admission would alter existing runtime language behavior and is prohibited by the approved Phase A boundary. The Workflow Studio Phase B mutation layer must refuse to create a duplicate dependency, cycle, self-edge, or unresolved dependency while retaining unknown/imported YAML. This deferred item does not excuse any of the six candidate-caused defects and does not block Phase B once Studio enforces its own visual mutation invariant.

## Verification record and limitations

The first Fable detached checkout completed the required 11-file focused
battery with 1,192 passing tests, the installed-distribution integration with
1 passing test, and Ruff successfully with one pre-existing warning. Codex's
sandboxed test attempts encountered environment-specific macOS `sysctl` and
cache restrictions; those are not counted as candidate failures or passes.
The authenticated Fable rerun could inspect Git and source but its permission
harness denied Python, the test runner, Ruff, and the Windows audit; none of
those denied commands is counted as a pass. `git diff --check` passed for the
immutable range.

No native Windows verification was performed. No report or reconciliation result represents Windows installed-distribution behavior as passing.

## Remediation gate

The branch may proceed only after all seven confirmed defects have
behavior-first regression tests, focused verification passes, a fresh code
review accepts the remediation, and the complete branch receives final
verification. The review artifacts themselves do not authorize merging,
pushing, or beginning Workflow Studio Phase B.
