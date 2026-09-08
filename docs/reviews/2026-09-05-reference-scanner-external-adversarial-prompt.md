# External adversarial code review: declarative reference scanner

You are the external reviewer for one assigned lane. The controller has
already launched independent Claude and Codex sessions, each supervised by a
separate subagent. Review the candidate yourself; do not launch reviewers.
The reviewer is
the external CLI model, not its launching subagent. Record the actual CLI,
model identity, command, exit status, permission failures, and raw report.
Neither lane may read the other lane's report before returning its verdict.

## Purpose and source lineage

This prompt adapts the evidence standards and invariant matrix from
`docs/reviews/2026-09-04-workflow-studio-loop-group-contract-adversarial-review-prompt.md`
and the finding-by-finding validation method in the corresponding
`2026-09-04-workflow-studio-loop-group-contract-adversarial-review-reconciliation.md`.
The earlier agent-handoff adversarial code-review prompt also supplies the
requirement to trace complete production paths and relevant unchanged callers.
Those historical reviews concern different candidates; do not import their
findings or old size limits into this review.

The user explicitly requested this additional two-model code review after
delivery. It does not reopen architecture or plan design. There is no shared
VM: Hermes keeps its existing Python runtime, publishes declarative metadata
and literal expected outcomes, and Studio later implements TypeScript.
Do not propose an interpreter, bytecode, transition program, VM resource
accounting, or cross-language memory model. Escalate a demonstrated need for
such an architecture instead of treating it as an automatic remediation.

## Immutable scope and isolation

```text
Worktree: /Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-reference-scanner-contract
Branch: feat/workflow-reference-scanner-contract
Base: 74fed08f91014ca7fc80ee9ea4427568ec95b04f
Candidate: 5c3efdbe757a6fa83cf85cec8a95ea3ff96bf6ed
Candidate tree: 6f7f9d70c3020fa999e65c21dec636c83fd6d5d7
Range: one commit, 34 changed paths, 12015 insertions, 206 deletions
```

Use this existing worktree. Do not create another checkout/worktree, switch
branches, stage, commit, merge, push, or touch another Hermes worktree or
Workflow Studio. The checkout intentionally contains six unrelated historical
documents and the controller's new review records; it is not globally clean.
Verify candidate HEAD/tree, parent, changed paths, and no local changes to the
candidate's 34 paths. Do not reject the scope just because historical or review
documents are untracked. If candidate code changed, return `SCOPE ERROR`.

Read source and Git only. Do not edit production, tests, corpus, configuration,
dependency environments, or existing documents. Return Markdown to stdout;
the launcher persists it. Disposable probes may use synthetic inputs and a
private temporary directory. Do not access secrets, live services, or another
model; external model invocation itself is handled by the authorized launcher.

Do not read existing candidate review verdicts, delivery narratives,
reconciliation reports, or `.superpowers/sdd/` ledgers. The binding spec/plan,
source, tests, literal case manifest, and baseline fixtures are permitted.
Documentation is a claim to verify, not proof of implementation correctness.

## Read and trace

Read `AGENTS.md`, then the complete replacement specification and plan:

- `docs/superpowers/specs/2026-09-04-declarative-reference-scanner-design.md`
- `docs/superpowers/plans/2026-09-04-declarative-reference-scanner.md`

Follow their workflow-language authority references as needed for the concrete
paths under review. Read the complete changed production files and tests,
including the literal JSON and manifest. Use the cumulative diff as an index,
not a substitute for final code. Inspect relevant unchanged runtime paths in:

- `plugins/workflow/language_schema.py`
- `plugins/workflow/bash_rendering.py`
- `plugins/workflow/resources.py`
- `plugins/workflow/conditions.py`
- `plugins/workflow/schema.py`

The amendment files include `reference_scanner_contract.py`,
`language_conformance.py`, `schema_cli.py`, packaged
`conformance/reference_scanner_v1.json`, the workflow README, package-data
selection, the release-gate list, and their tests. Large immutable baseline
JSON captures may be checked through independent canonical-byte comparison;
do not omit scanner literals on account of their length. State any incomplete
reading explicitly.

## Requirements matrix to falsify

For every row return `PASS`, `FAIL`, or `UNPROVEN`, with evidence. A test name
or comment alone does not establish a pass.

| ID | Locked requirement |
| --- | --- |
| H1 | Seven historical authoring pairs remain byte-identical; the legacy corpus stays format 1, 11 cases, 7265 canonical bytes, SHA-256 c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b. Only the approved v6 metadata delta is allowed. |
| S1 | Archon v6 reader 3 activation metadata declares every required section, grammar, mode, scalar, caller, diagnostic, version/profile and Unicode capability. Older readers must not silently interpret it. Reader checks in Studio itself remain deferred. |
| S2 | Ordinary/LOOP_PREV grammar, candidate boundaries, masking, suffixes .outputx/.output_/.output/path, bracket paths, leading-zero indices, and non-ASCII continuations match Hermes per caller and version. |
| S3 | Published Bash rules and literals cover quoting, escaping, comments, substitutions, arithmetic, arrays, heredocs, here-strings, functions, coprocesses, case arms, nesting and ambiguous states without replacing the Python classifier. |
| S4 | Structured-path static proof and runtime resolution remain distinct and match containing constraints, $ref siblings, unions, unknowns, and numeric object/array interpretations. |
| S5 | All 18 root fields, the same 18 body fields, and two controls derive from _INTERPOLATION_SURFACE_INVENTORY; no second handwritten inventory. Applicability and container/leaf order match actual callers. |
| S6 | Root Phase-4 systemPrompt, agents and hooks have faithful surfaces and validation witnesses. |
| S7 | Current/outer dependency errors and LOOP_PREV producer errors retain exact applicability, native and portable IDs, paths, causes and precedence. |
| S8 | Hermes publishes multi-group/multi-surface witnesses. Studio contract/document indexing once is explicitly deferred, not falsely claimed proved. |
| S9 | Hermes runtime performance is unchanged; publication bounds are appropriate and enforced. Studio 250-node/500-edge canvas and pointer-frame acceptance are explicitly deferred. |
| S10 | Unicode scalar offsets, three supported interpreter/Unicode profiles, and profile-specific behavior are explicit; UTF-16 conversion belongs only at the future editor boundary. |
| H2 | Existing runtime behavior/public APIs, eager versus lazy errors, prefix yields, overlap, masking, scalar forms, validation order, includes and scheduler remain unchanged. |
| H3 | Outcomes are independently authored literal data; fixed adapters execute real APIs and cannot hide mismatches. Deterministic contract/corpus digests bind the right artifacts and detect drift. Finite coverage is not presented as universal equivalence. |
| H4 | Real direct-wheel and sdist-built-wheel installs prove both schema and corpus/resources available offline, outside source imports, through actual console commands; no PYTHONPATH or flattened target-install shortcut. |
| H5 | CLI version/profile/section/count/byte checks fail before output; import/startup boundaries and legacy APIs remain compatible; all new suites participate in the existing release gate. |

