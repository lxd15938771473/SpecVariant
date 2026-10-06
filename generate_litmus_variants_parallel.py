#!/usr/bin/env python3
"""Parallel append-capable litmus-variant generation.

This wrapper intentionally leaves generate_litmus_variants.py unchanged.  It
reuses the original module's prompts, API caller, JSON repair, fallback, and
family/variant normalization logic, while adding threaded scheduling and
append/resume output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import generate_litmus_variants as base


BASE_SIGNATURE_KEYS = (
    "source_items",
    "name",
    "intent",
    "category",
    "preconditions",
    "actors",
    "minimal_trace",
    "control_trace",
    "field_bound_checks",
    "oracle",
    "E",
    "expected_strength",
    "feasibility",
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def family_sort_key(family: dict[str, Any]) -> tuple[int, str]:
    family_id = str(family.get("family_id", ""))
    match = re.search(r"_family_(\d+)$", family_id)
    if match:
        return int(match.group(1)), family_id
    return sys.maxsize, family_id


def base_signature(test: dict[str, Any]) -> str:
    payload = {key: test.get(key) for key in BASE_SIGNATURE_KEYS}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def family_base_signature(family: dict[str, Any]) -> str | None:
    existing = family.get("base_signature")
    if existing:
        return str(existing)
    base_test = family.get("base_test")
    if isinstance(base_test, dict):
        return base_signature(base_test)
    return None


def row_base_signature(row: dict[str, Any]) -> str | None:
    existing = row.get("base_signature")
    return str(existing) if existing else None


def variant_sort_key(variant: dict[str, Any]) -> tuple[int, int, str]:
    family_id = str(variant.get("family_id", ""))
    variant_id = str(variant.get("variant_id", ""))
    family_match = re.search(r"_family_(\d+)$", family_id)
    variant_match = re.search(r"_variant_(\d+)$", variant_id)
    family_index = int(family_match.group(1)) if family_match else sys.maxsize
    variant_index = int(variant_match.group(1)) if variant_match else sys.maxsize
    return family_index, variant_index, variant_id


def flatten_family(family: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for variant in family.get("variants", []) or []:
        if not isinstance(variant, dict):
            continue
        rows.append(
            {
                "family_id": family.get("family_id"),
                "base_litmus_id": family.get("base_litmus_id"),
                "base_signature": family.get("base_signature"),
                "source_items": family.get("source_items", []),
                "E": family.get("E", []),
                **variant,
            }
        )
    return rows


def read_existing_output(out_json: Path, out_jsonl: Path | None) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    data = read_json(out_json)
    families = [family for family in data.get("families", []) or [] if isinstance(family, dict)]
    results: dict[str, dict[str, Any]] = {}
    for row in data.get("results", []) or []:
        if isinstance(row, dict) and row.get("base_litmus_id"):
            results[str(row["base_litmus_id"])] = row

    flat_variants = [variant for variant in data.get("variants", []) or [] if isinstance(variant, dict)]
    if not flat_variants and families:
        for family in sorted(families, key=family_sort_key):
            flat_variants.extend(flatten_family(family))

    if not families and out_jsonl and out_jsonl.exists():
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
                    flat_variants.append(row)
                    if row.get("base_litmus_id"):
                        results.setdefault(
                            str(row["base_litmus_id"]),
                            {
                                "family_id": row.get("family_id"),
                                "base_litmus_id": row.get("base_litmus_id"),
                                "variants": 1,
                                "rejected_variants": None,
                                "error": None,
                            },
                        )

    return families, results, flat_variants


def filter_stale_existing_output(
    *,
    families: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    flat_variants: list[dict[str, Any]],
    current_base_signatures: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]], int]:
    kept_families: list[dict[str, Any]] = []
    kept_family_base_ids: set[str] = set()
    stale_families = 0
    for family in families:
        base_id = str(family.get("base_litmus_id", ""))
        expected_signature = current_base_signatures.get(base_id)
        actual_signature = family_base_signature(family)
        if expected_signature and actual_signature == expected_signature:
            family["base_signature"] = expected_signature
            kept_families.append(family)
            kept_family_base_ids.add(base_id)
        else:
            stale_families += 1

    kept_results: dict[str, dict[str, Any]] = {}
    stale_results = 0
    for base_id, row in results.items():
        expected_signature = current_base_signatures.get(base_id)
        actual_signature = row_base_signature(row)
        if expected_signature and (actual_signature == expected_signature or base_id in kept_family_base_ids):
            row["base_signature"] = expected_signature
            kept_results[base_id] = row
        else:
            stale_results += 1

    kept_flat_variants: list[dict[str, Any]] = []
    stale_flat_variants = 0
    for variant in flat_variants:
        base_id = str(variant.get("base_litmus_id", ""))
        expected_signature = current_base_signatures.get(base_id)
        actual_signature = row_base_signature(variant)
        if expected_signature and (actual_signature == expected_signature or base_id in kept_family_base_ids):
            variant["base_signature"] = expected_signature
            kept_flat_variants.append(variant)
        else:
            stale_flat_variants += 1

    stale_count = stale_families + stale_results + stale_flat_variants
    return kept_families, kept_results, kept_flat_variants, stale_count


def ensure_jsonl_seed(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if not path or path.exists() or not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in sorted(rows, key=variant_sort_key):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def rewrite_jsonl(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in sorted(rows, key=variant_sort_key):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path | None, rows: list[dict[str, Any]]) -> None:
    if not path or not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def generate_one(
    *,
    test_index: int,
    total_tests: int,
    test: dict[str, Any],
    args: argparse.Namespace,
    api_key: str,
) -> dict[str, Any]:
    try:
        raw_family, error = base.generate_raw_variants(
            base_test=test,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            retries=args.retries,
            target_variants=args.target_variants,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # Keep other workers moving and record the failed test.
        raw_family = {
            "family_name": f"{test.get('name', 'Litmus')} family",
            "core_semantic": test.get("intent", ""),
            "variant_strategy": "Fallback after worker failure.",
            "variants": base.fallback_variants(test, args.target_variants),
        }
        error = str(exc)
    return {
        "test_index": test_index,
        "total_tests": total_tests,
        "test": test,
        "raw_family": raw_family,
        "error": error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate litmus variants with threaded API calls and append/resume output."
    )
    parser.add_argument("--tests", "--litmus", dest="litmus", required=True, type=Path, help="Input litmus JSON or JSONL.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output litmus families JSON.")
    parser.add_argument("--out-jsonl", type=Path, default=None, help="Optional append JSONL for flat variants.")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", base.DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", base.DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=None, help="Optional sampling temperature; only use with models that support it.")
    parser.add_argument("--max-tokens", type=int, default=4200)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--target-variants", type=int, default=6, help="Upper bound per family; weak variants are filtered.")
    parser.add_argument("--min-value-score", type=int, default=3, help="Drop variants below this score.")
    parser.add_argument("--limit-tests", type=int, default=None)
    parser.add_argument("--start-test", type=int, default=1, help="1-based litmus-test index to start from.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent API calls.")
    parser.add_argument("--append", action="store_true", help="Accepted for compatibility; append is the default.")
    parser.add_argument("--dry-run", action="store_true")
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

    metadata, tests = base.load_tests(args.litmus)
    if args.limit_tests is not None:
        tests = tests[: args.limit_tests]
    if not tests:
        print("No litmus tests found.", file=sys.stderr)
        sys.exit(1)
    if args.start_test < 1 or args.start_test > len(tests) + 1:
        print(f"--start-test must be between 1 and {len(tests) + 1}.", file=sys.stderr)
        sys.exit(2)

    existing_families, existing_results, existing_flat_variants = read_existing_output(args.out_json, args.out_jsonl)
    current_base_signatures = {
        str(test.get("litmus_id", "")): base_signature(test)
        for test in tests
        if test.get("litmus_id")
    }
    existing_families, existing_results, existing_flat_variants, stale_count = filter_stale_existing_output(
        families=existing_families,
        results=existing_results,
        flat_variants=existing_flat_variants,
        current_base_signatures=current_base_signatures,
    )
    if stale_count:
        print(f"Detected and removed {stale_count} stale existing family/result/variant rows for the current litmus input.")
        rewrite_jsonl(args.out_jsonl, existing_flat_variants)
    else:
        ensure_jsonl_seed(args.out_jsonl, existing_flat_variants)

    families_by_base: dict[str, dict[str, Any]] = {}
    for family in existing_families:
        base_id = family.get("base_litmus_id")
        if base_id:
            families_by_base[str(base_id)] = family

    processed_base_ids = set(families_by_base)
    processed_base_ids.update(
        str(row.get("base_litmus_id"))
        for row in existing_flat_variants
        if row.get("base_litmus_id")
        and row_base_signature(row) == current_base_signatures.get(str(row.get("base_litmus_id")))
    )

    candidate_indices = list(range(args.start_test, len(tests) + 1))
    pending_indices = [
        idx
        for idx in candidate_indices
        if str(tests[idx - 1].get("litmus_id")) not in processed_base_ids
    ]

    if not pending_indices:
        print("No pending litmus tests. Existing output already covers the selected range.")
    else:
        print(
            f"Generating variants for {len(pending_indices)} litmus tests with "
            f"{args.workers} workers (append/resume mode)."
        )

    results_by_base: dict[str, dict[str, Any]] = dict(existing_results)
    new_families: list[dict[str, Any]] = []
    completed: dict[int, dict[str, Any]] = {}
    flush_pos = 0

    def flush_ready() -> None:
        nonlocal flush_pos
        while flush_pos < len(pending_indices):
            test_index = pending_indices[flush_pos]
            if test_index not in completed:
                break
            result = completed.pop(test_index)
            test = result["test"]
            signature = base_signature(test)
            family = base.build_family(
                base=copy.deepcopy(test),
                raw_family=copy.deepcopy(result["raw_family"]),
                family_index=test_index,
                min_value_score=args.min_value_score,
                target_variants=args.target_variants,
            )
            family["base_signature"] = signature
            families_by_base[str(family.get("base_litmus_id"))] = family
            new_families.append(family)
            flat_rows = flatten_family(family)
            append_jsonl(args.out_jsonl, flat_rows)
            results_by_base[str(family.get("base_litmus_id"))] = {
                "family_id": family["family_id"],
                "base_litmus_id": family["base_litmus_id"],
                "base_signature": signature,
                "variants": len(family["variants"]),
                "rejected_variants": len(family["rejected_variants"]),
                "error": result["error"],
            }
            if result["error"]:
                print(f"[{test_index}/{len(tests)}] error: {result['error']}")
            print(f"[{test_index}/{len(tests)}] kept={len(family['variants'])}, rejected={len(family['rejected_variants'])}")
            flush_pos += 1

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                generate_one,
                test_index=test_index,
                total_tests=len(tests),
                test=tests[test_index - 1],
                args=args,
                api_key=api_key,
            )
            for test_index in pending_indices
        ]
        for future in as_completed(futures):
            result = future.result()
            completed[int(result["test_index"])] = result
            flush_ready()

    all_families = sorted(families_by_base.values(), key=family_sort_key)
    all_flat_variants: list[dict[str, Any]] = []
    if all_families:
        for family in all_families:
            all_flat_variants.extend(flatten_family(family))
    else:
        all_flat_variants = list(existing_flat_variants)
    all_flat_variants = sorted(all_flat_variants, key=variant_sort_key)

    output = {
        "metadata": {
            "litmus_file": str(args.litmus),
            "source_metadata": metadata,
            "model": "dry-run" if args.dry_run else args.model,
            "base_url": args.base_url,
            "base_test_count": len(tests),
            "family_count": len(all_families),
            "variant_count": len(all_flat_variants),
            "target_variants": args.target_variants,
            "min_value_score": args.min_value_score,
            "workers": args.workers,
            "append_mode": True,
        },
        "results": [
            results_by_base[base_id]
            for base_id in sorted(
                results_by_base,
                key=lambda value: family_sort_key({"family_id": results_by_base[value].get("family_id", "")}),
            )
        ],
        "families": all_families,
        "variants": all_flat_variants,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
