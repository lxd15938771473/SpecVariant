#!/usr/bin/env python3
"""Generate bounded, typed test candidates from verified atomic requirements."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"
GENERATOR_NAME = "speclitmus-test-generator"
SUPPORTED_MODALITIES = {"MUST", "MUST NOT", "SHOULD", "SHOULD NOT"}
TRIAGE_SCHEMA_VERSION = "speclitmus.static-triage.v1"
TRIAGE_VERDICTS = {"no_issue", "suspected_issue", "issue_found"}
TRIAGE_GENERATE_VERDICTS = {"suspected_issue", "issue_found"}
MAX_VARIANTS_LIMIT = 4

RISK_ORDER = {
    "replay": 0,
    "state_order": 1,
    "boundary": 2,
    "unknown": 3,
    "duplicate": 4,
    "error_mapping": 5,
    "missing": 6,
}

RISK_ACTION = {
    "missing": "omit_required_element_or_action",
    "duplicate": "duplicate_single_element_or_message",
    "boundary": "set_nearest_out_of_range_value",
    "unknown": "substitute_unknown_or_reserved_value",
    "state_order": "deliver_in_adjacent_invalid_state",
    "replay": "replay_on_fresh_connection",
    "error_mapping": "trigger_violation_and_capture_error",
}

RISK_LABEL = {
    "missing": "Omit the required element or action",
    "duplicate": "Duplicate one element or message",
    "boundary": "Use the nearest invalid boundary value",
    "unknown": "Substitute an unknown or reserved value",
    "state_order": "Deliver the input in an adjacent invalid state",
    "replay": "Replay the input on a fresh connection",
    "error_mapping": "Trigger the violation and verify the exact error",
}

KEYWORDS = {
    "missing": {
        "contain",
        "contains",
        "include",
        "includes",
        "present",
        "presence",
        "provide",
        "provides",
        "required",
        "send",
        "sends",
        "field",
        "extension",
        "message",
        "parameter",
        "action",
    },
    "duplicate": {
        "duplicate",
        "exactly",
        "once",
        "single",
        "unique",
        "list",
        "entry",
        "field",
        "extension",
        "message",
        "parameter",
        "frame",
        "header",
    },
    "boundary": {
        "length",
        "size",
        "count",
        "limit",
        "range",
        "maximum",
        "minimum",
        "max",
        "min",
        "less",
        "greater",
        "bytes",
        "bits",
        "integer",
        "number",
        "zero",
    },
    "unknown": {
        "unknown",
        "reserved",
        "value",
        "type",
        "code",
        "identifier",
        "version",
        "algorithm",
        "cipher",
        "extension",
        "parameter",
        "enum",
    },
    "state_order": {
        "before",
        "after",
        "state",
        "phase",
        "sequence",
        "order",
        "handshake",
        "initial",
        "first",
        "last",
        "prior",
        "subsequent",
    },
    "replay": {
        "replay",
        "retransmit",
        "retransmission",
        "resumption",
        "ticket",
        "early",
        "nonce",
        "token",
        "connection",
        "session",
        "request",
        "message",
    },
    "error_mapping": {
        "error",
        "alert",
        "reject",
        "abort",
        "discard",
        "close",
        "terminate",
        "failure",
        "invalid",
        "illegal",
    },
}

BASE_SCORE = {
    "missing": 56,
    "duplicate": 61,
    "boundary": 68,
    "unknown": 66,
    "state_order": 72,
    "replay": 76,
    "error_mapping": 64,
}


class InputError(ValueError):
    """Raised when an upstream requirement violates the stage contract."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any, length: int = 12) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:length]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _require_string(item: dict[str, Any], field: str, index: int, allow_empty: bool = False) -> str:
    value = item.get(field)
    if not isinstance(value, str):
        raise InputError(f"requirements[{index}].{field} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise InputError(f"requirements[{index}].{field} must not be empty")
    return value


def _normalize_exceptions(value: Any, index: int) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise InputError(f"requirements[{index}].exceptions must be a string or array of strings")
    return [entry.strip() for entry in value if entry.strip()]


def _normalize_modality(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper().replace("_", " "))


def validate_requirements(document: Any) -> list[dict[str, Any]]:
    """Validate and normalize input without weakening evidence semantics."""
    raw = document.get("requirements") if isinstance(document, dict) else document
    agent_schema = (
        isinstance(document, dict)
        and document.get("schema_version")
        in {
            "speclitmus.requirements.v3",
            "speclitmus.requirement-selection.v1",
        }
    )
    if not isinstance(raw, list):
        raise InputError("input must be a JSON array or an object with a requirements array")
    if not raw:
        raise InputError("requirements array must not be empty")

    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, source in enumerate(raw):
        if not isinstance(source, dict):
            raise InputError(f"requirements[{index}] must be an object")

        requirement_id = _require_string(source, "requirement_id", index)
        if requirement_id in seen_ids:
            raise InputError(f"duplicate requirement_id: {requirement_id}")
        seen_ids.add(requirement_id)

        if agent_schema:
            eligibility = _require_string(source, "eligibility", index)
            if eligibility not in {"eligible", "exclude"}:
                raise InputError(
                    f"requirements[{index}].eligibility must be eligible or exclude"
                )
            if eligibility == "exclude":
                continue
            required_behavior = _require_string(
                source, "required_behavior", index, allow_empty=True
            )
            forbidden_behavior = _require_string(
                source, "forbidden_behavior", index, allow_empty=True
            )
            error_behavior = _require_string(
                source, "error_behavior", index, allow_empty=True
            )
            if forbidden_behavior and not required_behavior:
                modality = "MUST NOT"
                behavior = forbidden_behavior
            else:
                modality = "MUST"
                behavior = required_behavior or error_behavior
            if not behavior:
                raise InputError(
                    f"requirements[{index}] has no required, forbidden, or error behavior"
                )
        else:
            modality = _normalize_modality(_require_string(source, "modality", index))
            if modality not in SUPPORTED_MODALITIES:
                raise InputError(
                    f"requirements[{index}].modality must be one of "
                    + ", ".join(sorted(SUPPORTED_MODALITIES))
                )
            required_behavior = _require_string(source, "required_behavior", index)
            behavior = required_behavior
            forbidden_behavior = (
                required_behavior
                if modality in {"MUST NOT", "SHOULD NOT"}
                else ""
            )
            error_behavior = _require_string(
                source, "error_behavior", index, allow_empty=True
            )

        evidence = source.get("evidence")
        if not isinstance(evidence, dict):
            raise InputError(f"requirements[{index}].evidence must be an object")
        if evidence.get("verified") is not True:
            raise InputError(f"requirements[{index}].evidence.verified must be true")
        source_id = evidence.get("source_id", evidence.get("source_path"))
        if not isinstance(source_id, str) or not source_id.strip():
            raise InputError(
                f"requirements[{index}].evidence requires a non-empty source_id or source_path"
            )
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote:
            raise InputError(f"requirements[{index}].evidence.quote must not be empty")
        line_start = evidence.get("line_start")
        line_end = evidence.get("line_end")
        if (
            not isinstance(line_start, int)
            or isinstance(line_start, bool)
            or not isinstance(line_end, int)
            or isinstance(line_end, bool)
            or line_start < 1
            or line_end < line_start
        ):
            raise InputError(
                f"requirements[{index}].evidence line_start/line_end must be a positive interval"
            )

        condition = _require_string(source, "condition", index, allow_empty=True)
        normalized = {
            "requirement_id": requirement_id,
            "section": (
                f"{source_id.strip()} lines {line_start}-{line_end}"
                if agent_schema
                else _require_string(source, "section", index)
            ),
            "modality": modality,
            "subject": (
                behavior
                if agent_schema
                else _require_string(source, "subject", index)
            ),
            "condition": condition,
            "required_behavior": behavior,
            "forbidden_behavior": forbidden_behavior,
            "exceptions": (
                [] if agent_schema else _normalize_exceptions(source.get("exceptions"), index)
            ),
            "error_behavior": error_behavior,
            "check_type": (
                _require_string(source, "check_type", index)
                if agent_schema
                else "explicit_normative"
            ),
            # Preserve the parsed evidence object exactly. Validation above does
            # not add, remove, or rewrite provenance fields.
            "evidence": copy.deepcopy(evidence),
        }
        result.append(normalized)
    return result


def _triage_records(document: Any) -> list[dict[str, Any]]:
    raw = document.get("records") if isinstance(document, dict) else document
    if not isinstance(raw, list):
        raise InputError("static triage must be a JSON array or an object with a records array")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(raw):
        if not isinstance(record, dict):
            raise InputError(f"static_triage.records[{index}] must be an object")
        requirement_id = record.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise InputError(
                f"static_triage.records[{index}].requirement_id must be a non-empty string"
            )
        if requirement_id in seen:
            raise InputError(f"duplicate static triage requirement_id: {requirement_id}")
        seen.add(requirement_id)
        verdict = record.get("verdict")
        if verdict not in TRIAGE_VERDICTS:
            raise InputError(
                f"static_triage.records[{index}].verdict must be one of "
                + ", ".join(sorted(TRIAGE_VERDICTS))
            )
        records.append(record)
    return records


def apply_static_triage(
    requirements: list[dict[str, Any]], triage_document: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return requirements whose static triage requires candidate generation."""
    records = _triage_records(triage_document)
    by_id = {record["requirement_id"]: record for record in records}
    requirement_ids = [requirement["requirement_id"] for requirement in requirements]
    missing = sorted(set(requirement_ids) - set(by_id))
    unknown = sorted(set(by_id) - set(requirement_ids))
    if missing:
        raise InputError(f"static triage missing requirement IDs: {missing}")
    if unknown:
        raise InputError(f"static triage contains unknown requirement IDs: {unknown}")

    selected: list[dict[str, Any]] = []
    skipped: list[str] = []
    verdict_counts = {verdict: 0 for verdict in sorted(TRIAGE_VERDICTS)}
    for requirement in requirements:
        record = by_id[requirement["requirement_id"]]
        verdict = record["verdict"]
        verdict_counts[verdict] += 1
        if verdict in TRIAGE_GENERATE_VERDICTS:
            selected.append(requirement)
        else:
            skipped.append(requirement["requirement_id"])

    return selected, {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "records_consumed": len(records),
        "verdict_counts": verdict_counts,
        "requirements_selected_for_generation": len(selected),
        "requirements_skipped_after_static_no_issue": len(skipped),
        "skipped_requirement_ids": skipped,
    }


def _combined_text(requirement: dict[str, Any]) -> str:
    fields: Iterable[str] = (
        requirement["subject"],
        requirement["condition"],
        requirement["required_behavior"],
        " ".join(requirement["exceptions"]),
        requirement["error_behavior"],
        requirement["evidence"]["quote"],
    )
    return " ".join(fields)


def _extract_boundary_hints(text: str) -> dict[str, Any]:
    """Extract only numeric bounds whose direction is explicit."""
    normalized = re.sub(r"\s+", " ", text.lower())
    hints: dict[str, Any] = {}

    between = re.search(r"\bbetween\s+(\d+)\s+and\s+(\d+)\b", normalized)
    if between:
        lower_value, upper_value = (int(value) for value in between.groups())
        if lower_value <= upper_value:
            hints["lower"] = {
                "value": lower_value,
                "inclusive": True,
                "nearest_valid": lower_value,
                "nearest_outside": lower_value - 1,
            }
            hints["upper"] = {
                "value": upper_value,
                "inclusive": True,
                "nearest_valid": upper_value,
                "nearest_outside": upper_value + 1,
            }

    patterns = [
        (
            "upper",
            True,
            r"(?:\bno greater than\b|\bat most\b|\bmaximum(?:\s+(?:of|is))?\b|<=)\s*:?\s*(\d+)",
        ),
        (
            "upper",
            False,
            r"(?:\bless than\b|(?<!<)<(?![=]))\s*:?\s*(\d+)",
        ),
        (
            "lower",
            True,
            r"(?:\bno less than\b|\bat least\b|\bminimum(?:\s+(?:of|is))?\b|>=)\s*:?\s*(\d+)",
        ),
        (
            "lower",
            False,
            r"(?:\bgreater than\b|(?<!>)>(?![=]))\s*:?\s*(\d+)",
        ),
    ]
    for direction, inclusive, pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        value = int(match.group(1))
        if direction == "upper":
            nearest_valid = value if inclusive else value - 1
            nearest_outside = value + 1 if inclusive else value
        else:
            nearest_valid = value if inclusive else value + 1
            nearest_outside = value - 1 if inclusive else value
        hints[direction] = {
            "value": value,
            "inclusive": inclusive,
            "nearest_valid": nearest_valid,
            "nearest_outside": nearest_outside,
        }
    return hints


def _risk_scores(requirement: dict[str, Any]) -> list[tuple[str, int]]:
    text = _combined_text(requirement)
    tokens = _tokens(text)
    scores: list[tuple[str, int]] = []

    for risk_class, risk_words in KEYWORDS.items():
        hits = len(tokens & risk_words)
        applicable = hits > 0

        # Omission is a broadly useful direct negative probe for positive
        # obligations even if a requirement uses an uncommon noun.
        if risk_class == "missing" and requirement["modality"] in {"MUST", "SHOULD"}:
            applicable = True

        # Error mapping is applicable when the atomic requirement has an
        # explicit failure outcome or prohibits behavior.
        if risk_class == "error_mapping" and (
            requirement["error_behavior"]
            or requirement["modality"] in {"MUST NOT", "SHOULD NOT"}
        ):
            applicable = True
            hits += 2

        # Numeric literals are strong boundary evidence.
        if risk_class == "boundary" and re.search(r"\b\d+\b", text):
            applicable = True
            hits += 2

        if applicable:
            score = min(99, BASE_SCORE[risk_class] + min(hits, 5) * 3)
            scores.append((risk_class, score))

    scores.sort(key=lambda item: (-item[1], RISK_ORDER[item[0]]))
    return scores


def _snapshot(requirement: dict[str, Any]) -> dict[str, Any]:
    return {
        "section": requirement["section"],
        "modality": requirement["modality"],
        "subject": requirement["subject"],
        "condition": requirement["condition"],
        "required_behavior": requirement["required_behavior"],
        "forbidden_behavior": requirement["forbidden_behavior"],
        "exceptions": copy.deepcopy(requirement["exceptions"]),
        "error_behavior": requirement["error_behavior"],
        "check_type": requirement["check_type"],
    }


def _oracle(requirement: dict[str, Any]) -> dict[str, Any]:
    negative = requirement["modality"] in {"MUST NOT", "SHOULD NOT"}
    behavior = requirement["required_behavior"]
    return {
        "normative_direction": "forbid" if negative else "require",
        "expected_behavior": "" if negative else behavior,
        "forbidden_behavior": behavior if negative else "",
        "expected_error": requirement["error_behavior"],
        "condition": requirement["condition"],
        "exceptions": copy.deepcopy(requirement["exceptions"]),
    }


def _setup(requirement: dict[str, Any], risk_class: str) -> dict[str, Any]:
    connection_model = "fresh_pair" if risk_class == "replay" else "single"
    return {
        "type": "protocol_preconditions",
        "condition": requirement["condition"],
        "exceptions_to_avoid": copy.deepcopy(requirement["exceptions"]),
        "connections": connection_model,
        "target_subject": requirement["subject"],
    }


def _base_events(requirement: dict[str, Any]) -> list[dict[str, Any]]:
    negative = requirement["modality"] in {"MUST NOT", "SHOULD NOT"}
    construct_action = (
        "construct_minimal_prohibited_case"
        if negative
        else "construct_minimal_triggering_case"
    )
    return [
        {
            "seq": 1,
            "type": "setup",
            "actor": "harness",
            "connection": "primary",
            "action": "apply_requirement_preconditions",
            "parameters": {
                "condition": requirement["condition"],
                "exceptions_to_avoid": copy.deepcopy(requirement["exceptions"]),
            },
        },
        {
            "seq": 2,
            "type": "construct",
            "actor": "peer",
            "connection": "primary",
            "action": construct_action,
            "parameters": {
                "subject": requirement["subject"],
                "required_behavior": requirement["required_behavior"],
            },
        },
        {
            "seq": 3,
            "type": "send",
            "actor": "peer",
            "connection": "primary",
            "action": "deliver_to_implementation",
            "parameters": {"subject": requirement["subject"]},
        },
        {
            "seq": 4,
            "type": "observe",
            "actor": "harness",
            "connection": "primary",
            "action": "capture_normative_outcome",
            "parameters": {
                "capture": [
                    "acceptance_or_rejection",
                    "output",
                    "state_transition",
                    "error",
                ]
            },
        },
    ]


def _variant_events(requirement: dict[str, Any], risk_class: str) -> list[dict[str, Any]]:
    events = _base_events(requirement)
    parameters: dict[str, Any] = {
        "target": requirement["subject"],
        "single_operation": True,
        "risk_class": risk_class,
    }
    if risk_class == "boundary":
        boundary_hints = _extract_boundary_hints(_combined_text(requirement))
        if boundary_hints:
            parameters["boundary_hints"] = boundary_hints
    mutation = {
        "seq": 3,
        "type": "mutate",
        "actor": "harness",
        "connection": "secondary" if risk_class == "replay" else "primary",
        "action": RISK_ACTION[risk_class],
        "parameters": parameters,
    }
    events.insert(2, mutation)
    for seq, event in enumerate(events, start=1):
        event["seq"] = seq
    if risk_class == "replay":
        events[3]["connection"] = "secondary"
        events[4]["connection"] = "secondary"
    return events


def _candidate_id(requirement: dict[str, Any], risk_class: str) -> str:
    identity = {
        "requirement_id": requirement["requirement_id"],
        "requirement": _snapshot(requirement),
        "evidence": requirement["evidence"],
        "risk_class": risk_class,
    }
    return f"cand-{_digest(identity)}-{risk_class.replace('_', '-')}"


def _base_candidate(requirement: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": _candidate_id(requirement, "baseline"),
        "requirement_id": requirement["requirement_id"],
        "kind": "base",
        "name": f"Direct normative probe: {requirement['subject']}",
        "risk_class": "baseline",
        "setup": _setup(requirement, "baseline"),
        "events": _base_events(requirement),
        "oracle": _oracle(requirement),
        "expected_strength": (
            "required"
            if requirement["modality"] in {"MUST", "MUST NOT"}
            else "recommended"
        ),
        "feasibility": "high",
        "value_score": 100,
        "standard_evidence": copy.deepcopy(requirement["evidence"]),
        "requirement_snapshot": _snapshot(requirement),
    }


def _variant_candidate(
    requirement: dict[str, Any], risk_class: str, score: int
) -> dict[str, Any]:
    feasibility = "medium" if risk_class in {"replay", "state_order"} else "high"
    return {
        "candidate_id": _candidate_id(requirement, risk_class),
        "requirement_id": requirement["requirement_id"],
        "kind": "variant",
        "name": f"{RISK_LABEL[risk_class]}: {requirement['subject']}",
        "risk_class": risk_class,
        "setup": _setup(requirement, risk_class),
        "events": _variant_events(requirement, risk_class),
        "oracle": _oracle(requirement),
        "expected_strength": (
            "required"
            if requirement["modality"] in {"MUST", "MUST NOT"}
            else "recommended"
        ),
        "feasibility": feasibility,
        "value_score": score,
        "standard_evidence": copy.deepcopy(requirement["evidence"]),
        "requirement_snapshot": _snapshot(requirement),
    }


def generate_document(
    document: Any, max_variants: int = 4, static_triage: Any | None = None
) -> dict[str, Any]:
    """Return the deterministic candidate document for an input JSON value."""
    if (
        not isinstance(max_variants, int)
        or isinstance(max_variants, bool)
        or not 0 <= max_variants <= MAX_VARIANTS_LIMIT
    ):
        raise InputError(
            f"max_variants must be an integer between 0 and {MAX_VARIANTS_LIMIT}"
        )
    requirements = validate_requirements(document)
    static_triage_summary: dict[str, Any] | None = None
    if static_triage is not None:
        requirements, static_triage_summary = apply_static_triage(
            requirements, static_triage
        )
    candidates: list[dict[str, Any]] = []
    variant_count = 0
    for requirement in requirements:
        candidates.append(_base_candidate(requirement))
        selected = _risk_scores(requirement)[:max_variants]
        for risk_class, score in selected:
            candidates.append(_variant_candidate(requirement, risk_class, score))
            variant_count += 1

    ids = [candidate["candidate_id"] for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise RuntimeError("internal error: duplicate candidate_id generated")

    generation_summary = {
        "requirements_consumed": len(requirements),
        "base_tests": len(requirements),
        "variants": variant_count,
        "candidates": len(candidates),
    }
    if static_triage_summary is not None:
        generation_summary["static_triage"] = static_triage_summary

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "name": GENERATOR_NAME,
            "max_variants_per_requirement": max_variants,
        },
        "generation_summary": generation_summary,
        "candidates": candidates,
    }


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise InputError(f"requirements file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in {path}: {exc}") from exc


def _write_json_atomic(path: Path, document: dict[str, Any], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise InputError(f"output already exists (use --overwrite): {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not overwrite:
            raise InputError(f"output already exists (use --overwrite): {path}")
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one direct base test and a bounded set of high-risk typed "
            "variants for each verified atomic requirement."
        )
    )
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-variants", type=int, default=4)
    parser.add_argument(
        "--static-triage",
        type=Path,
        default=None,
        help=(
            "optional static triage JSON; only suspected_issue and "
            "issue_found requirements generate candidates"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace the named output if it already exists",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = _read_json(args.requirements)
        static_triage = _read_json(args.static_triage) if args.static_triage else None
        output = generate_document(
            source, max_variants=args.max_variants, static_triage=static_triage
        )
        _write_json_atomic(args.out, output, overwrite=args.overwrite)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"generated {output['generation_summary']['candidates']} candidates "
        f"from {output['generation_summary']['requirements_consumed']} requirements: "
        f"{args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
