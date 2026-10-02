# Requirement Artifact Data Contract

Schema version: `speclitmus.requirements.v3`

## Requirements document

The assembler writes one JSON object after every chunk has been reviewed by an
agent:

```json
{
  "schema_version": "speclitmus.requirements.v3",
  "metadata": {
    "document_id": "rfc8446",
    "source_sha256": "<64 lowercase hex characters>",
    "source_line_count": 5529,
    "max_chars": 6000,
    "overlap_paragraphs": 1,
    "chunk_count": 54,
    "processed_chunk_count": 54,
    "requirement_count": 312,
    "eligible_requirement_count": 300,
    "excluded_requirement_count": 12,
    "verified_requirement_count": 312,
    "unverified_requirement_count": 0,
    "covered_nonblank_line_count": 4870,
    "total_nonblank_line_count": 4870,
    "coverage_complete": true
  },
  "chunk_outcomes": [
    {"chunk_id": "chunk-0001", "status": "complete", "item_count": 6}
  ],
  "requirements": []
}
```

Metadata contains no clock time or machine-specific absolute path, so identical
input bytes and options produce identical output.

Each item in `requirements` has:

| Field | Type | Meaning |
|---|---|---|
| `requirement_id` | string | Stable `req-` ID derived from the document, evidence range, and atomic occurrence |
| `condition` | string | Leading or inline applicability condition, without being discarded from the evidence |
| `required_behavior` | string | Behavior the implementation is required to perform |
| `forbidden_behavior` | string | Forbidden behavior or invalid action constrained by the item; empty when not applicable |
| `error_behavior` | string | Required behavior when it explicitly rejects, aborts, alerts, fails, or returns an error |
| `check_type` | enum | `explicit_normative`, `structural_constraint`, `state_machine_invariant`, `security_consistency`, `error_handling`, or `derived_constraint` |
| `why_checkable` | string | Brief reason the item maps to a concrete implementation check |
| `eligibility` | enum | `eligible` or `exclude` |
| `evidence` | object | Exact source evidence described below |

Evidence has:

```json
{
  "source_id": "rfc8446",
  "quote": "The server MUST reject the message.",
  "line_start": 120,
  "line_end": 121,
  "verified": true,
  "chunk_id": "chunk-0007"
}
```

Line numbers are one-based and inclusive. `quote` preserves the source sentence's
characters and line breaks, except whitespace outside the sentence is trimmed.
Verification collapses runs of whitespace in both the quote and the declared
source line slice, then requires exact containment. It also checks the chunk
range. `source_id` equals `metadata.document_id` and is never blank. `verified`
is derived; extraction agents and consumers must not author or override it.

`check_type`, `why_checkable`, and `eligibility` are required for every item.
`eligibility` records whether the extracted item is fit for downstream static
triage after review. Items with incomplete evidence or unclear constrained
behavior must be marked `exclude`.

Canonical requirement items do not contain `section`, `derivation_kind`,
`subject`, or `modality`. A consumer that needs a section label can resolve it
from `evidence.chunk_id`; it must not require the extraction agent to repeat it.

## Per-chunk agent result

Each extraction worker writes exactly one JSON object:

```json
{
  "schema_version": "speclitmus.chunk-extraction.v1",
  "chunk_id": "chunk-0007",
  "status": "complete",
  "requirements": [
    {
      "quote": "The server rejects values greater than 10.",
      "line_start": 120,
      "line_end": 121,
      "condition": "the received value is greater than 10",
      "required_behavior": "reject the value",
      "forbidden_behavior": "",
      "error_behavior": "",
      "check_type": "structural_constraint",
      "why_checkable": "The parser's upper-bound branch can be inspected and tested.",
      "eligibility": "eligible"
    }
  ]
}
```

`status` must be `complete`. An agent that finds no checkable constraints still
writes a completed result with an empty `requirements` array. Agent items must
not contain `requirement_id`, `evidence.verified`, `section`,
`derivation_kind`, `subject`, or `modality`.

## Chunk document

`chunk_standard.py` writes:

```json
{
  "schema_version": "speclitmus.chunks.v1",
  "metadata": {
    "document_id": "rfc8446",
    "source_sha256": "<digest>",
    "source_line_count": 5529,
    "max_chars": 6000,
    "overlap_paragraphs": 1,
    "chunk_count": 54,
    "covered_nonblank_line_count": 4870,
    "total_nonblank_line_count": 4870,
    "coverage_complete": true
  },
  "chunks": [
    {
      "chunk_id": "chunk-0001",
      "section": "1. Introduction",
      "line_start": 42,
      "line_end": 106,
      "text": "...",
      "content_line_ranges": [[42, 106]],
      "overlap_line_ranges": []
    }
  ]
}
```

`content_line_ranges` identifies newly assigned source paragraphs.
`overlap_line_ranges` identifies paragraphs repeated from the preceding chunk.
Both reference original lines. Chunks never overlap section boundaries.

## Failure contract

The mechanical CLIs write diagnostics to stderr and return:

- `0`: chunking, assembly, or validation succeeded;
- `1`: artifact validation failed;
- `2`: invalid arguments, malformed JSON, unreadable input, or refused overwrite.

An output file is written atomically only after the full artifact validates.
