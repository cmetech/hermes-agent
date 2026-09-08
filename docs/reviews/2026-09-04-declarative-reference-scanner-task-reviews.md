# Declarative scanner focused task reviews

These are task-scoped reviews of the declarative compatibility amendment. Initial findings are followed by their scoped correction review. Tasks 1–5 are approved with no open findings. The single complete-branch review is approved with its integration finding resolved; see the separate branch review and verification records. The abandoned VM reviews are separate historical records.

## Task 1: historical baselines

# Task 1 focused review: historical publication freeze

## Verdict

- **Spec compliance: APPROVED.** Task 1 satisfies the brief and the applicable global constraints.
- **Code/test quality: APPROVED.** No blocking or non-blocking correctness findings.

## Scope reviewed

- Baseline: `74fed08f91014ca7fc80ee9ea4427568ec95b04f`
- Worktree/branch: `.worktrees/workflow-reference-scanner-contract` on `feat/workflow-reference-scanner-contract`
- Reviewed the live test and manifest, all fixture names/sizes/digests, the Task 1 report, and the supplied review package.
- Confirmed `git diff --quiet 74fed08f91014ca7fc80ee9ea4427568ec95b04f -- plugins/workflow pyproject.toml` exits 0. The production publication implementation used for capture is unchanged from the baseline commit.
- Did not rerun the already-passing pytest commands. The Task 1 report records the required RED result (8 intended missing-fixture failures) and GREEN result (29 passed across the baseline and language-conformance files), consistent with the review instruction to rely on that evidence unless a specific doubt required a focused rerun.

## Compliance and quality evidence

1. **Historical pair coverage is exact.** `PAIRS` contains legacy v1/v2 and Archon v1-v5 only (`test_reference_scanner_baselines.py:17-19`), and the parametrized test compares canonical output bytes directly with the fixtures (`:59-66`). This gives the required seven immutable pairs without accidentally freezing Archon v6 as immutable.
2. **The legacy corpus is frozen to the required contract.** The test pins format 1, 11 cases, 7,265 bytes, the required SHA-256, and exact fixture bytes (`test_reference_scanner_baselines.py:69-81`).
3. **Manifest membership and classifications are correct.** The manifest contains exactly the nine requested artifacts, labels Archon v6 `characterization-only`, labels every other artifact `immutable`, and records full byte counts and SHA-256 values (`manifest.json:1-78`; assertions at `test_reference_scanner_baselines.py:84-106`).
4. **Capture provenance and fixture integrity were independently verified.** Regenerating each artifact through `canonical_contract_json(workflow_authoring_contract(...)).encode("utf-8")` or `canonical_contract_json(workflow_language_conformance(...)).encode("utf-8")` against the unchanged baseline source matched every fixture byte-for-byte. Independent `wc -c` and SHA-256 checks also matched every manifest value. Archon v6 is exactly 283,522 bytes with digest `171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc`; the legacy corpus is exactly 7,265 bytes with digest `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b`.
5. **The Archon v6 guard is appropriately narrow for Task 1.** It pins the historical v6 fixture length/digest and compares definition schema, sidecar schema, editor projection 2, normalizer 6, and node kinds exactly (`test_reference_scanner_baselines.py:109-126`). The recursive dictionary/list-of-record diff exposes leaf paths, while its allowlist contains only the planned reader, scanner metadata, derived digest, total/scanner-section limits, and strict-reference root-field-path locations (`:128-142`). Using a subset relation is correct before metadata work because the initial old/current diff must be empty; later publication tests remain responsible for asserting the exact amended values and scanner literals.
6. **Scope is clean.** No production scanner/runtime/API behavior or unrelated worktree was modified. The large JSON blobs are canonical historical captures rather than authored scanner expectations.

## Findings

No concrete spec, correctness, provenance, integrity, or guard-specificity defects found.

## Task 2: declarative metadata

# Task 2 focused review

Verdict: **changes required** for spec compliance and metadata correctness. The implementation structure, inventory projection, historical version isolation, dependency direction and trusted publication bounds are sound. Four concrete publication issues remain; none calls for runtime scanner changes or new architecture.

