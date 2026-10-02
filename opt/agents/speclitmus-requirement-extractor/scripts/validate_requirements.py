#!/usr/bin/env python3
"""Validate a requirement artifact and its chunk evidence against source bytes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from artifact_core import read_source, validate_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate requirement/chunk artifacts against the source standard."
    )
    parser.add_argument("--input", required=True, type=Path, help="requirements JSON")
    parser.add_argument("--chunks", required=True, type=Path, help="chunks JSON")
    parser.add_argument("--source", required=True, type=Path, help="source text/Markdown")
    return parser


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return data


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requirements = _load_json(args.input)
        chunks = _load_json(args.chunks)
        raw, _text, lines = read_source(args.source)
        errors = validate_artifacts(requirements, chunks, raw, lines)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"validation error: {error}", file=sys.stderr)
        return 1
    print(
        f"valid: {len(requirements['requirements'])} requirements, "
        f"{len(chunks['chunks'])} chunks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
