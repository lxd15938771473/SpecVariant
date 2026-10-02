#!/usr/bin/env python3
"""Validate audit records and render ledgers plus Markdown problem reports."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


VERDICTS = {"no_issue", "suspected_issue", "issue_found"}
CONFIDENCES = {"low", "medium", "high"}
RUNTIME_STATUSES = {
    "passed",
    "failed",
    "timeout",
    "launch_error",
    "incomplete",
    "blocked",
}
ROLE_STATUSES = {"passed", "failed", "timeout", "launch_error", "not_run"}
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]{}()#+.!|>~-])")


class RecordError(ValueError):
    """Raised when an audit record violates the reporting contract."""


def _required_string(obj: dict[str, Any], key: str, context: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"{context}.{key} must be a non-empty string")
    if "\x00" in value:
        raise RecordError(f"{context}.{key} must not contain NUL")
    return value


def _optional_string(obj: dict[str, Any], key: str, context: str) -> str:
    value = obj.get(key, "")
    if not isinstance(value, str) or "\x00" in value:
        raise RecordError(f"{context}.{key} must be a string without NUL")
    return value


def _normalize_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("records")
    if not isinstance(raw, list):
        raise RecordError("input must be an array or an object containing records")
    if not raw:
        raise RecordError("records must not be empty")
    return [_validate_record(item, index) for index, item in enumerate(raw)]


def _validate_record(raw: Any, index: int) -> dict[str, Any]:
    context = f"records[{index}]"
    if not isinstance(raw, dict):
        raise RecordError(f"{context} must be an object")
    record = dict(raw)
    record_id = record.get("record_id", record.get("task_id"))
    if not isinstance(record_id, str) or not SAFE_ID_RE.fullmatch(record_id):
        raise RecordError(f"{context}.record_id must be filename-safe")
    record["record_id"] = record_id
    root_cause_key = record.get("root_cause_key", "")
    if root_cause_key not in ("", None):
        if not isinstance(root_cause_key, str) or not SAFE_ID_RE.fullmatch(
            root_cause_key
        ):
            raise RecordError(f"{context}.root_cause_key must be filename-safe")
        record["root_cause_key"] = root_cause_key
    else:
        record["root_cause_key"] = ""

    for key in ("title", "problem_description", "decision_reason"):
        _required_string(record, key, context)
    verdict = record.get("verdict")
    if verdict not in VERDICTS:
        raise RecordError(
            f"{context}.verdict must be exactly one of {sorted(VERDICTS)}"
        )
    confidence = record.get("confidence")
    if confidence not in CONFIDENCES:
        raise RecordError(
            f"{context}.confidence must be exactly one of {sorted(CONFIDENCES)}"
        )

    standard = record.get("standard_check")
    if not isinstance(standard, dict):
        raise RecordError(f"{context}.standard_check must be an object")
    for key in ("section", "exact_text", "interpretation"):
        _required_string(standard, key, f"{context}.standard_check")
    if standard.get("evidence_verified") is not True:
        raise RecordError(f"{context}.standard_check.evidence_verified must be true")

    code_check = record.get("code_check")
    if not isinstance(code_check, dict):
        raise RecordError(f"{context}.code_check must be an object")
    _required_string(code_check, "summary", f"{context}.code_check")
    excerpts = code_check.get("excerpts")
    if not isinstance(excerpts, list) or not excerpts:
        raise RecordError(f"{context}.code_check.excerpts must not be empty")
    for excerpt_index, excerpt in enumerate(excerpts):
        excerpt_context = f"{context}.code_check.excerpts[{excerpt_index}]"
        if not isinstance(excerpt, dict):
            raise RecordError(f"{excerpt_context} must be an object")
        for key in ("path", "code", "explanation"):
            _required_string(excerpt, key, excerpt_context)
        start = excerpt.get("line_start")
        end = excerpt.get("line_end")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            raise RecordError(f"{excerpt_context} has an invalid line interval")

    rounds = record.get("runtime_rounds")
    if not isinstance(rounds, list) or not 1 <= len(rounds) <= 5:
        raise RecordError(f"{context}.runtime_rounds must contain 1 through 5 rounds")
    round_numbers: set[int] = set()
    valid_runtime_observed = False
    for round_index, runtime_round in enumerate(rounds):
        round_context = f"{context}.runtime_rounds[{round_index}]"
        if not isinstance(runtime_round, dict):
            raise RecordError(f"{round_context} must be an object")
        number = runtime_round.get("round")
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not 1 <= number <= 5
            or number in round_numbers
        ):
            raise RecordError(f"{round_context}.round must be unique and in [1, 5]")
        round_numbers.add(number)
        if runtime_round.get("status") not in RUNTIME_STATUSES:
            raise RecordError(f"{round_context}.status is unsupported")
        _required_string(runtime_round, "summary", round_context)
        for status_field in ("positive_control_status", "reproducer_status"):
            if runtime_round.get(status_field) not in ROLE_STATUSES:
                raise RecordError(f"{round_context}.{status_field} is unsupported")
        if (
            runtime_round["positive_control_status"] == "passed"
            and runtime_round["reproducer_status"] == "passed"
        ):
            valid_runtime_observed = True
        steps = runtime_round.get("steps", [])
        if not isinstance(steps, list):
            raise RecordError(f"{round_context}.steps must be an array")

    inconsistency = _optional_string(record, "inconsistency_reason", context)
    uncertainty = _optional_string(record, "remaining_uncertainty", context)
    if verdict in {"issue_found", "suspected_issue"} and not inconsistency.strip():
        raise RecordError(f"{context}.inconsistency_reason is required for problems")
    if verdict == "suspected_issue" and not uncertainty.strip():
        raise RecordError(
            f"{context}.remaining_uncertainty is required for suspected_issue"
        )
    if verdict in {"no_issue", "issue_found"} and not valid_runtime_observed:
        raise RecordError(
            f"{context} requires a round with passed control and reproducer "
            f"for verdict {verdict}"
        )
    record["inconsistency_reason"] = inconsistency
    record["remaining_uncertainty"] = uncertainty
    return record


def _escape_inline(value: Any) -> str:
    single_line = " ".join(str(value).splitlines())
    escaped_html = html.escape(single_line, quote=False)
    return MD_SPECIAL_RE.sub(r"\\\1", escaped_html)


def _escape_block(value: Any) -> str:
    return "\n".join(_escape_inline(line) for line in str(value).splitlines())


def _inline_code(value: Any) -> str:
    single_line = " ".join(str(value).splitlines())
    escaped = html.escape(single_line, quote=False).replace("`", "&#96;")
    return f"`{escaped}`"


def _blockquote(value: Any) -> str:
    lines = str(value).splitlines() or [""]
    return "\n".join(f"> {html.escape(line, quote=False)}" for line in lines)


def _code_fence(code: Any, language: str = "") -> str:
    text = str(code)
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _render_runtime_round(runtime_round: dict[str, Any]) -> str:
    lines = [
        f"### Round {runtime_round['round']}",
        "",
        f"- Status: {_inline_code(runtime_round['status'])}",
    ]
    for field, label in (
        ("positive_control_status", "Positive control"),
        ("reproducer_status", "Reproducer"),
    ):
        if field in runtime_round:
            lines.append(f"- {label}: {_inline_code(runtime_round[field])}")
    lines.extend(["", _escape_block(runtime_round["summary"])])

    for step in runtime_round.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_name = step.get("id", "unnamed")
        lines.extend(["", f"#### Step: {_escape_inline(step_name)}", ""])
        if isinstance(step.get("argv"), list):
            lines.extend(
                [
                    "Command:",
                    "",
                    _code_fence(
                        json.dumps(step["argv"], ensure_ascii=False), "json"
                    ),
                    "",
                ]
            )
        for field, label in (
            ("status", "Status"),
            ("exit_code", "Exit code"),
            ("stdout_log", "stdout"),
            ("stderr_log", "stderr"),
        ):
            if field in step:
                lines.append(f"- {label}: {_inline_code(step[field])}")
    return "\n".join(lines)


def render_problem_report(record: dict[str, Any]) -> str:
    lines = [
        f"# {_escape_inline(record['title'])}",
        "",
        "## Verdict",
        "",
        f"- Verdict: {_inline_code(record['verdict'])}",
        f"- Confidence: {_inline_code(record['confidence'])}",
        f"- Record ID: {_inline_code(record['record_id'])}",
        "",
        "## Problem Description",
        "",
        _escape_block(record["problem_description"]),
        "",
        "## Standard Requirement",
        "",
        f"- Section: {_escape_inline(record['standard_check']['section'])}",
        "",
        _blockquote(record["standard_check"]["exact_text"]),
        "",
        "Interpretation:",
        "",
        _escape_block(record["standard_check"]["interpretation"]),
        "",
        "## Relevant Source Code",
        "",
        _escape_block(record["code_check"]["summary"]),
    ]
    for excerpt in record["code_check"]["excerpts"]:
        location = (
            f"{excerpt['path']}:{excerpt['line_start']}"
            if excerpt["line_start"] == excerpt["line_end"]
            else f"{excerpt['path']}:{excerpt['line_start']}-{excerpt['line_end']}"
        )
        lines.extend(
            [
                "",
                f"### {_inline_code(location)}",
                "",
                _code_fence(excerpt["code"]),
                "",
                _escape_block(excerpt["explanation"]),
            ]
        )
    lines.extend(["", "## Runtime Evidence", ""])
    for runtime_round in sorted(record["runtime_rounds"], key=lambda item: item["round"]):
        lines.extend([_render_runtime_round(runtime_round), ""])
    lines.extend(
        [
            "## Inconsistency Reason",
            "",
            _escape_block(record["inconsistency_reason"]),
            "",
            "## Decision Reason",
            "",
            _escape_block(record["decision_reason"]),
        ]
    )
    if record["verdict"] == "suspected_issue":
        lines.extend(
            [
                "",
                "## Remaining Uncertainty",
                "",
                _escape_block(record["remaining_uncertainty"]),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _unique_dicts(values: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        key = key_fn(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _group_problem_records(
    records: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for record in records:
        if record["verdict"] == "no_issue":
            continue
        key = record["root_cause_key"] or record["record_id"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(record)
    return [(key, grouped[key]) for key in order]


def render_problem_group_report(records: list[dict[str, Any]]) -> str:
    if len(records) == 1:
        return render_problem_report(records[0])

    first = records[0]
    verdict = (
        "issue_found"
        if any(record["verdict"] == "issue_found" for record in records)
        else "suspected_issue"
    )
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    confidence = max(
        (record["confidence"] for record in records),
        key=lambda value: confidence_rank.get(value, 0),
    )
    record_ids = [record["record_id"] for record in records]
    root_cause_key = first.get("root_cause_key", "")
    standards = _unique_dicts(
        [
            {
                "requirement_id": record.get("requirement_id", ""),
                "section": record["standard_check"]["section"],
                "exact_text": record["standard_check"]["exact_text"],
                "interpretation": record["standard_check"]["interpretation"],
            }
            for record in records
        ],
        lambda item: (
            item["requirement_id"],
            item["section"],
            item["exact_text"],
            item["interpretation"],
        ),
    )
    summaries = _unique_strings(
        [record["code_check"]["summary"] for record in records]
    )
    excerpts = _unique_dicts(
        [
            excerpt
            for record in records
            for excerpt in record["code_check"]["excerpts"]
        ],
        lambda item: (
            item["path"],
            item["line_start"],
            item["line_end"],
            item["code"],
            item["explanation"],
        ),
    )
    runtime_rounds = _unique_dicts(
        [
            runtime_round
            for record in records
            for runtime_round in record["runtime_rounds"]
        ],
        lambda item: (
            item["round"],
            item["status"],
            item["positive_control_status"],
            item["reproducer_status"],
            item["summary"],
            json.dumps(item.get("steps", []), ensure_ascii=False, sort_keys=True),
        ),
    )
    inconsistency_reasons = _unique_strings(
        [
            record["inconsistency_reason"]
            for record in records
            if record["inconsistency_reason"].strip()
        ]
    )
    decision_reasons = _unique_strings(
        [
            record["decision_reason"]
            for record in records
            if record["decision_reason"].strip()
        ]
    )
    uncertainties = _unique_strings(
        [
            record["remaining_uncertainty"]
            for record in records
            if record["remaining_uncertainty"].strip()
        ]
    )

    lines = [
        f"# {_escape_inline(first['title'])}",
        "",
        "## Verdict",
        "",
        f"- Verdict: {_inline_code(verdict)}",
        f"- Confidence: {_inline_code(confidence)}",
        f"- Covered Record IDs: {', '.join(_inline_code(value) for value in record_ids)}",
    ]
    if root_cause_key:
        lines.append(f"- Root Cause Key: {_inline_code(root_cause_key)}")
    lines.extend(
        [
            "",
            "## Problem Description",
            "",
            _escape_block(first["problem_description"]),
            "",
            "This report deduplicates multiple candidate-level records that resolved to the same root cause.",
            "",
            "## Standard Requirement",
            "",
        ]
    )
    for standard in standards:
        lines.extend(
            [
                f"- Requirement ID: {_inline_code(standard['requirement_id'])}",
                f"- Section: {_escape_inline(standard['section'])}",
                "",
                _blockquote(standard["exact_text"]),
                "",
                "Interpretation:",
                "",
                _escape_block(standard["interpretation"]),
                "",
            ]
        )
    lines.extend(["## Relevant Source Code", ""])
    for summary in summaries:
        lines.extend([_escape_block(summary), ""])
    for excerpt in excerpts:
        location = (
            f"{excerpt['path']}:{excerpt['line_start']}"
            if excerpt["line_start"] == excerpt["line_end"]
            else f"{excerpt['path']}:{excerpt['line_start']}-{excerpt['line_end']}"
        )
        lines.extend(
            [
                f"### {_inline_code(location)}",
                "",
                _code_fence(excerpt["code"]),
                "",
                _escape_block(excerpt["explanation"]),
                "",
            ]
        )
    lines.extend(["## Runtime Evidence", ""])
    for runtime_round in sorted(runtime_rounds, key=lambda item: item["round"]):
        lines.extend([_render_runtime_round(runtime_round), ""])
    lines.extend(["## Inconsistency Reason", ""])
    for reason in inconsistency_reasons:
        lines.append(f"- {_escape_block(reason)}")
    lines.extend(["", "## Decision Reason", ""])
    for reason in decision_reasons:
        lines.append(f"- {_escape_block(reason)}")
    if verdict == "suspected_issue":
        lines.extend(["", "## Remaining Uncertainty", ""])
        for item in uncertainties:
            lines.append(f"- {_escape_block(item)}")
    return "\n".join(lines).rstrip() + "\n"


def render_ledger(records: list[dict[str, Any]]) -> str:
    counts = Counter(record["verdict"] for record in records)
    lines = [
        "# SpecLitmus Variant Audit",
        "",
        f"- Total records: {len(records)}",
        f"- No issue: {counts['no_issue']}",
        f"- Suspected issue: {counts['suspected_issue']}",
        f"- Issue found: {counts['issue_found']}",
    ]
    for record in records:
        lines.extend(
            [
                "",
                f"## {_escape_inline(record['title'])}",
                "",
                f"- Record ID: {_inline_code(record['record_id'])}",
                f"- Verdict: {_inline_code(record['verdict'])}",
                f"- Confidence: {_inline_code(record['confidence'])}",
                f"- Standard: {_escape_inline(record['standard_check']['section'])}",
                "",
                _escape_block(record["decision_reason"]),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def render_outputs(raw: Any, output_dir: Path) -> dict[str, Any]:
    records = _normalize_records(raw)
    ids = [record["record_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise RecordError("record_id values must be unique")

    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for stale_report in reports_dir.glob("*.md"):
        stale_report.unlink()
    _write_text(
        output_dir / "variant_audit.json",
        json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write_text(output_dir / "variant_audit.md", render_ledger(records))

    report_files: list[str] = []
    report_index = 0
    for group_key, group_records in _group_problem_records(records):
        report_index += 1
        filename = f"{report_index:04d}-{group_key}.md"
        _write_text(
            reports_dir / filename,
            render_problem_group_report(group_records),
        )
        report_files.append(f"reports/{filename}")

    manifest = {
        "record_count": len(records),
        "problem_record_count": sum(
            1 for item in records if item["verdict"] != "no_issue"
        ),
        "problem_report_count": len(report_files),
        "deduplicated": any(bool(item.get("root_cause_key")) for item in records),
        "verdict_counts": dict(Counter(item["verdict"] for item in records)),
        "problem_reports": report_files,
    }
    _write_text(
        output_dir / "report_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate SpecLitmus audit records and render Markdown reports."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        manifest = render_outputs(raw, args.output_dir)
    except (OSError, json.JSONDecodeError, RecordError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