Reviewed the task brief, constraints, report, review package, approved declarative design, production diff and relevant unchanged scanner/validator source at base `74fed08f91014ca7fc80ee9ea4427568ec95b04f` plus current uncommitted changes.

## Findings

1. **P2 — Published case-arm example has the wrong classification.** `plugins/workflow/reference_scanner_contract.py:116-123` places `case value in $USER_MESSAGE) :;; esac` under `classification: rejected`. A focused direct probe of `classify_bash_reference_spans`, supplying the existing scalar regex's span, returns an admitted span. The existing classifier handles case grammar inside its command/backtick frames; that does not justify labeling this top-level example rejected. Publish the actual admitted behavior and use a genuinely rejected nested case example for the rejected rule. This is an S5 metadata mismatch, not a request to fix Bash semantics. The Task 2 mode test only checks rule shape/family coverage and therefore does not catch it.

2. **P2 — Group-control diagnostic mapping loses existing branch-local distinctions.** `plugins/workflow/reference_scanner_contract.py:554-557` assigns every group control `output_reference_path_unsupported` for missing producer schema and `structured_output_field_impossible` for impossible paths. For `loop_group.until_bash`, both failures are `loop_group_scope_invalid` in `schema.py:2087-2115`, including previous-output schema failures at `schema.py:2026-2057`. Gate messages use the root validator, with an additional dotted-key exception: an impossible dotted-key path emits `output_reference_path_unsupported`, not `structured_output_field_impossible` (`schema.py:1904-1938`). Existing independently asserted cases in `test_phase6_language.py:2122-2190` already pin both distinctions. Split gate/termination mappings and preserve the dotted-key native-code exception. Portable semantic codes remain as currently published. This violates the explicit S7 branch-local diagnostic requirement.

3. **P2 — Candidate detection metadata omits its marker suffix condition and caller distinction.** `plugins/workflow/reference_scanner_contract.py:349-352` gives three unqualified markers and a first-character admission rule. Actual Bash discovery in `language_schema.py:372-382` accepts `.output` as a substring, but `/output` and `\\output` require end-of-candidate or a following member of `.[]/\\`. A focused probe returns no candidates for `$a/outputx` and `$a\\outputx`, but returns a candidate for `$a/output` and `$a\\output`. The first-character filter also belongs to Bash candidate discovery, whereas text `_output_reference_at` (`language_schema.py:449-474`) has no such filter; for example `$!a.output` is malformed text but not a Bash output candidate. Publish the exact conditional marker rule and scope it to its actual caller so Studio can distinguish discovery from strict text parsing. This is missing S2 grammar/boundary information, not a demand for the later corpus.

4. **P2 — Previous-reference API applicability conflates workflow policy with accepted direct-API versions.** `plugins/workflow/reference_scanner_contract.py:163-168` publishes only `[6]` for `iter_loop_previous_output_references`. The unchanged API calls `_require_strict_reference_semantics` (`language_schema.py:477-484`), which accepts versions 3, 4, 5 and 6. A focused direct probe of `$LOOP_PREV.a.output` yielded the same token for all four versions. Workflow previous-output activation should remain v6-only; separately describe the direct API's accepted normalizers so consumers/adapters do not incorrectly reject historical invocations. This is an S6 caller-policy accuracy issue; do not alter the runtime API to fit the metadata.

## Compliant areas and quality assessment

- The complete projection derives its 38 fields and 16 grouped records from the existing inventory, preserves ordered leaves and container-major traversal, includes all root Phase-4 leaves, and retains the original scoped Phase-6 projection.
- Only v6 root strict-reference paths derive from the complete projection. Reader 3, the scanner field and expanded trusted limits are selected only for Archon v6. Seven historical byte baselines and the v6 intentional-delta checks directly cover preservation.
- Scanner function bodies/signatures and iterator code are unchanged in the inspected diff. New inventory annotations are inert for runtime traversal.
- The neutral builder imports only standard-library modules; cold-import publication tests cover the dependency boundary.
- Existing tests establish exact and overflow bounds and rejection of forged advertised limits. Reported canonical sizes are 315,819 total and 31,942 scanner bytes. The section fits its 32,000 bound but has only 58 bytes of headroom; corrections will need compact wording/data. This is a maintenance constraint, not an independent blocker or reason to raise approved limits.
- The 861-pass result is accepted as reported; the passing suites were not rerun. I ran `git diff --check` successfully and a read-only direct Python probe specifically to check the doubtful published Bash examples, candidate marker boundaries and previous-API versions. All declared Bash examples except the identified case example matched their published classification.

