#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PHASE=""
BRAND=""
TESTED_BASE_SHA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) PHASE="${2:-}"; shift 2 ;;
    --brand) BRAND="${2:-}"; shift 2 ;;
    --tested-base-sha) TESTED_BASE_SHA="${2:-}"; shift 2 ;;
    --repo) ROOT="$(cd "${2:-}" && pwd)"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

MANIFEST="$ROOT/docs/upstream-customizations/workflow-orchestration.yaml"
CHECKER="$ROOT/scripts/check_upstream_customizations.py"
REHEARSAL="$ROOT/scripts/test_workflow_upstream_merge.sh"
EVIDENCE_SCHEMA="$ROOT/docs/upstream-customizations/merge-evidence.schema.json"

if [[ ! -f "$MANIFEST" && ! -f "$CHECKER" && ! -f "$EVIDENCE_SCHEMA" ]]; then
  echo "workflow merge gate is not installed" >&2
  exit 3
fi
for required in "$MANIFEST" "$CHECKER" "$REHEARSAL" "$EVIDENCE_SCHEMA"; do
  [[ -f "$required" ]] || { echo "partial workflow merge gate: missing $required" >&2; exit 1; }
done
[[ "$PHASE" == "base" || "$PHASE" == "brand" ]] || { echo "--phase must be base or brand" >&2; exit 2; }
if [[ "$PHASE" == "brand" ]]; then
  [[ -n "$BRAND" ]] || { echo "brand phase requires --brand" >&2; exit 2; }
  [[ -f "$ROOT/brands/$BRAND.json" ]] || { echo "unknown brand: $BRAND" >&2; exit 2; }
fi

export OPENROUTER_API_KEY="" OPENAI_API_KEY="" NOUS_API_KEY=""
export HERMES_OFFLINE=1
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT"
"$PYTHON_BIN" "$CHECKER" --manifest "$MANIFEST"

if [[ "$PHASE" == "base" ]]; then
  if [[ "${WORKFLOW_MERGE_GATE_FAST:-0}" != "1" ]]; then
    "$PYTHON_BIN" -m pytest -q \
      tests/tools/test_managed_process.py tests/tools/test_process_registry.py \
      tests/agent/test_plugin_agent.py tests/tools/test_registry.py \
      tests/hermes_cli/test_kanban_mutation_preconditions.py \
      tests/hermes_cli/test_kanban_db.py \
      tests/hermes_cli/test_kanban_reclaim_claim_lock_guard.py \
      tests/hermes_cli/test_kanban_dispatch_lock.py \
      tests/plugins/test_kanban_dashboard_plugin.py \
      tests/plugins/workflow/test_admission.py tests/plugins/workflow/test_trust_policy.py \
      tests/plugins/workflow/test_showcase_catalog.py \
      tests/plugins/workflow/test_showcase_distribution_e2e.py \
      tests/plugins/workflow/test_portable_compatibility_e2e.py \
      tests/hermes_cli/test_capability_staging.py \
      tests/hermes_cli/test_baked_seed.py \
      tests/test_packaging_metadata.py
    SHARED_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
    SHARED_ROOT="$(dirname "$SHARED_GIT_DIR")"
    if [[ ! -d apps/desktop/node_modules && -d "$SHARED_ROOT/apps/desktop/node_modules" ]]; then
      ln -s "$SHARED_ROOT/apps/desktop/node_modules" apps/desktop/node_modules
    fi
    [[ -d apps/desktop/node_modules ]] || {
      echo "desktop dependencies are required for the base merge gate" >&2
      exit 1
    }
    (cd apps/desktop && npx vitest run \
      src/components/activity-board/activity-board.test.tsx \
      src/components/activity-board/activity-board.performance.test.tsx \
      src/app/workflows/adapter.test.ts \
      src/app/workflows/workflow-operations.e2e.test.tsx \
      src/app/kanban/adapter.test.ts \
      src/app/kanban/kanban-operations.e2e.test.tsx)
    (cd apps/desktop && npx tsc -p . --noEmit)
  fi
  echo "TESTED_BASE_SHA=$(git rev-parse HEAD)"
  exit 0
fi

[[ -n "$TESTED_BASE_SHA" ]] || TESTED_BASE_SHA="$(git rev-parse base 2>/dev/null || true)"
git cat-file -e "$TESTED_BASE_SHA^{commit}" 2>/dev/null || { echo "invalid tested base commit" >&2; exit 1; }
git merge-base --is-ancestor "$TESTED_BASE_SHA" HEAD || { echo "brand does not contain tested base $TESTED_BASE_SHA" >&2; exit 1; }

GENERIC_PATHS=(
  tools/managed_process.py tools/process_registry.py
  agent/plugin_agent.py agent/plugin_agent_worker.py
  hermes_cli/kanban_db.py plugins/kanban/dashboard/plugin_api.py
  plugins/workflow
)
git diff --quiet "$TESTED_BASE_SHA" -- "${GENERIC_PATHS[@]}" || {
  echo "brand diverges from tested generic workflow/Kanban runtime" >&2
  exit 1
}

if [[ "${WORKFLOW_MERGE_GATE_FAST:-0}" != "1" ]]; then
  node scripts/brand/generate.mjs "$BRAND" --check
  "$PYTHON_BIN" - <<'PY'
from plugins.workflow.showcase import load_showcase_catalog
assert len(load_showcase_catalog()) == 4
PY
fi
echo "TESTED_BRAND_SHA=$(git rev-parse HEAD)"
