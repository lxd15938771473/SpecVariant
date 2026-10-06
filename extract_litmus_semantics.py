#!/usr/bin/env python3
"""Extract standard-grounded protocol semantics for litmus-test synthesis.

Input: chunks JSONL produced by chunk_document.py.
Output:
  1. per-chunk JSONL with extracted items
  2. merged JSON with all items and run metadata

The script uses the official OpenAI Chat Completions API. By default it calls
https://api.openai.com/v1/chat/completions and reads OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.5"
BOUND_EXPR_RE = r"(?:2\s*\^\s*\d+(?:\s*[+-]\s*\d+)?|0x[0-9a-fA-F]+|\d+)"

SYSTEM_PROMPT = """You are extracting protocol-standard semantics for protocol litmus-test synthesis.

Return valid JSON only. Do not include markdown fences or explanatory prose.

Task:
- Read one chunk from a protocol standard.
- Extract only compact, testable protocol semantics that could become litmus tests.
- Prefer subtle boundary semantics over broad summaries.
- Evidence is mandatory: every extracted item must cite exact source line numbers and a short quote from the chunk.
- Do not invent protocol behavior. If the chunk has no useful testable semantics, return {"items": []}.
- The expected_behavior must be directly entailed by the cited evidence. Do not turn a described risk into a rejection rule unless the text states that rejection or enforcement behavior.
- The category must be exactly one label, not a combination such as "state|temporal".

Target semantics include:
- state/temporal behavior: ordering, reordering, retransmission, duplication, rollback, cross-phase messages
- field or value constraints: boundary values, reserved bits, duplicate extensions, unknown values
- field bounds: explicit lower/upper bounds, vector length ranges, integer widths, "MUST NOT exceed" limits, and required handling for out-of-range values
- negotiation semantics: negotiated parameters constraining later behavior
- security goals: replay, downgrade, authentication binding, anti-amplification, privacy leakage
- resource/exception behavior: timeout, error handling, resource release, rate limits
- cross-layer binding: lower-layer guarantees or identities constraining upper-layer behavior

Important modality rule:
- If the text says a guarantee is absent, weaker, not provided, or only a warning, use modality SECURITY_RATIONALE.
- Do not rewrite absent guarantees as MUST, MUST_NOT, accept, or reject unless the text explicitly states required enforcement behavior.
"""

USER_TEMPLATE = """Document: {document_id}
Chunk: {chunk_id}
Sections: {sections}
Original line range: L{line_start}-L{line_end}

Return this JSON shape exactly:
{{
  "items": [
    {{
      "category": "state|temporal|field|negotiation|security|resource|cross_layer|error_handling",
      "modality": "MUST|MUST_NOT|SHOULD|SHOULD_NOT|MAY|REQUIRED|SECURITY_RATIONALE|IMPLICIT|OTHER",
      "subject": "protocol object, message, extension, field, state, or mechanism",
      "condition": "when this semantic applies",
      "stimulus": "minimal input/event/trace that exercises the semantic",
      "expected_behavior": "standard-required or security-expected behavior",
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
      "litmus_boundary": [
        "short boundary scenario useful for a litmus test"
      ],
      "oracle": "accept|reject|alert|ignore|state_update|no_state_update|resource_bound|security_property|other",
      "candidate_trace": [
        "step 1",
        "step 2"
      ],
      "E": [
        {{
          "line_start": 0,
          "line_end": 0,
          "quote": "short exact evidence quote from the numbered lines, encoded as a single JSON string"
        }}
      ],
      "confidence": 0.0
    }}
  ]
}}

Rules:
- Keep each item atomic: one semantic obligation or one security expectation.
- Use absolute line numbers from the chunk, e.g. 1024, not relative positions.
- Evidence quotes must be copied from the numbered lines and should be short. Use one-line excerpts; do not include raw newlines.
- The cited evidence must support the item's condition and expected_behavior, not merely mention the same subject.
- For every field, value, vector length, registry range, or size-limit semantic, fill field_bound_checks with the explicit lower and upper bounds from the text. Use null for a missing side.
- If the chunk only implies a general boundary but gives no explicit numeric/value bound, use an empty field_bound_checks list.
- Boundary checks must include whether the lower and upper limits are inclusive when the text makes that clear.
- If a statement is descriptive and has no testable behavior, omit it.
- If the standard says behavior is weaker or not guaranteed, extract it only when it implies a useful negative/security litmus boundary.
- For "no guarantee" or "weaker guarantee" statements, set oracle to "security_property" or "other", not "accept" or "reject".

