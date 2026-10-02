#!/usr/bin/env python3
"""Run a lightweight final integrity check for a completed SpecLitmus run."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Perform a final integrity check on a SpecLitmus run.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    validation = load_json(args.validation)
    report_manifest = load_json(run_dir / "report_manifest.json")
    audit = load_json(run_dir / "variant_audit.json")
    static_triage = load_json(run_dir / "static_triage.json")
    candidates = load_json(run_dir / "candidates.json")

    records = audit if isinstance(audit, list) else audit.get("records", [])
    triage_records = static_triage.get("records", [])
    candidate_rows = candidates.get("candidates", [])
    report_dir = run_dir / "reports"
    report_texts = {
        path.name: path.read_text(encoding="utf-8-sig")
        for path in sorted(report_dir.glob("*.md"))
    }

    verdict_counts = Counter(row.get("verdict", "") for row in records if isinstance(row, dict))
    problem_ids = [
        row.get("record_id", row.get("candidate_id"))
        for row in records
        if isinstance(row, dict) and row.get("verdict") in {"suspected_issue", "issue_found"}
    ]
    report_files = report_manifest.get("problem_reports", [])
    problem_id_set = {str(item) for item in problem_ids if item}
    covered_problem_ids = {
        candidate_id
        for candidate_id in problem_id_set
        if any(candidate_id in text for text in report_texts.values())
    }

    check = {
        "run_dir": str(run_dir),
        "validation_ok": bool(validation.get("ok")),
        "required_files_present": all(
            (run_dir / name).is_file()
            for name in (
                "run_manifest.json",
                "requirements.json",
                "requirement_selection.json",
                "static_triage.json",
                "candidates.json",
                "variant_audit.json",
                "variant_audit.md",
                "report_manifest.json",
                "validation_summary.json",
            )
        ),
        "counts_match": {
            "candidate_count": len(candidate_rows),
            "audit_record_count": len(records),
            "static_triage_count": len(triage_records),
            "reported_problem_count": len(report_files),
            "expected_problem_count": len(problem_ids),
            "record_count_matches_candidates": len(records) == len(candidate_rows),
            "reports_cover_all_problem_records": covered_problem_ids == problem_id_set,
        },
        "verdict_counts": dict(verdict_counts),
        "runtime_proven_issue_clusters": {
            "server_authentication_probe_logs": all(
                (run_dir / rel).is_file()
                for rel in (
                    "shared/logs/verify-none/control.log",
                    "shared/logs/verify-none/strict-no-ca.log",
                    "shared/logs/verify-none/verify-none.log",
                )
            ),
            "early_ticket_probe_logs": all(
                (run_dir / rel).is_file()
                for rel in (
                    "shared/logs/ticket-order/default.log",
                    "shared/logs/ticket-order/early.log",
                )
            ),
        },
        "report_paths": report_files,
        "validation_errors": validation.get("errors", []),
        "validation_warnings": validation.get("warnings", []),
        "ok": bool(validation.get("ok"))
        and len(records) == len(candidate_rows)
        and covered_problem_ids == problem_id_set,
    }

    args.out.write_text(
        json.dumps(check, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(check, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
