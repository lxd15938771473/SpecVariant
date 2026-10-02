---
name: speclitmus-verdict-reporter
description: Validate SpecLitmus standard, code, and runtime evidence; enforce a three-way verdict; and render a complete audit ledger plus per-problem Markdown reports. Use after implementation validation to adjudicate no_issue, suspected_issue, or issue_found records and publish reproducible findings.
---

# SpecLitmus Verdict Reporter

Adjudicate evidence, not labels inherited from an earlier Agent.

## Workflow

1. Re-read the exact standard condition, implementation excerpts, positive
   controls, mutated runs, and any remaining uncertainty.
2. Choose exactly one verdict:

   - `no_issue`: evidence is sufficient and behavior is consistent.
   - `issue_found`: evidence establishes an implementation/standard
     inconsistency.
   - `suspected_issue`: a concrete inconsistency remains plausible but evidence
     cannot distinguish the remaining hypotheses.

3. Explain why the combined evidence supports the verdict. A failed or timed-out
   positive control cannot establish an issue. Do not convert an environment,
   harness, or invalid-candidate failure into an implementation defect.
4. For suspected issues, state the unresolved question. For confirmed or
   suspected issues, state the inconsistency reason and root behavioral
   difference.
5. Assign the same non-empty `root_cause_key` to records that are duplicate
   manifestations of the same underlying issue. Duplicate records remain in the
   audit ledger, but only one Markdown problem report is emitted for each
   unique root cause.
6. Produce records following
   [audit-record-contract.md](references/audit-record-contract.md), then run:

   ```text
   python scripts/render_reports.py --input <audit-records.json> --output-dir <run-output>
   ```

The renderer validates every record before writing. It creates the complete
`variant_audit.json` and `variant_audit.md` ledgers, plus one detailed Markdown
file under `reports/` for every unique root cause among `issue_found` and
unresolved `suspected_issue` records. Do not emit a detailed problem report for
`no_issue`, and do not emit duplicate reports for records that share a
`root_cause_key`.
