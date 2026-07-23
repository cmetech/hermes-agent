# Task 2 Report — B-1

## Result

Kept `WorkflowRunnerBinding` frozen, slotted, and server-owned while adding one
optional runtime-capability provider. Production bindings retain their initial
runtime snapshot for inspection but re-read and purely classify current raw
config whenever `execution_context()` is consumed. Injected bindings without a
provider retain immutable snapshot behavior; runners and runner capabilities
remain fixed.

## RED evidence

Added three regression tests before production edits:

- `test_production_binding_refreshes_runtime_capabilities_per_context`
- `test_same_binding_refreshes_runtime_before_scheduled_admission`
- `test_same_binding_rejects_runtime_change_after_scheduled_admission`

Command:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_runner_binding.py::test_production_binding_refreshes_runtime_capabilities_per_context \
  tests/plugins/workflow/test_schedule_revalidation.py::test_same_binding_refreshes_runtime_before_scheduled_admission \
  tests/plugins/workflow/test_schedule_revalidation.py::test_same_binding_rejects_runtime_change_after_scheduled_admission
```

RED result: expected `3 failed in 0.66s`.

- The binding-refresh test received stale `chat_completions` after config
  changed to the Hermes-managed `anthropic_messages` runtime.
- The pre-admission test persisted config A instead of current config B.
- The post-admission test incorrectly succeeded after current config changed to
  `codex_app_server`; the stale binding passed fire-time revalidation and reached
  the trapped real runner.

## GREEN evidence

Extracted config-to-runtime classification into the private zero-argument
`_production_runtime_capabilities()` function. Production construction stores
its first result in `runtime_capabilities` and installs the same function as the
optional provider. The private binding helper consults the provider only while
deriving an execution context and otherwise returns the stored snapshot.

The same three-test command passed: `3 passed in 0.50s`.

The pre-admission test proves one same-term binding reads config B before
admission, persists B, revalidates with B at fire time, and succeeds without a
spurious terminal failure. The post-admission test proves changing that same
binding's current config to `codex_app_server` terminalizes with
`schedule_revalidation_failed`, zero claims, and zero trapped real-runner or
provider requests.

## Files changed

- `plugins/workflow/runner_binding.py`
- `tests/plugins/workflow/test_runner_binding.py`
- `tests/plugins/workflow/test_schedule_revalidation.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-2-report.md`

## Verification

- Focused runtime/scheduling/E2E suite:
  `.venv/bin/python -m pytest -q tests/plugins/workflow/test_runner_binding.py
  tests/plugins/workflow/test_schedule_revalidation.py
  tests/plugins/workflow/test_ai_extensions_middleware_e2e.py` —
  `83 passed in 8.53s`.
- Ruff:
  `.venv/bin/python -m ruff check plugins/workflow/runner_binding.py
  tests/plugins/workflow/test_runner_binding.py
  tests/plugins/workflow/test_schedule_revalidation.py` —
  `All checks passed!`.
- Existing consumers:
  `rg -n "background_execution_context\\(|scheduled_execution_context\\(|runner_binding\\.execution_context\\("
  plugins/workflow/api_admission.py plugins/workflow/scheduler.py
  plugins/workflow/scheduled_revalidation.py` confirms admission derives via
  `background_execution_context()`, fire time derives via
  `scheduled_execution_context()` to `binding.execution_context()`, and claim
  time calls `runner_binding.execution_context()` directly.
- Customization ledger:
  `.venv/bin/python scripts/check_upstream_customizations.py --manifest
  docs/upstream-customizations/workflow-orchestration.yaml --diff
  26385c6c5bf7c6c006adbf80dfb50dad336a052b` — passed with exit code 0.
- `git diff --check
  26385c6c5bf7c6c006adbf80dfb50dad336a052b..HEAD` and
  `git show --check --stat --oneline HEAD` — passed after the atomic commit.

## Self-review

- The dataclass remains `@dataclass(frozen=True, slots=True)` and the new
  optional provider is its final defaulted field.
- Real and deterministic runner objects and both runner-capability records are
  constructed once and never refreshed.
- Runtime classification reads only current raw config plus configured provider
  metadata and calls the existing pure classifier. It performs no credential,
  provider-resolution, model, MCP, request, or network work.
- No config watcher, coordinator restart, request parameter, caller authority,
  core change, or alternate call-site seam was added.
- Both owning customization entries describe immutable binding identity with
  per-context production runtime refresh.

## Concerns

The required customization check against task base
`26385c6c5bf7c6c006adbf80dfb50dad336a052b` initially exposed that the prior
atomic task commit added `.superpowers/sdd/task-1-report.md` without listing it
in its owning customization entry. This task adds that existing report path to
`workflow-scheduled-queued-consumer-isolation`; no content in the prior report
was touched.
