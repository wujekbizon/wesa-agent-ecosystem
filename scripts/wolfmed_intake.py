"""
wolfmed_intake.py — Wolfmed PDF Intake Watcher
Part of the WESA Wolfmed content pipeline.

WHAT IT DOES:
  1. Watches extractor/intake/ for new PDF files dropped by Greg
  2. Splits large PDFs into chunks (via pdf_chunker)
  3. Drops extract_pdf + validate_questions task JSONs into agent-ecosystem/tasks/
  4. Polls manifests and triggers merge when all chunks are validated

HOW TO RUN:
  Option A — One-shot (process whatever is in intake/ right now):
      cd agent-ecosystem
      python scripts/wolfmed_intake.py

  Option B — Continuous watcher (poll every 30 seconds):
      python scripts/wolfmed_intake.py --watch

  Option C — Check merge status only (no new intake):
      python scripts/wolfmed_intake.py --check-merges

TASK CATEGORIES (override auto-detection):
  python scripts/wolfmed_intake.py --category fizjologia --mode generate

Greg only needs to drop PDFs into extractor/intake/.
Everything else is automatic.
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
EXTRACTOR_DIR = WESA_DIR / "extractor"

INTAKE_DIR = EXTRACTOR_DIR / "intake"
INTAKE_DONE_DIR = INTAKE_DIR / "done"
CHUNKS_DIR = EXTRACTOR_DIR / "chunks"
INPUTS_DIR = EXTRACTOR_DIR / "inputs"
OUTPUTS_DIR = EXTRACTOR_DIR / "outputs"
DATA_DIR = EXTRACTOR_DIR / "data"
DONE_DIR = EXTRACTOR_DIR / "done"
TASKS_DIR = ECOSYSTEM_DIR / "tasks"

# How many pages before we chunk (single-file PDFs below this go directly)
CHUNK_THRESHOLD = 60
WATCH_INTERVAL = 30  # seconds between scans in --watch mode


# ─── Task JSON helpers ────────────────────────────────────────────────────────

def _make_task_id(prefix: str) -> str:
    short = uuid.uuid4().hex[:8]
    return f"wolfmed-{prefix}-{short}"


def _build_extract_prompt(chunk: dict, category: str, mode: str, text: str) -> str:
    """Build the EX1 prompt for an extract_pdf task."""

    if mode == "generate":
        return f"""You are EX1, a Wolfmed question extractor. Mode: GENERATE.

Category: {category}
Source chunk: {chunk['id']} (pages {chunk['pages']})

INSTRUCTIONS:
- Create multiple-choice test questions based EXCLUSIVELY on the content below.
- PDF IS THE SINGLE SOURCE OF TRUTH — questions and correct answers must come from the text only.
- Bold text and key definitions are your primary targets.
- Generate 3 plausible but incorrect distractors per question using domain knowledge.
- Generate between 30-80 questions depending on content volume.
- Correct answer in random position (1-4).
- Preserve Polish diacritics. Concise, clear Polish language.

OUTPUT: A valid JSON array only. No commentary, no markdown fences.

Schema:
[
  {{
    "id": "<uuid v4>",
    "meta": {{"course": "pielegniarstwo", "category": "{category}"}},
    "data": {{
      "question": "<Polish question text>",
      "answers": [
        {{"option": "<text>", "isCorrect": true}},
        {{"option": "<text>", "isCorrect": false}},
        {{"option": "<text>", "isCorrect": false}},
        {{"option": "<text>", "isCorrect": false}}
      ]
    }},
    "createdAt": "<YYYY-MM-DD HH:MM:SS.micro>",
    "updatedAt": null
  }}
]

SOURCE TEXT:
{text}

Output JSON array only:"""

    elif mode == "extract":
        return f"""You are EX1, a Wolfmed question extractor. Mode: EXTRACT.

Category: {category}
Source chunk: {chunk['id']} (pages {chunk['pages']})

INSTRUCTIONS:
- The text below contains Q&A pairs — questions with their correct answers.
- Extract each question and its correct answer exactly as written.
- Generate 3 plausible but incorrect distractors for each question.
- Correct answer in random position (1-4).
- Preserve Polish diacritics. Do not paraphrase the original Q&A.

