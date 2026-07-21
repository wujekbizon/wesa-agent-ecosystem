import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from merge_utils import merge_book

# Path Resolution (WESA Standard: No traversing parent chains via __file__.parent.parent)
# We assume the script is located in agent-ecosystem/scripts/
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
# Original extractor (chunks, intake, data, done) lives at WesaTeam/extractor/
EXTRACTOR_DIR = WESA_DIR / "extractor"
INTAKE_DIR = EXTRACTOR_DIR / "intake"
CHUNKS_DIR = EXTRACTOR_DIR / "chunks"
DATA_DIR = EXTRACTOR_DIR / "data"
DONE_DIR = EXTRACTOR_DIR / "done"
# Daemon outputs go into agent-ecosystem/extractor/outputs/ (WORKSPACE_ROOT-relative paths)
OUTPUTS_DIR = ECOSYSTEM_DIR / "extractor" / "outputs"
TASKS_DIR = ECOSYSTEM_DIR / "tasks"


def ensure_dirs():
    """Ensure all required pipeline directories exist at startup."""
    for d in [CHUNKS_DIR, OUTPUTS_DIR, DATA_DIR, DONE_DIR, TASKS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, data: dict):
    """Writes JSON to a temporary file then renames to prevent corruption."""
    temp_path = path.with_suffix(".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_path.replace(path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise e


def load_manifest(manifest_path: Path) -> dict:
    """Reads manifest from disk."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def get_task_status(task_path: Path) -> str | None:
    """Reads the status field from a task JSON file."""
    if not task_path.exists():
        return None
    try:
        with open(task_path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("status")
    except Exception as e:
        print(f"Warning: Failed to read task {task_path} — {e}", file=sys.stderr)
        return None


def create_task(task_type: str, task_id: str, output_file: Path, prompt: str) -> dict:
    """Generates a standardized WESA task object."""
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


def process_new_pdf(pdf_path: Path, category: str):
    """Step 1 & 2: Run chunker and drop extraction tasks."""
    # Run pdf_chunker.py via subprocess
    # Assumption: pdf_chunker.py is in the same directory or accessible via PATH
    chunker_script = SCRIPT_DIR / "pdf_chunker.py"
    try:
        subprocess.run(
            [sys.executable, str(chunker_script), str(pdf_path), "--category", category],
            capture_output=True,
            text=True,
            check=True,
        )
        # pdf_chunker.py writes manifest to DATA_DIR/<bookname>_manifest.json
        # bookname = lowercase stem, spaces→underscores, non-[a-z0-9_-] removed
        import re as _re

        _stem = pdf_path.stem.lower().replace(" ", "_")
        _bookname = _re.sub(r"[^a-z0-9_-]", "", _stem)
        manifest_path = DATA_DIR / f"{_bookname}_manifest.json"

        if not manifest_path.exists():
            print(
                f"Error: Chunking failed for {pdf_path.name}. Output manifest not found.",
                file=sys.stderr,
            )
            return

        manifest = load_manifest(manifest_path)

        for chunk in manifest.get("chunks", []):
            if chunk.get("status") == "pending":
                chunk_id = chunk["id"]
                txt_path = WESA_DIR / chunk["txt"]

                if not txt_path.exists():
                    print(f"Warning: Missing text file for chunk {chunk_id}", file=sys.stderr)
                    continue

                with open(txt_path, encoding="utf-8") as f:
                    content = f.read()

                prompt = f"{category}\n{chunk['pages']}\n---\n{content}"
                out_file = OUTPUTS_DIR / f"{chunk_id}_raw.json"

                task_id = f"wm-ex-{uuid.uuid4().hex[:8]}"
                task_data = create_task("extract_pdf", task_id, out_file, prompt)

                # Save task
                task_file = TASKS_DIR / f"{task_id}.json"
                atomic_write_json(task_file, task_data)

                # Update manifest
                chunk["status"] = "queued"
                chunk["extract_task_id"] = task_id

        atomic_write_json(manifest_path, manifest)
        print(f"Successfully queued extraction for {pdf_path.name}")

    except subprocess.CalledProcessError as e:
        print(
            f"Error: Chunking subprocess failed for {pdf_path.name} — {e.stderr}", file=sys.stderr
        )
    except Exception as e:
        print(f"Error: Failed to process {pdf_path.name} — {e}", file=sys.stderr)


def poll_tasks(category: str):
    """Step 3: Poll TASKS_DIR for completed extraction and validation tasks."""
    # We need to find all manifests in CHUNKS_DIR
    for manifest_path in DATA_DIR.glob("*_manifest.json"):
        manifest = load_manifest(manifest_path)
        # Only process manifests belonging to the target category if specified
        # (Assuming manifest contains category info or we check folder structure)

        changed = False

        for chunk in manifest.get("chunks", []):
            # 1. Check Extraction Completion
            if chunk["status"] == "queued":
                task_id = chunk.get("extract_task_id")
                if not task_id:
                    continue

                task_path = TASKS_DIR / f"{task_id}.json"
                status = get_task_status(task_path)

                if status == "done":
                    # Check if output file exists
                    raw_json_path = OUTPUTS_DIR / f"{chunk['id']}_raw.json"
                    if raw_json_path.exists():
                        # Move to validation stage
                        chunk["status"] = "validating"
                        chunk["validate_task_id"] = ""  # placeholder

                        # Prepare validation task — use txt path from manifest (includes category subfolder)
                        txt_file_path = WESA_DIR / chunk["txt"]
                        with open(txt_file_path, encoding="utf-8") as f:
                            txt_content = f.read()
                        with open(raw_json_path, encoding="utf-8") as f:
                            json_content = f.read()

                        prompt = f"{category}\n{chunk['pages']}\n---\nSOURCE TEXT:\n{txt_content}\n---\nQUESTIONS JSON:\n{json_content}"
                        val_out_file = OUTPUTS_DIR / f"{chunk['id']}_validated.json"

                        val_task_id = f"wm-vl-{uuid.uuid4().hex[:8]}"
                        val_task_data = create_task(
                            "validate_questions", val_task_id, val_out_file, prompt
                        )

                        atomic_write_json(TASKS_DIR / f"{val_task_id}.json", val_task_data)
                        chunk["validate_task_id"] = val_task_id
                        changed = True

            # 2. Check Validation Completion
            elif chunk["status"] == "validating":
                task_id = chunk.get("validate_task_id")
                if not task_id:
                    continue

                task_path = TASKS_DIR / f"{task_id}.json"
                status = get_task_status(task_path)

                if status == "done":
                    val_json_path = OUTPUTS_DIR / f"{chunk['id']}_validated.json"
                    if val_json_path.exists():
                        chunk["status"] = "validated"
                        manifest["validated_count"] = manifest.get("validated_count", 0) + 1
                        changed = True

        if changed:
            atomic_write_json(manifest_path, manifest)

        # Step 4: Check for Merge Trigger
        if manifest.get("validated_count", 0) == len(manifest.get("chunks", [])):
            if manifest.get("merge_status") != "done":
                print(f"All chunks validated for {manifest_path.name}. Triggering merge...")
                try:
                    merge_book(manifest)
                    manifest["merge_status"] = "done"
                    atomic_write_json(manifest_path, manifest)

                    # Move source PDF to done
                    # We assume the manifest stores the original source path
                    src_pdf = Path(manifest["source_pdf"])
                    if src_pdf.exists():
                        dest_dir = src_pdf.parent / "done"
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src_pdf), str(dest_dir / src_pdf.name))
                        print(f"Pipeline complete for {src_pdf.name}. Moved to done/.")
                except Exception as e:
                    print(f"Error during merge for {manifest_path.name} — {e}", file=sys.stderr)


def manifest_exists_for_pdf(pdf_path: Path) -> bool:
    """Returns True if a manifest already exists for this PDF (already chunked)."""
    import re as _re

    _stem = pdf_path.stem.lower().replace(" ", "_")
    _bookname = _re.sub(r"[^a-z0-9_-]", "", _stem)
    manifest_path = DATA_DIR / f"{_bookname}_manifest.json"
    return manifest_path.exists()


def run_one_shot(category_filter: str | None):
    """Single pass execution."""
    ensure_dirs()

    # Step 1: Scan intake/ for new PDFs. Always iterate from INTAKE_DIR,
    # filter by category_dir.name if --category was given.
    if not INTAKE_DIR.exists():
        print(f"Error: Intake directory {INTAKE_DIR} does not exist.")
        return

    for category_dir in INTAKE_DIR.iterdir():
        if not category_dir.is_dir() or category_dir.name == "done":
            continue
        if category_filter and category_dir.name != category_filter:
            continue

        for pdf_file in category_dir.glob("*.pdf"):
            if manifest_exists_for_pdf(pdf_file):
                print(f"Skipping {pdf_file.name} — manifest already exists.")
                continue
            process_new_pdf(pdf_file, category_dir.name)

    # Step 2 & 3: Process existing tasks
    poll_tasks(category_filter if category_filter else "all")


def run_watcher(category_filter: str | None):
    """Continuous polling mode."""
    print(f"Starting Wolfmed Watcher (Filter: {category_filter or 'None'})...")
    while True:
        run_one_shot(category_filter)
        time.sleep(30)


def check_merges(category_filter: str | None):
    """Check for manifests that are stuck or pending merge."""
    for manifest_path in DATA_DIR.glob("*_manifest.json"):
        manifest = load_manifest(manifest_path)
        if manifest.get("merge_status") != "done":
            print(
                f"Pending merge: {manifest_path.name} ({manifest.get('validated_count', 0)}/{len(manifest.get('chunks', []))})"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wolfmed Intake Orchestrator")
    parser.add_argument("--watch", action="store_true", help="Continuous polling mode")
    parser.add_argument("--check-merges", action="store_true", help="Check for pending merges only")
    parser.add_argument("--category", type=str, help="Filter by specific category folder")

    args = parser.parse_args()

    try:
        if args.watch:
            run_watcher(args.category)
        elif args.check_merges:
            check_merges(args.category)
        else:
            run_one_shot(args.category)
    except KeyboardInterrupt:
        print("\nWatcher stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error in orchestrator: {e}", file=sys.stderr)
        sys.exit(1)
