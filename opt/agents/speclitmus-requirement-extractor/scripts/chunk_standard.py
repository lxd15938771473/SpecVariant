#!/usr/bin/env python3
"""Create deterministic, line-addressable chunks for agent extraction."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from artifact_core import (
    build_chunk_artifact,
    dump_json_bytes,
    read_source,
    validate_chunks,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Split a UTF-8 standard into deterministic section-aware chunks."
    )
    parser.add_argument("--input", required=True, type=Path, help="source text/Markdown")
    parser.add_argument("--out", required=True, type=Path, help="chunks JSON")
    parser.add_argument("--document-id", required=True, help="stable document ID")
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--overlap-paragraphs", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.document_id.strip():
        print("error: --document-id must not be blank", file=sys.stderr)
        return 2
    if args.out.exists() and not args.overwrite:
        print(f"error: refusing to overwrite existing output: {args.out}", file=sys.stderr)
        return 2
    try:
        raw, _text, lines = read_source(args.input)
        artifact = build_chunk_artifact(
            args.document_id.strip(),
            raw,
            lines,
            args.max_chars,
            args.overlap_paragraphs,
        )
        errors = validate_chunks(artifact, raw, lines)
        if errors:
            for error in errors:
                print(f"validation error: {error}", file=sys.stderr)
            return 1
        atomic_write(args.out, dump_json_bytes(artifact))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"created {artifact['metadata']['chunk_count']} chunks; "
        f"coverage_complete={str(artifact['metadata']['coverage_complete']).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
