# Declarative reference-scanner compatibility amendment

Status: architecture approved in conversation; replacement specification recorded for implementation planning.

## Authority and delivery boundary

Hermes remains the workflow-language authority and continues to execute its existing Python scanner. This amendment publishes the information and literal examples needed for a faithful Workflow Studio TypeScript implementation. It changes authoring metadata and conformance publication, not accepted workflow syntax or runtime behavior.

This specification supersedes the September 4 `workflow-reference-scanner-contract-design.md` and its implementation plan. Those files and the three adversarial reviews and feasibility record are historical evidence only. No fourth-review record was found. Do not implement `reference_scanner_vm.py`, an interpreter, bytecode, a transition program, instruction accounting, cross-language memory ownership, or prepared VM sessions.

Work exclusively in the existing Hermes worktree `.worktrees/workflow-reference-scanner-contract`, branch `feat/workflow-reference-scanner-contract`. The starting commit is `74fed08f91014ca7fc80ee9ea4427568ec95b04f`. Preserve unrelated changes and historical documents. Do not modify another worktree, literal `main`, or Workflow Studio. After complete Hermes verification and adversarial review, commit the feature and stop for explicit user approval before merging into `base` or starting Studio work. No automatic push or merge is part of implementation.

## Architecture

There are three deliverables:

1. The amended Archon v6 authoring contract publishes `reference_scanner_v1`, a compact description of the scanner's observable rules and its complete interpolation inventory.
2. The Archon conformance envelope publishes independently reviewed literal cases with fixed inputs and expected results. Its publisher reads and serializes data; it never executes the scanner to obtain expectations.
3. Test-only Python adapters execute the existing APIs against those cases. Studio later implements equivalent TypeScript functions and consumes the same corpus.

The contract describes rules, modes, caller policies, and diagnostic applicability. It is not a program. Tests call a fixed set of real APIs directly; case data cannot define operations, control flow, expressions, callbacks, or arbitrary Python imports. Existing state-machine code in `bash_rendering.py` remains intact.

## Historical preservation and versioning

The immutable authoring pairs are legacy normalizers 1 and 2, and Archon normalizers 1 through 5. Freeze their full canonical JSON bytes before publication changes. Preserve reader version 2, projection version 2, all limits, and embedded digests for those pairs.

The old Archon v6 contract is an intentional-delta characterization, not an eighth immutable contract: 283,522 canonical bytes, SHA-256 `171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc`. Its definition and companion schemas and executable-node semantics must remain unchanged except authoring reference metadata explicitly covered here.

Only amended Archon v6 advertises contract reader version 3. Keep envelope schema version 1, editor projection version 2, and normalizer version 6. A reader incapable of interpreting required scanner metadata must reject capability activation. Raising a global reader-version constant must not rewrite historical envelopes.

Legacy conformance stays byte-identical: format version 1, 11 workflow cases, 7,265 canonical bytes, SHA-256 `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b`. Do not add a digest field or new case sections to that envelope.

Archon conformance advances to format version 2, preserving existing workflow cases and adding the literal compatibility sections below. The contract digest does not depend on the corpus. The corpus includes the contract digest and `corpus_digest`, calculated over canonical corpus JSON with only `corpus_digest` omitted. Consumers pin both digests. Hashes detect artifact changes; covered behavior tests and differential testing detect semantic changes. Neither hashes nor a finite corpus establish equivalence for every possible input.

## Declarative metadata

`reference_scanner_v1` contains these required fields, whose values are covered by contract and behavior tests:

| Field | Meaning |
|---|---|
| `version` | Integer 1 |
| `applicability` | Archon v6 publication and explicit historical caller-policy applicability |
| `grammar` | Existing node/path patterns; ordinary and previous-reference spelling; named and positional scalar forms |
| `boundaries` | Text and Bash candidate endings, reference-like candidate detection, complete-reference suffix rejection, adjacent-dollar behavior |
| `offsets` | Half-open authored Unicode code-point ranges; replacement text does not change reported positions |
| `unicode_profiles` | Explicit supported Python/Unicode predicate semantics and applicability |
| `modes` | Text, Bash, and condition caller policies; literal/admitted/rejected state families |
| `scalars` | Recognized names, maximal matching, overlap, text/Bash differences, argument splitting, non-recursive substitution |
| `callers` | Named existing API policies, versions, scan order, laziness, and failure translation |
| `diagnostics` | Stable native identifiers, portable mappings, and scope/caller applicability |
| `interpolation_surface` | All inventory-derived surfaces, discriminator metadata, applicability and traversal order |

