from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from validate_run import validate_run


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def static_record(
    requirement_id: str,
    verdict: str,
    quote: str,
    line: int,
    *,
    no_excerpt: bool = False,
) -> dict[str, object]:
    record: dict[str, object] = {
        "requirement_id": requirement_id,
        "verdict": verdict,
        "confidence": "high",
        "standard_evidence": {
            "quote": quote,
            "line_start": line,
            "line_end": line,
            "verified": True,
        },
        "code_check": {
            "summary": "The reachable check path was inspected.",
            "excerpts": []
            if no_excerpt
            else [
                {
                    "path": "impl.py",
                    "line_start": 1,
                    "line_end": 2,
                    "code": "def check(value):\n    return value <= 10",
                    "explanation": "This is the reachable validation predicate.",
                }
            ],
        },
        "decision_reason": "Static inspection completed.",
        "inconsistency_reason": "",
        "remaining_uncertainty": "",
        "generation_action": "skip" if verdict == "no_issue" else "generate_variants",
    }
    if verdict == "suspected_issue":
        record["inconsistency_reason"] = "The mapping remains ambiguous."
        record["remaining_uncertainty"] = "Need a generated variant to distinguish behavior."
    elif verdict == "issue_found":
        record["inconsistency_reason"] = "The implementation appears inconsistent."
    return record


