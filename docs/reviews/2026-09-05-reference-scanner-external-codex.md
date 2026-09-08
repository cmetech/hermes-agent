# Independent Codex review — BLOCK

Three Important findings demonstrate mismatches in the published contract or its observation adapters. No Python runtime regression was established.

## Reviewer, scope, and coverage

Reviewer: Codex, identified by this session as a GPT-6-based agent. Date: September 5, 2026. The exact deployed model identifier, launcher CLI command, and launcher exit status were not exposed to this reviewer; those must come from the launcher’s records. I invoked no other model, agent, application, or network service.

Verified scope:

| Item | Observed |
|---|---|
| Branch | `feat/workflow-reference-scanner-contract` |
| HEAD | `5c3efdbe757a6fa83cf85cec8a95ea3ff96bf6ed` |
| Tree | `6f7f9d70c3020fa999e65c21dec636c83fd6d5d7` |
| Parent | `74fed08f91014ca7fc80ee9ea4427568ec95b04f` |
| Change set | 34 paths; 12,015 insertions, 206 deletions |
| Candidate-path local changes | None, including the index |

Read coverage:

- Read `AGENTS.md`, the replacement specification and plan, the amended Python publication modules, README, complete scanner JSON data, complete case manifest, observation adapters, and all three new test suites.
- Independently compared the historical canonical fixtures with publication from the base source loaded into memory.
- Traced scanner/classifier, substitution, condition parsing, structured-path proof, compiler validation, authenticated-resource validation, digest decoding, include rewriting, scheduler preflight, CLI startup, and packaging paths.
- Read all changes and relevant surrounding code in the modified existing tests.
- **Incomplete reading:** I did not exhaustively read every unchanged line of the large existing `test_cli.py`, `test_language_schema.py`, and `test_installed_distribution_e2e.py` files, or the entire unchanged packaging/release scripts. This report does not certify completion of that full-read requirement.
- Did not open candidate review verdicts, delivery records, reconciliation reports, `.superpowers/sdd/` ledgers, or the other reviewer’s report.

## Findings

### CODEX-001 — Important: loop-body `when` metadata omits the subsequent text-reference scan

**Candidate location:** [language_schema.py:2536](/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-reference-scanner-contract/plugins/workflow/language_schema.py:2536)

**Unchanged callers:** `plugins/workflow/schema.py:1407`, `:1987`, `:2004`, `:2117`, and `:2988`.

**Requirements:** S1, S5, S7; corpus discrimination under H3.

The projection assigns body `when` fields `scanner_mode: condition-v6` and `caller_policy: body-condition-v6`. The published condition mode identifies reference operands through the condition parser. That does not describe the complete compiler path.

Minimal expression:

```text
$a.output == '$missing.output'
```

Use it on consumer `b`, which directly depends on producer `a`.

Observed:

```text
condition operands [('a', 0, 9)]
grouped False ACCEPT
grouped True WorkflowValidationError [
  ('loop_group_scope_invalid',
   'nodes[0].loop_group.nodes[1].when',
   'scoped-reference-missing-dependency')
]
```

The root workflow accepts the expression. The otherwise equivalent loop-body workflow rejects the reference-looking text inside the quoted RHS.

Causal path:

1. `_normalize_node(..., loop_group_body=True)` validates condition syntax through `validate_v6_condition_syntax`. Its quoted RHS is a literal.
2. `_compile_workflow_source_document` subsequently invokes `_validate_v6_loop_group_references`.
3. That validator enumerates `when` through the shared inventory.
4. It discriminates only Bash surfaces. Every other surface, including `when`, receives previous-reference scanning, masking, and ordinary **text** scanning.
5. Text scanning discovers `$missing.output` inside the quoted RHS and emits the scoped missing-dependency diagnostic.

This is existing Python behavior: `schema.py` and `conditions.py` have no candidate diff. The defect introduced here is the incomplete caller metadata and missing integration discriminator.

