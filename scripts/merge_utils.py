"""
merge_utils.py — Wolfmed JSON Question Bank Merger
Part of the WESA Wolfmed content pipeline.

Merges multiple validated chunk JSONs into a single clean question bank.
Pure Python — no LLM involved. Deterministic.

Called by wolfmed_intake.py when all chunks of a book are validated.

Usage (standalone):
    python scripts/merge_utils.py --manifest extractor/data/fizjologia_manifest.json
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"

DONE_DIR = EXTRACTOR_DIR / "done"


def _normalize_question(text: str) -> str:
    """Normalize question text for deduplication (lowercase, collapse whitespace)."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _is_valid_question(entry: dict) -> bool:
    """Check that an entry has the required structure."""
    try:
        data = entry.get("data", {})
        q = data.get("question", "").strip()
        answers = data.get("answers", [])
        if not q or len(q) < 5:
            return False
        if len(answers) < 2:
            return False
        correct_count = sum(1 for a in answers if a.get("isCorrect") is True)
        if correct_count != 1:
            return False
        return True
    except Exception:
        return False


def merge_book(manifest: dict, output_path: Path | None = None) -> Path:
    """
    Merge all validated chunk JSONs from a manifest into one clean file.

    Args:
        manifest: manifest dict (from manifest_checker / wolfmed_intake)
        output_path: override output path (default: extractor/done/<book>_all_merged.json)

    Returns:
        Path to the merged output file
    """
    book = manifest["book"]
    category = manifest.get("category", book)

    DONE_DIR.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        output_path = DONE_DIR / f"{book}_all_merged.json"

    print(f"[merge_utils] Merging question bank for: {book}")

    all_questions = []
    seen_questions: set[str] = set()
    loaded_chunks = 0
    skipped_chunks = 0
    duplicate_count = 0
    invalid_count = 0

    for chunk in manifest["chunks"]:
        validated_json = chunk.get("validated_json")
        if not validated_json:
            # Fall back to raw output if no validated version
            validated_json = chunk.get("output_json")

        if not validated_json:
            print(f"[merge_utils]   WARNING: No output for chunk {chunk['id']} — skipping")
            skipped_chunks += 1
            continue

        json_path = Path(validated_json)
        if not json_path.exists():
            print(f"[merge_utils]   WARNING: File not found: {json_path} — skipping")
            skipped_chunks += 1
            continue

        try:
            chunk_data = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(chunk_data, list):
                print(f"[merge_utils]   WARNING: {json_path.name} is not a JSON array — skipping")
                skipped_chunks += 1
                continue

            chunk_valid = 0
            for entry in chunk_data:
                if not _is_valid_question(entry):
                    invalid_count += 1
                    continue

                q_key = _normalize_question(entry["data"]["question"])
                if q_key in seen_questions:
                    duplicate_count += 1
                    continue

                seen_questions.add(q_key)

                # Ensure valid UUID
                if not entry.get("id") or len(str(entry.get("id", ""))) < 10:
                    entry["id"] = str(uuid.uuid4())

                # Ensure meta is consistent
                if "meta" not in entry:
                    entry["meta"] = {}
                entry["meta"]["course"] = entry["meta"].get("course", "pielegniarstwo")
                entry["meta"]["category"] = category

                all_questions.append(entry)
                chunk_valid += 1

            print(f"[merge_utils]   {json_path.name}: {chunk_valid} valid questions loaded")
            loaded_chunks += 1

        except json.JSONDecodeError as e:
            print(f"[merge_utils]   ERROR parsing {json_path.name}: {e} — skipping")
            skipped_chunks += 1

    print(f"\n[merge_utils] Summary:")
    print(f"  Chunks loaded:    {loaded_chunks} / {len(manifest['chunks'])}")
    print(f"  Chunks skipped:   {skipped_chunks}")
    print(f"  Total questions:  {len(all_questions)}")
    print(f"  Duplicates removed: {duplicate_count}")
    print(f"  Invalid removed:  {invalid_count}")

    if not all_questions:
        raise ValueError(f"No valid questions found for book: {book}")

    # Write merged output
    output_path.write_text(
        json.dumps(all_questions, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n[merge_utils] Merged file written: {output_path}")
    print(f"[merge_utils] Final question count: {len(all_questions)}")

    return output_path


def merge_from_manifest_file(manifest_path: Path) -> Path:
    """Load manifest from file and run merge."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return merge_book(manifest)


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge validated Wolfmed JSON chunks into one question bank")
    parser.add_argument("--manifest", required=True, help="Path to book manifest JSON")
    parser.add_argument("--output", default=None, help="Override output file path")

    args = parser.parse_args()
    manifest_path = Path(args.manifest)

    if not manifest_path.exists():
        print(f"ERROR: Manifest not found: {manifest_path}")
        sys.exit(1)

    output = merge_from_manifest_file(manifest_path)
    print(f"\nDone. Output: {output}")
