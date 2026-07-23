# Workflow v3.0.3 SDD Progress

Task 3.0: complete (commits `265488dd8`, `9b1da9948`, and `4f8c18da0`;
final read-only task review approved with Critical 0 / Important 0 / Minor 0).
Bedrock prospective classification now imports
its model-ID predicate from side-effect-free model metadata rather than the
lazy-installing adapter, and both Claude/Messages and non-Claude/Converse modes
are known Hermes-managed loops. The resolver again preserves zero-config-read
MoA and explicit Azure-Anthropic short-circuits, using one lazy cached model
snapshot only for actual OpenAI app-server normalization. RED was 177 passed/
4 failed; GREEN is 182 focused runtime tests, 435 with Bedrock adapter/metadata
and fresh-process purity, and 735 for the complete Task 3.0 selection. The final
base gate is 888 Python passed/1 skipped, installed distribution 1 passed,
Desktop 93 passed, and TypeScript compilation green. Ruff, diff, and canonical
customization checks are green.

Branch base: `f1704cc0035f435275268b94df8189cb1551fa15`

Baseline gate (2026-07-21): 775 Python passed, 1 skipped; installed-distribution 1 passed; Desktop 90 passed across 11 files; TypeScript compilation passed.

Task 0: complete (commit `3b35e3a56`, task review clean).

Task 1.1: complete (commit `6d7aad8c8`, task review clean).

Task 1.2: complete (commit `67d0be2e6`, review approved). Minor carried forward: tighten catalog log tests to assert the exact class-only message, not merely absence of the bundle path.

Task 1.3: complete (commit `9c456a876`, concurrency review clean).

Task 1.4: complete (commits `914beac14` + `ecc1de6c6`; sidecar TOCTOU finding fixed; re-review clean).

Task 1.5: complete (commit `de90ab065`, review clean; 57-test gate/showcase selection passed).

Task 1.6: complete (commit `a807be57b`, task review spec-compliant and quality approved; no findings).

Task 1.7: complete (commit `bb6201e78`, task review spec-compliant and quality approved; no findings).

Workstream 1 exit: complete at `bb6201e78` (base gate: 789 passed, 1 skipped; installed distribution: 1 passed; Desktop: 91 passed across 11 files; TypeScript compilation passed).

Task 2.0: complete (commits `414a6734d`, `2a91274c7`, `0957c745d`, `45ff5e100`, `9e2a8e7c0`, `555845bde`; final task review spec-compliant and quality approved; focused gate 234 passed).

Task 2.1 review Minor carried forward: the unreachable laptop interpretation node was removed, but `commands/interpret-report.md` remains authenticated/orphaned and `docs/design/portable-workflow-orchestration.md` still promises an opt-in interpretation branch; final review must decide whether to remove/relocate the resource and align the design.

Task 2.1: complete (commits `382646328` + `b872d2dba`; deterministic persistent-session contamination fixed; final task review spec-compliant and quality approved; broad offline gate 269 passed and merge-gate metadata 18 passed). One non-blocking Minor remains carried above.

Task 2.2: complete (commit `fd34557dc`; declared/legacy-flat contract split,
typed endpoint byte bounds, verified-showcase reservation scoping, and
store-level text/local-file/verified-fixture bounds implemented; focused 9
passed, full store/Desktop API 69 passed, broad workflow/merge-gate selection
276 passed, installed-distribution 1 passed, Ruff/diff/customization gates
green). Task 2.3 retains text classification/projection; Task 2.4 retains
authenticated binding/fixture parsing, translation, and read-once acquisition.

Task 2.2 Important review fix: complete (commit `04ec3d219`; raw escaped
unpaired surrogates now fail as ordinary Pydantic 422 detail lists without raw
value disclosure; focused 1 passed, full Desktop API 57 passed, broad workflow
selection 277 passed, Ruff/diff/customization gates green).

Task 2.2 second Important review fix: complete (commit `11831703f`; surrogate
keys, malformed non-mapping values, and nested malformed values now fail as
serializable ordinary Pydantic 422 responses; omitted `values` still uses its
empty-dict default; focused 6 passed, full Desktop API 62 passed, broad
workflow selection 282 passed, Ruff/diff/customization gates green).

Task 2.2 third Important review fix: complete (commit `a6a855069`; every former
whole-model StartRunRequest error path now redacts input values through one
pre-validation invariant, with blank checks field-local; canary RED 6 failed,
focused GREEN 13 passed, full Desktop API 68 passed, broad workflow selection
288 passed, Ruff/diff/customization gates green).

Task 2.2 final review: spec-compliant and quality approved at `a6a855069`; no
remaining Critical, Important, or Minor findings.

Task 2.3: complete (commit `e909d2049`; declared text classification,
effective `min(declared max_bytes, 64 KiB)` catalog/detail projection,
legacy-flat compatibility, and ordinary declared-text API admission; corrected
RED 3 failed/1 compatibility guard passed, focused GREEN 4 passed, broad
workflow/merge-gate selection 247 passed, Ruff/diff/customization gates green;
fresh task review clean with no findings). Task 2.4 retains authenticated
fixture/binding parsing, translation, read-once staging, restamping, and the
showcase file-input policy flip; Task 2.5 retains Desktop forms.

Task 2.4: complete (commit `874be012e`); authenticated
fixture/binding schema, public-to-internal translation/reservation, verified
read-once fixture staging, sealed input identity/isolation, exact three-of-five
showcase support, installed-wheel fixture proof, and the W3-1 ai-extensions
metadata correction are implemented. Focused selection 204 passed, broad
workflow/isolation selection 289 passed, explicit installed distribution 1
passed, and the full base merge gate passed with 838 Python passed/1 skipped,
installed distribution 1 passed, Desktop 91 passed across 11 files, and
TypeScript compilation green. Fresh review's one Important namespace/staged-
filename collision finding was fixed with exact RED/GREEN tests; re-review is
clean with no remaining findings. Ruff/diff/customization checks are green
before and after the atomic commit; post-commit installed-wheel and exact
authenticated catalog/package digest confirmation are green.