Keep metadata dependency-neutral in `reference_scanner_contract.py`: standard-library data assembly only, no runtime scanner imports. `language_schema.py` supplies grammar constants and the derived inventory. Declarative scalar and Bash descriptions are checked against the existing implementations in tests; do not move scanner logic or introduce a second handwritten workflow field inventory.

The amended envelope has a 328,000-byte total bound with 4,000 bytes reserved, and a 32,000-byte bound on `reference_scanner_v1`. Existing section limits remain unchanged. These are publication bounds, not new runtime input limits. Measure actual compact bytes and record remaining headroom in delivery evidence. Historical envelopes retain their original bounds and boundary behavior.

### Grammar and boundaries

Node identifiers use `[A-Za-z_][A-Za-z0-9_-]*`. A dotted output-path segment uses `(?:[A-Za-z_][A-Za-z0-9_-]*|0|[1-9][0-9]*)`. Ordinary references spell `$ID.output(.path)*`; previous references spell `$LOOP_PREV.ID.output(.path)*`.

Publish the exact existing candidate delimiter characters, not a host-language whitespace approximation. Bash candidates additionally stop before a following dollar. Preserve the distinction between candidate discovery, lexical admission, strict parsing, and complete-reference discovery. Complete references reject ASCII continuation characters and every non-ASCII continuation as the existing implementation does. The corpus must include `.outputx`, `.output_`, `.output/path`, `.output[0]`, `.output.01`, backslash and hyphen suffixes, invalid producer spellings, nested dollars, and Unicode continuations.

Previous references are discovered through their dedicated API and masked with equal-length spaces before ordinary parsing. Text, Bash, and renderer callers do not necessarily discover previous/current candidates in the same way. Export those policies; do not replace them with one generalized scan.

### Inventory and traversal

Derive from `_INTERPOLATION_SURFACE_INVENTORY`: 18 root fields, the same 18 body fields, and `loop_group.until_bash` plus `loop_group.gate_message`. Include root `systemPrompt`, agents' description/prompt, and all four hook response leaves.

Each projected field carries its scope, node types, scanner mode/caller policy, authored-value role, authenticated-body source where applicable, Phase-4 gating, and inline-script discriminator reference. Add scanner annotations to the existing inventory records if needed; runtime consumers must behave identically when ignoring those new fields.

Retain the inventory grouping and `ordered_leaf_paths` information. Traversal is container-major: visit each agent or hook response container in authored order and then its ordered leaves. Flattened field paths alone do not authorize leaf-major traversal. Literal resource names are not templates; authenticated bodies are separate inputs and are scanned only by applicable callers.

Keep the existing 20-field scoped projection as an explicitly scoped view derived from the same inventory. The new 38-field projection is the complete capability inventory. Update only the v6 root strict-reference descriptor's paths from the root projection so it includes Phase-4 extensions. Historical strict-reference descriptors remain byte-identical.

### Bash and scalar rules

Describe the existing classifier's admitted, literal, and rejected situations with named rules and concrete examples. Do not pretend that it is a general Bash parser. Include unquoted/single/double quotes; physical continuations; escapes and dollar doubling; comments; command and backtick substitution; ANSI-C quoting; parameter expansion; arithmetic and legacy arithmetic; conditionals; extglobs; braces; arrays and subscripts; declaration flags; assignments; command position; functions; coprocesses; case syntax; redirections; heredoc delimiters and bodies; here-strings; unterminated/ambiguous states; and the existing nesting boundary.

