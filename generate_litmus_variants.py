#!/usr/bin/env python3
"""Generate valuable variants for protocol litmus tests.

Input: JSON from generate_litmus_tests.py.
Output: litmus families. Each family contains a base litmus test and a
small set of distinct, evidence-grounded variants.

The goal is not to maximize the number of variants.  The goal is to keep
variants that distinguish meaningfully different implementation behavior.
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
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.bltcy.ai/v1"
DEFAULT_MODEL = "gpt-5.5"

ALLOWED_ROLES = {
    "positive_control",
    "negative_boundary",
    "edge_value",
    "reordering",
    "duplication",
    "replay",
    "timeout",
    "cross_phase",
    "negotiation_mismatch",
    "fragmentation",
    "resource_stress",
    "differential_observation",
    "security_observation",
    "scheduler_perturbation",
}

SYSTEM_PROMPT = """You are SpecLitmus Variant Generator.

Return valid JSON only. Do not include markdown fences or explanatory prose.

Your job:
- Given one standard-grounded protocol litmus test, generate at most the requested number of variants that are most likely to expose implementation bugs or RFC conformance deviations.
- A variant is valuable only if it distinguishes a likely implementation failure mode, boundary-condition bug, or observability risk.
- Prefer fewer strong variants over many shallow variants.
- Preserve evidence grounding: do not invent new evidence; variants inherit the base test evidence.

Good variant roles:
- positive_control: legal baseline that proves the setup can work
- negative_boundary: minimal invalid/boundary case with a normative oracle
- edge_value: numeric or length values at lower/upper bounds and just outside those bounds
- reordering: same events in a different arrival order
- duplication: same message/data repeated within the same relevant scope
- replay: same data/event repeated across a new connection/session when replay is the boundary
- timeout: delayed event, expired ticket/token, late packet, or post-timeout message
- cross_phase: message from one phase delivered in another phase
- negotiation_mismatch: later behavior contradicts negotiated parameters
- fragmentation: same semantic message split/reassembled differently
- resource_stress: small bounded stress that targets resource/cleanup semantics
- differential_observation/security_observation: records behavior for security-rationale or ambiguous semantics without declaring conformance failure

Hard rules:
- Each variant must have a concrete delta_from_base.
- Each variant must state what it distinguishes.
- Do not create cosmetic variants that only rename messages or repeat the base.
- For SECURITY_RATIONALE-only tests, do not create strict fail verdicts unless the base contains an explicit normative check.
- For mixed differential tests, fail only on explicit normative violations; security observations are recorded as risk/differential behavior.
"""

USER_TEMPLATE = """Base litmus test:
{test_json}

Generate JSON with this exact shape:
{{
  "family": {{
    "family_name": "short family name",
    "core_semantic": "the core rule or semantic boundary under test",
    "variant_strategy": "how the variants cover distinct behavior around this rule",
    "variants": [
      {{
        "name": "short descriptive variant name",
        "role": "positive_control|negative_boundary|edge_value|reordering|duplication|replay|timeout|cross_phase|negotiation_mismatch|fragmentation|resource_stress|differential_observation|security_observation|scheduler_perturbation",
        "target_boundary": "specific protocol boundary this variant exercises",
        "delta_from_base": "precise difference from the base litmus test",
        "distinguishes": "what implementation behavior this variant can distinguish",
        "preconditions": [
          "additional setup beyond the base test, or inherited setup"
        ],
        "trace": [
          {{
            "step": 1,
            "actor": "client|server|attacker|scheduler|test_harness",
            "action": "protocol event",
            "message": "message/object",
            "mutation": "variant-specific change, or null",
            "expected_observation": "what should be observed"
          }}
        ],
        "oracle": {{
          "type": "accept|reject|alert|ignore|state_update|no_state_update|resource_bound|security_property|other",
          "expected": "expected result for this variant",
          "violation": "what observation distinguishes a bug, ambiguity, or risk",
          "verdicts": {{
            "pass": "condition for pass",
            "fail": "condition for fail",
            "inconclusive": "condition for inconclusive"
          }}
        }},
        "harness_plan": {{
          "needs_network_scheduler": true,
          "needs_fault_injection": false,
          "needs_multi_connection": false,
          "needs_replay_buffer": false,
          "needs_timer_control": false,
          "observable_signals": [
            "wire alert, state transition, callback, resource counter"
          ],
          "adapter_hooks": [
            "send_message",
            "capture_response"
          ]
        }},
        "field_bound_checks": [
          "copy relevant field bound checks from the base test, or an empty list"
        ],
        "expected_strength": "conformance|security_rationale|differential|ambiguous",
        "value_score": 1,
        "value_rationale": "why this variant is worth running",
        "feasibility": "high|medium|low",
        "confidence": 0.0
      }}
    ]
  }}
}}