Task 2.5: complete (commit `413facea0`); declared Desktop text inputs now
render through the shared textarea with server-projected UTF-8 byte counters,
exact-bound acceptance, visible and submit-time over-bound refusal, and
public-only value serialization. Authenticated bundled file fixtures render as
read-only named rows with no upload/path/edit control and never enter caller
values. Catalog and View-to-Review paths, Task 2.4 laptop run support, existing
scalar controls, and Task 1.6 disable/copy precedence remain covered. All four
actual supported locales (`en`, `ja`, `zh`, `zh-hant`) have exact copy and
parity coverage; the handoff's erroneous `en/de/es/fr` assumption was
adjudicated against the repository without inventing locales. RED was 4 failed
and 75 passed; focused GREEN was 79 passed, full workflow UI was 126 passed,
i18n runtime was 8 passed, and typecheck/lint/format/diff/customization gates
were green before and after the atomic commit. Fresh read-only review found no
Critical, Important, or Minor issues. Task 2.6 retains real-middleware laptop
approve/reject/evidence execution coverage.

Task 2.6: complete (commit `c1cbe4679`). A production-mounted
middleware E2E admits the authenticated laptop-diagnostic showcase, seals its
fixture and start material, executes real coordinator approve and bounded
reject/rework branches offline, proves zero model-runner calls on deterministic
rework, and scans all persisted/API/log/error evidence surfaces for raw input
canaries. The new test is selected exactly once in the base gate and native
three-OS CI matrix, with a YAML-structural job/matrix guard; the installed-wheel
proof remains gate-selected. Fresh review's one Important seam finding was
fixed by mutating the fixture inside the real `prepare_run_snapshot` wrapper
after exact POST `verified_inputs` observation and before original sealing;
strict RED was 19 passed/2 failed and GREEN is 21 passed. Re-review is clean
with no Critical, Important, or Minor findings. Capped broad offline coverage
is 833 passed, explicit installed distribution is 1 passed, and the final full
Workstream 2 base gate is 841 Python passed/1 skipped plus installed
distribution 1 passed, Desktop 93 passed, and TypeScript compilation green.
Ruff/diff/customization checks are green before and after the atomic commit;
post-commit focused coverage is 21 passed, installed distribution is 1 passed,
and commit integrity is clean. The protected untracked Revision 6 review
request remained untouched.

Task 3.0 review fix: the prospective runtime classifier now consumes configured
provider/model/selected-provider metadata and shares pure effective-mode
derivation with the actual public runtime resolver. RED was 4 passed/21 failed;
GREEN is 26 classifier/parity tests, 177 runtime-provider/classifier tests, and
477 focused worker/MCP/executor/process/Windows/gate tests. The full base gate is
885 Python passed/1 skipped, installed distribution 1 passed, Desktop 93 passed,
and TypeScript compilation green. Unknown and malformed metadata fail closed;
the resolved mode stays authoritative except the explicit valid app-server
override; every non-capability runtime field is preserved. Task 3.0 is ready for
orchestrator re-review.

Task 3.0 implementation: ready for orchestrator review. The isolated
plugin-agent worker now owns enabled request-MCP resolution, runtime capability
preflight, discovery, connected-status enforcement, tool-policy ordering, and
one outer cleanup boundary. Required MCP failures preserve exact
`package_mcp_unavailable`; incapable runtimes fail before MCP/AIAgent/provider;
real echo-MCP/local-provider success and seven post-start cleanup failures prove
PID reap plus loader/timeout/callback/hook/inline/session/registry restoration.
The pure runtime matrix, worker/executor contracts, process/Windows selections,
release-gate membership, customization ledger, and full offline base gate are
green (869 Python passed/1 skipped, installed distribution 1 passed, Desktop 93
passed, TypeScript compilation passed). Final completion/review status remains
with the orchestrator.

Task 3.1: complete (commits `68951555d`, `0510930aa`, and `98f96ad4d`;
final read-only task review approved with Critical 0 / Important 0 / Minor 0).
Frozen/slotted
server-owned runner and execution-context records now drive the full entitlement
× surface × runner × runtime capability truth table. Catalog, detail, admission,
coordinator scheduling, and execution-time corroboration recompute compatibility
then risk from the current immutable binding, while the verified showcase cache
retains only context-free authenticated package/tree material. Capable AI
showcase and trusted user MCP admission pass; incapable and app-server contexts
return typed compatibility refusal with zero run/staging residue. RED covered
truth-table, raw-cache, projection/admission, coordinator, fire-time, and gate
membership failures. GREEN is 24 focused binding tests, 330 consolidated
workflow tests, standalone Desktop API 77 passed, installed distribution 1
passed, and the full base gate at 913 Python passed/1 skipped, Desktop 93 passed,
and TypeScript compilation green. Ruff, diff, customization, and exact gate/CI
membership checks pass. The protected untracked Revision 6 review request
remains untouched.

Task 3.1 Important review fix: complete at `0510930aa`.
Catalog projection now carries the authenticated showcase
read budget through compatibility and risk, compares the recomputed package
digest to the cached authenticated digest before verified provenance, and omits
the full showcase set on mismatch while preserving user rows. Warm-cache byte
restoration moved outside the cache metadata lock. RED was 0 passed/2 failed;
GREEN is 2 regressions, 139 focused binding/catalog/cache tests, and the full
base gate at 915 Python passed/1 skipped, installed distribution 1 passed,
Desktop 93 passed, and TypeScript compilation green. Ruff, diff, and canonical
customization checks pass.

Task 3.1 second Important review fix: complete at `98f96ad4d`; final re-review
clean. A real authenticated showcase command deletion now omits the
complete showcase set without hiding independent user rows. The catch remains
bounded to catalog capacity, invalid definition, resource capacity, workflow
validation, and filesystem failures; programming/security failures propagate.
The warning is pinned to the exact class-only sanitized message. RED was 0
passed/1 failed; GREEN is 3 snapshot/digest/deletion regressions, 140 focused
binding/catalog/cache tests, and the full base gate at 916 Python passed/1
skipped, installed distribution 1 passed, Desktop 93 passed, and TypeScript
compilation green. Ruff, diff, and canonical customization checks pass.

