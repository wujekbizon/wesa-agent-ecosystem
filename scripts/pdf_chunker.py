#!/usr/bin/env python3
"""
PDF Chunker - Splits PDFs into fixed-size chunks with intelligent boundary detection.

Usage:
    python scripts/pdf_chunker.py <pdf_path> --category <category> [options]

Trimming options (can be combined — static skip applies first, then auto-trim refines):
    --skip-start N     Unconditionally skip first N pages (Option 1)
    --skip-end N       Unconditionally skip last N pages (Option 1)
    --auto-trim        Heuristically detect and skip non-content pages at boundaries (Option 2)
    --auto-trim-window N  How many pages to scan at each boundary for auto-trim (default: 20)

Examples:
    python pdf_chunker.py book.pdf --category fizjologia
    python pdf_chunker.py book.pdf --category anatomia --skip-start 12 --skip-end 5
    python pdf_chunker.py book.pdf --category anatomia --auto-trim
    python pdf_chunker.py book.pdf --category anatomia --skip-start 5 --auto-trim
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

# This script prints raw snippets of extracted PDF text (see score_page /
# find_content_boundaries below) while scanning for skip-worthy pages. On
# Windows, a subprocess's stdout defaults to the console's legacy codepage
# (cp1252/cp1250/cp852 depending on locale) instead of UTF-8, so any
# non-ASCII character in the source PDF — arrows, smart quotes, Polish
# diacritics in some codepages, etc. — can crash print() with
# UnicodeEncodeError and take down the whole chunking subprocess. Force UTF-8
# on both streams so extracted text is always printable regardless of the
# invoking console's codepage. (errors="replace" so a genuinely unprintable
# byte degrades to "?" instead of crashing again.)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("Error: pypdf is not installed. Install it with: pip install pypdf")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: pymupdf is not installed. Install it with: pip install pymupdf")
    sys.exit(1)

# Text extraction uses PyMuPDF (fitz), not pypdf. pypdf's extract_text() is
# still used to split/write the per-chunk PDF files (that only needs page
# structure, not text decoding), but its text extraction badly mishandles
# Type3 fonts — common in these Polish medical textbooks for title pages,
# headers, or in some source files the entire body text — silently emitting
# mostly '�' replacement characters instead of raising an error. That
# went undetected through extraction *and* validation (both stages "succeed"
# on garbage input) and only surfaced when someone read the merged output.
# PyMuPDF's get_text() decodes the same Type3 glyph programs correctly.


# ---------------------------------------------------------------------------
# Polish medical textbook heuristics — skip-indicator keywords
# These appear on structural pages (ToC, bibliography, copyright, index)
# ---------------------------------------------------------------------------
SKIP_KEYWORDS = [
    "spis treści",
    "spis rysunków",
    "spis tabel",
    "bibliografia",
    "piśmiennictwo",
    "wykaz literatury",
    "literatura",
    "indeks",
    "skorowidz",
    "©",
    "isbn",
    "issn",
    "all rights reserved",
    "wszelkie prawa",
    "wydawnictwo",
    "druk i oprawa",
    "przedmowa",
    "wstęp do wydania",  # preface pages — borderline, included in window only
]

# A ToC line looks like: "Some Chapter Title ........... 42"
TOC_LINE_PATTERN = re.compile(r"\.{4,}\s*\d+\s*$")


def sanitize_bookname(name: str) -> str:
    name = name.lower().replace(" ", "_")
    name = re.sub(r"[^a-z0-9_-]", "", name)
    return name


def get_page_range_str(start_page: int, end_page: int) -> str:
    return f"{start_page}-{end_page}"


def extract_page_text(fitz_doc: "fitz.Document", page_idx: int) -> str:
    """Extract text for a single 0-indexed page via PyMuPDF."""
    try:
        return fitz_doc[page_idx].get_text() or ""
    except Exception:
        return f"[Page {page_idx + 1} extraction failed]"


def extract_text_from_pages(fitz_doc: "fitz.Document", start_idx: int, end_idx: int) -> str:
    texts = [extract_page_text(fitz_doc, i) for i in range(start_idx, end_idx)]
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Option 2 — Heuristic page scorer
# ---------------------------------------------------------------------------


def score_page(text: str) -> dict:
    """
    Score a single page's text for educational content quality.

    Returns a dict with:
        'chars'           : total character count
        'lines'           : total line count
        'avg_line_len'    : average characters per line
        'short_line_ratio': fraction of lines with <35 chars (ToC indicator)
        'toc_line_count'  : lines matching "Chapter ......... 42" pattern
        'keyword_hits'    : count of skip-indicator keywords found
        'is_content'      : True if page likely contains educational content
        'reason'          : human-readable explanation of the decision
    """
    chars = len(text.strip())
    lines = [line for line in text.splitlines() if line.strip()]
    line_count = len(lines)
    avg_line_len = (chars / line_count) if line_count > 0 else 0

    short_lines = sum(1 for line in lines if len(line.strip()) < 35)
    short_line_ratio = (short_lines / line_count) if line_count > 0 else 0

    toc_lines = sum(1 for line in lines if TOC_LINE_PATTERN.search(line))

    text_lower = text.lower()
    keyword_hits = sum(1 for kw in SKIP_KEYWORDS if kw in text_lower)

    # Decision logic — ordered from most to least decisive
    if chars < 150:
        is_content = False
        reason = f"nearly blank ({chars} chars)"
    elif toc_lines >= 3:
        is_content = False
        reason = f"table of contents ({toc_lines} ToC-style lines)"
    elif short_line_ratio > 0.72 and chars < 1800:
        is_content = False
        reason = (
            f"dense short lines ({short_line_ratio:.0%} short, {chars} chars) — likely ToC/index"
        )
    elif keyword_hits >= 2:
        is_content = False
        reason = f"structural keywords ({keyword_hits} hits)"
    elif chars < 400 and keyword_hits >= 1:
        is_content = False
        reason = f"sparse content with structural keyword ({chars} chars)"
    else:
        is_content = True
        reason = f"content page ({chars} chars, {short_line_ratio:.0%} short lines)"

    return {
        "chars": chars,
        "lines": line_count,
        "avg_line_len": round(avg_line_len, 1),
        "short_line_ratio": round(short_line_ratio, 3),
        "toc_line_count": toc_lines,
        "keyword_hits": keyword_hits,
        "is_content": is_content,
        "reason": reason,
    }


def detect_content_boundaries(
    fitz_doc: "fitz.Document",
    start_idx: int,
    end_idx: int,
    window: int = 20,
) -> tuple[int, int]:
    """
    Scan the first `window` and last `window` pages within [start_idx, end_idx)
    and return (first_content_idx, last_content_idx) — both 0-indexed, inclusive.

    Rules:
    - From the front: find the first page that scores is_content=True.
      Keep all pages from there onward (we don't skip isolated non-content
      pages in the middle of a book).
    - From the back: find the last page that scores is_content=True.
    - If no content pages found in a window, don't trim (return original boundary).
    """
    total = end_idx - start_idx
    scan_front = min(window, total)
    scan_back = min(window, total)

    # --- Scan front window ---
    first_content = start_idx  # default: no trim
    for i in range(start_idx, start_idx + scan_front):
        text = extract_page_text(fitz_doc, i)
        result = score_page(text)
        page_num = i + 1
        status = "CONTENT" if result["is_content"] else "SKIP   "
        print(f"  [front] page {page_num:4d}: {status} — {result['reason']}")
        if result["is_content"]:
            first_content = i
            break
    else:
        print(f"  [front] no content found in first {scan_front} pages — keeping original start")

    # --- Scan back window ---
    last_content = end_idx - 1  # default: no trim
    for i in range(end_idx - 1, end_idx - 1 - scan_back, -1):
        text = extract_page_text(fitz_doc, i)
        result = score_page(text)
        page_num = i + 1
        status = "CONTENT" if result["is_content"] else "SKIP   "
        print(f"  [back]  page {page_num:4d}: {status} — {result['reason']}")
        if result["is_content"]:
            last_content = i
            break
    else:
        print(f"  [back] no content found in last {scan_back} pages — keeping original end")

    return first_content, last_content


# ---------------------------------------------------------------------------
# Core chunker
# ---------------------------------------------------------------------------


def chunk_pdf(
    pdf_path: Path,
    category: str,
    chunk_size: int = 10,
    skip_start: int = 0,
    skip_end: int = 0,
    auto_trim: bool = False,
    auto_trim_window: int = 20,
) -> dict:
    """
    Split a PDF into fixed-size chunks with optional intelligent trimming.

    Trimming order:
      1. Apply --skip-start / --skip-end (hard, unconditional)
      2. If --auto-trim: heuristically refine boundaries within the remaining range
      3. Chunk the resulting page range
    """
    script_dir = Path(__file__).resolve().parent
    ecosystem_dir = script_dir.parent
    wesa_dir = ecosystem_dir.parent
    extractor_dir = wesa_dir / "extractor"
    chunks_dir = extractor_dir / "chunks"
    data_dir = extractor_dir / "data"

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    bookname = sanitize_bookname(pdf_path.stem)
    category_chunks_dir = chunks_dir / category
    category_chunks_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    fitz_doc = fitz.open(str(pdf_path))

    print(f"Processing: {pdf_path.name}")
    print(f"Total pages: {total_pages}")
    print(f"Chunk size:  {chunk_size}")
    print(f"Category:    {category}")

    # --- Step 1: Static trim ---
    range_start = skip_start  # 0-indexed, inclusive
    range_end = total_pages - skip_end  # 0-indexed, exclusive

    if range_start >= range_end:
        raise ValueError(
            f"Static trim leaves no pages: skip_start={skip_start}, "
            f"skip_end={skip_end}, total={total_pages}"
        )

    if skip_start or skip_end:
        print(
            f"Static trim:  pages {range_start + 1}–{range_end} "
            f"(skipped {skip_start} front, {skip_end} back)"
        )

    # --- Step 2: Heuristic trim (auto-trim) ---
    trim_report = None
    if auto_trim:
        print(f"Auto-trim:   scanning up to {auto_trim_window} pages at each boundary...")
        first_content, last_content = detect_content_boundaries(
            fitz_doc, range_start, range_end, window=auto_trim_window
        )
        skipped_front = first_content - range_start
        skipped_back = (range_end - 1) - last_content
        range_start = first_content
        range_end = last_content + 1  # exclusive

        trim_report = {
            "auto_trim_skipped_front": skipped_front,
            "auto_trim_skipped_back": skipped_back,
            "content_start_page": first_content + 1,  # 1-indexed for humans
            "content_end_page": last_content + 1,
        }
        print(
            f"Auto-trim result: content pages {first_content + 1}–{last_content + 1} "
            f"(trimmed {skipped_front} front, {skipped_back} back)"
        )

    # --- Final range ---
    content_pages = range_end - range_start
    num_chunks = (content_pages + chunk_size - 1) // chunk_size

    print(
        f"Content range: pages {range_start + 1}–{range_end}  ({content_pages} pages → {num_chunks} chunks)"
    )
    print("-" * 50)

    # --- Build manifest ---
    run_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()

    manifest = {
        "run_id": run_id,
        "book": bookname,
        "category": category,
        "mode": "generate",
        "source_pdf": str(pdf_path.relative_to(wesa_dir)),
        "total_pages": total_pages,
        "content_pages": content_pages,
        "content_start_page": range_start + 1,
        "content_end_page": range_end,
        "skipped_front": range_start,
        "skipped_back": total_pages - range_end,
        "total_chunks": num_chunks,
        "validated_count": 0,
        "merge_status": "pending",
        "created_at": created_at,
        "updated_at": created_at,
        "chunks": [],
    }

    if trim_report:
        manifest["auto_trim"] = trim_report

    # --- Process chunks ---
    for chunk_idx in range(num_chunks):
        chunk_start_0idx = range_start + (chunk_idx * chunk_size)
        chunk_end_0idx = min(range_start + ((chunk_idx + 1) * chunk_size), range_end)

        start_1idx = chunk_start_0idx + 1
        end_1idx = chunk_end_0idx

        page_range_str = get_page_range_str(start_1idx, end_1idx)
        chunk_id = f"{bookname}_page_{page_range_str}"

        chunk_pdf_path = category_chunks_dir / f"{chunk_id}.pdf"
        chunk_txt_path = category_chunks_dir / f"{chunk_id}.txt"

        writer = PdfWriter()
        for page_idx in range(chunk_start_0idx, chunk_end_0idx):
            writer.add_page(reader.pages[page_idx])

        with open(chunk_pdf_path, "wb") as f:
            writer.write(f)

        text_content = extract_text_from_pages(fitz_doc, chunk_start_0idx, chunk_end_0idx)
        char_count = len(text_content)
        garbled_ratio = (text_content.count("�") / char_count) if char_count else 0.0
        if garbled_ratio > 0.05:
            print(
                f"  Warning: chunk {chunk_id} is {garbled_ratio:.0%} replacement "
                f"characters — extraction likely still broken for this page range.",
                file=sys.stderr,
            )

        with open(chunk_txt_path, "w", encoding="utf-8") as f:
            f.write(text_content)

        chunk_entry = {
            "id": chunk_id,
            "pdf": f"extractor/chunks/{category}/{chunk_id}.pdf",
            "txt": f"extractor/chunks/{category}/{chunk_id}.txt",
            "pages": page_range_str,
            "status": "pending",
            "extract_task_id": None,
            "validate_task_id": None,
            "raw_json": f"extractor/outputs/{chunk_id}_raw.json",
            "validated_json": f"extractor/outputs/{chunk_id}_validated.json",
        }
        manifest["chunks"].append(chunk_entry)

        print(f"Chunk {chunk_idx + 1}/{num_chunks}: pages {page_range_str}, {char_count} chars")

    # --- Write manifest ---
    manifest_path = data_dir / f"{bookname}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("-" * 50)
    print(f"Manifest: {manifest_path}")
    print(f"Chunks:   {category_chunks_dir}")

    fitz_doc.close()

    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Split a PDF into fixed-size chunks with intelligent boundary detection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Trimming modes (combinable):
  --skip-start N   Hard skip: ignore first N pages unconditionally
  --skip-end N     Hard skip: ignore last N pages unconditionally
  --auto-trim      Heuristic: scan boundaries and skip non-content pages
                   (blank, ToC, bibliography, index, copyright)

Examples:
  python pdf_chunker.py book.pdf --category fizjologia
  python pdf_chunker.py book.pdf --category anatomia --skip-start 12 --skip-end 5
  python pdf_chunker.py book.pdf --category anatomia --auto-trim
  python pdf_chunker.py book.pdf --category anatomia --skip-start 5 --auto-trim
  python pdf_chunker.py book.pdf --category anatomia --auto-trim --auto-trim-window 30
        """,
    )

    parser.add_argument("pdf_path", type=str, help="Path to the source PDF file")
    parser.add_argument(
        "--category", type=str, required=True, help="Wolfmed subject category (e.g. 'fizjologia')"
    )
    parser.add_argument("--chunk-size", type=int, default=10, help="Pages per chunk (default: 10)")
    parser.add_argument(
        "--skip-start", type=int, default=0, help="Unconditionally skip first N pages"
    )
    parser.add_argument("--skip-end", type=int, default=0, help="Unconditionally skip last N pages")
    parser.add_argument(
        "--auto-trim",
        action="store_true",
        help="Heuristically detect and skip non-content pages at boundaries",
    )
    parser.add_argument(
        "--auto-trim-window",
        type=int,
        default=20,
        help="Pages to scan at each boundary for auto-trim (default: 20)",
    )

    args = parser.parse_args()
    pdf_path = Path(args.pdf_path)

    try:
        manifest = chunk_pdf(
            pdf_path=pdf_path,
            category=args.category,
            chunk_size=args.chunk_size,
            skip_start=args.skip_start,
            skip_end=args.skip_end,
            auto_trim=args.auto_trim,
            auto_trim_window=args.auto_trim_window,
        )
        print(
            f"\nDone. {manifest['total_chunks']} chunks from {manifest['content_pages']} content pages "
            f"(of {manifest['total_pages']} total)."
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing PDF: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
