#!/usr/bin/env python3
"""Parallel append-capable litmus-test generation.

This wrapper intentionally leaves generate_litmus_tests.py unchanged.  It
reuses the original module's prompts, API caller, JSON repair, fallback, and
normalization logic, while adding threaded scheduling and append/resume output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import generate_litmus_tests as base


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def read_existing_tests(out_json: Path, out_jsonl: Path | None) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    if out_jsonl and out_jsonl.exists():
        with out_jsonl.open("r", encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid existing JSONL row at {out_jsonl}:{line_no}") from exc
                if isinstance(row, dict):
                    tests.append(row)
        return tests

    data = read_json(out_json)
    raw_tests = data.get("tests", [])
    return [test for test in raw_tests if isinstance(test, dict)]


def read_existing_group_results(out_json: Path) -> dict[int, dict[str, Any]]:
    data = read_json(out_json)
    results: dict[int, dict[str, Any]] = {}
    for row in data.get("group_results", []) or []:
        if not isinstance(row, dict):
            continue
        try:
            group_index = int(row.get("group_index"))
        except (TypeError, ValueError):
            continue
        results[group_index] = row
    return results


def ensure_jsonl_seed(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if not path or path.exists() or not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def rewrite_jsonl(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if not path or not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def group_completed(row: dict[str, Any]) -> bool:
    if row.get("error"):
        return bool(row.get("test_ids"))
    return "test_ids" in row


def group_source_items(group: list[dict[str, Any]]) -> list[Any]:
    return [item.get("item_id") for item in group]


def max_existing_test_index(tests: list[dict[str, Any]]) -> int:
    max_index = 0
    for test in tests:
        match = re.search(r"_litmus_(\d+)$", str(test.get("litmus_id", "")))
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index


def filter_stale_existing_state(
    *,
    existing_tests: list[dict[str, Any]],
    existing_group_results: dict[int, dict[str, Any]],
    groups: list[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], set[str], int]:
    kept_group_results: dict[int, dict[str, Any]] = {}
    stale_test_ids: set[str] = set()
    stale_groups = 0

    for group_index, row in existing_group_results.items():
        if group_index < 1 or group_index > len(groups):
            stale_groups += 1
            stale_test_ids.update(str(test_id) for test_id in row.get("test_ids", []) or [])
            continue
        expected = group_source_items(groups[group_index - 1])
        if row.get("source_items") == expected and group_completed(row):
            kept_group_results[group_index] = row
            continue
        stale_groups += 1
        stale_test_ids.update(str(test_id) for test_id in row.get("test_ids", []) or [])

    if not stale_test_ids:
        return existing_tests, kept_group_results, stale_test_ids, stale_groups

    filtered_tests = [
        test
        for test in existing_tests
        if str(test.get("litmus_id", "")) not in stale_test_ids
    ]
    return filtered_tests, kept_group_results, stale_test_ids, stale_groups


def generate_one(
    *,
    group_index: int,
    total_groups: int,
    group: list[dict[str, Any]],
    args: argparse.Namespace,
    api_key: str,
) -> dict[str, Any]:
    item_ids = [item.get("item_id") for item in group]
    try:
        raw_tests, error = base.generate_group(
            group=group,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            retries=args.retries,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # Keep other workers moving and record the failed group.
        raw_tests, error = [], str(exc)
    return {
        "group_index": group_index,
        "total_groups": total_groups,
        "group": group,
        "source_items": item_ids,
        "raw_tests": raw_tests,
        "error": error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate protocol litmus tests with threaded API calls and append/resume output."
    )
    parser.add_argument("--semantics", required=True, type=Path, help="Input semantics JSON or JSONL.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output litmus tests JSON.")
    parser.add_argument("--out-jsonl", type=Path, default=None, help="Optional append JSONL for tests.")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", base.DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", base.DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=None, help="Optional sampling temperature; only use with models that support it.")
    parser.add_argument("--max-tokens", type=int, default=3500)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--group-mode",
        choices=["item", "subject", "chunk", "section"],
        default="subject",
        help="How to group semantic items before generation.",
    )
    parser.add_argument("--max-group-size", type=int, default=5)
    parser.add_argument("--limit-groups", type=int, default=None, help="Process only the first N groups.")
    parser.add_argument("--start-group", type=int, default=1, help="1-based group index to start from.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent API calls.")
    parser.add_argument("--append", action="store_true", help="Accepted for compatibility; append is the default.")
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic fallback generation without LLM.")
    parser.add_argument("--overwrite", action="store_true", help="Delete existing outputs before running.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.workers < 1:
        print("--workers must be >= 1", file=sys.stderr)
        sys.exit(2)

    if not args.dry_run:
        api_key = os.getenv(args.api_key_env)
        if not api_key:
            print(f"Missing API key. Set {args.api_key_env}, or use --dry-run.", file=sys.stderr)
            sys.exit(2)
    else:
        api_key = ""

    outputs = [args.out_json]
    if args.out_jsonl:
        outputs.append(args.out_jsonl)
    if args.overwrite:
        for path in outputs:
            if path.exists():
                path.unlink()

    metadata, items = base.load_semantics(args.semantics)
    if not items:
        print("No semantic items found.", file=sys.stderr)
        sys.exit(1)

    groups = base.group_items(items, args.group_mode, args.max_group_size)
    if args.limit_groups is not None:
        groups = groups[: args.limit_groups]
    if args.start_group < 1 or args.start_group > len(groups) + 1:
        print(f"--start-group must be between 1 and {len(groups) + 1}.", file=sys.stderr)
        sys.exit(2)

    existing_tests = read_existing_tests(args.out_json, args.out_jsonl)
    existing_group_results = read_existing_group_results(args.out_json)
    existing_tests, existing_group_results, stale_test_ids, stale_groups = filter_stale_existing_state(
        existing_tests=existing_tests,
        existing_group_results=existing_group_results,
        groups=groups,
    )
    if stale_groups:
        print(
            f"Detected {stale_groups} stale group results for the current semantics/grouping; "
            f"will regenerate them."
        )
    if stale_test_ids:
        print(f"Removed {len(stale_test_ids)} stale litmus ids from the resumed JSON/JSONL view.")
        rewrite_jsonl(args.out_jsonl, existing_tests)
    else:
        ensure_jsonl_seed(args.out_jsonl, existing_tests)

    all_tests: list[dict[str, Any]] = list(existing_tests)
    group_results: dict[int, dict[str, Any]] = dict(existing_group_results)
    document_id = str(items[0].get("document_id") or "document")
    next_test_index = max_existing_test_index(all_tests) + 1

    candidate_indices = list(range(args.start_group, len(groups) + 1))
    pending_indices = [
        idx
        for idx in candidate_indices
        if not group_completed(group_results.get(idx, {}))
        or group_results.get(idx, {}).get("source_items") != group_source_items(groups[idx - 1])
    ]

    if not pending_indices:
        print("No pending groups. Existing output already covers the selected range.")
    else:
        print(
            f"Generating {len(pending_indices)} groups with {args.workers} workers "
            f"(append/resume mode)."
        )

    completed: dict[int, dict[str, Any]] = {}
    flush_pos = 0

    def flush_ready() -> None:
        nonlocal flush_pos, next_test_index
        while flush_pos < len(pending_indices):
            group_index = pending_indices[flush_pos]
            if group_index not in completed:
                break
            result = completed.pop(group_index)
            tests: list[dict[str, Any]] = []
            for raw_test in result["raw_tests"]:
                test = base.normalize_test(
                    raw_test,
                    group=result["group"],
                    test_index=next_test_index,
                    document_id=document_id,
                )
                if test:
                    tests.append(test)
                    all_tests.append(test)
                    next_test_index += 1
            append_jsonl(args.out_jsonl, tests)
            group_results[group_index] = {
                "group_index": group_index,
                "source_items": result["source_items"],
                "test_ids": [test["litmus_id"] for test in tests],
                "error": result["error"],
            }
            if result["error"]:
                print(f"[{group_index}/{len(groups)}] error: {result['error']}")
            print(f"[{group_index}/{len(groups)}] tests={len(tests)}")
            flush_pos += 1

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                generate_one,
                group_index=group_index,
                total_groups=len(groups),
                group=groups[group_index - 1],
                args=args,
                api_key=api_key,
            )
            for group_index in pending_indices
        ]
        for future in as_completed(futures):
            result = future.result()
            completed[int(result["group_index"])] = result
            flush_ready()

    output = {
        "metadata": {
            "semantics_file": str(args.semantics),
            "source_metadata": metadata,
            "model": "dry-run" if args.dry_run else args.model,
            "base_url": args.base_url,
            "group_mode": args.group_mode,
            "groups": len(groups),
            "source_item_count": len(items),
            "litmus_test_count": len(all_tests),
            "workers": args.workers,
            "append_mode": True,
        },
        "group_results": [
            group_results[idx]
            for idx in sorted(group_results)
        ],
        "tests": all_tests,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
