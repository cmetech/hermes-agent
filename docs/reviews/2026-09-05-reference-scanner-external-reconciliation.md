# External reference-scanner review reconciliation

Status: complete. All three Codex findings were independently confirmed, corrected and closed by the scoped external correction review. Full workflow and focused verification pass. No open findings remain in this correction.

## Scope and review provenance

The user requested an additional independent Claude/Codex adversarial review of the delivered Hermes amendment. Candidate `5c3efdbe757a6fa83cf85cec8a95ea3ff96bf6ed`, parent `74fed08f91014ca7fc80ee9ea4427568ec95b04f`, tree `6f7f9d70c3020fa999e65c21dec636c83fd6d5d7`: one commit, 34 paths, +12,015/-206. Both external CLIs reviewed the same candidate in the existing feature worktree, supervised by separate subagents; neither received the other lane's report. Six unrelated historical documents were hashed and preserved.

The shared [prompt](2026-09-05-reference-scanner-external-adversarial-prompt.md) adapts the September 4 loop-group prompt and reconciliation. Submitted prompt SHA-256: `1eeccf62155c72607e28eae374ce94c9a691fc0206b7ea580831091438ab91e8`.

- Claude CLI 2.1.212, actual `claude-opus-4-8[1m]`, high effort: [original report](2026-09-05-reference-scanner-external-claude.md). Exit 0. Its 22+1,063 test passes have runner receipts, but its admitted incomplete literal reading made its unconditional PASS too broad. The [bounded independent completion addendum](2026-09-05-reference-scanner-external-claude-addendum.md) supplies complete literal/manifest reading and evidence corrections. It reports zero defects with a qualified PASS and explicitly retains remaining source/test omissions.
- Codex CLI 0.153.4, explicit `gpt-6-astra`, high effort: [original report](2026-09-05-reference-scanner-external-codex.md). Exit 0. BLOCK with three Important findings. Two earlier incomplete attempts (launcher-role misunderstanding and model capacity failure) are preserved and not counted as reviews. It executed 175 API observations, 65 workflow diagnostic cases, historical-byte/digest checks, eight source CLI combinations, and exact/overflow publication bounds. Its pytest attempt could not create temporary files; zero tests ran in that attempt.

Neither report is rewritten to match the controller's conclusions. Raw events, exact argv, permission failures, prompt hashes and tool receipts are retained in the local external-review workspace. No merge, push, Studio edit, new worktree, or runtime syntax change is authorized by this review.

## Consolidated findings and validity

| ID | Source | Severity | Independent validity | Disposition |
| --- | --- | --- | --- | --- |
| RSC-001 | CODEX-001 | Important | Confirmed: a quoted reference on the RHS of body `when` is checked by the later text-based scoped pass, although condition parsing treats it as a literal. | Corrected: two-stage body-when policy and six paired workflow witnesses |
| RSC-002 | CODEX-002 | Important | Confirmed: authenticated current-missing-dependency and previous-unknown-producer errors have different native semantic codes but identical adapter observations. | Corrected: optional semantic_code retained; two authenticated-body witnesses and direct adapter regression |
| RSC-003 | CODEX-003 | Important | Confirmed: missing-heredoc rejection depends on whether EOF occurs before consumption and whether candidates exist. | Corrected: candidate-dependent EOF policy and four contrasting literals |
| OBS-1 | CLAUDE-OBS-1 | Non-finding | Current delimiter metadata matches Python. A possible future drift test improvement is not a demonstrated candidate mismatch. | No runtime/architecture expansion; documented observation |
| OBS-2 | CLAUDE-OBS-2 | Non-finding | Scanner metadata occupies 31,879/32,000 bytes; 121 bytes headroom is real and within the approved bound. | Preserve bound; measure corrections |

### RSC-001

Controller compiled `$a.output == '$missing.output'` on consumer `b` depending on `a`, once at root and once inside group `g`. Root accepted; body failed at `nodes[0].loop_group.nodes[1].when` with native `loop_group_scope_invalid`, portable `scoped-reference-missing-dependency`. Source trace confirms condition syntax normalization precedes `_validate_v6_loop_group_references`, which text-scans `when`, including quoted text. The existing Python semantics are the authority; only their published description and discrimination witnesses require correction.

### RSC-002

