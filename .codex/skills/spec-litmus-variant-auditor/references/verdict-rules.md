# Three-Verdict Review Rules

Use these rules when auditing SpecLitmus variants as an agent-driven protocol review.

## Final Verdicts

Use exactly three final verdicts:

- `no_issue`
  The standard requirement and implementation behavior are clear enough to conclude that this variant does not expose a defect.
- `suspected_issue`
  The variant identifies a plausible defect, but the available standard/code/runtime evidence is not enough to confirm it or clear it.
- `issue_found`
  The standard requirement and implementation behavior are clear enough to conclude that the implementation violates, omits, or incorrectly handles the required behavior.

Do not create extra final verdicts. Put runtime blockers, observation-only nature, positive-control status, or confidence in supporting fields.

## Static Evidence Is Enough When It Is Actually Strong

Static evidence can directly support either `no_issue` or `issue_found`.

Static evidence is strong enough only when all of these are true:

- the relevant standard condition is identified and understood;
- the variant condition maps cleanly to that standard condition;
- the implementation path has been followed through the relevant behavior;
- the cited code shows the actual accept/reject/construct/parse/state behavior, not only a symbol or helper name;
- there is no unresolved configuration, build, environment, or runtime dependency that could change the conclusion.

If the code clearly implements the required behavior, classify `no_issue`.

If the code clearly contradicts the requirement, lacks a required validation, accepts a forbidden input, rejects a required input, emits a wrong value, or maps a required error incorrectly, classify `issue_found`.

## When To Use Runtime Tests

Use runtime tests to settle uncertainty, not as a ritual.

Runtime testing is especially useful for:

- transport scheduling, replay, reorder, fragmentation, timer, and early-data behavior;
- behavior split across generated code, callbacks, configuration, or build options;
- cases where static review shows a plausible issue but not the externally observable result;
- confirming an `issue_found` before writing a detailed bug report when a focused test is feasible.

Runtime testing is not required when the static evidence already proves `no_issue` or `issue_found`.

If runtime testing is infeasible, keep the verdict based on the available evidence:

- use `issue_found` if static evidence proves the defect;
- use `no_issue` if static evidence proves correct behavior;
- use `suspected_issue` if the missing runtime observation is necessary to decide.

## Evidence Quality Bar

- Do not classify from keyword, constant, enum, function, or test-name presence alone.
- Do not classify from adapter support or adapter failure.
- Do not call missing test coverage an implementation defect by itself.
- Do not call an observation or differential result a defect unless it contradicts a concrete protocol requirement or implementation promise.
- Prefer `suspected_issue` over forcing a conclusion when the decisive behavior has not been checked.
- Prefer `issue_found` over unnecessary hesitation when the standard and code contradiction is direct and reproducible from source.

## Required Explanation

Every verdict needs:

- `standard_check`: exact section or source and the normative meaning;
- `code_check`: file:line evidence and behavior explanation;
- `test_check`: runtime evidence, or why runtime was unnecessary or infeasible;
- `decision_reason`: the short comparison that justifies the verdict.
