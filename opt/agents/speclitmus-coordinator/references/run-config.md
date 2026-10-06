# Run Configuration

Schema version: `speclitmus.run-config.v1`

The coordinator uses one explicit configuration to decide whether requirement
extraction is reused and which eligible requirements belong to the current
static-triage run.

```json
{
  "schema_version": "speclitmus.run-config.v1",
  "requirements": {
    "reuse": true,
    "artifact": "extract/rfc8446-extract/requirements.json"
  },
  "static_triage": {
    "start": 1,
    "end": 500,
    "batch_size": 100
  }
}
```

Save this configuration in the repository root. Relative artifact paths are
resolved against the directory containing the configuration file.

`static_triage.start` and `static_triage.end` are 1-based inclusive positions
in the stable sequence of requirements whose `eligibility` is `eligible`.
`end: null` selects through the last eligible requirement. `batch_size` is the
maximum number assigned to one static-triage Agent task. All three values must
be positive, `start <= end`, and `end` must not exceed the eligible count.

## Requirement Modes

- `requirements.reuse: true`: `artifact` is required. The coordinator skips
  extraction, verifies the artifact schema, source SHA-256, unique IDs, line
  intervals, exact source quotations, evidence flags, and eligible count, then
  snapshots it as `<output>/requirements.json`.
- `requirements.reuse: false`: omit `artifact`. The coordinator must invoke the
  requirement extractor first and write the fresh result to
  `<output>/requirements.json`. A pre-existing artifact outside the run cannot
  satisfy fresh mode.

Prepare the selected interval and deterministic batches with:

```powershell
python scripts/prepare_requirement_batches.py `
  --config <run-config.json> `
  --source <standard.txt> `
  --output-dir <output>
```

The command writes `run_config.resolved.json`, `requirement_selection.json`,
and `static-triage-batches/batch-NNNN.input.json`. Each Agent writes the
corresponding `batch-NNNN.output.json`. Merge only after every batch completes:

```powershell
python scripts/merge_static_triage_batches.py `
  --selection <output>/requirement_selection.json `
  --out <output>/static_triage.json
```

The merger rejects a missing batch, an unexpected requirement, a duplicate
record, a wrong selection digest, or any verdict outside the three allowed
values. Batch boundaries do not weaken coverage: every requirement in the
selected interval must appear exactly once.

## Manifest Fields

Copy the resolved values into `run_manifest.json`:

```json
{
  "requirement_source": {
    "mode": "reuse",
    "requirements_sha256": "<sha256>",
    "standard_sha256": "<sha256>"
  },
  "static_triage_selection": {
    "start": 1,
    "end": 500,
    "batch_size": 100,
    "selected_requirement_count": 500,
    "batch_count": 5,
    "selection_sha256": "<sha256>"
  }
}
```

The maximum runtime validation rounds remain separate from static-triage
batching. `max_rounds: 5` does not limit the number of static batches.