Controller passed authenticated command bodies `$a.output` and `$LOOP_PREV.missing.output` to child `g/b` without a current dependency. Native issues respectively retain `scoped-reference-missing-dependency` and `scoped-reference-unknown-producer`, at identical path `nodes[0].loop_group.nodes[1].command` with identical native code `loop_group_scope_invalid`. The candidate's `_error` reduces both to native code/path, making the observations equal. This is a confirmed loss of a required compatibility distinction, not an argument to change runtime errors.

### RSC-003

Controller independently observed:

| Literal input | Existing Python observation |
| --- | --- |
| `cat <<EOF` | Empty reference list |
| `cat <<EOF\nbody\n` | `BashRenderingError`, `bash_reference_context_unsupported` |
| `echo "` | Empty reference list |
| `echo "$a.output` | `BashRenderingError`, `bash_reference_context_unsupported` |

The classifier's final incomplete-state check is guarded by candidate spans. Heredoc consumption after a newline has its own rejection path. Source diff proves the classifier, schema validation and conditions are unchanged from the original base. The new unconditional metadata is the error.

## Verification and remaining boundary

Controller independently rechecked legacy format 1 / 11 cases / 7,265 bytes / exact SHA-256, no legacy corpus digest, Archon self-digest exclusion and contract-digest binding; all passed. Claude's reported successful conformance file includes its unconditional three-interpreter matrix, contrary to its initial prose claiming only 3.11 was executed; raw receipt and test structure establish the correction.

Seven expected regression assertions failed before correction (metadata, missing discriminator literals and semantic-code loss), without environment/import errors. The verification and final measurements below cover the frozen correction. The scoped external correction review closes all three findings. The containing feature-branch commit delivers the reviewed correction and evidence. Studio indexing, canvas performance and UTF-16 boundary behavior remain deferred. The prior full repository result remains red for independently reproduced baseline/environment failures; no claim of a green full repository is made.

## Reconciling reviewer disagreement and coverage

Claude's qualified PASS and lack of findings do not refute the three independently reproduced mismatches. Codex supplied concrete counterexamples; the controller reproduced each through real APIs and confirmed the affected runtime source is unchanged from base. Those results determine disposition without voting between model verdicts.

Claude's addendum completes the previously omitted full literal JSON and case manifest, the new contract/conformance tests, and remaining AGENTS instructions. It still does not establish exhaustive reading of unchanged portions of large existing source/tests. Codex likewise explicitly limits exhaustive reading of existing large tests/scripts, while directly tracing the relevant runtime callers and reading the literals/new suites. Neither lane's result is treated as universal equivalence or native-platform proof. No further broad reading/review cycle was launched.

The addendum withdraws OBS-1's hardening suggestion based partly on the repository's ban on source-text tests. That ban does not prohibit behavioral comparisons or comparisons with imported runtime constants. The controller's narrower disposition is simply that the current delimiter metadata is correct and no present mismatch was demonstrated; optional future hardening is outside this correction. OBS-2 remains an accurate size observation, not a reason to raise bounds.

For reuse, the saved prompt now explicitly addresses the running model as the single reviewer, preventing the initial Codex launcher-role misunderstanding. Only that introductory role wording changed after the independent runs; the review scope and evidence requirements are unchanged. Final reusable-prompt SHA-256: `641e1d5e69087836b15d53ce1bccb6878b01cc65ae105c9b44cf28542ea67971`. The submitted original is retained with raw provenance.

## Correction and verification

One subagent implemented all three confirmed findings across nine existing metadata, publication, corpus/manifest and test files. Existing literal entries retain their bytes, order and IDs. Six workflow cases and six scanner cases were added; expected outcomes were authored from the unchanged source semantics before executing them. No scanner, renderer, schema validator, include, scheduler, output-resolution or trust runtime file changed.

`body-when` now explicitly describes condition-v6 syntax followed by previous-text scanning, equal-length masking, and current-text scanning. The quoted current/previous/both root/body witnesses prove acceptance differences and previous-before-current diagnostics. The adapter retains semantic_code only when present, preserving native-only historical observations. Heredoc metadata separates consumption after a newline from candidate-guarded EOF checks; the redundant quoted-body Boolean was removed while its exact meaning remains in `body_references: rejected-even-when-delimiter-quoted` and a regression assertion. Limits and versions were not raised.

All pytest commands used `scripts/run_tests.sh`. Implementation commands used `HERMES_TEST_FILE_RETRIES=0`.

