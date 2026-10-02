# SpecLitmus Multi-Agent Audit Pipeline

This directory is the isolated, optimized workflow requested for the project.
Codex is the coordinator; deterministic scripts enforce evidence and output
contracts, while bounded Agents perform the judgment-heavy work.

## Agents

1. `speclitmus-requirement-extractor` splits the pinned standard and extracts
   atomic normative requirements with exact line-addressable evidence.
2. `speclitmus-test-generator` creates one baseline plus a bounded set of
   high-risk typed variants for each verified requirement.
3. `speclitmus-implementation-validator` inspects the implementation and safely
   runs positive controls and minimal reproducers.
4. `speclitmus-verdict-reporter` accepts only `no_issue`, `suspected_issue`, or
   `issue_found` and writes the audit ledger and detailed Markdown reports.
5. `speclitmus-coordinator` is the Codex-facing control skill. It dispatches the
   other Agents, permits at most five information-gaining validation rounds,
   and applies the final completeness gate.

The canonical contracts are in each Agent's `references` directory. Runtime
commands are typed argument arrays and execute with `shell=False`. A harness or
adapter failure is evidence about the test setup, not evidence of a protocol
defect.

## Requirement Reuse And Static Batches

Create a `speclitmus.run-config.v1` file to choose fresh extraction or a
reusable requirement corpus and to bound the current static-triage interval:

```json
{
  "schema_version": "speclitmus.run-config.v1",
  "requirements": {
    "reuse": true,
    "artifact": "D:/corpora/rfc8446/requirements.json"
  },
  "static_triage": {
    "start": 1,
    "end": 500,
    "batch_size": 100
  }
}
```

`start` and `end` are 1-based and inclusive over eligible requirements.
`end: null` means the final eligible requirement. See
`agents/speclitmus-coordinator/references/run-config.md` for preparation,
batch-output, merge, and manifest requirements.

## Outputs

Confirmed and unresolved suspected problems are written as Markdown under
`reports/`. Each report contains a title, verdict, problem description, exact
standard text, relevant source code, runtime evidence, inconsistency reason,
and—when needed—remaining uncertainty. `variant_audit.json` and
`variant_audit.md` retain the complete three-way decision ledger.

## Verification

Run every Agent's self-tests:

```powershell
python opt/scripts/run_all_self_tests.py
```

Run the complete demo pipeline, including an independently revalidated standard
quotation, a real minimal reproducer, final adjudication, Markdown generation,
and coordinator validation:

```powershell
python opt/scripts/run_total_test.py
```

Machine-readable evidence is stored in `opt/test-results/`.
