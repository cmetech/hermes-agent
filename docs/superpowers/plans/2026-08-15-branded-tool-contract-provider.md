# Branded Tool Contract Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow generated branded OTTO Gateway providers to participate in the explicit v1 tool contract without weakening direct-provider isolation.

**Architecture:** Add an empty-by-default contract-version declaration to `ProviderProfile`, emit exact v1 from the brand provider generator, and resolve that declaration at the existing request-header trust gate. Canonical OTTO names retain compatibility.

**Tech Stack:** Python, pytest, Node.js built-in test runner, Git, GitHub Actions.

## Global Constraints

- Preserve exact `X-Otto-Tool-Contract: v1` and `X-Otto-Call-Role` semantics.
- Direct or undeclared providers must not receive OTTO headers.
- Do not infer trust from URL, port, model name, prompt text, or active brand.
- Do not change prompt bytes, messages, tool schemas, tool authorization, or fallback behavior.
- Use `scripts/run_tests.sh`, exact-file staging, atomic commits, and paired brand release gates.

---

### Task 1: Declare and consume branded Gateway contract capability

**Files:**
- Modify: `providers/base.py`
- Modify: `scripts/brand/emitters/provider.mjs`
- Modify: `agent/otto_tool_contract.py`
- Test: `tests/agent/test_otto_tool_contract.py`
- Test: `scripts/brand/__tests__/provider.test.mjs`

**Interfaces:**
- Produces: `ProviderProfile.otto_tool_contract_version: str`, default `""`.
- Consumes: `providers.get_provider_profile(provider)` at the existing header trust gate.

- [ ] **Step 1: Write failing behavior tests**

Register a branded provider profile declaring `otto_tool_contract_version="v1"`
and assert `add_otto_request_headers` returns the exact static v1 headers. Assert
undeclared and non-v1 declarations still raise `OttoToolContractError`. Extend
the generator behavior test so both OTTO and LOOP24 generated profiles declare
v1.

- [ ] **Step 2: Run RED tests**

Run:
`scripts/run_tests.sh tests/agent/test_otto_tool_contract.py -k branded -q`
and
`node --test scripts/brand/__tests__/provider.test.mjs`.

Expected: failures because `ProviderProfile` does not accept the declaration
and generated profiles do not emit it.

- [ ] **Step 3: Implement the minimum production change**

Add the defaulted profile field, emit `otto_tool_contract_version="v1"`, and
replace the fixed-only provider decision with exact canonical-name or exact
profile-declaration validation.

- [ ] **Step 4: Run GREEN and adjacent tests**

Run the two RED commands again, then:
`scripts/run_tests.sh tests/agent/test_otto_tool_contract.py tests/agent/test_tool_choice_lifecycle.py tests/agent/test_tool_choice_policy.py -q`.

- [ ] **Step 5: Inspect and commit**

Run `git diff`, `git diff --check`, stage only the five task files, and commit
`fix(contract): trust generated gateway provider profiles`.

### Task 2: Verify, integrate, and release both brands

**Files:**
- No additional production files.
- Generated brand overlays change only on their respective brand branches.

**Interfaces:**
- Consumes: the exact tested `base` SHA from Task 1.
- Produces: immutable OTTO and LOOP24 source SHAs used by v5.8.2 workflows.

- [ ] **Step 1: Verify the feature branch**

Run focused contract, transport, lifecycle, concurrency, prompt-cache, full
Python, compileall, brand generator, Desktop typecheck/test/build, diff-check,
and status gates required by the repository release workflow.

- [ ] **Step 2: Merge and verify `base`**

Merge the feature branch into `base`, rerun the merged-result release gate,
and push `base` forward-only.

- [ ] **Step 3: Generate and verify every brand**

For each descriptor in `brands/*.json`, merge the tested base SHA into its
brand branch, run `scripts/brand/generate.mjs <brand> --write`, commit generated
changes if any, and run the brand workflow merge gate.

- [ ] **Step 4: Push and publish v5.8.2**

Push exact OTTO and LOOP24 SHAs. Dispatch each releases-only repository's
`release.yml` with its immutable SHA, matching stamp branch, version `5.8.2`,
and `prerelease=false`. Watch both exact runs to completion and verify release
source stamps and complete Windows/macOS asset sets.

- [ ] **Step 5: Restore checkout**

Switch the main checkout to `base`, verify `git branch --show-current` is
`base`, and report commits, test evidence, workflow URLs, release assets, and
the preserved unrelated working-tree state.