**Why current tests miss it:** the projection test asserts the `condition-v6` label directly. Published body-condition witnesses use ordinary literals such as `'ready'`; the low-level condition vectors do not exercise this subsequent compiler pass. A consumer using condition operands alone can satisfy those vectors while disagreeing with Hermes on this workflow.

**Smallest fix:** describe body `when` as a two-stage policy: condition syntax validation followed by the existing text-based scoped-reference validation. Add paired root/body workflow literals containing quoted current and previous reference spellings, including exact diagnostic ordering. Preserve the runtime behavior.

### CODEX-002 — Important: authenticated-resource observations erase portable diagnostic distinctions

**Candidate location:** [reference_scanner_observations.py:71](/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-reference-scanner-contract/tests/plugins/workflow/reference_scanner_observations.py:71)

**Unchanged callers:** `plugins/workflow/schema.py:2296`, `:2370`, `:2020`, and `:2055`.

**Requirements:** S7, H3.

`_error` serializes each `WorkflowValidationError` issue using only its native `code` and `path`. It discards the existing portable `semantic_code`.

A loop group `g` containing producer `a` and command consumer `b`, with no dependency declared by `b`, demonstrates the loss. Pass either authenticated command body through `command_bodies["g/b"]`:

| Body | Native portable diagnostic |
|---|---|
| `$a.output` | `scoped-reference-missing-dependency` |
| `$LOOP_PREV.missing.output` | `scoped-reference-unknown-producer` |

Both real API failures have native code `loop_group_scope_invalid` and path `nodes[0].loop_group.nodes[1].command`.

The adapter produces **the same observation for both**:

```json
{
  "value": null,
  "error": {
    "class": "WorkflowValidationError",
    "issues": [{
      "code": "loop_group_scope_invalid",
      "path": "nodes[0].loop_group.nodes[1].command"
    }]
  }
}
```

Causal path:

1. `validate_authenticated_resource_references` projects the authenticated body map into the group.
2. `_validate_v6_loop_group_references` distinguishes current dependency failure from unknown previous producer.
3. The native issue retains that distinction in `semantic_code`.
4. The new adapter removes it before comparison with the literal expectation.

The runtime distinction predates this candidate. The lossy adapter is new.

**Why current tests miss it:** authenticated-resource literals contain root failures and a successful v6 resource case, but no failing scoped authenticated bodies. YAML-only workflow cases preserve portable codes through a different observation path and therefore do not protect this adapter.

**Smallest fix:** preserve `semantic_code` when present, alongside native code and authored path. Add the two authenticated-body literals above and an adapter assertion requiring different observations. This needs no production runtime change.

### CODEX-003 — Important: unconditional missing-heredoc-terminator metadata contradicts no-candidate behavior

**Candidate location:** [reference_scanner_contract.py:497](/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-reference-scanner-contract/plugins/workflow/reference_scanner_contract.py:497)

**Unchanged callers:** `plugins/workflow/bash_rendering.py:538`, `:1476`, `:1490`, `:1509`; compiler dispatch at `plugins/workflow/schema.py:1873`.

**Requirements:** S3, H3.

The metadata publishes:

```json
"missing_terminator": "rejected"
```

Actual `bash_output_references(..., normalizer_version=6)` observations:

```text
'cat <<EOF'         => []
'cat <<EOF\nbody\n' => BashRenderingError bash_reference_context_unsupported
'echo "'           => []
'echo "$a.output'   => BashRenderingError bash_reference_context_unsupported
```

For `cat <<EOF`, the classifier records a pending heredoc, reaches EOF without encountering a newline, and bypasses its final incomplete-state rejection because that rejection is guarded by `ordered`—the presence of candidate spans.

For the newline-containing example, `consume_heredocs` runs and raises independently. Other unconditional checks, such as ambiguous function declarations, can also reject without candidates. Consequently, “shell-state validation still runs” does not imply unconditional rejection of every incomplete state.

This behavior is unchanged from base. The newly published blanket rule is inaccurate and can make an editor reject an input Hermes’s scanner accepts.

