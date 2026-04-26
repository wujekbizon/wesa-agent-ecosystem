#!/usr/bin/env python3
"""
Rebuilds wolfmed.db UUID registry from scratch.
Run this after DB corruption to restore the registry from existing merged JSON files.

Usage:
    python scripts/rebuild_wolfmed_db.py
"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCRIPT_DIR   = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR     = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"
DONE_DIR     = EXTRACTOR_DIR / "done"
DATA_DIR     = EXTRACTOR_DIR / "data"
WOLFMED_DB   = DATA_DIR / "wolfmed.db"
UUID_REGISTRY_JSON = DATA_DIR / "uuid_registry.json"


def main():
    now = datetime.now().isoformat()

    # Backup corrupt DB if it exists
    if WOLFMED_DB.exists():
        backup = DATA_DIR / f"wolfmed_corrupted_{now[:19].replace(':','-')}.db"
        WOLFMED_DB.rename(backup)
        print(f"Backed up corrupt DB -> {backup.name}")

    # Create fresh DB
    conn = sqlite3.connect(str(WOLFMED_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uuid_registry (
            uuid TEXT PRIMARY KEY,
            category TEXT,
            question_hash TEXT,
            registered_at TEXT
        )
    """)
    conn.commit()
    print("Created fresh wolfmed.db")

    registered = 0
    duplicates = 0

    # Import legacy JSON registry
    if UUID_REGISTRY_JSON.exists():
        try:
            legacy = json.loads(UUID_REGISTRY_JSON.read_text(encoding="utf-8"))
            for u in legacy:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO uuid_registry VALUES (?,?,?,?)",
                        (str(u), "legacy", "", now)
                    )
                    registered += 1
                except Exception:
                    duplicates += 1
            conn.commit()
            print(f"Imported {len(legacy)} legacy UUIDs from uuid_registry.json")
        except Exception as e:
            print(f"Warning: could not import legacy JSON: {e}")

    # Scan all merged JSON files in done/
    files_scanned = 0
    for json_file in sorted(DONE_DIR.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                entries = json.load(f)

            if not isinstance(entries, list):
                continue

            files_scanned += 1
            for entry in entries:
                uid = entry.get("id")
                if not uid or not isinstance(uid, str):
                    continue

                meta = entry.get("meta", {})
                category = meta.get("category", "unknown")

                data = entry.get("data", {})
                question = data.get("question", "")
                if not isinstance(question, str) or len(question.strip()) < 5:
                    continue

                q_hash = hashlib.sha256(question.strip().lower().encode()).hexdigest()

                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO uuid_registry VALUES (?,?,?,?)",
                        (uid, category, q_hash, now)
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        registered += 1
                    else:
                        duplicates += 1
                except Exception:
                    duplicates += 1

            conn.commit()

        except Exception as e:
            print(f"Warning: skipped {json_file.name} — {e}")

    conn.close()

    print(f"\nFiles scanned:    {files_scanned}")
    print(f"UUIDs registered: {registered}")
    print(f"Duplicates skipped: {duplicates}")
    print("wolfmed.db rebuilt successfully.")


if __name__ == "__main__":
    main()
