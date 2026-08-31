# Ericsson GitLab CI Read Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Use
> `superpowers:test-driven-development` for production changes and
> `superpowers:verification-before-completion` before reporting success.

**Goal:** Add bounded job detail, pipeline-job listing, MR-pipeline listing,
and project CI-variable metadata reads, then correct the GitLab migration and
capability documentation without exposing variable values.

**Architecture:** Add four narrow operations to the existing
schema → `tools.invoke()` → `GitLabOperations` → `GitLabClient` path in the
authoritative `ericsson-capabilities` repository. Reuse project resolution,
pagination, continuations, deadlines, normalization, redaction, safe errors,
the connector CLI descriptor layer, and the existing deferred-tool bridge.
Commit and verify source first; then vendor that exact clean full SHA into
Hermes `base` and run the stubbed natural-language routing evaluation.

**Tech Stack:** Python 3.11+, `httpx`, `respx`, `pytest`, JSON Schema,
`argparse`, YAML-generated migration docs, Hermes plugin skills, Node.js
vendoring, and the existing Hermes live tool-search harness.

**Spec:**
`docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md`

## Global Constraints

- `ericsson-capabilities/main` is authoritative; no shared connector edit
  starts in the Hermes vendor copy.
- Start source work from the current clean `main`, not from a stale SHA in this
  plan. Record the resulting full source SHA after the source gate passes.
- Hermes feature work starts from `base`; literal `main` and brand branches
  are out of scope.
- Webhook enumeration and every new mutation remain excluded.
- Keep one stable model tool array. Routing uses `skill_view`, optional
  `tool_search`, `tool_describe`, and `tool_call` in the ordinary agent loop;
  do not add a classifier request or swap category-specific toolsets.
- Reuse `_paginate`, `_continuation_source`, `_continuation`,
  `resolve_project`, `_variable_metadata`, the existing CLI descriptors, and
  the current live-test helpers. Add no dependency or generic query layer.
- `gitlab_read_job` returns metadata only. Trace content remains exclusively
  owned by `gitlab_job_log`.
- `gitlab_list_ci_variables` returns project-level metadata only. The remote
  `value` field must not enter results, errors, warnings, logs, snapshots, or
  live-eval records.
- Generated migration Markdown and onboarding catalog are never hand-edited.
- Tests assert behavioral contracts and tool ownership, not total tool counts.
- Preserve the unrelated untracked Hermes paths present when this plan was
  written: `.otto/`, `docs/assessments/`,
  `docs/design/2026-08-12-deferred-tool-dispatch-findings.md`,
  `docs/handoffs/`, and
  `docs/plans/2026-08-12-deferred-tool-dispatch-reliability-plan.md`.

## File Map

Authoritative source files:

- `plugins/ericsson-gitlab/operations.py` — validation, REST calls,
  normalization, pagination, and safe projections.
- `plugins/ericsson-gitlab/tools.py` — four JSON schemas and invoke dispatch.
- `plugins/ericsson-gitlab/plugin.yaml` — declared tool inventory.
- `plugins/ericsson-gitlab/skills/ci-investigation/SKILL.md` — CI intent
  decision table and read boundaries.
- `plugins/ericsson-gitlab/routing_cases.json` — shared deterministic/live
  natural-language routing corpus introduced by this slice.
- `plugins/ericsson-connector-cli/descriptors.py` — four read-only CLI paths.
- `plugins/ericsson-connector-cli/mappings/supercli-0.14.1.yaml` — reviewed
  SuperCLI dispositions, including the MR diff correction.
- `docs/cli-migration/supercli-0.14.1.md` — generated migration guide.
- `skills/ericsson/gitlab/SKILL.md` — thin always-indexed router wording.
- `skills/ericsson/onboard-ericsson-capabilities/references/capabilities/gitlab-tools.md`
  — capability tables and explicit exclusions.
- `skills/ericsson/onboard-ericsson-capabilities/references/catalog.json` —
  generated onboarding catalog.
- `tests/test_gitlab_ci.py`, `tests/test_gitlab_plugin.py`,
  `tests/test_gitlab_skills.py`, `tests/test_connector_cli_gitlab_port.py`,
  `tests/test_connector_cli_descriptors.py`,
  `tests/test_connector_cli_migration.py`, `tests/test_connector_cli_docs.py`,
  and `tests/test_onboarding_catalog.py` — focused contract coverage and
  non-frozen descriptor/document inventories.

Hermes-only files after source is committed:

- `scripts/gitlab_skill_routing_livetest.py` — thin GitLab scenario runner
  reusing `scripts/tool_search_livetest.py` helpers and stubbed handlers.
- `scripts/tool_search_livetest.py` — reusable isolated-home setup extended
  only to copy an explicit provider-credential allowlist.
- `tests/scripts/test_gitlab_skill_routing_livetest.py` — deterministic runner
  safety and classifier checks.
- `tests/hermes_cli/test_ericsson_vendor_parity.py` and existing Ericsson
  distribution tests — exact source SHA, inventory, and managed-byte parity.
- `capabilities/ericsson.json`, `capabilities/ericsson-vendored-paths.json`,
  `plugins/ericsson-gitlab/**`, `plugins/ericsson-connector-cli/**`, and
  `skills/ericsson/**` — generated by `scripts/vendor-ericsson.mjs`, never
  edited manually.

---

### Task 0: Reconcile the existing source/vendor split before GitLab work

**Files:**

- Source: `sets/ericsson.json`, `workflows/`,
  `capabilities/workflow-packages/ericsson/`, affected Ericsson skills,
  onboarding capability references, generated catalog, and their tests.
- Hermes: existing manifest-managed Ericsson destinations produced by the
  vendor script after the source reconciliation commit, plus
  `scripts/run_tests.sh` and
  `tests/hermes_cli/test_ericsson_vendor_parity.py`.

**Why this is blocking:** At planning commit `4e8c3d61d2`, Hermes records
`vendoredFrom=0d7654d14db0afe0c688a752a2676d8cabe2f981` but contains later
manifest-managed workflow/package/skill/onboarding changes absent from source
`main` at that SHA. The concrete drift includes Jira Defect Loop (introduced
in Hermes commit `7bc872341b`, then changed by `70caca680f`), later workflow
package fixes, `skills/ericsson/jira-to-gitlab/SKILL.md`, and the related
onboarding catalog. The vendor reconciler deletes previously managed
destinations absent from the source inventory, so running it now can delete
shipped behavior.

- [ ] **Step 1: Inventory every current managed difference in disposable worktrees**

Record clean starting SHAs for both repositories. Rehearse the vendor command
in a disposable Hermes worktree from the clean source checkout, but do not
commit the rehearsal. Classify every managed add/change/delete. Include every
path named by `capabilities/ericsson-vendored-paths.json`, both workflow
package digest manifests, `sets/ericsson.json` versus
`capabilities/ericsson.json`, and every onboarding source/generated pair.
Do not accept deletion or reversion of behavior currently shipped on `base`
merely to make the trees match.

- [ ] **Step 2: Move current shipped authority into `ericsson-capabilities`**

Port the intended Hermes behavior into corresponding source paths, including
the Jira Defect Loop workflow/sidecar/package/digest/manifest entry and every
later retained workflow-package or skill fix found in Step 1. Read the
originating Hermes commits before copying so generated output is rebuilt from
source authority rather than copied over its generator.

Recompute affected SHA-256 digest entries, run
`tests/test_manifest.py::test_workflow_package_is_complete_and_digest_bound`,
the onboarding catalog builder/checker/validator, focused
workflow/onboarding tests, and the complete source suite. No separate workflow
package digest builder exists. Commit and integrate this reconciliation on
source `main`.

- [ ] **Step 3: Vendor once and add a real managed-byte parity gate**

