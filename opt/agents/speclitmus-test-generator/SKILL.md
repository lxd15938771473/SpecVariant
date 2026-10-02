---
name: speclitmus-test-generator
description: Generate a direct base test and a bounded set of high-value, typed protocol variants from verified atomic normative requirements while preserving standard provenance. Use when Codex must turn SpecLitmus requirement JSON into deterministic candidate JSON for implementation inspection and minimal runtime reproduction, reject unverified evidence, or prevent downstream keyword-based IR rewriting.
---

# SpecLitmus Test Generator

Generate execution-independent test intent. Do not write implementation-specific
harness code in this stage.

Read [references/data-contract.md](references/data-contract.md) before consuming
or emitting pipeline artifacts.

## Workflow

1. Verify that every eligible input item is atomic, uniquely identified, maps
   to an implementation check, and carries `evidence.verified: true`. Ignore
   only items explicitly marked `eligibility: exclude`; stop on malformed
   evidence or an unknown eligibility value.
2. Run:

   ```powershell
   python scripts/generate_candidates.py `
     --requirements <requirement_selection.json> `
     --static-triage <static_triage.json> `
     --out <candidates.json> `
     --max-variants 3
   ```

   Omit `--static-triage` only for legacy/full-generation runs. Add
   `--overwrite` only when replacing the named output is intentional.
3. When `--static-triage` is supplied, reject missing, duplicate, or unknown
   triage records relative to the selected input. Generate candidates only for
   requirements whose static
   verdict is `suspected_issue` or `issue_found`; skip static `no_issue`
   requirements without converting them into final audit verdicts.
4. Require exactly one `base` candidate per generated requirement. Treat it as
   the direct normative probe, not as evidence that the implementation conforms.
5. Keep at most `--max-variants` variants per requirement. In normal runs,
   generate only the highest-value 2-3 variants for each generated
   requirement. Prefer missing, duplicate, boundary, unknown-value,
   state-order, replay, and error-mapping probes only when requirement text
   makes them applicable. If fewer than 2 variants are truly justified, emit
   fewer rather than inventing weak coverage.
6. Pass typed `setup`, `events`, and `oracle` fields unchanged to downstream
   validation. Never infer replay, connection, mutation, or oracle semantics
   later from names or free text.
7. Check `generation_summary` before dispatching validators. A requirement may
   legitimately have fewer variants than the requested maximum, and a triaged
   run may legitimately generate zero candidates when all requirements are
   static `no_issue`.

## Guardrails

- Preserve `standard_evidence` byte-for-byte as parsed JSON; do not paraphrase
  the quoted evidence.
- Keep one risk operation per variant. Let a later validator concretize it
  against the target implementation.
- Do not generate variants for static `no_issue` requirements in a triaged run.
- Do not exceed 3 variants per requirement.
- Do not manufacture a variant merely to fill the limit.
- Do not combine variants combinatorially.
- Do not convert descriptive or security-rationale text into normative tests.
- Treat an existing output as protected unless `--overwrite` is supplied.

## Self-Test

After changing this skill, run the three focused passes and the complete suite:

```powershell
python -m unittest tests.test_generate_candidates.GenerateCandidatesTests.test_normal_must_and_must_not
python -m unittest tests.test_generate_candidates.GenerateCandidatesTests.test_rejects_unverified_evidence
python -m unittest tests.test_generate_candidates.GenerateCandidatesTests.test_dedup_and_top_k_are_stable
python -m unittest discover -s tests -v
```