Task 3.2: complete (commits `5e5662ce6` and `854033420`; final read-only task
review approved with Critical 0 / Important 0 / Minor 0). The obsolete
CLI AI confirmation token/gate and `ai_confirmation_required` refusal are
removed without enabling the deterministic MCP-incapable CLI runner. Omitted
and legacy optional token arguments now fail through ordinary compatibility
before store/staging/scheduler/runner/MCP/provider/cron effects. The obsolete
`explicit-ai-consent` claim is rejected, while the authenticated Task 2.4
metadata, Task 3.1 capability-derived Desktop eligibility, and byte-pinned
schedule confirmation remain unchanged. RED was 56 passed/4 failed plus the
separate claim 0 passed/1 failed; focused GREEN is 61 passed and consolidated
coverage is 229 passed. The full base gate is 918 Python passed/1 skipped,
installed distribution 1 passed, Desktop 93 passed, and TypeScript compilation
green; the explicit installed-distribution rerun is 1 passed. The protected
untracked Revision 6 review request remains untouched.

Task 3.2 Important review fix: complete at `854033420`; final re-review clean.
The exact schedule token/refusal suite is now selected once in the
base gate and removed from `WORKFLOW_GATE_OPTOUTS`, with a meta-test pinning
both invariants. Native CI remains unchanged under the existing base-only
showcase-wrapper convention. RED was 0 passed/1 failed/21 deselected; focused
GREEN is 27 passed. The full base gate is 920 Python passed/1 skipped,
installed distribution 1 passed, Desktop 93 passed, and TypeScript compilation
green. The protected untracked Revision 6 review request and ignored Task 3.3
brief remain untouched.

Task 3.3: complete at `9f84fa019`; fresh read-only task review approved with
Critical 0 / Important 0 / Minor 0. Authenticated catalog and detail
projections now expose generic boolean `requires_ai` from
the exact verified showcase scenario and `false` for ordinary user workflows;
Desktop treats missing legacy fields as false and renders one localized
informational line only for true rows. Supported AI and non-AI rows remain
equally enabled, while support → compatibility → trust disable precedence is
unchanged. RED was backend 0 passed/2 failed/61 deselected on missing fields
and Desktop/i18n 2 failed/37 passed on absent copy. GREEN is 140 affected
Python tests, 86 workflow UI/i18n tests, installed distribution 1 passed, and
the full base gate at 922 Python passed/1 skipped plus installed distribution
1 passed, Desktop 94 passed across 11 files, and TypeScript compilation green.
Ruff, typecheck, scoped ESLint/Prettier, diff, and canonical customization
checks pass. The protected untracked Revision 6 review request remains
untouched. Atomic commit is `9f84fa0191cc437325c6b4a16486fddadb99d4d0`; report is
`.superpowers/sdd/task-3.3-report.md`.

Task 3.4: implementation complete at `e1ee62be3`; review remains with the
orchestrator. A production-mounted offline E2E now proves authenticated
`ai-extensions` catalog/detail projection, public 202 admission without
request-time node execution, server-owned Desktop/background/real metadata,
same-key joining, and real coordinator success with capability injection only
at the coordinator seam. The recording runner checks exact sealed request-MCP
definitions without claiming MCP startup. Incapable admission is a typed 409
with zero logical residue; explicit-real non-AI and digest-mismatch integrity
paths are runner-free; and a capable admission followed by actual app-server
runtime execution fails through `PluginAgentRunner` with node-level
`package_mcp_unavailable`, zero provider calls, and no new terminal code. Exact
base/native release membership preserves Task 3.0's independent real-worker
MCP proof. RED was 0 passed/1 failed/22 deselected for absent release wiring;
GREEN is 29 focused and 251 broad tests, 8 explicit golden checks, and installed
distribution 1 passed. The full base gate is 929 Python passed/1 skipped,
installed distribution 1 passed, Desktop 94 passed, and TypeScript compilation
green. Ruff format/lint, diff, and canonical customization checks pass. The
protected untracked Revision 6 review request remains untouched; report is
`.superpowers/sdd/task-3.4-report.md`. Atomic commit is
`e1ee62be3807607f79e9385313917b5d8d271280`.

Task 3.4 Important review fixes: complete at `e303af488`. The
direct-only explicit-real non-AI assertion now drives a genuine authenticated
`laptop-diagnostic` run through mounted POST, sealed fixture staging, real
coordinator pause, and the real reject API into its owned approval-rework path.
The persisted `review-plan` attempt fails with `execution_integrity` and the
runner remains unused. The initially requested `approval-gate` scenario was
adjudicated out after strict RED proved its authenticated definition has no
rework branch and real rejection correctly cancels it. The incapable mounted
path now targets a live loopback provider trap, asserts zero requests, and
fully shuts the server down. Reviewer RED was 1 passed/1 failed with the real
approval-gate outcome `cancelled`; corrected GREEN is 2 passed, final focused
E2E/meta is 29 passed, and broad workflow coverage including Task 2.6 is 253
passed. Scoped Ruff format/lint, canonical customization, and diff checks pass.
Atomic review-fix commit is `e303af4883c24685b6535eee5ead442fa1497c49`.

Task 3.4 final re-review: approved with Critical 0 / Important 0 / Minor 1.
Both behavior-proof gaps are closed. Carried Low for final review: review-fix
commit `e303af488` changed the manifest-owned AI middleware E2E test without
also modifying its already semantically accurate customization entry in that
same commit, contrary to the per-commit ledger procedure. Do not rewrite the
reviewed atomic history solely to manufacture a no-op manifest change.

Workstream 3 exit: complete at `e303af488`. Final-state base gate passed with
929 Python passed/1 skipped, installed distribution 1 passed, Desktop 94
passed across 11 files, TypeScript compilation green, and
`TESTED_BASE_SHA=e303af4883c24685b6535eee5ead442fa1497c49`. The policy
table is 4-of-5 as required before Workstream 4.

Task 4.1: implementation and self-review complete at `ecb540f4b`.
Schema 14 adds projection-derived nullable `runs.scheduled_at` and the partial
published/queued schedule index. One canonical helper now owns derivation and
parity across fresh/reserved publication, v13 and namespace migration, every
integrity/admission rebuild, damaged-index reconstruction, and journal-only
recovery. SQL-only corruption records repair and rebuilds from corroborated
evidence; projection-only corruption follows journal repair; malformed
metadata fails closed. Focused recovery coverage, genuine v13 idempotence and
newer-runtime refusal, hostile namespace source coverage, query-plan use, and
unscheduled digest/byte stability are implemented. Final base gate: 952 Python
passed/1 skipped, installed distribution 1 passed, Desktop 94 passed, and
TypeScript compilation green. Report: `.superpowers/sdd/task-4.1-report.md`.

