#!/usr/bin/env python3
"""Resolve requirement reuse and create deterministic static-triage batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


CONFIG_SCHEMA = "speclitmus.run-config.v1"
REQUIREMENTS_SCHEMA = "speclitmus.requirements.v3"
REQUIREMENT_DELTA_SCHEMA = "speclitmus.requirement-delta.v1"
SELECTION_SCHEMA = "speclitmus.requirement-selection.v1"
BATCH_SCHEMA = "speclitmus.static-triage-batch.v1"


class ConfigError(ValueError):
    """Raised when run configuration or requirement evidence is invalid."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read JSON {path}: {exc}") from exc


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{label} must be a positive integer")
    return value


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    return value


def normalize_requirement_document(
    document: Any,
    artifact_bytes: bytes,
    source_bytes: bytes,
    source_line_count: int,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Return a v3 corpus while preserving provenance for reviewed delta inputs."""
    artifact = require_object(document, "requirements artifact")
    schema_version = artifact.get("schema_version")
    if schema_version == REQUIREMENTS_SCHEMA:
        return artifact, artifact_bytes, {
            "configured_artifact_schema_version": schema_version,
            "configured_artifact_sha256": sha256_bytes(artifact_bytes),
            "normalized": False,
        }
    if schema_version != REQUIREMENT_DELTA_SCHEMA:
        raise ConfigError(
            "requirements schema_version must be "
            f"{REQUIREMENTS_SCHEMA!r} or {REQUIREMENT_DELTA_SCHEMA!r}"
        )

    delta_metadata = require_object(artifact.get("metadata"), "delta metadata")
    delta_rows = artifact.get("requirements")
    if not isinstance(delta_rows, list):
        raise ConfigError("delta requirements must be an array")
    requirements: list[dict[str, Any]] = []
    for index, delta_row in enumerate(delta_rows):
        row = require_object(delta_row, f"delta requirements[{index}]")
        requirement = require_object(
            row.get("rfc9846_requirement"),
            f"delta requirements[{index}].rfc9846_requirement",
        )
        requirements.append(requirement)

    eligible_count = sum(
        row.get("eligibility") == "eligible" for row in requirements
    )
    document_id = delta_metadata.get("newer_document_id")
    if not isinstance(document_id, str) or not document_id:
        raise ConfigError("delta metadata.newer_document_id must be non-empty")
    canonical = {
        "schema_version": REQUIREMENTS_SCHEMA,
        "metadata": {
            "document_id": document_id,
            "source_sha256": sha256_bytes(source_bytes),
            "source_line_count": source_line_count,
            "requirement_count": len(requirements),
            "eligible_requirement_count": eligible_count,
            "excluded_requirement_count": len(requirements) - eligible_count,
            "verified_requirement_count": sum(
                isinstance(row.get("evidence"), dict)
                and row["evidence"].get("verified") is True
                for row in requirements
            ),
            "unverified_requirement_count": sum(
                not isinstance(row.get("evidence"), dict)
                or row["evidence"].get("verified") is not True
                for row in requirements
            ),
            "delta_provenance": {
                "source_schema_version": schema_version,
                "source_sha256": sha256_bytes(artifact_bytes),
                "source_item_count": len(delta_rows),
                "baseline_document_id": delta_metadata.get("baseline_document_id"),
                "comparison_scope": delta_metadata.get("comparison_scope"),
                "validation_status": (
                    delta_metadata.get("validation", {}).get("status")
                    if isinstance(delta_metadata.get("validation"), dict)
                    else None
                ),
            },
        },
        "requirements": requirements,
    }
    return canonical, json_bytes(canonical), {
        "configured_artifact_schema_version": schema_version,
        "configured_artifact_sha256": sha256_bytes(artifact_bytes),
        "normalized": True,
    }


def validate_requirement_artifact(
    document: Any,
    artifact_bytes: bytes,
    source_bytes: bytes,
    source_lines: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    artifact = require_object(document, "requirements artifact")
    if artifact.get("schema_version") != REQUIREMENTS_SCHEMA:
        raise ConfigError(f"requirements schema_version must be {REQUIREMENTS_SCHEMA!r}")
    metadata = require_object(artifact.get("metadata"), "requirements metadata")
    source_sha256 = sha256_bytes(source_bytes)
    if metadata.get("source_sha256") != source_sha256:
        raise ConfigError("requirements source_sha256 does not match the standard")

    rows = artifact.get("requirements")
    if not isinstance(rows, list):
        raise ConfigError("requirements must be an array")

    seen_ids: set[str] = set()
    eligible: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        label = f"requirements[{index}]"
        requirement = require_object(row, label)
        requirement_id = requirement.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id:
            raise ConfigError(f"{label}.requirement_id must be a non-empty string")
        if requirement_id in seen_ids:
            raise ConfigError(f"duplicate requirement_id: {requirement_id}")
        seen_ids.add(requirement_id)

        eligibility = requirement.get("eligibility")
        if eligibility not in {"eligible", "exclude"}:
            raise ConfigError(f"{requirement_id}: invalid eligibility {eligibility!r}")
        if not any(
            isinstance(requirement.get(field), str)
            and requirement[field].strip()
            for field in (
                "required_behavior",
                "forbidden_behavior",
                "error_behavior",
            )
        ):
            if eligibility == "eligible":
                raise ConfigError(f"{requirement_id}: eligible item has no behavior")

        evidence = require_object(
            requirement.get("evidence"), f"{requirement_id}.evidence"
        )
        if evidence.get("verified") is not True:
            raise ConfigError(f"{requirement_id}: evidence.verified must be true")
        quote = evidence.get("quote")
        line_start = evidence.get("line_start")
        line_end = evidence.get("line_end")
        if not isinstance(quote, str) or not normalize_whitespace(quote):
            raise ConfigError(f"{requirement_id}: evidence.quote must not be empty")
        if (
            not isinstance(line_start, int)
            or isinstance(line_start, bool)
            or not isinstance(line_end, int)
            or isinstance(line_end, bool)
            or line_start < 1
            or line_end < line_start
            or line_end > len(source_lines)
        ):
            raise ConfigError(f"{requirement_id}: invalid evidence line interval")
        source_slice = normalize_whitespace(
            "\n".join(source_lines[line_start - 1 : line_end])
        )
        if normalize_whitespace(quote) not in source_slice:
            raise ConfigError(
                f"{requirement_id}: evidence quote does not match its source lines"
            )
        if eligibility == "eligible":
            eligible.append(requirement)

    declared_count = metadata.get("eligible_requirement_count")
    if declared_count is not None and declared_count != len(eligible):
        raise ConfigError(
            "requirements metadata.eligible_requirement_count does not match "
            f"the artifact ({declared_count!r} != {len(eligible)})"
        )
    if metadata.get("requirement_count") not in (None, len(rows)):
        raise ConfigError("requirements metadata.requirement_count is inconsistent")
    metadata_copy = dict(metadata)
    metadata_copy["requirements_sha256"] = sha256_bytes(artifact_bytes)
    return eligible, metadata_copy


def write_protected(path: Path, content: bytes, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ConfigError(f"output exists; use --overwrite to replace it: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def prepare(
    config_path: Path,
    source_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    config_path = config_path.resolve()
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    config = require_object(load_json(config_path), "run config")
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise ConfigError(f"run config schema_version must be {CONFIG_SCHEMA!r}")

    requirements_config = require_object(
        config.get("requirements"), "run config requirements"
    )
    reuse = requirements_config.get("reuse")
    if not isinstance(reuse, bool):
        raise ConfigError("requirements.reuse must be true or false")
    configured_artifact = requirements_config.get("artifact")
    destination = output_dir / "requirements.json"
    if reuse:
        if not isinstance(configured_artifact, str) or not configured_artifact.strip():
            raise ConfigError(
                "requirements.artifact is required when requirements.reuse is true"
            )
        artifact_path = resolve_path(configured_artifact, config_path.parent)
    else:
        if configured_artifact not in (None, ""):
            raise ConfigError(
                "requirements.artifact must be omitted when requirements.reuse is false"
            )
        artifact_path = destination
        if not artifact_path.is_file():
            raise ConfigError(
                "fresh extraction is required first; expected "
                f"{artifact_path}"
            )

    if not source_path.is_file():
        raise ConfigError(f"standard file does not exist: {source_path}")
    if not artifact_path.is_file():
        raise ConfigError(f"requirements artifact does not exist: {artifact_path}")

    source_bytes = source_path.read_bytes()
    try:
        source_text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"standard is not valid UTF-8: {exc}") from exc
    configured_artifact_bytes = artifact_path.read_bytes()
    configured_artifact_document = load_json(artifact_path)
    artifact_document, artifact_bytes, artifact_provenance = (
        normalize_requirement_document(
            configured_artifact_document,
            configured_artifact_bytes,
            source_bytes,
            len(source_text.splitlines()),
        )
    )
    eligible, artifact_metadata = validate_requirement_artifact(
        artifact_document,
        artifact_bytes,
        source_bytes,
        source_text.splitlines(),
    )
    if not eligible:
        raise ConfigError("requirements artifact contains no eligible requirements")

    triage = require_object(config.get("static_triage"), "run config static_triage")
    start = positive_int(triage.get("start"), "static_triage.start")
    raw_end = triage.get("end")
    end = len(eligible) if raw_end is None else positive_int(
        raw_end, "static_triage.end"
    )
    batch_size = positive_int(
        triage.get("batch_size"), "static_triage.batch_size"
    )
    if start > end:
        raise ConfigError("static_triage.start must not exceed static_triage.end")
    if end > len(eligible):
        raise ConfigError(
            f"static_triage.end {end} exceeds eligible requirement count "
            f"{len(eligible)}"
        )

    selected = eligible[start - 1 : end]
    selected_ids = [str(row["requirement_id"]) for row in selected]
    selection_sha256 = sha256_bytes(
        json.dumps(selected_ids, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )

    batch_dir = output_dir / "static-triage-batches"
    if overwrite and batch_dir.is_dir():
        for stale in batch_dir.glob("batch-*.json"):
            stale.unlink()

    descriptors: list[dict[str, Any]] = []
    batch_documents: list[tuple[Path, dict[str, Any]]] = []
    for batch_number, offset in enumerate(range(0, len(selected), batch_size), start=1):
        rows = selected[offset : offset + batch_size]
        position_start = start + offset
        position_end = position_start + len(rows) - 1
        batch_id = f"batch-{batch_number:04d}"
        input_rel = f"static-triage-batches/{batch_id}.input.json"
        output_rel = f"static-triage-batches/{batch_id}.output.json"
        requirement_ids = [str(row["requirement_id"]) for row in rows]
        descriptor = {
            "batch_id": batch_id,
            "position_start": position_start,
            "position_end": position_end,
            "requirement_count": len(rows),
            "requirement_ids": requirement_ids,
            "input": input_rel,
            "output": output_rel,
        }
        descriptors.append(descriptor)
        batch_documents.append(
            (
                output_dir / input_rel,
                {
                    "schema_version": BATCH_SCHEMA,
                    "metadata": {
                        "batch_id": batch_id,
                        "selection_sha256": selection_sha256,
                        "requirements_sha256": artifact_metadata[
                            "requirements_sha256"
                        ],
                        "position_start": position_start,
                        "position_end": position_end,
                        "requirement_count": len(rows),
                        "expected_output": output_rel,
                    },
                    "requirements": rows,
                },
            )
        )

    selection = {
        "schema_version": SELECTION_SCHEMA,
        "metadata": {
            "requirement_mode": "reuse" if reuse else "fresh",
            "standard_sha256": sha256_bytes(source_bytes),
            "requirements_sha256": artifact_metadata["requirements_sha256"],
            "total_requirement_count": len(
                artifact_document.get("requirements", [])
            ),
            "total_eligible_requirement_count": len(eligible),
            "start": start,
            "end": end,
            "batch_size": batch_size,
            "selected_requirement_count": len(selected),
            "batch_count": len(descriptors),
            "selection_sha256": selection_sha256,
        },
        "requirements": selected,
        "batches": descriptors,
    }
    resolved_config = {
        "schema_version": CONFIG_SCHEMA,
        "requirements": {
            "reuse": reuse,
            "artifact": str(artifact_path),
            "resolved_run_artifact": str(destination),
            "requirements_sha256": artifact_metadata["requirements_sha256"],
            **artifact_provenance,
        },
        "static_triage": {
            "start": start,
            "end": end,
            "batch_size": batch_size,
        },
    }

    protected_outputs = [
        *(path for path, _ in batch_documents),
        output_dir / "requirement_selection.json",
        output_dir / "run_config.resolved.json",
    ]
    if not overwrite:
        existing = [path for path in protected_outputs if path.exists()]
        stale_batches = (
            list(batch_dir.glob("batch-*.json")) if batch_dir.is_dir() else []
        )
        if existing or stale_batches:
            conflict = (existing or stale_batches)[0]
            raise ConfigError(
                f"output exists; use --overwrite to replace it: {conflict}"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    if artifact_path.resolve() != destination.resolve():
        if destination.exists():
            if destination.read_bytes() != artifact_bytes and not overwrite:
                raise ConfigError(
                    f"run requirements snapshot already differs: {destination}"
                )
        if overwrite or not destination.exists():
            destination.write_bytes(artifact_bytes)
    for path, document in batch_documents:
        write_protected(path, json_bytes(document), overwrite)
    write_protected(
        output_dir / "requirement_selection.json",
        json_bytes(selection),
        overwrite,
    )
    write_protected(
        output_dir / "run_config.resolved.json",
        json_bytes(resolved_config),
        overwrite,
    )
    return {
        "ok": True,
        "requirement_mode": selection["metadata"]["requirement_mode"],
        "total_eligible_requirement_count": len(eligible),
        "selected_requirement_count": len(selected),
        "start": start,
        "end": end,
        "batch_size": batch_size,
        "batch_count": len(descriptors),
        "selection": str(output_dir / "requirement_selection.json"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve requirement reuse and create static-triage batches."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = prepare(
            args.config,
            args.source,
            args.output_dir,
            overwrite=args.overwrite,
        )
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
