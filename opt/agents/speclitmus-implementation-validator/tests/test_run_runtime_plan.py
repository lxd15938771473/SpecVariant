import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_runtime_plan.py"
SPEC = importlib.util.spec_from_file_location("run_runtime_plan", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def plan_for(*steps):
    return {
        "schema_version": "1.0",
        "task_id": "task-001",
        "round": 1,
        "remaining_uncertainty": "Whether mutated input is accepted",
        "competing_hypotheses": ["it is rejected", "it is accepted"],
        "distinguishing_observation": "Exit status and protocol response",
        "difference_from_previous": "First focused attempt",
        "steps": list(steps),
    }


def py_step(step_id, role, source, **extra):
    result = {
        "id": step_id,
        "role": role,
        "argv": [sys.executable, "-c", source],
        "timeout_seconds": 5,
    }
    result.update(extra)
    return result


class RuntimePlanTests(unittest.TestCase):
    def test_pass_captures_control_reproducer_and_environment(self):
        plan = plan_for(
            py_step("control", "positive_control", "print('valid')"),
            py_step(
                "repro",
                "reproducer",
                "import os; print(os.environ['SPEC_TEST_VALUE'])",
                env={"SPEC_TEST_VALUE": "mutated"},
            ),
        )
        with tempfile.TemporaryDirectory() as root_text, tempfile.TemporaryDirectory() as out_text:
            result = MODULE.execute_plan(plan, Path(root_text), Path(out_text))
            self.assertEqual("passed", result["status"])
            self.assertEqual("passed", result["positive_control_status"])
            self.assertEqual("passed", result["reproducer_status"])
            self.assertIn("mutated", (Path(out_text) / "stdout.log").read_text())
            environment = json.loads((Path(out_text) / "environment.json").read_text())
            self.assertEqual(
                {"SPEC_TEST_VALUE": "mutated"},
                environment["environment_overrides"]["repro"],
            )

    def test_failure_stops_before_reproducer(self):
        plan = plan_for(
            py_step("control", "positive_control", "raise SystemExit(9)"),
            py_step("repro", "reproducer", "print('must-not-run')"),
        )
        with tempfile.TemporaryDirectory() as root_text, tempfile.TemporaryDirectory() as out_text:
            result = MODULE.execute_plan(plan, Path(root_text), Path(out_text))
            self.assertEqual("failed", result["status"])
            self.assertEqual("failed", result["positive_control_status"])
            self.assertEqual("not_run", result["reproducer_status"])
            self.assertNotIn("must-not-run", (Path(out_text) / "stdout.log").read_text())

    def test_timeout_is_recorded(self):
        plan = plan_for(
            py_step(
                "control",
                "positive_control",
                "import time; time.sleep(2)",
                timeout_seconds=1,
            ),
            py_step("repro", "reproducer", "print('not-run')"),
        )
        with tempfile.TemporaryDirectory() as root_text, tempfile.TemporaryDirectory() as out_text:
            result = MODULE.execute_plan(plan, Path(root_text), Path(out_text))
            self.assertEqual("timeout", result["status"])
            self.assertIsNone(result["steps"][0]["exit_code"])
            self.assertTrue((Path(out_text) / "01-control.stderr.log").exists())

    def test_traversal_and_shell_strings_are_rejected(self):
        bad_cwd = plan_for(
            py_step("control", "positive_control", "pass", cwd="../escape"),
            py_step("repro", "reproducer", "pass"),
        )
        with self.assertRaises(MODULE.PlanError):
            MODULE.validate_plan(bad_cwd)

        bad_argv = plan_for(
            {
                "id": "control",
                "role": "positive_control",
                "argv": f"{sys.executable} -c pass",
            },
            py_step("repro", "reproducer", "pass"),
        )
        with self.assertRaises(MODULE.PlanError):
            MODULE.validate_plan(bad_argv)

        late_control = plan_for(
            py_step("repro", "reproducer", "pass"),
            py_step("control", "positive_control", "pass"),
        )
        with self.assertRaises(MODULE.PlanError):
            MODULE.validate_plan(late_control)

        excessive_timeout = plan_for(
            py_step(
                "control",
                "positive_control",
                "pass",
                timeout_seconds=601,
            ),
            py_step("repro", "reproducer", "pass"),
        )
        with self.assertRaises(MODULE.PlanError):
            MODULE.validate_plan(excessive_timeout)

    def test_cli_returns_nonzero_for_malformed_json(self):
        with tempfile.TemporaryDirectory() as temp_text:
            temp = Path(temp_text)
            plan_path = temp / "bad.json"
            plan_path.write_text("{", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--plan",
                    str(plan_path),
                    "--workspace-root",
                    str(temp),
                    "--output-dir",
                    str(temp / "out"),
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertIn("error:", completed.stderr)


if __name__ == "__main__":
    unittest.main()
