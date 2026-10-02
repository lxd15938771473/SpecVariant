---
name: speclitmus-implementation-validator
description: Inspect a protocol implementation, design and execute bounded minimal reproducers with positive controls, and return code and runtime evidence. Use when a typed SpecLitmus candidate must be traced through real source code, built, run, or iteratively disambiguated for at most five rounds.
---

# SpecLitmus Implementation Validator

Inspect evidence before experimenting. Never infer implementation behavior from
function names, constants, or a generic runner.

## Static Requirement Triage Mode

Before variant generation, inspect each generator-eligible requirement directly
against the target source tree. Produce `static_triage.json` records conforming
to `../speclitmus-coordinator/references/static-triage-contract.md`.

The coordinator assigns a `speclitmus.static-triage-batch.v1` input. Process
every requirement in that batch in input order; do not sample, stop after an
interesting finding, or inspect rules outside the batch. Write one
`speclitmus.static-triage.v1` output to the exact path named by
`metadata.expected_output`, copying `batch_id` and `selection_sha256` into the
output metadata. The coordinator merges batch outputs only after exact coverage
checks pass.

For each requirement:

1. Read the exact standard evidence, including line interval.
2. Trace the implementation's reachable parse, generation, validation,
   state-transition, and error-mapping paths. Record real `path:line` excerpts,
   searched symbols, build/configuration assumptions, and negative search
   evidence when a path appears absent.
3. Choose exactly one static triage verdict:
   - `no_issue`: source evidence proves the reachable implementation path is
     consistent enough to terminate this requirement for the current run;
   - `suspected_issue`: a concrete mismatch or missing path remains plausible;
   - `issue_found`: static evidence already identifies an apparent mismatch.
4. Use `generation_action: "skip"` only for static `no_issue`; use
   `generation_action: "generate_variants"` for `suspected_issue` and
   `issue_found`.

Static `no_issue` skips all later candidate generation and runtime validation
for that requirement in the current run. Use it conservatively. A valid
`no_issue` requires verified standard evidence, at least one real source excerpt
with valid line numbers, a reachable-path explanation, recorded configuration or
negative-search assumptions when relevant, an empty `inconsistency_reason`, an
empty `remaining_uncertainty`, and `generation_action: "skip"`.

If evidence is incomplete, the reachable path is unclear, the code appears
absent, or a high-risk variant could still distinguish plausible behaviors,
classify the requirement as `suspected_issue` and send it to test generation.

## Workflow

1. Read the candidate and its exact standard evidence.
2. Inspect the target repository and trace the reachable parse, validation,
   state-transition, and error paths. Record real `path:line` excerpts and
   relevant build/configuration assumptions in `code_check.json`.
3. State the remaining uncertainty and competing hypotheses. Design the
   smallest experiment that distinguishes them. Include a valid positive
   control using the same fixture and a mutated reproducer.
4. Prefer the implementation's native build and test facilities. Keep all
   generated sources and logs under the assigned task directory.
5. Write a plan conforming to
   [runtime-plan-contract.md](references/runtime-plan-contract.md), then run:

   ```text
   python scripts/run_runtime_plan.py --plan <plan.json> --workspace-root <target-repo> --output-dir <round-dir>
   ```

6. Interpret failed controls as invalid test evidence, not implementation
   defects. Preserve commands, stdout, stderr, exit codes, timeout status, and
   environment metadata.
7. Stop when evidence supports a verdict or when another experiment cannot add
   information. Otherwise create a materially different plan aimed at the
   explicit remaining uncertainty. Never exceed five rounds.

Do not edit the target implementation merely to make a test pass. Instrumented
diagnostic builds are allowed only when clearly recorded and paired with an
unmodified behavioral run.