Task 3 literal publication/adapters, the later complete-branch review, and all Studio work remain outside this review. No files were modified except this scratch review report; no staging, commit, merge or subagent work was performed.

## Task 2: correction review

# Task 2 fix round 1 focused rereview

Verdict: **approved**. Each of the four original P2 findings is addressed, and the fix-only diff introduces no new Critical or Important issue.

Reviewed only the correction delta recorded in `task-2-fix-1-review-package.md`, against the four findings in `task-2-review.md`, the relevant unchanged scanner and validator branches, and the reported RED/GREEN appendix in `task-2-report.md`. This rereview does not reopen architecture, request Task 3 work, or assess unrelated unchanged code.

## Per-finding verdicts

1. **Wrong case-pattern classification — ADDRESSED.** The top-level `case value in $USER_MESSAGE) :;; esac` example now has an admitted rule, while the rejected command-grammar rule uses a nested case example that the classifier rejects. A focused direct probe observed `((14, 27, None),)` for the top-level case span and `BashRenderingError` for the nested example. The behavior-backed test now checks every published Bash example against candidate discovery and classification. Removing two redundant examples to remain within the approved section bound does not remove their state-family coverage: physical comments and backtick substitution remain represented by other examples.

2. **Gate/until native codes and dotted-key exception — ADDRESSED.** `native_by_scope` now separates `group-until` from `group-gate`. Until producer-schema and impossible-path failures publish `loop_group_scope_invalid`; gate publishes `output_reference_path_unsupported` for absent schema, `structured_output_field_impossible` for ordinary impossible paths, and the native `output_reference_path_unsupported` dotted-key exception. These declarations agree with the corresponding branches in `schema.py`. The new workflow-backed cases exercise all requested distinctions. The portable semantic-code table remains unchanged.

3. **Conditional candidate markers and Bash first-character filter — ADDRESSED.** The metadata now states that `.output` is unrestricted while `/output` and `\\output` require candidate end or one of `.[]/\\`, matching `_reference_like_candidate()` and `_output_reference_at()`. It scopes the first-character restriction to `iter_output_reference_candidate_spans` and records `iter_output_references` as unfiltered. Focused probes confirmed `$a.outputx` remains a Bash candidate, `$a/outputx` and `$a\\outputx` do not, and `$?a.output` is excluded by Bash discovery but raises `WorkflowReferenceSyntaxError(start=0)` in the strict text iterator. The earlier `!a` review example is not relied upon.

4. **Direct previous-reference API versions versus workflow activation — ADDRESSED.** `iter_loop_previous_output_references` now publishes normalizer versions 3, 4, 5, and 6 separately from `workflow_activation_versions: [6]`. Direct probes returned the same `OutputReferenceToken("a", (), 0, 19)` for all four accepted versions, while the workflow-level previous-output applicability remains v6-only.

## Fix quality and verification

The `field_defaults.phase4_only: false` compaction preserves the former projection semantics. The projection still has 38 fields: 19 retain explicit `phase4_only: true`, and the other 19 omit the field and resolve to false through the declared default. Inventory-driven field construction, ordering, scopes, and v6 root-path derivation are otherwise unchanged.

The added tests are tied to the corrected metadata and execute the underlying runtime APIs or validator paths rather than asserting only static literals. `git diff --check` passes for the reviewed paths. I accepted the reported fix-round GREEN of 863 passed and did not rerun that suite, per the rereview scope.

No new Critical or Important issues were found in the fix-only diff.

## Task 3: literal corpus and adapters

