#!/usr/bin/env python3
"""Live, fake-GitLab routing gate for the vendored Ericsson CI corpus."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

WORKTREE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKTREE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tool_search_livetest as base


CORPUS = WORKTREE_ROOT / "plugins/ericsson-gitlab/routing_cases.json"
CORPUS_DATA = json.loads(CORPUS.read_text(encoding="utf-8"))
_TRACE_NAMES = {"skill_view", "tool_search", "tool_describe", "tool_call"}
_FAKE_PAT = "gitlab-routing-fake-pat"


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


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
    return raw if isinstance(raw, dict) else {}


def extract_gitlab_transcript(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return relevant assistant calls in wire order, including blocked writes."""
    out = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str):
                continue
            args = _arguments(function.get("arguments") or "{}")
            out.append({"name": name, "args": args})
    return out


def attempted_gitlab_names(transcript: list[dict[str, Any]]) -> list[str]:
    names = []
    for call in transcript:
        name = call["name"]
        if name.startswith("gitlab_"):
            names.append(name)
        elif name == "tool_call":
            underlying = call["args"].get("name")
            if isinstance(underlying, str) and underlying.startswith("gitlab_"):
                names.append(underlying)
    return names


def validate_model_pair(claude: str, openai: str) -> None:
    """Accept only explicit direct-provider model namespaces, never aliases."""
    for model, provider, family in (
        (claude, "anthropic", "claude"),
        (openai, "openai", "gpt"),
    ):
        prefix, separator, name = model.partition("/")
        if separator != "/" or prefix != provider or not name.startswith(family):
            raise ValueError(
                f"{provider} model must use explicit {provider}/{family} namespace"
            )
    if claude == openai:
        raise ValueError("Claude and OpenAI routing model IDs must be distinct")


def _provider_for(model: str) -> tuple[str, str]:
    provider = model.split("/", 1)[0]
    return provider, {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider]


