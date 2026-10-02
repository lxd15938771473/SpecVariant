# Chunk Worker Task

Use this reference when dispatching one extraction worker per chunk.

## Worker Inputs

Give the worker only:

- the `chunk` object from `chunks.json`;
- the pinned `document_id`;
- the extraction rules from [`SKILL.md`](../SKILL.md);
- the output contract from [data-contract.md](data-contract.md).

Do not give the worker:

- other chunks;
- another worker's conclusions;
- prior requirement artifacts;
- requirement IDs;
- outside standards or related RFCs unless the user explicitly asked for them.

## Worker Goal

Read one chunk and produce exactly one
`speclitmus.chunk-extraction.v1` JSON object for that chunk.

If the chunk has no extractable implementation-checkable constraints, still
return a completed result with an empty `requirements` array.

## Worker Checklist

1. Read the chunk text closely and extract all atomic constraints that map to
   an implementation check.
2. Include explicit normative clauses, structural/encoding limits,
   state-machine rules, security-consistency rules, error-handling behavior,
   and directly traceable derived constraints.
3. Split one sentence into multiple items when it contains multiple independent
   checks.
4. Preserve the exact `quote`, `line_start`, and `line_end` from the chunk.
5. Fill `condition`, `required_behavior`, `forbidden_behavior`,
   `error_behavior`, `check_type`, `why_checkable`, and `eligibility` for every
   item.
6. Exclude background explanation, editorial text, IANA/process material, and
   vague guidance that cannot be checked in an implementation.
7. Mark items with incomplete evidence or unclear constrained behavior as
   `eligibility: "exclude"`, not `eligible`.

## Output Skeleton

```json
{
  "schema_version": "speclitmus.chunk-extraction.v1",
  "chunk_id": "chunk-0001",
  "status": "complete",
  "requirements": []
}
```

## Example Shape

```json
{
  "schema_version": "speclitmus.chunk-extraction.v1",
  "chunk_id": "chunk-0002",
  "status": "complete",
  "requirements": [
    {
      "quote": "Within one connection, an endpoint MUST NOT process the same Token value\nmore than once.",
      "line_start": 11,
      "line_end": 12,
      "condition": "within one connection, the same Token value was already processed",
      "required_behavior": "",
      "forbidden_behavior": "process the same Token value more than once",
      "error_behavior": "",
      "check_type": "state_machine_invariant",
      "why_checkable": "Token state and the duplicate-token branch can be inspected and replayed.",
      "eligibility": "eligible"
    }
  ]
}
```
