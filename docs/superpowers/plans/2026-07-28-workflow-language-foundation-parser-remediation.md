# Workflow Language Foundation Parser Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the customization ledger checker's heuristic JavaScript, TypeScript, and Markdown scanners with a bounded, fail-closed parser service while preserving committed-revision authority and exact overlap decisions.

**Architecture:** Python remains the sole authority for Git revisions, manifest validation, changed ranges, overlap classification, acknowledgement carry-forward, and report publication. A new repository-local Node helper accepts versioned NDJSON batches containing base64-encoded committed bytes and returns parser-derived UTF-8 byte spans; one invocation-scoped Python resolver batches and caches those results for both manifest and overlap paths. The merge gate provisions the exact pinned root parser dependencies before it invokes the checker, and no heuristic JS/TS or Markdown fallback remains.

**Tech Stack:** Python 3, Git object plumbing, Node.js ESM, NDJSON, TypeScript 6.0.3 compiler API, unified 11.0.5, remark-parse 11.0.0, micromark 4.0.2, pytest, Bash, npm lockfile v3.

## Global Constraints

- Start from and preserve the frozen remediation boundary `b6aaa21f3c6a1e29632ad559d157e82327fd0a95`; perform implementation only on `fix/workflow-language-foundation-parser-remediation`.
- Do not edit unrelated production code, dispatch Task 10, or mutate the original `fix/workflow-language-foundation-remediation` branch.
- Python remains authoritative for Git/revision/path/manifest/changed-range/overlap/decision/report behavior.
- Pin exact root dev dependencies: `typescript` `6.0.3`, `unified` `11.0.5`, `remark-parse` `11.0.0`, and `micromark` `4.0.2`.
- The Node helper uses versioned NDJSON on stdin/stdout, receives exact committed bytes as base64, and returns half-open UTF-8 byte spans.
- JavaScript and TypeScript searchable syntax includes identifiers, private/property/declaration names, dotted or qualified names, strings, regular expressions, templates, JSX, and literals; comments are excluded and parse diagnostics are terminal.
- Markdown searchable syntax includes prose, headings, links, inline code, fenced code, containers, autolinks, and non-comment HTML; HTML comments are excluded.
- Enforce 4 MiB per decoded blob, 16 MiB decoded source per batch, 16 MiB combined stdout/stderr per batch, and 60 seconds per batch.
- Enforce a separate 64 MiB combined-output and 60-second limit on the Git character-diff subprocess used for exact changed-byte ranges.
- Run helper batches sequentially and cache results only for the current checker invocation.
- Missing Node, missing helper or packages, wrong dependency versions, invalid UTF-8, parse diagnostics, timeout, oversized input/output, malformed protocol, and invalid spans are terminal sanitized checker errors.
- Never fall back to the removed JavaScript/TypeScript or Markdown heuristic scanners.
- Publish overlap reports atomically so a failed parser run cannot truncate or replace the last usable report.
- The base and brand merge-gate paths must provision and validate root `node_modules` before invoking the checker; Desktop dependencies remain required only where Desktop tests run.
- Preserve the existing upstream-rehearsal runner, evidence schema, sealed dependency-view behavior, and all unrelated language handlers.
- Treat the checker, merge gate, and invariant runner solely as developer, CI, upstream-merge, and release verification tooling. They do not validate or execute user workflow YAML, and executable ledger entries are trusted repository-controlled tests.
- On Windows preserve kernel-enforced Job Object containment. On POSIX preserve the owned process group plus descendants observed and PID/create-time verified before reparenting; deliberately daemonizing children that create a new session and reparent before observation are outside the portable Darwin contract and must not be ledgered.
- Treat only a successful exact zero-entry Git tree lookup as path absence; Git tree or present-object failures must remain terminal.
- Final review covers `5f5596d21..HEAD`; Task 10 remains blocked until a fresh Task 9 review is clean.

## File Map

- Create `scripts/extract_non_python_symbols.mjs`: the only Node parser process and NDJSON protocol endpoint; owns dependency attestation, UTF-8 byte mapping, JS/TS token spans, Markdown syntax spans, and protocol-safe errors.
- Create `tests/scripts/test_extract_non_python_symbols.py`: direct black-box protocol, grammar, Unicode, diagnostic, and version tests for the Node helper.
- Modify `package.json`: declare the four parser packages as exact root `devDependencies`.
- Modify `package-lock.json`: lock the exact parser dependency graph reproducibly.
- Modify `scripts/check_upstream_customizations.py`: own Git blob bytes, request batching, helper lifecycle and bounds, response validation, invocation cache, byte-range overlap, shared multi-entry classification, and atomic report publication.
- Modify `tests/scripts/test_check_upstream_customizations.py`: prove historical committed-byte authority, fail-closed transport behavior, batching/cache behavior, byte-exact overlap, and retained language semantics.
- Modify `scripts/test_workflow_merge_gate.sh`: expose a validated root dependency view before every checker invocation without changing the later Desktop dependency boundary.
- Modify `tests/scripts/test_workflow_merge_gate.py`: prove checker-before-test ordering includes root parser dependencies in both base and brand paths and remains fail closed.
- Modify `docs/upstream-customizations/workflow-orchestration.yaml`: replace ownership of deleted scanners with the parser client/helper interfaces and record the new files and invariants.
- Modify `docs/upstream-customizations/README.md`: document parser-backed broad symbol meaning, committed-byte authority, bounds, and terminal failure behavior.

---

### Task 1: Pinned Node Parser Helper

**Files:**
- Create: `scripts/extract_non_python_symbols.mjs`
- Create: `tests/scripts/test_extract_non_python_symbols.py`
- Modify: `package.json`
- Modify: `package-lock.json`

**Interfaces:**
- Consumes: newline-delimited JSON with a first record `{"type":"hello","protocol":1}` followed by parse records `{"type":"parse","id":str,"path":str,"language":"javascript"|"typescript"|"markdown","source_base64":str,"symbols":list[str]}`.
- Produces: first record `{"type":"hello","protocol":1,"versions":{"typescript":"6.0.3","unified":"11.0.5","remark-parse":"11.0.0","micromark":"4.0.2"}}`, then one `{"type":"result","id":str,"spans":{symbol:[[start_byte,end_byte],...]}}` per request.
- Produces functions `extractTypeScriptSpans(sourceText, sourceBytes, symbols, scriptKind)`, `extractQualifiedTypeScriptSpans(sourceFile, offsets, requestedSymbols)`, `extractMarkdownSpans(sourceText, sourceBytes, symbols)`, `utf8ByteOffsets(sourceText)`, and `exactCoveredMatches(sourceBytes, coveredByteRanges, symbolBytes)` for direct unit reasoning and ledger ownership.
- On any request error, writes one bounded `{"type":"error","id":str|null,"code":str,"detail":str}` record to stdout, writes no source bytes to stderr, and exits nonzero.

- [ ] **Step 1: Pin the exact direct parser dependencies**

Add this exact root manifest section without moving the existing runtime dependencies:

```json
"devDependencies": {
  "micromark": "4.0.2",
  "remark-parse": "11.0.0",
  "typescript": "6.0.3",
  "unified": "11.0.5"
}
```

Regenerate only the lock metadata from the already provisioned dependency tree:

```bash
npm install --package-lock-only --ignore-scripts --offline
```

Expected: exit 0; `package-lock.json` records all four direct root dev dependencies at the exact approved versions and does not upgrade unrelated packages.

- [ ] **Step 2: Write the failing helper protocol tests**

Create a subprocess harness in `tests/scripts/test_extract_non_python_symbols.py` that sends raw NDJSON and parses stdout one record per line:

```python
ROOT = Path(__file__).parents[2]
HELPER = ROOT / "scripts/extract_non_python_symbols.mjs"


def _request(path: str, language: str, source: bytes, symbols: list[str]) -> dict[str, object]:
    return {
        "type": "parse",
        "id": path,
        "path": path,
        "language": language,
        "source_base64": base64.b64encode(source).decode("ascii"),
        "symbols": symbols,
    }


def _run_helper(*requests: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    payload = "\n".join(json.dumps(item) for item in (
        {"type": "hello", "protocol": 1}, *requests,
    )) + "\n"
    result = subprocess.run(
        ["node", str(HELPER)], cwd=ROOT, input=payload,
        text=True, capture_output=True, check=False,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return result, records
```

Write the attestation test in full and use the same response lookup in the grammar tests:

```python
def test_helper_attests_exact_protocol_and_dependency_versions() -> None:
    result, records = _run_helper()
    assert result.returncode == 0, result.stderr
    assert records == [{
        "type": "hello",
        "protocol": 1,
        "versions": {
            "typescript": "6.0.3",
            "unified": "11.0.5",
            "remark-parse": "11.0.0",
            "micromark": "4.0.2",
        },
    }]


def _expected_occurrences(source: bytes, token: bytes) -> list[list[int]]:
    found: list[list[int]] = []
    start = 0
    while (offset := source.find(token, start)) >= 0:
        found.append([offset, offset + len(token)])
        start = offset + 1
    return found
```

Add `test_typescript_parser_finds_broad_syntax_but_not_comments`, `test_typescript_parser_accepts_as_and_satisfies_contexts`, `test_typescript_parser_finds_qualified_relationships_without_owning_trivia`, `test_markdown_parser_finds_broad_nodes_but_not_html_comments`, `test_helper_returns_utf8_byte_offsets_for_multibyte_prefixes`, and `test_helper_rejects_invalid_base64_utf8_and_parse_diagnostics`. Use JS/TS fixtures containing `obj.ExactToken`, `#ExactToken`, `'ExactToken'`, `/ExactToken/`, `` `ExactToken` ``, `<ExactToken />`, `value as ExactToken`, and `value satisfies ExactToken`; put the same token in `//` and `/* */` comments and assert the result equals `_expected_occurrences` with the comment offsets removed. For qualified syntax, request `Owner.member` from both `Owner /* trivia */ . member` and `namespace Owner { export const member = 1 }`; assert the returned spans are exactly the `Owner` and `member` token byte ranges, never the intervening whitespace/comment bytes. Use Markdown fixtures containing a heading, link destination, inline code, fenced code, blockquote/list content, autolink, non-comment HTML, and `<!-- ExactToken -->`; assert only the HTML-comment offset is removed. Prefix the Unicode fixture with `"é🙂"` and assert the returned start is `len("é🙂".encode("utf-8"))`, not the Python character count. For each invalid input, assert nonzero exit, one `error` record, a stable `code`, and absence of the source text in stdout/stderr.

- [ ] **Step 3: Run the new helper tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_extract_non_python_symbols.py -q
```

Expected: FAIL because `scripts/extract_non_python_symbols.mjs` does not exist.

- [ ] **Step 4: Implement dependency attestation and strict protocol decoding**

Implement the helper as ESM. Load package versions with `createRequire(import.meta.url)`; compare the exact map below before accepting parse records:

```javascript
const PROTOCOL_VERSION = 1;
const EXPECTED_VERSIONS = Object.freeze({
  typescript: "6.0.3",
  unified: "11.0.5",
  "remark-parse": "11.0.0",
  micromark: "4.0.2",
});
```

Reject a missing/duplicate hello, unknown record keys or types, duplicate request IDs, unsupported extensions/languages, non-string symbols, invalid canonical base64, invalid UTF-8 using `new TextDecoder("utf-8", {fatal: true})`, and embedded NUL. Sanitize error details by reporting the request ID/path and stable error class only; never echo source or parser excerpts.

Drive the protocol sequentially and enforce decoded batch size inside the helper as well as Python:

```javascript
const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
let helloSeen = false;
let decodedBytes = 0;
const requestIds = new Set();
for await (const line of input) {
  if (!line) continue;
  const record = JSON.parse(line);
  if (!helloSeen) {
    if (record.type !== "hello" || record.protocol !== PROTOCOL_VERSION) {
      fail("protocol_mismatch", null);
    }
    helloSeen = true;
    emit({type: "hello", protocol: PROTOCOL_VERSION, versions: loadedVersions()});
    continue;
  }
  validateParseRecord(record);
  if (requestIds.has(record.id)) fail("duplicate_request", record.id);
  requestIds.add(record.id);
  const sourceBytes = decodeCanonicalBase64(record.source_base64);
  decodedBytes += sourceBytes.length;
  if (sourceBytes.length > MAX_BLOB_BYTES || decodedBytes > MAX_BATCH_BYTES) {
    fail("input_limit", record.id);
  }
  const sourceText = new TextDecoder("utf-8", {fatal: true}).decode(sourceBytes);
  if (sourceText.includes("\0")) fail("invalid_source", record.id);
  emit({type: "result", id: record.id, spans: extract(record, sourceText, sourceBytes)});
}
if (!helloSeen) fail("missing_hello", null);
```

`loadedVersions()` must locate the actual resolved package roots, read their package manifests, compare them to `EXPECTED_VERSIONS`, and also compare `ts.version` with `6.0.3`; it must fail before emitting a successful hello on any mismatch.

- [ ] **Step 5: Implement TypeScript parser-backed coverage**

For `.js/.jsx/.mjs/.cjs/.ts/.tsx/.mts/.cts`, create a `ts.SourceFile` with the correct `ScriptKind`, reject every syntactic diagnostic, and then run the TypeScript scanner with `skipTrivia=true`. Convert scanner UTF-16 offsets through `utf8ByteOffsets`; mark every non-EOF syntax token byte interval as covered. Search the original byte array for each exact symbol and return a match only when every byte in its half-open interval is covered by one or more syntax-token intervals and the bytes immediately before/after are not ASCII letters, digits, underscore, or dollar sign. This retains exact qualified names and literal contents while excluding comments/trivia and substring matches inside larger identifiers.

The offset map must advance by Unicode code point, assigning both UTF-16 surrogate positions the same starting byte boundary and the following UTF-16 boundary the full UTF-8 length. Sort and deduplicate all returned spans.

Use these core implementations (with `fail(code, id)` from Step 4 for diagnostics):

```javascript
function utf8ByteOffsets(sourceText) {
  const offsets = new Array(sourceText.length + 1);
  let utf16 = 0;
  let bytes = 0;
  offsets[0] = 0;
  while (utf16 < sourceText.length) {
    const codePoint = sourceText.codePointAt(utf16);
    const width = codePoint > 0xffff ? 2 : 1;
    offsets[utf16] = bytes;
    if (width === 2) offsets[utf16 + 1] = bytes;
    bytes += Buffer.byteLength(String.fromCodePoint(codePoint), "utf8");
    utf16 += width;
    offsets[utf16] = bytes;
  }
  return offsets;
}