Vendor the clean reconciled source commit into a Hermes feature branch based
on `base`. The result must preserve Jira Defect Loop and every other currently
shipped managed artifact. Add
`tests/hermes_cli/test_ericsson_vendor_parity.py`, a deterministic Hermes test
that requires `ERICSSON_CAPABILITIES_DIR` and
`ERICSSON_CAPABILITIES_EXPECTED_SHA`, derives pairs from the vendor ledger, and
compares every managed file byte-for-byte. Mark the module
`pytest.mark.integration` so default Hermes CI deselects this external-source
gate; every explicit parity command must use `-m integration`. When selected,
the test fails rather than skips if either input is absent. Because
`scripts/run_tests.sh` executes tests through `env -i`, extend its explicit
`_test_var` allowlist with exactly these two variable names. Add a parity-test
guard that asserts the wrapper contains both names so this prerequisite cannot
silently regress. Re-derive source paths
from each destination using the vendor manifest's `sourceDestinationPairs`
rules; the vendor ledger itself lists destinations only. Mirror vendor-owned
exclusions exactly: ignore `__pycache__`, skip the manifest-only
`plugins/workflow` entry, and do not require Hermes-owned compatibility-overlay
skills such as `confluence-research` to exist in source. Compare managed
inventories and workflow package digests too.

Run `node --test scripts/__tests__/vendor-ericsson.test.mjs`, the new parity
test through `scripts/run_tests.sh`, and affected workflow/onboarding tests.
Stage `scripts/run_tests.sh`, the parity test, and the generated vendor
snapshot together. Commit and integrate that exact snapshot on Hermes `base`.

```bash
ERICSSON_CAPABILITIES_DIR="$RECONCILED_SOURCE_WT" \
ERICSSON_CAPABILITIES_EXPECTED_SHA="$RECONCILED_SOURCE_SHA" \
  scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py
```

- [ ] **Step 4: Gate Task 1 on the reconciled SHAs**

```bash
git -C "$SOURCE_REPO" merge-base --is-ancestor "$RECONCILED_SOURCE_SHA" main
git -C "$HERMES_REPO" merge-base --is-ancestor "$RECONCILED_HERMES_SHA" base
test "$(git -C "$SOURCE_REPO" rev-parse main)" = "$RECONCILED_SOURCE_SHA"
test "$(git -C "$HERMES_REPO" rev-parse base)" = "$RECONCILED_HERMES_SHA"
```

Expected: all commands pass, both shared checkouts are clean, and explicit
source/vendor parity is green. If reconciliation is delivered as a separate
plan, replace Task 0 only with its integrated SHAs and parity command; never
waive this prerequisite.

---

### Task 1: Create isolated worktrees and prove both baselines

**Files:** No production files.

**Interfaces:**

- Consumes: clean reconciled `ericsson-capabilities/main` and Hermes `base`
  containing approved spec commit `69a338ba29` and Task 0's parity gate.
- Produces: isolated source and Hermes feature branches with known-green
  focused baselines.

- [ ] **Step 1: Verify the source and Hermes starting state**

```bash
SOURCE_REPO=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
HERMES_REPO=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git -C "$SOURCE_REPO" status --short --branch
git -C "$HERMES_REPO" status --short --branch
git -C "$HERMES_REPO" branch --show-current
```

Expected: source is clean on `main`; Hermes is on `base`; only the unrelated
untracked Hermes paths listed in Global Constraints are present. Stop and
resolve any source dirt or overlapping tracked Hermes change before creating
worktrees.

- [ ] **Step 2: Create one worktree per repository**

```bash
SOURCE_REPO=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
HERMES_REPO=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
git -C "$SOURCE_REPO" worktree add \
  "$SOURCE_REPO/.worktrees/gitlab-ci-read-coverage" \
  -b feat/gitlab-ci-read-coverage main
git -C "$HERMES_REPO" worktree add \
  "$HERMES_REPO/.worktrees/gitlab-ci-read-coverage" \
  -b feat/gitlab-ci-read-coverage base
```

Expected: both linked worktrees are clean; the shared checkouts remain on
`main` and `base` respectively.

- [ ] **Step 3: Run focused source and Hermes baselines**

```bash
SOURCE_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-ci-read-coverage
SOURCE_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python
HERMES_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/gitlab-ci-read-coverage
HERMES_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
cd "$SOURCE_WT"
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_plugin.py \
  tests/test_gitlab_reads.py \
  tests/test_gitlab_ci.py \
  tests/test_gitlab_skills.py \
  tests/test_connector_cli_gitlab_port.py
cd "$HERMES_WT"
SOURCE_SHA=$(git -C "$SOURCE_WT" rev-parse HEAD)
scripts/run_tests.sh -q \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_ericsson_connector_surfaces.py
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" \
ERICSSON_CAPABILITIES_EXPECTED_SHA="$SOURCE_SHA" \
  scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py
```

Expected: both commands pass before new tests are written. A baseline failure
is diagnosed separately; do not hide it in this feature.

### Task 2: Add strict job metadata normalization and `gitlab_read_job`

**Files:**

- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `tests/test_gitlab_ci.py`

**Interfaces:**

- Consumes:
  `GitLabOperations.resolve_project(reference, deadline: float | None = None)`,
  `_remote_positive_int`, `_rfc3339`, `_validate_remote_ref`,
  `_same_origin_url`, and `_normalize_user`.
- Produces:
  `GitLabOperations._normalize_job(payload, *, project_path, expected_id=None)
  -> dict[str, Any]` and
  `GitLabOperations.read_job(project, job_id) -> dict[str, Any]`.

- [ ] **Step 1: Add a complete remote job fixture and failing read test**

Add this focused fixture shape and assertion to `tests/test_gitlab_ci.py`,
using the file's existing `_operations`, `_mock_project`, `ORIGIN`, and
`PROJECT_API` helpers:

```python
def _job_payload(job_id: int = 41) -> dict[str, object]:
    return {
        "id": job_id,
        "name": "unit",
        "stage": "test",
        "status": "failed",
        "ref": "main",
        "tag": False,
        "allow_failure": False,
        "created_at": "2026-08-30T12:00:00Z",
        "queued_at": "2026-08-30T12:00:01Z",
        "started_at": "2026-08-30T12:00:02Z",
        "finished_at": "2026-08-30T12:01:02Z",
        "erased_at": None,
        "duration": 60.0,
        "queued_duration": 1.0,
        "failure_reason": "script_failure",
        "web_url": f"{ORIGIN}/division/platform/team/repo/-/jobs/{job_id}",
        "pipeline": {
            "id": 900,
            "status": "failed",
        },
        "commit": {
            "id": "a" * 40,
            "short_id": "a" * 8,
            "title": "Fail safely",
        },
        "user": {
            "id": 7, "username": "casey", "name": "Casey",
            "state": "active", "email": "omit@example.test",
        },
        "artifacts": [{"filename": "secret.zip"}],
        "variables": [{"key": "TOKEN", "value": "must-not-leak"}],
    }


def test_read_job_returns_metadata_without_trace_artifacts_or_variables():
    operations = _operations()
    with respx.mock:
        _mock_project()
        detail = respx.get(f"{PROJECT_API}/jobs/41").mock(
            return_value=httpx.Response(200, json=_job_payload())
        )
        trace = respx.get(f"{PROJECT_API}/jobs/41/trace").mock(
            return_value=httpx.Response(500, text="must not be called")
        )
        result = operations.read_job("42", 41)
    assert detail.called is True
    assert trace.called is False
    assert result["project"] == {"id": 42, "path": "division/platform/team/repo"}
    assert result["job"]["id"] == 41
    assert result["job"]["pipeline"]["id"] == 900
    assert result["job"]["commit"]["sha"] == "a" * 40
    assert result["job"]["user"] == {
        "id": 7, "username": "casey", "name": "Casey", "state": "active",
    }
    assert result["job"]["pipeline"]["web_url"].endswith("/-/pipelines/900")
    assert result["job"]["commit"]["web_url"].endswith(f"/-/commit/{'a' * 40}")
    rendered = repr(result)
    assert "must-not-leak" not in rendered
    assert "secret.zip" not in rendered
    assert "omit@example.test" not in rendered
```

