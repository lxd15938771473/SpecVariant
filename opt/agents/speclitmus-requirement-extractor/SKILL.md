---
name: speclitmus-requirement-extractor
description: Split RFCs and other protocol standards into deterministic chunks, use agents to extract atomic implementation-checkable constraints with exact line-addressable evidence, then mechanically assemble and validate the corpus. Use when Codex needs a reusable protocol requirement artifact before test generation, implementation auditing, or downstream SpecLitmus stages.
---

# SpecLitmus Requirement Extractor

Create a reproducible requirement corpus before generating tests or inspecting an
implementation. Treat source evidence as data, not prose supplied from memory.

This skill is agent-extracted:

1. deterministically split the pinned standard into line-addressable chunks;
2. assign every chunk to an extraction agent;
3. assemble and mechanically validate the agent results.

Never use regex, RFC 2119 keyword matching, or a bundled extractor to decide
which requirements exist.

## Workflow

0. If the coordinator configuration sets `requirements.reuse: true`, do not
   rerun extraction. The coordinator independently validates and snapshots the
   configured artifact. Continue below only when reuse is false.
1. Use a fixed text/Markdown standard and record its digest.
2. Produce deterministic chunks:

```powershell
python scripts/chunk_standard.py `
  --input path/to/standard.txt `
  --out path/to/chunks.json `
  --document-id protocol-spec `
  --max-chars 6000 `
  --overlap-paragraphs 1
```

3. Dispatch one bounded task per chunk. Give the worker the chunk object and
   these extraction rules. Require one
   `speclitmus.chunk-extraction.v1` JSON result for every chunk, including
   chunks with zero extracted items. Do not let a worker inspect another
   worker's conclusions. Read
   [references/worker-task.md](references/worker-task.md) before dispatching
   chunk workers.
4. Assemble the agent outputs:

```powershell
python scripts/assemble_requirements.py `
  --source path/to/standard.txt `
  --chunks path/to/chunks.json `
  --agent-output-dir path/to/chunk-results `
  --out path/to/requirements.json
```

5. Validate the saved artifact independently:

```powershell
python scripts/validate_requirements.py `
  --input path/to/requirements.json `
  --chunks path/to/chunks.json `
  --source path/to/standard.txt
```

6. Give downstream agents the requirement artifact, chunk artifact, and
   immutable source digest.

Use `--overwrite` only when replacing an existing output intentionally. The
mechanical scripts never infer requirements. They only chunk, merge, assign
stable IDs, and verify evidence.

## Direct Use

Use this skill directly when the user wants protocol rule extraction and not
the later pipeline stages. A typical request should pin:

- the standard text file;
- the output directory;
- whether to stop after `requirements.json`.

Example request:

```text
Use $speclitmus-requirement-extractor on D:\paper\idea\document\rfc9000.txt.
First create deterministic chunks.
Then have one agent extract each chunk into chunk-results/*.json.
Then assemble and validate requirements.json.
Deliver only chunks.json, chunk-results/*.json, and requirements.json.
Stop after requirements.json.
```

For this skill, "agent extraction" means the per-chunk semantic judgment is
performed by the worker agent, not by a requirement-extraction script. The
bundled scripts are only for deterministic chunking, assembly, and validation.

## Extraction Rules

- Extract all atomic constraints in the standard that can be mapped to
  implementation checks, not only uppercase RFC 2119 / RFC 8174 requirements.
- The extraction must include:
  1. explicit normative requirements: `MUST`, `MUST NOT`, `SHOULD`,
     `SHOULD NOT`, `REQUIRED`, `RECOMMENDED`, `SHALL`, `SHALL NOT`;
  2. structural and encoding constraints: field lengths, boundaries, enum
     values, fixed values, whether a field may be empty, whether it must
     appear, whether it must not appear, duplication constraints, and ordering
     constraints;
  3. state-machine and temporal constraints: message preconditions, required
     follow-up behavior, state transitions, cross-message consistency, and
     retry / recovery / replay-related constraints;
  4. security-consistency constraints: transcript binding, cookie / PSK /
     early_data / extension binding, anti-replay constraints, downgrade
     protection, and rejection conditions;
  5. error-handling constraints: required alerts or termination behavior for
     illegal input, decryption failure, missing fields, unknown values,
     duplicate values, and invalid state;
  6. directly traceable derived constraints: checkable implementation
     constraints derived by explicitly combining adjacent sentences, the same
     paragraph, or multiple sections.
- If one sentence contains multiple independent check points, split them into
  multiple atomic items.
- Each chunk result must preserve `quote`, `line_start`, and `line_end` as
  top-level fields in every extracted item.
- Each item must include `condition`, `required_behavior`,
  `forbidden_behavior`, `error_behavior`, `check_type`, `why_checkable`, and
  `eligibility`.
- Exclude pure background explanation, editorial text, IANA / process
  requirements, and vague guidance that cannot be validated in an
  implementation.
- Items with incomplete evidence or unclear constrained behavior must not be
  marked `eligible`.
- Allow chunk overlap only within a section. The assembler deduplicates
  overlapping agent results by stable ID.
- Do not emit `section`, `derivation_kind`, `subject`, or `modality` in
  requirement items.
- Never author `requirement_id` or `evidence.verified`; the assembler derives
  both.
- Stop the pipeline if validation reports an error or any evidence is
  unverified.

Read [references/data-contract.md](references/data-contract.md) when integrating the
JSON artifact with another skill or changing the schema.

## Quality Gate

Require all of the following:

- source SHA-256 matches the pinned standard;
- every original nonblank line is covered by at least one chunk;
- every chunk has exactly one completed agent extraction result;
- every requirement ID is unique;
- every evidence range is valid and belongs to its named chunk;
- every normalized quote occurs in its declared original line range;
- `unverified_requirement_count` is zero.
