#!/usr/bin/env python3
"""Run every opt Agent's self-tests and persist machine-readable evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_tests(agents_dir: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for skill_dir in sorted(path for path in agents_dir.iterdir() if path.is_dir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        tests_dir = skill_dir / "tests"
        if not tests_dir.is_dir():
            results.append(
                {
                    "skill": skill_dir.name,
                    "status": "fail",
                    "reason": "missing tests directory",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
            )
            continue
        command = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ]
        completed = subprocess.run(
            command,
            cwd=str(skill_dir),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=180,
            shell=False,
        )
        results.append(
            {
                "skill": skill_dir.name,
                "status": "pass" if completed.returncode == 0 else "fail",
                "exit_code": completed.returncode,
                "command": command,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "agents_dir": str(agents_dir.resolve()),
        "ok": bool(results) and all(row["status"] == "pass" for row in results),
        "skills_tested": len(results),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all opt Agent self-tests.")
    default_agents = Path(__file__).resolve().parents[1] / "agents"
    default_out = Path(__file__).resolve().parents[1] / "test-results" / "self-tests.json"
    parser.add_argument("--agents-dir", type=Path, default=default_agents)
    parser.add_argument("--out", type=Path, default=default_out)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_tests(args.agents_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": result["ok"],
                "skills_tested": result["skills_tested"],
                "statuses": {
                    row["skill"]: row["status"] for row in result["results"]
                },
                "result_file": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