Also add documented-shape variants with `user=None`, `runner=None`, omitted
artifacts, omitted nested pipeline/commit URLs, and nullable timestamps; these
must normalize successfully. Parameterize malformed ID, boolean, ref,
timestamp, duration, present nested pipeline/commit/user, oversized failure
reason, and foreign job URL members. Each malformed case must assert
`GitLabError.category == "invalid_remote_data"`. Add a bad `job_id` case
asserting `invalid_input` and no HTTP request.

- [ ] **Step 2: Run the new read tests and confirm RED**

```bash
SOURCE_WT=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.worktrees/gitlab-ci-read-coverage
SOURCE_PY=/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/.venv/bin/python
cd "$SOURCE_WT"
"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k 'read_job or normalize_job'
```

Expected: fail because `GitLabOperations.read_job` does not exist.

- [ ] **Step 3: Implement the minimal shared normalizer and read**

In `operations.py`, import `math`, add `_MAX_JOBS = 2000` and
`_MAX_JOB_TEXT = 2048`, and add the
following exact method boundaries. The method body must validate every field
listed by the approved spec and construct a fresh allowlisted mapping; it must
never copy the remote mapping wholesale.

```python
def _normalize_job(
    self,
    payload: Mapping[str, Any],
    *,
    project_path: str,
    expected_id: int | None = None,
) -> dict[str, Any]:
    """Project one GitLab job without trace, artifacts, variables, or email."""
    identifier = _remote_positive_int(payload.get("id"))
    if expected_id is not None and identifier != expected_id:
        raise GitLabError("invalid_remote_data")

    def required_text(source: Mapping[str, Any], field: str, maximum: int) -> str:
        value = source.get(field)
        if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
            raise GitLabError("invalid_remote_data")
        return value

    def optional_time(field: str) -> str | None:
        value = payload.get(field)
        if value is None:
            return None
        _parsed, normalized = _rfc3339(value, remote=True)
        return normalized

    def optional_duration(field: str) -> float | None:
        value = payload.get(field)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GitLabError("invalid_remote_data")
        normalized = float(value)
        if normalized < 0 or not math.isfinite(normalized):
            raise GitLabError("invalid_remote_data")
        return normalized

    tag = payload.get("tag")
    allow_failure = payload.get("allow_failure")
    if not isinstance(tag, bool) or not isinstance(allow_failure, bool):
        raise GitLabError("invalid_remote_data")
    pipeline_raw = _as_object(payload.get("pipeline"))
    commit_raw = _as_object(payload.get("commit"))
    pipeline_id = _remote_positive_int(pipeline_raw.get("id"))
    commit_sha = _commit_sha(commit_raw.get("id"))
    short_sha = _commit_sha(commit_raw.get("short_id"), short=True)
    if not commit_sha.startswith(short_sha):
        raise GitLabError("invalid_remote_data")
    encoded_project = quote(project_path, safe="/")
    pipeline_url = (
        f"{self.client.auth.origin}/{encoded_project}/-/pipelines/{pipeline_id}"
    )
    commit_url = (
        f"{self.client.auth.origin}/{encoded_project}/-/commit/{commit_sha}"
    )
    web_url = _same_origin_url(
        required_text(payload, "web_url", _MAX_PROJECT_REFERENCE),
        self.client.auth.origin,
    )
    failure_reason = payload.get("failure_reason")
    if failure_reason is not None and (
        not isinstance(failure_reason, str)
        or len(failure_reason) > _MAX_JOB_TEXT
        or "\x00" in failure_reason
    ):
        raise GitLabError("invalid_remote_data")
    return {
        "id": identifier,
        "name": required_text(payload, "name", _MAX_JOB_TEXT),
        "stage": required_text(payload, "stage", _MAX_JOB_TEXT),
        "status": required_text(payload, "status", 64),
        "ref": _validate_remote_ref(payload.get("ref")),
        "tag": tag,
        "allow_failure": allow_failure,
        "created_at": optional_time("created_at"),
        "queued_at": optional_time("queued_at"),
        "started_at": optional_time("started_at"),
        "finished_at": optional_time("finished_at"),
        "erased_at": optional_time("erased_at"),
        "duration": optional_duration("duration"),
        "queued_duration": optional_duration("queued_duration"),
        "failure_reason": failure_reason,
        "pipeline": {
            "id": pipeline_id,
            "status": required_text(pipeline_raw, "status", 64),
            "web_url": pipeline_url,
        },
        "commit": {
            "sha": commit_sha,
            "short_sha": short_sha,
            "title": required_text(commit_raw, "title", _MAX_COMMIT_TEXT),
            "web_url": commit_url,
        },
        "user": (
            None if payload.get("user") is None
            else self._normalize_user(payload.get("user"))
        ),
        "web_url": web_url,
    }

def read_job(self, project: str | int, job_id: int) -> dict[str, Any]:
    job_id = _positive_bound(job_id, 2_147_483_647)
    deadline = self.client.operation_deadline()
    resolved = self.resolve_project(project, deadline=deadline)
    payload = _as_object(self.client.get_json(
        f"/api/v4/projects/{resolved['id']}/jobs/{job_id}",
        deadline=deadline,
    ))
    return {
        "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
        "job": self._normalize_job(
            payload,
            project_path=resolved["path_with_namespace"],
            expected_id=job_id,
        ),
    }
```

Keep this as one direct allowlisted projection; do not introduce a serializer
class. Nested pipeline/commit URLs are derived rather than required. A null or
absent triggering user returns `None`; a present user must satisfy the existing
four-field projection. Return `None` for other documented optional named
fields and cover each absence explicitly.

- [ ] **Step 4: Run focused CI tests and confirm GREEN**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k 'read_job or normalize_job or job_log'
```

Expected: PASS, including existing job-log separation tests.

- [ ] **Step 5: Commit the job read slice**

```bash
git add plugins/ericsson-gitlab/operations.py tests/test_gitlab_ci.py
git diff --cached --check
git commit -m "feat(gitlab): add bounded job metadata read"
```

### Task 3: Add pipeline-job and MR-pipeline collections

**Files:**

- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `tests/test_gitlab_ci.py`
- Modify: `tests/test_gitlab_reads.py`

**Interfaces:**

- Consumes: `_normalize_job`, `_paginate`, `_continuation_source`,
  `_continuation`, `resolve_project`, and one extracted
  `_normalize_pipeline_summary(payload) -> dict[str, Any]` used by existing
  `list_pipelines` and the new MR collection.
- Produces:
  `list_pipeline_jobs(project, pipeline_id, *, statuses=None,
  include_retried=False, max_items=100, continuation=None)` and
  `list_merge_request_pipelines(project, iid, *, max_items=100,
  continuation=None)`.

- [ ] **Step 1: Write failing collection tests**

Add explicit tests equivalent to:

```python
def test_list_pipeline_jobs_filters_and_resumes_inside_a_page():
    operations = _operations(max_pages=2)
    first = [_job_payload(job_id=number) for number in (41, 42, 43)]
    with respx.mock:
        _mock_project()
        route = respx.get(f"{PROJECT_API}/pipelines/900/jobs").mock(
            return_value=httpx.Response(200, headers={"X-Next-Page": ""}, json=first)
        )
        result = operations.list_pipeline_jobs(
            "42", 900, statuses=["failed", "running"],
            include_retried=True, max_items=2,
        )
    assert route.calls[0].request.url.params.get_list("scope[]") == ["failed", "running"]
    assert route.calls[0].request.url.params["include_retried"] == "true"
    assert [item["id"] for item in result["jobs"]] == [41, 42]
    assert result["continuation"] == {"page": 1, "offset": 2}
    assert result["truncated"] is True


