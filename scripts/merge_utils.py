import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Path Resolution (WESA Standard: No traversing __file__.parent chains)
# We assume the script is located in agent-ecosystem/scripts/
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"
DONE_DIR = EXTRACTOR_DIR / "done"
DATA_DIR = EXTRACTOR_DIR / "data"
WOLFMED_DB = DATA_DIR / "wolfmed.db"
UUID_REGISTRY_JSON = DATA_DIR / "uuid_registry.json"
OUTPUTS_DIR = ECOSYSTEM_DIR / "extractor" / "outputs"


def get_db_connection():
    """Returns a connection to the SQLite registry."""
    conn = sqlite3.connect(WOLFMED_DB)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db():
    """Ensures table exists and migrates legacy JSON registry if needed."""
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uuid_registry (
                uuid TEXT PRIMARY KEY,
                category TEXT,
                question_hash TEXT,
                registered_at TEXT
            )
        """)

        # Check if migration is needed
        cursor = conn.execute("SELECT COUNT(*) FROM uuid_registry")
        count = cursor.fetchone()[0]

        if count == 0 and UUID_REGISTRY_JSON.exists():
            with open(UUID_REGISTRY_JSON) as f:
                legacy_uuids = json.load(f)

            now = datetime.now().isoformat()
            data_to_insert = [(str(u), "legacy", "", now) for u in legacy_uuids]
            if data_to_insert:
                conn.executemany(
                    "INSERT INTO uuid_registry (uuid, category, question_hash, registered_at) VALUES (?, ?, ?, ?)",
                    data_to_insert,
                )
            conn.commit()
    finally:
        conn.close()


def validate_entry(entry: dict) -> bool:
    """Performs structural and content validation on a single question entry."""
    try:
        data = entry.get("data", {})
        question = data.get("question")
        answers = data.get("answers", [])

        if not isinstance(question, str) or len(question.strip()) < 5:
            return False

        if not isinstance(answers, list) or len(answers) != 4:
            return False

        correct_count = sum(1 for a in answers if a.get("isCorrect") is True)
        if correct_count != 1:
            return False

        return True
    except Exception as e:
        print(f"Warning: Entry validation failed — {e}", file=sys.stderr)
        return False


def merge_book(manifest: dict, output_path: Path | None = None, overwrite: bool = False) -> Path:
    """
    Merges all validated JSON chunks for a book into the category's question bank.

    The output file is shared across every book in a category — wolfmed_intake.py
    triggers this once per book as soon as that book's chunks finish validating, and
    multiple books in the same category (e.g. promocja-zdrowia's 35 source PDFs)
    all write into the same `{category}_all_merged.json`. So by default this is
    additive: existing entries already in the target file are loaded and preserved,
    and this book's new, deduplicated entries are appended on top.

    Pass overwrite=True to discard whatever is already in the target file and
    rebuild it from just this book's chunks — manual recovery / full-category-reset
    only, NOT the normal per-book path (it will drop every other book already merged
    into that file).
    """
    initialize_db()
    category = manifest.get("category", "unknown")
    book_name = manifest.get("book", manifest.get("book_name", "unknown"))

    # Ensure output directory exists
    DONE_DIR.mkdir(parents=True, exist_ok=True)

    target_output = output_path or (DONE_DIR / f"{category}_all_merged.json")

    all_entries = []
    seen_questions = set()  # For deduplication by text

    if target_output.exists() and not overwrite:
        with open(target_output, encoding="utf-8") as f:
            all_entries = json.load(f)
        for existing_entry in all_entries:
            q_norm = existing_entry.get("data", {}).get("question", "").strip().lower()
            if q_norm:
                seen_questions.add(q_norm)

    # 1. Load all *_validated.json files
    pattern = f"{book_name}_page_*_validated.json"
    input_files = list(OUTPUTS_DIR.glob(pattern))

    if not input_files:
        raise FileNotFoundError(f"No validated chunks found in {OUTPUTS_DIR} matching {pattern}")

    conn = get_db_connection()

    try:
        for file_path in input_files:
            with open(file_path, encoding="utf-8") as f:
                chunks = json.load(f)

            for entry in chunks:
                # 2. Normalize Schema
                meta = entry.get("meta", {})
                data = entry.get("data", {})

                if "course" not in meta:
                    meta["course"] = "pielegniarstwo"
                # Always force category to the manifest's category (folder-derived,
                # authoritative). Extraction/validation prompts sometimes populate
                # this field with their own topic guess (e.g. "Urazy klatki
                # piersiowej", "chirurgia") instead of leaving it unset — don't
                # trust a pre-existing value here.
                meta["category"] = category

                # 3. Deduplicate by question text
                q_text = data.get("question", "").strip()
                q_norm = q_text.lower()

                if not q_norm or q_norm in seen_questions:
                    continue

                # 4. UUID Uniqueness Check & Registration
                current_uuid = entry.get("id")

                # Check if UUID exists in DB
                cursor = conn.execute(
                    "SELECT uuid FROM uuid_registry WHERE uuid = ?", (str(current_uuid),)
                )
                if cursor.fetchone():
                    # Collision: Generate new UUID
                    current_uuid = str(uuid4())
                    entry["id"] = current_uuid

                # Register the UUID
                q_hash = hashlib.sha256(q_norm.encode()).hexdigest()
                now = datetime.now().isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO uuid_registry (uuid, category, question_hash, registered_at) VALUES (?, ?, ?, ?)",
                    (str(current_uuid), category, q_hash, now),
                )

                # 5. Final Validation & Collection
                if validate_entry(entry):
                    all_entries.append(entry)
                    seen_questions.add(q_norm)

        conn.commit()

        # Write Output
        with open(target_output, "w", encoding="utf-8") as f:
            json.dump(all_entries, f, indent=2, ensure_ascii=False)

        # 6. Update Manifest
        manifest["merge_status"] = "done"
        manifest["updated_at"] = datetime.now().isoformat()

        # We assume the caller handles writing the manifest back to disk,
        # but we update the dict object provided.

        return target_output

    except Exception as e:
        conn.rollback()
        print(f"Error during merge: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Merge validated JSON chunks into a single question bank."
    )
    parser.add_argument(
        "--manifest", type=Path, required=True, help="Path to the book manifest JSON"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Discard existing entries in the category's merged output and rebuild it "
            "from only this book's chunks. Default behavior (no flag) is additive — "
            "safe to run per-book, preserves every other book already merged into the "
            "same category file. Only use this for a deliberate full-category reset."
        ),
    )

    args = parser.parse_args()

    try:
        initialize_db()

        if not args.manifest.exists():
            print(f"Error: Manifest not found at {args.manifest}", file=sys.stderr)
            sys.exit(1)

        with open(args.manifest, encoding="utf-8") as f:
            manifest_data = json.load(f)

        result_path = merge_book(manifest_data, overwrite=args.overwrite)

        # Update the actual file on disk
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        print(f"Successfully merged. Output: {result_path}")

    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Fatal Error: {e}", file=sys.stderr)
        sys.exit(1)
