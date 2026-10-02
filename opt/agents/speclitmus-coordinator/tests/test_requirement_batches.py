from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from merge_static_triage_batches import MergeError, merge
from prepare_requirement_batches import ConfigError, prepare


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_artifact(source: Path, count: int = 5) -> dict[str, object]:
    source_bytes = source.read_bytes()
    requirements = []
    for index in range(1, count + 1):
        requirements.append(
            {
                "requirement_id": f"r{index}",
                "condition": "",
                "required_behavior": f"implement rule {index}",
                "forbidden_behavior": "",
                "error_behavior": "",
                "check_type": "structural_constraint",
                "why_checkable": "The behavior maps to a source-code check.",
                "eligibility": "eligible",
                "evidence": {
                    "source_id": "demo",
                    "chunk_id": "chunk-0001",
                    "quote": f"Rule {index}.",
                    "line_start": index,
                    "line_end": index,
                    "verified": True,
                },
            }
        )
    return {
        "schema_version": "speclitmus.requirements.v3",
        "metadata": {
            "document_id": "demo",
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "requirement_count": count,
            "eligible_requirement_count": count,
        },
        "requirements": requirements,
    }


def make_config(
    *,
    reuse: bool,
    artifact: str | None,
    start: int,
    end: int | None,
    batch_size: int,
) -> dict[str, object]:
    requirements: dict[str, object] = {"reuse": reuse}
    if artifact is not None:
        requirements["artifact"] = artifact
    return {
        "schema_version": "speclitmus.run-config.v1",
        "requirements": requirements,
        "static_triage": {
            "start": start,
            "end": end,
            "batch_size": batch_size,
        },
    }