OUTPUT: A valid JSON array only. No commentary, no markdown fences.

Schema:
[
  {{
    "id": "<uuid v4>",
    "meta": {{"course": "pielegniarstwo", "category": "{category}"}},
    "data": {{
      "question": "<extracted question text>",
      "answers": [
        {{"option": "<correct answer from source>", "isCorrect": true}},
        {{"option": "<generated incorrect>", "isCorrect": false}},
        {{"option": "<generated incorrect>", "isCorrect": false}},
        {{"option": "<generated incorrect>", "isCorrect": false}}
      ]
    }},
    "createdAt": "<YYYY-MM-DD HH:MM:SS.micro>",
    "updatedAt": null
  }}
]

SOURCE TEXT:
{text}

Output JSON array only:"""

    elif mode == "extract_procedures":
        return f"""You are EX1, a Wolfmed question extractor. Mode: EXTRACT_PROCEDURES.

Category: {category}
Source chunk: {chunk['id']} (pages {chunk['pages']})

INSTRUCTIONS:
This text contains nursing procedure checklists (CZYNNOŚCI PRZYGOTOWAWCZE, CZYNNOŚCI WŁAŚCIWE,
CZYNNOŚCI KOŃCOWE) and review questions (PYTANIA SPRAWDZAJĄCE).

Extract ONLY the review questions (PYTANIA SPRAWDZAJĄCE section).
Cross-reference with KLUCZ ODPOWIEDZI (answer key) if present to mark correct answers.
If no answer key found, mark all isCorrect as false and note "NOTE: brak klucza".

OUTPUT: A valid JSON array only. No commentary, no markdown fences.

Schema same as extract mode above.

SOURCE TEXT:
{text}

Output JSON array only:"""

    else:
        raise ValueError(f"Unknown mode: {mode}")


def _build_validate_prompt(chunk: dict, category: str, text: str, questions_json: str) -> str:
    """Build the EX1 prompt for a validate_questions task."""
    return f"""You are EX1, a Wolfmed question validator. Mode: VALIDATE.

Category: {category}
Source chunk: {chunk['id']} (pages {chunk['pages']})

INSTRUCTIONS:
- Compare each generated question against the source text below.
- KEEP only questions whose topic and correct answer are clearly supported by the source.
- REMOVE hallucinated, unsupported, or incorrect questions.
- Do NOT regenerate questions. Do NOT change question wording.
- Do NOT add commentary.

OUTPUT: A valid JSON array only (same schema as input, subset of questions kept).

SOURCE TEXT:
{text}

GENERATED QUESTIONS TO VALIDATE:
{questions_json}

