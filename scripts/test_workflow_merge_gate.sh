#!/usr/bin/env bash
set -euo pipefail

INVOCATION_ROOT="$(pwd -P)"
ROOT="$(git rev-parse --show-toplevel)"
PHASE="base"
BRAND=""
TESTED_BASE_SHA=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) PHASE="${2:-}"; shift 2 ;;
    --brand) BRAND="${2:-}"; shift 2 ;;
    --tested-base-sha) TESTED_BASE_SHA="${2:-}"; shift 2 ;;
    --repo) ROOT="$(cd "${2:-}" && pwd -P)"; shift 2 ;;
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
if [[ -n "${PYTHON_BIN:-}" ]]; then
  :
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/venv/bin/python"
else
  PYTHON_BIN="python3"
fi
case "$PYTHON_BIN" in
  /*) ;;
  */*) PYTHON_BIN="$INVOCATION_ROOT/$PYTHON_BIN" ;;
  *) PYTHON_BIN="$(command -v "$PYTHON_BIN")" ;;
esac
[[ -x "$PYTHON_BIN" ]] || { echo "python interpreter is not executable: $PYTHON_BIN" >&2; exit 1; }

_require_root_dependencies() {
  local shared_git_dir shared_root resolved_modules node_bin actual_versions
  shared_git_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
  shared_root="$(cd "$(dirname "$shared_git_dir")" && pwd -P)"
  if [[ -L "$ROOT/node_modules" && ! -e "$ROOT/node_modules" ]]; then
    echo "root parser dependencies contain a dangling local dependency link" >&2
    return 1
  fi
  if [[ ! -e "$ROOT/node_modules" && -d "$shared_root/node_modules" ]]; then
    ln -s "$shared_root/node_modules" "$ROOT/node_modules" 2>/dev/null || {
      echo "root parser dependencies could not create the bounded dependency view" >&2
      return 1
    }
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
  node_bin="$(command -v node || true)"
  [[ -n "$node_bin" ]] || {
    echo "root parser dependencies require node before ledger validation" >&2
    return 1
  }
  actual_versions="$(cd "$ROOT/scripts" && "$node_bin" \
    --experimental-import-meta-resolve --input-type=module -e '
    import fs from "node:fs";
    import path from "node:path";
    import { createRequire } from "node:module";
    import { fileURLToPath } from "node:url";
    const names = ["typescript", "unified", "remark-parse", "micromark"];
    const directImports = new Set(["typescript", "unified", "remark-parse"]);
    const helperUrl = new URL("./extract_non_python_symbols.mjs", import.meta.url);
    const helperRequire = createRequire(helperUrl);
    const allowedRoots = process.argv.slice(1)
      .filter(candidate => {
        try {
          return fs.statSync(candidate).isDirectory();
        } catch {
          return false;
        }
      })
      .map(candidate => fs.realpathSync(candidate));
    const isStrictlyWithin = (candidate, root) => {
      const relative = path.relative(root, candidate);
      return relative !== "" && relative !== ".." &&
        !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
    };
    const requireContainedFile = (candidate, packageRoot) => {
      const resolved = fs.realpathSync(candidate);
      if (!fs.statSync(resolved).isFile() ||
          !allowedRoots.some(root => isStrictlyWithin(resolved, root)) ||
          !isStrictlyWithin(resolved, packageRoot)) {
        throw new Error("dependency file escapes its package root");
      }
      return resolved;
    };
    const resolvedPackage = name => {
      const requireEntrypoint = fs.realpathSync(helperRequire.resolve(name));
      let directory = path.dirname(requireEntrypoint);
      while (true) {
        try {
          const manifestPath = fs.realpathSync(path.join(directory, "package.json"));
          const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
          if (manifest.name === name) {
            return {
              packageRoot: fs.realpathSync(directory),
              manifest,
              manifestPath,
              requireEntrypoint,
            };
          }
        } catch {
          // Match the helper: keep walking toward the package root.
        }
        const parent = path.dirname(directory);
        if (parent === directory) {
          throw new Error("dependency package manifest was not found");
        }
        directory = parent;
      }
    };
    const versions = names.map(name => {
      const {
        packageRoot,
        manifest,
        manifestPath,
        requireEntrypoint,
      } = resolvedPackage(name);
      if (!fs.statSync(packageRoot).isDirectory() ||
          !allowedRoots.some(root => isStrictlyWithin(packageRoot, root))) {
        throw new Error("dependency package escapes the allowed roots");
      }
      requireContainedFile(manifestPath, packageRoot);
      requireContainedFile(requireEntrypoint, packageRoot);
      if (directImports.has(name)) {
        const importEntrypoint = fileURLToPath(import.meta.resolve(name));
        requireContainedFile(importEntrypoint, packageRoot);
      }
      return manifest.version;
    });
    process.stdout.write(versions.join(" "));
  ' "$resolved_modules" "$ROOT/node_modules" "$shared_root/node_modules" 2>/dev/null)" || {
    echo "root parser dependencies are unreadable or escape the allowed roots" >&2
    return 1
  }
  [[ "$actual_versions" == "6.0.3 11.0.5 11.0.0 4.0.2" ]] || {
    echo "root parser dependencies do not match the lockfile versions" >&2
    return 1
  }
}

