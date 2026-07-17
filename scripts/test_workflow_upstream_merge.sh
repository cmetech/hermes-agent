#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
UPSTREAM_REF=""
BASE_REF=""
REPORT_DIR=""
BRAND_REFS=()
DECISIONS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --upstream-ref) UPSTREAM_REF="${2:-}"; shift 2 ;;
    --base-ref) BASE_REF="${2:-}"; shift 2 ;;
    --brand-ref) BRAND_REFS+=("${2:-}"); shift 2 ;;
    --report-dir) REPORT_DIR="${2:-}"; shift 2 ;;
    --decision) DECISIONS+=("${2:-}"); shift 2 ;;
    --repo) ROOT="$(cd "${2:-}" && pwd)"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$UPSTREAM_REF" && -n "$BASE_REF" && ${#BRAND_REFS[@]} -gt 0 ]] || {
  echo "required: --upstream-ref REF --base-ref REF --brand-ref REF [...]" >&2
  exit 2
}
REPORT_DIR="${REPORT_DIR:-$(mktemp -d)/workflow-merge-evidence}"
mkdir -p "$REPORT_DIR"
REPORT_DIR="$(cd "$REPORT_DIR" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CHECKER="$ROOT/scripts/check_upstream_customizations.py"
MANIFEST="$ROOT/docs/upstream-customizations/workflow-orchestration.yaml"
GATE="$ROOT/scripts/test_workflow_merge_gate.sh"
SCHEMA="$ROOT/docs/upstream-customizations/merge-evidence.schema.json"
for required in "$CHECKER" "$MANIFEST" "$GATE" "$SCHEMA"; do
  [[ -f "$required" ]] || { echo "partial workflow merge gate: missing $required" >&2; exit 1; }
done

cd "$ROOT"
UPSTREAM_SHA="$(git rev-parse "$UPSTREAM_REF^{commit}")"
BASE_SHA="$(git rev-parse "$BASE_REF^{commit}")"
BASELINE="$($PYTHON_BIN "$CHECKER" --manifest "$MANIFEST" --print-verified-upstream)"
for ref in "${BRAND_REFS[@]}"; do git rev-parse "$ref^{commit}" >/dev/null; done

OVERLAP="$REPORT_DIR/overlap.json"
set +e
"$PYTHON_BIN" "$CHECKER" --manifest "$MANIFEST" \
  --upstream-diff "$BASELINE..$UPSTREAM_SHA" --report "$OVERLAP"
OVERLAP_STATUS=$?
set -e
if [[ $OVERLAP_STATUS -eq 2 ]]; then
  while IFS= read -r required_id; do
    [[ -n "$required_id" ]] || continue
    matched=0
    for decision in "${DECISIONS[@]}"; do
      if [[ "$decision" == "$required_id="* ]]; then
        value="${decision#*=}"
        [[ "$value" == "preserve" || "$value" == "adapt" || "$value" == "remove-as-upstream-equivalent" ]] || {
          echo "invalid decision for $required_id" >&2
          exit 2
        }
        matched=1
      fi
    done
    [[ $matched -eq 1 ]] || {
      echo "explicit preserve/adapt/remove-as-upstream-equivalent decision required for $required_id" >&2
      exit 4
    }
  done < <("$PYTHON_BIN" -c 'import json,sys; d=json.load(open(sys.argv[1])); print("\n".join(x["id"] for x in d["overlaps"] if x["classification"] in {"owned_symbol","possible_upstream_equivalent"}))' "$OVERLAP")
elif [[ $OVERLAP_STATUS -ne 0 ]]; then
  exit "$OVERLAP_STATUS"
fi

