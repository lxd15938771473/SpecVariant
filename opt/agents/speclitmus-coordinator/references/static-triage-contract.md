# Static Requirement Triage Contract

Schema version: `speclitmus.static-triage.v1`

Static triage happens after requirement extraction/review and before candidate
generation. Its purpose is to perform a careful requirement-by-requirement code
audit before spending runtime budget.

The input is one `speclitmus.static-triage-batch.v1` artifact produced from the
configured requirement selection. A worker must process every requirement in
that batch and write the exact output path named by
`metadata.expected_output`. Batch size is only a task bound; it does not permit
sampling.

Static `no_issue` is terminal for that requirement in this run. It means the
coordinator will not generate variants, run reproducers, or revisit the
requirement unless a later artifact contradicts the static evidence. Therefore
`no_issue` requires stronger evidence than a casual source anchor: the validator
must prove the relevant reachable implementation path is consistent with the
standard. If that cannot be shown, use `suspected_issue`.

## Document Shape

```json
{
  "schema_version": "speclitmus.static-triage.v1",
  "metadata": {
    "requirements_sha256": "<sha256 of the full requirements artifact>",
    "selection_sha256": "<sha256 of the selected requirement ID sequence>",
    "batch_id": "batch-0001",
    "target": "wolfSSL",
    "target_revision": "<git hash or not-a-git-repository>",
    "records": 396
  },
  "records": []
}
```

Each record has:

| Field | Contract |
|---|---|
| `requirement_id` | Exact upstream requirement ID. Every requirement in the assigned batch has exactly one record. |
| `verdict` | Exactly `no_issue`, `suspected_issue`, or `issue_found`. |
| `confidence` | `low`, `medium`, or `high`. |
| `standard_evidence` | Exact copy of the requirement evidence, including quote and line interval. |
| `code_check` | Static implementation evidence with real `path:line` excerpts or an explicit absence trace. |
| `decision_reason` | Why static evidence is enough to skip or why dynamic tests are needed. |
| `inconsistency_reason` | Required for `suspected_issue` and `issue_found`; empty for static `no_issue`. |
| `remaining_uncertainty` | Required for `suspected_issue`; empty when evidence is settled. |
| `generation_action` | `skip` for static `no_issue`; `generate_variants` for `suspected_issue` and `issue_found`. |

`code_check.excerpts[]` uses the same shape as final audit records:

```json
{
  "path": "src/tls13.c",
  "line_start": 42,
  "line_end": 51,
  "code": "if (...) { ... }",
  "explanation": "Why this reachable code matters."
}
```

When the code path appears absent, record the searched files, symbols, and
negative search commands in `code_check.absence_evidence`; do not fabricate an
excerpt.

## Gate Semantics

- `no_issue`: static code inspection found a reachable parse, validation,
  generation, state-transition, or error-mapping path consistent with the
  requirement. The coordinator skips all later candidate generation and runtime
  validation for this requirement in the current run.
- `suspected_issue`: a concrete mismatch remains plausible, or code evidence is
  incomplete. The coordinator sends the requirement to test generation.
- `issue_found`: static evidence already identifies an apparent mismatch. The
  coordinator still sends the requirement to test generation for a baseline and
  high-risk reproducer unless runtime testing is concretely infeasible.

## Strict `no_issue` Criteria

A static `no_issue` record is valid only when all of these are true:

- `standard_evidence` preserves the exact quote and line interval and has
  `verified: true`.
- `code_check.summary` explains the mapped implementation behavior.
- `code_check.excerpts[]` contains at least one real source excerpt with
  `path`, `line_start`, `line_end`, `code`, and `explanation`.
- The excerpts identify the reachable parse, validation, generation,
  state-transition, or error path that satisfies the requirement, or explain why
  the requirement is not applicable to this implementation mode.
- Search and configuration assumptions are recorded when they affect the
  decision.
- `inconsistency_reason` and `remaining_uncertainty` are empty.
- `generation_action` is exactly `skip`.

Evidence gaps are never `no_issue`. If the path is only partially traced, the
build/configuration is uncertain, the relevant code appears absent, or a
high-risk variant could still distinguish plausible behaviors, the verdict is
`suspected_issue`.

The test generator consumes this artifact with:

```powershell
python scripts/generate_candidates.py `
  --requirements <requirements.json> `
  --static-triage <static_triage.json> `
  --out <candidates.json> `
  --max-variants 3
```

It must reject duplicate, missing, unknown, or under-evidenced triage records.
It only generates candidates for `suspected_issue` and `issue_found` records.
