#!/usr/bin/env python3
"""
PDF Chunker - Splits PDFs into fixed 10-page chunks with text extraction.

Usage:
    python scripts/pdf_chunker.py <pdf_path> --category <category> [--chunk-size N]
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Import pypdf with graceful fallback
try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    print("Error: pypdf is not installed. Install it with: pip install pypdf")
    sys.exit(1)


def sanitize_bookname(name: str) -> str:
    """Convert book name to safe filename: lowercase, underscores, [a-z0-9_-] only."""
    name = name.lower().replace(" ", "_")
    name = re.sub(r'[^a-z0-9_-]', '', name)
    return name


def get_page_range_str(start_page: int, end_page: int) -> str:
    """Return human-readable page range string (1-indexed)."""
    return f"{start_page}-{end_page}"


def extract_text_from_pages(reader: PdfReader, start_idx: int, end_idx: int) -> str:
    """
    Extract text from a range of pages (0-indexed, inclusive start, exclusive end).
    Returns concatenated text with placeholder for failed pages.
    """
    texts = []
    for i in range(start_idx, end_idx):
        try:
            page = reader.pages[i]
            text = page.extract_text() or ""
            texts.append(text)
        except Exception:
            page_num = i + 1  # Convert to 1-indexed for display
            texts.append(f"[Page {page_num} extraction failed]")
    return "\n".join(texts)


def chunk_pdf(pdf_path: Path, category: str, chunk_size: int = 10) -> dict:
    """
    Split a PDF into fixed-size chunks, extract text, and create manifest.
    
    Args:
        pdf_path: Path to source PDF file
        category: Wolfmed subject category (required, no auto-detection)
        chunk_size: Pages per chunk (default: 10)
    
    Returns:
        Manifest dictionary
    """
    # Resolve directory structure relative to this script
    script_dir = Path(__file__).resolve().parent
    ecosystem_dir = script_dir.parent
    wesa_dir = ecosystem_dir.parent
    extractor_dir = wesa_dir / "extractor"
    chunks_dir = extractor_dir / "chunks"
    data_dir = extractor_dir / "data"
    
    # Validate input file
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    # Generate book name
    bookname = sanitize_bookname(pdf_path.stem)
    
    # Create category subdirectory
    category_chunks_dir = chunks_dir / category
    category_chunks_dir.mkdir(parents=True, exist_ok=True)
    
    # Read PDF
    reader = PdfReader(str(pdf_path))
    total_pages = len(reader.pages)
    
    print(f"Processing: {pdf_path.name}")
    print(f"Total pages: {total_pages}")
    print(f"Chunk size: {chunk_size}")
    print(f"Category: {category}")
    print("-" * 50)
    
    # Calculate chunks
    num_chunks = (total_pages + chunk_size - 1) // chunk_size
    
    # Build manifest structure
    run_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    
    manifest = {
        "run_id": run_id,
        "book": bookname,
        "category": category,
        "mode": "generate",
        "source_pdf": str(pdf_path.relative_to(wesa_dir)),
        "total_chunks": num_chunks,
        "validated_count": 0,
        "merge_status": "pending",
        "created_at": created_at,
        "updated_at": created_at,
        "chunks": []
    }
    
    # Process each chunk
    for chunk_idx in range(num_chunks):
        start_page_0idx = chunk_idx * chunk_size
        end_page_0idx = min((chunk_idx + 1) * chunk_size, total_pages)
        
        # Convert to 1-indexed for display and naming
        start_page_1idx = start_page_0idx + 1
        end_page_1idx = end_page_0idx
        
        page_range_str = get_page_range_str(start_page_1idx, end_page_1idx)
        chunk_id = f"{bookname}_page_{page_range_str}"
        
        # Output paths
        chunk_pdf_path = category_chunks_dir / f"{chunk_id}.pdf"
        chunk_txt_path = category_chunks_dir / f"{chunk_id}.txt"
        
        # Create chunk PDF
        writer = PdfWriter()
        for page_idx in range(start_page_0idx, end_page_0idx):
            writer.add_page(reader.pages[page_idx])
        
        with open(chunk_pdf_path, "wb") as f:
            writer.write(f)
        
        # Extract text
        text_content = extract_text_from_pages(reader, start_page_0idx, end_page_0idx)
        char_count = len(text_content)
        
        with open(chunk_txt_path, "w", encoding="utf-8") as f:
            f.write(text_content)
        
        # Build chunk entry for manifest
        chunk_entry = {
            "id": chunk_id,
            "pdf": f"extractor/chunks/{category}/{chunk_id}.pdf",
            "txt": f"extractor/chunks/{category}/{chunk_id}.txt",
            "pages": page_range_str,
            "status": "pending",
            "extract_task_id": None,
            "validate_task_id": None,
            "raw_json": f"extractor/outputs/{chunk_id}_raw.json",
            "validated_json": f"extractor/outputs/{chunk_id}_validated.json"
        }
        manifest["chunks"].append(chunk_entry)
        
        # Print progress
        print(f"Chunk {chunk_idx + 1}/{num_chunks}: pages {page_range_str}, {char_count} chars")
    
    # Write manifest
    manifest_path = data_dir / f"{bookname}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    
    print("-" * 50)
    print(f"Manifest written to: {manifest_path}")
    print(f"Chunks written to: {category_chunks_dir}")
    
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Split a PDF into fixed 10-page chunks with text extraction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf_chunker.py book.pdf --category fizjologia
  python pdf_chunker.py book.pdf --category anatomia --chunk-size 15
        """
    )
    
    parser.add_argument(
        "pdf_path",
        type=str,
        help="Path to the source PDF file"
    )
    
    parser.add_argument(
        "--category",
        type=str,
        required=True,
        help="Wolfmed subject category string (e.g. 'fizjologia'). Required, no auto-detection."
    )
    
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10,
        help="Pages per chunk (default: 10)"
    )
    
    args = parser.parse_args()
    
    # Convert pdf_path to Path object
    pdf_path = Path(args.pdf_path)
    
    try:
        manifest = chunk_pdf(pdf_path, args.category, args.chunk_size)
        print(f"\nProcessing complete. Created {manifest['total_chunks']} chunks.")
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing PDF: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()