# Runtime Plan Contract

The executor accepts UTF-8 JSON with this shape:

```json
{
  "schema_version": "1.0",
  "task_id": "tls-clienthello-001",
  "round": 1,
  "remaining_uncertainty": "Whether the duplicate extension reaches validation",
  "competing_hypotheses": [
    "The parser rejects the duplicate",
    "The duplicate bypasses validation"
  ],
  "distinguishing_observation": "The mutated case is rejected with the required alert",
  "difference_from_previous": "First focused runtime experiment",
  "steps": [
    {
      "id": "build",
      "role": "build",
      "argv": ["python", "-m", "pytest", "--collect-only"],
      "cwd": ".",
      "timeout_seconds": 120,
      "env": {"TEST_MODE": "1"},
      "expected_exit_codes": [0],
      "continue_on_failure": false
    },
    {
      "id": "control",
      "role": "positive_control",
      "argv": ["python", "repro.py", "--valid"],
      "cwd": ".",
      "timeout_seconds": 30
    },
    {
      "id": "reproducer",
      "role": "reproducer",
      "argv": ["python", "repro.py", "--mutated"],
      "cwd": ".",
      "timeout_seconds": 30
    }
  ]
}
```

Rules:

- `round` is an integer from 1 through 5.
- Uncertainty, at least two competing hypotheses, the distinguishing
  observation, and the change from any prior attempt must be explicit.
- `steps` must contain both `positive_control` and `reproducer` roles. Allowed
  roles are `setup`, `build`, `positive_control`, `reproducer`, and
  `diagnostic`. At least one positive control must run before the reproducer.
- `argv` is a non-empty string array. Shell command strings are not accepted.
- `cwd` is relative to `--workspace-root`; traversal and resolved paths outside
  that root are rejected.
- Each step timeout is 1–600 seconds. Environment overrides must use ordinary
  environment-variable names and string values.
- Expected exit codes default to `[0]`. Execution stops after an unexpected
  result unless `continue_on_failure` is true.

The output directory contains `result.json`, `environment.json`,
`stdout.log`, `stderr.log`, and per-step logs. A timed-out or failed positive
control invalidates the experiment; it must not be used as defect evidence.

`code_check.json` should contain `task_id`, `summary`,
`configuration_assumptions`, `reachable_path`, and `excerpts`. Every excerpt
contains `path`, `line_start`, `line_end`, `code`, and `explanation`.
