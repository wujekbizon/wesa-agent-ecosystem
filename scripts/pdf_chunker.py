"""
pdf_chunker.py — Wolfmed PDF Splitter
Part of the WESA Wolfmed content pipeline.

Splits large PDFs into manageable chunks and extracts text for EX1 agent processing.
Creates a manifest file tracking all chunks and their status.

Usage:
    python scripts/pdf_chunker.py <pdf_path> [--chunk-size 60] [--category fizjologia] [--mode generate]

Called by wolfmed_intake.py — do not run standalone unless testing.
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("[pdf_chunker] ERROR: pypdf not installed. Run: pip install pypdf --break-system-packages")
    sys.exit(1)

# ─── Paths (resolve relative to this script's location) ───────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"

CHUNKS_DIR = EXTRACTOR_DIR / "chunks"
DATA_DIR = EXTRACTOR_DIR / "data"

DEFAULT_CHUNK_SIZE = 60   # pages per chunk
MIN_CHAPTER_PAGES = 5     # ignore chapter boundaries that produce < 5 pages
MAX_CHAPTER_PAGES = 80    # if a chapter is > 80 pages, subdivide it

# ─── Auto-detect extraction mode ───────────────────────────────────────────────
# Heuristics based on keyword density in first N pages of text
QA_KEYWORDS = ["?", "a)", "b)", "c)", "d)", "prawidłowa", "odpowiedź", "klucz"]
PROCEDURE_KEYWORDS = ["CZYNNOŚCI PRZYGOTOWAWCZE", "CZYNNOŚCI WŁAŚCIWE", "SUMA PUNKTÓW",
                      "Poziom zaliczenia", "PYTANIA SPRAWDZAJĄCE"]

def detect_mode(text_sample: str) -> str:
    """Heuristically detect extraction mode from PDF text sample."""
    sample_upper = text_sample.upper()
    # Procedure checklists take priority
    if any(kw.upper() in sample_upper for kw in PROCEDURE_KEYWORDS):
        return "extract_procedures"
    # High density of question marks + answer options = extract mode
    question_count = text_sample.count("?")
    if question_count > 20:
        return "extract"
    return "generate"


def extract_text_from_pages(reader: PdfReader, start: int, end: int) -> str:
    """Extract text from a page range (0-indexed, end exclusive)."""
    texts = []
    for page_num in range(start, min(end, len(reader.pages))):
        try:
            page_text = reader.pages[page_num].extract_text() or ""
            texts.append(page_text)
        except Exception as e:
            texts.append(f"[Page {page_num + 1} extraction failed: {e}]")
    return "\n\n".join(texts)


def get_chapter_boundaries(reader: PdfReader) -> list[int] | None:
    """
    Try to extract chapter start pages from PDF bookmarks/outlines.
    Returns sorted list of 0-indexed page numbers where chapters start,
    or None if no useful outline found.
    """
    try:
        outline = reader.outline
        if not outline:
            return None

        page_numbers = []

        def collect_pages(items):
            for item in items:
                if isinstance(item, list):
                    collect_pages(item)
                elif hasattr(item, "page"):
                    try:
                        page_idx = reader.get_page_number(item.page)
                        page_numbers.append(page_idx)
                    except Exception:
                        pass

        collect_pages(outline)

        if len(page_numbers) < 2:
            return None

        boundaries = sorted(set(page_numbers))
        return boundaries

    except Exception:
        return None


def split_by_chapters(reader: PdfReader, total_pages: int,
                       chapter_starts: list[int]) -> list[tuple[int, int]]:
    """
    Convert chapter start page numbers into (start, end) page ranges.
    Merges very short chapters, subdivides very long ones.
    Returns list of (start_page, end_page) tuples (0-indexed, end exclusive).
    """
    # Build raw chapter ranges
    ranges = []
    for i, start in enumerate(chapter_starts):
        end = chapter_starts[i + 1] if i + 1 < len(chapter_starts) else total_pages
        ranges.append((start, end))

    # Merge short chapters into the next one
    merged = []
    buffer_start = None
    buffer_end = None

    for start, end in ranges:
        length = end - start
        if buffer_start is None:
            buffer_start, buffer_end = start, end
        else:
            if (buffer_end - buffer_start) < MIN_CHAPTER_PAGES:
                buffer_end = end  # merge into buffer
            else:
                merged.append((buffer_start, buffer_end))
                buffer_start, buffer_end = start, end

    if buffer_start is not None:
        merged.append((buffer_start, buffer_end))

    # Subdivide oversized chunks
    final = []
    for start, end in merged:
        length = end - start
        if length <= MAX_CHAPTER_PAGES:
            final.append((start, end))
        else:
            # Split into fixed-size sub-chunks
            pos = start
            while pos < end:
                chunk_end = min(pos + DEFAULT_CHUNK_SIZE, end)
                final.append((pos, chunk_end))
                pos = chunk_end

    return final


def split_by_fixed_size(total_pages: int, chunk_size: int) -> list[tuple[int, int]]:
    """Fall back to fixed-size page ranges."""
    ranges = []
    for start in range(0, total_pages, chunk_size):
        end = min(start + chunk_size, total_pages)
        ranges.append((start, end))
    return ranges


def chunk_pdf(
    pdf_path: Path,
    category: str = "",
    mode: str = "auto",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict:
    """
    Main entry point. Splits a PDF and returns a manifest dict.

    Args:
        pdf_path: Path to the source PDF
        category: Wolfmed subject category (e.g. 'fizjologia'). Auto-detected if empty.
        mode: 'generate', 'extract', 'extract_procedures', or 'auto'
        chunk_size: Number of pages per chunk (used when chapter-aware splitting unavailable)

    Returns:
        manifest dict (also written to extractor/data/<book>_manifest.json)
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    book_name = pdf_path.stem
    # Sanitize book name for use in file names
    book_name_safe = re.sub(r"[^\w\-]", "_", book_name).lower()

    print(f"[pdf_chunker] Processing: {pdf_path.name}")

    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    print(f"[pdf_chunker] Total pages: {total_pages}")

    # ── Auto-detect category from filename if not provided ──
    if not category:
        # Guess from the filename
        fname_lower = book_name.lower()
        known = ["fizjologia", "anatomia", "biochemia", "mikrobiologia",
                 "socjologia", "pielegniarstwo", "prawo", "farmakologia",
                 "opiekun", "patologia"]
        category = next((k for k in known if k in fname_lower), book_name_safe)

    # ── Determine splitting strategy ──
    chapter_starts = get_chapter_boundaries(reader)
    if chapter_starts and len(chapter_starts) > 1:
        print(f"[pdf_chunker] Chapter-aware splitting: {len(chapter_starts)} chapters found")
        page_ranges = split_by_chapters(reader, total_pages, chapter_starts)
    else:
        print(f"[pdf_chunker] Fixed-size splitting: {chunk_size} pages per chunk")
        page_ranges = split_by_fixed_size(total_pages, chunk_size)

    print(f"[pdf_chunker] Will create {len(page_ranges)} chunks")

    # ── Create output directories ──
    book_chunks_dir = CHUNKS_DIR / book_name_safe
    book_chunks_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Sample text for mode detection ──
    sample_text = extract_text_from_pages(reader, 0, min(5, total_pages))
    if mode == "auto":
        mode = detect_mode(sample_text)
        print(f"[pdf_chunker] Auto-detected mode: {mode}")

    # ── Split PDF and extract text ──
    chunks = []
    for i, (start_page, end_page) in enumerate(page_ranges):
        chunk_num = str(i + 1).zfill(3)
        chunk_stem = f"{book_name_safe}_chunk_{chunk_num}"

        chunk_pdf_path = book_chunks_dir / f"{chunk_stem}.pdf"
        chunk_txt_path = book_chunks_dir / f"{chunk_stem}.txt"

        # Write chunk PDF
        writer = PdfWriter()
        for page_idx in range(start_page, end_page):
            writer.add_page(reader.pages[page_idx])

        with open(chunk_pdf_path, "wb") as f:
            writer.write(f)

        # Extract text from chunk
        chunk_text = extract_text_from_pages(reader, start_page, end_page)
        chunk_txt_path.write_text(chunk_text, encoding="utf-8")

        chunk_entry = {
            "id": chunk_stem,
            "chunk_num": i + 1,
            "pdf": str(chunk_pdf_path),
            "txt": str(chunk_txt_path),
            "pages": f"{start_page + 1}-{end_page}",
            "page_count": end_page - start_page,
            "status": "pending",           # pending → queued → extracted → validated → done
            "task_id": None,
            "output_json": None,
            "validated_json": None,
        }
        chunks.append(chunk_entry)
        print(f"[pdf_chunker]   Chunk {chunk_num}: pages {start_page + 1}-{end_page} "
              f"({end_page - start_page} pages, {len(chunk_text)} chars)")

    # ── Write manifest ──
    manifest = {
        "book": book_name_safe,
        "source_pdf": str(pdf_path),
        "category": category,
        "mode": mode,
        "total_pages": total_pages,
        "total_chunks": len(chunks),
        "chunks": chunks,
        "merge_status": "pending",    # pending → queued → done → failed
        "merged_output": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    manifest_path = DATA_DIR / f"{book_name_safe}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[pdf_chunker] Manifest written: {manifest_path}")

    return manifest


# ─── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split a PDF into chunks for Wolfmed extraction pipeline")
    parser.add_argument("pdf", help="Path to input PDF")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Pages per chunk (default: 60)")
    parser.add_argument("--category", default="", help="Wolfmed category (auto-detected if empty)")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "generate", "extract", "extract_procedures"],
                        help="Extraction mode (default: auto)")

    args = parser.parse_args()
    manifest = chunk_pdf(
        pdf_path=Path(args.pdf),
        category=args.category,
        mode=args.mode,
        chunk_size=args.chunk_size,
    )
    print(f"\n[pdf_chunker] Done. {manifest['total_chunks']} chunks created.")
    print(f"[pdf_chunker] Manifest: {DATA_DIR / (manifest['book'] + '_manifest.json')}")
