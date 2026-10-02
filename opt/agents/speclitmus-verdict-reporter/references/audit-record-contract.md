# Audit Record Contract

The renderer accepts either a JSON array or `{"records": [...]}`. Every record
must contain:

```json
{
  "record_id": "tls-clienthello-001",
  "root_cause_key": "tls-clienthello-duplicate-extension",
  "title": "Duplicate extension is accepted",
  "problem_description": "What input and externally visible behavior differ",
  "verdict": "issue_found",
  "confidence": "high",
  "standard_check": {
    "section": "RFC 8446 §4.2",
    "exact_text": "The exact, verified source text.",
    "interpretation": "The applicable condition and required behavior.",
    "evidence_verified": true
  },
  "code_check": {
    "summary": "The reachable implementation behavior.",
    "excerpts": [
      {
        "path": "src/parser.c",
        "line_start": 42,
        "line_end": 51,
        "code": "if (...) { ... }",
        "explanation": "Why this reachable code matters."
      }
    ]
  },
  "runtime_rounds": [
    {
      "round": 1,
      "status": "passed",
      "positive_control_status": "passed",
      "reproducer_status": "passed",
      "summary": "Valid input succeeded; the prohibited mutation was accepted.",
      "steps": []
    }
  ],
  "decision_reason": "Why all evidence supports this verdict.",
  "inconsistency_reason": "How implementation behavior differs from the requirement.",
  "remaining_uncertainty": ""
}
```

Rules:

- `verdict` is exactly `no_issue`, `suspected_issue`, or `issue_found`.
- `confidence` is exactly `low`, `medium`, or `high`.
- Standard evidence must be verified and include exact text, section, and
  interpretation.
- Code evidence must include at least one real excerpt with `path`, positive
  line interval, code, and explanation.
- Runtime evidence contains one through five uniquely numbered rounds. A round
  may record `passed`, `failed`, `timeout`, `launch_error`, `incomplete`, or
  `blocked`. Every round records positive-control and reproducer status.
  `no_issue` and `issue_found` require at least one round where both are
  `passed`; otherwise the evidence remains suspect.
- `issue_found` and `suspected_issue` require `inconsistency_reason`.
  `suspected_issue` additionally requires `remaining_uncertainty`.
- `task_id` may be used instead of `record_id` for coordinator compatibility.
- `root_cause_key` is optional but recommended for `issue_found` and
  `suspected_issue`. Records that share the same non-empty `root_cause_key`
  are treated as duplicate manifestations of the same underlying issue and
  produce one Markdown report rather than one report per record.

Detailed reports contain: title, verdict, problem description, exact standard
text, implementation `path:line` excerpts, runtime results, inconsistency
reason, decision reason, and—when applicable—remaining uncertainty.