class RequirementBatchTests(unittest.TestCase):
    def setUpWorkspace(self, root: Path) -> tuple[Path, Path, Path]:
        source = root / "standard.txt"
        source.write_text(
            "".join(f"Rule {index}.\n" for index in range(1, 6)),
            encoding="utf-8",
        )
        corpus = root / "corpus" / "requirements.json"
        write_json(corpus, make_artifact(source))
        return source, corpus, root / "run"

    def test_reuse_selects_inclusive_interval_and_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, corpus, run_dir = self.setUpWorkspace(root)
            config = root / "run-config.json"
            write_json(
                config,
                make_config(
                    reuse=True,
                    artifact="corpus/requirements.json",
                    start=2,
                    end=5,
                    batch_size=2,
                ),
            )

            result = prepare(config, source, run_dir)

            self.assertEqual(result["selected_requirement_count"], 4)
            self.assertEqual(result["batch_count"], 2)
            self.assertEqual(
                (run_dir / "requirements.json").read_bytes(),
                corpus.read_bytes(),
            )
            selection = json.loads(
                (run_dir / "requirement_selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [row["requirement_id"] for row in selection["requirements"]],
                ["r2", "r3", "r4", "r5"],
            )
            self.assertEqual(
                selection["batches"][0]["requirement_ids"], ["r2", "r3"]
            )
            self.assertEqual(
                selection["batches"][1]["requirement_ids"], ["r4", "r5"]
            )

    def test_fresh_mode_requires_run_local_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, corpus, run_dir = self.setUpWorkspace(root)
            config = root / "run-config.json"
            write_json(
                config,
                make_config(
                    reuse=False,
                    artifact=None,
                    start=1,
                    end=None,
                    batch_size=3,
                ),
            )

            with self.assertRaisesRegex(ConfigError, "fresh extraction is required"):
                prepare(config, source, run_dir)

            run_dir.mkdir()
            (run_dir / "requirements.json").write_bytes(corpus.read_bytes())
            result = prepare(config, source, run_dir)
            self.assertEqual(result["requirement_mode"], "fresh")
            self.assertEqual(result["selected_requirement_count"], 5)
            self.assertEqual(result["batch_count"], 2)

    def test_reuse_rejects_changed_standard_and_invalid_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _, run_dir = self.setUpWorkspace(root)
            config = root / "run-config.json"
            write_json(
                config,
                make_config(
                    reuse=True,
                    artifact="corpus/requirements.json",
                    start=1,
                    end=6,
                    batch_size=2,
                ),
            )
            with self.assertRaisesRegex(ConfigError, "exceeds eligible"):
                prepare(config, source, run_dir)

            source.write_text("Changed standard.\n", encoding="utf-8")
            write_json(
                config,
                make_config(
                    reuse=True,
                    artifact="corpus/requirements.json",
                    start=1,
                    end=1,
                    batch_size=1,
                ),
            )
            with self.assertRaisesRegex(ConfigError, "source_sha256"):
                prepare(config, source, run_dir)

    def test_reuse_normalizes_reviewed_delta_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, corpus, run_dir = self.setUpWorkspace(root)
            base = json.loads(corpus.read_text(encoding="utf-8"))
            delta = root / "corpus" / "requirements.delta.json"
            write_json(
                delta,
                {
                    "schema_version": "speclitmus.requirement-delta.v1",
                    "metadata": {
                        "newer_document_id": "demo",
                        "baseline_document_id": "older-demo",
                        "comparison_scope": "New or stricter requirements.",
                        "validation": {"status": "passed"},
                    },
                    "requirements": [
                        {"rfc9846_requirement": row}
                        for row in base["requirements"]
                    ],
                },
            )
            config = root / "run-config.json"
            write_json(
                config,
                make_config(
                    reuse=True,
                    artifact="corpus/requirements.delta.json",
                    start=1,
                    end=5,
                    batch_size=3,
                ),
            )

            result = prepare(config, source, run_dir)

            self.assertEqual(result["selected_requirement_count"], 5)
            snapshot = json.loads(
                (run_dir / "requirements.json").read_text(encoding="utf-8")
            )
            self.assertEqual(snapshot["schema_version"], "speclitmus.requirements.v3")
            self.assertEqual(snapshot["requirements"], base["requirements"])
            self.assertEqual(
                snapshot["metadata"]["delta_provenance"]["source_schema_version"],
                "speclitmus.requirement-delta.v1",
            )
            resolved = json.loads(
                (run_dir / "run_config.resolved.json").read_text(encoding="utf-8")
            )
            self.assertTrue(resolved["requirements"]["normalized"])

    def test_merge_requires_exact_batch_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _, run_dir = self.setUpWorkspace(root)
            config = root / "run-config.json"
            write_json(
                config,
                make_config(
                    reuse=True,
                    artifact="corpus/requirements.json",
                    start=1,
                    end=5,
                    batch_size=2,
                ),
            )
            prepare(config, source, run_dir)
            selection = json.loads(
                (run_dir / "requirement_selection.json").read_text(encoding="utf-8")
            )
            selection_sha = selection["metadata"]["selection_sha256"]
            for descriptor in selection["batches"]:
                records = [
                    {"requirement_id": requirement_id, "verdict": "no_issue"}
                    for requirement_id in descriptor["requirement_ids"]
                ]
                write_json(
                    run_dir / descriptor["output"],
                    {
                        "schema_version": "speclitmus.static-triage.v1",
                        "metadata": {
                            "batch_id": descriptor["batch_id"],
                            "selection_sha256": selection_sha,
                        },
                        "records": records,
                    },
                )

            result = merge(
                run_dir / "requirement_selection.json",
                run_dir / "static_triage.json",
            )
            self.assertEqual(result["records"], 5)
            merged = json.loads(
                (run_dir / "static_triage.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [row["requirement_id"] for row in merged["records"]],
                ["r1", "r2", "r3", "r4", "r5"],
            )

            bad_output = run_dir / selection["batches"][0]["output"]
            bad = json.loads(bad_output.read_text(encoding="utf-8"))
            bad["records"].pop()
            write_json(bad_output, bad)
            with self.assertRaisesRegex(MergeError, "coverage mismatch"):
                merge(
                    run_dir / "requirement_selection.json",
                    run_dir / "static_triage-bad.json",
                )


if __name__ == "__main__":
    unittest.main()
