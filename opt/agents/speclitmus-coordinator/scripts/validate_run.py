#!/usr/bin/env python3
"""Validate a completed SpecLitmus run without external dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ALLOWED_VERDICTS = {"no_issue", "suspected_issue", "issue_found"}
REPORT_VERDICTS = {"suspected_issue", "issue_found"}
REQUIRED_REPORT_HEADINGS = (
    "Problem Description",
    "Standard Requirement",
    "Relevant Source Code",
    "Runtime Evidence",
    "Inconsistency Reason",
)
SELECTION_SCHEMA = "speclitmus.requirement-selection.v1"
BATCH_SCHEMA = "speclitmus.static-triage-batch.v1"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def get_rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in keys:
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def stable_id(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def eligible_requirements(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in requirements
        if row.get("eligibility", "eligible") == "eligible"
    ]


def validate_requirement_selection(
    selection: Any,
    requirements: list[dict[str, Any]],
    requirements_path: Path,
    run_dir: Path,
    manifest: Any,
    errors: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(selection, dict):
        errors.append("requirement_selection.json must contain an object")
        return [], []
    if selection.get("schema_version") != SELECTION_SCHEMA:
        errors.append(
            f"requirement selection schema_version must be {SELECTION_SCHEMA!r}"
        )
    metadata = selection.get("metadata")
    selected = selection.get("requirements")
    batches = selection.get("batches")
    if not isinstance(metadata, dict):
        errors.append("requirement selection metadata must be an object")
        return [], []
    if not isinstance(selected, list):
        errors.append("requirement selection requirements must be an array")
        return [], []
    if not isinstance(batches, list):
        errors.append("requirement selection batches must be an array")
        batches = []

    full_digest = sha256_bytes(requirements_path.read_bytes())
    if metadata.get("requirements_sha256") != full_digest:
        errors.append("requirement selection requirements_sha256 mismatch")

    mode = metadata.get("requirement_mode")
    if mode not in {"reuse", "fresh"}:
        errors.append("requirement selection mode must be reuse or fresh")
    all_eligible = eligible_requirements(requirements)
    start = int_field(metadata.get("start"))
    end = int_field(metadata.get("end"))
    batch_size = int_field(metadata.get("batch_size"))
    if (
        start is None
        or end is None
        or batch_size is None
        or start < 1
        or end < start
        or end > len(all_eligible)
        or batch_size < 1
    ):
        errors.append("requirement selection has an invalid start/end/batch_size")
        return [], []
    expected = all_eligible[start - 1 : end]
    expected_ids = [
        stable_id(row, "requirement_id", "item_id") for row in expected
    ]
    selected_ids = [
        stable_id(row, "requirement_id", "item_id")
        for row in selected
        if isinstance(row, dict)
    ]
    if len(selected_ids) != len(selected):
        errors.append("requirement selection contains a non-object requirement")
    if selected_ids != expected_ids:
        errors.append(
            "requirement selection does not equal the configured eligible interval"
        )
    if selected != expected:
        errors.append(
            "requirement selection requirement objects differ from requirements.json"
        )
    if metadata.get("total_eligible_requirement_count") != len(all_eligible):
        errors.append(
            "requirement selection total_eligible_requirement_count mismatch"
        )
    if metadata.get("selected_requirement_count") != len(expected):
        errors.append("requirement selection selected_requirement_count mismatch")

    computed_selection_sha = sha256_bytes(
        json.dumps(
            expected_ids, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )
    if metadata.get("selection_sha256") != computed_selection_sha:
        errors.append("requirement selection selection_sha256 mismatch")

    expected_batch_count = (len(expected) + batch_size - 1) // batch_size
    if metadata.get("batch_count") != expected_batch_count:
        errors.append("requirement selection metadata.batch_count mismatch")
    if len(batches) != expected_batch_count:
        errors.append("requirement selection batch descriptor count mismatch")

    batch_output_records: list[dict[str, Any]] = []
    for batch_index, offset in enumerate(
        range(0, len(expected), batch_size), start=1
    ):
        if batch_index > len(batches):
            break
        descriptor = batches[batch_index - 1]
        if not isinstance(descriptor, dict):
            errors.append(f"requirement selection batches[{batch_index - 1}] is invalid")
            continue
        batch_id = f"batch-{batch_index:04d}"
        batch_rows = expected[offset : offset + batch_size]
        batch_ids = [
            stable_id(row, "requirement_id", "item_id") for row in batch_rows
        ]
        position_start = start + offset
        position_end = position_start + len(batch_rows) - 1
        expected_input = f"static-triage-batches/{batch_id}.input.json"
        expected_output = f"static-triage-batches/{batch_id}.output.json"
        expected_fields = {
            "batch_id": batch_id,
            "position_start": position_start,
            "position_end": position_end,
            "requirement_count": len(batch_rows),
            "requirement_ids": batch_ids,
            "input": expected_input,
            "output": expected_output,
        }
        for key, value in expected_fields.items():
            if descriptor.get(key) != value:
                errors.append(
                    f"{batch_id}: descriptor {key} is "
                    f"{descriptor.get(key)!r}; expected {value!r}"
                )

        input_path = (run_dir / expected_input).resolve()
        output_path = (run_dir / expected_output).resolve()
        for label, path in (("input", input_path), ("output", output_path)):
            if not is_within(path, run_dir):
                errors.append(f"{batch_id}: {label} path escapes run directory")
            elif not path.is_file():
                errors.append(f"{batch_id}: missing {label} artifact {path}")
        if not input_path.is_file() or not is_within(input_path, run_dir):
            continue
        batch_document = load_json(input_path)
        if (
            not isinstance(batch_document, dict)
            or batch_document.get("schema_version") != BATCH_SCHEMA
        ):
            errors.append(f"{batch_id}: invalid static-triage batch input schema")
            continue
        batch_metadata = batch_document.get("metadata")
        if not isinstance(batch_metadata, dict):
            errors.append(f"{batch_id}: batch input metadata is required")
        else:
            checks = {
                "batch_id": batch_id,
                "selection_sha256": computed_selection_sha,
                "requirements_sha256": full_digest,
                "position_start": position_start,
                "position_end": position_end,
                "requirement_count": len(batch_rows),
                "expected_output": expected_output,
            }
            for key, value in checks.items():
                if batch_metadata.get(key) != value:
                    errors.append(f"{batch_id}: batch input metadata.{key} mismatch")
        if batch_document.get("requirements") != batch_rows:
            errors.append(f"{batch_id}: batch input requirements mismatch")
        if output_path.is_file() and is_within(output_path, run_dir):
            output_document = load_json(output_path)
            if (
                not isinstance(output_document, dict)
                or output_document.get("schema_version")
                != "speclitmus.static-triage.v1"
            ):
                errors.append(f"{batch_id}: invalid static-triage batch output schema")
                continue
            output_metadata = output_document.get("metadata")
            if not isinstance(output_metadata, dict):
                errors.append(f"{batch_id}: batch output metadata is required")
            else:
                if output_metadata.get("batch_id") != batch_id:
                    errors.append(f"{batch_id}: batch output metadata.batch_id mismatch")
                if (
                    output_metadata.get("selection_sha256")
                    != computed_selection_sha
                ):
                    errors.append(
                        f"{batch_id}: batch output metadata.selection_sha256 mismatch"
                    )
            output_records = get_rows(output_document, "records")
            output_ids = [
                stable_id(row, "requirement_id") for row in output_records
            ]
            if output_ids != batch_ids:
                errors.append(f"{batch_id}: batch output requirement coverage mismatch")
            batch_output_records.extend(output_records)

    if not isinstance(manifest, dict):
        errors.append("run_manifest.json must contain an object")
        return expected_ids, batch_output_records
    requirement_source = manifest.get("requirement_source")
    triage_selection = manifest.get("static_triage_selection")
    if not isinstance(requirement_source, dict):
        errors.append("manifest requirement_source is required for a selected run")
    else:
        source_checks = {
            "mode": mode,
            "requirements_sha256": full_digest,
            "standard_sha256": metadata.get("standard_sha256"),
        }
        for key, value in source_checks.items():
            if requirement_source.get(key) != value:
                errors.append(f"manifest requirement_source.{key} mismatch")
    if not isinstance(triage_selection, dict):
        errors.append(
            "manifest static_triage_selection is required for a selected run"
        )
    else:
        selection_checks = {
            "start": start,
            "end": end,
            "batch_size": batch_size,
            "selected_requirement_count": len(expected),
            "batch_count": expected_batch_count,
            "selection_sha256": computed_selection_sha,
        }
        for key, value in selection_checks.items():
            if triage_selection.get(key) != value:
                errors.append(f"manifest static_triage_selection.{key} mismatch")
    return expected_ids, batch_output_records


def parse_code_reference(value: Any) -> tuple[str, int | None] | None:
    if isinstance(value, dict):
        path = value.get("file") or value.get("path")
        line = value.get("line") or value.get("line_start")
        if not path:
            return None
        try:
            return str(path), int(line) if line is not None else None
        except (TypeError, ValueError):
            return str(path), None
    text = str(value or "").strip()
    match = re.match(r"^(.*?):(\d+)$", text)
    if match:
        return match.group(1), int(match.group(2))
    return (text, None) if text else None


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_code_reference(
    reference: Any,
    target_repo: Path,
    errors: list[str],
    context: str,
) -> None:
    parsed = parse_code_reference(reference)
    if not parsed:
        errors.append(f"{context}: invalid code reference {reference!r}")
        return
    relpath, line = parsed
    candidate = Path(relpath)
    source_path = candidate if candidate.is_absolute() else target_repo / candidate
    if not is_within(source_path, target_repo):
        errors.append(f"{context}: code reference escapes target repo: {source_path}")
        return
    if not source_path.is_file():
        errors.append(f"{context}: code file does not exist: {source_path}")
        return
    if line is not None:
        line_count = len(source_path.read_text(encoding="utf-8", errors="replace").splitlines())
        if line < 1 or line > line_count:
            errors.append(
                f"{context}: line {line} outside {source_path} (1..{line_count})"
            )


def non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def non_empty_collection(value: Any) -> bool:
    if isinstance(value, dict):
        return any(non_empty_collection(item) for item in value.values())
    if isinstance(value, list):
        return any(non_empty_collection(item) for item in value)
    return non_empty_text(value)


def int_field(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_line_interval(
    row: dict[str, Any],
    errors: list[str],
    context: str,
) -> tuple[int | None, int | None]:
    line_start = int_field(row.get("line_start"))
    line_end = int_field(row.get("line_end"))
    if line_start is None or line_end is None:
        errors.append(f"{context}: missing valid line_start/line_end")
        return line_start, line_end
    if line_start < 1 or line_end < line_start:
        errors.append(
            f"{context}: invalid line interval {line_start}..{line_end}"
        )
    return line_start, line_end


def validate_standard_evidence(
    evidence: Any,
    errors: list[str],
    context: str,
) -> None:
    if not isinstance(evidence, dict):
        errors.append(f"{context}: standard_evidence must be an object")
        return
    if evidence.get("verified") is not True:
        errors.append(f"{context}: standard_evidence.verified must be true")
    if not non_empty_text(evidence.get("quote")):
        errors.append(f"{context}: standard_evidence.quote is required")
    validate_line_interval(evidence, errors, f"{context}: standard_evidence")


def validate_code_excerpt(
    excerpt: Any,
    target_repo: Path | None,
    errors: list[str],
    context: str,
) -> None:
    if not isinstance(excerpt, dict):
        errors.append(f"{context}: code excerpt must be an object")
        return
    path = excerpt.get("path") or excerpt.get("file")
    if not non_empty_text(path):
        errors.append(f"{context}: code excerpt path is required")
    line_start, line_end = validate_line_interval(excerpt, errors, context)
    if not non_empty_text(excerpt.get("code")):
        errors.append(f"{context}: code excerpt code is required")
    if not non_empty_text(excerpt.get("explanation")):
        errors.append(f"{context}: code excerpt explanation is required")

    if target_repo is None or not non_empty_text(path):
        return
    if line_start is not None:
        validate_code_reference(
            {"path": path, "line_start": line_start},
            target_repo,
            errors,
            f"{context}: line_start",
        )
    if line_end is not None:
        validate_code_reference(
            {"path": path, "line_start": line_end},
            target_repo,
            errors,
            f"{context}: line_end",
        )
    if line_start is None or line_end is None:
        return

    candidate = Path(str(path))
    source_path = candidate if candidate.is_absolute() else target_repo / candidate
    if not is_within(source_path, target_repo) or not source_path.is_file():
        return
    source_lines = source_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    if line_end <= len(source_lines):
        expected = "\n".join(source_lines[line_start - 1 : line_end]).strip()
        actual = str(excerpt.get("code", "")).strip()
        if actual != expected:
            errors.append(f"{context}: code excerpt does not match source lines")


def validate_static_code_check(
    code_check: Any,
    verdict: str,
    target_repo: Path | None,
    errors: list[str],
    context: str,
) -> None:
    if not isinstance(code_check, dict):
        errors.append(f"{context}: code_check must be an object")
        return
    if not non_empty_text(code_check.get("summary")):
        errors.append(f"{context}: code_check.summary is required")

    excerpts = code_check.get("excerpts")
    if excerpts is None:
        excerpts = []
    if not isinstance(excerpts, list):
        errors.append(f"{context}: code_check.excerpts must be a list")
        excerpts = []
    for index, excerpt in enumerate(excerpts):
        validate_code_excerpt(
            excerpt,
            target_repo,
            errors,
            f"{context}: code_check.excerpts[{index}]",
        )

    absence_evidence = code_check.get("absence_evidence")
    has_absence_evidence = non_empty_collection(absence_evidence)
    if verdict == "no_issue":
        if not excerpts:
            errors.append(f"{context}: static no_issue requires code_check.excerpts")
        if has_absence_evidence:
            errors.append(
                f"{context}: static no_issue cannot rely on absence_evidence"
            )
    elif not excerpts and not has_absence_evidence:
        errors.append(
            f"{context}: static {verdict} requires code_check.excerpts or "
            "code_check.absence_evidence"
        )


def validate_static_triage_record(
    row: dict[str, Any],
    target_repo: Path | None,
    errors: list[str],
    context: str,
) -> None:
    verdict = row.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        return
    expected_action = "skip" if verdict == "no_issue" else "generate_variants"
    if row.get("generation_action") != expected_action:
        errors.append(
            f"{context}: generation_action must be {expected_action!r} for {verdict}"
        )
    if row.get("confidence") not in {"low", "medium", "high"}:
        errors.append(f"{context}: confidence must be low, medium, or high")
    if not non_empty_text(row.get("decision_reason")):
        errors.append(f"{context}: decision_reason is required")

    validate_standard_evidence(row.get("standard_evidence"), errors, context)
    validate_static_code_check(
        row.get("code_check"),
        verdict,
        target_repo,
        errors,
        context,
    )

    inconsistency_reason = row.get("inconsistency_reason")
    remaining_uncertainty = row.get("remaining_uncertainty")
    if verdict == "no_issue":
        if non_empty_text(inconsistency_reason):
            errors.append(f"{context}: static no_issue must not set inconsistency_reason")
        if non_empty_text(remaining_uncertainty):
            errors.append(f"{context}: static no_issue must not set remaining_uncertainty")
    elif verdict == "suspected_issue":
        if not non_empty_text(inconsistency_reason):
            errors.append(
                f"{context}: suspected_issue requires inconsistency_reason"
            )
        if not non_empty_text(remaining_uncertainty):
            errors.append(
                f"{context}: suspected_issue requires remaining_uncertainty"
            )
    elif verdict == "issue_found" and not non_empty_text(inconsistency_reason):
        errors.append(f"{context}: issue_found requires inconsistency_reason")


def report_paths(run_dir: Path) -> list[Path]:
    reports = run_dir / "reports"
    return sorted(reports.glob("*.md")) if reports.is_dir() else []


def validate_run(
    run_dir: Path,
    *,
    target_repo: Path | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    required_files = {
        "manifest": run_dir / "run_manifest.json",
        "requirements": run_dir / "requirements.json",
        "candidates": run_dir / "candidates.json",
        "audit": run_dir / "variant_audit.json",
    }
    for label, path in required_files.items():
        if not path.is_file():
            errors.append(f"missing {label} file: {path}")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    manifest = load_json(required_files["manifest"])
    requirements_data = load_json(required_files["requirements"])
    candidates_data = load_json(required_files["candidates"])
    audit_data = load_json(required_files["audit"])
    static_triage_path = run_dir / "static_triage.json"
    static_triage_data = load_json(static_triage_path) if static_triage_path.is_file() else None
    selection_path = run_dir / "requirement_selection.json"
    selection_data = load_json(selection_path) if selection_path.is_file() else None

    max_rounds = int(manifest.get("max_rounds", 5))
    if max_rounds < 1 or max_rounds > 5:
        errors.append(f"manifest max_rounds must be in 1..5, got {max_rounds}")

    requirements = get_rows(requirements_data, "requirements", "items")
    requirement_ids = [
        stable_id(row, "requirement_id", "item_id") for row in requirements
    ]
    if any(not value for value in requirement_ids):
        errors.append("one or more requirements have no stable ID")
    duplicates = duplicate_values([value for value in requirement_ids if value])
    if duplicates:
        errors.append(f"duplicate requirement IDs: {duplicates}")
    requirement_id_set = set(requirement_ids)
    requirement_by_id = {
        stable_id(row, "requirement_id", "item_id"): row for row in requirements
    }

    for row in requirements:
        requirement_id = stable_id(row, "requirement_id", "item_id") or "<unknown>"
        evidence = row.get("evidence")
        if isinstance(evidence, list):
            verified = bool(evidence) and all(
                isinstance(item, dict) and item.get("verified") is True
                for item in evidence
            )
        else:
            verified = isinstance(evidence, dict) and evidence.get("verified") is True
        if not verified:
            errors.append(f"{requirement_id}: evidence is not fully verified")

    metadata = requirements_data.get("metadata", {}) if isinstance(requirements_data, dict) else {}
    chunk_count = metadata.get("chunk_count")
    processed = metadata.get("processed_chunk_count", metadata.get("processed_chunks"))
    if chunk_count is not None and processed is not None and int(chunk_count) != int(processed):
        errors.append(
            f"requirement coverage incomplete: processed {processed} of {chunk_count} chunks"
        )

    if selection_data is not None:
        expected_triage_ids, batch_output_records = validate_requirement_selection(
            selection_data,
            requirements,
            required_files["requirements"],
            run_dir,
            manifest,
            errors,
        )
    else:
        batch_output_records = []
        expected_triage_ids = [
            stable_id(row, "requirement_id", "item_id")
            for row in eligible_requirements(requirements)
        ]
    expected_triage_id_set = set(expected_triage_ids)

    candidates = get_rows(candidates_data, "candidates", "tests", "variants")
    candidate_ids = [
        stable_id(row, "candidate_id", "variant_id", "test_id") for row in candidates
    ]
    if any(not value for value in candidate_ids):
        errors.append("one or more candidates have no stable ID")
    duplicates = duplicate_values([value for value in candidate_ids if value])
    if duplicates:
        errors.append(f"duplicate candidate IDs: {duplicates}")
    candidate_id_set = set(candidate_ids)

    for row in candidates:
        candidate_id = stable_id(row, "candidate_id", "variant_id", "test_id") or "<unknown>"
        requirement_id = stable_id(row, "requirement_id")
        if requirement_id not in requirement_id_set:
            errors.append(
                f"{candidate_id}: unknown requirement_id {requirement_id!r}"
            )
        elif requirement_id not in expected_triage_id_set:
            errors.append(
                f"{candidate_id}: requirement_id {requirement_id!r} is outside "
                "the selected static-triage interval"
            )

    static_triage_records: list[dict[str, Any]] = []
    if static_triage_data is not None:
        static_triage_records = get_rows(static_triage_data, "records")
        if selection_data is not None and static_triage_records != batch_output_records:
            errors.append(
                "static_triage.json records differ from the ordered batch outputs"
            )
        if selection_data is not None:
            selection_metadata = selection_data.get("metadata", {})
            triage_metadata = (
                static_triage_data.get("metadata", {})
                if isinstance(static_triage_data, dict)
                else {}
            )
            if not isinstance(triage_metadata, dict):
                errors.append("static_triage metadata must be an object")
            else:
                for key in ("selection_sha256", "requirements_sha256"):
                    if triage_metadata.get(key) != selection_metadata.get(key):
                        errors.append(f"static_triage metadata.{key} mismatch")
        triage_by_requirement: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(static_triage_records):
            requirement_id = stable_id(row, "requirement_id")
            if not requirement_id:
                errors.append(f"static_triage.records[{index}]: missing requirement_id")
                continue
            if requirement_id in triage_by_requirement:
                errors.append(f"duplicate static triage record: {requirement_id}")
            triage_by_requirement[requirement_id] = row
            if requirement_id not in expected_triage_id_set:
                errors.append(
                    f"static_triage.records[{index}]: requirement_id "
                    f"{requirement_id!r} is outside the selected interval"
                )
            upstream = requirement_by_id.get(requirement_id)
            if (
                upstream is not None
                and row.get("standard_evidence") != upstream.get("evidence")
            ):
                errors.append(
                    f"static_triage.records[{index}]: standard_evidence differs "
                    "from the selected requirement"
                )
            verdict = row.get("verdict")
            if verdict not in ALLOWED_VERDICTS:
                errors.append(
                    f"static_triage.records[{index}]: invalid verdict {verdict!r}"
                )
            else:
                validate_static_triage_record(
                    row,
                    target_repo,
                    errors,
                    f"static_triage.records[{index}]",
                )

        missing_triage = [
            requirement_id
            for requirement_id in expected_triage_ids
            if requirement_id not in triage_by_requirement
        ]
        if missing_triage:
            errors.append(f"requirements without static triage: {missing_triage}")

        candidate_requirement_ids = {
            stable_id(row, "requirement_id") for row in candidates
        }
        generate_requirement_ids = {
            requirement_id
            for requirement_id, row in triage_by_requirement.items()
            if row.get("verdict") in REPORT_VERDICTS
        }
        skipped_requirement_ids = {
            requirement_id
            for requirement_id, row in triage_by_requirement.items()
            if row.get("verdict") == "no_issue"
        }
        missing_generated = sorted(generate_requirement_ids - candidate_requirement_ids)
        if missing_generated and not allow_partial:
            errors.append(
                "static suspected/issue requirements without generated candidates: "
                f"{missing_generated}"
            )
        elif missing_generated:
            warnings.append(
                "partial run; static suspected/issue requirements without generated "
                f"candidates: {missing_generated}"
            )
        unexpected_generated = sorted(skipped_requirement_ids & candidate_requirement_ids)
        if unexpected_generated:
            errors.append(
                f"static no_issue requirements generated candidates: {unexpected_generated}"
            )
    elif selection_data is not None:
        errors.append("selected run is missing static_triage.json")

    records = get_rows(audit_data, "records", "audits", "results")
    record_by_id: dict[str, dict[str, Any]] = {}
    for row in records:
        candidate_id = stable_id(
            row,
            "candidate_id",
            "record_id",
            "task_id",
            "variant_id",
            "test_id",
        )
        if not candidate_id:
            errors.append("audit record has no candidate ID")
            continue
        if candidate_id in record_by_id:
            errors.append(f"duplicate audit record: {candidate_id}")
        record_by_id[candidate_id] = row
        verdict = row.get("verdict")
        if verdict not in ALLOWED_VERDICTS:
            errors.append(f"{candidate_id}: invalid verdict {verdict!r}")
        rounds = row.get("runtime_rounds", row.get("rounds", [])) or []
        if not isinstance(rounds, list):
            errors.append(f"{candidate_id}: runtime rounds are not a list")
            rounds = []
        if len(rounds) > max_rounds:
            errors.append(
                f"{candidate_id}: {len(rounds)} runtime rounds exceed max {max_rounds}"
            )
        for round_index, runtime_round in enumerate(rounds, start=1):
            if not isinstance(runtime_round, dict):
                errors.append(f"{candidate_id}: round {round_index} is not an object")
                continue
            for artifact in runtime_round.get("artifacts", []) or []:
                artifact_path = Path(str(artifact))
                if not artifact_path.is_absolute():
                    artifact_path = run_dir / artifact_path
                if not is_within(artifact_path, run_dir):
                    errors.append(
                        f"{candidate_id}: runtime artifact escapes run directory: {artifact_path}"
                    )
                    continue
                if not artifact_path.exists():
                    errors.append(
                        f"{candidate_id}: missing runtime artifact {artifact_path}"
                    )

        if target_repo is not None:
            code_check = row.get("code_check", {}) or {}
            references = (
                code_check.get("references", [])
                if isinstance(code_check, dict)
                else []
            )
            for reference in references or []:
                validate_code_reference(reference, target_repo, errors, candidate_id)

    missing_records = sorted(candidate_id_set - set(record_by_id))
    extra_records = sorted(set(record_by_id) - candidate_id_set)
    if missing_records and not allow_partial:
        errors.append(f"candidates without final audit record: {missing_records}")
    elif missing_records:
        warnings.append(f"partial run; candidates without audit record: {missing_records}")
    if extra_records:
        errors.append(f"audit records for unknown candidates: {extra_records}")

    reports = report_paths(run_dir)
    report_texts = {
        path: path.read_text(encoding="utf-8-sig") for path in reports
    }
    expected_report_ids = {
        candidate_id
        for candidate_id, row in record_by_id.items()
        if row.get("verdict") in REPORT_VERDICTS
    }
    for candidate_id in sorted(expected_report_ids):
        matching = [
            (path, text)
            for path, text in report_texts.items()
            if candidate_id in text
        ]
        if not matching:
            errors.append(f"{candidate_id}: no Markdown problem report found")
            continue
        for path, text in matching:
            for heading in REQUIRED_REPORT_HEADINGS:
                if f"## {heading}" not in text:
                    errors.append(f"{path}: missing heading '## {heading}'")

    no_issue_ids = {
        candidate_id
        for candidate_id, row in record_by_id.items()
        if row.get("verdict") == "no_issue"
    }
    for path, text in report_texts.items():
        for candidate_id in no_issue_ids:
            if candidate_id in text:
                warnings.append(
                    f"{path}: no_issue candidate {candidate_id} appears in a problem report"
                )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "requirements": len(requirements),
            "eligible_requirements": len(eligible_requirements(requirements)),
            "selected_requirements": len(expected_triage_ids),
            "static_triage_records": len(static_triage_records),
            "candidates": len(candidates),
            "audit_records": len(records),
            "problem_reports": len(reports),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a complete SpecLitmus run.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--target-repo", type=Path, default=None)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = validate_run(
        args.run_dir,
        target_repo=args.target_repo,
        allow_partial=args.allow_partial,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output)
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
