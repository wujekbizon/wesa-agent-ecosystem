#!/usr/bin/env python3
"""
consolidate_slides.py -- One-shot consolidation of individual slide merged files.

Scans extractor/done/ for per-stem slide files (*_merged.json that are NOT
*_all_merged.json), groups them by category (read from the matching manifest),
appends their questions into <category>_all_merged.json (deduplicating against
existing content), then removes the individual files.

Usage:
    python scripts/consolidate_slides.py           # dry-run, print plan only
    python scripts/consolidate_slides.py --commit  # execute the merge + delete
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid as _uuid
from datetime import datetime
from pathlib import Path

SCRIPT_DIR    = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR      = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"
DONE_DIR      = EXTRACTOR_DIR / "done"
DATA_DIR      = EXTRACTOR_DIR / "data"
WOLFMED_DB    = DATA_DIR / "wolfmed.db"


def get_db_connection():
    conn = sqlite3.connect(str(WOLFMED_DB))
    conn.row_factory = sqlite3.Row
    return conn


def atomic_write_json(path, data):
    temp = path.with_suffix(".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp, path)
    except Exception as e:
        if temp.exists():
            temp.unlink()
        raise e


def safe_load_json(path):
    """Load JSON stripping null bytes."""
    raw = path.read_bytes().rstrip(b"\x00").decode("utf-8")
    return json.loads(raw)


def find_category_for_stem(stem):
    manifest_path = DATA_DIR / f"{stem}_manifest.json"
    if manifest_path.exists():
        try:
            m = safe_load_json(manifest_path)
            if m.get("mode") == "generate_slides":
                return m.get("category")
        except Exception:
            pass
    # Fallback: scan all manifests
    for mp in DATA_DIR.glob("*_manifest.json"):
        try:
            m = safe_load_json(mp)
            if m.get("book") == stem and m.get("mode") == "generate_slides":
                return m.get("category")
        except Exception:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Consolidate individual slide merged files")
    parser.add_argument("--commit", action="store_true", help="Execute merge+delete")
    args = parser.parse_args()
    dry_run = not args.commit

    if dry_run:
        print("DRY RUN -- pass --commit to execute\n")

    individual_pattern = re.compile(r"^(.+)_merged\.json$")
    individual_files = []
    for f in sorted(DONE_DIR.glob("*_merged.json")):
        if f.name.endswith("_all_merged.json"):
            continue
        m = individual_pattern.match(f.name)
        if m:
            individual_files.append((f, m.group(1)))

    if not individual_files:
        print("No individual slide merged files found -- nothing to consolidate.")
        return

    groups = {}
    unresolved = []
    for path, stem in individual_files:
        category = find_category_for_stem(stem)
        if not category:
            unresolved.append(path.name)
            continue
        groups.setdefault(category, []).append((path, stem))

    if unresolved:
        print(f"WARNING: could not determine category for: {', '.join(unresolved)}")
        print("These files will be skipped.\n")

    conn = get_db_connection() if args.commit else None
    now = datetime.now().isoformat()

    for category, files in sorted(groups.items()):
        output_path = DONE_DIR / f"{category}_all_merged.json"

        existing = []
        seen_questions = set()
        seen_uuids = set()
        if output_path.exists():
            try:
                existing = safe_load_json(output_path)
                for e in existing:
                    q = e.get("data", {}).get("question", "")
                    if isinstance(q, str) and q.strip():
                        seen_questions.add(q.strip().lower())
                    uid = e.get("id")
                    if uid:
                        seen_uuids.add(uid)
            except Exception as ex:
                print(f"Warning: could not load {output_path.name}: {ex}")

        combined = list(existing)
        added_total = 0
        skipped_total = 0

        print(f"\nCategory: {category}")
        print(f"  Target: {output_path.name} (existing: {len(existing)} questions)")

        for path, stem in sorted(files, key=lambda x: x[1]):
            try:
                entries = safe_load_json(path)
            except Exception as ex:
                print(f"  SKIP {path.name} -- could not read: {ex}")
                continue

            added = 0
            skipped = 0
            for entry in entries:
                q = entry.get("data", {}).get("question", "")
                if not isinstance(q, str) or len(q.strip()) < 5:
                    skipped += 1
                    continue
                q_norm = q.strip().lower()
                if q_norm in seen_questions:
                    skipped += 1
                    continue
                uid = entry.get("id", "")
                if uid in seen_uuids:
                    uid = str(_uuid.uuid4())
                    entry["id"] = uid
                combined.append(entry)
                seen_questions.add(q_norm)
                seen_uuids.add(uid)
                if conn:
                    q_hash = hashlib.sha256(q_norm.encode()).hexdigest()
                    conn.execute(
                        "INSERT OR IGNORE INTO uuid_registry "
                        "(uuid, category, question_hash, registered_at) VALUES (?,?,?,?)",
                        (uid, category, q_hash, now)
                    )
                added += 1

            added_total += added
            skipped_total += skipped
            action = "MERGE" if not dry_run else "would merge"
            print(f"  {action} {path.name}: +{added} questions ({skipped} dupes skipped)")

        print(f"  Total: {len(combined)} questions (+{added_total} new, {skipped_total} dupes skipped)")

        if not dry_run:
            atomic_write_json(output_path, combined)
            if conn:
                conn.commit()
            print(f"  Written -> {output_path.name}")
            for path, _ in files:
                path.unlink()
                print(f"  Deleted  -> {path.name}")

    if conn:
        conn.close()

    if dry_run:
        print("\nDry run complete. Run with --commit to apply.")
    else:
        print("\nConsolidation complete.")


if __name__ == "__main__":
    main()