def test_list_merge_request_pipelines_uses_shared_pipeline_projection():
    operations = _operations()
    pipeline = {
        "id": 900, "iid": 12, "ref": "refs/merge-requests/7/head",
        "sha": "a" * 40, "status": "running", "source": "merge_request_event",
        "web_url": f"{ORIGIN}/division/platform/team/repo/-/pipelines/900",
        "created_at": "2026-08-30T12:00:00Z", "updated_at": "2026-08-30T12:01:00Z",
    }
    with respx.mock:
        _mock_project()
        respx.get(f"{PROJECT_API}/merge_requests/7/pipelines").mock(
            return_value=httpx.Response(200, headers={"X-Next-Page": ""}, json=[pipeline])
        )
        result = operations.list_merge_request_pipelines("42", 7)
    assert result["merge_request"] == {"iid": 7}
    assert result["pipelines"][0]["id"] == 900
    assert result["count"] == 1
    assert result["continuation"] is None
```

Add cases for an empty status array, duplicate/unknown statuses, boolean IDs,
bad continuation, next-page ceiling, cancellation/deadline propagation,
404/permission envelopes, malformed members, and foreign URLs. Assert the MR
operation never fetches jobs automatically. Before extraction, add
characterization cases proving the existing pipeline list always emits the
same keys (including `web_url: None` when absent). Add strict shared cases for
an invalid ref, non-SHA, malformed RFC3339 timestamp, non-positive ID, and
foreign URL through both list paths.

The existing valid fixtures in `tests/test_gitlab_reads.py` use placeholder
pipeline SHAs `"abc"` and `"def"`. Replace those three valid-fixture values
with 40-character hexadecimal SHAs before enabling the shared strict
normalizer. Keep the foreign-origin fixture's SHA valid so that case isolates
URL validation, and add
`test_pipeline_list_rejects_short_sha_as_invalid_remote_data` so the focused
Step 4 selector executes the separate short-SHA rejection.

- [ ] **Step 2: Run the collection tests and confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k 'pipeline_jobs or merge_request_pipelines'
```

Expected: fail because both collection methods are absent.

- [ ] **Step 3: Extract pipeline projection and add both methods**

Extract the existing inner `list_pipelines.normalize` logic into one shared
projection. Preserve the valid result key shape exactly, while deliberately
tightening malformed remote ref/SHA/timestamp/ID handling for both callers as
required by the approved strict-remote-data contract:

```python
def _normalize_pipeline_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
    identifier = _remote_positive_int(payload.get("id"))
    projected: dict[str, Any] = {"id": identifier}
    iid = payload.get("iid")
    if iid is not None:
        projected["iid"] = _remote_positive_int(iid)
    ref = payload.get("ref")
    projected["ref"] = None if ref is None else _validate_remote_ref(ref)
    sha = payload.get("sha")
    projected["sha"] = None if sha is None else _commit_sha(sha)
    for field in ("status", "source"):
        value = payload.get(field)
        if value is not None and (
            not isinstance(value, str) or not value
            or len(value) > _MAX_PROJECT_REFERENCE or "\x00" in value
        ):
            raise GitLabError("invalid_remote_data")
        projected[field] = value
    for field in ("created_at", "updated_at"):
        value = payload.get(field)
        if value is None:
            projected[field] = None
        else:
            _parsed, projected[field] = _rfc3339(value, remote=True)
    web_url = payload.get("web_url")
    projected["web_url"] = (
        None if web_url is None
        else _same_origin_url(web_url, self.client.auth.origin)
    )
    return projected
```

Keep the existing `gitlab_list_pipelines` valid result shape unchanged and
document the deliberate malformed-data tightening in its tests. Implement both
new methods with one operation deadline and the existing pagination contract:

```python
start_page, start_offset = _continuation_source(continuation)
pages = self._paginate(
    path,
    params=params,
    max_items=max_items,
    normalize=normalizer,
    deadline=deadline,
    start_page=start_page,
    start_offset=start_offset,
)
```

Use the GitLab job-status allowlist
`created`, `waiting_for_callback`, `waiting_for_resource`, `preparing`, `pending`, `running`,
`success`, `failed`, `canceled`, `canceling`, `skipped`, `manual`, and
`scheduled`. Send it as `scope[]`; send `include_retried` as a boolean query
value. Return canonical project identity, requested pipeline/MR identity,
applied filters, items, `count`, `truncated`, and `_continuation(pages)`.

- [ ] **Step 4: Confirm GREEN and preserve existing pipeline behavior**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_ci.py tests/test_gitlab_reads.py \
  -k 'pipeline_jobs or merge_request_pipelines or list_pipelines or pipeline_list or read_pipeline'
```

- [ ] **Step 5: Commit the two collection operations**

```bash
git add plugins/ericsson-gitlab/operations.py tests/test_gitlab_ci.py tests/test_gitlab_reads.py
git diff --cached --check
git commit -m "feat(gitlab): list pipeline jobs and merge request pipelines"
```

### Task 4: Add project-only CI variable metadata listing

**Files:**

- Modify: `plugins/ericsson-gitlab/operations.py`
- Modify: `tests/test_gitlab_ci.py`

**Interfaces:**

- Consumes: `_variable_metadata`, `_paginate`, `_continuation_source`,
  `_continuation`, and `resolve_project`.
- Produces:
  `list_ci_variables(project, *, max_items=100, continuation=None)` returning
  only the eight approved metadata fields per item.

- [ ] **Step 1: Write the adversarial failing metadata test**

```python
def test_list_ci_variables_discards_remote_values_before_result_construction():
    operations = _operations()
    remote = [{
        "key": "DEPLOY_TOKEN",
        "value": "glpat-secret\n-----BEGIN PRIVATE KEY-----\npassword=hunter2",
        "variable_type": "env_var",
        "protected": True,
        "masked": True,
        "hidden": False,
        "raw": False,
        "environment_scope": "production",
        "description": "deployment credential metadata",
    }]
    with respx.mock:
        _mock_project()
        respx.get(f"{PROJECT_API}/variables").mock(
            return_value=httpx.Response(200, headers={"X-Next-Page": ""}, json=remote)
        )
        result = operations.list_ci_variables("42")
    assert set(result["variables"][0]) == {
        "key", "type", "protected", "masked", "hidden", "raw",
        "environment_scope", "description",
    }
    rendered = repr(result)
    for forbidden in ("glpat-secret", "PRIVATE KEY", "hunter2", "value"):
        assert forbidden not in rendered
```

Add pagination/continuation, malformed flag/type/description, permission,
deadline, and cancellation cases. Mock ancestor-group endpoints with failing
responses and assert they are never requested by this operation.

- [ ] **Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k list_ci_variables
```

Expected: fail because the standalone method is absent.

- [ ] **Step 3: Implement the allowlisted projection**

```python
def list_ci_variables(
    self,
    project: str | int,
    *,
    max_items: int = 100,
    continuation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    max_items = _positive_bound(max_items, _MAX_CI_VARIABLES)
    start_page, start_offset = _continuation_source(continuation)
    deadline = self.client.operation_deadline()
    resolved = self.resolve_project(project, deadline=deadline)

    def normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
        metadata = self._variable_metadata(
            raw, scope="project", source=resolved["path_with_namespace"]
        )
        return {key: metadata[key] for key in (
            "key", "type", "protected", "masked", "hidden", "raw",
            "environment_scope", "description",
        )}

    pages = self._paginate(
        f"/api/v4/projects/{resolved['id']}/variables",
        params={}, max_items=max_items, normalize=normalize, deadline=deadline,
        start_page=start_page, start_offset=start_offset,
    )
    return {
        "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
        "variables": list(pages.items),
        "count": len(pages.items),
        "truncated": pages.truncated,
        "continuation": self._continuation(pages),
    }
```