Preserve lexical admission before malformed-reference parsing. A shell string with no candidate references can still fail classifier validation. Live references in quoted heredoc bodies or delimiters can reject; quoted heredoc bodies must not be broadly labeled literal.

Scalar grammar is the existing maximal positional-or-uppercase-name match. Publish all ten recognized names. Text substitution does not acquire Bash comment or escape suppression. Bash substitution must preserve quote context, overlap handling, and rejection of unsafe scalar contexts. Positional arguments retain the existing `shlex.split` with plain-split fallback. Replacement bytes are never rescanned as authored references.

### Callers, diagnostics, and structured paths

Cover `iter_output_references`, `iter_loop_previous_output_references`, candidate discovery, parsing admitted spans, `contains_output_reference`, the historical `iter_when_output_references`, `validate_v3_condition_syntax`, `validate_v6_condition_syntax`, the Bash classifier and its ordinary/previous wrappers, and `StrictSubstitutionRenderer`'s public substitution paths.

The historical condition iterator is narrower than condition validation, which can return both reference operands. The resource-presence predicate must find valid references before or after malformed candidates. Authenticated named-script rejection is version-specific, including decoded invalid bytes. Preserve include rewriting's v4 policy and scheduler authenticated preflight's v3 policy.

Use portable scoped diagnostics already published by Hermes. Ordinary missing current/outer dependency failures are not automatically unknown-producer failures. Preserve sibling-over-outer shadowing, gate/termination policies, first-iteration previous-output behavior, native branch-local codes, exception causes, and which failure appears first. Diagnostic prose is not newly promoted to a stable public identifier.

Structured-path cases exercise the existing schema proof and resolution: containing constraints, `$ref` siblings, `allOf`, `anyOf`, `oneOf`, numeric object keys versus array indices, tuple/prefix items, additional properties/items, dotted keys, and conservative unknowns for unresolved/cyclic/nonlocal references. Both numeric interpretations must be impossible before a path is rejected. Do not invent a new schema evaluator or drop constraints when resolving references. Runtime values and static schema proofs are separate case families.

### Unicode

Publish deterministic profiles for the Python versions supported by `pyproject.toml` (3.11, 3.12, 3.13), describing their Unicode database and the predicates actually observed by the scanner: alphabetic, digit, alphanumeric and whitespace where the caller uses them. Common cases apply to all profiles; distinguishing cases have explicit profile applicability and independently reviewed outcomes. The publishing interpreter must not select or calculate expected results.

Verification must execute the profile-sensitive cases under each supported interpreter and verify its Unicode database identity. If that matrix exposes an unmodeled difference, correct the metadata and literal profile cases; do not change runtime classification to force uniformity. An unknown profile is unsupported for Studio capability activation. Use Unicode standard property definitions in metadata; no bytecode, interpreter or cross-language memory model follows from this requirement.

Published JSON inputs use valid Unicode scalar values. Test unchanged direct Python API behavior for lone surrogates separately; do not add surrogate rejection to the runtime scanner. Store authored code-point offsets in corpus results. UTF-16 conversion belongs only at Studio's editor boundary.

## Literal conformance data

Store the reviewed static data in `plugins/workflow/conformance/reference_scanner_v1.json`, loaded through `importlib.resources.files("plugins.workflow")`. It contains `scanner_cases`, `substitution_cases`, and `structured_path_cases`. Every case has a unique stable `id`, sorted requirement tags, applicable Unicode profile IDs, an explicit API/policy identifier and normalizer version, literal input, and literal expected result. A separately reviewed manifest maps IDs to requirements and source test evidence; it is not computed by discovering cases at runtime.

Scanner cases use this result shape:

```json
{
  "id": "text.whole-output",
  "requirements": ["S2"],
  "unicode_profiles": ["all"],
  "api": "iter_output_references",
  "normalizer_version": 6,
  "input": {"text": "$build.output", "consume": "all"},
  "expected": {
    "tokens": [{"node_id": "build", "path": [], "start": 0, "end": 13}],
    "error": null
  }
}
```