cd "$ROOT"
if [[ "$PHASE" == "base" ]] && [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "tracked working tree is dirty; refusing to seal TESTED_BASE_SHA" >&2
  exit 1
fi
_require_root_dependencies
"$PYTHON_BIN" "$CHECKER" --manifest "$MANIFEST"

if [[ "$PHASE" == "base" ]]; then
  if [[ "${WORKFLOW_MERGE_GATE_FAST:-0}" != "1" ]]; then
    if [[ ! -e "$ROOT/.venv" ]]; then
      SHARED_VENV="$(cd "$(dirname "$PYTHON_BIN")/.." && pwd -P)"
      if [[ -f "$SHARED_VENV/bin/activate" ]]; then
        ln -s "$SHARED_VENV" "$ROOT/.venv"
      fi
    fi
    HERMES_PYTHON="$PYTHON_BIN" "$ROOT/scripts/run_tests.sh" \
      tests/tools/test_managed_process.py tests/tools/test_process_registry.py \
      tests/agent/test_plugin_agent.py tests/tools/test_registry.py \
      tests/hermes_cli/test_execution_runtime_capabilities.py \
      tests/hermes_cli/test_kanban_mutation_preconditions.py \
      tests/hermes_cli/test_kanban_db.py \
      tests/hermes_cli/test_kanban_reclaim_claim_lock_guard.py \
      tests/hermes_cli/test_kanban_dispatch_lock.py \
      tests/plugins/test_kanban_dashboard_plugin.py \
      tests/gateway/test_plugin_background_services.py \
      tests/gateway/test_plugin_delivery.py \
      tests/hermes_cli/test_plugin_provider_hot_reload.py \
      tests/scripts/test_workflow_merge_gate.py \
      tests/plugins/workflow/test_language.py \
      tests/plugins/workflow/test_language_snapshot.py \
      tests/plugins/workflow/test_language_schema.py \
      tests/plugins/workflow/test_structured_output_language.py \
      tests/plugins/workflow/test_admission.py \
      tests/plugins/workflow/test_schedule_store_identity.py \
      tests/plugins/workflow/test_scheduled_runs.py \
      tests/plugins/workflow/test_schedule_revalidation.py \
      tests/plugins/workflow/test_ai_entitlement.py \
      tests/plugins/workflow/test_node_mcp.py \
      tests/plugins/workflow/test_trust_policy.py \
      tests/plugins/workflow/test_runner_binding.py \
      tests/plugins/workflow/test_typed_publication.py \
      tests/plugins/workflow/test_typed_publication_recovery.py \
      tests/plugins/workflow/test_catalog_api.py \
      tests/plugins/workflow/test_workflow_detail_api.py \
      tests/plugins/workflow/test_workflow_catalog_desktop_e2e.py \
      tests/plugins/workflow/test_workflow_language_desktop_e2e.py \
      tests/plugins/workflow/test_workflow_showcase_desktop_e2e.py \
      tests/plugins/workflow/test_laptop_diagnostic_middleware_e2e.py \
      tests/plugins/workflow/test_ai_extensions_middleware_e2e.py \
      tests/plugins/workflow/test_scheduling_middleware_e2e.py \
      tests/plugins/workflow/test_showcase_catalog.py \
      tests/plugins/workflow/test_showcase_ai_e2e.py \
      tests/plugins/workflow/test_showcase_schedule_e2e.py \
      tests/plugins/workflow/test_showcase_evidence.py \
      tests/plugins/workflow/test_showcase_distribution_e2e.py \
      tests/plugins/workflow/test_portable_compatibility_e2e.py \
      tests/plugins/workflow/test_journal_reserve_fanout.py \
      tests/plugins/workflow/test_quarantine_replace_retry.py \
      tests/hermes_cli/test_capability_staging.py \
      tests/hermes_cli/test_baked_seed.py \
      tests/test_packaging_metadata.py -q
    HERMES_PYTHON="$PYTHON_BIN" "$ROOT/scripts/run_tests.sh" \
      tests/plugins/workflow/test_installed_distribution_e2e.py -q -m integration
    SHARED_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
    SHARED_ROOT="$(dirname "$SHARED_GIT_DIR")"
    if [[ ! -d node_modules && -d "$SHARED_ROOT/node_modules" ]]; then
      ln -s "$SHARED_ROOT/node_modules" node_modules
    fi
    if [[ ! -d apps/desktop/node_modules && -d "$SHARED_ROOT/apps/desktop/node_modules" ]]; then
      ln -s "$SHARED_ROOT/apps/desktop/node_modules" apps/desktop/node_modules
    fi
    [[ -d node_modules ]] || {
      echo "workspace dependencies are required for the base merge gate" >&2
      exit 1
    }
    [[ -d apps/desktop/node_modules ]] || {
      echo "desktop dependencies are required for the base merge gate" >&2
      exit 1
    }
    (cd apps/desktop && npx vitest run \
      src/components/activity-board/activity-board.test.tsx \
      src/components/activity-board/activity-board.performance.test.tsx \
      src/components/assistant-ui/embeds/workflow-topology.test.tsx \
      src/app/workflows/adapter.test.ts \
      src/app/workflows/catalog-run-policy.test.ts \
      src/app/workflows/index.test.tsx \
      src/app/workflows/review-run-dialog.test.tsx \
      src/app/workflows/view-workflow-dialog.test.tsx \
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
catalog = load_showcase_catalog()
assert set(catalog) == {
    "ai-extensions",
    "approval-gate",
    "laptop-diagnostic",
    "resilience",
    "scheduling",
}
assert all(item.package_digest for item in catalog.values())
assert all(item.verified_bundled_provenance for item in catalog.values())
PY
fi
echo "TESTED_BRAND_SHA=$(git rev-parse HEAD)"
