#!/usr/bin/env python3
"""Run SpecLitmus harness-ready IR through a protocol adapter.

This is step 5 of the SpecLitmus prototype:
  executable IR -> adapter execution trace -> verdict artifacts.

The runner is protocol-neutral. It understands normalized IR structure,
adapter operation results, and oracle buckets. Protocol behavior belongs in
adapters, not here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from adapters.dry_run import DryRunAdapter
from adapters.openssl_tls13 import OpenSSLTLS13Adapter


VERDICT_ORDER = {
    "fail": 0,
    "inconclusive": 1,
    "observed": 2,
    "pass": 3,
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        cases: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if not isinstance(row, dict) or "ir_id" not in row:
                    raise ValueError(f"Unsupported IR JSONL row at {path}:{line_no}")
                cases.append(row)
        return {"source": str(path)}, cases

    data = read_json(path)
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data.get("metadata", {"source": str(path)}), data["cases"]
    if isinstance(data, list):
        return {"source": str(path)}, data
    raise ValueError(f"Unsupported IR JSON shape: {path}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_profile(path: Path | None) -> dict[str, Any]:
    if not path:
        path = Path(__file__).resolve().parent / "adapters" / "profiles" / "generic.json"
    return read_json(path)


def build_adapter(name: str, profile: dict[str, Any]) -> Any:
    if name == "dry-run":
        return DryRunAdapter(profile=profile)
    if name in {"openssl-tls13", "openssl_tls13"}:
        return OpenSSLTLS13Adapter(profile=profile)
    raise ValueError(f"Unsupported adapter: {name}")


def result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "observation": result.observation,
        "error": result.error,
    }


def adapter_failed(adapter_results: list[dict[str, Any]]) -> bool:
    return any(result.get("status") == "error" for result in adapter_results)


def adapter_unsupported(adapter_results: list[dict[str, Any]]) -> bool:
    return any(result.get("status") == "unsupported" for result in adapter_results)


def scheduled_event_ids(adapter_results: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for result in adapter_results:
        observation = result.get("observation") or {}
        if observation.get("phase") == "event" and observation.get("event_id"):
            ids.add(str(observation["event_id"]))
    return ids


def combine_verdicts(verdicts: list[str]) -> str:
    if not verdicts:
        return "inconclusive"
    return min(verdicts, key=lambda verdict: VERDICT_ORDER.get(verdict, 1))


def evaluate_setup_checks(
    checks: list[dict[str, Any]],
    adapter_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scheduled = scheduled_event_ids(adapter_results)
    evaluated: list[dict[str, Any]] = []
    for check in checks:
        target_events = {str(event_id) for event_id in check.get("target_events", [])}
        missing = sorted(target_events - scheduled)
        verdict = (
            "pass"
            if not missing and not adapter_failed(adapter_results) and not adapter_unsupported(adapter_results)
            else "inconclusive"
        )
        evaluated.append(
            {
                "check_id": check.get("check_id"),
                "kind": check.get("kind"),
                "verdict": verdict,
                "reason": (
                    "all setup target events were scheduled"
                    if verdict == "pass"
                    else "setup target events were not fully scheduled or adapter lacked support"
                ),
                "missing_events": missing,
                "oracle": check,
            }
        )
    return evaluated


def evaluate_normative_checks(
    checks: list[dict[str, Any]],
    adapter_results: list[dict[str, Any]],
    adapter_name: str,
) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    failed = adapter_failed(adapter_results)
    unsupported = adapter_unsupported(adapter_results)
    for check in checks:
        if failed:
            verdict = "fail"
            reason = "adapter reported an execution error"
        elif unsupported:
            verdict = "inconclusive"
            reason = "adapter does not support one or more required IR operations"
        elif adapter_name == "dry-run":
            verdict = "inconclusive"
            reason = "dry-run cannot confirm endpoint protocol behavior"
        else:
            verdict = "observed"
            reason = "adapter executed the check; endpoint-specific verdict mapping is required"
        evaluated.append(
            {
                "check_id": check.get("check_id"),
                "kind": check.get("kind"),
                "verdict": verdict,
                "reason": reason,
                "oracle": check,
            }
        )
    return evaluated


def evaluate_observations(
    observations: list[dict[str, Any]],
    adapter_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    failed = adapter_failed(adapter_results)
    unsupported = adapter_unsupported(adapter_results)
    for observation in observations:
        evaluated.append(
            {
                "observation_id": observation.get("observation_id"),
                "kind": observation.get("kind"),
                "verdict": "inconclusive" if failed or unsupported else "observed",
                "reason": (
                    "adapter reported an execution error"
                    if failed
                    else "adapter does not support one or more required IR operations"
                    if unsupported
                    else "observation boundary was scheduled and adapter trace was recorded"
                ),
                "oracle": observation,
            }
        )
    return evaluated


def evaluate_oracle(
    case: dict[str, Any],
    adapter_results: list[dict[str, Any]],
    adapter_name: str,
) -> dict[str, Any]:
    oracle = case.get("oracle", {}) or {}
    setup_checks = evaluate_setup_checks(oracle.get("setup_checks", []) or [], adapter_results)
    normative_checks = evaluate_normative_checks(
        oracle.get("normative_checks", []) or [],
        adapter_results,
        adapter_name,
    )
    observations = evaluate_observations(oracle.get("observations", []) or [], adapter_results)

    component_verdicts = [
        item["verdict"]
        for bucket in (setup_checks, normative_checks, observations)
        for item in bucket
    ]
    if adapter_failed(adapter_results) and not component_verdicts:
        overall = "fail"
    else:
        overall = combine_verdicts(component_verdicts)

    return {
        "overall": overall,
        "setup_checks": setup_checks,
        "normative_checks": normative_checks,
        "observations": observations,
    }


def run_case(case: dict[str, Any], adapter: Any) -> dict[str, Any]:
    adapter_results: list[dict[str, Any]] = []

    prepare = adapter.prepare(case)
    adapter_results.append({"phase": "prepare", **result_to_dict(prepare)})
    if prepare.status not in {"error", "unsupported"}:
        for event in case.get("events", []) or []:
            result = adapter.execute_event(case, event)
            adapter_results.append({"phase": "event", **result_to_dict(result)})
            if result.status in {"error", "unsupported"}:
                break
    teardown = adapter.teardown(case)
    adapter_results.append({"phase": "teardown", **result_to_dict(teardown)})

    verdict = evaluate_oracle(case, adapter_results, adapter.name)
    return {
        "ir_id": case.get("ir_id"),
        "family_id": case.get("family_id"),
        "base_litmus_id": case.get("base_litmus_id"),
        "variant_id": case.get("variant_id"),
        "name": case.get("name"),
        "adapter": adapter.name,
        "protocol": case.get("setup", {}).get("protocol"),
        "verdict": verdict,
        "adapter_results": adapter_results,
        "evidence_refs": [
            evidence.get("item_id")
            for evidence in case.get("evidence", []) or []
            if evidence.get("item_id")
        ],
    }


def summarize(results: list[dict[str, Any]], metadata: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    verdict_counts: dict[str, int] = {}
    for result in results:
        verdict = result.get("verdict", {}).get("overall", "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    return {
        "input_ir": str(args.ir),
        "adapter": args.adapter,
        "profile": str(args.profile) if args.profile else "adapters/profiles/generic.json",
        "source_metadata": metadata,
        "case_count": len(results),
        "verdict_counts": verdict_counts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SpecLitmus IR through a protocol adapter.")
    parser.add_argument("--ir", required=True, type=Path, help="Input IR JSON or JSONL.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output run results JSON.")
    parser.add_argument("--out-jsonl", type=Path, default=None, help="Optional flat run results JSONL.")
    parser.add_argument("--adapter", default="dry-run", help="Adapter name. Currently: dry-run.")
    parser.add_argument("--profile", type=Path, default=None, help="Adapter capability profile JSON.")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs = [args.out_json]
    if args.out_jsonl:
        outputs.append(args.out_jsonl)
    if args.overwrite:
        for path in outputs:
            if path.exists():
                path.unlink()
    else:
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            print(f"Output exists: {', '.join(existing)}. Use --overwrite.", file=sys.stderr)
            sys.exit(2)

    metadata, cases = load_cases(args.ir)
    if args.limit is not None:
        cases = cases[: args.limit]
    profile = load_profile(args.profile)
    adapter = build_adapter(args.adapter, profile)
    results = [run_case(case, adapter) for case in cases]
    summary = summarize(results, metadata, args)

    output = {
        "metadata": summary,
        "results": results,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_jsonl:
        write_jsonl(args.out_jsonl, results)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