# Spec Compliance

- ✅ Task 3 is spec compliant within the controller's explicitly assigned scope. The static resource, independent manifest, fixed observation helper, conformance tests, and scoped Unicode ID-string amendments are present in the supplied review package. No runtime change appears in that package.
- ⚠️ Combined workflow applicability remains a Task 4 acceptance obligation: all interpolation surfaces, root Phase-4 fields, authored current/body/outer/previous scope diagnostics, competing workflow failures, include-v4 policy, and scheduler-v3 policy cannot be established from this Task 3 package alone. This is the controller's approved division of work, not a request for another scanner API.
- ⚠️ Manifest-first authoring chronology and the reported RED/GREEN execution history are historical evidence in `task-3-report.md`; the final diff cannot independently prove chronology. The reviewer did not regenerate expectations or rerun reported suites.

# Strengths

- The literal corpus covers the Task 3 state families with substantive inputs, rather than only family labels. `plugins/workflow/conformance/reference_scanner_v1.json:4` begins with the specified whole-output example; `:159` through `:297` explicitly cover x, underscore, slash, brackets, leading-zero, backslash, and hyphen suffixes. The exact first/drain pair appears at `:458` and `:485`.
- `tests/plugins/workflow/reference_scanner_observations.py:80` consumes lazy iterators inside the observation boundary and retains successful prefixes; `:123` explicitly dispatches fixed APIs and passes authored spans unchanged. Eager Bash results remain atomic. Independent adapter assertions at `tests/plugins/workflow/test_reference_scanner_conformance.py:88` and `:107` exercise late failure, eager failure, presence, and quote context.
- Caller distinctions are observable: narrow condition iterator versus both-operand parser (`reference_scanner_v1.json:925`, `:945`), v3 versus v6 previous-reference conditions (`:979`, `:1013`), ordinary-versus-dedicated previous APIs (`:1235`, `:3356`), pre-v6 ordinary Bash behavior (`:1341`), and boolean spans accepted by the text API but rejected by the classifier (`:810`, `:3323`).
- Previous-reference cases separately establish whole/path parsing, malformed producer/path behavior, authored-offset masking, first-iteration whole/field resolution, and sibling-over-outer selection (`reference_scanner_v1.json:1119`, `:1313`, `:3424`, `:3457`, `:3481`, `:3538`). Broader workflow producer applicability is correctly reserved for Task 4.
- Bash literals cover ordinary quotes, escape/comment/doubled-dollar suppression, physical continuations, malformed live/literal contrast, command/backtick/ANSI-C/parameter/arithmetic/legacy arithmetic/conditional/extglob/brace/process frames, mixed nesting, assignments/arrays/subscripts/declarations/functions/coprocess/case/redirection states, heredoc variants and here-strings. Quoted heredoc references correctly reject (`reference_scanner_v1.json:2161`); the 64/65 boundary and context-before-grammar precedence are explicit (`:2316`, `:2521`, `:2541`).
- Scalar cases cover all ten names, maximal names, positional boundaries and malformed-quoting fallback, paired text/Bash comment and escape behavior, all quote contexts, output/scalar overlap, nonrecursive replacement, outputs-only rendering, unsafe scalar context, and translated causes (`reference_scanner_v1.json:3571` through `:4015`). `reference_scanner_observations.py:193` uses secure Bash rendering in temporary storage and closes the rendered command at `:219`; it never starts a shell.
- Authenticated coverage uses the actual APIs rather than YAML-only compilation claims. `reference_scanner_observations.py:94` uses the approved source compiler for body-map validation, writes digest resource bytes with `bytes.fromhex` at `:113`, then loads a real snapshot at `:114`. It performs no resource-byte decoding. Ordered issue codes/paths and meaningful direct causes survive serialization at `:64`. Cases at `reference_scanner_v1.json:2836` through `:3212` cover version boundaries and the three specifically required existing byte examples; `:3384` checks authored issue order against reversed body-map insertion order.
- Static proof and runtime resolution have separate API identifiers and direct calls (`reference_scanner_observations.py:233`). Literals include containing type/ref siblings and target restrictions, union branches, both numeric interpretations, tuple/prefix/additional items, dotted keys, unresolved/cyclic/nonlocal refs, and runtime mapping/index/missing/schemaless behavior (`reference_scanner_v1.json:4053` through `:4849`). The fixture at `reference_scanner_observations.py:177` matches the existing immutable output constructor.
- Unicode profiles are explicit and verified against the interpreter's actual database (`reference_scanner_observations.py:51`). Kawi alphabetic cases genuinely distinguish Unicode 14 from 15/15.1 (`reference_scanner_v1.json:2730`, `:2757`). Digit cases intentionally have identical API outcomes across profiles; they should not be described as proving an output divergence. Astral offsets and the text-versus-condition NBSP boundary are explicit at `:2625` through `:2676`. Surrogate inputs remain outside published JSON (`test_reference_scanner_conformance.py:126`). The offline three-interpreter matrix fails missing prerequisites, compares all applicable observations, and checks unchanged publication bytes (`:157`).
- Resource tests verify global ID uniqueness, exact manifest membership, nonempty outcomes/applicability, declared families, and fixed API result variants (`test_reference_scanner_conformance.py:29`, `:68`, `:75`, `:82`). Unknown API names fail outside the observation boundary (`reference_scanner_observations.py:123`, `:193`, `:233`).

