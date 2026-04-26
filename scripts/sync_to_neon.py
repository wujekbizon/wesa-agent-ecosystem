#!/usr/bin/env python3
"""
sync_to_neon.py — Push extractor/done/ merged JSON to wolfmed_tests (Neon/PostgreSQL).

Reads a single merged JSON file from extractor/done/, inserts new questions only.
ON CONFLICT (id) DO NOTHING — safe to re-run, never overwrites existing rows.

Usage:
    python scripts/sync_to_neon.py <filename>
    python scripts/sync_to_neon.py badania-fizykalne_all_merged.json
    python scripts/sync_to_neon.py badania-fizykalne_all_merged.json --dry-run

Requires:
    NEON_DATABASE_URL env var  (or .env file in agent-ecosystem/ or wolfmed/)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR    = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR      = ECOSYSTEM_DIR.parent
DONE_DIR      = WESA_DIR / "extractor" / "done"
BATCH_SIZE    = 100


def load_env():
    """Load NEON_DATABASE_URL from .env file if not already set."""
    if os.environ.get("NEON_DATABASE_URL"):
        return
    for env_path in [
        ECOSYSTEM_DIR / ".env",
        WESA_DIR / "wolfmed" / ".env",
        WESA_DIR / "wolfmed" / ".env.local",
    ]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("NEON_DATABASE_URL=") and not line.startswith("#"):
                    os.environ["NEON_DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    print(f"Loaded NEON_DATABASE_URL from {env_path.name}")
                    return


def normalize_date(val):
    """Normalize date strings to ISO format. Returns None for invalid dates."""
    if not val:
        return None
    if re.match(r"^\d{4}-0-", str(val)):
        return None
    try:
        normalized = str(val).replace(" ", "T")
        normalized = re.sub(r"(\.\d{3})\d*$", r"\1", normalized)
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Sync extractor done/ file to Neon wolfmed_tests")
    parser.add_argument("filename", help="JSON file in extractor/done/ (e.g. badania-fizykalne_all_merged.json)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without inserting")
    args = parser.parse_args()

    target = DONE_DIR / args.filename
    if not target.exists():
        # Allow passing just the category name
        alt = DONE_DIR / f"{args.filename}_all_merged.json"
        if alt.exists():
            target = alt
        else:
            print(f"Error: file not found: {target}", file=sys.stderr)
            print(f"Available files in done/:", file=sys.stderr)
            for f in sorted(DONE_DIR.glob("*.json")):
                print(f"  {f.name}", file=sys.stderr)
            sys.exit(1)

    print(f"Source: {target}")

    raw = target.read_bytes().rstrip(b"\x00").decode("utf-8")
    # Repair truncated output (missing closing bracket)
    for candidate in [raw, raw.rstrip() + "\n]", raw.rstrip().rstrip(",") + "\n]"]:
        try:
            entries = json.loads(candidate, strict=False)
            break
        except json.JSONDecodeError:
            pass
    else:
        print(f"Error: could not parse {target.name}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(entries, list):
        print("Error: expected a JSON array", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(entries)} entries")

    # Count by category
    cats = {}
    for e in entries:
        cat = e.get("meta", {}).get("category") or e.get("category", "unknown")
        cats[cat] = cats.get(cat, 0) + 1
    for cat, count in sorted(cats.items()):
        print(f"  {cat}: {count}")

    if args.dry_run:
        print("\nDry run — no DB connection made.")
        return

    load_env()
    db_url = os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        print("Error: NEON_DATABASE_URL not set", file=sys.stderr)
        print("Set it as an env var or add it to agent-ecosystem/.env", file=sys.stderr)
        sys.exit(1)

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    errors = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for i in range(0, len(entries), BATCH_SIZE):
        batch = entries[i : i + BATCH_SIZE]
        for entry in batch:
            uid = entry.get("id")
            if not uid:
                skipped += 1
                continue

            meta = entry.get("meta", {})
            data = entry.get("data", {})

            created_at = normalize_date(entry.get("createdAt")) or now_iso
            updated_at = normalize_date(entry.get("updatedAt"))

            try:
                cur.execute(
                    """
                    INSERT INTO wolfmed_tests (id, meta, data, "createdAt", "updatedAt")
                    VALUES (%s::uuid, %s::jsonb, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        uid,
                        json.dumps(meta, ensure_ascii=False),
                        json.dumps(data, ensure_ascii=False),
                        created_at,
                        updated_at,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                print(f"  Error on {uid}: {e}", file=sys.stderr)
                conn.rollback()
                continue

        conn.commit()
        done = min(i + BATCH_SIZE, len(entries))
        print(f"  Progress: {done}/{len(entries)}  (inserted: {inserted}, skipped: {skipped})")

    cur.close()
    conn.close()

    print(f"\nDone.")
    print(f"  Inserted : {inserted}")
    print(f"  Skipped  : {skipped} (already in DB)")
    if errors:
        print(f"  Errors   : {errors}")


if __name__ == "__main__":
    main()
