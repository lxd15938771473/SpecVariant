import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPORTER_ROOT = Path(__file__).parents[1]
VALIDATOR_ROOT = REPORTER_ROOT.parent / "speclitmus-implementation-validator"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_module(
    "integration_runtime_plan",
    VALIDATOR_ROOT / "scripts" / "run_runtime_plan.py",
)
REPORTER = load_module(
    "integration_reporter",
    REPORTER_ROOT / "scripts" / "render_reports.py",
)


class ValidationReportingIntegrationTests(unittest.TestCase):
    def test_runtime_result_flows_into_markdown_problem_report(self):
        plan = {
            "schema_version": "1.0",
            "task_id": "integration-001",
            "round": 1,
            "remaining_uncertainty": "Whether the mutated value is accepted",
            "competing_hypotheses": [
                "The implementation rejects it",
                "The implementation accepts it",
            ],
            "distinguishing_observation": "The reproducer's observable output",
            "difference_from_previous": "First minimal experiment",
            "steps": [
                {
                    "id": "control",
                    "role": "positive_control",
                    "argv": [sys.executable, "-c", "print('valid accepted')"],
                },
                {
                    "id": "repro",
                    "role": "reproducer",
                    "argv": [sys.executable, "-c", "print('invalid accepted')"],
                },
            ],
        }
        with (
            tempfile.TemporaryDirectory() as workspace_text,
            tempfile.TemporaryDirectory() as round_text,
            tempfile.TemporaryDirectory() as report_text,
        ):
            runtime = VALIDATOR.execute_plan(
                plan, Path(workspace_text), Path(round_text)
            )
            runtime["summary"] = (
                "The positive control and prohibited mutation were both accepted."
            )
            record = {
                "record_id": "integration-001",
                "title": "Prohibited mutation is accepted",
                "problem_description": "The target does not reject the mutation.",
                "verdict": "issue_found",
                "confidence": "high",
                "standard_check": {
                    "section": "Example Standard §1",
                    "exact_text": "An endpoint MUST reject the prohibited mutation.",
                    "interpretation": "The mutated case must fail.",
                    "evidence_verified": True,
                },
                "code_check": {
                    "summary": "The reachable branch accepts both values.",
                    "excerpts": [
                        {
                            "path": "src/example.c",
                            "line_start": 10,
                            "line_end": 12,
                            "code": "return ACCEPT;",
                            "explanation": "No rejection check is present.",
                        }
                    ],
                },
                "runtime_rounds": [runtime],
                "decision_reason": "All three evidence sources agree.",
                "inconsistency_reason": (
                    "The implementation accepts a value the standard requires it to reject."
                ),
                "remaining_uncertainty": "",
            }
            manifest = REPORTER.render_outputs([record], Path(report_text))
            report = (
                Path(report_text) / manifest["problem_reports"][0]
            ).read_text(encoding="utf-8")
            self.assertEqual("passed", runtime["positive_control_status"])
            self.assertEqual("passed", runtime["reproducer_status"])
            self.assertIn("## Runtime Evidence", report)
            self.assertIn("positive control and prohibited mutation", report)
            self.assertIn("src/example.c:10-12", report)


if __name__ == "__main__":
    unittest.main()
