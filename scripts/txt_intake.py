#!/usr/bin/env python3
"""
Wolfmed TXT Intake Orchestrator
Processes lecture slide TXT files through the SL1 generation pipeline.

Usage:
    python scripts/txt_intake.py                        # one-shot, all categories
    python scripts/txt_intake.py --category <name>      # filter to one category
    python scripts/txt_intake.py --watch                # continuous polling every 30s
    python scripts/txt_intake.py --check-merges         # list pending merges

Flow:
    Greg drops .txt into extractor/intake/<category>/
    -> generate_from_slides task (SL1)
    -> validate_questions task (EX1)
    -> inline merge -> extractor/done/<stem>_merged.json
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from merge_utils import rebuild_category, release_is_current


def repair_json(raw: str) -> str:
    """Attempt to repair common agent JSON output issues before parsing."""
    # Attempt 1: strict=False handles embedded control chars natively
    try:
        json.loads(raw, strict=False)
        return raw
    except json.JSONDecodeError:
        pass
    # Attempt 2: strip non-printable control chars, retry
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)
    try:
        json.loads(cleaned, strict=False)
        return cleaned
    except json.JSONDecodeError:
        pass
    # Attempt 3: handle truncated output (missing closing bracket)
    for suffix in ["]", "\n]"]:
        candidate = cleaned.rstrip() + suffix
        try:
            json.loads(candidate, strict=False)
            return candidate
        except json.JSONDecodeError:
            pass
    raise ValueError("Could not repair JSON output from agent")


# ---------------------------------------------------------------------------
# Path constants (WESA standard — no traversing __file__.parent chains)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"
INTAKE_DIR = EXTRACTOR_DIR / "intake"
DATA_DIR = EXTRACTOR_DIR / "data"
DONE_DIR = EXTRACTOR_DIR / "done"
OUTPUTS_DIR = ECOSYSTEM_DIR / "extractor" / "outputs"
TASKS_DIR = ECOSYSTEM_DIR / "tasks"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_dirs():
    for d in [OUTPUTS_DIR, DONE_DIR, DATA_DIR, TASKS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def sanitize_stem(name: str) -> str:
    name = name.lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_-]", "", name)


def atomic_write_json(path: Path, data: dict):
    temp = path.with_suffix(".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp, path)
    except Exception as e:
        if temp.exists():
            temp.unlink()
        raise e


def load_manifest(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_task_status(task_path: Path) -> str | None:
    if not task_path.exists():
        return None
    try:
        with open(task_path, encoding="utf-8") as f:
            return json.load(f).get("status")
    except Exception as e:
        print(f"Warning: failed to read task {task_path} — {e}", file=sys.stderr)
        return None


def create_task(task_type: str, task_id: str, output_file: Path, prompt: str) -> dict:
    return {
        "id": task_id,
        "type": task_type,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "completed_at": "",
        "result": "",
        "error": "",
        "model_used": "",
        "output_file": str(output_file.relative_to(ECOSYSTEM_DIR)),
        "prompt": prompt,
    }


def manifest_path_for(stem: str) -> Path:
    return DATA_DIR / f"{stem}_manifest.json"


def manifest_exists(stem: str) -> bool:
    return manifest_path_for(stem).exists()


# ---------------------------------------------------------------------------
# Phase 1: Process new TXT file — create manifest + drop generate task
# ---------------------------------------------------------------------------


def process_new_txt(txt_path: Path, category: str):
    stem = sanitize_stem(txt_path.stem)

    if manifest_exists(stem):
        print(f"Skipping {txt_path.name} — manifest already exists.")
        return

    try:
        with open(txt_path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {txt_path}: {e}", file=sys.stderr)
        return

    if not content.strip():
        print(f"Skipping {txt_path.name} — file is empty.", file=sys.stderr)
        return

    run_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    raw_json_path = OUTPUTS_DIR / f"{stem}_raw.json"
    OUTPUTS_DIR / f"{stem}_validated.json"

    manifest = {
        "run_id": run_id,
        "book": stem,
        "category": category,
        "mode": "generate_slides",
        "source_txt": f"extractor/intake/{category}/{txt_path.name}",
        "total_chunks": 1,
        "validated_count": 0,
        "merge_status": "pending",
        "created_at": now,
        "updated_at": now,
        "chunks": [
            {
                "id": stem,
                "txt": f"extractor/intake/{category}/{txt_path.name}",
                "status": "pending",
                "extract_task_id": None,
                "validate_task_id": None,
                "raw_json": f"extractor/outputs/{stem}_raw.json",
                "validated_json": f"extractor/outputs/{stem}_validated.json",
            }
        ],
    }

    task_id = f"wm-sl-{uuid.uuid4().hex[:8]}"
    prompt = f"CATEGORY: {category}\nSOURCE TEXT:\n{content}"
    task_data = create_task("generate_from_slides", task_id, raw_json_path, prompt)

    atomic_write_json(TASKS_DIR / f"{task_id}.json", task_data)

    manifest["chunks"][0]["status"] = "queued"
    manifest["chunks"][0]["extract_task_id"] = task_id
    atomic_write_json(manifest_path_for(stem), manifest)

    print(f"Queued generate_from_slides for {txt_path.name} (task: {task_id})")


# ---------------------------------------------------------------------------
# Phase 2 & 3: Poll tasks — advance through queued → validating → validated
# ---------------------------------------------------------------------------


def poll_tasks(category_filter: str | None):
    categories_to_merge: set[str] = set()
    for manifest_path in DATA_DIR.glob("*_manifest.json"):
        manifest = load_manifest(manifest_path)

        if manifest.get("mode") != "generate_slides":
            continue
        if category_filter and manifest.get("category") != category_filter:
            continue

        changed = False
        chunk = manifest["chunks"][0]  # always exactly one chunk for slides

        # --- Extraction complete → queue validation ---
        if chunk["status"] == "queued":
            task_id = chunk.get("extract_task_id")
            if not task_id:
                continue
            status = get_task_status(TASKS_DIR / f"{task_id}.json")
            if status == "done":
                raw_json_path = OUTPUTS_DIR / f"{chunk['id']}_raw.json"
                if not raw_json_path.exists():
                    print(
                        f"Warning: generate task done but raw JSON missing: {raw_json_path}",
                        file=sys.stderr,
                    )
                    continue

                # Read source text for validation prompt
                source_txt_path = WESA_DIR / chunk["txt"]
                try:
                    with open(source_txt_path, encoding="utf-8") as f:
                        txt_content = f.read()
                    with open(raw_json_path, encoding="utf-8") as f:
                        raw_json_content = repair_json(f.read())
                except Exception as e:
                    print(f"Error reading files for validation: {e}", file=sys.stderr)
                    continue

                category = manifest["category"]
                val_prompt = (
                    f"{category}\n---\nSOURCE TEXT:\n{txt_content}"
                    f"\n---\nQUESTIONS JSON:\n{raw_json_content}"
                )
                val_task_id = f"wm-vl-{uuid.uuid4().hex[:8]}"
                val_out = OUTPUTS_DIR / f"{chunk['id']}_validated.json"
                val_task = create_task("validate_questions", val_task_id, val_out, val_prompt)

                atomic_write_json(TASKS_DIR / f"{val_task_id}.json", val_task)

                chunk["status"] = "validating"
                chunk["validate_task_id"] = val_task_id
                changed = True
                print(f"Queued validate_questions for {chunk['id']} (task: {val_task_id})")

        # --- Validation complete → mark validated ---
        elif chunk["status"] == "validating":
            task_id = chunk.get("validate_task_id")
            if not task_id:
                continue
            status = get_task_status(TASKS_DIR / f"{task_id}.json")
            if status == "done":
                val_path = OUTPUTS_DIR / f"{chunk['id']}_validated.json"
                if val_path.exists():
                    chunk["status"] = "validated"
                    manifest["validated_count"] = 1
                    changed = True
                    print(f"Validation complete for {chunk['id']}")

        if changed:
            manifest["updated_at"] = datetime.now().isoformat()
            atomic_write_json(manifest_path, manifest)

        derived_validated_count = int(chunk.get("status") in ("validated", "done"))
        if manifest.get("validated_count") != derived_validated_count:
            manifest["validated_count"] = derived_validated_count
            atomic_write_json(manifest_path, manifest)

        category = manifest["category"]
        if derived_validated_count == 1 and (
            manifest.get("merge_status") != "done" or not release_is_current(category)
        ):
            categories_to_merge.add(category)

    for category in sorted(categories_to_merge):
        try:
            result = rebuild_category(category)
            print(
                f"Category merge complete: {result.output_path.name} "
                f"({result.question_count} questions)"
            )
        except Exception as e:
            print(f"Error rebuilding category {category}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Phase 4: Compatibility wrapper; canonical logic lives in merge_utils.py
# ---------------------------------------------------------------------------


def merge_slides(manifest: dict, manifest_path: Path):
    result = rebuild_category(manifest["category"])
    print(f"Rebuilt {result.output_path.name} ({result.question_count} questions)")


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def check_merges(category_filter: str | None):
    found = False
    for manifest_path in DATA_DIR.glob("*_manifest.json"):
        manifest = load_manifest(manifest_path)
        if manifest.get("mode") != "generate_slides":
            continue
        if category_filter and manifest.get("category") != category_filter:
            continue
        if manifest.get("merge_status") != "done":
            vc = manifest.get("validated_count", 0)
            print(f"Pending: {manifest_path.name} ({vc}/1 validated)")
            found = True
    if not found:
        print("No pending slide merges.")


def run_one_shot(category_filter: str | None):
    ensure_dirs()

    if not INTAKE_DIR.exists():
        print(f"Error: intake directory not found: {INTAKE_DIR}", file=sys.stderr)
        return

    for category_dir in INTAKE_DIR.iterdir():
        if not category_dir.is_dir() or category_dir.name == "done":
            continue
        if category_filter and category_dir.name != category_filter:
            continue

        for txt_file in category_dir.glob("*.txt"):
            try:
                process_new_txt(txt_file, category_dir.name)
            except Exception as e:
                print(f"Error processing {txt_file.name}: {e}", file=sys.stderr)

    poll_tasks(category_filter)


def run_watcher(category_filter: str | None):
    print(f"Starting TXT watcher (category: {category_filter or 'all'})...")
    while True:
        run_one_shot(category_filter)
        time.sleep(30)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Wolfmed TXT Intake — lecture slide question generation pipeline"
    )
    parser.add_argument("--category", type=str, help="Filter to specific category folder")
    parser.add_argument("--watch", action="store_true", help="Continuous polling every 30s")
    parser.add_argument("--check-merges", action="store_true", help="List pending merges")

    args = parser.parse_args()

    try:
        if args.check_merges:
            check_merges(args.category)
        elif args.watch:
            run_watcher(args.category)
        else:
            run_one_shot(args.category)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
