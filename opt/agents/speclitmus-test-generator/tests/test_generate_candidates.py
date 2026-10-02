from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_candidates.py"
SPEC = importlib.util.spec_from_file_location("generate_candidates", SCRIPT)
assert SPEC and SPEC.loader
generate_candidates = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_candidates
SPEC.loader.exec_module(generate_candidates)


def requirement(
    requirement_id: str,
    modality: str,
    *,
    subject: str,
    behavior: str,
    condition: str = "during the handshake",
    error_behavior: str = "",
) -> dict:
    return {
        "requirement_id": requirement_id,
        "section": "RFC TEST Section 1",
        "modality": modality,
        "subject": subject,
        "condition": condition,
        "required_behavior": behavior,
        "exceptions": [],
        "error_behavior": error_behavior,
        "evidence": {
            "source_id": "rfc-test.txt",
            "quote": f"The endpoint {modality} {behavior}.",
            "line_start": 10,
            "line_end": 10,
            "verified": True,
            "sha256": "abc123",
        },
    }


class GenerateCandidatesTests(unittest.TestCase):
    def test_v3_agent_requirement_needs_no_legacy_extractor_fields(self) -> None:
        evidence = {
            "source_id": "rfc-test",
            "quote": "The endpoint does not process the token twice.",
            "line_start": 20,
            "line_end": 20,
            "verified": True,
            "chunk_id": "chunk-0002",
        }
        item = {
            "requirement_id": "req-v3",
            "condition": "the token was already processed",
            "required_behavior": "",
            "forbidden_behavior": "process the same token twice",
            "error_behavior": "",
            "check_type": "state_machine_invariant",
            "why_checkable": "The duplicate-token state branch can be tested.",
            "eligibility": "eligible",
            "evidence": evidence,
        }
        document = generate_candidates.generate_document(
            {
                "schema_version": "speclitmus.requirements.v3",
                "requirements": [item],
            },
            max_variants=1,
        )
        base = next(
            candidate
            for candidate in document["candidates"]
            if candidate["risk_class"] == "baseline"
        )
        self.assertEqual("forbid", base["oracle"]["normative_direction"])
        self.assertEqual(
            "process the same token twice", base["oracle"]["forbidden_behavior"]
        )
        self.assertEqual(evidence, base["standard_evidence"])

    def test_requirement_selection_schema_is_accepted(self) -> None:
        item = {
            "requirement_id": "req-selected",
            "condition": "",
            "required_behavior": "reject an invalid token",
            "forbidden_behavior": "",
            "error_behavior": "terminate processing",
            "check_type": "error_handling",
            "why_checkable": "The rejection branch is directly testable.",
            "eligibility": "eligible",
            "evidence": {
                "source_id": "rfc-test",
                "quote": "An endpoint rejects an invalid token.",
                "line_start": 30,
                "line_end": 30,
                "verified": True,
                "chunk_id": "chunk-0003",
            },
        }
        document = generate_candidates.generate_document(
            {
                "schema_version": "speclitmus.requirement-selection.v1",
                "requirements": [item],
            },
            max_variants=1,
        )
        self.assertEqual(document["generation_summary"]["requirements_consumed"], 1)
        self.assertTrue(
            any(
                candidate["requirement_id"] == "req-selected"
                for candidate in document["candidates"]
            )
        )

    def test_normal_must_and_must_not(self) -> None:
        must = requirement(
            "REQ-001",
            "MUST",
            subject="length field",
            behavior="include a length field with a value no greater than 255 bytes",
        )
        must_not = requirement(
            "REQ-002",
            "MUST NOT",
            subject="reserved extension type",
            behavior="accept an unknown reserved extension type",
            error_behavior="abort with illegal_parameter",
        )
        document = generate_candidates.generate_document(
            {"requirements": [must, must_not]}, max_variants=3
        )

        self.assertEqual(document["generation_summary"]["requirements_consumed"], 2)
        self.assertEqual(document["generation_summary"]["base_tests"], 2)
        bases = [item for item in document["candidates"] if item["kind"] == "base"]
        self.assertEqual(len(bases), 2)
        self.assertEqual(bases[0]["oracle"]["normative_direction"], "require")
        self.assertEqual(bases[1]["oracle"]["normative_direction"], "forbid")
        self.assertEqual(bases[0]["standard_evidence"], must["evidence"])
        self.assertIsNot(bases[0]["standard_evidence"], must["evidence"])
        boundary = next(
            item
            for item in document["candidates"]
            if item["requirement_id"] == "REQ-001"
            and item["risk_class"] == "boundary"
        )
        boundary_mutation = next(
            event for event in boundary["events"] if event["type"] == "mutate"
        )
        self.assertEqual(
            boundary_mutation["parameters"]["boundary_hints"]["upper"],
            {
                "value": 255,
                "inclusive": True,
                "nearest_valid": 255,
                "nearest_outside": 256,
            },
        )
        for candidate in document["candidates"]:
            self.assertTrue(candidate["events"])
            self.assertTrue(
                all(
                    {"seq", "type", "actor", "connection", "action", "parameters"}
                    <= event.keys()
                    for event in candidate["events"]
                )
            )

    def test_rejects_unverified_evidence(self) -> None:
        unverified = requirement(
            "REQ-BAD",
            "MUST",
            subject="required field",
            behavior="include the required field",
        )
        unverified["evidence"]["verified"] = False

        with self.assertRaisesRegex(
            generate_candidates.InputError, r"evidence\.verified must be true"
        ):
            generate_candidates.generate_document([unverified])

    def test_dedup_and_top_k_are_stable(self) -> None:
        noisy = requirement(
            "REQ-STABLE",
            "MUST",
            subject="ticket nonce message field",
            behavior=(
                "include exactly one nonce field of maximum length 255 before "
                "resumption on a fresh connection; duplicate duplicate replay replay"
            ),
            error_behavior="reject with an error alert",
        )
        first = generate_candidates.generate_document([noisy], max_variants=3)
        second = generate_candidates.generate_document(
            {"requirements": [copy.deepcopy(noisy)]}, max_variants=3
        )

        self.assertEqual(first, second)
        variants = [
            candidate
            for candidate in first["candidates"]
            if candidate["kind"] == "variant"
        ]
        self.assertEqual(len(variants), 3)
        self.assertEqual(
            len({candidate["risk_class"] for candidate in variants}), len(variants)
        )
        self.assertEqual(
            [candidate["value_score"] for candidate in variants],
            sorted(
                [candidate["value_score"] for candidate in variants], reverse=True
            ),
        )
        self.assertEqual(len({item["candidate_id"] for item in first["candidates"]}), 4)

    def test_duplicate_requirement_id_is_rejected(self) -> None:
        item = requirement(
            "REQ-DUP",
            "MUST",
            subject="field",
            behavior="include a field",
        )
        with self.assertRaisesRegex(
            generate_candidates.InputError, "duplicate requirement_id"
        ):
            generate_candidates.generate_document([item, copy.deepcopy(item)])

    def test_zero_variants_still_emits_one_base(self) -> None:
        item = requirement(
            "REQ-BASE",
            "SHOULD",
            subject="diagnostic event",
            behavior="emit a diagnostic event",
            condition="",
        )
        result = generate_candidates.generate_document([item], max_variants=0)
        self.assertEqual(result["generation_summary"]["base_tests"], 1)
        self.assertEqual(result["generation_summary"]["variants"], 0)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["expected_strength"], "recommended")

    def test_static_triage_generates_only_suspects_and_issues(self) -> None:
        ok = requirement(
            "REQ-OK",
            "MUST",
            subject="stable field",
            behavior="include a stable field",
        )
        suspect = requirement(
            "REQ-SUSPECT",
            "MUST",
            subject="length field",
            behavior="reject values greater than 10",
            error_behavior="abort with protocol_violation",
        )
        issue = requirement(
            "REQ-ISSUE",
            "MUST NOT",
            subject="reserved value",
            behavior="accept a reserved value",
            error_behavior="abort with illegal_parameter",
        )
        triage = {
            "schema_version": "speclitmus.static-triage.v1",
            "records": [
                {"requirement_id": "REQ-OK", "verdict": "no_issue"},
                {"requirement_id": "REQ-SUSPECT", "verdict": "suspected_issue"},
                {"requirement_id": "REQ-ISSUE", "verdict": "issue_found"},
            ],
        }

        result = generate_candidates.generate_document(
            {"requirements": [ok, suspect, issue]},
            max_variants=2,
            static_triage=triage,
        )

        generated_ids = {item["requirement_id"] for item in result["candidates"]}
        self.assertEqual(generated_ids, {"REQ-SUSPECT", "REQ-ISSUE"})
        self.assertEqual(result["generation_summary"]["base_tests"], 2)
        self.assertEqual(result["generation_summary"]["static_triage"]["verdict_counts"], {
            "issue_found": 1,
            "no_issue": 1,
            "suspected_issue": 1,
        })
        self.assertEqual(
            result["generation_summary"]["static_triage"][
                "skipped_requirement_ids"
            ],
            ["REQ-OK"],
        )

    def test_static_triage_must_cover_every_requirement(self) -> None:
        item = requirement(
            "REQ-MISSING-TRIAGE",
            "MUST",
            subject="field",
            behavior="include a field",
        )
        with self.assertRaisesRegex(
            generate_candidates.InputError, "static triage missing requirement IDs"
        ):
            generate_candidates.generate_document(
                [item],
                static_triage={"records": []},
            )

    def test_cli_round_trip_and_overwrite_guard(self) -> None:
        item = requirement(
            "REQ-CLI",
            "MUST",
            subject="version field",
            behavior="include a supported version value",
        )
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            source = root / "requirements.json"
            output = root / "candidates.json"
            source.write_text(
                json.dumps({"requirements": [item]}, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(
                generate_candidates.main(
                    [
                        "--requirements",
                        str(source),
                        "--out",
                        str(output),
                        "--max-variants",
                        "2",
                    ]
                ),
                0,
            )
            emitted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(emitted["generation_summary"]["base_tests"], 1)
            original = output.read_bytes()

            self.assertEqual(
                generate_candidates.main(
                    [
                        "--requirements",
                        str(source),
                        "--out",
                        str(output),
                        "--max-variants",
                        "1",
                    ]
                ),
                2,
            )
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(
                generate_candidates.main(
                    [
                        "--requirements",
                        str(source),
                        "--out",
                        str(output),
                        "--max-variants",
                        "1",
                        "--overwrite",
                    ]
                ),
                0,
            )
            overwritten = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(overwritten["generation_summary"]["variants"], 1)


if __name__ == "__main__":
    unittest.main()