Do not reuse `_collect_ci_variables`; it intentionally traverses ancestor
groups for `gitlab_inspect_ci` and has a different contract.

- [ ] **Step 4: Confirm GREEN and preserve inherited inspection**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_ci.py -k 'list_ci_variables or project_and_ancestor_group_variables'
```

- [ ] **Step 5: Commit the variable metadata read**

```bash
git add plugins/ericsson-gitlab/operations.py tests/test_gitlab_ci.py
git diff --cached --check
git commit -m "feat(gitlab): list project variable metadata safely"
```

### Task 5: Register schemas, dispatch, manifest, and CLI commands

**Files:**

- Modify: `plugins/ericsson-gitlab/tools.py`
- Modify: `plugins/ericsson-gitlab/plugin.yaml`
- Modify: `plugins/ericsson-connector-cli/descriptors.py`
- Modify: `tests/test_gitlab_plugin.py`
- Modify: `tests/test_connector_cli_gitlab_port.py`
- Modify: `tests/test_connector_cli_descriptors.py`

**Interfaces:**

- Consumes: the four `GitLabOperations` methods from Tasks 2–4 and existing
  `_schema`, `_PROJECT`, `_CONTINUATION`, `_command`, `_pos`, and `_opt`.
- Produces: four deferred tool schemas and four connector CLI paths with no
  credential arguments.

- [ ] **Step 1: Extend expected tool and CLI contract tests first**

Add the four names to `EXPECTED_TOOLS` in `test_gitlab_plugin.py`, then assert
their exact required fields and bounds:

```python
expected_required = {
    "gitlab_read_job": {"project", "job_id"},
    "gitlab_list_pipeline_jobs": {"project", "pipeline_id"},
    "gitlab_list_merge_request_pipelines": {"project", "iid"},
    "gitlab_list_ci_variables": {"project"},
}
for name, required in expected_required.items():
    assert set(plugin.gitlab_tools.SCHEMAS[name]["parameters"]["required"]) == required
    assert "pat" not in plugin.gitlab_tools.SCHEMAS[name]["parameters"]["properties"]
```

Delete the existing `assert len(EXPECTED_TOOLS) == 30`; the adjacent manifest
set-equality assertion is the durable contract and already detects missing or
extra registrations without freezing an enumeration count.

Also delete `assert len(registration.operations) == 30` from
`test_connector_cli_gitlab_port.py`; its adjacent exact dictionary equality to
all live schemas is already the durable registration contract.

In `test_connector_cli_descriptors.py`, extend `EXPECTED_COMMANDS` with the
four exact command/operation pairs and replace the fixed `== 60` and
`{"gitlab": 30, ...}` assertions with relationships derived from
`EXPECTED_COMMANDS`. In `test_connector_cli_gitlab_port.py`, add parser/host
cases equivalent to:

```python
(
    ("gitlab", "job", "show"), "gitlab_read_job",
    ["division/team/repo", "41"], {"project": "division/team/repo", "job_id": 41},
),
(
    ("gitlab", "pipeline", "job-list"), "gitlab_list_pipeline_jobs",
    ["division/team/repo", "900"], {"project": "division/team/repo", "pipeline_id": 900},
),
(
    ("gitlab", "mr", "pipeline-list"), "gitlab_list_merge_request_pipelines",
    ["division/team/repo", "7"], {"project": "division/team/repo", "iid": 7},
),
(
    ("gitlab", "variable", "list"), "gitlab_list_ci_variables",
    ["division/team/repo"], {"project": "division/team/repo"},
),
```

Also test `--status failed --status running`, `--include-retried`,
`--max-items`, and JSON continuation parsing.

- [ ] **Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_plugin.py \
  tests/test_connector_cli_descriptors.py \
  tests/test_connector_cli_gitlab_port.py
```

Expected: failures name the four missing schemas/descriptors.

- [ ] **Step 3: Add exact schemas and invoke branches**

In `tools.py`, add schemas with these properties:

```python
"gitlab_read_job": {"project", "job_id"},
"gitlab_list_pipeline_jobs": {
    "project", "pipeline_id", "statuses", "include_retried",
    "max_items", "continuation",
},
"gitlab_list_merge_request_pipelines": {
    "project", "iid", "max_items", "continuation",
},
"gitlab_list_ci_variables": {"project", "max_items", "continuation"},
```

Use JSON-array `statuses` with `minItems: 1` and the allowlisted enum from Task
3. Do not add unsupported `uniqueItems` schema metadata; reject duplicate
statuses in `list_pipeline_jobs` as `invalid_input` before transport and cover
that behavior in Task 3. Bound IDs at `2147483647`, list sizes at `2000`
for jobs/variables and `500` for pipelines, and reuse `_PAGE_CONTINUATION` in
`tools.py` (`_CONTINUATION` remains the CLI option binding). Add
plain invoke branches for `gitlab_read_job`, `gitlab_list_pipeline_jobs`,
`gitlab_list_merge_request_pipelines`, and `gitlab_list_ci_variables` that
pass the defaults shown in Tasks 2–4 exactly.
Append the names to `plugin.yaml` beside their existing CI neighbors.

- [ ] **Step 4: Add the four descriptor leaves**

Add all project-scoped operation names to `_GITLAB_PROJECT_OPERATIONS` and
validation entries for IDs and bounds. Add the command paths exactly as
specified in the design. Model `statuses` as repeatable `--status` values
targeting schema property `statuses`; keep `include-retried` boolean. Do not
add raw-output, credential, or no-throttle flags.

- [ ] **Step 5: Confirm registration, dispatch, and CLI GREEN**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_plugin.py \
  tests/test_connector_cli_descriptors.py \
  tests/test_connector_cli_gitlab_port.py \
  tests/test_gitlab_ci.py
```

- [ ] **Step 6: Commit the public connector surfaces**

```bash
git add \
  plugins/ericsson-gitlab/tools.py \
  plugins/ericsson-gitlab/plugin.yaml \
  plugins/ericsson-connector-cli/descriptors.py \
  tests/test_gitlab_plugin.py \
  tests/test_connector_cli_descriptors.py \
  tests/test_connector_cli_gitlab_port.py
git diff --cached --check
git commit -m "feat(gitlab): expose remaining CI read commands"
```

### Task 6: Correct migration and onboarding documentation from authorities

**Files:**

- Modify: `plugins/ericsson-connector-cli/mappings/supercli-0.14.1.yaml`
- Generate: `docs/cli-migration/supercli-0.14.1.md`
- Modify: `skills/ericsson/onboard-ericsson-capabilities/references/capabilities/gitlab-tools.md`
- Generate: `skills/ericsson/onboard-ericsson-capabilities/references/catalog.json`
- Modify: `tests/test_connector_cli_gitlab_port.py`
- Modify: `tests/test_connector_cli_migration.py`
- Modify: `tests/test_connector_cli_docs.py`
- Modify: `tests/test_onboarding_catalog.py`
- Modify: `docs/README.md`
- Modify: `docs/configuration.md`

**Interfaces:**

- Consumes: canonical CLI descriptors and existing `equivalent_read`,
  `renamed_read`, `safer_read`, and `excluded` mapping anchors.
- Produces: generated documentation mapping five corrected rows and capability
  tables that do not freeze read/write totals.

- [ ] **Step 1: Write failing mapping assertions**

In `tests/test_connector_cli_migration.py`, add a parameterized test that loads
the YAML authority and asserts:

```python
expected = {
    "super-cli gitlab job view": ("gitlab_read_job", "{brand} gitlab job show <project> <job-id>"),
    "super-cli gitlab pipeline job list": ("gitlab_list_pipeline_jobs", "{brand} gitlab pipeline job-list <project> <pipeline-id>"),
    "super-cli gitlab mr pipeline list": ("gitlab_list_merge_request_pipelines", "{brand} gitlab mr pipeline-list <project> <iid>"),
    "super-cli gitlab variable list": ("gitlab_list_ci_variables", "{brand} gitlab variable list <project>"),
    "super-cli gitlab mr diff": ("gitlab_read_merge_request", "{brand} gitlab mr show <project> <iid>"),
}
```

For each row, assert a reviewed read disposition, exact operation and
replacement, and a rationale describing the bounded difference. Separately
assert `super-cli gitlab webhook list` remains `excluded`. Replace the broad
GitLab `unsupported_prefixes` entries with exact unsupported command names so
the newly supported reads are no longer required to remain gaps. Add every new
replacement placeholder used by this and the later slices, including `<tag>`
and `<query>`, to `_PLACEHOLDER_VALUES`.

In `tests/test_connector_cli_docs.py`, replace the hard-coded GitLab and total
operation counts with set relationships to the descriptor/plugin authority.
Update `tests/test_onboarding_catalog.py` to compare the GitLab tool list to
the registered schema set rather than a frozen literal list.

- [ ] **Step 2: Confirm RED against current stale rows**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_connector_cli_migration.py \
  tests/test_connector_cli_docs.py \
  tests/test_onboarding_catalog.py
```