Task 4.1 Important review fixes: complete at `eb347b7fa`. Genuine v13
reserved rows now compose with schedule
backfill and publication recovery, while incomplete reserved evidence remains
owned by ordinary reservation cleanup. Namespace migration pins corroborated
evidence under stable run locks before taking SQLite write authority, then
revalidates exact rows, generation, and evidence digests without lock
reacquisition and retries bounded drift. RED exposed the exact reserved parity
failure, incomplete-reservation corruption detour, and DB-before-run inversion;
GREEN is 30 composed schedule/schema tests and 114 seven-file recovery tests.
The customization ledger is updated in the same fix commit; the protected
untracked Revision 6 review request remains untouched.

Task 4.1 second Important review fix: complete at `9cd480e78`. Incomplete
scheduled reservations whose final run
directory is absent are now pinned and carried through genuine v13 namespace
rebuild with derived namespace, preserved start digest, and forced null schedule
for ordinary healthy release/quarantine reconciliation. Published and
directory-present reserved evidence remain strictly corroborated;
source/generation/directory/evidence revalidation and run-before-SQL lock order
are unchanged. Cleanup RED was 0 passed/1 failed with
`repair_required:index_corrupt`; durable-corrupt RED was 0 passed/1 failed
because malformed final evidence recovered healthy. Combined GREEN for
incomplete, valid durable-final, and corrupt durable-final behavior is 3 passed.
The complete schedule/schema suite is 33 passed and the seven-file recovery
selection is 117 passed. Manifest ownership is updated; the protected Revision
6 review request remains untouched.

Task 4.1 second review post-commit test correction: complete at `1e36e498f`.
Post-commit verification exposed the lock-order test signaling its simulated
writer before the independent SQLite probe completed, allowing a false
DB-before-run result. Synchronization now probes first and signals second; the
corrected regression passed 10 consecutive isolated runs. Production is
unchanged and the manifest-owned deterministic probe contract is updated.

Task 4.1 final re-review: approved with Critical 0 / Important 0 / Minor 0.
The incomplete legacy-namespace reservation reaches ordinary healthy cleanup,
while published and directory-present reserved evidence remain strictly
corroborated and corrupt durable evidence fails closed. Run-before-SQL locking,
source/generation/directory/evidence revalidation, and the deterministic probe
are intact. Final-state Workstream gate passed with 958 Python passed/1
skipped, installed distribution 1 passed, Desktop 94 passed across 11 files,
TypeScript compilation green, and
`TESTED_BASE_SHA=1e36e498f77d99a4d0178c9b71cbb9acfe46d3a9`.

Task 4.2: implementation and self-review complete at `4a74f5106`.
Desktop admission accepts optional RFC 3339 `schedule_at`, canonicalizes
future instants to UTC-Z, rejects malformed/non-string/naive/unknown-offset/
non-future input with stable typed 422s before store or catalog access, and
requires the field for authenticated schedule-mode showcases. Scheduled runs
publish queued/background with server-owned catalog/risk/schedule metadata;
equivalent offset representations preserve idempotent identity and journal
recovery. Public list/detail output exposes only top-level canonical
`schedule_at` plus queued `scheduled_wait` presentation, with internal metadata
redacted. Ordinary unscheduled identity remains unchanged. Final base gate:
959 Python passed/1 skipped, installed distribution 1 passed, Desktop 94 passed
across 11 files, and TypeScript compilation green. Report:
`.superpowers/sdd/task-4.2-report.md`.

Task 4.2 metadata-boundary review fix: complete at `24131b63d`. The shared
schedule normalizer now enforces the durable 512-character metadata value cap
after canonicalization. The 491-fraction-digit maximum remains exact and
accepted; the adjacent 492-digit/513-character canonical value receives the
typed pre-store 422 with no package lookup, home creation, or staging residue.
Boundary GREEN is 2 passed and the combined post-commit review matrix is 11
passed. Focused Desktop/store coverage is 137 passed, Task 4.1 recovery is 105
passed, and the full base gate is 960 Python passed/1 skipped, extracted-wheel
integration 1 passed, Desktop 94 passed across 11 files, and TypeScript green.
Report: `.superpowers/sdd/task-4.2-report.md`.

Task 4.2 Important review fixes: complete at `1d16f5ec9`. A shared exact
RFC 3339 schedule helper now preserves distinct submicrosecond identity across
API normalization, start digests, SQL indexing, public projection, and journal
recovery while retaining established six-digit padding and joining equivalent
offset/trailing-zero forms. Lower/upper UTC conversion overflow and hostile
observed-clock conversion return the typed pre-store 422. Mutation success,
stale-state `current`, and invalid-transition `current` all use the public run
projection, preserving schedule/wait presentation without internal metadata.
Review GREEN is 9 exact post-commit contracts, 135 focused Desktop/store tests,
105 Task 4.1 recovery tests, and 394 broad affected tests. Final base gate:
960 Python passed/1 skipped, extracted-wheel integration 1 passed, Desktop 94
passed across 11 files, and TypeScript compilation green. Report:
`.superpowers/sdd/task-4.2-report.md`.

Task 4.2 final re-review: approved at `24131b63d` with Critical 0 / Important
0 / Minor 0. The shared canonical identity admits exactly the existing
512-character durable metadata maximum and typed-rejects the first oversized
canonical value before store lease, package lookup, home creation, or staging.
The bound is applied after canonicalization, so overlong source spellings whose
trailing-zero-equivalent identity fits remain accepted. Exact fractional
identity, typed overflow handling, mutation projection redaction, Task 4.1
parity/recovery, ordinary unscheduled identity, and `extra=forbid` remain
intact. Focused boundary re-review is 2 passed; final base gate remains 960
Python passed/1 skipped, extracted-wheel 1 passed, Desktop 94 passed across 11
files, and TypeScript compilation green.

