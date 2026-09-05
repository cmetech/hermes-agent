# Workflow language publication

Hermes is the authority for the workflow language. The workflow plugin publishes
the versioned authoring contract and conformance corpus that offline consumers
use to implement compatible editors and validators. Publication does not change
the existing Python scanner, accepted workflow syntax, validation precedence, or
runtime behavior. Workflow Studio consumption is delivered separately.

## Reading the publications

The installed CLI emits either compact canonical JSON with `--json` or
human-readable pretty JSON without it:

```console
hermes workflow schema --profile hermes-legacy --json
hermes workflow schema --profile archon-2026-07 --json
hermes workflow schema-corpus --profile hermes-legacy --json
hermes workflow schema-corpus --profile archon-2026-07 --json
```

The `schema` command publishes the authoring contract. The amended Archon v6
contract keeps schema version 1, editor projection version 2, and normalizer
version 6, and requires contract reader version 3. The `schema-corpus` command
publishes Archon conformance format 2, including the workflow cases and the
scanner, substitution, and structured-path sections.

A consumer must reject reference-scanner capability activation when it cannot
interpret reader 3, any required `reference_scanner_v1` section, or an applicable
Unicode profile. Unsupported capability data is not permission to approximate
the Python scanner.

## Literal corpus and digests

`conformance/reference_scanner_v1.json` is the reviewed literal authority for
scanner, substitution, and structured-path inputs and expected results. Hermes
loads it with `importlib.resources.files("plugins.workflow")`; the corpus
publisher serializes those values and never runs the scanner to create expected
results.

Canonical JSON is UTF-8 JSON with object keys sorted, Unicode emitted directly,
and separators `,` and `:` without additional whitespace. A contract's
`contract_digest` is `sha256:` plus the SHA-256 of its canonical object before
the `contract_digest` field is added. Archon corpus format 2 uses the same rule
for `corpus_digest`, omitting only `corpus_digest` from the hashed object. The
corpus binds the contract digest; consumers should pin and verify both values.
Pretty output is an alternate serialization of the same JSON value, so digest
verification always uses the canonical form rather than the displayed bytes.

The seven historical authoring versions remain byte-for-byte fixed: Hermes
legacy normalizers 1 and 2, and Archon normalizers 1, 2, 3, 4, and 5. The legacy
corpus also remains exactly format 1 with 11 cases, 7,265 canonical bytes, and
SHA-256 `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b`;
it has no `corpus_digest` or scanner-extension sections.

## Unicode applicability

The scanner contract publishes explicit profiles for supported Python runtimes:

- `python-3.11-unicode-14.0.0`
- `python-3.12-unicode-15.0.0`
- `python-3.13-unicode-15.1.0`

Cases marked `all` apply to every supported profile. Distinguishing cases name
their applicable profile IDs, and a consumer must select one explicitly. An
unknown profile is unsupported. Published inputs contain Unicode scalar values;
authored offsets are half-open Unicode code-point ranges. Lone-surrogate behavior
is retained only as direct Python API characterization, outside the portable
published-input contract.
