"""Deterministic chunking and validation for agent-extracted requirements."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

REQUIREMENTS_SCHEMA = "speclitmus.requirements.v3"
CHUNKS_SCHEMA = "speclitmus.chunks.v1"
CHUNK_EXTRACTION_SCHEMA = "speclitmus.chunk-extraction.v1"

CHECK_TYPES = {
    "explicit_normative",
    "structural_constraint",
    "state_machine_invariant",
    "security_consistency",
    "error_handling",
    "derived_constraint",
}
ELIGIBILITY_VALUES = {"eligible", "exclude"}
LEGACY_REQUIREMENT_FIELDS = {"section", "derivation_kind", "subject", "modality"}
AGENT_ITEM_STRING_FIELDS = (
    "quote",
    "condition",
    "required_behavior",
    "forbidden_behavior",
    "error_behavior",
    "check_type",
    "why_checkable",
    "eligibility",
)

_SECTION_PATTERNS = (
    re.compile(r"^(#{1,6})\s+(.+?)\s*$"),
    re.compile(r"^\s*((?:\d+\.)*\d+\.?)\s+([A-Z][^\n]{1,160}?)\s*$"),
    re.compile(
        r"^\s*(Appendix\s+[A-Z](?:\.\d+)*)\.?\s+([^\n]{1,160}?)\s*$",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class Paragraph:
    section: str
    line_start: int
    line_end: int
    text: str


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def read_source(path: Path) -> tuple[bytes, str, list[str]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"source is not valid UTF-8: {exc}") from exc
    return raw, text, text.splitlines()


def source_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dump_json_bytes(data: dict[str, Any]) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 200:
        return None
    # Publication dates such as "07. March 2019" satisfy the generic
    # numbered-heading shape but are front-matter metadata, not sections.
    if re.fullmatch(r"\d{1,2}\.?(?:\s+)[A-Z][a-z]+\s+\d{4}", stripped):
        return None
    # RFC body headings are flush left. Indented numbered lists and cross
    # references must remain part of their surrounding paragraph.
    if line != line.lstrip():
        return None
    # Table-of-contents entries commonly end in either dot leaders plus a
    # page number or a tab-delimited page number. Keep them as ordinary
    # source text so their titles cannot become sticky section labels for a
    # converted document whose body headings lost their numeric prefixes.
    if re.search(r"(?:\.{3,}|\t+)\d+\s*$", stripped):
        return None
    markdown = _SECTION_PATTERNS[0].match(line)
    if markdown:
        return markdown.group(2).strip()
    numbered = _SECTION_PATTERNS[1].match(line)
    if numbered:
        number = numbered.group(1).rstrip(".")
        return f"{number}. {numbered.group(2).strip()}"
    appendix = _SECTION_PATTERNS[2].match(line)
    if appendix:
        return f"{appendix.group(1)}. {appendix.group(2).strip()}"
    return None


def paragraphs_from_lines(lines: Sequence[str]) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    current_section = "Preamble"
    buffer: list[str] = []
    start = 0

    def flush(end_line: int) -> None:
        nonlocal buffer, start
        if not buffer:
            return
        paragraphs.append(
            Paragraph(
                section=current_section,
                line_start=start,
                line_end=end_line,
                text="\n".join(buffer),
            )
        )
        buffer = []
        start = 0

    for index, line in enumerate(lines, start=1):
        heading = _heading(line)
        if heading is not None:
            flush(index - 1)
            current_section = heading
            paragraphs.append(Paragraph(current_section, index, index, line))
            continue
        if not line.strip():
            flush(index - 1)
            continue
        if not buffer:
            start = index
        buffer.append(line)
    flush(len(lines))
    return paragraphs


def _sentence_spans(text: str) -> Iterable[tuple[int, int]]:
    start = 0
    for match in re.finditer(r"(?:[.!?](?=\s|$)|\n\s*\n)", text):
        end = match.end()
        if text[start:end].strip():
            yield start, end
        start = end
    if text[start:].strip():
        yield start, len(text)


def _line_for_offset(text: str, base_line: int, offset: int) -> int:
    return base_line + text.count("\n", 0, offset)


def _split_oversized(paragraph: Paragraph, max_chars: int) -> list[Paragraph]:
    if len(paragraph.text) <= max_chars:
        return [paragraph]
    sentence_parts: list[Paragraph] = []
    for start, end in _sentence_spans(paragraph.text):
        raw = paragraph.text[start:end]
        leading = len(raw) - len(raw.lstrip())
        trailing_end = len(raw.rstrip())
        if trailing_end <= leading:
            continue
        text_start = start + leading
        text_end = start + trailing_end
        sentence_parts.append(
            Paragraph(
                paragraph.section,
                _line_for_offset(paragraph.text, paragraph.line_start, text_start),
                _line_for_offset(
                    paragraph.text, paragraph.line_start, max(text_start, text_end - 1)
                ),
                paragraph.text[text_start:text_end],
            )
        )
    if len(sentence_parts) <= 1:
        return [paragraph]

    pieces: list[Paragraph] = []
    current: list[Paragraph] = []
    for sentence in sentence_parts:
        projected = len("\n".join([part.text for part in current] + [sentence.text]))
        if current and projected > max_chars:
            pieces.append(
                Paragraph(
                    paragraph.section,
                    current[0].line_start,
                    current[-1].line_end,
                    "\n".join(part.text for part in current),
                )
            )
            current = []
        current.append(sentence)
    if current:
        pieces.append(
            Paragraph(
                paragraph.section,
                current[0].line_start,
                current[-1].line_end,
                "\n".join(part.text for part in current),
            )
        )
    return pieces


def make_chunks(
    lines: Sequence[str], max_chars: int, overlap_paragraphs: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if max_chars < 128:
        raise ValueError("max_chars must be at least 128")
    if overlap_paragraphs < 0:
        raise ValueError("overlap_paragraphs must be non-negative")

    base = paragraphs_from_lines(lines)
    paragraphs = [
        piece
        for paragraph in base
        for piece in _split_oversized(paragraph, max_chars)
    ]
    chunks: list[dict[str, Any]] = []
    index = 0
    previous_by_section: dict[str, list[Paragraph]] = {}

    while index < len(paragraphs):
        section = paragraphs[index].section
        assigned: list[Paragraph] = []
        length = 0
        while index < len(paragraphs) and paragraphs[index].section == section:
            paragraph = paragraphs[index]
            projected = length + (2 if assigned else 0) + len(paragraph.text)
            if assigned and projected > max_chars:
                break
            assigned.append(paragraph)
            length = projected
            index += 1

        prior = previous_by_section.get(section, [])
        overlap = prior[-overlap_paragraphs:] if overlap_paragraphs else []
        combined = overlap + assigned
        while overlap and len("\n\n".join(p.text for p in combined)) > max_chars:
            overlap = overlap[1:]
            combined = overlap + assigned

        chunks.append(
            {
                "chunk_id": f"chunk-{len(chunks) + 1:04d}",
                "section": section,
                "line_start": min(p.line_start for p in combined),
                "line_end": max(p.line_end for p in combined),
                "text": "\n\n".join(p.text for p in combined),
                "content_line_ranges": [
                    [p.line_start, p.line_end] for p in assigned
                ],
                "overlap_line_ranges": [
                    [p.line_start, p.line_end] for p in overlap
                ],
            }
        )
        previous_by_section[section] = prior + assigned

    covered = {
        line_no
        for chunk in chunks
        for start, end in chunk["content_line_ranges"]
        for line_no in range(start, end + 1)
        if lines[line_no - 1].strip()
    }
    nonblank = {i for i, line in enumerate(lines, 1) if line.strip()}
    return chunks, {
        "covered_nonblank_line_count": len(covered),
        "total_nonblank_line_count": len(nonblank),
        "coverage_complete": covered == nonblank,
    }


def build_chunk_artifact(
    document_id: str,
    raw: bytes,
    lines: Sequence[str],
    max_chars: int,
    overlap_paragraphs: int,
) -> dict[str, Any]:
    chunks, coverage = make_chunks(lines, max_chars, overlap_paragraphs)
    return {
        "schema_version": CHUNKS_SCHEMA,
        "metadata": {
            "document_id": document_id,
            "source_sha256": source_digest(raw),
            "source_line_count": len(lines),
            "max_chars": max_chars,
            "overlap_paragraphs": overlap_paragraphs,
            "chunk_count": len(chunks),
            **coverage,
        },
        "chunks": chunks,
    }


def validate_chunks(
    chunks_artifact: dict[str, Any], raw: bytes, lines: Sequence[str]
) -> list[str]:
    errors: list[str] = []
    if chunks_artifact.get("schema_version") != CHUNKS_SCHEMA:
        errors.append("unexpected chunks schema_version")
    metadata = chunks_artifact.get("metadata")
    chunks = chunks_artifact.get("chunks")
    if not isinstance(metadata, dict):
        return errors + ["chunks metadata must be an object"]
    if not isinstance(chunks, list):
        return errors + ["chunks must be an array"]
    if metadata.get("source_sha256") != source_digest(raw):
        errors.append("chunks source_sha256 does not match source")
    if metadata.get("source_line_count") != len(lines):
        errors.append("chunks source_line_count does not match source")

    ids: set[str] = set()
    covered: set[int] = set()
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            errors.append(f"chunks[{index}] must be an object")
            continue
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            errors.append(f"chunks[{index}].chunk_id must be a non-empty string")
            continue
        if chunk_id in ids:
            errors.append(f"duplicate chunk_id: {chunk_id}")
        ids.add(chunk_id)
        start = chunk.get("line_start")
        end = chunk.get("line_end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 1
            or end < start
            or end > len(lines)
        ):
            errors.append(f"{chunk_id}: invalid line interval")
            continue
        text = chunk.get("text")
        if not isinstance(text, str) or not text:
            errors.append(f"{chunk_id}: text must be a non-empty string")
        for pair in chunk.get("content_line_ranges", []):
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(isinstance(value, int) for value in pair)
            ):
                errors.append(f"{chunk_id}: invalid content_line_ranges entry")
                continue
            pair_start, pair_end = pair
            if pair_start < start or pair_end > end or pair_end < pair_start:
                errors.append(f"{chunk_id}: content range is outside chunk interval")
                continue
            covered.update(
                line_no
                for line_no in range(pair_start, pair_end + 1)
                if lines[line_no - 1].strip()
            )

    nonblank = {i for i, line in enumerate(lines, 1) if line.strip()}
    if covered != nonblank:
        errors.append(
            "chunk content coverage is incomplete; "
            f"missing lines: {sorted(nonblank - covered)}"
        )
    expected = {
        "chunk_count": len(chunks),
        "covered_nonblank_line_count": len(covered),
        "total_nonblank_line_count": len(nonblank),
        "coverage_complete": covered == nonblank,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            errors.append(f"chunks metadata.{key} is {metadata.get(key)!r}; expected {value!r}")
    return errors


def _requirement_id(document_id: str, item: dict[str, Any]) -> str:
    identity = "\x1f".join(
        (
            document_id,
            str(item["line_start"]),
            str(item["line_end"]),
            normalize_whitespace(item["quote"]),
            normalize_whitespace(item["condition"]),
            normalize_whitespace(item["required_behavior"]),
            normalize_whitespace(item["forbidden_behavior"]),
            normalize_whitespace(item["error_behavior"]),
            item["check_type"],
        )
    )
    return "req-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _validate_agent_item(
    item: Any,
    chunk: dict[str, Any],
    lines: Sequence[str],
    label: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(item, dict):
        return [f"{label} must be an object"]
    for field in AGENT_ITEM_STRING_FIELDS:
        if not isinstance(item.get(field), str):
            errors.append(f"{label}.{field} must be a string")
    if errors:
        return errors
    start = item.get("line_start")
    end = item.get("line_end")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 1
        or end < start
        or end > len(lines)
    ):
        errors.append(f"{label}: invalid line interval")
        return errors
    if not (chunk["line_start"] <= start <= end <= chunk["line_end"]):
        errors.append(f"{label}: evidence range is outside {chunk['chunk_id']}")
    quote = normalize_whitespace(item["quote"])
    source_slice = normalize_whitespace("\n".join(lines[start - 1 : end]))
    if not quote or quote not in source_slice:
        errors.append(f"{label}: quote does not match the declared source lines")
    if quote not in normalize_whitespace(str(chunk["text"])):
        errors.append(f"{label}: quote does not occur in {chunk['chunk_id']}")
    if item["check_type"] not in CHECK_TYPES:
        errors.append(f"{label}: invalid check_type {item['check_type']!r}")
    if item["eligibility"] not in ELIGIBILITY_VALUES:
        errors.append(f"{label}: invalid eligibility {item['eligibility']!r}")
    if not item["why_checkable"].strip():
        errors.append(f"{label}.why_checkable must not be empty")
    if item["eligibility"] == "eligible" and not any(
        item[field].strip()
        for field in ("required_behavior", "forbidden_behavior", "error_behavior")
    ):
        errors.append(f"{label}: eligible item has no checkable behavior")
    return errors


def build_requirement_artifact(
    chunks_artifact: dict[str, Any],
    agent_outputs: Sequence[dict[str, Any]],
    raw: bytes,
    lines: Sequence[str],
) -> tuple[dict[str, Any], list[str]]:
    errors = validate_chunks(chunks_artifact, raw, lines)
    metadata = chunks_artifact.get("metadata", {})
    chunks = chunks_artifact.get("chunks", [])
    chunks_by_id = {
        chunk["chunk_id"]: chunk
        for chunk in chunks
        if isinstance(chunk, dict) and isinstance(chunk.get("chunk_id"), str)
    }
    outputs_by_chunk: dict[str, dict[str, Any]] = {}
    for index, output in enumerate(agent_outputs):
        if not isinstance(output, dict):
            errors.append(f"agent output {index} must be an object")
            continue
        if output.get("schema_version") != CHUNK_EXTRACTION_SCHEMA:
            errors.append(f"agent output {index} has unexpected schema_version")
        chunk_id = output.get("chunk_id")
        if not isinstance(chunk_id, str) or chunk_id not in chunks_by_id:
            errors.append(f"agent output {index} has unknown chunk_id {chunk_id!r}")
            continue
        if chunk_id in outputs_by_chunk:
            errors.append(f"duplicate agent output for {chunk_id}")
            continue
        if output.get("status") != "complete":
            errors.append(f"{chunk_id}: status must be 'complete'")
        items = output.get("requirements")
        if not isinstance(items, list):
            errors.append(f"{chunk_id}: requirements must be an array")
            items = []
        for item_index, item in enumerate(items):
            errors.extend(
                _validate_agent_item(
                    item,
                    chunks_by_id[chunk_id],
                    lines,
                    f"{chunk_id}.requirements[{item_index}]",
                )
            )
        outputs_by_chunk[chunk_id] = output

    missing = sorted(set(chunks_by_id) - set(outputs_by_chunk))
    if missing:
        errors.append(f"missing agent outputs for chunks: {missing}")

    document_id = str(metadata.get("document_id", ""))
    canonical_by_id: dict[str, dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    for chunk_id in sorted(outputs_by_chunk):
        output = outputs_by_chunk[chunk_id]
        items = output.get("requirements", [])
        outcomes.append(
            {
                "chunk_id": chunk_id,
                "status": output.get("status"),
                "item_count": len(items) if isinstance(items, list) else 0,
            }
        )
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or _validate_agent_item(
                item, chunks_by_id[chunk_id], lines, chunk_id
            ):
                continue
            req_id = _requirement_id(document_id, item)
            canonical_by_id[req_id] = {
                "requirement_id": req_id,
                "condition": normalize_whitespace(item["condition"]),
                "required_behavior": normalize_whitespace(item["required_behavior"]),
                "forbidden_behavior": normalize_whitespace(item["forbidden_behavior"]),
                "error_behavior": normalize_whitespace(item["error_behavior"]),
                "check_type": item["check_type"],
                "why_checkable": normalize_whitespace(item["why_checkable"]),
                "eligibility": item["eligibility"],
                "evidence": {
                    "source_id": document_id,
                    "quote": item["quote"].strip(),
                    "line_start": item["line_start"],
                    "line_end": item["line_end"],
                    "verified": True,
                    "chunk_id": chunk_id,
                },
            }

    requirements = sorted(
        canonical_by_id.values(),
        key=lambda item: (
            item["evidence"]["line_start"],
            item["evidence"]["line_end"],
            item["requirement_id"],
        ),
    )
    artifact = {
        "schema_version": REQUIREMENTS_SCHEMA,
        "metadata": {
            **metadata,
            "processed_chunk_count": len(outputs_by_chunk),
            "requirement_count": len(requirements),
            "eligible_requirement_count": sum(
                item["eligibility"] == "eligible" for item in requirements
            ),
            "excluded_requirement_count": sum(
                item["eligibility"] == "exclude" for item in requirements
            ),
            "verified_requirement_count": len(requirements),
            "unverified_requirement_count": 0,
        },
        "chunk_outcomes": outcomes,
        "requirements": requirements,
    }
    if not errors:
        errors.extend(validate_artifacts(artifact, chunks_artifact, raw, lines))
    return artifact, errors


def _evidence_verified(
    requirement: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    lines: Sequence[str],
    document_id: str,
) -> bool:
    evidence = requirement.get("evidence")
    if not isinstance(evidence, dict):
        return False
    try:
        start = evidence["line_start"]
        end = evidence["line_end"]
        quote = evidence["quote"]
        chunk = chunks_by_id[evidence["chunk_id"]]
    except (KeyError, TypeError):
        return False
    if (
        evidence.get("source_id") != document_id
        or not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(quote, str)
        or start < 1
        or end < start
        or end > len(lines)
        or not (chunk["line_start"] <= start <= end <= chunk["line_end"])
    ):
        return False
    needle = normalize_whitespace(quote)
    return bool(needle) and needle in normalize_whitespace(
        "\n".join(lines[start - 1 : end])
    ) and needle in normalize_whitespace(str(chunk["text"]))


def validate_artifacts(
    requirements_artifact: dict[str, Any],
    chunks_artifact: dict[str, Any],
    raw: bytes,
    lines: Sequence[str],
) -> list[str]:
    errors = validate_chunks(chunks_artifact, raw, lines)
    if requirements_artifact.get("schema_version") != REQUIREMENTS_SCHEMA:
        errors.append("unexpected requirements schema_version")
    req_meta = requirements_artifact.get("metadata")
    chunk_meta = chunks_artifact.get("metadata")
    if not isinstance(req_meta, dict) or not isinstance(chunk_meta, dict):
        return errors + ["metadata must be an object in both artifacts"]
    if req_meta.get("source_sha256") != source_digest(raw):
        errors.append("requirements source_sha256 does not match source")
    if req_meta.get("document_id") != chunk_meta.get("document_id"):
        errors.append("document_id differs between artifacts")

    chunks = chunks_artifact.get("chunks", [])
    chunks_by_id = {
        chunk["chunk_id"]: chunk
        for chunk in chunks
        if isinstance(chunk, dict) and isinstance(chunk.get("chunk_id"), str)
    }
    outcomes = requirements_artifact.get("chunk_outcomes")
    if not isinstance(outcomes, list):
        errors.append("chunk_outcomes must be an array")
        outcomes = []
    outcome_ids: list[str] = []
    for index, outcome in enumerate(outcomes):
        if not isinstance(outcome, dict):
            errors.append(f"chunk_outcomes[{index}] must be an object")
            continue
        chunk_id = outcome.get("chunk_id")
        if not isinstance(chunk_id, str):
            errors.append(f"chunk_outcomes[{index}].chunk_id must be a string")
            continue
        outcome_ids.append(chunk_id)
        if outcome.get("status") != "complete":
            errors.append(f"{chunk_id}: chunk outcome is not complete")
    if len(outcome_ids) != len(set(outcome_ids)):
        errors.append("chunk_outcomes contains duplicate chunk IDs")
    if set(outcome_ids) != set(chunks_by_id):
        errors.append("chunk_outcomes does not cover every chunk exactly once")

    requirements = requirements_artifact.get("requirements")
    if not isinstance(requirements, list):
        return errors + ["requirements must be an array"]
    ids: set[str] = set()
    verified_count = 0
    eligible_count = 0
    for index, requirement in enumerate(requirements):
        label = f"requirements[{index}]"
        if not isinstance(requirement, dict):
            errors.append(f"{label} must be an object")
            continue
        legacy = sorted(LEGACY_REQUIREMENT_FIELDS & set(requirement))
        if legacy:
            errors.append(f"{label} contains removed fields: {legacy}")
        for field in (
            "requirement_id",
            "condition",
            "required_behavior",
            "forbidden_behavior",
            "error_behavior",
            "check_type",
            "why_checkable",
            "eligibility",
        ):
            if not isinstance(requirement.get(field), str):
                errors.append(f"{label}.{field} must be a string")
        req_id = requirement.get("requirement_id")
        if not isinstance(req_id, str) or not req_id:
            continue
        if req_id in ids:
            errors.append(f"duplicate requirement_id: {req_id}")
        ids.add(req_id)
        if requirement.get("check_type") not in CHECK_TYPES:
            errors.append(f"{req_id}: invalid check_type")
        if requirement.get("eligibility") not in ELIGIBILITY_VALUES:
            errors.append(f"{req_id}: invalid eligibility")
        elif requirement["eligibility"] == "eligible":
            eligible_count += 1
            if not any(
                isinstance(requirement.get(field), str)
                and requirement[field].strip()
                for field in (
                    "required_behavior",
                    "forbidden_behavior",
                    "error_behavior",
                )
            ):
                errors.append(f"{req_id}: eligible requirement has no behavior")
        if not isinstance(requirement.get("why_checkable"), str) or not requirement[
            "why_checkable"
        ].strip():
            errors.append(f"{req_id}: why_checkable must not be empty")

        actual_verified = _evidence_verified(
            requirement,
            chunks_by_id,
            lines,
            str(req_meta.get("document_id", "")),
        )
        declared_verified = requirement.get("evidence", {}).get("verified")
        if declared_verified is not actual_verified:
            errors.append(
                f"{req_id}: declared evidence.verified={declared_verified!r}, "
                f"but validation computed {actual_verified!r}"
            )
        if not actual_verified:
            errors.append(f"{req_id}: evidence is not verifiable")
        else:
            verified_count += 1
        evidence = requirement.get("evidence", {})
        identity_item = {
            "line_start": evidence.get("line_start"),
            "line_end": evidence.get("line_end"),
            "quote": evidence.get("quote"),
            "condition": requirement.get("condition"),
            "required_behavior": requirement.get("required_behavior"),
            "forbidden_behavior": requirement.get("forbidden_behavior"),
            "error_behavior": requirement.get("error_behavior"),
            "check_type": requirement.get("check_type"),
        }
        if all(isinstance(value, (str, int)) for value in identity_item.values()):
            expected_id = _requirement_id(str(req_meta.get("document_id", "")), identity_item)
            if req_id != expected_id:
                errors.append(f"{req_id}: requirement_id is not the stable computed ID")

    expected_counts = {
        "processed_chunk_count": len(outcome_ids),
        "requirement_count": len(requirements),
        "eligible_requirement_count": eligible_count,
        "excluded_requirement_count": len(requirements) - eligible_count,
        "verified_requirement_count": verified_count,
        "unverified_requirement_count": len(requirements) - verified_count,
    }
    for key, expected in expected_counts.items():
        if req_meta.get(key) != expected:
            errors.append(
                f"requirements metadata.{key} is {req_meta.get(key)!r}; expected {expected!r}"
            )
    return errors