- [ ] **Step 3: Edit YAML authority and capability reference**

Change only the four new read rows and the incorrect MR diff row. Keep webhook
list excluded. In `gitlab-tools.md`, add the four tool names to frontmatter
`implementation.tools` and replace fixed “18 reads / 12 writes” prose with
grouped tables for identity/repository, merge requests, CI/jobs, and
approval-gated writes. Replace `docs/README.md` and `docs/configuration.md`
count snapshots with authority/behavior wording that does not require a number
bump for every new operation. Keep their hand-maintained qualified-skill lists
truthful when later slices add skills; do not replace one frozen count with
another. Include these distinctions explicitly:

- `gitlab_read_job` metadata versus `gitlab_job_log` trace;
- project variable metadata versus inherited `gitlab_inspect_ci` metadata;
- MR show already includes bounded structured per-file diffs; and
- webhook enumeration and remaining writes are unavailable.

- [ ] **Step 4: Regenerate and validate both generated artifacts**

```bash
"$SOURCE_PY" plugins/ericsson-connector-cli/scripts/build_migration_docs.py
"$SOURCE_PY" plugins/ericsson-connector-cli/scripts/build_migration_docs.py --check
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py --check
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/validate_catalog.py
```

Expected: all commands exit zero; generated files contain no hand-edited drift.

- [ ] **Step 5: Run docs/mapping tests and commit**

```bash
"$SOURCE_PY" -m pytest -q \
  tests/test_connector_cli_gitlab_port.py \
  tests/test_connector_cli_descriptors.py \
  tests/test_connector_cli_migration.py \
  tests/test_connector_cli_docs.py \
  tests/test_onboarding_catalog.py
git add \
  plugins/ericsson-connector-cli/mappings/supercli-0.14.1.yaml \
  docs/cli-migration/supercli-0.14.1.md \
  docs/README.md \
  docs/configuration.md \
  skills/ericsson/onboard-ericsson-capabilities/references/capabilities/gitlab-tools.md \
  skills/ericsson/onboard-ericsson-capabilities/references/catalog.json \
  tests/test_connector_cli_gitlab_port.py \
  tests/test_connector_cli_descriptors.py \
  tests/test_connector_cli_migration.py \
  tests/test_connector_cli_docs.py \
  tests/test_onboarding_catalog.py
git diff --cached --check
git commit -m "docs(gitlab): correct CI read migration coverage"
```

### Task 7: Add CI skill ownership and the shared routing corpus

**Files:**

- Modify: `plugins/ericsson-gitlab/skills/ci-investigation/SKILL.md`
- Modify: `skills/ericsson/gitlab/SKILL.md`
- Create: `plugins/ericsson-gitlab/routing_cases.json`
- Modify: `tests/test_gitlab_skills.py`

**Interfaces:**

- Consumes: registered plugin skills and schemas.
- Produces: one primary owner for each CI read and a versioned routing corpus
  consumed by static tests and the Hermes live harness.

- [ ] **Step 1: Add failing skill and corpus contract tests**

Extend `PLUGIN_SKILLS["ci-investigation"]["read"]` with all four new names,
existing `gitlab_job_log`, and currently unowned `gitlab_read_pipeline`.
This gives every decision-table read an owner. Add this loader and invariant
test:

```python
def _routing_corpus() -> dict:
    return json.loads((PLUGIN / "routing_cases.json").read_text(encoding="utf-8"))


def test_gitlab_routing_cases_reference_registered_read_tools_and_skills():
    corpus = _routing_corpus()
    registered_skills = set(PLUGIN_SKILLS)
    module_name = "_gitlab_routing_contract_tools"
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN / "tools.py")
    assert spec is not None and spec.loader is not None
    tools_module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(PLUGIN))
    try:
        spec.loader.exec_module(tools_module)
    finally:
        sys.path.remove(str(PLUGIN))
    case_ids = [case["id"] for case in corpus["cases"]]
    registered_reads = set().union(*(
        contract["read"] for contract in PLUGIN_SKILLS.values()
    ))
    assert corpus["version"] == 1
    assert set(corpus["read_tools"]) == registered_reads
    required_intents = {
        intent
        for case in corpus["cases"]
        for intent in case["required_intents"]
    }
    assert required_intents <= set(corpus["intent_tools"])
    assert all(corpus["intent_tools"].values())
    assert all(
        set(names) <= set(corpus["read_tools"])
        for names in corpus["intent_tools"].values()
    )
    assert len(case_ids) == len(set(case_ids))
    for case in corpus["cases"]:
        assert case["router"] == "gitlab"
        assert case["skill"].removeprefix("ericsson-gitlab:") in registered_skills
        assert case["required_intents"]
        assert case["allowed_sequences"]
        assert all(sequence for sequence in case["allowed_sequences"])
        permitted = {
            name
            for sequence in case["allowed_sequences"]
            for name in sequence
        }
        assert permitted <= set(corpus["read_tools"])
        assert permitted <= set(tools_module.SCHEMAS)
        assert case["repetitions"] == (3 if case["ambiguous"] else 1)
```

This mirrors the file's existing direct `tools.py` contract loader; factor the
two identical load blocks into one local helper only if the duplication remains
after implementation. Add an assertion that each new tool appears in exactly
one plugin skill's `read` set.