TMP="$(mktemp -d)"
WORKTREES=()
COMMANDS_TSV="$REPORT_DIR/commands.tsv"
: >"$COMMANDS_TSV"
PLATFORM="$(uname -s)-$(uname -m)"
now_ms() { "$PYTHON_BIN" -c 'import time; print(time.monotonic_ns() // 1_000_000)'; }
record_command() {
  local name="$1" result="$2" started="$3" finished
  finished="$(now_ms)"
  printf '%s\t%s\t%s\t%s\n' "$name" "$result" "$((finished - started))" "$PLATFORM" >>"$COMMANDS_TSV"
}
cleanup() {
  for worktree in "${WORKTREES[@]}"; do
    git -C "$ROOT" worktree remove --force "$worktree" >/dev/null 2>&1 || true
  done
  find "$TMP" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT

BASE_WT="$TMP/base"
git worktree add --detach "$BASE_WT" "$BASE_SHA" >/dev/null
WORKTREES+=("$BASE_WT")
started="$(now_ms)"
set +e
git -C "$BASE_WT" merge --no-edit "$UPSTREAM_SHA" >"$REPORT_DIR/base-merge.log" 2>&1
MERGE_STATUS=$?
set -e
if [[ $MERGE_STATUS -ne 0 ]]; then
  record_command "merge-upstream-into-base" "failed" "$started"
  git -C "$BASE_WT" diff --name-only --diff-filter=U >"$REPORT_DIR/conflict-files.txt" || true
  git -C "$BASE_WT" merge --abort >/dev/null 2>&1 || true
  echo "upstream merge requires explicit conflict resolution; whole-file ours/theirs is forbidden" >&2
  exit 5
fi
record_command "merge-upstream-into-base" "passed" "$started"
TESTED_BASE_SHA="$(git -C "$BASE_WT" rev-parse HEAD)"
BASE_TREE="$(git -C "$BASE_WT" rev-parse HEAD^{tree})"

started="$(now_ms)"
set +e
PYTHON_BIN="$PYTHON_BIN" "$BASE_WT/scripts/test_workflow_merge_gate.sh" \
  --repo "$BASE_WT" --phase base >"$REPORT_DIR/base-gate.log" 2>&1
GATE_STATUS=$?
set -e
if [[ $GATE_STATUS -ne 0 ]]; then
  record_command "base-invariant-gate" "failed" "$started"
  echo "temporary base invariant gate failed; no refs were advanced" >&2
  exit 6
fi
record_command "base-invariant-gate" "passed" "$started"

BRANDS_TSV="$REPORT_DIR/brands.tsv"
: >"$BRANDS_TSV"
for ref in "${BRAND_REFS[@]}"; do
  slug="${ref##*/}"
  worktree="$TMP/brand-$slug"
  git worktree add --detach "$worktree" "$ref" >/dev/null
  WORKTREES+=("$worktree")

  started="$(now_ms)"
  set +e
  git -C "$worktree" merge --no-edit "$TESTED_BASE_SHA" >"$REPORT_DIR/$slug-merge.log" 2>&1
  BRAND_MERGE_STATUS=$?
  set -e
  if [[ $BRAND_MERGE_STATUS -ne 0 ]]; then
    record_command "merge-tested-base-into-$slug" "failed" "$started"
    git -C "$worktree" diff --name-only --diff-filter=U >"$REPORT_DIR/$slug-conflict-files.txt" || true
    git -C "$worktree" merge --abort >/dev/null 2>&1 || true
    echo "brand $slug merge failed; no refs were advanced" >&2
    exit 7
  fi
  record_command "merge-tested-base-into-$slug" "passed" "$started"

  started="$(now_ms)"
  node "$worktree/scripts/brand/generate.mjs" "$slug" --write >"$REPORT_DIR/$slug-brand.log" 2>&1
  record_command "generate-$slug-overlay" "passed" "$started"
  if ! git -C "$worktree" diff --quiet; then
    git -C "$worktree" add -A
    git -C "$worktree" -c user.name='Workflow Gate' -c user.email='workflow-gate@localhost' \
      commit -m "test(brand): rehearse $slug overlay" >/dev/null
  fi

  started="$(now_ms)"
  set +e
  PYTHON_BIN="$PYTHON_BIN" "$worktree/scripts/test_workflow_merge_gate.sh" \
    --repo "$worktree" --phase brand --brand "$slug" \
    --tested-base-sha "$TESTED_BASE_SHA" >"$REPORT_DIR/$slug-gate.log" 2>&1
  BRAND_GATE_STATUS=$?
  set -e
  if [[ $BRAND_GATE_STATUS -ne 0 ]]; then
    record_command "brand-$slug-invariant-gate" "failed" "$started"
    echo "brand $slug invariant gate failed; no refs were advanced" >&2
    exit 8
  fi
  record_command "brand-$slug-invariant-gate" "passed" "$started"

  brand_sha="$(git -C "$worktree" rev-parse HEAD)"
  brand_tree="$(git -C "$worktree" rev-parse HEAD^{tree})"
  descriptor_sha="$($PYTHON_BIN -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$worktree/brands/$slug.json")"
  printf '%s\t%s\t%s\t%s\n' "$ref" "$brand_sha" "$brand_tree" "$descriptor_sha" >>"$BRANDS_TSV"
done

