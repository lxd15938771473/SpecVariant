#!/usr/bin/env python3
"""Merge per-batch static-triage outputs with exact selection coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SELECTION_SCHEMA = "speclitmus.requirement-selection.v1"
TRIAGE_SCHEMA = "speclitmus.static-triage.v1"
ALLOWED_VERDICTS = {"no_issue", "suspected_issue", "issue_found"}


class MergeError(ValueError):
    """Raised when batch output cannot be merged safely."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MergeError(f"cannot read JSON {path}: {exc}") from exc


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def merge(selection_path: Path, output_path: Path, *, overwrite: bool = False) -> dict[str, Any]:
    selection_path = selection_path.resolve()
    output_path = output_path.resolve()
    selection = load_json(selection_path)
    if not isinstance(selection, dict) or selection.get("schema_version") != SELECTION_SCHEMA:
        raise MergeError(f"selection schema_version must be {SELECTION_SCHEMA!r}")
    metadata = selection.get("metadata")
    batches = selection.get("batches")
    selected = selection.get("requirements")
    if not isinstance(metadata, dict) or not isinstance(batches, list):
        raise MergeError("selection metadata and batches are required")
    if not isinstance(selected, list):
        raise MergeError("selection requirements must be an array")

    selected_ids = [
        row.get("requirement_id") if isinstance(row, dict) else None
        for row in selected
    ]
    if any(not isinstance(value, str) or not value for value in selected_ids):
        raise MergeError("selection contains an invalid requirement_id")
    if len(selected_ids) != len(set(selected_ids)):
        raise MergeError("selection contains duplicate requirement IDs")

    records_by_id: dict[str, dict[str, Any]] = {}
    selection_root = selection_path.parent
    for index, descriptor in enumerate(batches):
        if not isinstance(descriptor, dict):
            raise MergeError(f"batches[{index}] must be an object")
        batch_id = descriptor.get("batch_id")
        expected_ids = descriptor.get("requirement_ids")
        output_rel = descriptor.get("output")
        if (
            not isinstance(batch_id, str)
            or not isinstance(expected_ids, list)
            or any(not isinstance(value, str) for value in expected_ids)
            or not isinstance(output_rel, str)
        ):
            raise MergeError(f"batches[{index}] has invalid fields")
        batch_output = (selection_root / output_rel).resolve()
        if not is_within(batch_output, selection_root):
            raise MergeError(f"{batch_id}: output path escapes the run directory")
        if not batch_output.is_file():
            raise MergeError(f"{batch_id}: missing output {batch_output}")
        document = load_json(batch_output)
        if not isinstance(document, dict) or document.get("schema_version") != TRIAGE_SCHEMA:
            raise MergeError(f"{batch_id}: output must use {TRIAGE_SCHEMA!r}")
        output_metadata = document.get("metadata")
        if not isinstance(output_metadata, dict):
            raise MergeError(f"{batch_id}: output metadata is required")
        if output_metadata.get("batch_id") != batch_id:
            raise MergeError(f"{batch_id}: output metadata.batch_id mismatch")
        if output_metadata.get("selection_sha256") != metadata.get("selection_sha256"):
            raise MergeError(f"{batch_id}: output selection_sha256 mismatch")
        records = document.get("records")
        if not isinstance(records, list):
            raise MergeError(f"{batch_id}: records must be an array")
        batch_records: dict[str, dict[str, Any]] = {}
        for record_index, record in enumerate(records):
            if not isinstance(record, dict):
                raise MergeError(f"{batch_id}.records[{record_index}] must be an object")
            requirement_id = record.get("requirement_id")
            if not isinstance(requirement_id, str) or not requirement_id:
                raise MergeError(
                    f"{batch_id}.records[{record_index}].requirement_id is invalid"
                )
            if requirement_id in batch_records or requirement_id in records_by_id:
                raise MergeError(f"duplicate static triage record: {requirement_id}")
            if record.get("verdict") not in ALLOWED_VERDICTS:
                raise MergeError(
                    f"{batch_id}: {requirement_id} has invalid verdict "
                    f"{record.get('verdict')!r}"
                )
            batch_records[requirement_id] = record
        missing = [value for value in expected_ids if value not in batch_records]
        unexpected = sorted(set(batch_records) - set(expected_ids))
        if missing or unexpected:
            raise MergeError(
                f"{batch_id}: coverage mismatch; missing={missing}, "
                f"unexpected={unexpected}"
            )
        records_by_id.update(batch_records)

    missing_selected = [value for value in selected_ids if value not in records_by_id]
    unexpected_selected = sorted(set(records_by_id) - set(selected_ids))
    if missing_selected or unexpected_selected:
        raise MergeError(
            "merged coverage mismatch; "
            f"missing={missing_selected}, unexpected={unexpected_selected}"
        )

    ordered_records = [records_by_id[value] for value in selected_ids]
    merged = {
        "schema_version": TRIAGE_SCHEMA,
        "metadata": {
            "selection_sha256": metadata.get("selection_sha256"),
            "requirements_sha256": metadata.get("requirements_sha256"),
            "position_start": metadata.get("start"),
            "position_end": metadata.get("end"),
            "batch_size": metadata.get("batch_size"),
            "batch_count": len(batches),
            "records": len(ordered_records),
        },
        "records": ordered_records,
    }
    if output_path.exists() and not overwrite:
        raise MergeError(f"output exists; use --overwrite to replace it: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(json_bytes(merged))
    return {
        "ok": True,
        "records": len(ordered_records),
        "batches": len(batches),
        "output": str(output_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge static-triage batch outputs with exact coverage."
    )
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = merge(args.selection, args.out, overwrite=args.overwrite)
    except MergeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