# Issues

## Critical

None.

## Important

None.

## Minor

1. `plugins/workflow/conformance/reference_scanner_v1.json:3571` and `:3603`: every recognized scalar receives the same value, `v`. These cases detect a missing replacement but cannot detect one name accidentally reading another scalar's field. Use ten distinct short literal values in both cases and corresponding literal expected strings. This strengthens the existing required witnesses without adding APIs or runtime behavior.
2. `tests/plugins/workflow/fixtures/reference_scanner/case_manifest.json:395` and adjacent entries `api.condition-v3-rhs-reference`, `api.condition-v6-previous`, and `api.condition-v3-previous` reuse the generic iterator-prefix/in-span evidence string. Their literal outcomes are correct, but that citation is indirect for condition parsing. Point these rows to `conditions.validate_v3_condition_syntax`, `conditions.validate_v6_condition_syntax`, and the existing `test_phase3_conditions.py:292` both-operand assertion, as appropriate. Similarly, the Unicode digit rows at `case_manifest.json:1688` and `:1699` currently cite the Kawi letter/isalpha rule rather than the redirection `isdigit` branches; cite `bash_rendering.py:1176`, `:1197`, or `:1214` as appropriate. No expected-output change is needed.

# Focused Checks and Evidence

- Reviewed the supplied package in bounded passes because the initial tool output was truncated. Recovered the omitted data by compacting the package's added JSON and reading its adapter/test portion. No diff was recalculated and no changed source file was separately reread.
- Named risk: authenticated-byte provenance and policy could be asserted without crossing the real boundary. Checked `tests/plugins/workflow/test_strict_output_references.py:865` through the specifically named byte test, `schema.py:2296` policy branches, and the `trust.py:517` surrogateescape call. The three required byte literals match the independently authored existing test inputs, and the adapter invokes the correct APIs.
- Named risk: mutable/misconstructed facets could invalidate resolution observations. Checked `test_strict_output_references.py:995` and `output_resolution.py:565`; constructor fields match and `__post_init__` freezes JSON values.
- Named risk: static ref/numeric cases could silently encode dropped containing constraints. Checked `schema.py:1639`, `:1653`, and `:1680`; the independently reasoned literals retain containing/target restrictions and require both numeric interpretations to be impossible.
- Named risk: Unicode alphabetic/digit profile cases might claim unsupported divergence. Checked `bash_rendering.py:790` declaration-flag handling and `:1164` onward redirection handling. Alphabetic expectations distinguish the actual branch; the digit pair correctly retains equal observable outcomes.
- Named risk: historical condition scanning could be confused with validation or presence. Checked `language_schema.py:526`, `:543`, `conditions.py:290`, `:297`, and `test_phase3_conditions.py:292`. The literal API distinctions match the implementations and existing assertion.
- Verified cited existing grammar, Bash admission, quoted-offset, case-action, in-span, and scalar-overlap test names exist via focused searches. No broad architecture investigation, scanner-driven expected generation, suite rerun, production/test write, staging, or commit was performed.
- Reported verification retained from implementer evidence: required focused suite 1,957 passed; final conformance/contract/baseline run 222 passed; final manifest-location conformance run 190 passed, including all supported Python profiles. This review does not claim to have independently rerun those commands.

