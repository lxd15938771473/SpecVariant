#!/usr/bin/env python3
"""Execute a validated SpecLitmus runtime plan without invoking a shell."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
ALLOWED_ROLES = {
    "setup",
    "build",
    "positive_control",
    "reproducer",
    "diagnostic",
}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PlanError(ValueError):
    """Raised when a runtime plan violates the contract."""


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{field} must be a non-empty string")
    if "\x00" in value:
        raise PlanError(f"{field} must not contain NUL")
    return value


def _relative_directory(value: Any, field: str) -> str:
    text = _nonempty_string(value, field)
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PlanError(f"{field} must be a relative contained path")
    return text


def validate_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlanError("plan must be a JSON object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise PlanError(f"schema_version must be {SCHEMA_VERSION!r}")

    task_id = _nonempty_string(raw.get("task_id"), "task_id")
    if not ID_RE.fullmatch(task_id):
        raise PlanError("task_id contains unsupported characters")

    round_number = raw.get("round")
    if isinstance(round_number, bool) or not isinstance(round_number, int):
        raise PlanError("round must be an integer")
    if not 1 <= round_number <= 5:
        raise PlanError("round must be between 1 and 5")

    for field in (
        "remaining_uncertainty",
        "distinguishing_observation",
        "difference_from_previous",
    ):
        _nonempty_string(raw.get(field), field)

    hypotheses = raw.get("competing_hypotheses")
    if (
        not isinstance(hypotheses, list)
        or len(hypotheses) < 2
        or any(not isinstance(item, str) or not item.strip() for item in hypotheses)
    ):
        raise PlanError("competing_hypotheses must contain at least two strings")

    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanError("steps must be a non-empty array")

    seen_ids: set[str] = set()
    roles: set[str] = set()
    normalized_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        prefix = f"steps[{index}]"
        if not isinstance(step, dict):
            raise PlanError(f"{prefix} must be an object")
        step_id = _nonempty_string(step.get("id"), f"{prefix}.id")
        if not ID_RE.fullmatch(step_id) or step_id in seen_ids:
            raise PlanError(f"{prefix}.id must be unique and filename-safe")
        seen_ids.add(step_id)

        role = step.get("role")
        if role not in ALLOWED_ROLES:
            raise PlanError(f"{prefix}.role is not supported")
        roles.add(role)

        argv = step.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
        ):
            raise PlanError(f"{prefix}.argv must be a non-empty string array")

        cwd = _relative_directory(step.get("cwd", "."), f"{prefix}.cwd")
        timeout = step.get("timeout_seconds", 60)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 1 <= timeout <= 600
        ):
            raise PlanError(f"{prefix}.timeout_seconds must be in [1, 600]")

        env = step.get("env", {})
        if not isinstance(env, dict):
            raise PlanError(f"{prefix}.env must be an object")
        for key, value in env.items():
            if not isinstance(key, str) or not ENV_RE.fullmatch(key):
                raise PlanError(f"{prefix}.env contains an invalid name")
            if not isinstance(value, str) or "\x00" in value:
                raise PlanError(f"{prefix}.env values must be strings without NUL")

        expected = step.get("expected_exit_codes", [0])
        if (
            not isinstance(expected, list)
            or not expected
            or any(isinstance(code, bool) or not isinstance(code, int) for code in expected)
        ):
            raise PlanError(f"{prefix}.expected_exit_codes must be an integer array")

        continue_on_failure = step.get("continue_on_failure", False)
        if not isinstance(continue_on_failure, bool):
            raise PlanError(f"{prefix}.continue_on_failure must be boolean")

        normalized_steps.append(
            {
                "id": step_id,
                "role": role,
                "argv": list(argv),
                "cwd": cwd,
                "timeout_seconds": float(timeout),
                "env": dict(env),
                "expected_exit_codes": list(expected),
                "continue_on_failure": continue_on_failure,
            }
        )

    missing = {"positive_control", "reproducer"} - roles
    if missing:
        raise PlanError("steps must include roles: " + ", ".join(sorted(missing)))
    first_control = next(
        index
        for index, step in enumerate(normalized_steps)
        if step["role"] == "positive_control"
    )
    first_reproducer = next(
        index
        for index, step in enumerate(normalized_steps)
        if step["role"] == "reproducer"
    )
    if first_control > first_reproducer:
        raise PlanError("a positive_control step must precede the reproducer")

    normalized = dict(raw)
    normalized["steps"] = normalized_steps
    return normalized


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_cwd(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve(strict=True)
    if not candidate.is_dir():
        raise PlanError(f"step cwd is not a directory: {relative}")
    if not _is_within(candidate, root):
        raise PlanError(f"step cwd resolves outside workspace root: {relative}")
    return candidate


def _decode(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def execute_plan(
    plan: dict[str, Any], workspace_root: Path, output_dir: Path
) -> dict[str, Any]:
    plan = validate_plan(plan)
    root = workspace_root.resolve(strict=True)
    if not root.is_dir():
        raise PlanError("workspace root must be a directory")

    output_dir.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(
        plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    environment = {
        "platform": platform.platform(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "workspace_root": str(root),
        "plan_sha256": hashlib.sha256(canonical).hexdigest(),
        "environment_overrides": {
            step["id"]: step["env"] for step in plan["steps"] if step["env"]
        },
    }
    _write_json(output_dir / "environment.json", environment)

    results: list[dict[str, Any]] = []
    aggregate_stdout: list[str] = []
    aggregate_stderr: list[str] = []

    for index, step in enumerate(plan["steps"], start=1):
        cwd = _resolve_cwd(root, step["cwd"])
        env = os.environ.copy()
        env.update(step["env"])
        started = time.monotonic()
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        status = "failed"
        try:
            completed = subprocess.run(
                step["argv"],
                cwd=str(cwd),
                env=env,
                shell=False,
                capture_output=True,
                timeout=step["timeout_seconds"],
                check=False,
            )
            stdout = _decode(completed.stdout)
            stderr = _decode(completed.stderr)
            exit_code = completed.returncode
            status = (
                "passed"
                if exit_code in step["expected_exit_codes"]
                else "failed"
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _decode(exc.stdout)
            stderr = _decode(exc.stderr)
            status = "timeout"
        except (FileNotFoundError, PermissionError, OSError) as exc:
            stderr = f"{type(exc).__name__}: {exc}\n"
            status = "launch_error"

        duration_ms = round((time.monotonic() - started) * 1000, 3)
        stem = f"{index:02d}-{step['id']}"
        _write_text(output_dir / f"{stem}.stdout.log", stdout)
        _write_text(output_dir / f"{stem}.stderr.log", stderr)
        aggregate_stdout.append(f"===== {step['id']} =====\n{stdout}")
        aggregate_stderr.append(f"===== {step['id']} =====\n{stderr}")
        results.append(
            {
                "id": step["id"],
                "role": step["role"],
                "argv": step["argv"],
                "cwd": str(cwd),
                "timeout_seconds": step["timeout_seconds"],
                "expected_exit_codes": step["expected_exit_codes"],
                "exit_code": exit_code,
                "status": status,
                "duration_ms": duration_ms,
                "stdout_log": f"{stem}.stdout.log",
                "stderr_log": f"{stem}.stderr.log",
            }
        )
        if status != "passed" and not step["continue_on_failure"]:
            break

    _write_text(output_dir / "stdout.log", "\n".join(aggregate_stdout))
    _write_text(output_dir / "stderr.log", "\n".join(aggregate_stderr))

    statuses = {item["status"] for item in results}
    if "timeout" in statuses:
        overall_status = "timeout"
    elif "launch_error" in statuses:
        overall_status = "launch_error"
    elif "failed" in statuses:
        overall_status = "failed"
    elif len(results) != len(plan["steps"]):
        overall_status = "incomplete"
    else:
        overall_status = "passed"

    def role_status(role: str) -> str:
        matching = [item["status"] for item in results if item["role"] == role]
        if not matching:
            return "not_run"
        for adverse in ("timeout", "launch_error", "failed"):
            if adverse in matching:
                return adverse
        return "passed"

    result = {
        "schema_version": SCHEMA_VERSION,
        "task_id": plan["task_id"],
        "round": plan["round"],
        "remaining_uncertainty": plan["remaining_uncertainty"],
        "competing_hypotheses": plan["competing_hypotheses"],
        "distinguishing_observation": plan["distinguishing_observation"],
        "difference_from_previous": plan["difference_from_previous"],
        "status": overall_status,
        "positive_control_status": role_status("positive_control"),
        "reproducer_status": role_status("reproducer"),
        "environment_file": "environment.json",
        "steps": results,
    }
    _write_json(output_dir / "result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute a bounded SpecLitmus runtime plan without a shell."
    )
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = json.loads(args.plan.read_text(encoding="utf-8"))
        result = execute_plan(raw, args.workspace_root, args.output_dir)
    except (OSError, json.JSONDecodeError, PlanError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