Task 4.3: implementation and self-review complete at `72c3b70fe`. Scheduled
candidate paging now excludes exact future queued rows before page capacity,
preserves stable cursors across filtered submicrosecond rows, and corroborates
projection/index parity. A real early admission wake completes once as
`scheduled_not_due` without submit, stall, promotion, claim, or retained wake;
the bounded indexed sweep rediscovers it at due. Coordinator wall/monotonic
clocks reach both scheduler paths, promotion repeats the required aware-clock
due check under its existing locks/transaction, and every sweep wait/backoff is
capped at five monotonic seconds. Final focused is 7 passed, affected matrix is
287 passed, promotion compatibility is 63 passed, and post-commit focused plus
gate inventory is 31 passed. Fresh full base gate: 968 Python passed/1 skipped,
extracted-wheel integration 1 passed, Desktop 94 passed across 11 files, and
TypeScript compilation green. Report: `.superpowers/sdd/task-4.3-report.md`.

Task 4.3 Important review fixes: implementation and pre-commit verification
complete. Ordinary and scheduled discovery now have independent cursors, with
a bounded exact newly-due interval rescue so a forward jump submits an older
scheduled row on the next real sweep even while both ordinary and scheduled
page cursors remain active. Exact six-digit SQL bounds exclude arbitrary-
precision same-second future rows before bounded projection corroboration;
the canonical fraction-prefix case remains future until its exact instant.
Scheduled base/rescue and ordinary pages merge in original admission order.
Review-fix REDs were 1 failed for the ordinary-cursor skip, 1 failed with 102
projection loads for the unbounded exact filter, and 1 failed on the real
active-cursor forward jump. GREEN is Task 4.3 10 passed, the 12-file affected
matrix 179 passed, merge inventory 2 passed, and a fresh full base gate of 971
Python passed/1 skipped, installed distribution 1 passed, Desktop 94 passed,
and TypeScript compilation green. Atomic review-fix commit: `6884ce754`;
report: `.superpowers/sdd/task-4.3-report.md`.

Task 4.3 second Important review fixes: implementation and pre-commit
verification complete. The scheduled/rescue cursor state is removed: each
sweep independently samples the current oldest due scheduled prefix, merges it
with the ordinary cursor prefix and actionable wakes, and processes one global
created-at/run-id prefix. This catches consecutive forward jumps and preserves
the original admission fairness barrier. Corroborated not-due wakes complete
administratively outside that execution budget. Exact due SQL now uses
scheduled-index range/equality UNION branches, so adding 300 future rows no
longer adds unbounded VM work. REDs covered the t2 jump, 161-to-2,561 opcode
growth, 51st scheduled fairness, wake-inclusive fairness, and a future wake
behind a full 100-row execution page. GREEN is 47 focused tests, 284 affected
tests, 2 merge-inventory tests, and the full base gate at 976 Python passed/1
skipped, installed distribution 1 passed, Desktop 94 passed, and TypeScript
green. Atomic commit: `727ab65ae`; report:
`.superpowers/sdd/task-4.3-report.md`.

Task 4.3 final re-review: approved at `727ab65ae` with Critical 0 / Important
0 / Minor 0. The reviewer withdrew the proposed O(page-size) SQLite-work
requirement after checking it against the approved Revision 7 contract: the
contract bounds sweep waits/backoff/local-wake timeouts, while production
queued/nonterminal admission is hard-capped. Independent worst-cap probes were
about 8 ms for the ordinary future-row scan and 57 ms for the due-range sort,
well below the five-second cadence. Guaranteeing O(page size) for both dynamic
due filtering and global admission order would require new materialized
activation state outside the approved contract. All earlier Task 4.3 Important
findings are closed; final post-commit focused plus merge-inventory verification
is 39 passed, customization/diff checks pass, and the protected Revision 6
request remains untouched.

Task 4.4: implementation and self-review complete at `c1e1fa185`. Future
scheduled rows keep their original fairness sequence but are isolated from
immediate queue position, lane/predecessor ownership, coordinator submission,
foreground ownership, running counts, and worker claims until due. They still
count against queued/nonterminal quotas and use the existing retained-evidence
cancellation path. At due, allow/queue/forbid preserve their declared policies;
active-lane forbid records terminal `schedule_overlap_forbidden` with zero
claims. RED was 3 passed/4 failed; focused GREEN is 129 passed, ordinary
promotion compatibility is 37 passed, and the 19-file breadth suite is 394
passed. Full base gate: 984 Python passed/1 skipped, extracted-wheel 1 passed,
Desktop 94 passed across 11 files, and TypeScript green. Report:
`.superpowers/sdd/task-4.4-report.md`. Exact post-commit customization and show
checks passed; the focused post-commit Task 4.4 rerun is 9 passed.

Task 4.4 review fixes: implementation and pre-commit verification complete.
Backward wall movement now journals and atomically removes stale immediate
queue position/blocker state while preserving scheduled admission sequence.
Run-list first pages sample the injected server clock once; cursor pages reuse
the signed observation, and store status plus public presentation share that
same wall instant. Authenticated cancel coverage now completes real pending
wakes as `not_actionable`, proves zero scheduler submission, and confirms the
retained run enters ordinary cleanup preview. Review RED was 0 passed/2 failed;
GREEN is 3 focused, 131 scheduled/API, 37 ordinary promotion, and 396 broad
tests. Full base gate: 985 Python passed/1 skipped, extracted-wheel 1 passed,
Desktop 94 passed, and TypeScript green. Atomic review-fix commit: `e8fc8315a`;
exact post-commit customization/show checks and 3 focused contracts passed;
report: `.superpowers/sdd/task-4.4-report.md`.

Task 4.4 second review fix: implementation and pre-commit verification
complete. GET detail and mutation success/stale/invalid current projections now
sample one server-owned response instant and pass it to both status derivation
and public presentation; unrelated authorized loads retain their ordinary call
shape. Strict RED was 0 passed/4 failed. GREEN is 4 focused, 135 scheduled/API,
37 ordinary promotion, and 400 broad tests. Full base gate: 985 Python passed/1
skipped, extracted-wheel 1 passed, Desktop 94 passed, and TypeScript green.
Atomic second review-fix commit: `38d783fd2`; report:
`.superpowers/sdd/task-4.4-report.md`. Exact post-commit customization/show
checks and all 4 response-clock contracts passed.

