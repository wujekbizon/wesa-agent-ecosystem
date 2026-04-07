"""
manifest_checker.py — Wolfmed Chunk Completion Checker
Part of the WESA Wolfmed content pipeline.

Checks the status of all chunks in a manifest after each EX1 task completes.
When all chunks are validated, triggers the merge step directly.

Called by wolfmed_intake.py's completion polling loop.
Can also be called standalone to force a merge check on a specific manifest.

Usage:
    python scripts/manifest_checker.py --manifest extractor/data/fizjologia_manifest.json
    python scripts/manifest_checker.py --check-all   # check all pending manifests
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"

OUTPUTS_DIR = EXTRACTOR_DIR / "outputs"
DATA_DIR = EXTRACTOR_DIR / "data"
DONE_DIR = EXTRACTOR_DIR / "done"


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _save_manifest(manifest_path: Path, manifest: dict):
    manifest["updated_at"] = datetime.now().isoformat()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def scan_outputs_for_chunk(chunk: dict) -> tuple[str | None, str | None]:
    """
    Scan extractor/outputs/ for JSON files matching this chunk's ID.
    Returns (raw_json_path, validated_json_path) — either can be None.
    """
    chunk_id = chunk["id"]
    raw_path = OUTPUTS_DIR / f"{chunk_id}.json"
    validated_path = OUTPUTS_DIR / f"{chunk_id}_validated.json"

    raw = str(raw_path) if raw_path.exists() and raw_path.stat().st_size > 10 else None
    validated = str(validated_path) if validated_path.exists() and validated_path.stat().st_size > 10 else None

    return raw, validated


def check_manifest(manifest_path: Path, auto_merge: bool = True) -> dict:
    """
    Inspect manifest, update chunk statuses based on what's in outputs/.
    If all chunks are validated → trigger merge.

    Returns updated manifest dict.
    """
    manifest = _load_manifest(manifest_path)
    book = manifest["book"]
    merge_status = manifest.get("merge_status", "pending")

    if merge_status in ("done", "failed"):
        print(f"[manifest_checker] {book}: merge already {merge_status}, skipping")
        return manifest

    print(f"[manifest_checker] Checking {book} ({manifest['total_chunks']} chunks)...")

    validated_count = 0
    extracted_count = 0

    for chunk in manifest["chunks"]:
        raw, validated = scan_outputs_for_chunk(chunk)

        # Update chunk fields
        if raw and not chunk.get("output_json"):
            chunk["output_json"] = raw

        if validated and not chunk.get("validated_json"):
            chunk["validated_json"] = validated

        # Update chunk status
        if validated:
            chunk["status"] = "validated"
            validated_count += 1
        elif raw:
            chunk["status"] = "extracted"
            extracted_count += 1
        elif chunk["status"] == "pending" and chunk.get("task_id"):
            chunk["status"] = "queued"

    print(f"[manifest_checker]   Validated: {validated_count} / {manifest['total_chunks']}")
    print(f"[manifest_checker]   Extracted (not yet validated): {extracted_count}")

    # Save updated manifest
    _save_manifest(manifest_path, manifest)

    # Check if all chunks are done
    if validated_count == manifest["total_chunks"]:
        print(f"[manifest_checker] All chunks validated for: {book}")
        if auto_merge:
            _trigger_merge(manifest_path, manifest)
    elif validated_count + extracted_count == manifest["total_chunks"]:
        # All extracted but not all validated yet
        print(f"[manifest_checker] All chunks extracted, validation in progress...")
    else:
        remaining = manifest["total_chunks"] - validated_count - extracted_count
        print(f"[manifest_checker] Still waiting for {remaining} chunks to be extracted")

    return manifest


def _trigger_merge(manifest_path: Path, manifest: dict):
    """Run the merge step directly."""
    try:
        # Import here to avoid circular imports if called from wolfmed_intake
        sys.path.insert(0, str(SCRIPT_DIR))
        from merge_utils import merge_book

        # Update manifest merge status
        manifest["merge_status"] = "queued"
        _save_manifest(manifest_path, manifest)

        print(f"[manifest_checker] Triggering merge for: {manifest['book']}")
        output_path = merge_book(manifest)

        # Update manifest with final status
        manifest["merge_status"] = "done"
        manifest["merged_output"] = str(output_path)
        _save_manifest(manifest_path, manifest)

        print(f"[manifest_checker] MERGE COMPLETE: {output_path}")
        print(f"[manifest_checker] Question bank ready: {output_path.name}")

        # Update chunk statuses to done
        for chunk in manifest["chunks"]:
            if chunk["status"] == "validated":
                chunk["status"] = "done"
        _save_manifest(manifest_path, manifest)

    except Exception as e:
        print(f"[manifest_checker] MERGE FAILED: {e}")
        manifest["merge_status"] = "failed"
        manifest["merge_error"] = str(e)
        _save_manifest(manifest_path, manifest)
        raise


def check_all_manifests(auto_merge: bool = True) -> list[dict]:
    """Check all manifests in extractor/data/ that are not yet complete."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifests = list(DATA_DIR.glob("*_manifest.json"))

    if not manifests:
        print("[manifest_checker] No manifests found in extractor/data/")
        return []

    results = []
    for mp in sorted(manifests):
        try:
            manifest = _load_manifest(mp)
            if manifest.get("merge_status") in ("done",):
                print(f"[manifest_checker] {manifest['book']}: already complete, skipping")
                continue
            result = check_manifest(mp, auto_merge=auto_merge)
            results.append(result)
        except Exception as e:
            print(f"[manifest_checker] ERROR processing {mp.name}: {e}")

    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check Wolfmed chunk manifest status and trigger merge")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", help="Path to a specific manifest JSON")
    group.add_argument("--check-all", action="store_true", help="Check all pending manifests")
    parser.add_argument("--no-merge", action="store_true", help="Check only, do not trigger merge")

    args = parser.parse_args()
    auto_merge = not args.no_merge

    if args.manifest:
        mp = Path(args.manifest)
        if not mp.exists():
            print(f"ERROR: Manifest not found: {mp}")
            sys.exit(1)
        check_manifest(mp, auto_merge=auto_merge)
    else:
        check_all_manifests(auto_merge=auto_merge)
