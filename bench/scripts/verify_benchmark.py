from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main() -> None:
    cases = [json.loads(line) for line in (ROOT / "benchmark.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    baselines = json.loads((ROOT / "baselines.json").read_text(encoding="utf-8"))
    summary = json.loads((ROOT / "validation-summary.json").read_text(encoding="utf-8"))

    if len(cases) != 214:
        fail(f"expected 214 cases, found {len(cases)}")
    if len(baselines) != 6:
        fail(f"expected 6 baselines, found {len(baselines)}")

    baseline_by_repo = {item["repository"]: item["sha"] for item in baselines}
    if len(baseline_by_repo) != 6:
        fail("baseline repositories are not unique")

    ids = set()
    upstream = set()
    observed_shas: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        if case["id"] in ids:
            fail(f"duplicate case id {case['id']}")
        ids.add(case["id"])

        source_key = (case["repository"], case["upstream_record_number"])
        if source_key in upstream:
            fail(f"duplicate upstream record {source_key}")
        upstream.add(source_key)

        repo = case["repository"]
        sha = case["baseline"]["sha"]
        observed_shas[repo].add(sha)
        if baseline_by_repo.get(repo) != sha:
            fail(f"baseline mismatch for {case['id']}")
        if not case["baseline_evidence"]:
            fail(f"missing baseline evidence for {case['id']}")
        if not case["accepted_fix"]["sha"] or not case["accepted_fix"]["url"]:
            fail(f"missing accepted fix for {case['id']}")
        if case["runtime_verified"] is not False:
            fail(f"candidate unexpectedly marked runtime verified: {case['id']}")

        unsigned = {key: value for key, value in case.items() if key != "record_sha256"}
        digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if digest != case["record_sha256"]:
            fail(f"record hash mismatch for {case['id']}")

    if any(len(shas) != 1 for shas in observed_shas.values()):
        fail("a repository uses more than one baseline commit")
    if set(observed_shas) != set(baseline_by_repo):
        fail("case repositories and baseline repositories differ")

    report_files = list((ROOT / "reports").rglob("SVB-*.md"))
    if len(report_files) != 214:
        fail(f"expected 214 Markdown reports, found {len(report_files)}")

    distribution = dict(Counter(case["repository"] for case in cases))
    if distribution != summary["distribution"]:
        fail("validation-summary distribution is stale")
    if summary["case_count"] != 214 or summary["runtime_verified"] != 0:
        fail("validation-summary counts are inconsistent")

    print("OK: 214 unique cases, 6 single-commit baselines, 214 reports, hashes valid")
    for repo, count in distribution.items():
        print(f"  {repo}: {count} @ {baseline_by_repo[repo]}")


if __name__ == "__main__":
    main()