`consume` is either `first` or `all` for iterator APIs. Eager APIs always consume all. Candidate-span APIs return literal spans; classifiers return literal spans plus quote contexts; predicates return booleans; conditions return the API's reference records. Expected failures specify exception class, stable code when present, authored start when present, producer/path when present, and meaningful cause class/code. Iterator results retain the tokens emitted before failure. This is fixed test result serialization, not an execution protocol.

Substitution cases provide text, scalar values, immutable output/previous-output values, direct dependencies and mode; results are literal rendered text or structured failure. Use public text/Bash substitution methods without launching a shell or providers. Shell execution/spill tests remain in their existing suites. Structured-path cases provide literal schemas/paths or immutable output facets and expect static proof/resolution outcomes. Workflow cases retain their existing YAML and diagnostic shape.

Authenticated-resource policy uses fixed variants in `scanner_cases`, because ordinary YAML workflow compilation does not inspect authenticated bodies. The `validate_authenticated_resource_references` variant supplies literal definition/companion YAML, normalizer version and command/named-script body maps; its adapter compiles the package and invokes that exact existing API. The `compute_package_digest` variant supplies literal YAML plus relative resource paths and hexadecimal resource bytes, materialized only inside a real temporary package. Invoke the existing digest API without decoding bytes in the adapter, so the runtime's existing `surrogateescape` behavior is exercised without placing surrogates in published JSON. Expected failures include ordered `WorkflowValidationError.issues` (native code, authored path and meaningful diagnostic fields) and meaningful causes. Success results retain validated body maps or the applicable digest outcome. These fixed adapters accept a temporary-directory argument; case data cannot invoke arbitrary filesystem actions.

Publication limits: 64 workflow cases, 256 scanner cases, 64 substitution cases, 64 structured-path cases, 384,000 canonical bytes, and 768,000 emitted pretty-JSON bytes. Enforce all section counts and both byte bounds before printing any stdout. These generous but finite artifact limits do not become scanner runtime limits. Keep the legacy emitter's existing limits. Select cases by state-family and interaction coverage, not by filling a quota.

The manifest must cover at least these families:

| Family | Required distinguishing cases |
|---|---|
| Text grammar | whole/path, repeated, punctuation, malformed suffixes listed above, absent reference, embedded dollars, valid beside malformed |
| Previous outputs | whole/path, malformed producer/path, current overlap, equal-length masking, first iteration, body producer policy |
| Iterator/API behavior | first versus drain, prefix tokens before failure, invalid version including booleans, invalid/overlapping span inputs, narrow condition iterator versus parser |
| Bash ordinary quotes | bare, single, double, escaped, comments, dollar doubling, continued lines, malformed-but-literal versus malformed-live |
| Bash frames | each substitution/arithmetic/conditional/extglob/brace state, nested mixed frames, command positions and unterminated inputs |
| Bash words | assignments, arrays, integer declarations and Unicode flags, functions, coprocesses, case arms and redirections |
| Bash input operators | quoted/unquoted/tab-stripped/multiple heredocs, delimiter versus body references, here-strings, missing terminators |
| Bash boundaries | no-candidate rejection, nesting at/beyond 64, adjacent current/previous/scalar references, precedence between context and grammar failures |
| Scalars | each recognized name, unknown/maximal name, positional 1/10/0/leading zero, text comment/escape behavior, Bash quote contexts, overlap and non-recursive replacement, malformed argument quoting |
| Schema paths | constraints/ref siblings/unions, numeric keys and indices, impossible versus unknown, dotted keys and static/runtime distinctions |
| Workflow integration | all surfaces, root Phase-4 extensions, body/current/outer/previous scopes, competing failures, authenticated body policy, include and scheduler policy versions |
| Unicode | astral offsets, non-ASCII boundaries, alphabetic/digit/whitespace profile distinctions, direct-API surrogate characterization |

Create expectations from the reviewed semantics and existing independently asserted tests. Do not run a scanner and paste its output into a fixture as the expectation. A mismatch requires systematic debugging of the case, adapter and current source. A discovered runtime defect or proposed syntax change is escalated separately; this amendment does not repair it silently.