Task 4.4 final re-review: approved at `38d783fd2` with Critical 0 / Important
0 / Minor 0. The complete three-commit task range now uses one request-scoped
injected clock for list/cursor, detail, mutation success, stale/invalid current,
and runtime-stale reload projections; backward clock movement atomically
dematerializes immediate queue fields while retaining fairness sequence/index
parity; authenticated cancellation completes real wakes without submission and
retains evidence under ordinary cleanup. Ordinary callers, redaction,
lock/fencing behavior, and manifest ownership remain intact. Independent
focused scheduled/API verification passed 135 tests; customization, show, and
range-diff checks passed. All Task 4.4 findings are closed.

Task 4.5: implementation and self-review complete from `38d783fd2` at atomic
commit `50ffd2131`. Scheduled admission persists exact catalog/package/sealed
definition-policy-input/risk/execution identity plus authenticated showcase
scenario/bundle identity. The coordinator performs read-only current-source,
trust, forced-showcase, compatibility-then-risk, and actual runner/runtime
revalidation immediately before correlated promotion. Failure atomically
records `schedule_revalidation_failed` with zero claims and retained evidence;
pre-promotion restart revalidates, post-promotion restart continues once. New
scheduled evidence cannot use tokenless store promotion or a scheduler without
server capability context; legacy schedule-only projections remain compatible,
and unscheduled start identity is unchanged. Strict REDs covered missing
identity (0/2), revoked trust (0/1), and tokenless promotion (0/1). Final Task
4.5 GREEN is 26 passed; reconciled affected breadth is 571 passed; full base
gate is 1012 Python passed/1 skipped, installed distribution 1 passed, Desktop
94 passed across 11 files, and TypeScript green. Report:
`.superpowers/sdd/task-4.5-report.md`. Exact post-commit customization/show
checks and the full 26-test Task 4.5 suite passed.

Task 4.5 replacement review fixes complete on top of `50ffd2131` at atomic
commit `bd627206f`. The initial review's Critical reusable/pre-load authorization
gap is closed by a store-instance-owned, run-bound, one-use verifier consumed
against the current locked projection inside the atomic promotion transaction.
The Important incomplete-seal gap is closed by one scheduled-only full-tree
identity covering commands, scripts, MCP configuration/server files, input
manifest/files, and node/agent skill material. The Important CWD fallback gap
is closed by persisting and resolving the exact server-derived canonical
catalog root plus contained relative workflow path. Projected lookalikes,
cross-run use, replay, post-package-load trust changes, changed process CWD,
deleted/replaced original sources, and all eight sealed-resource mutations fail
before claim. Transactional terminal failure defers the notification write to
durable wake/journal reconciliation, avoiding a second-connection SQLite
self-lock while preserving atomic failure evidence. Strict REDs were 0/4,
0/8, and 0/3. Final GREEN is Task 4.5 44/44, affected breadth 553/553 across 22
files, and a fresh full base gate of 1030 Python passed/1 skipped,
installed-distribution 1 passed, Desktop 94/94 across 11 files, and TypeScript
green. Ruff lint/format, diff check, and customization verification passed.
Exact post-commit customization/show checks and Task 4.5 44/44 also passed.

Task 4.5 second replacement review fix complete on top of `bd627206f` at atomic
commit `e055dece2`. One remaining Important package-load boundary was reproduced:
after eager verification and opaque handle creation, malformed sealed YAML made
single advance propagate `WorkflowValidationError` and made advance-all abort
valid peers. Strict RED was 0/2. Newly scheduled package preparation now
terminalizes through a handle-consuming, admission/run-locked transaction;
advance-all isolates the failed run. Ordinary and legacy scheduled package
exceptions still propagate without a server authorization. Focused GREEN is
5/5, including invalid definition/policy, batch continuation, valid-change
promotion-bound verification, and legacy/ordinary propagation. Task 4.5 is
49/49; affected breadth is 577/577 across 23 files; full base is 1035 Python
passed/1 skipped, installed-distribution 1 passed, Desktop 94/94, and
TypeScript green. Ruff, format, diff, and customization gates passed.
Exact post-commit customization/show checks and Task 4.5 49/49 passed.

Task 4.5 final re-review: approved at `e055dece2` with Critical 0 / Important
0 / Minor 0. The complete three-commit task range preserves exact store/run
single-consumption at promotion and package-preparation failure, full immutable
execution-readable snapshot coverage, exact pinned source resolution, typed
zero-claim terminal failure with batch isolation, crash correlation, deferred
transactional notification, and unchanged ordinary/legacy behavior.
Independent verification passed Task 4.5 49/49, scheduled runs 24/24, parallel
scheduler 19/19, Desktop API 111/111, and merge inventory 25/25. All Task 4.5
review findings are closed; the protected Revision 6 request remains untouched.

Task 4.6: implementation and self-review complete from `e055dece2` at atomic
commit `d022b20c2`.
Scheduling eligibility is now server-derived as `schedule_required` without an
instant and supported with canonical future `schedule_at`; the same admission
path permits scheduled AI only under the server-owned capable runner/runtime
binding. Desktop Review & Run exposes immediate and one-shot Run later actions,
keeps immediate request bodies unchanged, validates and canonicalizes the local
picker, maps typed server scheduling races, and presents server-derived
`scheduled_wait` as localized Scheduled with local/canonical instants.
Cancel-before-fire uses the existing mutation and retained terminal state.
All four locale shapes compile and make no Cron-creation claim; the separate
legacy CLI scheduling confirmation remains intact. Focused Desktop is 86/86,
the 7-file Python/CLI selection is 267/267, pinned Desktop inventory is 140/140,
and the fresh base gate is 1,036 Python passed/1 skipped, installed distribution
1/1, Desktop 105/105, and TypeScript green. Scoped ESLint, customization, and
diff checks passed. Report: `.superpowers/sdd/task-4.6-report.md`. Task review
remains with the orchestrator.

