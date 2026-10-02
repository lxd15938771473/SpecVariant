# SpecLitmus Orchestration Instructions

For a protocol compliance audit in this directory, Codex is the sole
coordinator.

1. Read `agents/speclitmus-coordinator/SKILL.md` and its pipeline contract in
   full before dispatching work.
2. Read each sibling Agent's `SKILL.md` before assigning that stage.
3. Delegate requirement extraction, test generation, implementation validation,
   and verdict/reporting as bounded tasks with stable IDs and isolated output
   directories.
4. Keep exact standard evidence attached to every requirement and candidate.
5. Permit only `no_issue`, `suspected_issue`, and `issue_found`.
6. For an unresolved suspected issue, request a new plan only when it targets
   explicit remaining uncertainty and differs from prior rounds. Never exceed
   five runtime rounds.
7. Require real code references, controlled runtime artifacts, Agent self-test
   evidence, and final coordinator validation before declaring the run complete.
8. Write detailed problem reports as Markdown. Do not classify harness,
   adapter, build, or launch failures as implementation defects.

Use `scripts/run_all_self_tests.py` for the full Agent regression suite and
`scripts/run_total_test.py` for the final executable pipeline check.