| Verification | Exact selection and result |
| --- | --- |
| RED then GREEN | Contract/conformance/language-conformance files with `-k 'body_when_publishes or heredoc_metadata or authenticated_observations or literal_corpus_covers_scoped or quoted_when_literals' -q`: seven expected assertion failures, then seven passes |
| Focused campaign | `test_reference_scanner_baselines.py test_reference_scanner_contract.py test_reference_scanner_conformance.py test_language_conformance.py test_language_schema.py test_cli.py test_phase6_language.py -q`, all under `tests/plugins/workflow/`: 1,223 passed and one prerequisite failure on the first invocation |
| Affected conformance file after prerequisite correction | `scripts/run_tests.sh tests/plugins/workflow/test_reference_scanner_conformance.py -q`: 198 passed, including the three Python/Unicode environments; 188 applicable literal observations per profile |
| Installed artifacts | `scripts/run_tests.sh tests/plugins/workflow/test_installed_distribution_e2e.py -m integration -k 'scanner or corpus_resources' -q`: two passed; real direct-wheel/sdist-built-wheel offline resource and console proofs |
| Complete workflow suite | `scripts/run_tests.sh tests/plugins/workflow -q`: exit 0; 136 files, 6,318 passed, 0 failed, 44 Darwin host-marker skips; 183.1 seconds; no flaky files reported |
| Preservation and publication | Seven historical authoring pairs and exact legacy corpus pass baseline tests; controller independently verified final digests, sizes and contract/corpus binding; runtime diff empty |

The initial focused failure was `ModuleNotFoundError: psutil` in the deliberately minimal temporary matrix environments. New authenticated loop-group cases enter existing capacity normalization, importing executors/store/lease_clock. Adding the already-declared dependency to the offline temporary-venv prerequisite list fixed the actual path; cached psutil 7.2.2 was used. No runtime dependency declaration or existing worktree environment changed and no API was mocked. The affected file was rerun in full. Together with the six unchanged passing files, this covers 1,224 distinct focused tests; the initial seven-file invocation itself was not green.

The prior complete repository suite was not rerun for this bounded metadata/corpus correction. Its red result and exact starting-commit comparison remain in the [original verification record](2026-09-04-declarative-reference-scanner-verification.md): 51,850 passed, 103 failed, 657 skipped, plus file-descriptor exhaustion; all 103 exact failures and the resource condition reproduced at the original base. This follow-up makes no green full-repository claim.

## Final corrected publications

These values were independently recomputed by the controller after the code froze:

| Artifact | Canonical bytes | Embedded digest |
| --- | ---: | --- |
| Archon authoring contract |315,851|`sha256:f435a385f26c971d37f3c691ed6f42d2aa76e7b0c24b199455f4002b0ab1976f`|
| Archon corpus |207,930|`sha256:ebbd30326de23984e254929774b4dd7d7f8a71f59c050da7a3ac9bfe190256a7`|

The scanner section is 31,974/32,000 bytes, leaving 26 bytes. The whole contract leaves 8,149 usable bytes after its 4,000-byte reserve. Pretty corpus JSON is 265,706 bytes, or 265,707 including the print newline. Corpus counts are 60 workflow, 138 scanner, 23 substitution and 29 structured-path cases; 190 literal API cases total, 188 applicable to each supported Python/Unicode profile. Publication limits remain unchanged. The legacy corpus remains format 1, 11 cases, 7,265 bytes and SHA-256 `c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b`.

Studio must pin the corrected artifact pair when its separately approved implementation starts. All Studio-specific indexing, canvas-performance and UTF-16-boundary obligations remain deferred. This record supersedes the original delivery's current-artifact measurements; that dated record remains historical evidence for `5c3efdbe75`.

## Final correction review and delivery boundary

The [scoped external Codex correction review](2026-09-05-reference-scanner-external-codex-fix-review.md) marks CODEX-001, CODEX-002 and CODEX-003 **ADDRESSED**, with no new fix-diff regression. It reviewed the exact nine-path patch, affected functions and all 12 new literals, and verified matching source hashes before and after. The controller observed the full-workflow process exit 0; the reviewer correctly attributes the log summary to controller evidence.

The [durable invocation/provenance record](2026-09-05-reference-scanner-external-provenance.json) preserves actual models, exact argv, incomplete attempts, report hashes, frozen correction hashes and full-workflow log hash. No additional broad architecture, plan or whole-branch review cycle was run.

All intended fixes and review artifacts are committed only on `feat/workflow-reference-scanner-contract`. The six unrelated historical documents remain intact and excluded. This work does not merge into `base`, push, or begin Workflow Studio changes. Those actions still require the user's separate explicit approval. Remaining native-platform and Studio acceptance limits are documented above.