function isIdentifierByte(value) {
  return value === 0x24 || value === 0x5f ||
    (value >= 0x30 && value <= 0x39) ||
    (value >= 0x41 && value <= 0x5a) ||
    (value >= 0x61 && value <= 0x7a);
}

function mergeRanges(ranges) {
  const merged = [];
  for (const [start, end] of [...ranges].sort((left, right) => left[0] - right[0])) {
    const previous = merged.at(-1);
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

function rangeCovered(start, end, ranges) {
  let low = 0;
  let high = ranges.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (ranges[middle][0] <= start) low = middle + 1;
    else high = middle;
  }
  const candidate = ranges[low - 1];
  return candidate !== undefined && candidate[0] <= start && end <= candidate[1];
}

function exactCoveredMatches(sourceBytes, coveredByteRanges, symbolBytes) {
  const matches = [];
  const searchable = mergeRanges(coveredByteRanges);
  for (let start = sourceBytes.indexOf(symbolBytes); start >= 0;
       start = sourceBytes.indexOf(symbolBytes, start + 1)) {
    const end = start + symbolBytes.length;
    const bounded = (start === 0 || !isIdentifierByte(sourceBytes[start - 1])) &&
      (end === sourceBytes.length || !isIdentifierByte(sourceBytes[end]));
    if (bounded && rangeCovered(start, end, searchable)) matches.push([start, end]);
  }
  return matches;
}

function extractTypeScriptSpans(sourceText, sourceBytes, symbols, scriptKind) {
  const parsed = ts.createSourceFile("input", sourceText, ts.ScriptTarget.Latest, true, scriptKind);
  if (parsed.parseDiagnostics.length) fail("parse_diagnostic", null);
  const offsets = utf8ByteOffsets(sourceText);
  const scanner = ts.createScanner(
    ts.ScriptTarget.Latest, true, parsed.languageVariant, sourceText,
  );
  const covered = [];
  for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan()) {
    covered.push([offsets[scanner.getTokenPos()], offsets[scanner.getTextPos()]]);
  }
  const qualified = extractQualifiedTypeScriptSpans(parsed, offsets, new Set(symbols));
  return Object.fromEntries(symbols.map(symbol => [symbol, deduplicateSpans([
    ...exactCoveredMatches(sourceBytes, covered, Buffer.from(symbol, "utf8")),
    ...(qualified.get(symbol) ?? []),
  ])]));
}
```

`extractQualifiedTypeScriptSpans` must walk parser relationships rather than normalize raw text. For `PropertyAccessExpression` and `QualifiedName`, recursively collect identifier/private-identifier components; when their dot-joined names equal a requested symbol, record each component token's own byte span. Maintain a syntax-only scope stack for module/namespace, class, interface, and enum declarations; when a named member or declaration makes a requested scoped name, record only the scope-name and member-name token spans. Never return an enclosing node range, because that would make edits to whitespace or comments between components look like owned-symbol changes. Exact contiguous occurrences in literals and other tokens continue through `exactCoveredMatches`.

- [ ] **Step 6: Implement Markdown parser-backed coverage**

Parse with `unified().use(remarkParse).parse(sourceText)` and reject any malformed position object. Walk the mdast tree, adding byte coverage only for semantic leaf/value-bearing nodes and explicit broad syntax nodes: `text`, `inlineCode`, `code`, `html` when it is not an HTML comment, `definition`, `link`, `image`, `linkReference`, and `imageReference`. Container nodes are traversed but not themselves marked, preventing a root/paragraph/container range from accidentally re-including an HTML comment. For code/link/HTML nodes, use the node's source position so delimiters and destinations remain searchable. Convert line/column positions to UTF-16 offsets and then UTF-8 byte offsets; use the same exact covered-match and ASCII identifier-boundary rules as JS/TS.

Remark positions include source offsets. Collect eligible ranges and all parser-produced HTML comment node ranges separately, subtract excluded ranges after traversal, then call `exactCoveredMatches`:

```javascript
const SEARCHABLE_MARKDOWN = new Set([
  "text", "inlineCode", "code", "definition", "link", "image",
  "linkReference", "imageReference",
]);

function nodeRange(node, offsets) {
  const start = node?.position?.start?.offset;
  const end = node?.position?.end?.offset;
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start >= end) {
    fail("invalid_markdown_position", null);
  }
  return [offsets[start], offsets[end]];
}

function extractMarkdownSpans(sourceText, sourceBytes, symbols) {
  const tree = unified().use(remarkParse).parse(sourceText);
  const offsets = utf8ByteOffsets(sourceText);
  const covered = [];
  const excluded = [];
  const visit = node => {
    if (node.type === "html" && /^<!--(?:.|\n|\r)*-->$/.test(node.value)) {
      excluded.push(nodeRange(node, offsets));
    } else if (node.type === "html" || SEARCHABLE_MARKDOWN.has(node.type)) {
      covered.push(nodeRange(node, offsets));
    }
    for (const child of node.children ?? []) visit(child);
  };
  visit(tree);
  const searchable = covered.flatMap(([start, end]) => {
    let pieces = [[start, end]];
    for (const [cutStart, cutEnd] of excluded) {
      pieces = pieces.flatMap(([left, right]) =>
        cutEnd <= left || right <= cutStart
          ? [[left, right]]
          : [[left, Math.max(left, cutStart)], [Math.min(right, cutEnd), right]]
              .filter(([pieceStart, pieceEnd]) => pieceStart < pieceEnd));
    }
    return pieces;
  });
  return Object.fromEntries(symbols.map(symbol => [
    symbol,
    exactCoveredMatches(sourceBytes, searchable, Buffer.from(symbol, "utf8")),
  ]));
}
```

- [ ] **Step 7: Run focused and package integrity tests**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_extract_non_python_symbols.py -q
npm ls --depth=0 --json
git diff --check
```

Expected: helper tests PASS; `npm ls` reports exact direct versions without invalid/extraneous root packages; `git diff --check` exits 0.

- [ ] **Step 8: Commit the standalone parser deliverable**

```bash
git add package.json package-lock.json scripts/extract_non_python_symbols.mjs tests/scripts/test_extract_non_python_symbols.py
git commit -m "feat(workflow): add bounded symbol parser helper"
```

---

### Task 2: Python Parser Transport and Committed-Blob Authority