**Why current tests miss it:** `bash.heredoc-missing` exercises the newline/body route. Unterminated quote/command cases include candidates. The manifest acknowledges the candidate guard in prose, but the exported metadata and literals do not encode its distinguishing outcome.

**Smallest fix:** publish the candidate-dependent EOF behavior separately from failures during heredoc consumption. Add the contrasting no-candidate literals above. Do not change the Python classifier.

## Requirements matrix

`PASS` below means the stated Hermes obligation was supported by the inspected source and executed observations; it does not claim universal equivalence.

| ID | Result | Evidence and limits |
|---|---|---|
| H1 | PASS | Seven historical contracts matched base-generated bytes and fixtures. Legacy corpus matched format 1, 11 cases, 7,265 bytes and required SHA-256. V6 differences were exactly the six approved publication paths listed below. |
| S1 | FAIL | Reader 3 activation and required sections are present, but caller/Bash metadata is inaccurate: CODEX-001/003. Studio rejection checks remain deferred. |
| S2 | PASS | Read grammar and boundary implementations; applicable literal observations matched, including malformed suffixes, previous masking, lazy prefixes, overlapping spans and Unicode continuations. |
| S3 | FAIL | CODEX-003 contradicts the published heredoc rule. Other inspected state-family literals matched Python. |
| S4 | PASS | All 29 structured-path literals matched real proof/resolution APIs. Traced containing constraints, refs, unions, conservative unknowns, terminal handling and separate numeric interpretations. |
| S5 | FAIL | Inventory-derived 18 root, 18 body and two control fields are present, with grouped traversal. Body `when` caller policy is incomplete: CODEX-001. |
| S6 | PASS | Root Phase-4 valid/error witnesses matched real compilation, including all published agent/hook leaf paths and diagnostic order. |
| S7 | FAIL | Published YAML diagnostics matched, but body-condition applicability and authenticated portable-ID preservation fail: CODEX-001/002. |
| S8 | PASS | Multi-group and multi-surface workflow witnesses matched compilation. Studio index construction/reuse remains explicitly deferred. |
| S9 | PASS | Scanner/runtime implementation files are unchanged. Publication bounds accepted exact limits and rejected overflow. Studio canvas and pointer-frame performance remain deferred; no benchmark was run here. |
| S10 | UNPROVEN | Explicit profiles and authored code-point offsets are present; Python 3.11/Unicode 14 observations passed. This lane did not execute Python 3.12/3.13. Studio UTF-16 boundary conversion remains deferred. |
| H2 | PASS | No differences in the eight checked runtime/caller files. Applicable literal API observations matched. Include rewriting remains v4; scheduler preflight remains v3. This is not a full runtime-suite pass. |
| H3 | FAIL | Expectations are literal publication data and digests verified, but CODEX-002 removes a promised observable distinction. Finite coverage cannot establish universal equivalence. |
| H4 | UNPROVEN | Inspected real-venv direct-wheel/sdist-wheel installation, resource-origin checks, actual console execution and socket guard. Could not execute builds/installations in this read-only lane. |
| H5 | PASS | Eight source CLI combinations succeeded; invalid formats, section shapes/counts and multibyte overflow failed before stdout. Three new suites are registered in the release gate. Installed startup remains under H4’s limitation. |

## Verification results

### Scope and runtime preservation

Executed Git checks included:

```sh
git rev-parse HEAD HEAD^{tree} HEAD^
git branch --show-current
git diff --stat 74fed08f91014ca7fc80ee9ea4427568ec95b04f 5c3efdbe757a6fa83cf85cec8a95ea3ff96bf6ed
git status --short
git diff --check 74fed08f91014ca7fc80ee9ea4427568ec95b04f 5c3efdbe757a6fa83cf85cec8a95ea3ff96bf6ed
```

`git diff --check` returned 0. A candidate-path-specific check found no worktree or index changes across the 34 paths.

The base-to-candidate diff was empty for:

```text
plugins/workflow/bash_rendering.py
plugins/workflow/resources.py
plugins/workflow/conditions.py
plugins/workflow/schema.py
plugins/workflow/includes.py
plugins/workflow/scheduler.py
plugins/workflow/output_resolution.py
plugins/workflow/trust.py
```