Output validated JSON array only:"""


def _write_task_json(task_id: str, task_type: str, prompt: str, output_file: str) -> Path:
    """Write a task JSON file to agent-ecosystem/tasks/."""
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    task = {
        "id": task_id,
        "type": task_type,
        "status": "pending",
        "prompt": prompt,
        "output_file": output_file,
        "created_at": datetime.now().isoformat(),
    }
    task_path = TASKS_DIR / f"{task_id}.json"
    task_path.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
    return task_path


def _load_manifest(manifest_path: Path) -> dict:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _save_manifest(manifest_path: Path, manifest: dict):
    manifest["updated_at"] = datetime.now().isoformat()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# ─── Core: process one PDF ────────────────────────────────────────────────────

def process_pdf(pdf_path: Path, category: str = "", mode: str = "auto") -> dict:
    """
    Process a single PDF from intake/:
    - Chunk it (or use as-is if small)
    - Create extract_pdf tasks for each chunk
    - Return the manifest
    """
    from pdf_chunker import chunk_pdf  # local import (scripts/ is in sys.path)

    print(f"\n[wolfmed_intake] ─────────────────────────────────────────")
    print(f"[wolfmed_intake] Processing: {pdf_path.name}")

    # Chunk the PDF (creates manifest + chunk files)
    manifest = chunk_pdf(pdf_path, category=category, mode=mode)
    book = manifest["book"]
    manifest_path = DATA_DIR / f"{book}_manifest.json"

    queued_count = 0

    for chunk in manifest["chunks"]:
        chunk_txt_path = Path(chunk["txt"])
        if not chunk_txt_path.exists():
            print(f"[wolfmed_intake]   WARNING: text file missing for {chunk['id']}, skipping")
            continue

        text = chunk_txt_path.read_text(encoding="utf-8")

        if not text.strip():
            print(f"[wolfmed_intake]   WARNING: empty text for {chunk['id']} — may be scanned PDF")
            text = f"[Scanned PDF chunk — {chunk['id']} pages {chunk['pages']} — text extraction unavailable]"

        # Output path for raw extraction
        output_json = str(OUTPUTS_DIR / f"{chunk['id']}.json")
        # Output path for validated JSON
        validated_json = str(OUTPUTS_DIR / f"{chunk['id']}_validated.json")

        # ── Task 1: extract_pdf ──
        extract_task_id = _make_task_id(f"{chunk['id']}-extract")
        extract_prompt = _build_extract_prompt(chunk, manifest["category"], manifest["mode"], text)
        task_path = _write_task_json(extract_task_id, "extract_pdf", extract_prompt, output_json)

        # Update chunk in manifest
        chunk["status"] = "queued"
        chunk["task_id"] = extract_task_id
        chunk["output_json"] = output_json
        chunk["validated_json"] = validated_json

        print(f"[wolfmed_intake]   Queued extract task: {extract_task_id} → {task_path.name}")
        queued_count += 1

    manifest["merge_status"] = "pending"
    _save_manifest(manifest_path, manifest)

    # Move source PDF to intake/done/
    INTAKE_DONE_DIR.mkdir(parents=True, exist_ok=True)
    dest = INTAKE_DONE_DIR / pdf_path.name
    if not dest.exists():
        pdf_path.rename(dest)
        print(f"[wolfmed_intake] Archived source PDF → intake/done/{pdf_path.name}")

    print(f"[wolfmed_intake] {queued_count} extract tasks queued for: {book}")
    return manifest


def queue_validate_tasks(manifest: dict, manifest_path: Path):
    """
    After extraction is complete for all chunks, queue validate_questions tasks.
    Called by check_intake_completions() when all chunks are extracted.
    """
    book = manifest["book"]
    queued = 0

    for chunk in manifest["chunks"]:
        if chunk["status"] != "extracted":
            continue

        output_json_path = Path(chunk.get("output_json", ""))
        if not output_json_path.exists() or output_json_path.stat().st_size < 10:
            print(f"[wolfmed_intake]   WARNING: No output for {chunk['id']}, cannot validate")
            continue

        chunk_txt_path = Path(chunk["txt"])
        text = chunk_txt_path.read_text(encoding="utf-8") if chunk_txt_path.exists() else ""
        questions_json = output_json_path.read_text(encoding="utf-8")

        validated_json = str(OUTPUTS_DIR / f"{chunk['id']}_validated.json")
        validate_task_id = _make_task_id(f"{chunk['id']}-validate")

        validate_prompt = _build_validate_prompt(
            chunk, manifest["category"], text, questions_json
        )
        _write_task_json(validate_task_id, "validate_questions", validate_prompt, validated_json)

        chunk["status"] = "validating"
        chunk["validate_task_id"] = validate_task_id
        chunk["validated_json"] = validated_json

        print(f"[wolfmed_intake]   Queued validate task: {validate_task_id}")
        queued += 1

    if queued > 0:
        _save_manifest(manifest_path, manifest)
        print(f"[wolfmed_intake] {queued} validate tasks queued for: {book}")


# ─── Completion polling ───────────────────────────────────────────────────────

def check_intake_completions():
    """
    Scan all manifests, check which chunks now have output files.
    Queue validate tasks for newly extracted chunks.
    Trigger merge for fully validated books.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifests = list(DATA_DIR.glob("*_manifest.json"))

    if not manifests:
        return

    for mp in sorted(manifests):
        try:
            manifest = _load_manifest(mp)
            if manifest.get("merge_status") in ("done", "failed"):
                continue

            book = manifest["book"]
            needs_save = False
            extracted_count = 0
            validated_count = 0
            total = manifest["total_chunks"]

            for chunk in manifest["chunks"]:
                # Check if extract output appeared
                if chunk["status"] in ("queued", "pending"):
                    out = Path(chunk.get("output_json", ""))
                    if out.exists() and out.stat().st_size > 10:
                        chunk["status"] = "extracted"
                        needs_save = True

                # Check if validated output appeared
                if chunk["status"] in ("extracted", "validating"):
                    val = Path(chunk.get("validated_json", ""))
                    if val.exists() and val.stat().st_size > 10:
                        chunk["status"] = "validated"
                        needs_save = True

                if chunk["status"] == "extracted":
                    extracted_count += 1
                elif chunk["status"] in ("validated", "done"):
                    validated_count += 1

            if needs_save:
                _save_manifest(mp, manifest)

            # Queue validate tasks for newly extracted chunks
            if extracted_count > 0:
                queue_validate_tasks(manifest, mp)

            # Check if all validated → merge
            if validated_count == total:
                print(f"[wolfmed_intake] All {total} chunks validated for {book} → triggering merge")
                sys.path.insert(0, str(SCRIPT_DIR))
                from manifest_checker import _trigger_merge
                _trigger_merge(mp, manifest)

            elif extracted_count + validated_count > 0:
                print(f"[wolfmed_intake] {book}: {validated_count}/{total} validated, "
                      f"{extracted_count} extracted, "
                      f"{total - validated_count - extracted_count} pending")

        except Exception as e:
            print(f"[wolfmed_intake] ERROR checking {mp.name}: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_once(category: str = "", mode: str = "auto"):
    """Process all PDFs in intake/ and check completion status."""
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # Add scripts/ to path so imports work
    sys.path.insert(0, str(SCRIPT_DIR))

    # ── Phase 1: Process new PDFs in intake/ ──
    pdfs = [f for f in INTAKE_DIR.glob("*.pdf") if f.is_file()]
    if pdfs:
        print(f"[wolfmed_intake] Found {len(pdfs)} new PDF(s) in intake/")
        for pdf in sorted(pdfs):
            try:
                process_pdf(pdf, category=category, mode=mode)
            except Exception as e:
                print(f"[wolfmed_intake] ERROR processing {pdf.name}: {e}")
    else:
        print("[wolfmed_intake] No new PDFs in intake/")

    # ── Phase 2: Check completions and trigger next steps ──
    print("\n[wolfmed_intake] Checking completion status...")
    check_intake_completions()


def run_watch(category: str = "", mode: str = "auto", interval: int = WATCH_INTERVAL):
    """Continuous watcher loop."""
    print(f"[wolfmed_intake] Starting continuous watcher (interval: {interval}s)")
    print(f"[wolfmed_intake] Drop PDFs into: {INTAKE_DIR}")
    print(f"[wolfmed_intake] Press Ctrl+C to stop\n")

    sys.path.insert(0, str(SCRIPT_DIR))

    while True:
        try:
            run_once(category=category, mode=mode)
        except KeyboardInterrupt:
            print("\n[wolfmed_intake] Stopped.")
            break
        except Exception as e:
            print(f"[wolfmed_intake] ERROR in watch loop: {e}")

        print(f"\n[wolfmed_intake] Next scan in {interval}s...")
        time.sleep(interval)


# ─── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Wolfmed PDF intake watcher — process PDFs dropped into extractor/intake/"
    )
    parser.add_argument("--watch", action="store_true",
                        help="Run continuously (poll every 30s)")
    parser.add_argument("--check-merges", action="store_true",
                        help="Only check merge status, no new intake")
    parser.add_argument("--category", default="",
                        help="Override category (default: auto-detect from filename)")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "generate", "extract", "extract_procedures"],
                        help="Override extraction mode (default: auto)")
    parser.add_argument("--interval", type=int, default=WATCH_INTERVAL,
                        help=f"Watch interval in seconds (default: {WATCH_INTERVAL})")

    args = parser.parse_args()

    if args.check_merges:
        sys.path.insert(0, str(SCRIPT_DIR))
        check_intake_completions()
    elif args.watch:
        run_watch(category=args.category, mode=args.mode, interval=args.interval)
    else:
        run_once(category=args.category, mode=args.mode)