Numbered chunk:
{numbered_text}
"""

REPAIR_SYSTEM_PROMPT = """You repair malformed JSON.

Return valid JSON only. Do not include markdown fences. Preserve all fields and values that can be recovered.
If the input is too broken to recover, return {"items": []}.
"""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def write_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def numbered_text(chunk: dict[str, Any], max_chars: int) -> str:
    lines = chunk.get("lines") or []
    rendered = "\n".join(f"[L{line['n']}] {line['text']}" for line in lines)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars] + "\n[TRUNCATED]"


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
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
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
    repair_prompt = (
        "Repair the following malformed JSON into the required object shape "
        '{"items": [...]}. Keep evidence line numbers and quotes when possible.\n\n'
        "Malformed JSON:\n"
        f"{malformed[:16000]}"
    )
    content = call_chat_completion(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=[
            {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
            {"role": "user", "content": repair_prompt},
        ],
        temperature=None,
        max_tokens=2200,
        timeout=timeout,
    )
    return parse_json_object(content)


def compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def comparable(text: str) -> str:
    return compact_ws(text).casefold()


def loose_comparable(text: str) -> str:
    return re.sub(r"[^\w\s-]", "", comparable(text))


def parse_bound_number(value: str) -> int | str:
    cleaned = value.strip().rstrip(".,;:")
    power = re.fullmatch(r"2\s*\^\s*(\d+)(?:\s*([+-])\s*(\d+))?", cleaned)
    if power:
        base = 2 ** int(power.group(1))
        if power.group(2) == "+":
            return base + int(power.group(3))
        if power.group(2) == "-":
            return base - int(power.group(3))
        return base
    if re.fullmatch(r"0x[0-9a-fA-F]+", cleaned):
        return int(cleaned, 16)
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned)
    return cleaned


def uint_upper_bound(value_type: str) -> int | None:
    match = re.fullmatch(r"uint(\d+)", value_type.casefold())
    if not match:
        return None
    bits = int(match.group(1))
    return (2 ** bits) - 1


def normalize_bound_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if not text or text.casefold() in {"null", "none", "n/a", "unknown"}:
        return None
    return parse_bound_number(text)


def optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def line_text_for_evidence(chunk: dict[str, Any], evidence: dict[str, Any]) -> str:
    try:
        line_start = int(evidence.get("line_start"))
        line_end = int(evidence.get("line_end"))
    except (TypeError, ValueError):
        return str(evidence.get("quote", ""))
    lines = [
        str(line.get("text", ""))
        for line in chunk.get("lines", [])
        if line_start <= int(line.get("n", -1)) <= line_end
    ]
    return " ".join(lines) or str(evidence.get("quote", ""))


def bound_check_key(check: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(check.get("field", "")).casefold(),
        str(check.get("bound_kind", "")).casefold(),
        str(check.get("value_type", "")).casefold(),
        str(check.get("lower_bound", "")),
        str(check.get("upper_bound", "")),
        str(check.get("unit", "")).casefold(),
    )


def make_bound_check(
    *,
    field: str,
    bound_kind: str,
    value_type: str,
    lower_bound: Any,
    lower_inclusive: bool | None,
    upper_bound: Any,
    upper_inclusive: bool | None,
    unit: str,
    applies_to: str,
    required_check: str,
    source: str,
) -> dict[str, Any]:
    return {
        "field": field,
        "bound_kind": bound_kind,
        "value_type": value_type,
        "lower_bound": normalize_bound_value(lower_bound),
        "lower_inclusive": lower_inclusive,
        "upper_bound": normalize_bound_value(upper_bound),
        "upper_inclusive": upper_inclusive,
        "unit": unit,
        "applies_to": applies_to,
        "required_check": compact_ws(required_check),
        "source": source,
    }


def infer_field_bound_checks(raw: dict[str, Any], chunk: dict[str, Any], normalized_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    context_parts = [
        str(raw.get("subject", "")),
        str(raw.get("condition", "")),
        str(raw.get("stimulus", "")),
        str(raw.get("expected_behavior", "")),
    ]
    for ev in normalized_evidence:
        context_parts.append(str(ev.get("quote", "")))
        context_parts.append(line_text_for_evidence(chunk, ev))
    context = compact_ws(" ".join(context_parts))
    context_lower = context.casefold()
    checks: list[dict[str, Any]] = []

    field_hint = compact_ws(str(raw.get("subject", ""))) or "protocol field"
    applies_to = compact_ws(str(raw.get("condition", ""))) or "when this semantic applies"
    required_check = compact_ws(str(raw.get("expected_behavior", ""))) or "enforce the extracted field bound"

    for match in re.finditer(r"\b(uint(?:8|16|24|32|64))\b", context, flags=re.I):
        value_type = match.group(1).lower()
        upper = uint_upper_bound(value_type)
        if upper is None:
            continue
        checks.append(
            make_bound_check(
                field=field_hint,
                bound_kind="integer_width",
                value_type=value_type,
                lower_bound=0,
                lower_inclusive=True,
                upper_bound=upper,
                upper_inclusive=True,
                unit="value",
                applies_to=applies_to,
                required_check=required_check,
                source="deterministic:uint-width",
            )
        )

    vector_pattern = re.compile(
        r"\b([A-Za-z][A-Za-z0-9_.-]*(?:\s+[A-Za-z][A-Za-z0-9_.-]*)?)\s*<\s*([^<>\s]+)\s*\.\.\s*([^<>\s]+)\s*>"
    )
    for match in vector_pattern.finditer(context):
        checks.append(
            make_bound_check(
                field=match.group(1).strip(),
                bound_kind="vector_length",
                value_type="bytes",
                lower_bound=match.group(2),
                lower_inclusive=True,
                upper_bound=match.group(3),
                upper_inclusive=True,
                unit="bytes",
                applies_to=applies_to,
                required_check=required_check,
                source="deterministic:vector-range",
            )
        )

    range_pattern = re.compile(
        r"\b(?:range|values?)\s+(?:of\s+|in\s+)?(?:the\s+)?(?:range\s+)?"
        rf"({BOUND_EXPR_RE})"
        r"\s*(?:-|to|through|\.\.)\s*"
        rf"({BOUND_EXPR_RE})",
        flags=re.I,
    )
    for match in range_pattern.finditer(context):
        checks.append(
            make_bound_check(
                field=field_hint,
                bound_kind="numeric_range",
                value_type="other",
                lower_bound=match.group(1),
                lower_inclusive=True,
                upper_bound=match.group(2),
                upper_inclusive=True,
                unit="value",
                applies_to=applies_to,
                required_check=required_check,
                source="deterministic:numeric-range",
            )
        )

    exceed_pattern = re.compile(
        r"(?:MUST\s+NOT\s+exceed|not\s+exceed|maximum(?:\s+length|\s+size)?(?:\s+of)?|limited\s+to|limit\s+of)\s+"
        rf"({BOUND_EXPR_RE})",
        flags=re.I,
    )
    for match in exceed_pattern.finditer(context):
        unit = "bytes" if any(word in context_lower for word in ("byte", "octet", "length", "size")) else "value"
        checks.append(
            make_bound_check(
                field=field_hint,
                bound_kind="upper_bound",
                value_type="other",
                lower_bound=None,
                lower_inclusive=None,
                upper_bound=match.group(1),
                upper_inclusive=True,
                unit=unit,
                applies_to=applies_to,
                required_check=required_check,
                source="deterministic:upper-bound",
            )
        )

    lower_pattern = re.compile(
        r"(?:at\s+least|minimum(?:\s+length|\s+size)?(?:\s+of)?|no\s+less\s+than)\s+"
        rf"({BOUND_EXPR_RE})",
        flags=re.I,
    )
    for match in lower_pattern.finditer(context):
        unit = "bytes" if any(word in context_lower for word in ("byte", "octet", "length", "size")) else "value"
        checks.append(
            make_bound_check(
                field=field_hint,
                bound_kind="lower_bound",
                value_type="other",
                lower_bound=match.group(1),
                lower_inclusive=True,
                upper_bound=None,
                upper_inclusive=None,
                unit=unit,
                applies_to=applies_to,
                required_check=required_check,
                source="deterministic:lower-bound",
            )
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for check in checks:
        key = bound_check_key(check)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(check)
    return deduped


def normalize_field_bound_checks(raw: dict[str, Any], chunk: dict[str, Any], normalized_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_checks = raw.get("field_bound_checks")
    normalized: list[dict[str, Any]] = []
    if isinstance(raw_checks, list):
        for check in raw_checks:
            if not isinstance(check, dict):
                continue
            field = compact_ws(str(check.get("field", "")))
            if not field:
                continue
            normalized.append(
                make_bound_check(
                    field=field,
                    bound_kind=compact_ws(str(check.get("bound_kind", "other"))) or "other",
                    value_type=compact_ws(str(check.get("value_type", "other"))) or "other",
                    lower_bound=check.get("lower_bound"),
                    lower_inclusive=optional_bool(check.get("lower_inclusive")),
                    upper_bound=check.get("upper_bound"),
                    upper_inclusive=optional_bool(check.get("upper_inclusive")),
                    unit=compact_ws(str(check.get("unit", "other"))) or "other",
                    applies_to=compact_ws(str(check.get("applies_to", ""))) or compact_ws(str(raw.get("condition", ""))),
                    required_check=compact_ws(str(check.get("required_check", ""))) or compact_ws(str(raw.get("expected_behavior", ""))),
                    source="model",
                )
            )

    inferred = infer_field_bound_checks(raw, chunk, normalized_evidence)
    seen = {bound_check_key(check) for check in normalized}
    for check in inferred:
        key = bound_check_key(check)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(check)
    return normalized


def evidence_verified(chunk: dict[str, Any], evidence: dict[str, Any]) -> bool:
    try:
        line_start = int(evidence.get("line_start"))
        line_end = int(evidence.get("line_end"))
    except (TypeError, ValueError):
        return False

    if line_start < int(chunk["line_start"]) or line_end > int(chunk["line_end"]) or line_start > line_end:
        return False

    quote = compact_ws(str(evidence.get("quote", "")))
    if not quote:
        return False

    line_texts = [
        line["text"]
        for line in chunk.get("lines", [])
        if line_start <= int(line["n"]) <= line_end
    ]
    if not line_texts:
        return False

    haystack = comparable(" ".join(line_texts))
    quote = comparable(quote)
    if quote in haystack or haystack in quote:
        return True
    loose_quote = loose_comparable(quote)
    loose_haystack = loose_comparable(haystack)
    return loose_quote in loose_haystack or loose_haystack in loose_quote


def locate_evidence_by_quote(chunk: dict[str, Any], quote: str, max_window: int = 6) -> tuple[int, int] | None:
    needle = comparable(quote)
    if not needle:
        return None

    lines = chunk.get("lines", [])
    for window_size in range(1, max_window + 1):
        for idx in range(0, len(lines) - window_size + 1):
            window = lines[idx : idx + window_size]
            haystack = comparable(" ".join(str(line["text"]) for line in window))
            if needle in haystack or haystack in needle:
                return int(window[0]["n"]), int(window[-1]["n"])
            loose_needle = loose_comparable(needle)
            loose_haystack = loose_comparable(haystack)
            if loose_needle in loose_haystack or loose_haystack in loose_needle:
                return int(window[0]["n"]), int(window[-1]["n"])
    return None


def normalize_category(value: Any) -> str:
    allowed = {
        "state",
        "temporal",
        "field",
        "negotiation",
        "security",
        "resource",
        "cross_layer",
        "error_handling",
    }
    raw = str(value or "other").strip().lower()
    if raw in allowed:
        return raw
    for part in re.split(r"[^a-zA-Z_]+", raw):
        if part in allowed:
            return part
    return "other"


def normalize_item(raw: dict[str, Any], chunk: dict[str, Any], item_index: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    evidence = raw.get("E")
    if not isinstance(evidence, list) or not evidence:
        return None

    normalized_evidence: list[dict[str, Any]] = []
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        quote = str(ev.get("quote", "")).strip()
        verified = evidence_verified(chunk, ev)
        line_start = ev.get("line_start")
        line_end = ev.get("line_end")
        corrected = False
        if not verified:
            located = locate_evidence_by_quote(chunk, quote)
            if located:
                line_start, line_end = located
                verified = True
                corrected = True
        normalized_evidence.append(
            {
                "line_start": line_start,
                "line_end": line_end,
                "quote": quote,
                "verified": verified,
                "line_corrected": corrected,
            }
        )

    if not normalized_evidence:
        return None

    field_bound_checks = normalize_field_bound_checks(raw, chunk, normalized_evidence)
    item = {
        "item_id": f"{chunk['chunk_id']}_item_{item_index:02d}",
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "source_path": chunk["source_path"],
        "sections": chunk.get("sections", []),
        "category": normalize_category(raw.get("category", "other")),
        "modality": raw.get("modality", "OTHER"),
        "subject": raw.get("subject", ""),
        "condition": raw.get("condition", ""),
        "stimulus": raw.get("stimulus", ""),
        "expected_behavior": raw.get("expected_behavior", ""),
        "field_bound_checks": field_bound_checks,
        "litmus_boundary": raw.get("litmus_boundary", []),
        "oracle": raw.get("oracle", "other"),
        "candidate_trace": raw.get("candidate_trace", []),
        "E": normalized_evidence,
        "confidence": raw.get("confidence", None),
    }
    return item


def extract_chunk(
    *,
    chunk: dict[str, Any],
    base_url: str,
    api_key: str,
    model: str,
    temperature: float | None,
    max_tokens: int,
    prompt_max_chars: int,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    user_prompt = USER_TEMPLATE.format(
        document_id=chunk["document_id"],
        chunk_id=chunk["chunk_id"],
        sections=", ".join(chunk.get("sections", [])),
        line_start=chunk["line_start"],
        line_end=chunk["line_end"],
        numbered_text=numbered_text(chunk, prompt_max_chars),
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
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
            raw_items = parsed.get("items", [])
            if not isinstance(raw_items, list):
                raw_items = []
            items = []
            for idx, raw_item in enumerate(raw_items, start=1):
                item = normalize_item(raw_item, chunk, idx)
                if item:
                    items.append(item)
            return {
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "sections": chunk.get("sections", []),
                "model": model,
                "items": items,
                "raw_item_count": len(raw_items),
                "error": None,
            }
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))

    return {
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "line_start": chunk["line_start"],
        "line_end": chunk["line_end"],
        "sections": chunk.get("sections", []),
        "model": model,
        "items": [],
        "raw_item_count": 0,
        "error": str(last_error) if last_error else "unknown error",
        "raw_response_preview": last_content[:1200],
    }


def select_chunks(
    chunks: list[dict[str, Any]],
    *,
    start_index: int,
    limit: int | None,
    keyword: str | None,
) -> list[dict[str, Any]]:
    selected = [chunk for chunk in chunks if int(chunk.get("chunk_index", 0)) >= start_index]
    if keyword:
        needle = keyword.lower()
        selected = [
            chunk
            for chunk in selected
            if needle in chunk.get("text", "").lower()
            or any(needle in section.lower() for section in chunk.get("sections", []))
        ]
    if limit is not None:
        selected = selected[:limit]
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract protocol litmus-test semantics from document chunks.")
    parser.add_argument("--chunks", required=True, type=Path, help="Chunks JSONL from chunk_document.py.")
    parser.add_argument("--out-jsonl", required=True, type=Path, help="Per-chunk extraction JSONL.")
    parser.add_argument("--out-json", required=True, type=Path, help="Merged extraction JSON.")
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable holding the API key.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=None, help="Optional sampling temperature; only use with models that support it.")
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--prompt-max-chars", type=int, default=12000)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--start-index", type=int, default=0, help="Skip chunks before this chunk_index.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of chunks to process.")
    parser.add_argument("--keyword", default=None, help="Only process chunks containing this keyword.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        print(f"Missing API key. Set {args.api_key_env}.", file=sys.stderr)
        sys.exit(2)

    if args.overwrite:
        for path in (args.out_jsonl, args.out_json):
            if path.exists():
                path.unlink()
    elif args.out_jsonl.exists() or args.out_json.exists():
        print("Output file exists. Use --overwrite to replace it.", file=sys.stderr)
        sys.exit(2)

    chunks = load_jsonl(args.chunks)
    selected = select_chunks(chunks, start_index=args.start_index, limit=args.limit, keyword=args.keyword)
    if not selected:
        print("No chunks selected.", file=sys.stderr)
        sys.exit(1)

    all_items: list[dict[str, Any]] = []
    chunk_results: list[dict[str, Any]] = []

    for position, chunk in enumerate(selected, start=1):
        print(
            f"[{position}/{len(selected)}] extracting {chunk['chunk_id']} "
            f"L{chunk['line_start']}-L{chunk['line_end']} sections={chunk.get('sections', [])}"
        )
        result = extract_chunk(
            chunk=chunk,
            base_url=args.base_url,
            api_key=api_key,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            prompt_max_chars=args.prompt_max_chars,
            timeout=args.timeout,
            retries=args.retries,
        )
        write_jsonl_row(args.out_jsonl, result)
        chunk_results.append(result)
        all_items.extend(result["items"])
        if result["error"]:
            print(f"  error: {result['error']}")
        else:
            verified = sum(
                1
                for item in result["items"]
                if all(ev.get("verified") for ev in item.get("E", []))
            )
            bound_checks = sum(len(item.get("field_bound_checks", [])) for item in result["items"])
            print(f"  items={len(result['items'])}, fully_verified_evidence={verified}, field_bound_checks={bound_checks}")

    field_bound_check_count = sum(len(item.get("field_bound_checks", [])) for item in all_items)
    merged = {
        "metadata": {
            "chunks_file": str(args.chunks),
            "processed_chunks": len(selected),
            "model": args.model,
            "base_url": args.base_url,
            "item_count": len(all_items),
            "field_bound_check_count": field_bound_check_count,
        },
        "chunk_results": chunk_results,
        "items": all_items,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(merged["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