**Files:**
- Modify: `scripts/check_upstream_customizations.py:1-390`
- Modify: `tests/scripts/test_check_upstream_customizations.py:1-390`

**Interfaces:**
- Consumes: the Task 1 NDJSON protocol and exact Git blob bytes from `git show <resolved-revision>:<path>`.
- Produces immutable `_ParserRequest(request_id: str, path: str, language: str, blob_oid: str, source: bytes, symbols: tuple[str, ...])`.
- Produces `_NonPythonSymbolResolver.spans(requests: Sequence[_ParserRequest]) -> dict[str, dict[str, list[tuple[int, int]]]]`; it batches sequentially, validates every helper response, and caches by `(blob_oid, language, symbols, protocol, parser_versions)` for its own lifetime only.
- Produces `_blob_bytes(repo: Path, revision: str, path: str) -> tuple[str, bytes]` returning the exact blob OID and bytes; retains `_blob_text(repo, revision, path) -> str` for parser-independent formats by strict UTF-8 decoding of those bytes.
- Produces `_parser_language(path: str) -> str | None` for only the approved JS/TS/Markdown suffixes.
- Produces `_run_bounded_capture(argv: Sequence[str], *, cwd: Path, input_bytes: bytes | None, timeout_seconds: float, output_limit_bytes: int) -> _CompletedCapture`, the sanitized bounded subprocess primitive reused by Task 3's Git character diff.

- [ ] **Step 1: Write failing committed-byte and batching tests**

Add `test_parser_blob_loader_reads_resolved_commit_not_dirty_worktree`, `test_parser_blob_loader_reads_requested_historical_revision_not_head`, `test_exact_missing_tree_entry_is_absent`, terminal Git tree/object failure cases, `test_parser_resolver_batches_paths_and_deduplicates_identical_requests`, and `test_parser_batches_sequentially_at_sixteen_mibibytes`. Prove separately that exact zero-entry `ls-tree` output means absent and that tree lookup, non-blob, malformed type/size, unreadable object, and length-mismatch failures propagate instead of becoming empty content. These are direct transport-boundary tests: Task 3, not this task, owns wiring manifest validation and overlap classification to the resolver. Monkeypatch `customization_checker._run_parser_batch` with this shape rather than adding a test-only production switch:

```python
calls: list[list[customization_checker._ParserRequest]] = []

def recording_batch(requests):
    calls.append(list(requests))
    return {
        request.request_id: {
            symbol: [
                (offset, offset + len(symbol.encode("utf-8")))
                for offset in [request.source.find(symbol.encode("utf-8"))]
                if offset >= 0
            ]
            for symbol in request.symbols
        }
        for request in requests
    }

monkeypatch.setattr(customization_checker, "_run_parser_batch", recording_batch)
```

In the dirty-tree test, commit a TypeScript file containing `ExactToken`, replace the worktree file with a comment-only occurrence, call `_blob_bytes(repo, resolved_revision, "owned.ts")`, construct the parser request from those returned bytes/OID, resolve it, and assert `calls[0][0].source` equals `git show HEAD:owned.ts` bytes. In the historical test, move `HEAD` past a commit that removed the token, call `_blob_bytes` with the older resolved commit, and assert the captured `blob_oid` equals `git rev-parse older:owned.ts`. For batching, use two request IDs with the same blob OID/language/symbols and assert one helper request supplies both results; use five blobs just below 4 MiB and assert two sequential calls whose decoded totals are each at most 16 MiB. End-to-end manifest and overlap authority remains a Task 3 integration test.

- [ ] **Step 2: Write failing terminal-boundary tests**

Add one named test per failure class: `test_parser_fails_closed_when_node_or_helper_is_missing`, `test_parser_rejects_wrong_protocol_or_dependency_versions`, `test_parser_rejects_invalid_utf8_and_oversized_blob`, `test_parser_kills_a_batch_after_sixty_seconds`, `test_parser_rejects_oversized_or_malformed_output`, `test_parser_rejects_unknown_missing_duplicate_and_invalid_spans`, and `test_parser_errors_are_bounded_and_do_not_echo_source`. Parameterize the response-shape test with missing ID, duplicate ID, extra ID, missing symbol, negative start, reversed span, endpoint beyond `len(source)`, duplicate span, unsorted span, overlapping same-symbol spans, boolean endpoint, and continuation-byte endpoint. Use monkeypatched `subprocess.Popen` or a temporary executable Node fixture to emit the exact controlled records. Assert every public failure is a `ValueError` whose message begins with `non-Python parser`, excludes the sentinel source `DO_NOT_ECHO_SECRET_SOURCE`, and is at most 512 characters.

- [ ] **Step 3: Run the focused tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q -k 'parser or committed_blob or historical_revision'
```

Expected: FAIL because `_ParserRequest`, `_NonPythonSymbolResolver`, and `_run_parser_batch` do not exist.

- [ ] **Step 4: Add byte-safe Git loading and request types**

Use `subprocess.run(["git", "show", f"{revision}:{path}"], cwd=repo, text=False, capture_output=True, check=False)` for `_blob_bytes`, check status without decoding successful stdout, obtain the OID with `git rev-parse --verify <revision>:<path>`, enforce `_MAX_SCANNED_SOURCE_BYTES = 4 * 1024 * 1024`, reject NUL and strict UTF-8 before constructing requests, and keep only sanitized Git failure text. Define:

```python
@dataclass(frozen=True)
class _ParserRequest:
    request_id: str
    path: str
    language: str
    blob_oid: str
    source: bytes
    symbols: tuple[str, ...]
```

Add exact constants for protocol 1, 16 MiB decoded batch input, 16 MiB output, 60 seconds, helper path, supported suffix mapping, and the four expected versions.

- [ ] **Step 5: Implement a bounded helper lifecycle**

Implement `_run_bounded_capture` with `subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE)` plus one bounded writer and two bounded drain threads. It returns only `returncode`, bounded `stdout`, and bounded `stderr`; start, timeout, output-limit, and stream failures become stable internal categories without embedding argv, absolute paths, or captured text. `_run_parser_batch(requests)` serializes one hello and all requests with compact JSON, calls this primitive with Node at the repository root, a 16 MiB output cap, and 60-second timeout, then applies parser-specific error translation. Pass no source in argv/environment, kill immediately when the shared counter crosses the limit, and always reap.

The drain function must enforce the bound before appending a chunk, so neither memory nor a temporary file can grow past the contract:

```python
captured = {"stdout": bytearray(), "stderr": bytearray()}
captured_bytes = 0
capture_lock = threading.Lock()
output_exceeded = threading.Event()

def drain(name: str, stream: BinaryIO) -> None:
    nonlocal captured_bytes
    while chunk := stream.read(64 * 1024):
        with capture_lock:
            if captured_bytes + len(chunk) > _MAX_PARSER_OUTPUT_BYTES:
                output_exceeded.set()
                process.kill()
                return
            captured[name].extend(chunk)
            captured_bytes += len(chunk)

