# Pipeline Contract

## Run Layout

```text
<output>/
  run_manifest.json
  run_config.resolved.json
  chunks.json
  requirements.json
  requirement_selection.json
  static-triage-batches/
    batch-0001.input.json
    batch-0001.output.json
  static_triage.json
  candidates.json
  tasks/<task_id>/
    input.json
    code_check.json
    rounds/01/
      plan.json
      result.json
      stdout.log
      stderr.log
    final_record.json
  variant_audit.json
  variant_audit.md
  reports/*.md
  validation_summary.json
```

Workers own only their task directory. The coordinator owns all run-level
files.

## Requirement Gate

A requirement is eligible for static triage only when it contains:

- a stable requirement ID;
- condition plus at least one required, forbidden, or error behavior;
- check type, eligibility, and a concrete explanation of why it is checkable;
- exact source quotation and original line interval;
- `evidence.verified: true`.

Descriptive or security-rationale items may be retained for observation, but
must not silently become conformance requirements.

The extraction stage must include one completed outcome for every source chunk.
Requirement items do not carry `section`, `derivation_kind`, `subject`, or
`modality`; resolve a section from the referenced chunk only when presentation
requires it.

The coordinator may reuse a validated requirement artifact instead of invoking
the extractor. Reuse is valid only when the configured artifact still matches
the pinned standard digest and every quoted line interval. The coordinator
snapshots the artifact into the run directory and records its digest and reuse
mode in the manifest.

Static triage may cover a configured 1-based inclusive interval of the eligible
requirements. `requirement_selection.json` is the authoritative current-run
set. Its batches must preserve eligible order, be non-overlapping, and cover
the selected interval exactly once. See
[run-config.md](run-config.md).

## Candidate Gate

Run static requirement triage before candidate generation. See
[static-triage-contract.md](static-triage-contract.md). Every eligible
requirement selected for this run must have exactly one static triage record:

- static `no_issue` is a terminal requirement-level decision for the current
  run: it skips candidate generation, high-risk variants, and runtime
  validation for that requirement;
- `suspected_issue` and `issue_found` proceed to candidate generation;
- missing, duplicate, unknown, or under-evidenced triage records fail the
  stage.

Because static `no_issue` prevents later testing, it must include verified
standard evidence, reachable source excerpts, a concrete decision reason, empty
uncertainty fields, and `generation_action: "skip"`. If that evidence is not
available, classify the requirement as `suspected_issue`.

Every generated candidate must belong to the selected interval and preserve its
requirement ID and standard
evidence. Events and mutations must already be typed. Downstream stages validate
these fields; they must not infer execution-critical semantics from keyword
matching.

## Validation Loop

Each new round must state:

- `remaining_uncertainty`;
- competing hypotheses;
- the observation that distinguishes them;
- why the plan differs from previous attempts.

The coordinator permits at most five rounds.

## Final Record

Each record includes identifiers, title, standard check, code check, runtime
rounds, final verdict, confidence, decision reason, inconsistency reason, and
remaining uncertainty. Problem records should also carry a stable
`root_cause_key` whenever multiple candidates resolve to the same underlying
issue. Only `issue_found` and unresolved `suspected_issue` produce detailed
problem reports, and duplicate root causes must collapse to one Markdown
report.

## Markdown Report Sections

1. Title
2. Verdict
3. Problem Description
4. Standard Requirement
5. Relevant Source Code
6. Runtime Evidence
7. Inconsistency Reason
8. Remaining Uncertainty (required for suspected issues)
