# Candidate Data Contract

## Input

The primary input is either a full `speclitmus.requirements.v3` object or the
coordinator-produced `speclitmus.requirement-selection.v1` object for the
current static-triage interval. Both carry the same requirement item shape.
Every item contains:

```json
{
  "requirement_id": "req-a81c64bd76c858d07fc5",
  "condition": "when negotiating TLS 1.3",
  "required_behavior": "include the supported_versions extension",
  "forbidden_behavior": "",
  "error_behavior": "",
  "check_type": "explicit_normative",
  "why_checkable": "The ClientHello extension serializer can be inspected and exercised.",
  "eligibility": "eligible",
  "evidence": {
    "source_id": "rfc8446",
    "quote": "A ClientHello message MUST contain ...",
    "line_start": 2040,
    "line_end": 2042,
    "verified": true,
    "chunk_id": "chunk-0021"
  }
}
```

`condition`, `required_behavior`, `forbidden_behavior`, and `error_behavior`
may be empty, but an eligible item must have at least one non-empty behavior.
Items marked `exclude` are not passed to static triage or candidate generation.
Evidence requires a non-empty source identifier, exact quotation, positive
inclusive line interval, and Boolean `verified: true`.

The generator derives its internal require/forbid direction from
`required_behavior` and `forbidden_behavior`; extractor output does not need
`section`, `modality`, or `subject`. For compatibility, legacy arrays containing
those fields remain accepted.

The generator rejects the complete stage if any eligible item is invalid, has
unverified evidence, or repeats a `requirement_id`. It drops only items
explicitly marked `eligibility: exclude`.

## Optional Static Triage Input

When `--static-triage <static_triage.json>` is supplied, the generator also
requires one static triage record for every input requirement. Passing
`requirement_selection.json` therefore checks only the configured interval,
without weakening exact coverage inside that interval. The triage schema
is `speclitmus.static-triage.v1` and is defined in the coordinator's
`references/static-triage-contract.md`.

Only triage records with verdict `suspected_issue` or `issue_found` are selected
for candidate generation. Static `no_issue` records are skipped and remain in
the static triage ledger; they are not converted into final runtime audit
records.

The generator rejects:

- missing triage records for input requirements;
- duplicate triage records;
- triage records for unknown requirements;
- verdicts outside `no_issue`, `suspected_issue`, and `issue_found`.

## Output Envelope

```json
{
  "schema_version": "1.0",
  "generator": {
    "name": "speclitmus-test-generator",
    "max_variants_per_requirement": 3
  },
  "generation_summary": {
    "requirements_consumed": 1,
    "base_tests": 1,
    "variants": 3,
    "candidates": 4,
    "static_triage": {
      "schema_version": "speclitmus.static-triage.v1",
      "records_consumed": 396,
      "verdict_counts": {
        "no_issue": 320,
        "suspected_issue": 70,
        "issue_found": 6
      },
      "requirements_selected_for_generation": 76,
      "requirements_skipped_after_static_no_issue": 320,
      "skipped_requirement_ids": []
    }
  },
  "candidates": []
}
```

Candidate ordering is stable: input requirement order, then base, then variants
ranked by descending value score and a fixed risk-class tie breaker.
In a triaged run, zero candidates is valid when every input requirement has
static triage verdict `no_issue`.

## Candidate

Every candidate contains:

| Field | Contract |
|---|---|
| `candidate_id` | Stable identifier derived from requirement content and risk class |
| `requirement_id` | Exact upstream identifier |
| `kind` | `base` or `variant` |
| `name` | Human-readable probe title |
| `risk_class` | `baseline`, `missing`, `duplicate`, `boundary`, `unknown`, `state_order`, `replay`, or `error_mapping` |
| `setup` | Typed preconditions and connection model |
| `events` | Ordered typed operations; downstream tools must not reinterpret names |
| `oracle` | Normative direction, expected/forbidden behavior, and expected error |
| `expected_strength` | `required` for MUST-level or `recommended` for SHOULD-level text |
| `feasibility` | `high` or `medium` implementation-neutral estimate |
| `value_score` | Integer used for bounded selection |
| `standard_evidence` | Unchanged copy of the upstream evidence object |
| `requirement_snapshot` | Internal candidate view derived from the atomic behaviors and evidence |

## Typed Events

Each event has `seq`, `type`, `actor`, `connection`, `action`, and `parameters`.
The allowed event types emitted here are:

- `setup`: apply explicit requirement preconditions;
- `construct`: create the direct normative stimulus;
- `mutate`: apply exactly one named high-risk operation;
- `send`: deliver the input to the implementation;
- `observe`: capture acceptance, rejection, output, state change, and error.

Mutation actions have fixed meanings:

| Risk | Action |
|---|---|
| missing | `omit_required_element_or_action` |
| duplicate | `duplicate_single_element_or_message` |
| boundary | `set_nearest_out_of_range_value` |
| unknown | `substitute_unknown_or_reserved_value` |
| state_order | `deliver_in_adjacent_invalid_state` |
| replay | `replay_on_fresh_connection` |
| error_mapping | `trigger_violation_and_capture_error` |

The implementation validator may map these typed abstract actions to native
APIs, packets, fixtures, or state transitions. It must not change their
semantics.

When an inclusive or exclusive numeric bound is explicit, a boundary mutation
also carries `parameters.boundary_hints`. Each detected lower or upper bound
records its value, inclusivity, nearest valid value, and nearest outside value.
No bound is invented when direction is unclear.
