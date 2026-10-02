#!/usr/bin/env python3
"""Assemble and verify per-chunk requirement records produced by agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from artifact_core import (
    build_requirement_artifact,
    dump_json_bytes,
    read_source,
)
from chunk_standard import atomic_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge agent-produced chunk records into a verified requirement artifact."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--chunks", required=True, type=Path)
    parser.add_argument("--agent-output-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.out.exists() and not args.overwrite:
        print(f"error: refusing to overwrite existing output: {args.out}", file=sys.stderr)
        return 2
    try:
        chunks = _load_object(args.chunks)
        output_paths = sorted(args.agent_output_dir.glob("*.json"))
        if not output_paths:
            raise ValueError(f"{args.agent_output_dir}: no agent JSON outputs found")
        outputs = [_load_object(path) for path in output_paths]
        raw, _text, lines = read_source(args.source)
        artifact, errors = build_requirement_artifact(chunks, outputs, raw, lines)
        if errors:
            for error in errors:
                print(f"validation error: {error}", file=sys.stderr)
            return 1
        atomic_write(args.out, dump_json_bytes(artifact))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"assembled {artifact['metadata']['requirement_count']} requirements from "
        f"{artifact['metadata']['processed_chunk_count']} agent-reviewed chunks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