## Adversarial campaigns

1. Derive a contradictory witness from each relevant grammar/boundary rule.
   Compare text and Bash observations, ordinary and previous references,
   condition helpers and validators, and scalar substitution modes. Distinguish
   complete references from candidate/presence detection. Test malformed
   suffixes adjacent to valid references and Unicode characters.
2. Trace Bash state transitions in the unchanged classifier and check whether
   the declarative description or a literal witness contradicts them. Compose
   a small number of state families and error-order cases. Do not implement a
   second runtime just to conduct the review.
3. Check lazy first/drain outcomes, yielded prefixes before failure, eager
   atomicity, exception serialization/causes, authenticated script-body maps,
   and digest-owned invalid-byte decoding. Look for adapters that normalize
   away an incompatibility or expectations that agree only with a helper.
4. Trace all inventory surfaces to their root/body/control caller, including
   containers, Phase-4 gates, literal/inline Bash discrimination, group and
   outer scope. Compose competing failures in document order.
5. Probe structured paths with containing and referenced constraints, union
   branches, numeric object keys, array indices, terminal schemas and unknown
   keywords. Separate existing Python semantics from metadata inaccuracies.
6. Audit literal case independence and meaningful distinct observations. A
   demonstrated missing behavioral discriminator in the promised contract or
   corpus is in scope; a demand to serialize every possible input is not.
   Identify whether a realistic future TypeScript implementation could pass
   every applicable vector while violating a specifically published behavior.
7. Check exact historical bytes, current digest inputs, self-digest exclusion,
   repeated publication determinism, exact-integer format handling, invalid
   section shapes, limit-plus-one cases, multibyte accounting, and stdout
   atomicity. Bounds describe publications, not new runtime input limits.
8. Inspect source/sdist/wheel resource selection and real installed origins.
   Try to refute offline proof or show masked dependency/startup behavior.
   Do not repeat expensive build/matrix suites solely to reproduce known
   passes; run a focused test when it proves a new hypothesis.

## Verification discipline and known limitations

Use `scripts/run_tests.sh` for pytest; never raw pytest. The existing local
`.venv` is available. Use bounded synthetic Python probes for non-pytest API
observations, without source mutations. Record exact commands and outputs.
If sandbox restrictions prevent a probe, return the proposed command/input
and mark that evidence unexecuted; the controller can validate it separately.

Do not run the full repository suite in either review lane. The candidate's
completed verification recorded 6305 workflow passes/44 Darwin host skips,
506 focused passes, three Python/Unicode profiles, real offline installations,
and 51 release-gate passes. The full repository recorded 51850 passes,
103 failures, 657 skips, plus an API-server file-descriptor resource failure.
All 103 exact failures and that resource condition were reproduced at the
starting commit. These facts limit broad test claims; they are not grounds
to dismiss a new demonstrated candidate regression. Native Linux/Windows
execution and Studio acceptance are not established by this Darwin review.

## Findings and final report

Return one self-contained Markdown report with:

1. Actual reviewer/model, date, immutable scope verification and read coverage.
2. Verdict `BLOCK`, `PASS`, or `INCOMPLETE`; do not turn missing evidence into
   an unconditional pass. Use stable IDs prefixed `CLAUDE-` or `CODEX-`.
3. Severity-sorted findings, each with exact candidate file/line and unchanged
   caller; violated requirement; minimal literal input or precise trigger;
   expected versus actual observable behavior; full causal path; executed
   reproduction or rigorous source proof; baseline comparison when relevant;
   why tests miss it; smallest in-scope fix and regression test.
4. The complete 15-row requirements matrix, separating deferred Studio checks.
5. Exact verification commands/results, sandbox denials and unverified claims.
6. Final candidate-file status proving the reviewer changed no source.

Critical means severe corruption, unauthorized effects, or systemic breakage.
Important means a realistic published-contract mismatch, wrong literal outcome,
compatibility regression, ineffective promised proof, or broken installed/CLI
behavior. Minor means a concrete localized correctness defect. Style,
speculative hardening and demands for a new runtime are not findings.
Do not invent findings to meet a quota, stop at the first finding, or treat
another review's silence as proof. The controller will consolidate duplicates,
independently validate every finding, and document confirmed, rejected,
pre-existing, deferred, or unresolved dispositions before remediation.