PATCH_SHA="$($PYTHON_BIN -c 'import hashlib,subprocess,sys; d=subprocess.check_output(["git","-C",sys.argv[1],"diff","--binary",sys.argv[2],sys.argv[3]]); print(hashlib.sha256(d).hexdigest())' "$ROOT" "$BASE_SHA" "$TESTED_BASE_SHA")"
DECISIONS_FILE="$REPORT_DIR/decisions.txt"
printf '%s\n' "${DECISIONS[@]}" >"$DECISIONS_FILE"
MERGE_SKILL_PATH="$ROOT/../.claude/skills/otto-upstream-merge/SKILL.md"
if [[ -f "$MERGE_SKILL_PATH" ]]; then
  MERGE_SKILL_LABEL="../.claude/skills/otto-upstream-merge/SKILL.md"
  MERGE_SKILL_SHA="$($PYTHON_BIN -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$MERGE_SKILL_PATH")"
else
  MERGE_SKILL_LABEL="external-skill-not-installed-in-fixture"
  MERGE_SKILL_SHA="$($PYTHON_BIN -c 'import hashlib; print(hashlib.sha256(b"").hexdigest())')"
fi

"$PYTHON_BIN" - "$MANIFEST" "$OVERLAP" "$COMMANDS_TSV" "$BRANDS_TSV" \
  "$DECISIONS_FILE" "$ROOT" "$BASE_SHA" "$TESTED_BASE_SHA" "$REPORT_DIR/merge-evidence.json" \
  "$BASELINE" "$UPSTREAM_SHA" "$BASE_TREE" "$PATCH_SHA" "$PLATFORM" \
  "$MERGE_SKILL_LABEL" "$MERGE_SKILL_SHA" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

(
    manifest_path, overlap_path, commands_path, brands_path, decisions_path,
    repo, base_sha, tested_base_sha, output_path, baseline, upstream_sha,
    base_tree, patch_sha, platform, merge_skill_path, merge_skill_sha,
) = sys.argv[1:]
manifest_bytes = Path(manifest_path).read_bytes()
manifest = yaml.safe_load(manifest_bytes)
overlaps = {
    item["id"]: item
    for item in json.loads(Path(overlap_path).read_text(encoding="utf-8"))["overlaps"]
}
decisions = {}
for line in Path(decisions_path).read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        decisions[key] = value

commands = []
for line in Path(commands_path).read_text(encoding="utf-8").splitlines():
    if not line:
        continue
    name, result, duration, command_platform = line.split("\t")
    commands.append({
        "name": name,
        "result": result,
        "duration_ms": int(duration),
        "platform": command_platform,
    })
base_gate = next(item for item in commands if item["name"] == "base-invariant-gate")

def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()

entries = []
for entry in manifest["upstream_changes"]:
    patch = subprocess.check_output(
        ["git", "-C", repo, "diff", "--binary", base_sha, tested_base_sha, "--", *entry["files"]]
    )
    expected = entry["expected_commit_subject"]
    before_subjects = git("log", "--format=%s", base_sha, "--", *entry["files"]).splitlines()
    after_subjects = git("log", "--format=%s", tested_base_sha, "--", *entry["files"]).splitlines()
    retained = [expected] if expected in after_subjects else []
    removed = [expected] if expected in before_subjects and expected not in after_subjects else []
    overlap = overlaps[entry["id"]]
    entries.append({
        "id": entry["id"],
        "baseline": entry["last_verified_upstream"],
        "files": entry["files"],
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "overlap_class": overlap["classification"],
        "decision": decisions.get(entry["id"], "not-required"),
        "conflict_files": [],
        "retained_commit_subjects": retained,
        "removed_commit_subjects": removed,
        "tests": [{**base_gate, "name": "entry invariants: " + ", ".join(entry["tests"])}],
    })

brands = []
for line in Path(brands_path).read_text(encoding="utf-8").splitlines():
    ref, commit, tree, descriptor = line.split("\t")
    brands.append({
        "ref": ref,
        "commit": commit,
        "tree": tree,
        "descriptor_sha256": descriptor,
        "contains_tested_base": True,
        "generic_runtime_matches_base": True,
    })

evidence = {
    "schema_version": 1,
    "prior_upstream_commit": baseline,
    "candidate_upstream_commit": upstream_sha,
    "pre_base_commit": base_sha,
    "post_base_commit": tested_base_sha,
    "tested_base_tree": base_tree,
    "ledger_baseline": baseline,
    "ledger_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    "patch_sha256": patch_sha,
    "merge_skill": {
        "path": merge_skill_path,
        "sha256": merge_skill_sha,
        "owner_commit": None,
    },
    "entries": entries,
    "commands": commands,
    "platform": platform,
    "brands": brands,
    "final_ancestry": True,
}
Path(output_path).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

"$PYTHON_BIN" -c 'import json,sys; from jsonschema import validate; validate(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])))' \
  "$REPORT_DIR/merge-evidence.json" "$SCHEMA"
echo "$REPORT_DIR/merge-evidence.json"
