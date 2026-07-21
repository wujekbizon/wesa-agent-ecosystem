#!/usr/bin/env python3
"""
export_from_neon.py — Export wolfmed_tests rows back to extractor/done/ JSON.

Usage:
    python scripts/export_from_neon.py badania-fizykalne
    python scripts/export_from_neon.py badania-fizykalne --output custom_name.json

Pulls all rows where meta->>'category' matches, writes to done/<category>_all_merged.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
DONE_DIR = WESA_DIR / "extractor" / "done"


def load_env():
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
                    os.environ["NEON_DATABASE_URL"] = (
                        line.split("=", 1)[1].strip().strip('"').strip("'")
                    )
                    print(f"Loaded NEON_DATABASE_URL from {env_path.name}")
                    return


def main():
    parser = argparse.ArgumentParser(description="Export wolfmed_tests category to JSON")
    parser.add_argument("category", help="Category value (e.g. badania-fizykalne)")
    parser.add_argument(
        "--output", help="Output filename in done/ (default: <category>_all_merged.json)"
    )
    args = parser.parse_args()

    load_env()
    db_url = os.environ.get("NEON_DATABASE_URL")
    if not db_url:
        print("Error: NEON_DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT id::text, meta, data, "createdAt", "updatedAt"
        FROM wolfmed_tests
        WHERE meta->>'category' = %s
        ORDER BY "createdAt"
        """,
        (args.category,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"Fetched {len(rows)} rows for category '{args.category}'")

    entries = []
    for row in rows:
        entries.append(
            {
                "id": row["id"],
                "meta": row["meta"],
                "data": row["data"],
                "createdAt": row["createdAt"].isoformat() if row["createdAt"] else None,
                "updatedAt": row["updatedAt"].isoformat() if row["updatedAt"] else None,
            }
        )

    output_name = args.output or f"{args.category}_all_merged.json"
    output_path = DONE_DIR / output_name

    tmp = output_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, output_path)

    print(f"Written -> {output_path}")


if __name__ == "__main__":
    main()