Task 4.6 review fixes: implementation and self-review complete at separate
atomic commit `27e04f792`. A
schedule-bearing scenario now retains the generic no-network showcase policy;
new scheduled admissions use localized Scheduled success copy while existing
dispositions keep Already running; inspector local time follows the active
Desktop locale; scheduled card visible and accessible states share the same
localized value; and nullable `presentation_state`/`schedule_at` fields are
explicit on `WorkflowRunSnapshot`. Strict RED was 0/1 policy, 65 passed/3
failed Desktop behavior, and two TypeScript field-contract errors. GREEN is
focused policy 1/1, affected Desktop 65/65, Python/CLI breadth 268/268, pinned
Desktop inventory 144/144, and the fresh base gate at 1,037 Python passed/1
skipped, installed distribution 1/1, Desktop 109/109, and TypeScript green.
Scoped ESLint, customization, and diff checks passed. Re-review remains with
the orchestrator.

Task 4.6 second review fix: implementation and self-review complete at separate
atomic commit `81e54ccef`. Generic
showcase network eligibility now runs before scheduling reason selection, so a
synthetic non-ID networked scheduling scenario projects
`showcase_cli_required` both without and with a future `schedule_at`. Strict
RED was 1 passed/1 failed; focused GREEN is 2/2 and affected
catalog/detail/admission/runner breadth is 231/231. Customization and diff
checks passed. No Desktop, locale, TypeScript, execution, scheduler, or stored
state changed. Re-review remains with the orchestrator.

Task 4.6 final re-review: approved at `81e54ccef` with Critical 0 / Important
0 / Minor 0. Catalog/detail projection and admission now apply generic
no-network eligibility consistently before scheduling reason selection; all
prior Desktop copy, locale, accessibility, and projection-type findings are
closed. Spec verdict conformant and task quality approved. Task 4.6 is
complete.

Task 4.7: implementation and review complete at `8e8a54d09`. Real
authenticated one-shot scheduling E2E and exact release-gate selection pass;
Workstream 4 breadth is 496/496 and the committed-HEAD base gate is 1,045
Python passed/1 skipped, installed distribution 1/1, Desktop 109/109, and
TypeScript green. Final review approved Critical 0 / Important 0 / Minor 1.
Carry the non-blocking test-proof Low into Task 5: explicitly assert singleton
completed `run_admitted` wake evidence, successful schedule-revalidation
projection/promotion evidence, and one terminal `run_succeeded` after
reconstruction/repeated sweep. Workstream 4 is complete.

Task 4.7: implementation and self-review complete from `81e54ccef` at atomic
commit `8e8a54d09`. A new real session-token-authenticated middleware E2E
selects scheduling and AI showcase catalog rows, submits canonical Run later
requests, completes the genuine early wake as `scheduled_not_due`, executes
the exact-due scheduling scenario once through the real coordinator/scheduler
to its checkpoint, retains cancellation evidence with zero after-due work,
and reconstructs the exact schedule plus exactly-once outcome after complete
index loss and coordinator restart. The local AI matrix succeeds under a
declared-capable runner/runtime and makes incapable-runner and
`codex_app_server` substitutions terminal `schedule_revalidation_failed`
before promotion, claim, runner, or localhost provider traffic. Strict gate
RED was 0/1; corrected focused GREEN is 32/32. Workstream 4 affected breadth
is 496/496. The final base gate is 1,045 Python passed/1 skipped, installed
distribution 1/1, Desktop merge selection 109/109, TypeScript green, and
`TESTED_BASE_SHA=8e8a54d09eab0efb8a90f1a6449bd0552bcd21aa`; full Desktop
Workflow UI is 144/144 and renderer+Electron typecheck is green. Ruff lint,
new-file format, customization, and diff checks pass. The Workstream 4 policy
table is 5-of-5. Exact post-commit customization/show checks and focused
E2E/inventory 32/32 passed. Report: `.superpowers/sdd/task-4.7-report.md`.

Task 5: automated release verification and handoff complete at final tracked
HEAD `e6155f2060e5049a4dd8213da5bf726cfe1c48e5`. Atomic cleanup and
documentation commits are `2a020bbfb`, `bb16d4ebb`, `a6be8121e`,
`65b407ae1`, and `e6155f206`. Task 1.2's exact class-only catalog log proof,
Task 2.1's stale laptop-interpretation promise, and Task 4.7's singleton
wake/revalidation/promotion/terminal-success proof are closed. Task 3.4's
historical per-commit manifest Low is preserved without history rewriting;
the final manifest is current and verified. The 22-file contract matrix is
524/524. Final exact-SHA base gate: 1,045 Python passed/1 skipped,
installed-distribution 1/1, Desktop merge selection 109/109 across 11 files,
and TypeScript green. Full Desktop Workflow UI is 144/144 across 16 files;
renderer+Electron typecheck, scoped ESLint, Ruff, customization, and diff
checks pass. OTTO and LOOP24 detached no-ref rehearsals each passed `--write`,
8/8 `--check`, Hermes-managed `chat_completions` provider proof, gateway
parity `SELFTEST OK`, and the brand gate with
`TESTED_BRAND_SHA=e6155f2060e5049a4dd8213da5bf726cfe1c48e5`; temp
worktrees were removed and no refs changed. Seven non-normative v3.0.3 backlog
documents and the tracked final verification mapping are committed. Manual
configured-Gateway structured tool-call proof and Electron UAT remain honestly
outstanding because no already-authorized environment was available. Report:
`.superpowers/sdd/task-5-report.md`.

Task 5 final review: approved at
`e6155f2060e5049a4dd8213da5bf726cfe1c48e5` with Critical 0 / Important 0 /
Minor 0. Independent proof reruns passed catalog logging 65/65, scheduling E2E
6/6, entitlement/goldens 22/22, and release inventory 26/26. The complete
Revision 7 automated contract is conformant. Release remains blocked only on
the explicitly documented configured-Gateway structured `tool_calls` proof for
both brands and specified Electron manual UAT scenarios.

Revision 7 adversarial remediation Task 1: complete (commit `26385c6c5`;
strict RED/GREEN evidence recorded, fresh task review spec-compliant and quality
approved with Critical 0 / Important 0 / Minor 0).