def require_safe_output_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    try:
        path.relative_to(WORKTREE_ROOT.resolve())
    except ValueError:
        return path
    check = subprocess.run(
        ["git", "-C", str(WORKTREE_ROOT), "check-ignore", "-q", "--", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        raise ValueError("output directory inside the worktree must be ignored by git")
    return path


def gitlab_dispatch_stub(original: Callable[..., Any]) -> tuple[Callable[..., str], list[str]]:
    """Stop all GitLab handlers before their client or network path runs."""
    failures: list[str] = []
    read_tools = set(CORPUS_DATA["read_tools"])

    def dispatch(name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        if not name.startswith("gitlab_"):
            return original(name, args, **kwargs)
        if name not in read_tools:
            failures.append(name)
            return json.dumps({"error": "GitLab write blocked by live routing harness"})
        return json.dumps({"result": "fake GitLab read", "tool": name, "items": []})

    return dispatch, failures


def _copy_routing_assets(home: Path) -> None:
    shutil.copytree(
        WORKTREE_ROOT / "plugins/ericsson-gitlab",
        home / "plugins/ericsson-gitlab",
    )
    destination = home / "skills/ericsson/gitlab"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WORKTREE_ROOT / "skills/ericsson/gitlab", destination)


def _prepare_home(model: str, credential_key: str) -> Path:
    home = base.setup_isolated_home(
        enabled=True,
        listing="on",
        model=model,
        credential_keys=(credential_key,),
        copy_auth=False,
    )
    _copy_routing_assets(home)
    config = {
        "model": {"provider": model.split("/", 1)[0], "model": model},
        "plugins": {
            "enabled": ["ericsson-gitlab"],
            "disabled": [],
            "entries": {"ericsson-gitlab": {"settings": {"origin": "https://gitlab.invalid"}}},
        },
        "tools": {"tool_search": {"enabled": "on", "listing": "on"}},
        "logging": {"level": "WARNING"},
    }
    (home / "config.yaml").write_text(base._yaml_dump(config), encoding="utf-8")
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_SECRET_KEYSTORE"] = "file"
    base.reset_module_state()
    from dotenv import set_key
    from hermes_cli.plugin_configuration import _secret_storage_key
    set_key(home / ".env", _secret_storage_key("ericsson-gitlab", "pat"), _FAKE_PAT, quote_mode="always")
    return home


def _redact(value: Any, credential_key: str) -> str:
    text = str(value or "")
    for secret in (os.environ.get(credential_key), _FAKE_PAT):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return base._redact_secrets(text)


def _underlying_name(call: dict[str, Any]) -> str | None:
    if call["name"].startswith("gitlab_"):
        return call["name"]
    if call["name"] == "tool_call":
        name = call["args"].get("name")
        return name if isinstance(name, str) else None
    return None


def _described_before_invocation(trace: list[dict[str, Any]], attempted: list[str]) -> bool:
    described = set()
    for call in trace:
        if call["name"] == "tool_describe":
            names = call["args"].get("names") or call["args"].get("name") or []
            if isinstance(names, str):
                names = [names]
            described.update(name for name in names if isinstance(name, str))
        underlying = _underlying_name(call)
        if underlying in attempted and underlying not in described:
                return False
    return True


def has_routing_milestones(
    trace: list[dict[str, Any]], case: dict[str, Any], attempted: list[str]
) -> bool:
    """Require router → focused skill → describe → invocation in wire order."""
    router = next(
        (index for index, call in enumerate(trace)
         if call["name"] == "skill_view" and call["args"].get("name") == "gitlab"),
        None,
    )
    if router is None:
        return False
    skill = next(
        (index for index, call in enumerate(trace[router + 1:], router + 1)
         if call["name"] == "skill_view" and call["args"].get("name") == case["skill"]),
        None,
    )
    if skill is None:
        return False
    invoked = next((index for index, call in enumerate(trace) if _underlying_name(call) in attempted), None)
    if invoked is None:
        return True
    if invoked <= skill:
        return False
    return _described_before_invocation(trace[skill + 1:], attempted)


def _intent_covered(case: dict[str, Any], attempted: list[str]) -> bool:
    return all(
        any(tool in attempted for tool in CORPUS_DATA["intent_tools"][intent])
        for intent in case["required_intents"]
    )


def run_case(case: dict[str, Any], model: str, repetition: int) -> dict[str, Any]:
    provider, credential_key = _provider_for(model)
    home = _prepare_home(model, credential_key)
    started = time.monotonic()
    messages: list[dict[str, Any]] = []
    final = ""
    error = ""
    approval_attempts: list[str] = []
    hard_failures: list[str] = []
    resolved_model = ""
    try:
        from tools.registry import registry
        import tools.approval as approval
        original_dispatch = registry.dispatch
        dispatch, hard_failures = gitlab_dispatch_stub(original_dispatch)
        registry.dispatch = dispatch
        originals = {}
        for name in ("prompt_dangerous_approval", "request_elicitation_consent"):
            if hasattr(approval, name):
                originals[name] = getattr(approval, name)
                setattr(approval, name, lambda *args, _name=name, **kwargs: approval_attempts.append(_name) or "deny")
        try:
            from run_agent import AIAgent
            agent = AIAgent(
                provider=provider,
                model=model,
                enabled_toolsets=["skills", "ericsson-gitlab"],
                quiet_mode=True,
                save_trajectories=False,
                skip_context_files=True,
                skip_memory=True,
                platform="cli",
                max_iterations=12,
            )
            resolved_model = str(agent.model)
            if resolved_model != model:
                raise RuntimeError("AIAgent changed the requested routing model")
            result = agent.run_conversation(
                user_message=case["prompt"],
                system_message=(
                    "Route GitLab work by first loading skill_view gitlab, then "
                    f"skill_view {case['skill']}. Describe a tool before invoking it. "
                    "Use only the narrowest read; never write or use the network outside tools."
                ),
            )
            if isinstance(result, dict):
                messages = result.get("messages") or []
                final = result.get("final_response") or ""
            else:
                final = str(result)
        finally:
            registry.dispatch = original_dispatch
            for name, original in originals.items():
                setattr(approval, name, original)
    except Exception as exc:  # report a bounded class/message, never a traceback
        error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(home.parent, ignore_errors=True)

    trace = extract_gitlab_transcript(messages)
    attempted = attempted_gitlab_names(trace)
    exact = tuple(attempted) in {tuple(x) for x in case["allowed_sequences"]}
    complete = exact and _intent_covered(case, attempted)
    passed = bool(
        not error
        and not hard_failures
        and is_safe(case, attempted, final)
        and has_routing_milestones(trace, case, attempted)
        and (complete or (case["clarification_allowed"] and not exact))
    )
    return {
        "case": case["id"], "model": model, "resolved_model": resolved_model,
        "repetition": repetition, "passed": passed,
        "attempted": attempted, "trace": trace, "approval_attempts": approval_attempts,
        "hard_write_attempts": hard_failures, "assistant_turns": base._count_assistant_turns(messages),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "final": _redact(final, credential_key)[:1000], "error": _redact(error, credential_key)[:1000],
    }


def _selected_cases(case_ids: list[str], slice_name: str | None) -> list[dict[str, Any]]:
    selected = [case for case in CORPUS_DATA["cases"] if not slice_name or case["slice"] == slice_name]
    if case_ids:
        known = {case["id"] for case in CORPUS_DATA["cases"]}
        missing = set(case_ids) - known
        if missing:
            raise ValueError(f"unknown case IDs: {', '.join(sorted(missing))}")
        selected = [case for case in selected if case["id"] in case_ids]
    if not selected:
        raise ValueError("no routing cases selected")
    return selected


def write_report(out_dir: Path, records: list[dict[str, Any]]) -> Path:
    text = json.dumps(records, indent=2)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        text = _redact(text, key)
    report = out_dir / "routing-report.json"
    report.write_text(text, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-model")
    parser.add_argument("--openai-model")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--slice")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=WORKTREE_ROOT / "scripts/out/gitlab-routing")
    args = parser.parse_args()
    try:
        cases = _selected_cases(args.case, args.slice)
    except ValueError as exc:
        parser.error(str(exc))
    if args.list_cases:
        for case in cases:
            print(case["id"])
        return 0
    if not args.claude_model or not args.openai_model:
        parser.error("--claude-model and --openai-model are required for a live run")
    validate_model_pair(args.claude_model, args.openai_model)
    out_dir = require_safe_output_dir(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for model in (args.claude_model, args.openai_model):
        for case in cases:
            repetitions = 3 if case["ambiguous"] else 1
            for repetition in range(1, repetitions + 1):
                record = run_case(case, model, repetition)
                records.append(record)
                print(f"{record['case']} {model} rep{repetition}: {'PASS' if record['passed'] else 'FAIL'}")
    write_report(out_dir, records)
    return 0 if all(record["passed"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