# Assessment

**Task quality: Approved.**

The fixed observation boundaries are faithful, the reviewed literal outcomes are coherent with the requirements and focused independent source assertions, and the package introduces no runtime semantics. The two minor items improve witness sensitivity and traceability; neither blocks the Task 3 gate. Final branch verification, Task 4 integration coverage, and the user's requested final commit remain controller-owned work.

## Task 3: literal refinements

# Spec Compliance

✅ Approved. Both previously reported Minor findings are addressed by the supplied focused data/evidence delta.

# Findings Closure

- **Addressed — distinct scalar witnesses:** `plugins/workflow/conformance/reference_scanner_v1.json:3585` and `:3617` now assign ten distinct short scalar values. The corresponding prompt and Bash expected strings preserve the authored name order, with only the Bash command prefix added. These cases can now expose substitution from the wrong scalar field. The values require no additional Bash escaping and remain below spill thresholds.
- **Addressed — precise manifest provenance:** `tests/plugins/workflow/fixtures/reference_scanner/case_manifest.json:395`, `:406`, and `:417` now identify the v3/v6 condition validators, previous-reference allowance, source-order behavior, and exception translation. Adjacent condition rows also identify their actual iterator rules. The digit rows at `:1655`, `:1688`, and `:1699` cite the ordinary-redirection `str.isdigit` branch; Kawi digit evidence explicitly preserves equal observable outcomes across Unicode profiles instead of claiming a divergence.
- **Open findings:** None.

# Verification Evidence

- Reviewed only `task-3-fix-1-review-package.md` and `task-3-fix-1-report.md` against the two original Minor findings. No full review, source reread, suite rerun, or expected-result generation was performed.
- The implementer's focused report records `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_conformance.py -q`: 190 passed, no failures or skips, including all three supported Python/Unicode profiles. This reviewer did not rerun that command.
- The supplied package changes literal scalar values/expectations and manifest evidence only; it contains no runtime or adapter changes.

# Assessment

**Task quality: Approved.** Both minor findings are closed. The Task 3 approval stands, with combined Task 4 coverage and final branch verification remaining controller-owned obligations.

## Task 4: corpus publication

### Spec Compliance

- ❌ Issues found: trusted format validation is not exact, the promised validation precedence is bypassable by serialization, and the existing two-limit `emit_authoring_json` behavior regressed. See Important findings below.
- ✅ Verified from the scoped diff and focused existing-code checks: legacy corpus bytes remain protected by the exact 11-case/7,265-byte/SHA-256 fixture assertion (`tests/plugins/workflow/test_reference_scanner_baselines.py:69`); all six new workflow literals are appended only for Archon (`plugins/workflow/language_conformance.py:1570`), independently indexed with provenance (`tests/plugins/workflow/fixtures/reference_scanner/case_manifest.json:2089`), and executed against the real parser with exact ordered diagnostics (`tests/plugins/workflow/test_language_conformance.py:820`).
- ✅ Include v4/authenticated-body and scheduler v3 continuity are separate executable proofs at `tests/plugins/workflow/test_phase4_references.py:122`, `tests/plugins/workflow/test_phase4_references.py:367`, and `tests/plugins/workflow/test_phase3_bash_reference_ordering.py:114`; they are not treated as YAML-only corpus execution.

### Strengths

