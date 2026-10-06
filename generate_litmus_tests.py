#!/usr/bin/env python3
"""Generate protocol litmus tests from extracted standard semantics.

This is step 2 of the SpecLitmus prototype:
  extracted semantics -> compact, standard-grounded litmus tests.

The input is the JSON produced by extract_litmus_semantics.py.  The
output is JSON containing generated tests, evidence links, and an
adapter-neutral execution plan that later harness code can implement.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.5"

SYSTEM_PROMPT = """You are SpecLitmus Generator.

Return valid JSON only. Do not include markdown fences or explanatory prose.

Your job:
- Convert extracted protocol-standard semantics into compact protocol litmus tests.
- A litmus test is a short executable experiment that distinguishes subtle protocol behavior.
- Preserve grounding: every generated test must reference the source semantic item ids and include evidence E.
- Do not invent requirements that are not entailed by the provided semantics and evidence.

Good litmus tests:
- are minimal: remove unnecessary steps
- target a boundary condition: replay, duplication, reordering, timeout, negotiation mismatch, cross-phase message, invalid field edge, security-goal boundary, resource edge
- have a clear oracle: expected observation and verdict rule
- include a control variant when useful
- are adapter-neutral: they describe protocol events, not implementation-specific APIs
- for related items in one group, prefer one sharper comparative/combined litmus test over many shallow one-item tests

Important distinction:
- Normative obligations (MUST, MUST_NOT, REQUIRED, SHOULD) can produce conformance oracles.
- SECURITY_RATIONALE items produce security-property or risk-boundary tests, not strict conformance failures unless paired with explicit normative behavior.
- A missing security guarantee is not a conformance oracle. Do not mark "server rejects replay" as fail merely because the standard says replay is not guaranteed.
- Do not confuse declining resumption with rejecting the whole connection. If the text says fallback to a full handshake, the expected observation is fallback/renegotiation, not connection rejection.
"""

USER_TEMPLATE = """Input semantic group:
{items_json}

Generate JSON with this exact shape:
{{
  "tests": [
    {{
      "name": "short descriptive name",
      "intent": "what subtle protocol behavior this test distinguishes",
      "category": "state|temporal|field|negotiation|security|resource|cross_layer|error_handling|combined",
      "source_items": ["item_id"],
      "preconditions": [
        "required configuration, negotiated parameter, credential, or prior state"
      ],
      "actors": [
        "client",
        "server",
        "attacker or scheduler if needed"
      ],
      "minimal_trace": [
        {{
          "step": 1,
          "actor": "client|server|attacker|scheduler|test_harness",
          "action": "protocol event to perform",
          "message": "message or protocol object",
          "mutation": "boundary manipulation, or null",
          "expected_observation": "what should be observed after this step"
        }}
      ],
      "control_trace": [
        "optional baseline trace, or empty list"
      ],
      "field_bound_checks": [
        {{
          "field": "field, vector, enum, registry value, or length being constrained",
          "bound_kind": "integer_width|vector_length|numeric_range|upper_bound|lower_bound|enumerated_values|other",
          "value_type": "uint8|uint16|uint24|uint32|uint64|opaque|enum|bytes|other",
          "lower_bound": "minimum allowed value or length, or null",
          "lower_inclusive": true,
          "upper_bound": "maximum allowed value or length, or null",
          "upper_inclusive": true,
          "unit": "value|bytes|octets|length|entries|other",
          "applies_to": "condition under which this bound applies",
          "required_check": "what an implementation must accept, reject, alert, ignore, or limit"
        }}
      ],
      "oracle": {{
        "type": "accept|reject|alert|ignore|state_update|no_state_update|resource_bound|security_property|other",
        "expected": "precise expected result",
        "violation": "what observation would distinguish a bug, ambiguity, or risk",
        "verdicts": {{
          "pass": "condition for pass",
          "fail": "condition for fail",
          "inconclusive": "condition for inconclusive"
        }}
      }},
      "minimization_rationale": "why this trace is short and sufficient",
      "harness_plan": {{
        "needs_network_scheduler": true,
        "needs_fault_injection": false,
        "needs_multi_connection": false,
        "observable_signals": [
          "wire alert, accept/reject, state transition, callback, resource counter"
        ],
        "adapter_hooks": [
          "send_message",
          "capture_response"
        ]
      }},
      "E": [
        {{
          "item_id": "source item id",
          "line_start": 0,
          "line_end": 0,
          "quote": "evidence quote"
        }}
      ],
      "expected_strength": "conformance|security_rationale|differential|ambiguous",
      "feasibility": "high|medium|low",
      "confidence": 0.0
    }}
  ]
}}

