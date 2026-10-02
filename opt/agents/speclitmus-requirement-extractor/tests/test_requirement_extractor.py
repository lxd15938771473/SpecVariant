from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from artifact_core import (  # noqa: E402
    CHUNK_EXTRACTION_SCHEMA,
    build_chunk_artifact,
    build_requirement_artifact,
    validate_artifacts,
    validate_chunks,
)


class RequirementExtractorTests(unittest.TestCase):
    def chunks(self, text: str, max_chars: int = 500, overlap: int = 1):
        raw = text.encode("utf-8")
        lines = text.splitlines()
        chunks = build_chunk_artifact(
            "test-standard", raw, lines, max_chars, overlap
        )
        self.assertEqual([], validate_chunks(chunks, raw, lines))
        return raw, lines, chunks

    @staticmethod
    def output(chunk_id: str, requirements: list[dict]) -> dict:
        return {
            "schema_version": CHUNK_EXTRACTION_SCHEMA,
            "chunk_id": chunk_id,
            "status": "complete",
            "requirements": requirements,
        }

    @staticmethod
    def item(
        quote: str,
        line_start: int,
        line_end: int,
        *,
        condition: str = "",
        required_behavior: str = "",
        forbidden_behavior: str = "",
        error_behavior: str = "",
        check_type: str = "structural_constraint",
        eligibility: str = "eligible",
    ) -> dict:
        return {
            "quote": quote,
            "line_start": line_start,
            "line_end": line_end,
            "condition": condition,
            "required_behavior": required_behavior,
            "forbidden_behavior": forbidden_behavior,
            "error_behavior": error_behavior,
            "check_type": check_type,
            "why_checkable": "A concrete parser or state-machine branch can be checked.",
            "eligibility": eligibility,
        }

    def test_agent_result_is_assembled_without_legacy_extractor_fields(self):
        text = """1. Rules

The length field is a 16-bit unsigned integer.
When the value is zero, the receiver aborts the connection.
"""
        raw, lines, chunks = self.chunks(text)
        chunk = chunks["chunks"][0]
        outputs = [
            self.output(
                chunk["chunk_id"],
                [
                    self.item(
                        "The length field is a 16-bit unsigned integer.",
                        3,
                        3,
                        required_behavior="encode and parse length as an unsigned 16-bit value",
                    ),
                    self.item(
                        "When the value is zero, the receiver aborts the connection.",
                        4,
                        4,
                        condition="the value is zero",
                        error_behavior="abort the connection",
                        check_type="error_handling",
                    ),
                ],
            )
        ]
        artifact, errors = build_requirement_artifact(
            chunks, outputs, raw, lines
        )
        self.assertEqual([], errors)
        self.assertEqual(2, len(artifact["requirements"]))
        for requirement in artifact["requirements"]:
            self.assertTrue(requirement["evidence"]["verified"])
            self.assertFalse(
                {"section", "derivation_kind", "subject", "modality"}
                & set(requirement)
            )
        self.assertEqual([], validate_artifacts(artifact, chunks, raw, lines))

    def test_no_keyword_extractor_runs_during_chunking(self):
        text = """1. Encoding

The value occupies exactly three octets and cannot be empty.
"""
        raw, lines, chunks = self.chunks(text)
        self.assertNotIn("requirements", chunks)
        self.assertEqual(1, chunks["metadata"]["chunk_count"])
        self.assertEqual([], validate_chunks(chunks, raw, lines))

    def test_rfc_table_of_contents_entries_are_not_section_headings(self):
        text = """Table of Contents

   1. Introduction ....................................................6
   2. Protocol Overview ..............................................10

   1. This numbered body item is not a section heading.

1. Introduction

The value occupies exactly three octets.
"""
        raw, lines, chunks = self.chunks(text)
        toc_chunks = [
            chunk
            for chunk in chunks["chunks"]
            if "................................" in chunk["text"]
        ]
        self.assertEqual(1, len(toc_chunks))
        self.assertEqual("Preamble", toc_chunks[0]["section"])
        self.assertTrue(
            any(
                chunk["section"] == "1. Introduction"
                for chunk in chunks["chunks"]
            )
        )
        self.assertEqual([], validate_chunks(chunks, raw, lines))

    def test_tab_delimited_table_of_contents_entries_are_not_section_headings(self):
        text = """Table of Contents

1. Introduction\t11
2. Protocol Overview\t14

Introduction

The value occupies exactly three octets.
"""
        raw, lines, chunks = self.chunks(text)
        self.assertEqual(1, chunks["metadata"]["chunk_count"])
        self.assertEqual("Preamble", chunks["chunks"][0]["section"])
        self.assertEqual([], validate_chunks(chunks, raw, lines))

    def test_publication_date_is_not_a_section_heading(self):
        text = """MQTT Version 5.0

07 March 2019

Introduction

The value occupies exactly three octets.
"""
        raw, lines, chunks = self.chunks(text)
        self.assertEqual("Preamble", chunks["chunks"][0]["section"])
        self.assertEqual([], validate_chunks(chunks, raw, lines))

    def test_malformed_agent_evidence_is_rejected(self):
        text = """1. Rule

The receiver rejects malformed input.
"""
        raw, lines, chunks = self.chunks(text)
        output = self.output(
            chunks["chunks"][0]["chunk_id"],
            [
                self.item(
                    "Fabricated quotation.",
                    3,
                    3,
                    error_behavior="reject malformed input",
                    check_type="error_handling",
                )
            ],
        )
        _artifact, errors = build_requirement_artifact(
            chunks, [output], raw, lines
        )
        self.assertTrue(any("quote does not match" in error for error in errors))

    def test_missing_chunk_outcome_is_rejected(self):
        text = """# One

Alpha occupies exactly one octet.

# Two

Beta occupies exactly two octets.
"""
        raw, lines, chunks = self.chunks(text, max_chars=128, overlap=0)
        self.assertGreaterEqual(len(chunks["chunks"]), 2)
        first = chunks["chunks"][0]
        _artifact, errors = build_requirement_artifact(
            chunks,
            [self.output(first["chunk_id"], [])],
            raw,
            lines,
        )
        self.assertTrue(any("missing agent outputs" in error for error in errors))

    def test_validator_recomputes_evidence_and_stable_id(self):
        text = """1. Rule

The receiver rejects malformed input.
"""
        raw, lines, chunks = self.chunks(text)
        output = self.output(
            chunks["chunks"][0]["chunk_id"],
            [
                self.item(
                    "The receiver rejects malformed input.",
                    3,
                    3,
                    error_behavior="reject malformed input",
                    check_type="error_handling",
                )
            ],
        )
        artifact, errors = build_requirement_artifact(
            chunks, [output], raw, lines
        )
        self.assertEqual([], errors)
        tampered = copy.deepcopy(artifact)
        tampered["requirements"][0]["evidence"]["quote"] = "Fabricated."
        validation_errors = validate_artifacts(tampered, chunks, raw, lines)
        self.assertTrue(any("computed False" in error for error in validation_errors))
        self.assertTrue(any("stable computed ID" in error for error in validation_errors))


if __name__ == "__main__":
    unittest.main()