### Historical bytes and digest checks

A read-only Python probe loaded `git show 74fed08f:plugins/workflow/language_schema.py` into an in-memory module, generated its contracts, and compared canonical bytes against fixtures and candidate publication. Exit 0.

| Historical pair | Canonical bytes | Base = fixture = candidate |
|---|---:|---|
| Legacy 1 | 227,906 | Yes |
| Legacy 2 | 227,906 | Yes |
| Archon 1 | 224,201 | Yes |
| Archon 2 | 224,201 | Yes |
| Archon 3 | 236,325 | Yes |
| Archon 4 | 242,648 | Yes |
| Archon 5 | 243,698 | Yes |

Old Archon 6 matched its characterization fixture: 283,522 bytes, SHA-256 `171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc`.

Legacy corpus matched:

```text
7265 bytes
c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b
```

An independent recursive comparison found only:

```text
/contract_digest
/contract_reader_version
/limits/max_contract_bytes
/limits/section_max_bytes/reference_scanner_v1
/reference_scanner_v1
/semantic_rules/3/field_paths
```

Current publication measurements:

| Artifact | Canonical bytes | Embedded digest verified |
|---|---:|---|
| Archon contract | 315,756 | `sha256:02830acc6e3c797cec14e7debc3b2d15f91eca514bb9d2ffe31bd3b74e2549ed` |
| Archon corpus | 200,496 | `sha256:df14cc2dc8adec213e98a126360943b9b831426ef43304632a7582288ecd6384` |
| Scanner contract section | 31,879 | Bound by contract digest |

The scanner section has 121 bytes of remaining section capacity. Corpus counts are 54 workflow, 132 scanner, 23 substitution, and 29 structured-path cases.

### Real API observations

Executed this read-only adapter campaign:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import json,sys; from pathlib import Path; sys.path.insert(0,str(Path("tests/plugins/workflow").resolve())); import reference_scanner_observations as o; d=json.loads(Path("plugins/workflow/conformance/reference_scanner_v1.json").read_text()); count=0; skipped=[]; mismatches=[]
for section,cases in d.items():
 for c in cases:
  if c["unicode_profiles"] != ["all"] and o.profile_id() not in c["unicode_profiles"]: continue
  if c["api"]=="compute_package_digest": skipped.append(c["id"]); continue
  actual= o.observe_structured_path_case(c) if section=="structured_path_cases" else (o.observe_scanner_case(c,Path("unused-probe-directory")) if section=="scanner_cases" else o.observe_substitution_case(c,Path("unused-probe-directory")))
  if actual!=c["expected"]: mismatches.append((c["id"],actual,c["expected"]))
  count+=1
print(json.dumps({"profile":o.profile_id(),"observations":count,"mismatches":mismatches,"unexecuted_io_cases":skipped}))'
```

Exit 0:

```text
profile: python-3.11-unicode-14.0.0
observations: 175
mismatches: []
```

Seven `compute_package_digest` literals were intentionally unexecuted because they materialize files.

Executed compiler observations for all workflow cases:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'from plugins.workflow.language_conformance import workflow_language_conformance; from plugins.workflow.models import WorkflowLanguageProfile as P,WorkflowValidationError; from plugins.workflow.schema import parse_workflow_source_bytes,_compile_workflow_source_document
for p in P:
 cases=workflow_language_conformance(p)["cases"]; bad=[]
 for c in cases:
  src=parse_workflow_source_bytes(c["id"]+".yaml",workflow_bytes=c["definition_yaml"].encode(),sidecar_bytes=c.get("companion_yaml","").encode() or None)
  try:
   pkg=_compile_workflow_source_document(src,normalizer_version=c["normalizer_version"]); issues=pkg.validation_issues
  except WorkflowValidationError as e: issues=e.issues
  actual=[(i.code,i.path,getattr(i,"semantic_code",i.code),i.severity,i.blocking) for i in issues]; expected=[(d["hermes_code"],d["path"],d["code"],d["severity"],d["blocking"]) for d in c["diagnostics"]]
  if actual!=expected: bad.append((c["id"],actual,expected))
 print(p.value,"cases",len(cases),"mismatches",bad)'
```

