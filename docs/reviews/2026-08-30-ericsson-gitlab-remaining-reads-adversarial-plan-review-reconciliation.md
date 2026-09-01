# Reconciliation — remaining Ericsson GitLab read plans

Date: 2026-08-30

## Review inputs

- Claude Fable 5, `xhigh`:
  `2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-fable-5.md`
- Codex GPT-5.6 Sol, `xhigh`:
  `2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-codex-5-6-xhigh.md`
- Shared source-grounded prompt:
  `2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-prompt.md`

Both reviewers ran independently against the same six frozen spec/plan
hashes. Both returned `BLOCK`. The persisted Fable report contains six
Important and seven Minor findings; Codex contains ten Important findings.

The first Fable CLI pass exceeded capture limits. It surfaced a source/vendor
divergence that was not repeated in the compact persisted rerun. The
orchestrator therefore treated it as an untrusted lead, reproduced it directly,
and records it below as `V-01`, not as a finding attributed to the persisted
Fable report. The two independent report files were not edited.

## Source verification

The reconciliation checked the plans against:

- current `ericsson-capabilities/main` at
  `0d7654d14db0afe0c688a752a2676d8cabe2f981`;
- Hermes `base` at the planning commit;
- GitLab's official Jobs, Tags, Releases, To-Do, and Merge Requests API docs;
- GitLab source for current To-Do model/finder behavior where the public docs
  and current implementation differ;
- Ericsson connector operations, schemas, descriptors, parser, generation
  tests, routing skills, and migration authorities;
- Hermes approval ordering, vendor reconciliation, routing harness helpers,
  distribution tests, and mandatory test wrapper.

## Dispositions

| ID | Disposition | Verified reason and plan correction |
| --- | --- | --- |
| V-01 | Accepted, blocking | Hermes' managed inventory includes Jira Defect Loop and later workflow/skill/onboarding content absent from source at the manifest's `vendoredFrom` SHA. The vendor reconciler removes stale managed paths. CI Task 0 now reconciles authority into source, vendors once, and adds inventory/byte parity before GitLab work. |
| F-01 / I-10 | Accepted | The real fixed inventories live in descriptor, migration, docs, and onboarding tests, not only `test_connector_cli_gitlab_port.py`. All three plans now enumerate and update those authorities; CI replaces fixed totals with relational contracts and corrects hand-authored count prose. |
| F-02 | Accepted | `uniqueItems` is unsupported by the connector schema mirror and runtime dispatch. It was removed; duplicate statuses are rejected by the operation before transport. |
| F-03 / I-03 | Accepted | `argparse` otherwise stores absent optional positionals as `None`. The release plan keeps `default=argparse.SUPPRESS` with `nargs="?"` and tests empty, string, and numeric forms. |
| F-04 / I-01 | Accepted | `_normalize_user` requires and returns `state`; GitLab also permits a job user to be null. Fixtures and projections now cover the four-field user or `None`, and release/To-Do author fixtures include state. |
| F-05 | Accepted with input/output distinction | API-documented enums constrain caller filters. Returned action/type values are bounded remote data so newer GitLab values do not invalidate the page. Project/group identity is optional and Commit target identity is a SHA. |
| F-06 / I-04 | Accepted | GitLab writes can be denied by the pre-tool approval hook before `registry.dispatch`. The harness now scores transcript-level attempted names and patches approval to record-and-deny, while dispatch remains a no-network backstop. |
| F-07 / I-02 | Accepted | Shared pipeline extraction is no longer described as a behavior-neutral move. The plan characterizes existing valid shape, preserves the `web_url` key, and explicitly tests deliberate malformed-data tightening. |
| F-08 / I-06 | Accepted | Corpus `read_tools` is the union of qualified skill-owned reads. Later slices cumulatively extend it and `intent_tools`, including existing commit and MR reads required by later cases. |
| F-09 | Accepted | `_GITLAB_PROJECT_OPERATIONS` owns validation/description, not requiredness. Optional MR and To-Do project fields remain in that table; parser requiredness is handled separately. |
| F-10 | Accepted | A new manifest-driven Hermes parity test compares managed inventories, source SHA, package digests, and bytes rather than trusting `vendoredFrom` alone. |
| F-11 / I-09 | Accepted | Every Hermes Python test command now uses `scripts/run_tests.sh`. Direct `pytest` remains only in the separate Ericsson source repository. |
| F-12 / I-07 | Accepted | The runner accepts the real router identity forms, requires explicit distinct Claude/OpenAI namespaces and exact resolved models, and tests clarification semantics rather than substring family inference. |
| F-13 | Accepted | Repository helpers/constants are defined once and reused by later tasks. |
| I-05 | Accepted | Completed routes must equal an allowed ordered sequence and fulfill every required intent through `intent_tools`. Only a genuine question may pass as an allowed safe prefix. |
| I-08 | Accepted | Each slice integrates to source `main` and Hermes `base`, records exact SHAs, and the next slice proves ancestry before creating worktrees. |

