---
name: speclitmus-coordinator
description: Coordinate the complete SpecLitmus evidence pipeline from a protocol standard to verified atomic requirements, high-risk structured tests, implementation-native runtime validation, iterative three-way adjudication, and Markdown issue reports. Use when Codex must supervise a multi-agent protocol audit, resume a run, enforce a maximum validation round count, or perform the final completeness check.
---

# SpecLitmus Coordinator

Treat Codex as the coordinator. Delegate bounded tasks to the four sibling
SpecLitmus skills and keep deterministic operations in scripts.

Read [references/pipeline-contract.md](references/pipeline-contract.md) before
creating tasks or merging results.
Read [references/run-config.md](references/run-config.md) before deciding
whether to extract requirements or reuse a verified corpus.

## Inputs

Require:

- protocol name and pinned standard file;
- target implementation name and source tree;
- output directory;
- a `speclitmus.run-config.v1` file containing requirement reuse and static
  triage selection settings;
- optional candidate limit and `max_rounds` (default and maximum: `5`).

Record the standard SHA-256, target revision when available, commands, and
configuration in `run_manifest.json`.

## Workflow

1. Resolve requirement mode from the run configuration. If `reuse` is false,
   invoke `$speclitmus-requirement-extractor` and write the fresh artifact to
   `<output>/requirements.json`. If `reuse` is true, skip extraction and use the
   configured artifact.
2. Run `scripts/prepare_requirement_batches.py`. Reject reused requirements
   unless their digest and every evidence quote still match the pinned
   standard. Treat `start` and `end` as a 1-based inclusive interval over all
   eligible requirements and create deterministic batches of at most
   `batch_size`.
3. Invoke `$speclitmus-implementation-validator` in static requirement triage
   mode once per batch. Require one triage record per selected requirement,
   with verified standard evidence and real reachable code references or
   explicit absence evidence. Treat static `no_issue` as a terminal
   requirement-level decision for the current run: it skips all later
   candidate generation and runtime validation, so evidence gaps must become
   `suspected_issue`.
4. Run `scripts/merge_static_triage_batches.py`. Do not continue if a selected
   requirement is missing, duplicated, assigned to the wrong batch, or returned
   with an invalid verdict.
5. Invoke `$speclitmus-test-generator` with
   `requirement_selection.json` and `static_triage.json`.
   Generate one base test and a bounded set of high-value structured variants
   only for requirements whose static triage verdict is `suspected_issue` or
   `issue_found`.
6. Invoke `$speclitmus-implementation-validator` for each candidate or closely
   related family. Make the worker inspect code before creating and running a
   minimal reproducer.
7. Invoke `$speclitmus-verdict-reporter` with the standard, code, and runtime
   evidence. Accept only `no_issue`, `suspected_issue`, or `issue_found`.
8. If the result is `suspected_issue` and fewer than `max_rounds` attempts have
   run, dispatch a new validation task whose plan targets the explicit remaining
   uncertainty. Reject duplicate plans or plans with no expected information
   gain.
9. Stop early when evidence settles the result, testing is concretely
   infeasible, no new discriminating plan exists, or five rounds have run.
10. Aggregate `issue_found` and unresolved `suspected_issue` records by root
   cause. Assign a stable `root_cause_key` to duplicate manifestations of the
   same issue, discard duplicate problem-report entries, and generate exactly
   one Markdown problem report per unique root cause while keeping the full
   audit ledger separately.

## Coordination Rules

- Give every task a stable `run_id`, `task_id`, input digest, output directory,
  and attempt number.
- Never reinterpret `start` or `end` as requirement IDs or zero-based offsets.
  Preserve upstream eligible order and process every generated static batch.
- Let workers write only to their task directories. Let one aggregator write
  canonical run-level files.
- Treat a heartbeat as liveness, never completion. Require a terminal result
  with artifacts and test notes.
- Run independent standard/code checks before exposing either conclusion to the
  final judge when practical.
- Do not call adapter support, harness failure, or missing tests an
  implementation defect.
- Do not force a verdict at the round limit; retain `suspected_issue` with the
  remaining uncertainty and concrete blocker.

## Completion Gate

Before finishing, run the bundled run validator and prove:

- selected requirement and candidate IDs are unique and covered exactly once;
- requirement reuse mode, source digest, selected interval, batch size, and
  batch count match `run_manifest.json`;
- every generator-eligible requirement has one static triage record;
- every static `suspected_issue` or `issue_found` requirement has generated
  candidates, and every static `no_issue` requirement is recorded as skipped
  with strict standard and source evidence;
- all evidence quotations match their source lines;
- every candidate has one final three-way verdict;
- all runtime commands, exit codes, and logs referenced by a verdict exist;
- every code citation resolves to an existing file and valid line;
- each detailed problem report is Markdown and contains the required sections;
- all worker self-tests and the end-to-end test passed.
