#!/usr/bin/env python3
"""Run a real extraction-to-Markdown SpecLitmus integration test."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_REPORT_HEADINGS = (
    "# Limit value 11 is accepted above the normative maximum",
    "## Problem Description",
    "## Standard Requirement",
    "## Relevant Source Code",
    "## Runtime Evidence",
    "## Inconsistency Reason",
)


class TotalTestError(RuntimeError):
    """Raised when an integration stage or final assertion fails."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run_stage(
    name: str,
    argv: list[str],
    *,
    cwd: Path,
    log_dir: Path,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        shell=False,
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{name}.stdout.log").write_text(
        completed.stdout, encoding="utf-8", newline="\n"
    )
    (log_dir / f"{name}.stderr.log").write_text(
        completed.stderr, encoding="utf-8", newline="\n"
    )
    if completed.returncode != 0:
        raise TotalTestError(
            f"{name} failed with exit code {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def select_boundary_candidate(document: dict[str, Any]) -> dict[str, Any]:
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise TotalTestError("generator output has no candidates array")
    matches = [
        item
        for item in candidates
        if isinstance(item, dict)
        and item.get("risk_class") == "boundary"
        and any(
            hint.get("nearest_valid") == 11
            for event in item.get("events", [])
            if isinstance(event, dict)
            for hint in (
                event.get("parameters", {}).get("boundary_hints", {}).values()
                if isinstance(event.get("parameters"), dict)
                and isinstance(
                    event.get("parameters", {}).get("boundary_hints"), dict
                )
                else []
            )
            if isinstance(hint, dict)
        )
    ]
    if len(matches) != 1:
        raise TotalTestError(
            f"expected exactly one boundary candidate for value 11, found {len(matches)}"
        )
    return matches[0]


def requirement_by_id(document: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    requirements = document.get("requirements")
    if not isinstance(requirements, list):
        raise TotalTestError("extractor output has no requirements array")
    matches = [
        item
        for item in requirements
        if isinstance(item, dict) and item.get("requirement_id") == requirement_id
    ]
    if len(matches) != 1:
        raise TotalTestError(
            f"candidate requirement {requirement_id!r} is not uniquely present"
        )
    return matches[0]


def build_runtime_round(
    runtime_result: dict[str, Any],
    runtime_dir: Path,
    run_dir: Path,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for step in runtime_result["steps"]:
        copied = dict(step)
        for field in ("stdout_log", "stderr_log"):
            copied[field] = relative_posix(runtime_dir / copied[field], run_dir)
        steps.append(copied)

    artifacts = [
        runtime_dir / "environment.json",
        runtime_dir / "result.json",
        runtime_dir / "stdout.log",
        runtime_dir / "stderr.log",
    ]
    for step in runtime_result["steps"]:
        artifacts.extend(
            [
                runtime_dir / step["stdout_log"],
                runtime_dir / step["stderr_log"],
            ]
        )
    reproducer = next(
        step for step in runtime_result["steps"] if step["role"] == "reproducer"
    )
    reproducer_stdout = (runtime_dir / reproducer["stdout_log"]).read_text(
        encoding="utf-8"
    ).strip()
    return {
        "round": runtime_result["round"],
        "status": runtime_result["status"],
        "positive_control_status": runtime_result["positive_control_status"],
        "reproducer_status": runtime_result["reproducer_status"],
        "summary": (
            "The positive control accepted valid value 10. The focused reproducer "
            f"observed the implementation accepting invalid value 11: {reproducer_stdout}"
        ),
        "steps": steps,
        "artifacts": [relative_posix(path, run_dir) for path in artifacts],
    }


def assert_final_report(run_dir: Path, candidate_id: str) -> Path:
    reports = sorted((run_dir / "reports").glob("*.md"))
    if len(reports) != 1:
        raise TotalTestError(f"expected one Markdown problem report, found {len(reports)}")
    report = reports[0]
    text = report.read_text(encoding="utf-8")
    for heading in REQUIRED_REPORT_HEADINGS:
        if heading not in text:
            raise TotalTestError(f"final report is missing {heading!r}")
    for required_text in (
        candidate_id,
        "MUST reject",
        "return 0 <= value <= 11",
        "protocol\\_violation\\_observed",
        "limit_impl.py",
    ):
        if required_text not in text:
            raise TotalTestError(
                f"final report does not retain required evidence {required_text!r}"
            )
    return report


def run_total_test(opt_dir: Path, run_dir: Path) -> dict[str, Any]:
    agents = opt_dir / "agents"
    fixtures = opt_dir / "tests" / "fixtures"
    target = fixtures / "demo_target"
    log_dir = run_dir / "pipeline-logs"

    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    chunker = (
        agents
        / "speclitmus-requirement-extractor"
        / "scripts"
        / "chunk_standard.py"
    )
    requirement_assembler = (
        agents
        / "speclitmus-requirement-extractor"
        / "scripts"
        / "assemble_requirements.py"
    )
    requirement_validator = (
        agents
        / "speclitmus-requirement-extractor"
        / "scripts"
        / "validate_requirements.py"
    )
    batch_preparer = (
        agents
        / "speclitmus-coordinator"
        / "scripts"
        / "prepare_requirement_batches.py"
    )
    batch_merger = (
        agents
        / "speclitmus-coordinator"
        / "scripts"
        / "merge_static_triage_batches.py"
    )
    generator = (
        agents
        / "speclitmus-test-generator"
        / "scripts"
        / "generate_candidates.py"
    )
    runtime_validator = (
        agents
        / "speclitmus-implementation-validator"
        / "scripts"
        / "run_runtime_plan.py"
    )
    reporter = (
        agents / "speclitmus-verdict-reporter" / "scripts" / "render_reports.py"
    )
    coordinator_validator = (
        agents / "speclitmus-coordinator" / "scripts" / "validate_run.py"
    )

    run_stage(
        "01-chunk-standard",
        [
            sys.executable,
            str(chunker),
            "--input",
            str(fixtures / "demo_standard.txt"),
            "--out",
            str(run_dir / "chunks.json"),
            "--document-id",
            "demo-standard-v1",
            "--max-chars",
            "240",
            "--overlap-paragraphs",
            "1",
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    run_stage(
        "02-assemble-agent-requirements",
        [
            sys.executable,
            str(requirement_assembler),
            "--source",
            str(fixtures / "demo_standard.txt"),
            "--chunks",
            str(run_dir / "chunks.json"),
            "--agent-output-dir",
            str(fixtures / "demo_agent_extraction"),
            "--out",
            str(run_dir / "requirements.json"),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    run_stage(
        "03-validate-requirements",
        [
            sys.executable,
            str(requirement_validator),
            "--input",
            str(run_dir / "requirements.json"),
            "--chunks",
            str(run_dir / "chunks.json"),
            "--source",
            str(fixtures / "demo_standard.txt"),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    requirements_document = json.loads(
        (run_dir / "requirements.json").read_text(encoding="utf-8")
    )
    write_json(
        run_dir / "run_config.json",
        {
            "schema_version": "speclitmus.run-config.v1",
            "requirements": {"reuse": False},
            "static_triage": {
                "start": 1,
                "end": None,
                "batch_size": 2,
            },
        },
    )
    run_stage(
        "04-prepare-static-triage-batches",
        [
            sys.executable,
            str(batch_preparer),
            "--config",
            str(run_dir / "run_config.json"),
            "--source",
            str(fixtures / "demo_standard.txt"),
            "--output-dir",
            str(run_dir),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    selection_document = json.loads(
        (run_dir / "requirement_selection.json").read_text(encoding="utf-8")
    )
    triage_records = []
    limit_source = (target / "limit_impl.py").read_text(encoding="utf-8").splitlines()
    for requirement in selection_document["requirements"]:
        quote = requirement["evidence"]["quote"]
        is_limit_bug = "greater than 10" in quote
        is_limit_acceptance = "0 through 10" in quote
        excerpt_start = 4 if (is_limit_bug or is_limit_acceptance) else 8
        excerpt_end = 6 if (is_limit_bug or is_limit_acceptance) else 12
        triage_records.append(
            {
                "requirement_id": requirement["requirement_id"],
                "verdict": "issue_found" if is_limit_bug else "no_issue",
                "confidence": "high",
                "standard_evidence": requirement["evidence"],
                "code_check": {
                    "summary": (
                        "The demo limit predicate accepts 11."
                        if is_limit_bug
                        else "The demo static triage found no mismatch requiring variants."
                    ),
                    "excerpts": [
                        {
                            "path": "limit_impl.py",
                            "line_start": excerpt_start,
                            "line_end": excerpt_end,
                            "code": "\n".join(
                                limit_source[excerpt_start - 1 : excerpt_end]
                            ),
                            "explanation": (
                                "The inclusive comparison accepts one value above the standard maximum."
                                if is_limit_bug
                                else (
                                    "The inclusive comparison accepts the valid range 0 through 10."
                                    if is_limit_acceptance
                                    else "The token processing path rejects repeated tokens."
                                )
                            ),
                        }
                    ],
                },
                "decision_reason": (
                    "Static code inspection identifies a mismatch that needs a focused runtime variant."
                    if is_limit_bug
                    else "Static code inspection is consistent enough to skip variant generation."
                ),
                "inconsistency_reason": (
                    "The implementation accepts value 11 while the standard requires rejection."
                    if is_limit_bug
                    else ""
                ),
                "remaining_uncertainty": "",
                "generation_action": "generate_variants" if is_limit_bug else "skip",
            }
        )
    records_by_requirement = {
        record["requirement_id"]: record for record in triage_records
    }
    selection_metadata = selection_document["metadata"]
    for descriptor in selection_document["batches"]:
        write_json(
            run_dir / descriptor["output"],
            {
                "schema_version": "speclitmus.static-triage.v1",
                "metadata": {
                    "batch_id": descriptor["batch_id"],
                    "selection_sha256": selection_metadata["selection_sha256"],
                    "requirements_sha256": selection_metadata[
                        "requirements_sha256"
                    ],
                    "target": "demo_target",
                    "target_revision": "fixture-deliberate-bug-v1",
                    "records": descriptor["requirement_count"],
                },
                "records": [
                    records_by_requirement[requirement_id]
                    for requirement_id in descriptor["requirement_ids"]
                ],
            },
        )
    run_stage(
        "05-merge-static-triage-batches",
        [
            sys.executable,
            str(batch_merger),
            "--selection",
            str(run_dir / "requirement_selection.json"),
            "--out",
            str(run_dir / "static_triage.json"),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    run_stage(
        "06-generate",
        [
            sys.executable,
            str(generator),
            "--requirements",
            str(run_dir / "requirement_selection.json"),
            "--static-triage",
            str(run_dir / "static_triage.json"),
            "--out",
            str(run_dir / "candidates.generated.json"),
            "--max-variants",
            "3",
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )

    generated_document = json.loads(
        (run_dir / "candidates.generated.json").read_text(encoding="utf-8")
    )
    candidate = select_boundary_candidate(generated_document)
    requirement = requirement_by_id(
        requirements_document, str(candidate["requirement_id"])
    )
    write_json(
        run_dir / "candidates.json",
        {
            "schema_version": generated_document.get("schema_version"),
            "selection": {
                "purpose": "focused end-to-end infrastructure test",
                "source": "candidates.generated.json",
                "count": 1,
            },
            "candidates": [candidate],
        },
    )

    runtime_plan = json.loads(
        (fixtures / "demo_runtime_plan.json").read_text(encoding="utf-8")
    )
    runtime_plan["task_id"] = candidate["candidate_id"]
    for step in runtime_plan["steps"]:
        step["argv"][0] = sys.executable
    write_json(run_dir / "runtime-plan.json", runtime_plan)
    runtime_dir = (
        run_dir / "runtime" / str(candidate["candidate_id"]) / "round-01"
    )
    run_stage(
        "07-runtime",
        [
            sys.executable,
            str(runtime_validator),
            "--plan",
            str(run_dir / "runtime-plan.json"),
            "--workspace-root",
            str(target),
            "--output-dir",
            str(runtime_dir),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    runtime_result = json.loads(
        (runtime_dir / "result.json").read_text(encoding="utf-8")
    )
    runtime_round = build_runtime_round(runtime_result, runtime_dir, run_dir)

    source_lines = (target / "limit_impl.py").read_text(encoding="utf-8").splitlines()
    code = "\n".join(source_lines[3:6])
    evidence = requirement["evidence"]
    audit_input = {
        "records": [
            {
                "record_id": candidate["candidate_id"],
                "candidate_id": candidate["candidate_id"],
                "title": "Limit value 11 is accepted above the normative maximum",
                "problem_description": (
                    "The focused boundary variant sends Limit value 11. The demo "
                    "implementation accepts it even though the standard requires "
                    "rejection for every value greater than 10."
                ),
                "verdict": "issue_found",
                "confidence": "high",
                "standard_check": {
                    "section": (
                        f"{evidence['source_id']} lines "
                        f"{evidence['line_start']}-{evidence['line_end']}"
                    ),
                    "exact_text": evidence["quote"],
                    "interpretation": (
                        f"Under {requirement['condition'] or 'the stated condition'}, "
                        f"the endpoint {requirement['required_behavior']}."
                    ),
                    "evidence_verified": evidence["verified"],
                },
                "code_check": {
                    "summary": (
                        "The reachable limit predicate uses 11 as an inclusive "
                        "maximum, one above the normative limit."
                    ),
                    "references": [{"path": "limit_impl.py", "line_start": 4}],
                    "excerpts": [
                        {
                            "path": "limit_impl.py",
                            "line_start": 4,
                            "line_end": 6,
                            "code": code,
                            "explanation": (
                                "The inclusive comparison returns true for value 11."
                            ),
                        }
                    ],
                },
                "runtime_rounds": [runtime_round],
                "decision_reason": (
                    "Verified standard evidence, a reachable source predicate, a "
                    "passing positive control, and a focused reproducer all agree."
                ),
                "inconsistency_reason": (
                    "The standard requires rejection above 10, while the code and "
                    "runtime evidence both show acceptance at 11."
                ),
                "remaining_uncertainty": "",
            }
        ]
    }
    write_json(run_dir / "audit-input.json", audit_input)
    run_stage(
        "08-report",
        [
            sys.executable,
            str(reporter),
            "--input",
            str(run_dir / "audit-input.json"),
            "--output-dir",
            str(run_dir),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    write_json(
        run_dir / "run_manifest.json",
        {
            "schema_version": "1.0",
            "max_rounds": 5,
            "standard": "demo_standard.txt",
            "standard_sha256": requirements_document["metadata"]["source_sha256"],
            "target": "demo_target",
            "target_revision": "fixture-deliberate-bug-v1",
            "selected_candidate": candidate["candidate_id"],
            "static_triage": "static_triage.json",
            "requirement_source": {
                "mode": selection_metadata["requirement_mode"],
                "requirements_sha256": selection_metadata[
                    "requirements_sha256"
                ],
                "standard_sha256": selection_metadata["standard_sha256"],
            },
            "static_triage_selection": {
                "start": selection_metadata["start"],
                "end": selection_metadata["end"],
                "batch_size": selection_metadata["batch_size"],
                "selected_requirement_count": selection_metadata[
                    "selected_requirement_count"
                ],
                "batch_count": selection_metadata["batch_count"],
                "selection_sha256": selection_metadata["selection_sha256"],
            },
        },
    )
    run_stage(
        "09-validate",
        [
            sys.executable,
            str(coordinator_validator),
            "--run-dir",
            str(run_dir),
            "--target-repo",
            str(target),
            "--out",
            str(run_dir / "validation-summary.json"),
        ],
        cwd=opt_dir,
        log_dir=log_dir,
    )
    report = assert_final_report(run_dir, str(candidate["candidate_id"]))

    summary = {
        "ok": True,
        "stages": [
            "agent_requirement_extraction",
            "independent_requirement_validation",
            "requirement_selection_and_batching",
            "static_triage_batch_merge",
            "candidate_generation",
            "runtime_validation",
            "verdict_reporting",
            "coordinator_validation",
        ],
        "requirements_extracted": len(requirements_document["requirements"]),
        "requirements_selected": selection_metadata[
            "selected_requirement_count"
        ],
        "static_triage_batches": selection_metadata["batch_count"],
        "static_triage_records": len(triage_records),
        "requirements_selected_for_generation": generated_document[
            "generation_summary"
        ]["static_triage"]["requirements_selected_for_generation"],
        "candidates_generated": len(generated_document["candidates"]),
        "candidate_executed": candidate["candidate_id"],
        "verdict": "issue_found",
        "runtime_rounds": 1,
        "problem_report": relative_posix(report, opt_dir),
        "run_dir": str(run_dir.resolve()),
    }
    write_json(run_dir / "total-test-summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the complete opt SpecLitmus workflow against a demo target."
    )
    opt_dir = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=opt_dir / "test-results" / "total-run",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    opt_dir = Path(__file__).resolve().parents[1]
    try:
        result = run_total_test(opt_dir, args.run_dir)
    except (OSError, ValueError, KeyError, StopIteration, TotalTestError) as exc:
        print(f"TOTAL TEST FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