process = subprocess.Popen(
    [node, str(_NON_PYTHON_HELPER)], cwd=_REPOSITORY_ROOT,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
writer = threading.Thread(target=_write_and_close, args=(process.stdin, payload))
readers = [
    threading.Thread(target=drain, args=("stdout", process.stdout)),
    threading.Thread(target=drain, args=("stderr", process.stderr)),
]
for thread in [writer, *readers]:
    thread.start()
try:
    process.wait(timeout=_PARSER_TIMEOUT_SECONDS)
except subprocess.TimeoutExpired as exc:
    process.kill()
    process.wait()
    raise _parser_error("timeout") from exc
finally:
    for thread in [writer, *readers]:
        thread.join()
if output_exceeded.is_set():
    raise _parser_error("output_limit")
```

`_write_and_close` catches `BrokenPipeError`, always closes stdin, and stores no error text. Wrap start/kill/wait/thread failures in the bounded `_parser_error` categories and validate return code only after all streams are drained.

Validate exactly one matching hello, exact dependency versions, exactly one result per request ID, no extra IDs/keys, every requested symbol present once, integer (not boolean) endpoints, `0 <= start < end <= len(source)`, monotonically sorted non-overlapping spans, and UTF-8 character boundaries. Convert any exception through one `_parser_error(code, request_id=None)` formatter capped at 512 characters.

- [ ] **Step 6: Implement sequential batching and invocation-only caching**

`_NonPythonSymbolResolver.spans()` must:

1. canonicalize symbol tuples in sorted unique order;
2. serve identical `(blob_oid, language, symbols, protocol, parser_versions)` requests from its in-memory dictionary;
3. split uncached requests in input order before decoded source bytes would exceed 16 MiB;
4. reject a single request above 4 MiB before spawning Node;
5. invoke `_run_parser_batch` one batch at a time;
6. return results under every caller request ID, including cache aliases.

Never persist the cache or derive source bytes from the working tree.

Use a deterministic batching loop rather than a size-dependent concurrent executor:

```python
pending: list[_ParserRequest] = []
pending_bytes = 0
for request in uncached_requests:
    if pending and pending_bytes + len(request.source) > _MAX_PARSER_BATCH_BYTES:
        self._store(_run_parser_batch(pending), pending)
        pending, pending_bytes = [], 0
    pending.append(request)
    pending_bytes += len(request.source)
if pending:
    self._store(_run_parser_batch(pending), pending)
return {request.request_id: self._cached_result(request) for request in requests}
```

- [ ] **Step 7: Run the adapter and existing manifest tests**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q -k 'parser or manifest or source_revision or committed'
```

Expected: all selected tests PASS, including existing source-revision and strict-tree tests.

- [ ] **Step 8: Commit the transport boundary**

```bash
git add scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
git commit -m "feat(workflow): add fail-closed parser transport"
```

---

### Task 3: Shared Semantic Resolution and Byte-Exact Overlap

**Files:**
- Modify: `scripts/check_upstream_customizations.py:186-390,1780-2100`
- Modify: `tests/scripts/test_check_upstream_customizations.py:390-1400,1890-2050`

**Interfaces:**
- Consumes: `_NonPythonSymbolResolver`, `_ParserRequest`, `_blob_bytes`, and `_parser_language` from Task 2.
- Produces `_collect_manifest_parser_requests(entries, repo, revision) -> list[_ParserRequest]` and uses one resolver instance for all strict manifest lookups.
- Produces `_git_changed_byte_ranges(repo: Path, left: str, right: str, path: str, old_source: bytes, new_source: bytes) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]` by parsing a bounded character-token Git word diff.
- Produces `classify_upstream_overlaps(entries: Sequence[dict[str, Any]], repo: Path, diff_range: str, *, resolver: _NonPythonSymbolResolver | None = None) -> list[dict[str, Any]]` for one shared resolution pass.
- Retains `classify_upstream_overlap(entry, repo, diff_range)` as a compatibility wrapper around `classify_upstream_overlaps([entry], ...)`.

- [ ] **Step 1: Add failing integration tests for broad meaning and exact byte overlap**

Convert the existing TypeScript/Markdown scanner fixtures to exercise the real helper through `load_and_validate_manifest` and `classify_upstream_overlap`. Add `test_typescript_broad_symbols_include_as_satisfies_jsx_and_qualified_names`, `test_markdown_broad_symbols_include_links_code_containers_and_html`, `test_non_python_overlap_uses_utf8_byte_ranges_after_multibyte_prefix`, `test_non_python_overlap_ignores_comment_only_edits`, and `test_classify_all_entries_uses_one_shared_parser_resolution_pass`.

Use this exact overlap assertion pattern for the Unicode test:

```python
source_path.write_text('const café = "ExactToken";\n', encoding="utf-8")
_git(repo, "add", source_path.name)
_git(repo, "commit", "-m", "add multibyte fixture")
left = _git(repo, "rev-parse", "HEAD")
source_path.write_text('const café = "ExactT0ken";\n', encoding="utf-8")
_git(repo, "commit", "-am", "edit owned literal")
assert classify_upstream_overlap(entry, repo, f"{left}..HEAD")["classification"] == "owned_symbol"
```

Create a sibling history that edits only `café` and assert `same_file`. For comment-only edits, change only a `// ExactToken` or `<!-- ExactToken -->` occurrence while leaving a searchable occurrence untouched elsewhere; assert the classification is `same_file`, not `owned_symbol`. In the shared-pass test, monkeypatch `_run_parser_batch`, build two ledger entries over three changed JS/Markdown blobs, call `classify_upstream_overlaps`, and assert the fake was called once with deterministic request IDs and both entries remain in ledger order.

- [ ] **Step 2: Run the semantic tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q -k 'broad_symbols or utf8_byte_ranges or comment_only_edits or shared_parser_resolution'
```

Expected: FAIL because manifest validation and overlap classification still route through the lexical scanners and character/line ranges.

- [ ] **Step 3: Batch manifest semantic requests before entry validation**

After structural/path checks have collected valid entries, gather every strict symbol against each declared JS/TS/Markdown file at the one resolved source revision. Load each committed blob once, create deterministic request IDs from `revision:path`, and resolve the entire set through one `_NonPythonSymbolResolver`. During the existing per-entry validation loop, use the precomputed `spans[path][symbol]`; keep Python AST, shell, PowerShell, CSS, JSON, TOML, and other existing handlers on their current paths.

If a parseable file has no requested symbol, the helper still returns that symbol with an empty list. Any parser failure aborts manifest validation before it returns data.

Build one unioned symbol set per path before constructing requests:

```python
symbols_by_path: dict[str, set[str]] = {}
for entry in entries:
    for path in entry["files"]:
        if _parser_language(path) is not None:
            symbols_by_path.setdefault(path, set()).update(_strict_owned_symbols(entry))
requests = []
for path in sorted(symbols_by_path):
    blob_oid, source = _blob_bytes(repo, resolved_source_revision, path)
    language = _parser_language(path)
    assert language is not None
    requests.append(_ParserRequest(
        request_id=f"{resolved_source_revision}:{path}",
        path=path,
        language=language,
        blob_oid=blob_oid,
        source=source,
        symbols=tuple(sorted(symbols_by_path[path])),
    ))
parser_spans = resolver.spans(requests)
```

- [ ] **Step 4: Replace non-Python line overlap with exact byte overlap**

For JS/TS/Markdown changed paths, load exact left and right blob bytes (empty bytes when the path does not exist at that endpoint), parse both existing endpoints, and compare returned half-open spans with `_git_changed_byte_ranges`. Do not use `difflib.SequenceMatcher(..., autojunk=False)`: it exceeds five seconds on 100 KiB of repetitive input and cannot honor the approved 4 MiB source bound.

Run Git with argument-vector invocation, a 60-second timeout, and a 64 MiB combined-output cap:

```python
argv = [
    "git", "diff", "--no-ext-diff", "--no-color", "--text", "--unified=0",
    "--word-diff=porcelain",
    "--word-diff-regex=[[:space:]]|[^[:space:]]",
    f"{left}..{right}", "--", path,
]
diff = _run_bounded_capture(
    argv, cwd=repo, timeout_seconds=60, output_limit_bytes=64 * 1024 * 1024,
)
```

Parse output as bytes. Ignore headers until an `@@ -old_line,old_count +new_line,new_count @@` record, then initialize old/new cursors from byte line-start tables derived independently from `old_source` and `new_source`. In porcelain mode a line beginning with one space advances both cursors by the payload byte length, `-` records that old half-open range and advances only old, and `+` records that new range and advances only new. A standalone `~` represents a source newline and advances the side selected by the preceding marker (` ` advances both); protocol newlines are not source bytes. Validate each hunk's final cursors against its declared old/new line counts, reject malformed/oversized/timed-out output, and coalesce adjacent changed ranges. Character-token word diff ensures unchanged bytes inside a source line remain outside changed ranges without the quadratic Python matcher.

Add `test_git_character_diff_bounds_repetitive_input_and_fails_closed` using a 100 KiB repetitive fixture, plus malformed-hunk, timeout, and output-limit cases against `_run_bounded_capture`. An insertion or deletion has an empty range on one side and a non-empty range on the other; only the non-empty side can overlap. Retain the current JSON/TOML exact-offset path and line-based behavior for unrelated languages.

- [ ] **Step 5: Share one parser pass across the full overlap report**

Implement `classify_upstream_overlaps` to resolve diff endpoints and changed paths once, gather old/new requests for all entries and both same-file and possible-equivalent paths, call one resolver, then build results in ledger order. Preserve every existing result key and rationale string. Make `main()` call this plural function; keep the singular wrapper for unit callers.

The compatibility boundary is exact:

```python
def classify_upstream_overlap(
    entry: dict[str, Any], repo: Path, diff_range: str
) -> dict[str, Any]:
    return classify_upstream_overlaps([entry], repo, diff_range)[0]


overlaps = classify_upstream_overlaps(data["upstream_changes"], repo, args.upstream_diff)
```

- [ ] **Step 6: Make report publication atomic**

Serialize the completed report before touching the destination. Write it to a sibling temporary file opened with mode `0o600`, flush and `os.fsync`, then `os.replace(temp_path, args.report)`. Remove the temporary file in `finally`. Add a CLI test that seeds `--report` with valid prior JSON, forces a parser failure, and asserts the file is byte-for-byte unchanged; add a success assertion that acknowledgements still carry forward by entry ID.

Factor publication so no caller can accidentally restore `Path.write_text`:

```python
def _write_json_atomically(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
```

- [ ] **Step 7: Prove parser failures do not fall back**

Add a behavior test that supplies syntactically valid TypeScript containing `ExactToken`, monkeypatches `_run_parser_batch` to raise `ValueError("non-Python parser unavailable")`, and asserts both public paths propagate the failure:

```python
def unavailable(_requests):
    raise ValueError("non-Python parser unavailable")

monkeypatch.setattr(customization_checker, "_run_parser_batch", unavailable)
with pytest.raises(ValueError, match="non-Python parser unavailable"):
    load_and_validate_manifest(manifest, repo, check_git=False)
with pytest.raises(ValueError, match="non-Python parser unavailable"):
    classify_upstream_overlap(entry, repo, f"{left}..{right}")
```

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_check_upstream_customizations.py -q -k 'cannot_fall_back'
```

Expected: PASS only by propagating the helper failure; no classification or manifest result is returned.

- [ ] **Step 8: Delete the superseded scanners**

Remove `_typescript_without_comments`, `_markdown_without_comments`, and every helper used exclusively by those two functions. Before deleting a supporting helper, run `rg -n '<name>' scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py` and retain it if any shell, PowerShell, CSS, JSON, TOML, YAML, or Python route still consumes it. Rename scanner-oriented test helper/docstrings to parser-oriented terms; preserve all behavioral fixtures as regression tests.

Verify manually:

```bash
rg -n '_typescript_without_comments|_markdown_without_comments' scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
```

Expected: no matches. This is a refactor under the green parser behavior from Steps 1-7, not a second implementation path.

- [ ] **Step 9: Run the complete checker suite**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_extract_non_python_symbols.py tests/scripts/test_check_upstream_customizations.py -q
```

Expected: all helper and checker tests PASS; no skip is introduced for Node when the pinned root dependencies are present.

- [ ] **Step 10: Commit the semantic cutover**

```bash
git add scripts/check_upstream_customizations.py tests/scripts/test_check_upstream_customizations.py
git commit -m "fix(workflow): use parser spans for ledger overlap"
```

---

### Task 4: Update the Machine-Readable Ledger Contract

**Files:**
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml:4045-4105`
- Modify: `docs/upstream-customizations/README.md`

**Interfaces:**
- Consumes: the parser-only JS/TS/Markdown routes and deleted scanner symbols from Task 3.
- Produces ledger ownership for `_ParserRequest`, `_NonPythonSymbolResolver`, `_run_parser_batch`, `_git_changed_byte_ranges`, `classify_upstream_overlaps`, `PROTOCOL_VERSION`, `extractTypeScriptSpans`, `extractQualifiedTypeScriptSpans`, and `extractMarkdownSpans`.

- [ ] **Step 1: Run strict live-ledger validation to verify RED**

Run:

```bash
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --strict --base-ref HEAD
```

Expected: FAIL because the committed ledger still declares `_typescript_without_comments` and `_markdown_without_comments`, which Task 3 intentionally removed.

- [ ] **Step 2: Update the machine-readable ledger entry**

In `workflow-rehearsal-executed-invariant-evidence`:

- replace `_typescript_without_comments` and `_markdown_without_comments` with `_ParserRequest`, `_NonPythonSymbolResolver`, `_run_parser_batch`, and `classify_upstream_overlaps` (all live in its already-declared checker file);
- replace the long JavaScript lexical-goal and CommonMark fence-scanner invariants with concise invariants for parser-derived broad semantic syntax, HTML/comment exclusion, committed base64 bytes, exact UTF-8 byte spans, attested versions, sequential bounded batches, terminal parser failures, and atomic reports;
- retain every unrelated evidence, process-containment, structured-data, and detached-tree invariant unchanged.

Use exact machine-locatable helper names in `owned_symbols`; keep prose only in `owned_invariants`.

Add a separate `workflow-parser-backed-symbol-ownership` entry so `package-lock.json` does not inherit the existing entry's `any_owned_file` policy. Use `overlap_policy: owned_symbol`, these exact files, and these exact searchable symbols:

```yaml
- id: workflow-parser-backed-symbol-ownership
  change_class: workflow-testing
  owner: workflow-orchestration
  overlap_policy: owned_symbol
  files:
  - package.json
  - package-lock.json
  - scripts/extract_non_python_symbols.mjs
  - scripts/check_upstream_customizations.py
  owned_symbols:
  - PROTOCOL_VERSION
  - extractTypeScriptSpans
  - extractQualifiedTypeScriptSpans
  - extractMarkdownSpans
  - _NonPythonSymbolResolver
  - _run_parser_batch
  - _git_changed_byte_ranges
  - 6.0.3
  - 11.0.5
  - 11.0.0
  - 4.0.2
  owned_invariants:
  - parser requests contain only exact committed Git blob bytes selected by Python authority
  - parser-derived broad syntax spans exclude comments and use half-open UTF-8 byte offsets
  - exact character-level Git diffs are bounded independently and fail closed before overlap classification
  - exact parser versions and bounded sequential batches fail closed without a lexical fallback
  - overlap reports replace prior evidence atomically only after complete parser success
  tests:
  - tests/scripts/test_extract_non_python_symbols.py
  - tests/scripts/test_check_upstream_customizations.py
  expected_commit_subject: 'feat(workflow): add bounded symbol parser helper'
  upstream_candidate: true
  merge_guidance: >-
    Preserve Python authority over committed revisions and overlap policy while
    using the exact pinned parser helper for broad JS, TS, and Markdown spans.
  removal_condition: >-
    Remove when upstream provides equivalent committed-byte parser extraction,
    bounded fail-closed transport, exact byte overlap, and atomic reporting.
  last_verified_upstream: aaf5691261f12601db845386d650dce1cdfa30f9
```

- [ ] **Step 3: Update the checker README contract**

Document:

1. broad symbol meaning for JS/TS and Markdown;
2. comments excluded but literals/code/prose included;
3. exact committed revision bytes, never dirty worktree bytes;
4. half-open UTF-8 byte spans and byte-exact changed ranges;
5. exact parser versions and root dependency requirement;
6. the 4 MiB/16 MiB/16 MiB/60-second bounds;
7. fail-closed errors with no heuristic fallback;
8. atomic report replacement only after complete successful classification.
9. the separate 64 MiB/60-second bound for character-token Git diff output and execution.
10. exact zero-entry Git tree lookup as the only absent-path result, with Git/object failures terminal.
11. the trusted verification-only runner scope and its Windows/POSIX containment boundaries, including the unsupported pre-observation POSIX daemon/session escape.

- [ ] **Step 4: Run parser, checker, and live-ledger validation**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_extract_non_python_symbols.py tests/scripts/test_check_upstream_customizations.py -q
.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --strict --base-ref HEAD
git diff --check
```

Expected: all tests PASS; strict live-ledger validation exits 0 using committed `HEAD`; diff check exits 0.

- [ ] **Step 5: Commit the parser-only contract**

```bash
git add docs/upstream-customizations/workflow-orchestration.yaml docs/upstream-customizations/README.md
git commit -m "docs(workflow): record parser-backed ledger ownership"
```

---

### Task 5: Detached Merge-Gate Provisioning and Full Acceptance

**Files:**
- Modify: `scripts/test_workflow_merge_gate.sh:1-135`
- Modify: `tests/scripts/test_workflow_merge_gate.py:90-380`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**
- Consumes: root `node_modules` containing the exact Task 1 parser packages, either inside the detached checkout or at the shared worktree root found from `git rev-parse --path-format=absolute --git-common-dir`.
- Produces: `_require_root_dependencies` Bash function that links the shared root dependency tree when safe, rejects missing/broken/escaping dependency views, and runs before the checker in both base and brand phases.
- Produces: final `workflow-parser-backed-symbol-ownership` ledger coverage for the gate function and its tests.
- Preserves: existing `_require`/dependency-view sealing behavior later in the base slow path and the existing Desktop `node_modules` boundary.

- [ ] **Step 1: Write failing gate-order tests**

Change the synthetic checker fixture to assert, at execution time, that all four package roots exist under `node_modules` and that their package versions equal the approved pins. Add `test_base_gate_provisions_root_parser_dependencies_before_checker`, `test_brand_gate_provisions_root_parser_dependencies_before_checker`, `test_gate_fails_before_checker_when_root_parser_dependencies_are_missing`, and `test_gate_rejects_broken_or_escaping_root_dependency_link`.

Build package fixtures with this helper:

```python
PARSER_VERSIONS = {
    "typescript": "6.0.3",
    "unified": "11.0.5",
    "remark-parse": "11.0.0",
    "micromark": "4.0.2",
}


def _write_parser_dependencies(root: Path) -> None:
    for package, version in PARSER_VERSIONS.items():
        package_dir = root / "node_modules" / package
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "package.json").write_text(
            json.dumps({"name": package, "version": version}), encoding="utf-8"
        )
```

For linked-worktree behavior, create a real temporary Git common directory with `git worktree add`, call `_write_parser_dependencies` only in the shared root, invoke the real gate in the linked checkout, and assert the checker observes the bounded symlink before it records its invocation. In the missing test, omit `micromark` and assert exit 1, a stable `root parser dependencies` error, and no checker marker. In the escaping test, point `node_modules` outside both allowed roots and assert the same pre-checker failure. Keep command-recording fixtures for later suites so the tests prove ordering, not merely final presence.

- [ ] **Step 2: Run the focused gate tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_workflow_merge_gate.py -q -k 'parser_dependencies or dependency_link'
```

Expected: FAIL because the current gate invokes the checker before creating the root dependency view.

- [ ] **Step 3: Provision and validate root dependencies before the checker**

Move shared Git/root discovery ahead of the checker and implement `_require_root_dependencies` with these rules:

1. if local `node_modules` is absent and shared-root `node_modules` is a directory, create the existing bounded symlink;
2. resolve the resulting path with physical paths and require it to equal either `$ROOT/node_modules` as a real local directory or `$SHARED_ROOT/node_modules`;
3. require readable package manifests for TypeScript, unified, remark-parse, and micromark;
4. ask Node to read the four versions and compare them with `6.0.3`, `11.0.5`, `11.0.0`, and `4.0.2` before Python starts;
5. emit a stable dependency error and exit 1 on any mismatch.

Call the function after base dirty-tree sealing and phase/brand validation but before `"$PYTHON_BIN" "$CHECKER"`. Do not move Desktop linking earlier and do not run `npm install` inside the gate.

Use a fixed version table and physical containment checks; the Node expression reads explicit package manifests instead of relying on package export maps:

```bash
_require_root_dependencies() {
  local shared_git_dir shared_root resolved_modules actual_versions
  shared_git_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
  shared_root="$(cd "$(dirname "$shared_git_dir")" && pwd -P)"
  if [[ ! -e "$ROOT/node_modules" && -d "$shared_root/node_modules" ]]; then
    ln -s "$shared_root/node_modules" "$ROOT/node_modules"
  fi
  [[ -d "$ROOT/node_modules" ]] || {
    echo "root parser dependencies are required before ledger validation" >&2
    return 1
  }
  resolved_modules="$(cd "$ROOT/node_modules" && pwd -P)"
  [[ "$resolved_modules" == "$ROOT/node_modules" ||
     "$resolved_modules" == "$shared_root/node_modules" ]] || {
    echo "root parser dependencies escape the allowed dependency roots" >&2
    return 1
  }
  actual_versions="$(node -e '
    const fs = require("node:fs");
    const path = require("node:path");
    const names = ["typescript", "unified", "remark-parse", "micromark"];
    process.stdout.write(names.map(name =>
      JSON.parse(fs.readFileSync(path.join(process.argv[1], name, "package.json"), "utf8")).version
    ).join(" "));
  ' "$resolved_modules")" || {
    echo "root parser dependencies are unreadable" >&2
    return 1
  }
  [[ "$actual_versions" == "6.0.3 11.0.5 11.0.0 4.0.2" ]] || {
    echo "root parser dependency versions do not match the lockfile" >&2
    return 1
  }
}
```

Resolve `node` with `command -v node` first and emit the same bounded dependency-family error if it is absent. Quote all resolved paths and preserve the existing dirty-tree check before creating a symlink in a base detached checkout.

- [ ] **Step 4: Extend the parser ledger entry across the gate boundary**

Add `scripts/test_workflow_merge_gate.sh` to the `workflow-parser-backed-symbol-ownership.files` list, `_require_root_dependencies` to its `owned_symbols`, and `tests/scripts/test_workflow_merge_gate.py` to its `tests`. Append this invariant without changing the four Task 4 invariants:

```yaml
  - detached base and brand gates validate the bounded root parser dependency view before checker execution
```

Expected: every newly declared symbol exists in the committed source files, and the entry remains `overlap_policy: owned_symbol` so unrelated root lockfile changes do not become file-level decisions.

- [ ] **Step 5: Run all gate contract tests**

Run:

```bash
.venv/bin/python -m pytest tests/scripts/test_workflow_merge_gate.py -q
```

Expected: all gate tests PASS, including existing dirty-tree, detached authority, dependency sealing, retry, base, and brand contracts.

- [ ] **Step 6: Run the complete Task 9 focused acceptance**

Run the new helper file directly, then run the exact prescribed three-file meta-gate with retries disabled:

```bash
.venv/bin/python -m pytest tests/scripts/test_extract_non_python_symbols.py -q
scripts/run_tests.sh --file-retries 0 \
  tests/scripts/test_check_upstream_customizations.py \
  tests/scripts/test_workflow_merge_gate.py \
  tests/scripts/test_workflow_upstream_merge.py
```

Expected: helper tests PASS; every three-file meta-gate test passes on its first attempt with zero file retries; the pre-existing environment-dependent skip, if still applicable, is the only skip.

- [ ] **Step 7: Run live authority and static checks**

Run:

```bash
npm ls typescript unified remark-parse micromark --depth=0
.venv/bin/ruff check scripts/check_upstream_customizations.py tests/scripts/test_extract_non_python_symbols.py tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py
.venv/bin/python -m py_compile scripts/check_upstream_customizations.py tests/scripts/test_extract_non_python_symbols.py tests/scripts/test_check_upstream_customizations.py tests/scripts/test_workflow_merge_gate.py
bash -n scripts/test_workflow_merge_gate.sh
git diff --check
git status --short
```

Expected: npm reports the four exact versions; Ruff, Python compilation, Bash syntax, and diff checks exit 0; status lists the two gate files and the ledger before commit. Strict ledger validation waits until Step 9 because committed `HEAD` does not contain the new gate symbol until Step 8 commits it.

- [ ] **Step 8: Commit the gate integration**

```bash
git add scripts/test_workflow_merge_gate.sh tests/scripts/test_workflow_merge_gate.py docs/upstream-customizations/workflow-orchestration.yaml
git commit -m "fix(workflow): provision parser dependencies before gate"
```

- [ ] **Step 9: Run post-commit strict and sealed live-ledger acceptance**

Run both authority probes against committed bytes:

```bash
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --strict --base-ref HEAD
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --strict --base-ref 04ac98b0aa3c88b90782b05c739bb116c7874a62
```

Expected: both exit 0; the historical probe validates the old Git-tree inventory rather than the current checkout.

Run one sealed live ledger from the committed tip:

```bash
TASK9_ACCEPTANCE_DIR="$(mktemp -d)"
.venv/bin/python scripts/run_workflow_ledger_invariants.py \
  --repo . \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml \
  --report-path "$TASK9_ACCEPTANCE_DIR/ledger-report.json" \
  --platform darwin \
  --base-ref HEAD
.venv/bin/python -c 'import json,sys; rows=json.load(open(sys.argv[1], encoding="utf-8")); executed=[r for r in rows if r["kind"] == "executed"]; assert rows and executed; assert all(r["result"] == "passed" and not r["flaky_on_first_attempt"] and len(r["attempts"]) == 1 and not r["attempts"][0]["output_truncated"] for r in executed); print(len(rows), len(executed))' "$TASK9_ACCEPTANCE_DIR/ledger-report.json"
```

Expected: runner exits 0; the report is valid, nonempty, has no failed/flaky/truncated record, and every executable record has exactly one successful attempt. After exit, `git worktree list --porcelain`, `ps -axo pid=,command=`, and the system temporary directory show no live-ledger detached worktree, `workflow-ledger-*` directory, parser helper, invariant runner, supervisor, watchdog, or in-contract trusted-test descendant left by this run. This evidence does not claim portable Darwin containment of a deliberately daemonizing child that creates a new session and reparents before observation; such a program is forbidden from the trusted ledger.

- [ ] **Step 10: Verify final branch scope and review boundary**

Run:

```bash
git status --short
git log --oneline b6aaa21f3c6a1e29632ad559d157e82327fd0a95..HEAD
git diff --stat 5f5596d21..HEAD
git -C ../workflow-language-foundation-remediation rev-parse HEAD
git -C ../workflow-language-foundation-remediation status --short
```

Expected: implementation worktree is clean; commits are limited to the approved spec/plan and five implementation deliverables; the original remediation worktree still reports `b6aaa21f3c6a1e29632ad559d157e82327fd0a95` and no changes. Hand the clean `5f5596d21..HEAD` range to a fresh Task 9 reviewer; do not begin Task 10.