Rules:
- Generate 0 to {target_variants} variants. Do not force variants to reach the limit.
- Include a positive_control only when it is needed to interpret a high-risk boundary variant.
- Do not include a variant if it would have the same trace and oracle as another variant.
- The base test evidence is inherited; do not output new E in variants.
- The base test field_bound_checks are inherited; copy only the checks relevant to a boundary variant.
- For numeric, length, enum, or vector bound checks, prefer variants at the lower bound, upper bound, just below lower bound, and just above upper bound when those values are meaningful.
- value_score must reflect issue-finding likelihood: 5 = highly likely to expose an implementation bug or RFC deviation, 3 = plausible, 1-2 = weak and should usually be omitted.
- SECURITY_RATIONALE-only variants must use expected_strength "security_rationale" or "differential" and observational verdicts.
"""

REPAIR_SYSTEM_PROMPT = """You repair malformed JSON.

Return valid JSON only. Do not include markdown fences. Preserve all fields and values that can be recovered.
If the input is too broken to recover, return {"family": {"variants": []}}.
"""


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


def load_tests(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        tests: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if isinstance(row, dict) and "litmus_id" in row:
                    tests.append(row)
                else:
                    raise ValueError(f"Unsupported JSONL row at {path}:{line_no}")
        return {"source": str(path)}, tests

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and isinstance(data.get("tests"), list):
        return data.get("metadata", {"source": str(path)}), data["tests"]
    if isinstance(data, list):
        return {"source": str(path)}, data
    raise ValueError(f"Unsupported litmus JSON shape: {path}")


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def call_chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    url = normalize_base_url(base_url) + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
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
                "content": "Repair the following malformed JSON into {\"family\": {\"variants\": [...]}}.\n\n"
                + malformed[:18000],
            },
        ],
        temperature=0.0,
        max_tokens=3200,
        timeout=timeout,
    )
    return parse_json_object(content)


def is_security_observational(test: dict[str, Any]) -> bool:
    return str(test.get("expected_strength", "")).lower() in {"security_rationale", "ambiguous"}


def is_mixed_differential(test: dict[str, Any]) -> bool:
    return str(test.get("expected_strength", "")).lower() == "differential"


def role_from_text(raw: dict[str, Any], current_role: str) -> str:
    if current_role == "positive_control":
        return current_role
    blob = normalize_text(raw)
    if "duplicate" in blob or "same connection" in blob or "second instance" in blob:
        return "duplication"
    if "replay" in blob or "reconnect" in blob or "new connection" in blob or "cross connection" in blob:
        return "replay"
    if "timeout" in blob or "expire" in blob or "expired" in blob:
        return "timeout"
    if "reorder" in blob or "out of order" in blob:
        return "reordering"
    if "fragment" in blob:
        return "fragmentation"
    if "negotiat" in blob or "mismatch" in blob:
        return "negotiation_mismatch"
    return current_role


def source_item_modalities(base: dict[str, Any]) -> dict[str, str]:
    modalities: dict[str, str] = {}
    for ev in base.get("E", []) or []:
        if isinstance(ev, dict) and ev.get("item_id"):
            modalities[str(ev["item_id"])] = "UNKNOWN"
    return modalities


def variant_mentions_normative_duplicate(base: dict[str, Any], variant: dict[str, Any]) -> bool:
    blob = normalize_text(variant)
    evidence_blob = normalize_text(base.get("E", []))
    return (
        ("duplicate" in blob or "duplicated" in blob or "second instance" in blob)
        and ("cannot be duplicated" in evidence_blob or "will not process the same data twice" in evidence_blob)
    )


def base_supports_temporal_perturbation(base: dict[str, Any]) -> bool:
    blob = normalize_text({"intent": base.get("intent"), "name": base.get("name"), "E": base.get("E", [])})
    temporal_terms = (
        "reorder",
        "out of order",
        "timeout",
        "late",
        "delay",
        "duplicate",
        "duplicated",
        "replay",
        "cross phase",
        "fragment",
        "migration",
        "key update",
    )
    return any(term in blob for term in temporal_terms)


def infer_harness_plan(base: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    base_plan = dict(base.get("harness_plan") or {})
    plan = dict(variant.get("harness_plan") or {})
    merged = {**base_plan, **plan}
    feature_blob = normalize_text(
        {
            "name": variant.get("name"),
            "role": variant.get("role"),
            "target_boundary": variant.get("target_boundary"),
            "delta_from_base": variant.get("delta_from_base"),
            "distinguishes": variant.get("distinguishes"),
            "trace": variant.get("trace"),
        }
    )

    if any(term in feature_blob for term in ("replay", "reconnect", "new connection", "across connection", "second connection")):
        merged["needs_multi_connection"] = True
        merged["needs_replay_buffer"] = True
    if any(term in feature_blob for term in ("reorder", "duplicate", "delay", "late", "scheduler", "interleave")):
        merged["needs_network_scheduler"] = True
    if any(term in feature_blob for term in ("timeout", "expire", "expired", "timer")):
        merged["needs_timer_control"] = True
    if any(term in feature_blob for term in ("fault", "drop", "corrupt", "truncate")):
        merged["needs_fault_injection"] = True

    if not any(term in feature_blob for term in ("fault", "drop", "corrupt", "truncate")):
        merged["needs_fault_injection"] = False
    if not any(term in feature_blob for term in ("timeout", "expire", "expired", "timer")):
        merged["needs_timer_control"] = False
    if not any(term in feature_blob for term in ("replay", "reconnect", "new connection", "across connection", "second connection")):
        merged["needs_replay_buffer"] = False
        if not any(term in feature_blob for term in ("connection", "reconnect")):
            merged["needs_multi_connection"] = False

    merged.setdefault("needs_network_scheduler", False)
    merged.setdefault("needs_fault_injection", False)
    merged.setdefault("needs_multi_connection", False)
    merged.setdefault("needs_replay_buffer", False)
    merged.setdefault("needs_timer_control", False)
    merged.setdefault("observable_signals", base_plan.get("observable_signals", ["wire response", "state transition"]))
    merged.setdefault("adapter_hooks", base_plan.get("adapter_hooks", ["send_message", "capture_response"]))
    return merged


def normalize_oracle(base: dict[str, Any], variant: dict[str, Any], expected_strength: str) -> dict[str, Any]:
    oracle = variant.get("oracle")
    if not isinstance(oracle, dict):
        oracle = dict(base.get("oracle") or {})

    oracle_type = str(oracle.get("type") or "other").strip().lower()
    allowed = {
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
    if oracle_type not in allowed:
        if "security" in oracle_type:
            oracle_type = "security_property"
        elif "reject" in oracle_type:
            oracle_type = "reject"
        elif "accept" in oracle_type:
            oracle_type = "accept"
        else:
            oracle_type = "other"
    oracle["type"] = oracle_type

    role = str(variant.get("role", "")).strip().lower()
    if role == "duplication" and variant_mentions_normative_duplicate(base, variant):
        oracle["type"] = "reject" if oracle.get("type") == "security_property" else oracle.get("type", "reject")
        oracle["violation"] = "duplicate data in the same connection is processed as a fresh semantic event"
        oracle["verdicts"] = {
            "pass": "the duplicate is not processed as a second semantic event",
            "fail": "the duplicate is processed as a fresh semantic event",
            "inconclusive": "the harness cannot observe whether the duplicate was processed",
        }
        return oracle

    if role == "positive_control" and expected_strength == "differential":
        oracle["type"] = "security_property"
        oracle["violation"] = "the positive-control setup cannot reach the target behavior, so boundary variants cannot be interpreted"
        oracle["verdicts"] = {
            "pass": "the baseline behavior is reachable and observations are recorded",
            "fail": "the harness or endpoint cannot establish the baseline behavior required for this family",
            "inconclusive": "the baseline behavior is partially observed but not enough to validate the setup",
        }
        return oracle

    if expected_strength == "security_rationale":
        oracle["type"] = "security_property"
        oracle["violation"] = (
            "observed behavior indicates a security boundary or risk; this is not a conformance failure without an explicit normative rule"
        )
        oracle["verdicts"] = {
            "pass": "the variant boundary is exercised and observations are recorded",
            "fail": "the harness cannot enforce required setup or an explicit paired normative rule is violated",
            "inconclusive": "the boundary cannot be reached or the relevant observation is unavailable",
        }
    elif expected_strength == "differential":
        if oracle["type"] in {"accept", "alert", "state_update", "no_state_update"}:
            oracle["type"] = "security_property"
        oracle["violation"] = (
            "an explicit normative requirement is violated; security-rationale observations are reported as differential behavior"
        )
        oracle["verdicts"] = {
            "pass": "explicit normative checks hold and differential/security observations are recorded",
            "fail": "an explicit normative requirement is violated",
            "inconclusive": "the variant cannot isolate the normative check or observe the differential boundary",
        }
    return oracle


def fallback_variants(base: dict[str, Any], target_variants: int) -> list[dict[str, Any]]:
    """Deterministic baseline when LLM generation is unavailable."""
    base_trace = base.get("minimal_trace", [])
    expected_strength = str(base.get("expected_strength", "conformance")).lower()
    variants = [
        {
            "name": "Positive control",
            "role": "positive_control",
            "target_boundary": "baseline valid behavior",
            "delta_from_base": "use the closest valid form of the base trace",
            "distinguishes": "setup or harness failure from protocol-boundary failure",
            "preconditions": base.get("preconditions", []),
            "trace": base_trace,
            "oracle": base.get("oracle", {}),
            "harness_plan": base.get("harness_plan", {}),
            "field_bound_checks": base.get("field_bound_checks", []),
            "expected_strength": expected_strength,
            "value_score": 3,
            "value_rationale": "A positive control is needed to show the test setup can reach the target state.",
            "feasibility": "high",
            "confidence": base.get("confidence", 0.5),
        }
    ]
    blob = normalize_text(json.dumps(base, ensure_ascii=False))
    if "duplicate" in blob or "0 rtt" in blob:
        variants.extend(
            [
                {
                    "name": "Exact duplicate in same scope",
                    "role": "duplication",
                    "target_boundary": "duplicate message/data in the same connection or state scope",
                    "delta_from_base": "repeat the target message/data without changing connection context",
                    "distinguishes": "implementations that process duplicate data from those that suppress it",
                    "preconditions": base.get("preconditions", []),
                    "trace": base_trace
                    + [
                        {
                            "step": len(base_trace) + 1,
                            "actor": "client",
                            "action": "repeat target data",
                            "message": "same target data",
                            "mutation": "exact duplicate",
                            "expected_observation": "duplicate is not processed as a fresh semantic event",
                        }
                    ],
                    "oracle": base.get("oracle", {}),
                    "harness_plan": base.get("harness_plan", {}),
                    "field_bound_checks": base.get("field_bound_checks", []),
                    "expected_strength": "differential" if expected_strength == "differential" else "conformance",
                    "value_score": 5,
                    "value_rationale": "Exact duplicates often expose missing idempotence or replay suppression checks.",
                    "feasibility": "high",
                    "confidence": base.get("confidence", 0.5),
                },
                {
                    "name": "Cross-connection replay observation",
                    "role": "replay",
                    "target_boundary": "same data replayed after a new connection/session is established",
                    "delta_from_base": "replay the captured data in a fresh connection",
                    "distinguishes": "single-connection duplicate handling from cross-connection replay exposure",
                    "preconditions": base.get("preconditions", []),
                    "trace": base_trace
                    + [
                        {
                            "step": len(base_trace) + 1,
                            "actor": "client",
                            "action": "open new connection and replay target data",
                            "message": "same target data",
                            "mutation": "cross-connection replay",
                            "expected_observation": "behavior is recorded as a security/differential observation",
                        }
                    ],
                    "oracle": {"type": "security_property"},
                    "harness_plan": {**(base.get("harness_plan") or {}), "needs_multi_connection": True, "needs_replay_buffer": True},
                    "field_bound_checks": base.get("field_bound_checks", []),
                    "expected_strength": "differential",
                    "value_score": 5,
                    "value_rationale": "Cross-connection replay separates replay exposure from same-connection duplicate processing.",
                    "feasibility": "medium",
                    "confidence": base.get("confidence", 0.5),
                },
            ]
        )
    if "key share" in blob or "resumption" in blob:
        variants.extend(
            [
                {
                    "name": "Valid PSK with key_share control",
                    "role": "positive_control",
                    "target_boundary": "valid PSK resumption with key_share present",
                    "delta_from_base": "include key_share instead of omitting it",
                    "distinguishes": "server rejection of the setup from correct fallback on missing key_share",
                    "preconditions": base.get("preconditions", []),
                    "trace": [
                        {
                            "step": 1,
                            "actor": "client",
                            "action": "send ClientHello",
                            "message": "ClientHello with pre_shared_key and key_share",
                            "mutation": "valid control",
                            "expected_observation": "server can select PSK resumption or continue a valid handshake path",
                        }
                    ],
                    "oracle": {"type": "state_update"},
                    "harness_plan": base.get("harness_plan", {}),
                    "field_bound_checks": base.get("field_bound_checks", []),
                    "expected_strength": "conformance",
                    "value_score": 4,
                    "value_rationale": "The control confirms the PSK and extension setup is otherwise valid.",
                    "feasibility": "high",
                    "confidence": base.get("confidence", 0.5),
                }
            ]
        )
    return variants[:target_variants]


def generate_raw_variants(
    *,
    base_test: dict[str, Any],
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    retries: int,
    target_variants: int,
    dry_run: bool,
) -> tuple[dict[str, Any], str | None]:
    if dry_run:
        return {
            "family_name": f"{base_test.get('name', 'Litmus')} family",
            "core_semantic": base_test.get("intent", ""),
            "variant_strategy": "Deterministic fallback variants.",
            "variants": fallback_variants(base_test, target_variants),
        }, None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                test_json=json.dumps(base_test, ensure_ascii=False, indent=2),
                target_variants=target_variants,
            ),
        },
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
            family = parsed.get("family", {})
            if not isinstance(family, dict):
                family = {}
            return family, None
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            http.client.HTTPException,
            OSError,
            TimeoutError,
            json.JSONDecodeError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))

    preview = f"; raw response preview: {last_content[:500]}" if last_content else ""
    return {
        "family_name": f"{base_test.get('name', 'Litmus')} family",
        "core_semantic": base_test.get("intent", ""),
        "variant_strategy": "Fallback after generation failure.",
        "variants": fallback_variants(base_test, target_variants),
    }, (str(last_error) if last_error else "unknown error") + preview


def trace_signature(variant: dict[str, Any]) -> str:
    trace = variant.get("trace") or variant.get("minimal_trace") or []
    role = variant.get("role", "")
    oracle = variant.get("oracle", {})
    return normalize_text({"role": role, "trace": trace, "oracle": oracle})


def base_trace_signature(base: dict[str, Any]) -> str:
    return normalize_text({"trace": base.get("minimal_trace", []), "oracle": base.get("oracle", {})})


def normalize_variant(
    raw: dict[str, Any],
    *,
    base: dict[str, Any],
    family_id: str,
    variant_index: int,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    role = str(raw.get("role", "")).strip().lower()
    if role not in ALLOWED_ROLES:
        blob = normalize_text(raw)
        role = "differential_observation"
        for candidate in ALLOWED_ROLES:
            if candidate.replace("_", " ") in blob:
                role = candidate
                break
    role = role_from_text(raw, role)

    try:
        value_score = int(float(raw.get("value_score", 3)))
    except (TypeError, ValueError):
        value_score = 3
    value_score = max(1, min(5, value_score))

    expected_strength = str(raw.get("expected_strength") or base.get("expected_strength") or "conformance").strip().lower()
    if expected_strength not in {"conformance", "security_rationale", "differential", "ambiguous"}:
        expected_strength = str(base.get("expected_strength", "conformance")).lower()
    if role == "positive_control" and is_mixed_differential(base):
        expected_strength = "differential"
    if variant_mentions_normative_duplicate(base, raw):
        expected_strength = "conformance"
    elif is_security_observational(base) and expected_strength == "conformance":
        expected_strength = "security_rationale"
    if is_mixed_differential(base) and expected_strength == "conformance" and role in {"replay", "differential_observation", "security_observation"}:
        expected_strength = "differential"
    if is_mixed_differential(base) and role == "duplication" and variant_mentions_normative_duplicate(base, raw):
        expected_strength = "conformance"

    trace = normalize_nulls(raw.get("trace") or raw.get("minimal_trace") or [])
    if not isinstance(trace, list) or not trace:
        return None

    variant = {
        "variant_id": f"{family_id}_variant_{variant_index:02d}",
        "name": raw.get("name", "Litmus variant"),
        "role": role,
        "target_boundary": raw.get("target_boundary", ""),
        "delta_from_base": raw.get("delta_from_base", ""),
        "distinguishes": raw.get("distinguishes", ""),
        "preconditions": raw.get("preconditions", base.get("preconditions", [])),
        "trace": trace,
        "oracle": {},
        "harness_plan": {},
        "field_bound_checks": raw.get("field_bound_checks", base.get("field_bound_checks", [])),
        "expected_strength": expected_strength,
        "value_score": value_score,
        "value_rationale": raw.get("value_rationale", ""),
        "feasibility": raw.get("feasibility", "medium"),
        "confidence": raw.get("confidence", base.get("confidence", 0.5)),
    }
    variant["oracle"] = normalize_oracle(base, raw, expected_strength)
    variant["harness_plan"] = infer_harness_plan(base, raw)
    return variant


def variant_is_valuable(variant: dict[str, Any], base: dict[str, Any], min_value_score: int) -> tuple[bool, str]:
    if int(variant.get("value_score", 0)) < min_value_score:
        return False, "value_score below threshold"
    if not str(variant.get("delta_from_base", "")).strip():
        return False, "missing delta_from_base"
    if not str(variant.get("distinguishes", "")).strip():
        return False, "missing distinguishes"
    if normalize_text(variant.get("trace")) == normalize_text(base.get("minimal_trace")):
        # Positive controls may intentionally be close to the base, but they
        # still need a concrete delta or oracle distinction.
        if variant.get("role") != "positive_control":
            return False, "trace duplicates base"
    if trace_signature(variant) == base_trace_signature(base):
        return False, "trace and oracle duplicate base"
    if variant.get("role") in {"reordering", "timeout", "fragmentation"} and not base_supports_temporal_perturbation(base):
        return False, f"{variant.get('role')} not supported by base evidence"
    if variant.get("role") == "positive_control" and "no change" in normalize_text(variant.get("delta_from_base")):
        return False, "positive control has no concrete delta"
    evidence_blob = normalize_text(base.get("E", []))
    variant_blob = normalize_text(variant)
    if "psk" in evidence_blob and "key share" in evidence_blob:
        if "without psk" in variant_blob or "no psk" in variant_blob or "missing psk" in variant_blob:
            return False, "variant changes PSK availability instead of the evidenced key_share boundary"
    return True, "kept"


def build_family(
    *,
    base: dict[str, Any],
    raw_family: dict[str, Any],
    family_index: int,
    min_value_score: int,
    target_variants: int,
) -> dict[str, Any]:
    document_id = str(base.get("litmus_id", "litmus")).split("_litmus_")[0]
    family_id = f"{document_id}_family_{family_index:04d}"

    raw_variants = raw_family.get("variants", [])
    if not isinstance(raw_variants, list):
        raw_variants = []

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_variant in raw_variants:
        variant = normalize_variant(
            raw_variant,
            base=base,
            family_id=family_id,
            variant_index=len(kept) + 1,
        )
        if not variant:
            rejected.append({"reason": "malformed variant", "variant": raw_variant})
            continue
        keep, reason = variant_is_valuable(variant, base, min_value_score)
        sig = trace_signature(variant)
        if sig in seen:
            keep = False
            reason = "duplicate variant"
        if keep:
            seen.add(sig)
            kept.append(variant)
        else:
            rejected.append(
                {
                    "reason": reason,
                    "name": variant.get("name"),
                    "role": variant.get("role"),
                    "value_score": variant.get("value_score"),
                }
            )
        if len(kept) >= target_variants:
            break

    return {
        "family_id": family_id,
        "base_litmus_id": base.get("litmus_id"),
        "family_name": raw_family.get("family_name", f"{base.get('name', 'Litmus')} family"),
        "core_semantic": raw_family.get("core_semantic", base.get("intent", "")),
        "variant_strategy": raw_family.get("variant_strategy", ""),
        "source_items": base.get("source_items", []),
        "E": base.get("E", []),
        "field_bound_checks": base.get("field_bound_checks", []),
        "base_test": base,
        "variants": kept,
        "rejected_variants": rejected,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate valuable variants for protocol litmus tests.")
    parser.add_argument("--litmus", required=True, type=Path, help="Input litmus JSON or JSONL.")
    parser.add_argument("--out-json", required=True, type=Path, help="Output litmus families JSON.")
    parser.add_argument("--out-jsonl", type=Path, default=None, help="Optional flat variant JSONL.")
    parser.add_argument("--base-url", default=os.getenv("BLTCY_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="BLTCY_API_KEY")
    parser.add_argument("--model", default=os.getenv("BLTCY_MODEL", DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4200)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--target-variants", type=int, default=6, help="Upper bound per family; weak variants are filtered.")
    parser.add_argument("--min-value-score", type=int, default=3, help="Drop variants below this score.")
    parser.add_argument("--limit-tests", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
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
    if args.overwrite:
        for path in outputs:
            if path.exists():
                path.unlink()
    else:
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            print(f"Output exists: {', '.join(existing)}. Use --overwrite.", file=sys.stderr)
            sys.exit(2)

    metadata, tests = load_tests(args.litmus)
    if args.limit_tests is not None:
        tests = tests[: args.limit_tests]
    if not tests:
        print("No litmus tests found.", file=sys.stderr)
        sys.exit(1)

    families: list[dict[str, Any]] = []
    flat_variants: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for index, test in enumerate(tests, start=1):
        print(f"[{index}/{len(tests)}] generating variants for {test.get('litmus_id')} {test.get('name')}")
        raw_family, error = generate_raw_variants(
            base_test=test,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            retries=args.retries,
            target_variants=args.target_variants,
            dry_run=args.dry_run,
        )
        family = build_family(
            base=test,
            raw_family=raw_family,
            family_index=index,
            min_value_score=args.min_value_score,
            target_variants=args.target_variants,
        )
        families.append(family)
        for variant in family["variants"]:
            flat = {
                "family_id": family["family_id"],
                "base_litmus_id": family["base_litmus_id"],
                "source_items": family["source_items"],
                "E": family["E"],
                **variant,
            }
            flat_variants.append(flat)
        results.append(
            {
                "family_id": family["family_id"],
                "base_litmus_id": family["base_litmus_id"],
                "variants": len(family["variants"]),
                "rejected_variants": len(family["rejected_variants"]),
                "error": error,
            }
        )
        if error:
            print(f"  error: {error}")
        print(f"  kept={len(family['variants'])}, rejected={len(family['rejected_variants'])}")

    output = {
        "metadata": {
            "litmus_file": str(args.litmus),
            "source_metadata": metadata,
            "model": "dry-run" if args.dry_run else args.model,
            "base_url": args.base_url,
            "base_test_count": len(tests),
            "family_count": len(families),
            "variant_count": len(flat_variants),
            "target_variants": args.target_variants,
            "min_value_score": args.min_value_score,
        },
        "results": results,
        "families": families,
        "variants": flat_variants,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_jsonl:
        write_jsonl(args.out_jsonl, flat_variants)
    print(json.dumps(output["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