## Additional verified corrections

- GitLab Tags responses do not document a tag `web_url`; the repository plan
  derives it from validated project path plus encoded tag name.
- GitLab release asset links may intentionally point off-instance. The plugin
  still enforces the approved safety policy: primary external URLs omit the
  entry, external direct URLs omit only that field, all omissions are counted,
  and no returned URL is fetched.
- The Merge Requests API documents `assignee_username[]` as an array filter;
  the release plan sends the single CLI username as a one-element array.
- Release `author`, `commit`, and `description` are treated as optional when
  absent and strict when present.
- The routing runner's isolated home copies only the explicit model-provider
  credential key; it never copies the source `.env` or a real GitLab PAT.
- The release slice updates Hermes' exact qualified-skill inventory tests for
  `release-research` and `personal-inbox` rather than weakening them.

## Deliberately unchanged decisions

- One normal LLM/tool-calling conversation remains the router. There is no
  preliminary classifier request, tool-array swap, or system-prompt mutation.
- Code search stays project-scoped with the existing aggregate text ceiling.
- Merge-request listing remains one backward-compatible operation; no duplicate
  global MR tool is introduced.
- Webhooks and every new write remain excluded.

## Blocker-only rereview round 1

The first amended set was independently rereviewed by Claude Fable 5 xhigh
and a clean Codex 5.6 xhigh agent. Both returned `BLOCK`. The following
source-verified findings were accepted and corrected:

- preserve `argparse.SUPPRESS` through the parser's final positional cleanup;
- make the backward-compatibility MR test pass before the extension and keep
  post-extension scope assertions in post-extension tests;
- update the installed-distribution e2e qualified-skill inventory;
- give `gitlab_read_pipeline` an owning skill and create/validate
  `intent_tools` in the source corpus authoring task;
- remove the not-yet-created routing-runner test from the CI baseline;
- replace zero-selection branch/tag `-k` expressions with matching terms;
- delete the remaining fixed connector registration count;
- remove undocumented project-search `namespace`, `archived`, and `visibility`
  response requirements while deriving display namespace from validated path;
- supply the source directory and expected SHA to every fail-closed managed-byte
  parity invocation;
- canonicalize matching native personal scope plus `@me` to the native scope
  without an unsupported redundant actor filter.

The plans also incorporated verified non-blocking cleanup from the same pass:
documented `waiting_for_callback`, exact eight-field variable wording, vendor
overlay exclusions, no isolated-home `auth.json` copy, explicit new-skill
content requirements, and count-free README/configuration skill-list updates.

## Final rereview and targeted correction

The next independent final rereview still returned `BLOCK` from both models.
The three new findings were reproduced against source and accepted:

- `scripts/run_tests.sh` strips unlisted variables through `env -i`; CI Task 0
  now owns the wrapper and explicitly forwards both parity inputs;
- strict 40-hex pipeline SHA validation would break three existing
  `tests/test_gitlab_reads.py` fixtures; CI Task 3 now owns, updates, runs, and
  commits that file while retaining an isolated short-SHA rejection; and
- the official Tags API permits `message: null`; the repository contract now
  preserves `None` and tests null, string, and malformed non-string shapes.

A targeted rereview then split: Codex 5.6 xhigh returned `PASS`, while Claude
Fable 5 xhigh found one additional Important defect. Its `NEW-A` claim was
valid: a fail-closed parity test with external source inputs would be collected
by the normal `base` CI run and fail when those inputs were intentionally
absent. The plan now marks that module `pytest.mark.integration`, uses
`-m integration` on all eight explicit parity invocations, and preserves
fail-closed behavior whenever the gate is deliberately selected. The runner's
documented per-file exit-5 handling deselects the module safely inside the
normal full-suite CI run. A command selecting only that marked file without
`-m integration` still triggers the runner's intentional whole-run zero-test
guard, but no plan or default CI path uses that command shape.

The same amendment incorporated two non-blocking clarifications: the isolated
short-SHA test name matches its focused selector, and tag messages are redacted
before truncation to the bound.

## Convergence gate

Claude Fable 5 xhigh and Codex 5.6 xhigh independently rereviewed the final
frozen artifacts. Both returned `PASS`: `NEW-A` is resolved, all earlier
blockers remain resolved, and neither reviewer found a new Critical or
Important plan defect.

Final frozen SHA-256 values:

- CI design: `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922`
- repository design: `e80f75ae8af2a8e64b6f37017fd732da3504ea84805248394460a0c24eba6a30`
- release/inbox design: `3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f`
- CI plan: `c0fe22fb9ecdfa97bf3addadaaa69a3a3e4c97edc1325410c18b30b51c4af028`
- repository plan: `8269698319f777f6e6ee808c9cd91e2a10bfc57b036124108de9f54b989263e7`
- release/inbox plan: `f50b88b603a54807b4cf8a8f8fa2c7ed789908135ce726e0f6642b9b424f0888`