- `plugins/workflow/language_conformance.py:1598` loads the reviewed literal resource with `importlib.resources`, copies only its three fixed sections, and computes the self-excluding digest once at `plugins/workflow/language_conformance.py:1609`. The focused tests at `tests/plugins/workflow/test_language_conformance.py:178`, `:193`, and `:209` cover binding, nested mutation isolation, and absence of runtime scanner calls.
- The six S6/S7 workflow literals cover root Phase-4 containers, current/outer/previous scopes, multigroup isolation, competing-error precedence, and body-before-control ordering; the generic authority comparison at `tests/plugins/workflow/test_language_conformance.py:820` validates their exact runtime outcomes rather than merely checking IDs.
- Archon section limits and canonical/emitted byte ceilings are centralized in `plugins/workflow/schema_cli.py:19`, and successful validation completes before the sole `print` at `plugins/workflow/schema_cli.py:146`.

### Issues

#### Critical (Must Fix)

None.

#### Important (Should Fix)

- `plugins/workflow/schema_cli.py:51`: `format_version not in (1, 2)` uses Python numeric equality, so `1.0` and `2.0` are accepted as trusted formats despite not being integer format identifiers. A focused probe confirmed both pass validation and a `2.0` corpus is emitted. Require an exact integer type before selecting limits, and add malformed numeric-version coverage beside `tests/plugins/workflow/test_cli.py:1320`.
- `plugins/workflow/schema_cli.py:115`: `emit_authoring_json` calls `json.dumps` at line 117 and canonicalizes at lines 128-130 before `_validate_schema_corpus_payload` runs. Consequently, an unknown format, wrong profile/format pair, or invalid section shape containing a non-serializable value raises `TypeError` instead of the required explicit format/profile/section error. Focused probes reproduced all three cases. Split structural validation from byte validation so format/profile and section shape/count checks run first, then canonical size, then emitted size, before stdout; extend the precedence tests at `tests/plugins/workflow/test_cli.py:1286` and `:1320` with payloads that would fail if inspected or encoded early.
- `plugins/workflow/schema_cli.py:124`: supplying both existing optional limits now unconditionally invokes schema-corpus format validation. Before this task, `emit_authoring_json(args, producer, max_cases=64, max_bytes=160_000)` accepted a producer returning `{"cases": []}`; it now raises `unsupported format_version: None`, although the callable signature is unchanged. A focused probe confirmed the regression. Preserve the generic helper's original two-limit semantics and invoke the version-specific corpus validator from `emit_schema_corpus` (`plugins/workflow/schema_cli.py:155`), or add an explicit opt-in that leaves existing callers unchanged.

#### Minor (Nice to Have)

None.

### Assessment

**Task quality:** Needs fixes

**Reasoning:** Corpus publication, literal workflow coverage, legacy preservation, digest isolation, and the separate include/scheduler proof are sound. The CLI gate needs focused fixes before Task 4 can be trusted because malformed formats can be accepted, required error precedence can be preempted by serialization, and the shared helper's prior API behavior changed.

### Focused Checks

- No reported green suite was rerun.
- Direct read-only Python probes confirmed acceptance/emission of float format versions, serialization preempting all three structural errors, and the two-limit public-helper regression.

## Task 4: CLI corrections

### Finding Verdicts

- **Float format versions were accepted as trusted corpus formats** — ADDRESSED. `plugins/workflow/schema_cli.py:48` now requires `type(format_version) is int` before accepting formats 1 or 2, and `tests/plugins/workflow/test_cli.py:1336` covers both `1.0` and `2.0` through the real corpus emitter with empty stdout on failure.
- **Serialization could preempt required format/profile/section errors** — ADDRESSED. `plugins/workflow/schema_cli.py:155` runs `_validate_schema_corpus_structure` before canonicalization at line 158 or JSON encoding at line 159. `tests/plugins/workflow/test_cli.py:1389` exercises unknown-format, wrong-profile, and invalid-section payloads containing unserializable objects; the strengthened count-precedence case also contains an unserializable member.
- **The generic two-limit `emit_authoring_json` contract regressed** — ADDRESSED. `plugins/workflow/schema_cli.py:123` again applies `max_cases` and `max_bytes` independently without requiring corpus metadata, while version-specific validation is confined to `emit_schema_corpus` at line 151. `tests/plugins/workflow/test_cli.py:1519` proves a generic `{"cases": []}` producer with both limits still emits successfully.

