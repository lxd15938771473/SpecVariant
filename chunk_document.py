#!/usr/bin/env python3
"""Chunk RFC/specification text while preserving evidence locations.

The output is JSONL.  Each line is one chunk with source line numbers,
section hints, plain text, and per-line records.  The extractor consumes
this file and asks the LLM to cite evidence by original line number.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SECTION_RE = re.compile(
    r"^(?P<num>(?:\d+|[A-Z])(?:\.\d+)*\.?)\s{1,4}(?P<title>[A-Z0-9][^\n]{2,120})$"
)
TOC_DOTS_RE = re.compile(r"\.{3,}\s*\d+\s*$")
RFC_HEADER_RE = re.compile(r"^RFC\s+\d+\s+.+\s+\w+\s+\d{4}\s*$")
PAGE_FOOTER_RE = re.compile(r"^.+\s+\[Page\s+\d+\]\s*$")


@dataclass
class SourceLine:
    n: int
    text: str
    char_start: int
    char_end: int


@dataclass
class Paragraph:
    lines: list[SourceLine]
    section: str

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def line_start(self) -> int:
        return self.lines[0].n

    @property
    def line_end(self) -> int:
        return self.lines[-1].n

    @property
    def char_start(self) -> int:
        return self.lines[0].char_start

    @property
    def char_end(self) -> int:
        return self.lines[-1].char_end


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def iter_source_lines(raw: str, keep_page_noise: bool) -> Iterable[SourceLine]:
    offset = 0
    for idx, raw_line in enumerate(raw.splitlines(keepends=True), start=1):
        line_no_eol = raw_line.rstrip("\r\n")
        clean = line_no_eol.replace("\f", "").rstrip()
        char_start = offset
        char_end = offset + len(raw_line)
        offset = char_end

        if not keep_page_noise:
            stripped = clean.strip()
            if RFC_HEADER_RE.match(stripped) or PAGE_FOOTER_RE.match(stripped):
                continue

        yield SourceLine(n=idx, text=clean, char_start=char_start, char_end=char_end)


def is_section_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or TOC_DOTS_RE.search(stripped):
        return False
    if line.startswith(" ") or line.startswith("\t"):
        return False
    match = SECTION_RE.match(stripped)
    if not match:
        return False
    title = match.group("title")
    lower = title.lower()
    if lower.startswith(("figure ", "table ")):
        return False
    return True


def build_paragraphs(lines: list[SourceLine]) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    current: list[SourceLine] = []
    current_section = "front matter"
    paragraph_section = current_section

    def flush() -> None:
        nonlocal current, paragraph_section
        if current:
            paragraphs.append(Paragraph(lines=current, section=paragraph_section))
            current = []
            paragraph_section = current_section

    for line in lines:
        if is_section_heading(line.text):
            flush()
            current_section = line.text.strip()
            paragraph_section = current_section
            current = [line]
            flush()
            continue

        if line.text.strip() == "":
            flush()
            continue

        if not current:
            paragraph_section = current_section
        current.append(line)

    flush()
    return paragraphs


def split_large_paragraph(paragraph: Paragraph, max_chars: int) -> list[Paragraph]:
    if len(paragraph.text) <= max_chars:
        return [paragraph]

    pieces: list[Paragraph] = []
    current: list[SourceLine] = []
    current_chars = 0
    for line in paragraph.lines:
        line_len = len(line.text) + 1
        if current and current_chars + line_len > max_chars:
            pieces.append(Paragraph(lines=current, section=paragraph.section))
            current = []
            current_chars = 0
        current.append(line)
        current_chars += line_len
    if current:
        pieces.append(Paragraph(lines=current, section=paragraph.section))
    return pieces


def make_chunk(
    *,
    source_path: Path,
    document_id: str,
    chunk_index: int,
    paragraphs: list[Paragraph],
) -> dict:
    lines = [line for paragraph in paragraphs for line in paragraph.lines]
    text = "\n\n".join(paragraph.text for paragraph in paragraphs)
    sections: list[str] = []
    for paragraph in paragraphs:
        if paragraph.section not in sections:
            sections.append(paragraph.section)

    return {
        "chunk_id": f"{document_id}_chunk_{chunk_index:04d}",
        "document_id": document_id,
        "source_path": str(source_path),
        "chunk_index": chunk_index,
        "line_start": lines[0].n,
        "line_end": lines[-1].n,
        "char_start": lines[0].char_start,
        "char_end": lines[-1].char_end,
        "sections": sections,
        "text": text,
        "lines": [{"n": line.n, "text": line.text} for line in lines],
    }


def chunk_paragraphs(
    source_path: Path,
    document_id: str,
    paragraphs: list[Paragraph],
    max_chars: int,
    overlap_paragraphs: int,
) -> list[dict]:
    expanded: list[Paragraph] = []
    for paragraph in paragraphs:
        expanded.extend(split_large_paragraph(paragraph, max_chars))

    chunks: list[dict] = []
    current: list[Paragraph] = []
    current_chars = 0

    for paragraph in expanded:
        para_len = len(paragraph.text) + 2
        if current and current_chars + para_len > max_chars:
            chunks.append(
                make_chunk(
                    source_path=source_path,
                    document_id=document_id,
                    chunk_index=len(chunks),
                    paragraphs=current,
                )
            )
            if overlap_paragraphs > 0:
                current = current[-overlap_paragraphs:]
                current_chars = sum(len(p.text) + 2 for p in current)
            else:
                current = []
                current_chars = 0

        current.append(paragraph)
        current_chars += para_len

    if current:
        chunks.append(
            make_chunk(
                source_path=source_path,
                document_id=document_id,
                chunk_index=len(chunks),
                paragraphs=current,
            )
        )

    return chunks


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk a protocol standard into evidence-preserving JSONL.")
    parser.add_argument("--input", required=True, type=Path, help="Input text file.")
    parser.add_argument("--output", required=True, type=Path, help="Output chunks JSONL.")
    parser.add_argument("--document-id", default=None, help="Stable document id. Defaults to input stem.")
    parser.add_argument("--max-chars", type=int, default=6500, help="Approximate maximum characters per chunk.")
    parser.add_argument(
        "--overlap-paragraphs",
        type=int,
        default=1,
        help="Number of trailing paragraphs to repeat in the next chunk.",
    )
    parser.add_argument(
        "--keep-page-noise",
        action="store_true",
        help="Keep RFC page headers/footers instead of removing them.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = read_text(args.input)
    lines = list(iter_source_lines(raw, keep_page_noise=args.keep_page_noise))
    paragraphs = build_paragraphs(lines)
    document_id = args.document_id or args.input.stem
    chunks = chunk_paragraphs(
        source_path=args.input,
        document_id=document_id,
        paragraphs=paragraphs,
        max_chars=args.max_chars,
        overlap_paragraphs=args.overlap_paragraphs,
    )
    write_jsonl(args.output, chunks)
    print(
        json.dumps(
            {
                "input": str(args.input),
                "output": str(args.output),
                "document_id": document_id,
                "paragraphs": len(paragraphs),
                "chunks": len(chunks),
                "max_chars": args.max_chars,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