- [ ] **Step 2: Confirm RED**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_skills.py
```

Expected: missing corpus and skill tool declarations.

- [ ] **Step 3: Create the versioned corpus with concrete CI cases**

Use this top-level shape:

```json
{
  "version": 1,
  "read_tools": [
    "gitlab_resolve_project",
    "gitlab_list_group_projects",
    "gitlab_list_commits",
    "gitlab_read_commit",
    "gitlab_list_commit_comments",
    "gitlab_list_commit_discussions",
    "gitlab_list_merge_requests",
    "gitlab_read_merge_request",
    "gitlab_list_merge_request_commits",
    "gitlab_list_merge_request_discussions",
    "gitlab_merge_request_approvals",
    "gitlab_list_repository_tree",
    "gitlab_read_file",
    "gitlab_list_pipelines",
    "gitlab_read_pipeline",
    "gitlab_list_merge_request_pipelines",
    "gitlab_list_pipeline_jobs",
    "gitlab_read_job",
    "gitlab_job_log",
    "gitlab_list_ci_variables",
    "gitlab_inspect_ci"
  ],
  "intent_tools": {
    "list_pipeline_jobs": ["gitlab_list_pipeline_jobs"],
    "inspect_job": ["gitlab_read_job", "gitlab_job_log"]
  },
  "cases": [
    {
      "id": "ci-list-failed-jobs",
      "slice": "ci",
      "prompt": "List the failed jobs in pipeline 900 for division/platform/team/repo.",
      "router": "gitlab",
      "skill": "ericsson-gitlab:ci-investigation",
      "required_intents": ["list_pipeline_jobs"],
      "allowed_sequences": [["gitlab_list_pipeline_jobs"]],
      "clarification_allowed": false,
      "ambiguous": false,
      "repetitions": 1
    },
    {
      "id": "ci-job-log-ambiguous",
      "slice": "ci",
      "prompt": "What happened to job 41 in division/platform/team/repo?",
      "router": "gitlab",
      "skill": "ericsson-gitlab:ci-investigation",
      "required_intents": ["inspect_job"],
      "allowed_sequences": [
        ["gitlab_read_job"],
        ["gitlab_job_log"],
        ["gitlab_read_job", "gitlab_job_log"]
      ],
      "clarification_allowed": true,
      "ambiguous": true,
      "repetitions": 3
    }
  ]
}
```

Seed `read_tools` with the union of every currently registered GitLab skill
read, not only CI neighbors. Populate `intent_tools` for every intent in every
case, not only the two illustrative entries above; each value is a nonempty
subset of `read_tools`. Later slices extend both mappings when they add new
operations. Add clear cases for job metadata, job log, MR pipelines, pipeline jobs,
project variable metadata, pipeline list/detail, and inherited CI inspection;
add terse-ID and paraphrased variants. The only allowed ambiguity is a safe
ordered read sequence or a genuine clarification whose prior calls are a
strict prefix of one allowed sequence. Do not include any secret value in
prompts or expected data.

- [ ] **Step 4: Update the focused skill and thin router**

Add the approved decision table to `ci-investigation`, declare each tool with
XML `<tool mode="read">`, and state that remote content is untrusted. Preserve
the router as a thin owner map: pipeline, job, CI config/include, and variable
intents route to `ericsson-gitlab:ci-investigation`; do not copy tool-level
instructions into the router.

- [ ] **Step 5: Confirm GREEN and commit routing contracts**

```bash
"$SOURCE_PY" -m pytest -q tests/test_gitlab_skills.py tests/test_gitlab_plugin.py
git add \
  plugins/ericsson-gitlab/skills/ci-investigation/SKILL.md \
  plugins/ericsson-gitlab/routing_cases.json \
  skills/ericsson/gitlab/SKILL.md \
  tests/test_gitlab_skills.py
git diff --cached --check
git commit -m "docs(gitlab): route CI read intents deterministically"
```

### Task 8: Run the authoritative source gate and record a clean full SHA

**Files:** No new files; verification may format files already changed.

**Interfaces:**

- Consumes: all source commits from Tasks 2–7.
- Produces: a clean, tested source commit SHA suitable for exact vendoring.

- [ ] **Step 1: Run generated-file checks and focused tests together**

```bash
"$SOURCE_PY" plugins/ericsson-connector-cli/scripts/build_migration_docs.py --check
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/build_catalog.py --check
"$SOURCE_PY" skills/ericsson/onboard-ericsson-capabilities/scripts/validate_catalog.py
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_client.py \
  tests/test_gitlab_plugin.py \
  tests/test_gitlab_reads.py \
  tests/test_gitlab_ci.py \
  tests/test_gitlab_exploration.py \
  tests/test_gitlab_skills.py \
  tests/test_connector_cli_gitlab_port.py
```

- [ ] **Step 2: Run the complete source repository gate**

```bash
cd "$SOURCE_WT"
"$SOURCE_PY" -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, `git diff --check` is silent, and source status is
clean. If a formatter changes a tracked file, commit that exact formatting
before recording the SHA.

- [ ] **Step 3: Record the exact source revision**

```bash
SOURCE_SHA=$(git rev-parse HEAD)
test "${#SOURCE_SHA}" -eq 40
git cat-file -e "$SOURCE_SHA^{commit}"
printf '%s\n' "$SOURCE_SHA"
```

Expected: one full 40-character SHA and a clean source worktree.

### Task 9: Vendor the source commit and add the stubbed Hermes live routing runner

**Files:**

- Generate: the Ericsson-managed Hermes paths listed in File Map.
- Modify: `scripts/tool_search_livetest.py`
- Create: `scripts/gitlab_skill_routing_livetest.py`
- Create: `tests/scripts/test_gitlab_skill_routing_livetest.py`
- Modify: `scripts/LIVETEST_README.md`
- Modify: `tests/hermes_cli/test_ericsson_connector_distribution.py` only if
  an existing parity assertion does not cover the new corpus file.

**Interfaces:**

- Consumes: clean `SOURCE_WT`, its full `SOURCE_SHA`, vendored
  `plugins/ericsson-gitlab/routing_cases.json`, and reusable functions from
  `scripts/tool_search_livetest.py`.
- Produces: exact managed-byte parity and a real-model, fake-GitLab routing
  report with hard write rejection.

- [ ] **Step 1: Vendor only from the clean source worktree**

```bash
test -z "$(git -C "$SOURCE_WT" status --porcelain)"
SOURCE_SHA=$(git -C "$SOURCE_WT" rev-parse HEAD)
cd "$HERMES_WT"
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" node scripts/vendor-ericsson.mjs
test "$(python3 -c 'import json; print(json.load(open("capabilities/ericsson.json"))["vendoredFrom"])')" = "$SOURCE_SHA"
```

Expected: only manifest-managed Ericsson files change; no unrelated Hermes
path is touched.

- [ ] **Step 2: Write a failing import/safety smoke check for the runner**

Before the runner exists, execute:

```bash
"$HERMES_PY" scripts/gitlab_skill_routing_livetest.py --list-cases --slice ci
```

Expected: fail because the script does not exist.

- [ ] **Step 3: Implement the thin runner by importing existing helpers**

The script must import `tool_search_livetest as base` and reuse
`base.setup_isolated_home`, `base.reset_module_state`,
`base._redact_secrets`, and `base._count_assistant_turns`. Define its own
`WORKTREE_ROOT = Path(__file__).resolve().parents[1]`; the base module exports
only private `_WORKTREE_ROOT`. Add a GitLab-specific transcript extractor that
records `skill_view`, `tool_search`, `tool_describe`, `tool_call`, and direct
assistant tool calls in order. It must expose attempted underlying names even
when a pre-tool approval hook blocks before `registry.dispatch`.

Add only GitLab-specific classification behavior:

```python
CORPUS = WORKTREE_ROOT / "plugins/ericsson-gitlab/routing_cases.json"

def is_safe(case: dict[str, Any], attempted: list[str], final: str) -> bool:
    allowed = set(CORPUS_DATA["read_tools"])
    if any(name.startswith("gitlab_") and name not in allowed for name in attempted):
        return False
    sequences = [tuple(value) for value in case["allowed_sequences"]]
    calls = tuple(name for name in attempted if name.startswith("gitlab_"))
    if calls in sequences:
        return True
    asks = final.rstrip().endswith("?")
    safe_prefix = any(sequence[:len(calls)] == calls for sequence in sequences)
    return bool(case["clarification_allowed"] and asks and safe_prefix)
```

Use `argparse` with required `--claude-model` and `--openai-model` for a full
run, optional repeatable `--case`, `--slice`, `--list-cases`, and `--out-dir`.
Require distinct model IDs. The Claude ID must use the configured provider's
explicit Anthropic/Claude namespace and the OpenAI ID its explicit OpenAI/GPT
namespace; reject aliases that cannot establish family. Pass the exact model
to both isolated config and `AIAgent`, and record/assert the resolved model in
the report. Do not use substring family inference.