### New Breakage in the Fix Diff

None. The new private encoder preserves the existing compact/pretty settings, structural and byte gates remain before corpus stdout, and the fix does not touch publication or corpus artifacts.

### Out-of-Scope Observations

None.

### Verdict

**Fix round:** All findings addressed, no new Critical/Important breakage.

### Checks

- Reviewed only `task-4-fix-1-review-package.md` and the appended fix report; no suite was rerun.
- The report records the expected 6-test RED, 135-test affected-file GREEN, final 7-test precedence GREEN, clean static checks, and unchanged legacy/Archon/resource hashes.

## Task 5: offline packaging and documentation

### Spec Compliance

- ✅ Spec compliant. The package-data declaration explicitly includes the literal scanner JSON without changing dependency policy (`pyproject.toml:657`), while the artifact test compares reviewed source bytes across the direct wheel, sdist, and sdist-built wheel (`tests/plugins/workflow/test_installed_distribution_e2e.py:427`).
- ✅ The fixture builds wheel and sdist from a temporary source copy, safely extracts only contained regular files/directories, builds the downstream wheel from that extraction, and exercises both artifacts through offline/no-index installers (`tests/plugins/workflow/test_installed_distribution_e2e.py:95`, `tests/plugins/workflow/test_installed_distribution_e2e.py:223`, `tests/plugins/workflow/test_installed_distribution_e2e.py:464`).
- ✅ The real installed-console proof removes `PYTHONPATH`, runs outside the checkout, proves module/scanner/README origins under each artifact-specific venv, preserves the Jira resource assertion, demonstrates both guarded socket APIs, and compares all 16 installed command outputs with source output for both profiles, both publications, and compact/pretty modes (`tests/plugins/workflow/test_installed_distribution_e2e.py:464`).
- ✅ Canonical encoding, contract/corpus digest omission rules, reader/normalizer versions, legacy exact bytes/digest, and profile-specific canonical/pretty limits are independently asserted (`tests/plugins/workflow/test_installed_distribution_e2e.py:488`).
- ✅ The packaged README accurately documents reader 3, corpus format 2, CLI modes, literal authority, digest rules, Unicode applicability, unsupported capability rejection, unchanged Python behavior, separate Studio delivery, the seven historical versions, and the exact legacy corpus guarantee (`plugins/workflow/README.md:9`, `plugins/workflow/README.md:27`, `plugins/workflow/README.md:46`, `plugins/workflow/README.md:55`).
- ✅ The evidence-based RED is valid: the recorded baseline archive inspection found JSON already shipped through the existing manifest graft, while the absent README caused the genuine publication failure; the implementation neither manufactures a missing-JSON failure nor weakens existing packaging behavior (`.superpowers/sdd/2026-09-04-declarative-reference-scanner/task-5-report.md:31`).

### Strengths

- The archive boundary is handled carefully: absolute paths, traversal, links, and special members are rejected before extraction (`tests/plugins/workflow/test_installed_distribution_e2e.py:95`).
- The installation proof is positive rather than inferred: each wheel gets its own venv, imported resources are located under that venv, source bytes and digests are compared, and checkout/legacy extraction origins are rejected (`tests/plugins/workflow/test_installed_distribution_e2e.py:464`).
- The socket guard is both behaviorally probed and origin-checked before the same venv console is exercised, giving credible offline execution evidence without production changes (`tests/plugins/workflow/test_installed_distribution_e2e.py:579`).
- The README is concise and precise about compatibility boundaries and canonicalization (`plugins/workflow/README.md:21`, `plugins/workflow/README.md:34`).

### Issues

#### Critical (Must Fix)

- None.

#### Important (Should Fix)

- None.

#### Minor (Nice to Have)

- None.

### Assessment

**Code quality: Approved**

**Task quality: Approved**

**Reasoning:** The implementation is scoped, readable, and directly tests the distribution and execution guarantees required by Task 5. Reported verification is complete and clean; no suite was rerun because the review raised no specific unanswered doubt.