Exit 0:

```text
hermes-legacy cases 11 mismatches []
archon-2026-07 cases 54 mismatches []
```

An initial version of this probe accessed `semantic_code` unconditionally and exited 1 with `AttributeError` on ordinary `ValidationIssue`. The corrected probe above uses the native code when no portable override exists.

### Finding reproductions

CODEX-001 was executed with this input construction and real compiler calls:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'from pathlib import Path; import json; from plugins.workflow.schema import parse_workflow_source_bytes,_compile_workflow_source_document; from plugins.workflow.conditions import validate_v6_condition_syntax; from plugins.workflow.language_schema import reference_scanner_interpolation_surface
expr="$a.output == "+chr(39)+"$missing.output"+chr(39)
print("expression",repr(expr)); print("condition operands",[(r.node_id,r.start,r.end) for r in validate_v6_condition_syntax(expr)])
for grouped in (False,True):
 nodes=[{"id":"a","prompt":"Produce"},{"id":"b","depends_on":["a"],"prompt":"Consume","when":expr}]
 if grouped: nodes=[{"id":"g","loop_group":{"until":"done","max_iterations":2,"nodes":nodes}}]
 source=parse_workflow_source_bytes(Path("synthetic.yaml"),workflow_bytes=json.dumps({"name":"probe","description":"probe","nodes":nodes}).encode(),sidecar_bytes=b"language_compatibility: archon-2026-07\n")
 try:
  _compile_workflow_source_document(source,normalizer_version=6); print("grouped",grouped,"ACCEPT")
 except Exception as e: print("grouped",grouped,type(e).__name__,[(i.code,i.path,i.semantic_code) for i in e.issues])
print([f for f in reference_scanner_interpolation_surface()["fields"] if f["relative_path"]=="when"])'
```

Exit 0; results appear under CODEX-001.

CODEX-002 reproduction:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import json,sys; from pathlib import Path; sys.path.insert(0,str(Path("tests/plugins/workflow").resolve())); import reference_scanner_observations as o; from plugins.workflow.schema import parse_workflow_source_bytes,_compile_workflow_source_document,validate_authenticated_resource_references
text=json.dumps({"name":"probe","description":"probe","nodes":[{"id":"g","loop_group":{"until":"done","max_iterations":2,"nodes":[{"id":"a","prompt":"Produce"},{"id":"b","command":"consume"}]}}]})
for body in ("$a.output","$LOOP_PREV.missing.output"):
 c={"api":"validate_authenticated_resource_references","normalizer_version":6,"input":{"definition_yaml":text,"companion_yaml":"language_compatibility: archon-2026-07\n","command_bodies":{"g/b":body},"named_script_bodies":{}}}
 try: validate_authenticated_resource_references(o._resource_package(c,Path("unused")),command_bodies=c["input"]["command_bodies"],named_script_bodies={})
 except Exception as e: print("native",repr(body),[(i.code,i.path,i.semantic_code) for i in e.issues])
 print("adapter",o.observe_scanner_case(c,Path("unused")))'
```

Exit 0; distinct native portable IDs collapsed to the identical adapter observation reported above.

CODEX-003 and Unicode-digit observations:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import sys,unicodedata; from plugins.workflow.bash_rendering import bash_output_references,classify_bash_reference_spans; print(sys.version.split()[0],unicodedata.unidata_version)
for s in ["cat <<EOF","cat <<EOF\nbody\n", "echo \"", "echo \"$a.output", "²>file declare -i x=$a.output", "x>file declare -i x=$a.output", "𑽐>file declare -i x=$a.output"]:
 try: print(repr(s),"=>",[(r.node_id,r.start,r.end) for r in bash_output_references(s,normalizer_version=6)])
 except Exception as e: print(repr(s),"=>",type(e).__name__,getattr(e,"code",None))'