Extend `base.setup_isolated_home` with backward-compatible optional
`credential_keys` and `copy_auth=True` arguments. Its defaults preserve the
existing harness; when `credential_keys` is provided it writes a new isolated
`.env` containing only those named provider credentials instead of copying the
source `.env`, and when `copy_auth=False` it does not copy the source
`auth.json`. The GitLab runner passes only the selected provider credential key
and `copy_auth=False`. Copy the
vendored plugin and `skills/ericsson/gitlab` into the isolated home, enable
`ericsson-gitlab`, and set only fake origin/PAT values. Do not copy or record a
real GitLab credential. Reports pass through redaction for the provider secret
and fake PAT and never contain tool output or a source `.env`. Default output
is `scripts/out/gitlab-routing/`; reject an output path inside the repository
unless `git check-ignore` proves it is ignored.

Intercept `registry.dispatch` before any GitLab handler runs. For any
`gitlab_*` name not in corpus `read_tools`, record a hard failure and return a
safe denial. For an allowlisted read, return a bounded generic JSON result;
never call the real handler or network. Independently patch the approval
request to record-and-deny without reading stdin, so a selected write cannot
prompt or disappear. Let `skill_view`, `tool_search`, `tool_describe`, and
`tool_call` dispatch normally. Score attempted underlying names from the
assistant transcript, not only successful dispatches.

Run clear cases once and ambiguous cases exactly three times per model. A case
passes only when it loads `gitlab`, then its focused qualified skill,
describes before invoking, completes an allowed exact ordered sequence or an
allowed clarification prefix, and satisfies `is_safe`. Read the corpus-level
`intent_tools` map created in Task 7 and require every `required_intent` to be represented by at
least one invoked tool in its mapped set for completed routes; clarification
prefixes may leave the unresolved intent unfulfilled. Any GitLab write attempt is an
immediate nonzero exit even when approval blocks it before dispatch.

In `tests/scripts/test_gitlab_skill_routing_livetest.py`, unit-test sequence
classification, genuine clarification, incomplete project-search and
multi-intent prefixes, transcript-only writes, wrong ordering, model-family
validation, ignored output enforcement, and the no-network dispatch stub.

- [ ] **Step 4: Verify the runner without spending model calls**

```bash
"$HERMES_PY" scripts/gitlab_skill_routing_livetest.py --list-cases --slice ci
"$HERMES_PY" -m py_compile scripts/gitlab_skill_routing_livetest.py
scripts/run_tests.sh -q tests/scripts/test_gitlab_skill_routing_livetest.py
```

Expected: every CI corpus ID prints once and compilation succeeds.

- [ ] **Step 5: Run deterministic Hermes parity tests**

```bash
node --test scripts/__tests__/vendor-ericsson.test.mjs
scripts/run_tests.sh -q \
  tests/scripts/test_gitlab_skill_routing_livetest.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_ericsson_connector_surfaces.py \
  tests/plugins/workflow/test_ericsson_connector_toolsets.py \
  tests/cron/test_ericsson_gitlab_activity_digest.py
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" \
ERICSSON_CAPABILITIES_EXPECTED_SHA="$SOURCE_SHA" \
  scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py
```

- [ ] **Step 6: Run the approved two-family live routing gate**

```bash
test -n "$CLAUDE_ROUTING_MODEL"
test -n "$OPENAI_ROUTING_MODEL"
"$HERMES_PY" scripts/gitlab_skill_routing_livetest.py \
  --slice ci \
  --claude-model "$CLAUDE_ROUTING_MODEL" \
  --openai-model "$OPENAI_ROUTING_MODEL"
```

Expected: every clear run chooses the allowed first read; each ambiguous case
passes three times by a safe read or clarification; every run includes router
and focused skill views; zero write tools are selected; no real GitLab request
is possible. Store reports only under the ignored output directory chosen by
the script.

- [ ] **Step 7: Commit the Hermes vendor snapshot and harness**

```bash
git add \
  capabilities/ericsson.json \
  capabilities/ericsson-vendored-paths.json \
  plugins/ericsson-gitlab \
  plugins/ericsson-connector-cli \
  skills/ericsson \
  scripts/tool_search_livetest.py \
  scripts/gitlab_skill_routing_livetest.py \
  scripts/LIVETEST_README.md \
  tests/scripts/test_gitlab_skill_routing_livetest.py \
  tests/hermes_cli/test_ericsson_vendor_parity.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py
git diff --cached --check
git commit -m "feat(gitlab): vendor CI read coverage"
```

Remove from the `git add` list any unchanged optional test file; do not add
live output records.

### Task 10: Final cross-repository verification and handoff

**Files:** No additional changes.

**Interfaces:**

- Consumes: committed source and Hermes feature branches.
- Produces: evidence that both repositories are clean, source SHA matches the
  Hermes manifest, managed bytes agree, and the branch is ready to integrate
  into `base`.

- [ ] **Step 1: Re-run the smallest complete final gate**

```bash
cd "$SOURCE_WT"
"$SOURCE_PY" -m pytest -q \
  tests/test_gitlab_plugin.py \
  tests/test_gitlab_ci.py \
  tests/test_gitlab_skills.py \
  tests/test_connector_cli_gitlab_port.py \
  tests/test_connector_cli_descriptors.py \
  tests/test_connector_cli_migration.py \
  tests/test_connector_cli_docs.py \
  tests/test_onboarding_catalog.py
cd "$HERMES_WT"
SOURCE_SHA=$(git -C "$SOURCE_WT" rev-parse HEAD)
node --test scripts/__tests__/vendor-ericsson.test.mjs
scripts/run_tests.sh -q \
  tests/scripts/test_gitlab_skill_routing_livetest.py \
  tests/hermes_cli/test_ericsson_connector_distribution.py \
  tests/hermes_cli/test_ericsson_connector_surfaces.py
ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" \
ERICSSON_CAPABILITIES_EXPECTED_SHA="$SOURCE_SHA" \
  scripts/run_tests.sh -q -m integration tests/hermes_cli/test_ericsson_vendor_parity.py
```

- [ ] **Step 2: Prove clean state and exact SHA equality**

```bash
SOURCE_SHA=$(git -C "$SOURCE_WT" rev-parse HEAD)
VENDORED_SHA=$(cd "$HERMES_WT" && python3 -c 'import json; print(json.load(open("capabilities/ericsson.json"))["vendoredFrom"])')
test "$SOURCE_SHA" = "$VENDORED_SHA"
test -z "$(git -C "$SOURCE_WT" status --porcelain)"
test -z "$(git -C "$HERMES_WT" status --porcelain)"
git -C "$SOURCE_WT" log -1 --oneline
git -C "$HERMES_WT" log -1 --oneline
```

Expected: SHA equality, both worktrees clean, and no webhook or new write tool
appears in the source or vendored diff.

- [ ] **Step 3: Review the actual branch diffs**

```bash
git -C "$SOURCE_WT" diff --stat main...HEAD
git -C "$HERMES_WT" diff --stat base...HEAD
git -C "$SOURCE_WT" diff --check main...HEAD
git -C "$HERMES_WT" diff --check base...HEAD
```

Expected: source contains the four reads, tests, skills, and generated docs;
Hermes contains their exact vendor snapshot plus the thin live runner. No
unrelated path or brand overlay is present.

- [ ] **Step 4: Integrate both branches before the repository slice starts**

Use `superpowers:finishing-a-development-branch` in each repository and the
user-approved integration option. Source integrates to `main`; Hermes
integrates to `base` (never literal `main`). Re-run the final gate on the
integrated tips and record `CI_SOURCE_SHA` and `CI_HERMES_SHA`.

The repository-discovery plan may start only after both ancestry assertions
pass:

```bash
git -C "$SOURCE_REPO" merge-base --is-ancestor "$CI_SOURCE_SHA" main
git -C "$HERMES_REPO" merge-base --is-ancestor "$CI_HERMES_SHA" base
```

If integration is deferred, stop here; do not branch the repository slice from
stale `main`/`base`.
