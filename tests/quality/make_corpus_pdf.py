"""Render a plain-text file as a minimal text PDF (Helvetica, ASCII).

Used to build ``tests/fixtures/local_corpus/*.pdf`` without third-party PDF
libraries so the fixture is reproducible from the checked-in text::

    python -m tests.quality.make_corpus_pdf input.txt output.pdf
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import List

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN = 54
LINE_HEIGHT = 14
FONT_SIZE = 10
LINES_PER_PAGE = int((PAGE_HEIGHT - 2 * MARGIN) / LINE_HEIGHT)
WRAP = 95


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pages(text: str) -> List[List[str]]:
    lines: List[str] = []
    for paragraph in text.splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, WRAP) or [""])
    pages: List[List[str]] = []
    for start in range(0, len(lines), LINES_PER_PAGE):
        pages.append(lines[start : start + LINES_PER_PAGE])
    return pages or [[""]]


def build_pdf(text: str) -> bytes:
    ascii_text = text.encode("ascii", "replace").decode("ascii")
    pages = _pages(ascii_text)
    objects: List[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: List[int] = []
    pages_id_placeholder = len(objects) + 2 * len(pages) + 1
    for page_lines in pages:
        content_parts = ["BT", f"/F1 {FONT_SIZE} Tf", f"{LINE_HEIGHT} TL", f"{MARGIN} {PAGE_HEIGHT - MARGIN} Td"]
        for line in page_lines:
            content_parts.append(f"({_escape(line)}) Tj T*")
        content_parts.append("ET")
        stream = "\n".join(content_parts).encode("ascii")
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add(
            (
                f"<< /Type /Page /Parent {pages_id_placeholder} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        page_ids.append(page_id)
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii"))
    assert pages_id == pages_id_placeholder
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return bytes(out)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: make_corpus_pdf.py input.txt output.pdf")
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    Path(sys.argv[2]).write_bytes(build_pdf(source))
    print(f"wrote {sys.argv[2]}")


if __name__ == "__main__":
    main()