```

Exit 0, Python 3.11.16/Unicode 14.0.0. Heredoc/quote results appear under CODEX-003. The superscript-digit declaration rejected; the letter and unassigned Kawi-digit variants admitted `a` at `[20,29)`.

### CLI and bounds

Executed source CLI commands through subprocesses with an explicit environment containing only `PATH`, `PYTHONDONTWRITEBYTECODE`, and a synthetic `HERMES_HOME`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c 'import subprocess,sys,json; from pathlib import Path
for p in ("hermes-legacy","archon-2026-07"):
 for action in ("schema","schema-corpus"):
  for pretty in (False,True):
   cmd=[sys.executable,"-m","hermes_cli.main","workflow",action,"--profile",p]+([] if pretty else ["--json"])
   r=subprocess.run(cmd,capture_output=True,env={"PATH":"/usr/bin:/bin","PYTHONDONTWRITEBYTECODE":"1","HERMES_HOME":str(Path("unused-review-home").resolve())},timeout=30)
   print(p,action,"pretty" if pretty else "compact","exit",r.returncode,"stdout_bytes",len(r.stdout),"stderr",r.stderr.decode())
   if r.returncode==0: assert json.loads(r.stdout)["profile"]==p'
```

All eight child commands exited 0 with empty stderr:

| Profile | Publication | Compact bytes, including newline | Pretty bytes, including newline |
|---|---|---:|---:|
| Legacy | Schema | 227,907 | 502,880 |
| Legacy | Corpus | 7,266 | 8,928 |
| Archon | Schema | 315,757 | 663,583 |
| Archon | Corpus | 200,497 | 255,732 |

Additional in-memory calls to `emit_schema_corpus` established:

- `True`, `1.0`, `2.0`, unknown format 3, and Archon format 1 rejected with zero stdout.
- Every section accepted its exact count and rejected count-plus-one and dictionary-shaped sections with zero stdout.
- A multibyte payload of exactly 384,000 canonical bytes emitted successfully; one additional byte rejected with zero stdout.

Direct `_require_contract_bounds` observations established:

```text
total-exact OK
total-over ValueError workflow authoring contract exceeds 324000 bytes
section-exact OK
section-over ValueError workflow authoring contract reference_scanner_v1 exceeds 32000 bytes
```

### Sandbox failures and unexecuted verification

Attempted:

```sh
PYTHONDONTWRITEBYTECODE=1 scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_baselines.py -q -p no:cacheprovider
```

The wrapper was attempted twice; the second invocation retained its exit status. Exit **1**, zero tests ran:

```text
FileNotFoundError:
No usable temporary directory found in
['/tmp', '/var/tmp', '/usr/tmp', '<authorized-worktree>']
```

A shell-heredoc Python invocation also failed before Python started:

```text
zsh:1: can't create temp file for here document: operation not permitted
```

Subsequent probes used `python -c` without filesystem writes.

Not executed here:

- Wheel/sdist builds and installed-console integration tests.
- Python 3.12/3.13 observation matrix.
- Seven digest-owned filesystem literals.
- Workflow/repository suites, native Linux/Windows execution, or Studio acceptance.

Controller follow-up commands, **unexecuted in this lane**, are:

```sh
scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_conformance.py -q
scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -k 'scanner or corpus_resources' -q
```

The supplied historical test totals were not independently reproduced and are not counted as this reviewer’s passes.

## Final candidate-file status

Final commands:

```sh
git diff --name-only HEAD -- plugins/workflow tests/plugins/workflow pyproject.toml scripts/test_workflow_merge_gate.sh
git diff --cached --name-only HEAD -- plugins/workflow tests/plugins/workflow pyproject.toml scripts/test_workflow_merge_gate.sh
git rev-parse HEAD HEAD^{tree} HEAD^
git branch --show-current
```

Both diff outputs were empty. HEAD, tree, parent, and branch remained pinned. The earlier exact 34-path check was also empty. Unrelated historical documents and launcher records were left untouched; no candidate files or review records were written by this reviewer.