## Requirements-to-tests traceability

This matrix consolidates the known Studio Task 6 findings and historical review lessons. Test names are implementation targets; Studio rows are acceptance obligations for its separately approved stage.

| ID | Requirement | Hermes proof | Later Studio proof |
|---|---|---|---|
| H1 | Historical bytes | `test_reference_scanner_baselines.py`: seven byte fixtures; exact legacy corpus; v6 intentional delta | Pinned artifacts and historical activation |
| S1 | Complete activation metadata | Required metadata shape, scalar/grammar parity, version/digest checks | Delete/alter each required section and reject activation |
| S2 | Exact malformed boundaries | Literal scanner cases and candidate/strict/presence adapters | Run identical corpus |
| S3 | Bash states and precedence | State-family manifest, classifier/wrapper cases, existing Bash suites | Corpus plus seeded Python/TS differential tests |
| S4 | Structured-path constraints | Literal static-proof and runtime-resolution cases | Same cases; constraint-preserving memoized proof |
| S5 | 18 root + 18 body + 2 controls | Inventory-derived parity and container-major traversal tests | Complete applicability-aware surface traversal |
| S6 | Root Phase-4 fields | Root systemPrompt/agents/hooks workflow cases | Matching root diagnostics |
| S7 | Correct scope diagnostics | Current/outer/previous and competing-error workflow cases | Identical portable code, path and precedence |
| S8 | Index once | Corpus includes multi-group and multi-surface cases | Instrument contract/document index construction and reuse |
| S9 | Performance | Existing operation-count/performance tests; dense scanner cases | 250 nodes/500 edges per scope; no parsing/validation/layout/Git/I/O in pointer frames |
| S10 | Unicode offsets | Profile matrix, astral offsets, direct-API characterization | Code-point internals; UTF-16 only at editor boundary |
| H2 | Public APIs/scalars/laziness | First/drain, overlap, masking, caller/version, cause-chain cases | Equivalent scanner APIs used by validation |
| H3 | Independent corpus/drift | Static literals, manifest coverage, deterministic hashes; publisher cannot execute scanner | Pin pair, corpus and adversarial differential tests |
| H4 | Offline packaging and CLI | Wheel/sdist checks, actual installed resource/CLI proof, no execution-time network | Bundled offline artifacts |
| H5 | Runtime continuity | Full workflow and repository suites, include v4, scheduler preflight v3, no scanner-source changes | Preserve runtime-availability advisories |

## Packaging and verification

Add the conformance JSON to setuptools package data. Test both the direct wheel and source distribution contents. Extract the sdist in a temporary directory, build a wheel from it, and actually install that wheel into a temporary environment. Execute the real installed `hermes workflow schema` and `schema-corpus` commands outside the checkout without `PYTHONPATH`; verify module/resource origins and byte parity with source publication. Block network calls during installed execution and demonstrate that the block is effective. Cached build dependencies may be provisioned before the offline execution proof; missing prerequisites are reported, never silently skipped.

Use `scripts/run_tests.sh` for every pytest invocation, including explicit integration-marker selection for installation tests. Run focused tests, the complete workflow suite, and the complete repository suite. Record exit status, passed/failed/skipped totals and any environment limitations. Preserve the existing Bash security/spill, include, scheduler and performance suites. Passing scanner tests does not establish Studio's deferred canvas performance contract.

## Review and execution

The architecture received one independent review before this specification. Its three important clarifications are incorporated: caller-specific policies, supported-Python Unicode applicability, and source-distribution installation proof. Do not restart serial architecture reviews.

Write one complete implementation plan and review it once against this specification and the matrix. Use TDD for missing publication behavior: observe the failing test, implement the smallest change, then verify. Historical/runtime characterization tests can pass immediately; do not deliberately break an unchanged scanner to manufacture a red test. Use subagent-driven implementation and focused task reviews. Finish with one adversarial review of the complete branch, required verification, and a feature-branch commit. Report the Hermes delivery in plain language and wait for the user's separate merge/Studio approval.
