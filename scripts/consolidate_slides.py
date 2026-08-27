#!/usr/bin/env python3
"""Rebuild category banks containing completed slide/TXT manifests."""

import argparse
import json
from pathlib import Path

from merge_utils import DATA_DIR, rebuild_category


def completed_slide_categories(data_dir: Path = DATA_DIR) -> list[str]:
    categories: set[str] = set()
    for manifest_path in data_dir.glob("*_manifest.json"):
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("mode") != "generate_slides":
            continue
        chunks = manifest.get("chunks", [])
        if chunks and all(chunk.get("status") in ("validated", "done") for chunk in chunks):
            categories.add(manifest["category"])
    return sorted(categories)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild categories containing validated slide/TXT sources."
    )
    parser.add_argument("--commit", action="store_true", help="Execute category rebuilds")
    arguments = parser.parse_args()

    categories = completed_slide_categories()
    if not categories:
        print("No completed slide categories found.")
        return
    if not arguments.commit:
        print("Dry run. Categories ready for canonical rebuild:")
        for category in categories:
            print(f"- {category}")
        return

    for category in categories:
        result = rebuild_category(category)
        action = "rebuilt" if result.changed else "unchanged"
        print(f"{category}: {action}, {result.question_count} questions")


if __name__ == "__main__":
    main()