Revision 7 adversarial remediation Task 2: complete (commit `521b214c9`;
three stale-binding RED cases and 83-test GREEN recorded, fresh task review
spec-compliant and quality approved with Critical 0 / Important 0 / Minor 0).

Revision 7 adversarial remediation Task 3: complete (commit `9e3c5baab`;
real 100-row starvation RED and 61-test GREEN recorded, fresh concurrency review
spec-compliant and quality approved with Critical 0 / Important 0 / Minor 0).

Revision 7 adversarial remediation Task 4: complete (commit `9acd5e511`;
valid oversized trust-store RED and 127-test GREEN recorded, fresh task review
spec-compliant and quality approved with Critical 0 / Important 0 / Minor 0).

Revision 7 adversarial remediation Task 5: complete (commit `35fdf8f3f`;
v13 whole-index replacement RED and 79-test GREEN recorded, fresh migration
review spec-compliant and quality approved with Critical 0 / Important 0 /
Minor 0).

Revision 7 adversarial remediation Task 6: complete (commit `3ca922569`;
one-extra-verification RED and 260-test GREEN recorded, fresh cache-boundary
review spec-compliant and quality approved with Critical 0 / Important 0 /
Minor 0).

Revision 7 adversarial remediation Task 7: integrated automated gates complete
at production SHA `3ca92256955cd3fe73675ca5515f3b612cc59dc8`.
The focused resilience gate is 244/244; exact Strategy A is 532/532 across 22
files; installed distribution is 1/1; Desktop Workflow UI is 144/144 across 16
files; TypeScript, merge inventory 26/26, Ruff, customization, diff, and
brand/provider quiet checks are green. The six accepted adversarial findings
and every deferred/disputed/nit/pre-existing disposition are recorded in the
remediation report and backlog. Seven configured-Gateway/Electron manual-only
release gates remain outstanding. Fresh Task 7 and final branch reviews remain
with the orchestrator.

Revision 7 adversarial remediation Task 7: fresh task review complete at
documentation commit `61aadd443`; spec compliant and quality approved with
Critical 0 / Important 0 / Minor 0. The reviewer mechanically confirmed the
532-test matrix delta, 244-test integrated selection, 26-test merge inventory,
complete deferred-item ledger, exact seven manual-only gates, and valid
customization ownership.

Revision 7 adversarial remediation final range review: APPROVE for
`e6155f206..61aadd443`, with Critical 0 / Important 0 / Minor 0. The fresh
reviewer independently reran all ten new regression tests (10/10), confirmed
all six accepted findings resolved, and found the remediation diff clean with
no brand/model-provider or preserved-contract regression.

Task 8 first fix `e039bef3d`: deterministic RED evidence captured for bounded
scheduled page progress, leadership-term page-one restart, and the post-restore
verified-cache generation race. The finding-author re-review approved those
two runtime fixes, but the independent fresh task review found one additional
Important sustained-admission continuation gap and one Minor report/test-
evidence gap.

Task 8 review correction committed at `77b73f37f`: sustained future-first RED
reproduced on `e039bef3d` (`1 failed, 37 deselected`). Finite fixed-time
scheduled generations now run alongside an every-sweep live due-prefix sample,
with scheduled continuation and periodic liveness reserved inside the shared
100-run cap. Cache rollback assertions cover exact pre-populated request-owned
containers and counters. Controller GREEN was targeted 5/5, affected
three-file matrix 123/123, eight-file resilience matrix 250/250; Ruff,
customization, diff, and protected brand/provider checks green.

Task 8 correction round 2: reviewer found the created-at/run-id generation tail
admitted post-capture rows under equal and backward `_utc_now()` values. Strict
RED was 2 failed/27 deselected. Generation membership now uses the existing
monotonic scheduled queue-sequence metadata fence while preserving fixed due
time, fresh-prefix sampling, cursor order, cap reservations, and reset
behavior. GREEN is targeted 5/5, affected three-file matrix 125/125, and
eight-file resilience matrix 252/252. Ruff, customization, diff, and protected
brand/provider checks are green; the atomic correction commit remains before
completion.

Task 8 complete at `51cd5c302`: the clock-independent scheduled admission
generation fence is committed and fresh task re-review reports spec Pass,
quality Approved, Critical 0 / Important 0 / Minor 0. Reviewer verification
confirmed 6 focused tests, legacy/null-sequence repair, fail-closed metadata
validation, processed-prefix/cap/reservation/reset invariants, Ruff, and range
diff hygiene.

Task 9 documentation implementation complete at `a23a5fa4f`: the final
verification handoff now preserves `a6be8121e` as historical plan-completion
evidence and separately records the final `51cd5c302` remediated gates,
diagnostic timing-flake sequence, APPROVE verdict, and exact seven OUTSTANDING
manual-only release gates. The customization checker and marker/diff hygiene
checks passed; only the two owned tracked documentation files were committed.

Task 9 and final whole-branch review closure: fresh task review Pass/Approved
with Critical 0 / Important 0 / Minor 0. The original final reviewer re-reviewed
the exact documentation fix, marked its sole Minor CLOSED, and returned
APPROVE with Critical 0 / Important 0 / Minor 0. Final HEAD checker, full-range
diff hygiene, commit check, protected brand/provider diff, and tracked
worktree status are clean. Release remains NOT READY solely because the seven
manual configured-Gateway/Electron gates are OUTSTANDING.

Task 10 implementation and self-review complete, pending orchestrator review.
Strict RED reproduced both repair-marked page-slot consumption and the
v13→v14 unrecoverable journal-body sweep outage (`2 failed, 29 deselected`).
Both ordinary and exact-due scheduled coordinator candidate queries now
exclude active latest-transition run-scoped repairs inside their bounded SQL,
while historical verified repairs become eligible again. The wake-drained
migration regression proves only the healthy scheduled run is submitted and
the damaged published row remains queued, `scheduled_at=NULL`,
`run_evidence_uncorroborated`, byte-preserved, and unquarantined. Focused GREEN
is 2/2; affected schedule-store/coordinator coverage is 102/102. Ruff lint,
customization, and diff checks are green. Report:
`.superpowers/sdd/task-10-report.md`. Atomic commit subject:
`fix(workflow): isolate repair-marked coordinator rows`.