Rules:
- Prefer one strong test per distinct boundary. If two items naturally combine into a sharper boundary, generate one combined test.
- If a group has multiple related items, first try to generate a comparative litmus test that distinguishes the boundaries against each other.
- Keep traces short, but include all protocol history needed for the oracle to make sense.
- Evidence E must be copied from the source items. Do not create new evidence.
- Copy relevant field_bound_checks from source items into each generated field-boundary test.
- source_items must only contain ids from the input group.
- For SECURITY_RATIONALE-only tests, expected_strength must be "security_rationale" or "differential", not "conformance".
- For SECURITY_RATIONALE-only tests, verdicts must be observational: pass means the boundary was exercised and evidence was collected, fail is reserved for explicit normative violations or harness errors.
- If a group cannot produce a meaningful litmus test, return {{"tests": []}}.
"""

REPAIR_SYSTEM_PROMPT = """You repair malformed JSON.

Return valid JSON only. Do not include markdown fences. Preserve all fields and values that can be recovered.
If the input is too broken to recover, return {"tests": []}.
"""


def load_semantics(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        items: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if isinstance(row, dict) and isinstance(row.get("items"), list):
                    items.extend(row["items"])
                elif isinstance(row, dict) and "item_id" in row:
                    items.append(row)
                else:
                    raise ValueError(f"Unsupported JSONL row at {path}:{line_no}")
        return {"source": str(path)}, items

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data.get("metadata", {"source": str(path)}), data["items"]
    if isinstance(data, list):
        return {"source": str(path)}, data
    raise ValueError(f"Unsupported semantics JSON shape: {path}")


def normalize_text(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_nulls(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_nulls(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_nulls(item) for key, item in value.items()}
    if isinstance(value, str) and value.strip().lower() in {"null", "none", "n/a"}:
        return None
    return value


def subject_key(item: dict[str, Any]) -> str:
    subject = normalize_text(item.get("subject"))
    if not subject:
        subject = normalize_text(item.get("category"))
    # Keep common protocol nouns together without over-engineering clustering.
    if "0 rtt" in subject or "early data" in subject:
        return "0-rtt data"
    if "psk" in subject or "pre shared key" in subject:
        return "psk"
    if "key share" in subject:
        return "key share"
    return subject[:80] or "misc"


def group_items(items: list[dict[str, Any]], mode: str, max_group_size: int) -> list[list[dict[str, Any]]]:
    if mode == "item":
        return [[item] for item in items]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if mode == "chunk":
            key = str(item.get("chunk_id", "unknown"))
        elif mode == "section":
            sections = item.get("sections") or ["unknown"]
            key = str(sections[0])
        else:
            key = subject_key(item)
        buckets[key].append(item)

    groups: list[list[dict[str, Any]]] = []
    for bucket_items in buckets.values():
        for idx in range(0, len(bucket_items), max_group_size):
            groups.append(bucket_items[idx : idx + max_group_size])
    return groups


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def call_chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None,
    max_tokens: int,
    timeout: int,
) -> str:
    url = normalize_base_url(base_url) + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    try:
        return parsed["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected API response shape: {raw[:1000]}") from exc


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = strip_code_fences(text)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM response JSON is not an object")
    return value


def repair_json_response(
    *,
    malformed: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
) -> dict[str, Any]:
    content = call_chat_completion(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=[
            {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Repair the following malformed JSON into {\"tests\": [...]}.\n\n"
                + malformed[:18000],
            },
        ],
        temperature=None,
        max_tokens=2600,
        timeout=timeout,
    )
    return parse_json_object(content)


def evidence_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in items:
        for ev in item.get("E", []) or []:
            if not isinstance(ev, dict):
                continue
            evidence.append(
                {
                    "item_id": item.get("item_id"),
                    "line_start": ev.get("line_start"),
                    "line_end": ev.get("line_end"),
                    "quote": ev.get("quote", ""),
                    "verified": ev.get("verified", None),
                }
            )
    return evidence


def field_bound_checks_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        for check in item.get("field_bound_checks", []) or []:
            if not isinstance(check, dict):
                continue
            key = json.dumps(check, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            checks.append(check)
    return checks


def fallback_test(item: dict[str, Any], index: int) -> dict[str, Any]:
    """Deterministic fallback for dry runs or failed LLM calls."""
    category = str(item.get("category", "other"))
    modality = str(item.get("modality", "OTHER"))
    security_only = modality == "SECURITY_RATIONALE"
    oracle_type = str(item.get("oracle") or ("security_property" if security_only else "other"))
    source_id = item.get("item_id", f"item_{index:04d}")
    evidence = evidence_from_items([item])
    return {
        "litmus_id": "",
        "name": f"{item.get('subject', 'Protocol')} boundary litmus",
        "intent": item.get("litmus_boundary", [item.get("expected_behavior", "")])[0]
        if isinstance(item.get("litmus_boundary"), list) and item.get("litmus_boundary")
        else item.get("expected_behavior", ""),
        "category": category,
        "source_items": [source_id],
        "preconditions": [str(item.get("condition", ""))] if item.get("condition") else [],
        "actors": ["client", "server", "test_harness"],
        "minimal_trace": [
            {
                "step": 1,
                "actor": "test_harness",
                "action": str(item.get("stimulus", "exercise boundary condition")),
                "message": str(item.get("subject", "")),
                "mutation": None,
                "expected_observation": str(item.get("expected_behavior", "")),
            }
        ],
        "control_trace": [],
        "field_bound_checks": field_bound_checks_from_items([item]),
        "oracle": {
            "type": oracle_type,
            "expected": str(item.get("expected_behavior", "")),
            "violation": "observed behavior contradicts the expected behavior",
            "verdicts": {
                "pass": "observed behavior matches expected",
                "fail": "observed behavior contradicts expected",
                "inconclusive": "the test cannot reach or observe the target behavior",
            },
        },
        "minimization_rationale": "Single semantic item fallback trace.",
        "harness_plan": {
            "needs_network_scheduler": category in {"temporal", "state", "security"},
            "needs_fault_injection": False,
            "needs_multi_connection": "replay" in normalize_text(item.get("expected_behavior"))
            or "connection" in normalize_text(item.get("condition")),
            "observable_signals": ["wire response", "accept/reject", "state transition"],
            "adapter_hooks": ["send_message", "capture_response"],
        },
        "E": evidence,
        "expected_strength": "security_rationale" if security_only else "conformance",
        "feasibility": "medium",
        "confidence": item.get("confidence", 0.5),
    }


def normalize_test(
    raw: dict[str, Any],
    *,
    group: list[dict[str, Any]],
    test_index: int,
    document_id: str,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    valid_item_ids = {str(item.get("item_id")) for item in group}
    source_items = raw.get("source_items")
    if not isinstance(source_items, list):
        source_items = []
    source_items = [str(item_id) for item_id in source_items if str(item_id) in valid_item_ids]
    if not source_items:
        source_items = [str(group[0].get("item_id"))]

    selected_items = [item for item in group if str(item.get("item_id")) in set(source_items)]
    if not selected_items:
        selected_items = group

    raw_evidence = raw.get("E")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raw_evidence = evidence_from_items(selected_items)
    field_bound_checks = raw.get("field_bound_checks")
    if not isinstance(field_bound_checks, list):
        field_bound_checks = field_bound_checks_from_items(selected_items)

    expected_strength = str(raw.get("expected_strength", "")).strip().lower()
    if expected_strength not in {"conformance", "security_rationale", "differential", "ambiguous"}:
        if all(str(item.get("modality")) == "SECURITY_RATIONALE" for item in selected_items):
            expected_strength = "security_rationale"
        else:
            expected_strength = "conformance"
    has_security_rationale = any(str(item.get("modality")) == "SECURITY_RATIONALE" for item in selected_items)
    has_normative = any(str(item.get("modality")) != "SECURITY_RATIONALE" for item in selected_items)
    security_only = has_security_rationale and not has_normative
    mixed_security_normative = has_security_rationale and has_normative
    if security_only and expected_strength == "conformance":
        expected_strength = "security_rationale"
    if mixed_security_normative and expected_strength == "security_rationale":
        expected_strength = "differential"

    category = str(raw.get("category", "combined")).strip().lower()
    allowed_categories = {
        "state",
        "temporal",
        "field",
        "negotiation",
        "security",
        "resource",
        "cross_layer",
        "error_handling",
        "combined",
    }
    if category not in allowed_categories:
        category = "combined"

    oracle = raw.get("oracle", {})
    if not isinstance(oracle, dict):
        oracle = {}
    oracle_type = str(oracle.get("type") or "other").strip().lower()
    allowed_oracle_types = {
        "accept",
        "reject",
        "alert",
        "ignore",
        "state_update",
        "no_state_update",
        "resource_bound",
        "security_property",
        "other",
    }
    if oracle_type not in allowed_oracle_types:
        if "security" in oracle_type:
            oracle_type = "security_property"
        elif "reject" in oracle_type:
            oracle_type = "reject"
        elif "accept" in oracle_type:
            oracle_type = "accept"
        else:
            oracle_type = "other"
    oracle["type"] = oracle_type

    if security_only:
        oracle_type = str(oracle.get("type") or "security_property")
        if oracle_type in {"accept", "reject", "alert", "ignore", "state_update", "no_state_update"}:
            oracle_type = "security_property"
        oracle = {
            **oracle,
            "type": oracle_type,
            "violation": oracle.get(
                "violation",
                "observed behavior indicates a risk boundary; this is not a conformance failure without an explicit normative rule",
            ),
            "verdicts": {
                "pass": "the litmus boundary is exercised and observations are recorded",
                "fail": "the harness cannot enforce required setup or an explicit paired normative rule is violated",
                "inconclusive": "the boundary cannot be reached or the relevant observation is unavailable",
            },
        }
    elif mixed_security_normative:
        if oracle.get("type") in {"accept", "alert", "state_update", "no_state_update"}:
            oracle["type"] = "security_property"
        oracle = {
            **oracle,
            "violation": "an explicit normative requirement is violated; security-rationale observations are reported as risk or differential behavior, not as conformance failures",
            "verdicts": {
                "pass": "all explicit normative checks in the litmus test hold; security-rationale boundary observations are recorded",
                "fail": "an explicit normative requirement is violated, such as processing data that the standard says cannot be duplicated",
                "inconclusive": "the test cannot isolate the normative check or observe the security-rationale boundary",
            },
        }

    oracle_blob = normalize_text(json.dumps({"oracle": oracle, "trace": raw.get("minimal_trace", [])}, ensure_ascii=False))
    if "resumption" in oracle_blob and (
        "fall back" in oracle_blob
        or "fallback" in oracle_blob
        or "full handshake" in oracle_blob
        or "decline resumption" in oracle_blob
        or "declines resumption" in oracle_blob
    ):
        oracle = {
            **oracle,
            "type": "state_update",
            "expected": "resumption is declined or not selected, and the endpoint proceeds with the appropriate full-handshake path when applicable",
            "violation": "the endpoint completes PSK resumption despite the missing key_share boundary",
            "verdicts": {
                "pass": "resumption is not selected and the observed path is a full-handshake or retry/fallback path",
                "fail": "PSK resumption is completed despite the missing key_share boundary",
                "inconclusive": "the trace cannot distinguish full-handshake fallback from resumption",
            },
        }

    harness_plan = raw.get("harness_plan", {})
    if not isinstance(harness_plan, dict):
        harness_plan = {}
    minimal_trace = normalize_nulls(raw.get("minimal_trace", []))
    control_trace = normalize_nulls(raw.get("control_trace", []))
    trace_blob = normalize_text(json.dumps(minimal_trace, ensure_ascii=False))
    if "reconnect" in trace_blob or "connection" in trace_blob or "replay" in trace_blob:
        harness_plan["needs_multi_connection"] = bool(harness_plan.get("needs_multi_connection", False)) or (
            "reconnect" in trace_blob or "between connection" in trace_blob
        )
    if "reorder" in trace_blob or "duplicate" in trace_blob or "replay" in trace_blob:
        harness_plan["needs_network_scheduler"] = bool(harness_plan.get("needs_network_scheduler", False)) or True
    if category == "negotiation" and not any(word in trace_blob for word in ("reorder", "duplicate", "replay", "timeout")):
        harness_plan["needs_network_scheduler"] = False

    test = {
        "litmus_id": f"{document_id}_litmus_{test_index:04d}",
        "name": raw.get("name", "Protocol litmus test"),
        "intent": raw.get("intent", ""),
        "category": category,
        "source_items": source_items,
        "preconditions": raw.get("preconditions", []),
        "actors": raw.get("actors", ["client", "server", "test_harness"]),
        "minimal_trace": minimal_trace,
        "control_trace": control_trace,
        "field_bound_checks": field_bound_checks,
        "oracle": oracle,
        "minimization_rationale": raw.get("minimization_rationale", ""),
        "harness_plan": harness_plan,
        "E": raw_evidence,
        "expected_strength": expected_strength,
        "feasibility": raw.get("feasibility", "medium"),
        "confidence": raw.get("confidence", None),
    }
    return test


def generate_group(
    *,
    group: list[dict[str, Any]],
    base_url: str,
    api_key: str,
    model: str,
    temperature: float | None,
    max_tokens: int,
    timeout: int,
    retries: int,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    if dry_run:
        return [fallback_test(item, idx) for idx, item in enumerate(group, start=1)], None

    items_json = json.dumps(group, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(items_json=items_json)},
    ]

    last_error: Exception | None = None
    last_content = ""
    for attempt in range(retries + 1):
        try:
            content = call_chat_completion(
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            last_content = content
            try:
                parsed = parse_json_object(content)
            except (json.JSONDecodeError, ValueError):
                parsed = repair_json_response(
                    malformed=content,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    timeout=timeout,
                )
            raw_tests = parsed.get("tests", [])
            if not isinstance(raw_tests, list):
                raw_tests = []
            return [test for test in raw_tests if isinstance(test, dict)], None
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            http.client.RemoteDisconnected,
            TimeoutError,
            json.JSONDecodeError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))

    preview = f"; raw response preview: {last_content[:500]}" if last_content else ""
    return [], (str(last_error) if last_error else "unknown error") + preview


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate protocol litmus tests from extracted semantics.")
    parser.add_argument("--semantics", required=True, type=Path, help="Input semantics JSON or JSONL.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output litmus tests JSON.")
    parser.add_argument("--out-jsonl", type=Path, default=None, help="Optional tests JSONL.")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=None, help="Optional sampling temperature; only use with models that support it.")
    parser.add_argument("--max-tokens", type=int, default=3500)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--group-mode",
        choices=["item", "subject", "chunk", "section"],
        default="subject",
        help="How to group semantic items before generation.",
    )
    parser.add_argument("--max-group-size", type=int, default=5)
    parser.add_argument("--limit-groups", type=int, default=None, help="Process only the first N groups.")
    parser.add_argument("--start-group", type=int, default=1, help="1-based group index to start from.")
    parser.add_argument("--append", action="store_true", help="Append to an existing JSONL and rewrite out-json.")
    parser.add_argument("--dry-run", action="store_true", help="Use deterministic fallback generation without LLM.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dry_run:
        api_key = os.getenv(args.api_key_env)
        if not api_key:
            print(f"Missing API key. Set {args.api_key_env}, or use --dry-run.", file=sys.stderr)
            sys.exit(2)
    else:
        api_key = ""

    outputs = [args.out_json]
    if args.out_jsonl:
        outputs.append(args.out_jsonl)
    if args.append and args.overwrite:
        print("--append and --overwrite cannot be used together.", file=sys.stderr)
        sys.exit(2)
    if args.append and not args.out_jsonl:
        print("--append requires --out-jsonl.", file=sys.stderr)
        sys.exit(2)

    if args.overwrite:
        for path in outputs:
            if path.exists():
                path.unlink()
    elif not args.append:
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            print(f"Output exists: {', '.join(existing)}. Use --overwrite.", file=sys.stderr)
            sys.exit(2)

    metadata, items = load_semantics(args.semantics)
    if not items:
        print("No semantic items found.", file=sys.stderr)
        sys.exit(1)

    groups = group_items(items, args.group_mode, args.max_group_size)
    if args.limit_groups is not None:
        groups = groups[: args.limit_groups]
    if args.start_group < 1 or args.start_group > len(groups) + 1:
        print(f"--start-group must be between 1 and {len(groups) + 1}.", file=sys.stderr)
        sys.exit(2)

    all_tests: list[dict[str, Any]] = []
    group_results: list[dict[str, Any]] = []
    document_id = str(items[0].get("document_id") or "document")
    next_test_index = 1

    if args.out_jsonl:
        args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        if args.append and args.out_jsonl.exists():
            with args.out_jsonl.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        test = json.loads(stripped)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid existing JSONL row at {args.out_jsonl}:{line_no}") from exc
                    if isinstance(test, dict):
                        all_tests.append(test)
            next_test_index = len(all_tests) + 1

    groups_to_process = groups[args.start_group - 1 :]
    for group_index, group in enumerate(groups_to_process, start=args.start_group):
        item_ids = [item.get("item_id") for item in group]
        print(f"[{group_index}/{len(groups)}] generating litmus tests for {item_ids}")
        raw_tests, error = generate_group(
            group=group,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            retries=args.retries,
            dry_run=args.dry_run,
        )
        tests: list[dict[str, Any]] = []
        for raw_test in raw_tests:
            test = normalize_test(
                raw_test,
                group=group,
                test_index=next_test_index,
                document_id=document_id,
            )
            if test:
                tests.append(test)
                all_tests.append(test)
                next_test_index += 1
                if args.out_jsonl:
                    with args.out_jsonl.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(test, ensure_ascii=False) + "\n")

        group_results.append(
            {
                "group_index": group_index,
                "source_items": item_ids,
                "test_ids": [test["litmus_id"] for test in tests],
                "error": error,
            }
        )
        if error:
            print(f"  error: {error}")
        print(f"  tests={len(tests)}")

    output = {
        "metadata": {
            "semantics_file": str(args.semantics),
            "source_metadata": metadata,
            "model": "dry-run" if args.dry_run else args.model,
            "base_url": args.base_url,
            "group_mode": args.group_mode,
            "groups": len(groups),
            "source_item_count": len(items),
            "litmus_test_count": len(all_tests),
        },
        "group_results": group_results,
        "tests": all_tests,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
