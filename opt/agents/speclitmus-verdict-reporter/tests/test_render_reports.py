import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "render_reports.py"
SPEC = importlib.util.spec_from_file_location("render_reports", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def base_record(record_id="record-001", verdict="issue_found"):
    record = {
        "record_id": record_id,
        "title": "Duplicate extension accepted",
        "problem_description": "The peer accepts a prohibited duplicate.",
        "verdict": verdict,
        "confidence": "high",
        "standard_check": {
            "section": "RFC 8446 §4.2",
            "exact_text": "An extension MUST NOT appear more than once.",
            "interpretation": "The receiver rejects duplicate extensions.",
            "evidence_verified": True,
        },
        "code_check": {
            "summary": "The reachable parser overwrites the earlier value.",
            "excerpts": [
                {
                    "path": "src/parser.c",
                    "line_start": 42,
                    "line_end": 44,
                    "code": "value = parse_extension(input);",
                    "explanation": "No duplicate check precedes assignment.",
                }
            ],
        },
        "runtime_rounds": [
            {
                "round": 1,
                "status": "passed",
                "positive_control_status": "passed",
                "reproducer_status": "passed",
                "summary": "Control completed; duplicate input was accepted.",
                "steps": [
                    {
                        "id": "repro",
                        "argv": ["python", "repro.py", "--duplicate"],
                        "status": "passed",
                        "exit_code": 0,
                        "stdout_log": "02-repro.stdout.log",
                        "stderr_log": "02-repro.stderr.log",
                    }
                ],
            }
        ],
        "decision_reason": "Standard, code, and runtime evidence agree.",
        "inconsistency_reason": "The implementation accepts input the standard prohibits.",
        "remaining_uncertainty": "",
    }
    if verdict == "no_issue":
        record["inconsistency_reason"] = ""
    if verdict == "suspected_issue":
        record["remaining_uncertainty"] = "The active build flags remain unknown."
    return record


class RenderReportTests(unittest.TestCase):
    def test_three_verdicts_create_complete_ledger_and_two_problem_reports(self):
        records = [
            base_record("found-001", "issue_found"),
            base_record("suspect-001", "suspected_issue"),
            base_record("clean-001", "no_issue"),
        ]
        with tempfile.TemporaryDirectory() as output_text:
            manifest = MODULE.render_outputs(records, Path(output_text))
            self.assertEqual(3, manifest["record_count"])
            self.assertEqual(2, len(manifest["problem_reports"]))
            reports = list((Path(output_text) / "reports").glob("*.md"))
            self.assertEqual(2, len(reports))
            self.assertFalse(any("clean-001" in path.name for path in reports))
            ledger = json.loads(
                (Path(output_text) / "variant_audit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {"no_issue", "suspected_issue", "issue_found"},
                {item["verdict"] for item in ledger},
            )

    def test_markdown_escapes_structure_and_preserves_exact_evidence_and_code(self):
        record = base_record()
        record["title"] = "Finding\n## injected <script>"
        record["problem_description"] = "# not a heading <unsafe>"
        record["standard_check"]["exact_text"] = "MUST reject <tag>\n> exact second line"
        record["code_check"]["excerpts"][0]["code"] = "const char *x = \"```\";"
        with tempfile.TemporaryDirectory() as output_text:
            manifest = MODULE.render_outputs([record], Path(output_text))
            report = (
                Path(output_text) / manifest["problem_reports"][0]
            ).read_text(encoding="utf-8")
            self.assertIn("# Finding \\#\\# injected &lt;script&gt;", report)
            self.assertNotIn("\n## injected", report)
            self.assertIn("> MUST reject &lt;tag&gt;", report)
            self.assertIn("> &gt; exact second line", report)
            self.assertIn('const char *x = "```";', report)
            self.assertIn("````\nconst char", report)
            self.assertIn("`src/parser.c:42-44`", report)

    def test_invalid_verdict_is_rejected_before_output_creation(self):
        record = base_record()
        record["verdict"] = "possible_violation"
        with tempfile.TemporaryDirectory() as temp_text:
            output = Path(temp_text) / "output"
            with self.assertRaises(MODULE.RecordError):
                MODULE.render_outputs([record], output)
            self.assertFalse(output.exists())

    def test_suspected_issue_requires_remaining_uncertainty(self):
        record = base_record(verdict="suspected_issue")
        record["remaining_uncertainty"] = ""
        with self.assertRaises(MODULE.RecordError):
            MODULE._normalize_records([record])

    def test_definitive_verdict_requires_a_valid_controlled_round(self):
        record = base_record()
        record["runtime_rounds"][0]["positive_control_status"] = "failed"
        record["runtime_rounds"][0]["reproducer_status"] = "not_run"
        with self.assertRaises(MODULE.RecordError):
            MODULE._normalize_records([record])

    def test_cli_accepts_records_wrapper(self):
        with tempfile.TemporaryDirectory() as temp_text:
            temp = Path(temp_text)
            input_path = temp / "records.json"
            output = temp / "out"
            input_path.write_text(
                json.dumps({"records": [deepcopy(base_record())]}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(input_path),
                    "--output-dir",
                    str(output),
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((output / "variant_audit.md").exists())
            self.assertTrue((output / "reports").is_dir())

    def test_shared_root_cause_key_creates_one_problem_report(self):
        found = base_record("found-001", "issue_found")
        suspect = base_record("suspect-001", "suspected_issue")
        found["root_cause_key"] = "shared-root-cause"
        suspect["root_cause_key"] = "shared-root-cause"
        suspect["remaining_uncertainty"] = "Need one more platform check."
        with tempfile.TemporaryDirectory() as output_text:
            manifest = MODULE.render_outputs([found, suspect], Path(output_text))
            self.assertEqual(2, manifest["record_count"])
            self.assertEqual(2, manifest["problem_record_count"])
            self.assertEqual(1, manifest["problem_report_count"])
            self.assertEqual(1, len(manifest["problem_reports"]))
            report = (
                Path(output_text) / manifest["problem_reports"][0]
            ).read_text(encoding="utf-8")
            self.assertIn("found-001", report)
            self.assertIn("suspect-001", report)
            self.assertIn("shared-root-cause", report)


if __name__ == "__main__":
    unittest.main()
