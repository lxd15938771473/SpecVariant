#!/usr/bin/env python3
"""Normalize litmus families/variants into harness-ready IR.

This is step 4 of the SpecLitmus prototype:
  litmus families -> executable intermediate representation (IR).

The script is intentionally deterministic. It does not call an LLM.
It normalizes natural-language traces into adapter-neutral events,
splits oracles into setup checks / normative checks / observations,
infers execution requirements, and validates evidence links.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


GENERIC_FEATURE_CHECKS = [
    ("duplicate", "duplicate"),
    ("second instance", "duplicate"),
    ("replay", "replay"),
    ("resend", "replay"),
    ("reconnect", "multi_connection"),
    ("new connection", "multi_connection"),
    ("open new connection", "multi_connection"),
    ("out of order", "reordering"),
    ("reorder", "reordering"),
    ("delay", "delay"),
    ("timeout", "timeout"),
    ("fragment", "fragmentation"),
    ("missing", "missing_field"),
    ("without", "missing_field"),
    ("invalid", "invalid_value"),
    ("unknown", "unknown_value"),
    ("fallback", "fallback"),
    ("decline", "decline"),
]

MESSAGE_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "data",
    "duplicate",
    "first",
    "invalid",
    "message",
    "mutated",
    "replayed",
    "same",
    "second",
    "the",
    "valid",
    "with",
    "without",
}

PARAMETER_CUE_RE = re.compile(
    r"\b(?:with|without|containing|contains|including|includes|missing|using)\b\s+(.+)$",
    re.I,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9_-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def loose_text(value: Any) -> str:
    text = compact_ws(str(value or "")).casefold()
    return re.sub(r"[^\w\s-]", "", text)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_families(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = read_json(path)
    if isinstance(data, dict) and isinstance(data.get("families"), list):
        return data.get("metadata", {"source": str(path)}), data["families"]
    if isinstance(data, list):
        return {"source": str(path)}, data
    raise ValueError(f"Unsupported family JSON shape: {path}")


def load_chunk_line_map(path: Path | None) -> dict[int, str]:
    if not path:
        return {}
    line_map: dict[int, str] = {}
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            for source_line in row.get("lines", []) or []:
                try:
                    n = int(source_line["n"])
                except (KeyError, TypeError, ValueError):
                    continue
                line_map[n] = str(source_line.get("text", ""))
    return line_map


def verify_evidence(evidence: dict[str, Any], line_map: dict[int, str]) -> dict[str, Any]:
    normalized = {
        "item_id": evidence.get("item_id"),
        "line_start": evidence.get("line_start"),
        "line_end": evidence.get("line_end"),
        "quote": evidence.get("quote", ""),
        "verified": "unknown",
    }
    if not line_map:
        return normalized

    try:
        line_start = int(evidence.get("line_start"))
        line_end = int(evidence.get("line_end"))
    except (TypeError, ValueError):
        normalized["verified"] = False
        normalized["reason"] = "invalid line range"
        return normalized

    if line_start > line_end:
        normalized["verified"] = False
        normalized["reason"] = "line_start greater than line_end"
        return normalized

    quote = loose_text(evidence.get("quote", ""))
    source_text = loose_text(" ".join(line_map.get(n, "") for n in range(line_start, line_end + 1)))
    if not quote or not source_text:
        normalized["verified"] = False
        normalized["reason"] = "missing quote or source lines"
    else:
        normalized["verified"] = quote in source_text or source_text in quote
        if not normalized["verified"]:
            normalized["reason"] = "quote not found in cited lines"
    return normalized


def infer_protocol(case_blob: str, metadata: dict[str, Any], override: str | None = None) -> str:
    if override:
        return override

    meta_blob = normalize_text(metadata)
    for blob in (meta_blob, case_blob):
        match = re.search(r"\brfc[_-]?(\d+)\b", blob)
        if match:
            return f"RFC{match.group(1)}"

    source = metadata.get("source") or metadata.get("litmus_file") or metadata.get("semantics_file")
    if source:
        stem = Path(str(source)).stem
        if stem:
            return stem
    return "unknown"


def canonical_label(text: str, fallback: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[0-9]+(?:-[A-Za-z0-9]+)?", text)
    kept: list[str] = []
    for word in words:
        normalized = word.casefold()
        if normalized in MESSAGE_STOPWORDS:
            continue
        kept.append(word)
        if len(kept) >= 4:
            break
    if not kept:
        return fallback
    return ".".join(kept)


def infer_message_type(step: dict[str, Any]) -> str:
    for key in ("message_type", "message", "object"):
        value = compact_ws(step.get(key, ""))
        if value:
            value = PARAMETER_CUE_RE.split(value, maxsplit=1)[0].strip() or value
            return canonical_label(value, "ProtocolMessage")

    action = compact_ws(step.get("action", ""))
    match = re.search(r"\b(?:send|receive|capture|observe|drop|block|repeat|resend)\s+(.+)$", action, re.I)
    if match:
        return canonical_label(match.group(1), "ProtocolMessage")
    return "ProtocolMessage"


def infer_parameter_hints(step: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("message", "mutation", "action"):
        value = compact_ws(step.get(key, ""))
        match = PARAMETER_CUE_RE.search(value)
        if not match:
            continue
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", match.group(1)):
            normalized = token.casefold()
            if normalized not in MESSAGE_STOPWORDS and normalized not in {"or", "only"}:
                hints.append(token)
    return list(dict.fromkeys(hints))


def infer_features(text: Any) -> list[str]:
    blob = normalize_text(text)
    features: list[str] = []
    for needle, feature in GENERIC_FEATURE_CHECKS:
        if needle in blob and feature not in features:
            features.append(feature)
    return features


def is_noop_mutation(value: Any) -> bool:
    return value is None or normalize_text(value) in {"", "none", "null", "n a"}


def infer_operation(action: Any, role: str) -> str:
    blob = normalize_text(action)
    if "reconnect" in blob or "open new connection" in blob:
        return "send"
    if "send" in blob or "resend" in blob or "repeat" in blob:
        return "send"
    if "receive" in blob or "capture" in blob or "observe" in blob:
        return "observe"
    if "delay" in blob or "wait" in blob or "timeout" in blob:
        return "wait"
    if "drop" in blob or "block" in blob:
        return "drop"
    if role == "positive_control":
        return "send"
    return "action"


def infer_mutation(
    *,
    event_blob: str,
    role: str,
    prior_events: list[dict[str, Any]],
    explicit_mutation: Any = None,
    current_message_type: str | None = None,
) -> dict[str, Any] | None:
    mutation_blob = normalize_text(explicit_mutation)
    has_explicit_mutation = not is_noop_mutation(explicit_mutation)
    mutation_type = None
    if (
        "duplicate" in event_blob
        or "second instance" in event_blob
        or (role == "duplication" and has_explicit_mutation)
        or ("duplicate" in mutation_blob)
    ):
        mutation_type = "duplicate"
    elif (
        "replay" in event_blob
        or "reconnect" in event_blob
        or "new connection" in event_blob
        or "same 0-rtt" in event_blob
        or "same 0 rtt" in event_blob
        or (role == "replay" and has_explicit_mutation)
        or ("replay" in mutation_blob)
    ):
        mutation_type = "replay"
    elif role == "negotiation_mismatch" or "without key share" in event_blob or "missing key share" in event_blob:
        mutation_type = "negotiation_mismatch"
    elif role == "reordering" or "reorder" in event_blob or "out of order" in event_blob:
        mutation_type = "reordering"
    elif role == "timeout" or "timeout" in event_blob or "delay" in event_blob:
        mutation_type = "delay"
    elif role == "fragmentation" or "fragment" in event_blob:
        mutation_type = "fragmentation"

    if not mutation_type:
        return None

    target_event = None
    if mutation_type in {"duplicate", "replay"}:
        for prior in reversed(prior_events):
            if current_message_type and prior.get("message_type") == current_message_type:
                target_event = prior["event_id"]
                break
        if target_event is None and prior_events:
            target_event = prior_events[-1]["event_id"]

    return {
        "type": mutation_type,
        "target_event": target_event,
        "description": event_blob,
    }


def next_connection_id(
    *,
    event_blob: str,
    role: str,
    current_connection: int,
) -> int:
    if role == "replay" and ("reconnect" in event_blob or "new connection" in event_blob):
        return current_connection + 1
    if "reconnect" in event_blob or "open new connection" in event_blob or "new connection" in event_blob:
        return current_connection + 1
    return current_connection


def normalize_trace(variant: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    current_connection = 1
    role = str(variant.get("role", ""))

    for idx, step in enumerate(as_list(variant.get("trace")), start=1):
        if not isinstance(step, dict):
            warnings.append(f"trace step {idx} is not an object")
            continue

        raw_blob = normalize_text(step)
        current_connection = next_connection_id(
            event_blob=raw_blob,
            role=role,
            current_connection=current_connection,
        )
        message_type = infer_message_type(step)
        features = infer_features(step)
        mutation = infer_mutation(
            event_blob=raw_blob,
            role=role,
            prior_events=events,
            explicit_mutation=step.get("mutation"),
            current_message_type=message_type,
        )
        if mutation and mutation["type"] not in features:
            features.append(mutation["type"])

        event = {
            "event_id": f"e{len(events) + 1}",
            "source_step": step.get("step", idx),
            "connection": f"c{current_connection}",
            "actor": step.get("actor", "test_harness"),
            "operation": infer_operation(step.get("action"), role),
            "message_type": message_type,
            "message": step.get("message"),
            "parameter_hints": infer_parameter_hints(step),
            "features": features,
            "mutation": mutation,
            "expected_observation": step.get("expected_observation", ""),
            "raw_action": step.get("action", ""),
        }
        if message_type == "ProtocolMessage":
            warnings.append(f"{event['event_id']} has generic message type")
        events.append(event)

    return events, warnings


def infer_setup(
    *,
    variant: dict[str, Any],
    events: list[dict[str, Any]],
    metadata: dict[str, Any],
    protocol: str | None = None,
) -> dict[str, Any]:
    blob = normalize_text({"variant": variant, "events": events})
    plan = variant.get("harness_plan") or {}
    max_conn = 1
    for event in events:
        match = re.match(r"c(\d+)", str(event.get("connection", "")))
        if match:
            max_conn = max(max_conn, int(match.group(1)))

    mutation_types = {
        event["mutation"]["type"]
        for event in events
        if isinstance(event.get("mutation"), dict) and event["mutation"].get("type")
    }
    role = str(variant.get("role", ""))

    return {
        "protocol": infer_protocol(blob, metadata, override=protocol),
        "requirements": {
            "connections": max(max_conn, 2 if plan.get("needs_multi_connection") else 1),
            "needs_network_scheduler": bool(plan.get("needs_network_scheduler", False)),
            "needs_fault_injection": bool(plan.get("needs_fault_injection", False)),
            "needs_multi_connection": bool(plan.get("needs_multi_connection", False)) or max_conn > 1,
            "needs_replay_buffer": (
                bool(plan.get("needs_replay_buffer", False))
                or role == "replay"
                or "replay" in mutation_types
            ),
            "needs_timer_control": bool(plan.get("needs_timer_control", False)),
        },
        "capability_hints": sorted(
            {
                feature
                for event in events
                for feature in event.get("features", [])
                if feature
            }
        ),
        "message_types": sorted({event.get("message_type") for event in events if event.get("message_type")}),
        "preconditions": as_list(variant.get("preconditions")),
        "observable_signals": as_list(plan.get("observable_signals")),
        "adapter_hooks": as_list(plan.get("adapter_hooks")),
    }


def evidence_status(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "missing"
    values = [ev.get("verified") for ev in evidence]
    if all(value is True for value in values):
        return "verified"
    if any(value is True for value in values):
        return "partial"
    if all(value == "unknown" for value in values):
        return "unknown"
    return "unverified"


def split_oracle(
    *,
    variant: dict[str, Any],
    events: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    role = str(variant.get("role", ""))
    expected_strength = str(variant.get("expected_strength", "conformance")).lower()
    raw_oracle = variant.get("oracle") or {}
    check_common = {
        "source": "variant_oracle",
        "oracle_type": raw_oracle.get("type", "other"),
        "expected": raw_oracle.get("expected", ""),
        "violation": raw_oracle.get("violation", ""),
        "verdicts": raw_oracle.get("verdicts", {}),
        "evidence_refs": [ev.get("item_id") for ev in evidence if ev.get("item_id")],
    }

    setup_checks: list[dict[str, Any]] = []
    normative_checks: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    if role == "positive_control":
        setup_checks.append(
            {
                **check_common,
                "check_id": "setup_1",
                "kind": "baseline_reachable",
                "target_events": [event["event_id"] for event in events],
            }
        )
    elif expected_strength == "conformance":
        normative_checks.append(
            {
                **check_common,
                "check_id": "normative_1",
                "kind": "conformance",
                "target_events": [event["event_id"] for event in events],
            }
        )
    else:
        observations.append(
            {
                **check_common,
                "observation_id": "observation_1",
                "kind": expected_strength,
                "target_events": [event["event_id"] for event in events],
                "is_conformance_failure": False,
            }
        )

    return {
        "setup_checks": setup_checks,
        "normative_checks": normative_checks,
        "observations": observations,
    }


def validate_case(case: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = list(case.pop("_warnings", []))

    if not case["events"]:
        errors.append("no normalized events")
    if case["evidence_status"] in {"missing", "unverified"}:
        warnings.append(f"evidence status is {case['evidence_status']}")

    role = case["variant"]["role"]
    if role in {"duplication", "replay"}:
        mutated = [
            event
            for event in case["events"]
            if isinstance(event.get("mutation"), dict)
            and event["mutation"].get("type") in {"duplicate", "replay"}
        ]
        if not mutated:
            errors.append(f"{role} variant has no duplicate/replay mutation event")
        elif not any(event["mutation"].get("target_event") for event in mutated):
            warnings.append(f"{role} mutation has no target_event")

    if case["oracle"]["normative_checks"]:
        if case["evidence_status"] not in {"verified", "partial", "unknown"}:
            warnings.append("normative check has weak evidence status")
    if not (
        case["oracle"]["setup_checks"]
        or case["oracle"]["normative_checks"]
        or case["oracle"]["observations"]
    ):
        errors.append("oracle has no checks or observations")

    requirements = case["setup"]["requirements"]
    if requirements["needs_multi_connection"] and requirements["connections"] < 2:
        errors.append("multi-connection setup has fewer than two connections")

    return {
        "errors": errors,
        "warnings": warnings,
        "is_harness_ready": not errors,
    }


def normalize_variant_case(
    *,
    variant: dict[str, Any],
    family: dict[str, Any],
    metadata: dict[str, Any],
    line_map: dict[int, str],
    index: int,
    protocol: str | None = None,
) -> dict[str, Any]:
    evidence = [verify_evidence(ev, line_map) for ev in as_list(variant.get("E") or family.get("E"))]
    events, warnings = normalize_trace(variant)
    setup = infer_setup(variant=variant, events=events, metadata=metadata, protocol=protocol)
    oracle = split_oracle(variant=variant, events=events, evidence=evidence)
    case = {
        "ir_id": f"{variant.get('variant_id', 'variant')}_ir",
        "family_id": variant.get("family_id") or family.get("family_id"),
        "base_litmus_id": variant.get("base_litmus_id") or family.get("base_litmus_id"),
        "variant_id": variant.get("variant_id"),
        "name": variant.get("name", f"IR case {index}"),
        "variant": {
            "role": variant.get("role"),
            "expected_strength": variant.get("expected_strength"),
            "target_boundary": variant.get("target_boundary"),
            "delta_from_base": variant.get("delta_from_base"),
            "distinguishes": variant.get("distinguishes"),
            "value_score": variant.get("value_score"),
            "feasibility": variant.get("feasibility"),
            "confidence": variant.get("confidence"),
            "field_bound_checks": as_list(variant.get("field_bound_checks") or family.get("field_bound_checks")),
        },
        "setup": setup,
        "events": events,
        "oracle": oracle,
        "field_bound_checks": as_list(variant.get("field_bound_checks") or family.get("field_bound_checks")),
        "evidence": evidence,
        "evidence_status": evidence_status(evidence),
        "_warnings": warnings,
    }
    case["validation"] = validate_case(case)
    return case


def flatten_variants(families: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for family in families:
        for variant in family.get("variants", []) or []:
            flat = {
                "family_id": family.get("family_id"),
                "base_litmus_id": family.get("base_litmus_id"),
                "source_items": family.get("source_items", []),
                "E": family.get("E", []),
                "field_bound_checks": family.get("field_bound_checks", []),
                **variant,
            }
            pairs.append((family, flat))
    return pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize litmus family variants into harness-ready IR.")
    parser.add_argument("--families", required=True, type=Path, help="Input litmus families JSON.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output IR JSON.")
    parser.add_argument("--out-jsonl", type=Path, default=None, help="Optional cases JSONL.")
    parser.add_argument("--chunks", type=Path, default=None, help="Optional chunks JSONL for evidence verification.")
    parser.add_argument("--protocol", default=None, help="Optional protocol label to place in the IR setup.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs = [args.out_json]
    if args.out_jsonl:
        outputs.append(args.out_jsonl)
    if args.overwrite:
        for path in outputs:
            if path.exists():
                path.unlink()
    else:
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            print(f"Output exists: {', '.join(existing)}. Use --overwrite.", file=sys.stderr)
            sys.exit(2)

    metadata, families = load_families(args.families)
    line_map = load_chunk_line_map(args.chunks)
    pairs = flatten_variants(families)
    if not pairs:
        print("No variants found in families JSON.", file=sys.stderr)
        sys.exit(1)

    cases: list[dict[str, Any]] = []
    for index, (family, variant) in enumerate(pairs, start=1):
        case = normalize_variant_case(
            variant=variant,
            family=family,
            metadata=metadata,
            line_map=line_map,
            index=index,
            protocol=args.protocol,
        )
        cases.append(case)

    summary = {
        "input_families": str(args.families),
        "chunks_file": str(args.chunks) if args.chunks else None,
        "family_count": len(families),
        "variant_count": len(pairs),
        "case_count": len(cases),
        "harness_ready_count": sum(1 for case in cases if case["validation"]["is_harness_ready"]),
        "normative_check_count": sum(len(case["oracle"]["normative_checks"]) for case in cases),
        "observation_count": sum(len(case["oracle"]["observations"]) for case in cases),
        "setup_check_count": sum(len(case["oracle"]["setup_checks"]) for case in cases),
        "evidence_status_counts": {},
    }
    for case in cases:
        status = case["evidence_status"]
        summary["evidence_status_counts"][status] = summary["evidence_status_counts"].get(status, 0) + 1

    output = {
        "metadata": {
            "source_metadata": metadata,
            **summary,
        },
        "cases": cases,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_jsonl:
        write_jsonl(args.out_jsonl, cases)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