class ValidateRunTests(unittest.TestCase):
    def make_valid_run(self, root: Path) -> tuple[Path, Path]:
        run_dir = root / "run"
        target = root / "target"
        target.mkdir()
        (target / "impl.py").write_text("def check(value):\n    return value <= 10\n", encoding="utf-8")
        runtime_log = run_dir / "tasks" / "c1" / "rounds" / "01" / "stdout.log"
        runtime_log.parent.mkdir(parents=True)
        runtime_log.write_text("violation observed\n", encoding="utf-8")
        write_json(run_dir / "run_manifest.json", {"max_rounds": 5})
        write_json(
            run_dir / "requirements.json",
            {
                "metadata": {"chunk_count": 1, "processed_chunk_count": 1},
                "requirements": [
                    {
                        "requirement_id": "r1",
                        "evidence": {
                            "quote": "MUST reject values greater than 10",
                            "line_start": 1,
                            "line_end": 1,
                            "verified": True,
                        },
                    }
                ],
            },
        )
        write_json(
            run_dir / "candidates.json",
            {"candidates": [{"candidate_id": "c1", "requirement_id": "r1"}]},
        )
        write_json(
            run_dir / "variant_audit.json",
            {
                "records": [
                    {
                        "candidate_id": "c1",
                        "verdict": "issue_found",
                        "code_check": {
                            "references": [{"file": "impl.py", "line": 1}]
                        },
                        "runtime_rounds": [
                            {"artifacts": ["tasks/c1/rounds/01/stdout.log"]}
                        ],
                    }
                ]
            },
        )
        reports = run_dir / "reports"
        reports.mkdir()
        (reports / "limit.md").write_text(
            "# Limit validation\n\n"
            "Candidate: c1\n\n"
            "## Problem Description\n\nIssue.\n\n"
            "## Standard Requirement\n\nText.\n\n"
            "## Relevant Source Code\n\nCode.\n\n"
            "## Runtime Evidence\n\nResult.\n\n"
            "## Inconsistency Reason\n\nMismatch.\n",
            encoding="utf-8",
        )
        return run_dir, target

    def test_valid_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            result = validate_run(run_dir, target_repo=target)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["counts"]["problem_reports"], 1)

    def test_unverified_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            data = json.loads((run_dir / "requirements.json").read_text(encoding="utf-8"))
            data["requirements"][0]["evidence"]["verified"] = False
            write_json(run_dir / "requirements.json", data)
            result = validate_run(run_dir, target_repo=target)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("not fully verified" in error for error in result["errors"])
            )

    def test_round_limit_and_missing_report_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            data = json.loads((run_dir / "variant_audit.json").read_text(encoding="utf-8"))
            data["records"][0]["runtime_rounds"] = [{} for _ in range(6)]
            write_json(run_dir / "variant_audit.json", data)
            (run_dir / "reports" / "limit.md").unlink()
            result = validate_run(run_dir, target_repo=target)
            self.assertFalse(result["ok"])
            self.assertTrue(any("exceed max" in error for error in result["errors"]))
            self.assertTrue(any("no Markdown" in error for error in result["errors"]))

    def test_escaping_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir, target = self.make_valid_run(root)
            outside = root / "outside.py"
            outside.write_text("print('outside')\n", encoding="utf-8")
            data = json.loads((run_dir / "variant_audit.json").read_text(encoding="utf-8"))
            data["records"][0]["code_check"]["references"] = [
                {"file": str(outside), "line": 1}
            ]
            data["records"][0]["runtime_rounds"][0]["artifacts"] = ["../outside.log"]
            write_json(run_dir / "variant_audit.json", data)
            result = validate_run(run_dir, target_repo=target)
            self.assertFalse(result["ok"])
            self.assertTrue(any("escapes target repo" in e for e in result["errors"]))
            self.assertTrue(any("escapes run directory" in e for e in result["errors"]))

    def test_reporter_record_id_can_identify_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            path = run_dir / "variant_audit.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            record = data["records"][0]
            record["record_id"] = record.pop("candidate_id")
            write_json(path, data)

            result = validate_run(run_dir, target_repo=target)

            self.assertTrue(result["ok"], result["errors"])

    def test_static_triage_gate_accepts_skipped_no_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            requirements_path = run_dir / "requirements.json"
            requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
            requirements["requirements"].append(
                {
                    "requirement_id": "r2",
                    "evidence": {
                        "quote": "MUST include a supported value",
                        "line_start": 2,
                        "line_end": 2,
                        "verified": True,
                    },
                }
            )
            write_json(requirements_path, requirements)
            write_json(
                run_dir / "static_triage.json",
                {
                    "schema_version": "speclitmus.static-triage.v1",
                    "records": [
                        static_record(
                            "r1",
                            "issue_found",
                            "MUST reject values greater than 10",
                            1,
                        ),
                        static_record(
                            "r2",
                            "no_issue",
                            "MUST include a supported value",
                            2,
                        ),
                    ],
                },
            )

            result = validate_run(run_dir, target_repo=target)

            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["counts"]["static_triage_records"], 2)

    def test_static_triage_rejects_uncovered_suspect_and_generated_no_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            requirements_path = run_dir / "requirements.json"
            requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
            requirements["requirements"].extend(
                [
                    {
                        "requirement_id": "r2",
                        "evidence": {
                            "quote": "MUST include a supported value",
                            "line_start": 2,
                            "line_end": 2,
                            "verified": True,
                        },
                    },
                    {
                        "requirement_id": "r3",
                        "evidence": {
                            "quote": "MUST reject an invalid value",
                            "line_start": 3,
                            "line_end": 3,
                            "verified": True,
                        },
                    },
                ]
            )
            write_json(requirements_path, requirements)
            candidates_path = run_dir / "candidates.json"
            candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
            candidates["candidates"].append(
                {"candidate_id": "c2", "requirement_id": "r2"}
            )
            write_json(candidates_path, candidates)
            write_json(
                run_dir / "static_triage.json",
                {
                    "schema_version": "speclitmus.static-triage.v1",
                    "records": [
                        static_record(
                            "r1",
                            "issue_found",
                            "MUST reject values greater than 10",
                            1,
                        ),
                        static_record(
                            "r2",
                            "no_issue",
                            "MUST include a supported value",
                            2,
                        ),
                        static_record(
                            "r3",
                            "suspected_issue",
                            "MUST reject an invalid value",
                            3,
                        ),
                    ],
                },
            )

            result = validate_run(run_dir, target_repo=target)

            self.assertFalse(result["ok"])
            self.assertTrue(
                any("static no_issue requirements generated" in e for e in result["errors"])
            )
            self.assertTrue(
                any("without generated candidates" in e for e in result["errors"])
            )

    def test_static_no_issue_requires_code_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            requirements_path = run_dir / "requirements.json"
            requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
            requirements["requirements"].append(
                {
                    "requirement_id": "r2",
                    "evidence": {
                        "quote": "MUST include a supported value",
                        "line_start": 2,
                        "line_end": 2,
                        "verified": True,
                    },
                }
            )
            write_json(requirements_path, requirements)
            write_json(
                run_dir / "static_triage.json",
                {
                    "schema_version": "speclitmus.static-triage.v1",
                    "records": [
                        static_record(
                            "r1",
                            "issue_found",
                            "MUST reject values greater than 10",
                            1,
                        ),
                        static_record(
                            "r2",
                            "no_issue",
                            "MUST include a supported value",
                            2,
                            no_excerpt=True,
                        ),
                    ],
                },
            )

            result = validate_run(run_dir, target_repo=target)

            self.assertFalse(result["ok"])
            self.assertTrue(
                any("static no_issue requires code_check.excerpts" in e for e in result["errors"])
            )

    def test_selected_run_validates_only_exact_interval_and_batch_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, target = self.make_valid_run(Path(tmp))
            requirements_path = run_dir / "requirements.json"
            requirements_document = json.loads(
                requirements_path.read_text(encoding="utf-8")
            )
            requirement = requirements_document["requirements"][0]
            requirements_sha = hashlib.sha256(requirements_path.read_bytes()).hexdigest()
            selected_ids = ["r1"]
            selection_sha = hashlib.sha256(
                json.dumps(
                    selected_ids, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            input_rel = "static-triage-batches/batch-0001.input.json"
            output_rel = "static-triage-batches/batch-0001.output.json"
            selection = {
                "schema_version": "speclitmus.requirement-selection.v1",
                "metadata": {
                    "requirement_mode": "reuse",
                    "standard_sha256": "standard-sha",
                    "requirements_sha256": requirements_sha,
                    "total_requirement_count": 1,
                    "total_eligible_requirement_count": 1,
                    "start": 1,
                    "end": 1,
                    "batch_size": 1,
                    "selected_requirement_count": 1,
                    "batch_count": 1,
                    "selection_sha256": selection_sha,
                },
                "requirements": [requirement],
                "batches": [
                    {
                        "batch_id": "batch-0001",
                        "position_start": 1,
                        "position_end": 1,
                        "requirement_count": 1,
                        "requirement_ids": selected_ids,
                        "input": input_rel,
                        "output": output_rel,
                    }
                ],
            }
            write_json(run_dir / "requirement_selection.json", selection)
            write_json(
                run_dir / input_rel,
                {
                    "schema_version": "speclitmus.static-triage-batch.v1",
                    "metadata": {
                        "batch_id": "batch-0001",
                        "selection_sha256": selection_sha,
                        "requirements_sha256": requirements_sha,
                        "position_start": 1,
                        "position_end": 1,
                        "requirement_count": 1,
                        "expected_output": output_rel,
                    },
                    "requirements": [requirement],
                },
            )
            record = static_record(
                "r1",
                "issue_found",
                "MUST reject values greater than 10",
                1,
            )
            write_json(
                run_dir / output_rel,
                {
                    "schema_version": "speclitmus.static-triage.v1",
                    "metadata": {
                        "batch_id": "batch-0001",
                        "selection_sha256": selection_sha,
                    },
                    "records": [record],
                },
            )
            write_json(
                run_dir / "static_triage.json",
                {
                    "schema_version": "speclitmus.static-triage.v1",
                    "metadata": {
                        "selection_sha256": selection_sha,
                        "requirements_sha256": requirements_sha,
                    },
                    "records": [record],
                },
            )
            manifest = json.loads(
                (run_dir / "run_manifest.json").read_text(encoding="utf-8")
            )
            manifest["requirement_source"] = {
                "mode": "reuse",
                "requirements_sha256": requirements_sha,
                "standard_sha256": "standard-sha",
            }
            manifest["static_triage_selection"] = {
                "start": 1,
                "end": 1,
                "batch_size": 1,
                "selected_requirement_count": 1,
                "batch_count": 1,
                "selection_sha256": selection_sha,
            }
            write_json(run_dir / "run_manifest.json", manifest)

            result = validate_run(run_dir, target_repo=target)

            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["counts"]["selected_requirements"], 1)

            (run_dir / output_rel).unlink()
            result = validate_run(run_dir, target_repo=target)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("missing output artifact" in error for error in result["errors"])
            )


if __name__ == "__main__":
    unittest.main()
