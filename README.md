# SpecVariant: Detecting Protocol Implementation Defects through Specification-Driven Semantic Variant Testing

SpecVariant extracts atomic requirements with line-addressable evidence from protocol standards, constructs semantic test variants around a single targeted change, and derives behavioral oracles from the corresponding requirements. It then locates relevant implementation paths and combines static analysis with controlled runtime validation to determine whether implementation behavior conforms to the standard.

The included artifacts cover 11 protocol standards and 11 implementation subjects across TLS, MQTT, QUIC, HTTP/2, DNS, and BGP. The `reports/` directory contains a selected collection of 212 fixed reports and 12 rejected reports, grouped by implementation.

## Main Modules

* Protocol-standard preprocessing and evidence-preserving chunking
* Atomic requirement and litmus-semantics extraction
* Baseline test and single-change semantic-variant generation
* Harness-independent intermediate representation normalization
* Adapter-based execution and implementation-native test generation
* Static analysis, controlled runtime validation, and verdict reporting
* Multi-agent workflow support for requirement extraction, test generation, implementation validation, and report generation
* Historical-defect benchmark metadata and baseline checkout utilities

## Repository Layout

```text
.
|- adapters/                       # Execution adapters and capability profiles
|- bench/                          # 214-case historical-defect benchmark
|  |- baselines.json               # Pinned baseline revisions
|  |- benchmark.jsonl              # One integrity-protected record per case
|  |- reports/                     # Human-readable benchmark case sheets
|  \- scripts/                     # Benchmark verification and revision checkout tools
|- document/                       # Eleven protocol-standard text files
|- extract/                        # Chunks, requirements, and extraction evidence
|- opt/                            # Multi-agent audit workflow
|  |- agents/                      # Agent prompts, contracts, scripts, and tests
|  |- scripts/                     # Self-tests, validation run, and integrity checks
|  \- tests/                       # End-to-end validation fixtures
|- reports/                        # Selected issue reports
|  |- fixed/                       # 212 reports for fixed defects
|  \- rejected/                    # 12 rejected reports
|- subjects/
|  \- pinned-base-revisions.json   # Pinned revisions for 11 implementation subjects
|- chunk_document.py               # Standard preprocessing and chunking
|- extract_litmus_semantics.py     # Requirement-semantics extraction
|- generate_litmus_tests.py        # Baseline litmus-test generation
|- generate_litmus_variants.py     # Semantic-variant generation
|- normalize_litmus_ir.py          # Harness-independent IR construction
|- run_litmus_ir.py                # Adapter-based IR execution
|- source_patch_agent.py           # Implementation-native test-patch generation
\- README.md
```

The `*_parallel.py` scripts provide parallel forms of the corresponding extraction or generation stages. Some existing internal paths and identifiers may still use the earlier name `SpecLitmus`.

## Environment Setup

Python **3.11 or later** is recommended. The OpenSSL TLS 1.3 adapter uses the dependency pinned in `requirements.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The semantic-extraction and generation stages use the official OpenAI API. Set your OpenAI API key:

```powershell
$env:OPENAI_API_KEY="your_api_key"
```

Model-calling commands read this key through `--api-key-env OPENAI_API_KEY` and use the official OpenAI API endpoint. Replace `YOUR_MODEL` in the commands with the model selected for the experiment. Run `python <script-name> --help` to view the complete command-line interface.

## Reproducible Pipeline

The following commands run the complete SpecVariant pipeline against TLS 1.3 (`RFC 8446`). Replace the standard document, protocol name, output paths, and adapter configuration when processing other standards and implementations.

### 1) Preprocess the standard

```powershell
python .\chunk_document.py `
  --input .\document\rfc8446.txt `
  --output .\output\rfc8446\chunks.jsonl `
  --document-id rfc8446
```

### 2) Extract requirement semantics

```powershell
python .\extract_litmus_semantics.py `
  --chunks .\output\rfc8446\chunks.jsonl `
  --out-jsonl .\output\rfc8446\semantics.jsonl `
  --out-json .\output\rfc8446\semantics.json `
  --api-key-env OPENAI_API_KEY `
  --base-url https://api.openai.com/v1 `
  --model YOUR_MODEL
```

### 3) Generate baseline litmus tests

```powershell
python .\generate_litmus_tests.py `
  --semantics .\output\rfc8446\semantics.json `
  --out-json .\output\rfc8446\litmus-tests.json `
  --out-jsonl .\output\rfc8446\litmus-tests.jsonl `
  --api-key-env OPENAI_API_KEY `
  --base-url https://api.openai.com/v1 `
  --model YOUR_MODEL
```

Add `--dry-run` at this stage to use the deterministic fallback without calling a model.

### 4) Generate semantic variants

```powershell
python .\generate_litmus_variants.py `
  --litmus .\output\rfc8446\litmus-tests.json `
  --out-json .\output\rfc8446\litmus-families.json `
  --out-jsonl .\output\rfc8446\variants.jsonl `
  --target-variants 4 `
  --api-key-env OPENAI_API_KEY `
  --base-url https://api.openai.com/v1 `
  --model YOUR_MODEL
```

### 5) Normalize the execution IR

```powershell
python .\normalize_litmus_ir.py `
  --families .\output\rfc8446\litmus-families.json `
  --chunks .\output\rfc8446\chunks.jsonl `
  --protocol tls13 `
  --out-json .\output\rfc8446\litmus-ir.json `
  --out-jsonl .\output\rfc8446\litmus-ir.jsonl
```

### 6) Exercise the IR with the bundled dry-run adapter

```powershell
python .\run_litmus_ir.py `
  --ir .\output\rfc8446\litmus-ir.json `
  --adapter dry-run `
  --profile .\adapters\profiles\generic.json `
  --out-json .\output\rfc8446\run-results.json `
  --out-jsonl .\output\rfc8446\run-results.jsonl
```

## SpecVariant End-to-End Validation

Run the bundled end-to-end validation workflow. It covers requirement assembly, candidate generation, runtime-plan execution against a fixture implementation, verdict rendering, and final integrity checks without calling a model service.

```powershell
python .\opt\scripts\run_total_test.py
```

Run all agent self-tests:

```powershell
python .\opt\scripts\run_all_self_tests.py
```

Machine-readable outputs are written to `opt/test-results/`. Detailed workflow contracts and the reusable-requirement run configuration are documented in [`opt/README.md`](opt/README.md) and [`opt/agents/speclitmus-coordinator/SKILL.md`](opt/agents/speclitmus-coordinator/SKILL.md).

## Report Collection

The selected report collection is organized by disposition and implementation:

* `reports/fixed/`: 212 reports describing fixed defects
* `reports/rejected/`: 12 rejected reports

These directories contain Markdown reports only; they do not include third-party source trees or generated build artifacts.

## Notes

* Generated reports should be reviewed manually before disclosure or submission.
* Some artifacts may be incomplete or redacted when the data required for direct reproduction cannot be shared safely.
