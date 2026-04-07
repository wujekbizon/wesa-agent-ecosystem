# Wolfmed Extractor Pipeline — Flow Documentation

**Version**: 1.0
**Last updated**: 2026-04-07
**Mode covered**: GENERATE (create questions from scratch)

---

## Overview

The pipeline takes a Polish medical PDF and produces a validated JSON question bank. It spans two root directories and involves Python scripts, the WESA daemon, and EX1 (Gemini agent).

---

## Directory Map

```
C:\Users\wujek\WesaTeam\
│
├── extractor\                        ← ORIGINAL extractor root
│   ├── intake\
│   │   └── <category>\               ← DROP PDF HERE (e.g. fizjologia\)
│   │       └── book.pdf
│   ├── chunks\
│   │   └── <category>\               ← Auto-created by pdf_chunker.py
│   │       ├── book_page_1-10.pdf
│   │       ├── book_page_1-10.txt    ← Extracted text (EX1 reads this)
│   │       ├── book_page_11-20.pdf
│   │       └── ...
│   ├── data\
│   │   └── <book>_manifest.json      ← Pipeline state tracker (created by pdf_chunker.py)
│   └── done\
│       └── <category>_all_merged.json ← FINAL OUTPUT
│
└── agent-ecosystem\                  ← WESA daemon root (WORKSPACE_ROOT)
    ├── extractor\
    │   └── outputs\                  ← Daemon writes agent outputs here
    │       ├── book_page_1-10_raw.json        ← EX1 generate output
    │       ├── book_page_1-10_validated.json  ← EX1 validate output
    │       └── ...
    ├── tasks\                        ← Daemon task queue (JSON files dropped here)
    │   ├── wm-ex-<id>.json           ← extract_pdf tasks (generate mode)
    │   └── wm-vl-<id>.json           ← validate_questions tasks
    └── scripts\
        ├── wolfmed_intake.py         ← Main orchestrator (run manually)
        ├── pdf_chunker.py            ← Phase 1: splits PDF into chunks
        └── merge_utils.py            ← Phase 4: merges validated JSONs
```

---

## Pipeline Phases

### Phase 1 — Chunk

**Trigger**: `wolfmed_intake.py` finds a PDF in `intake/<category>/` with no existing manifest.
**Script**: `pdf_chunker.py`
**What happens**:
- Splits PDF into fixed 10-page chunks
- Extracts text from each chunk to `.txt`
- Writes chunks to `extractor/chunks/<category>/`
- Creates `extractor/data/<book>_manifest.json` with all chunks at `status: pending`
- Drops `wm-ex-<id>.json` task files into `agent-ecosystem/tasks/`

**Manifest state after**: all chunks `queued`

---

### Phase 2 — Generate (EX1)

**Trigger**: Daemon picks up `wm-ex-*` task files automatically (polls every 3 seconds).
**Agent**: EX1 (gemini-3.1-pro-preview), MODE: GENERATE
**What happens**:
- Reads chunk text from the task prompt
- Generates 30–80 Polish Q&A questions grounded in source text
- Writes raw JSON to `agent-ecosystem/extractor/outputs/<chunk>_raw.json`
- Sets task status to `done`

**Manifest state after**: chunks still `queued` (manifest not updated until intake is re-run)

---

### Phase 3 — Validate (EX1)

**Trigger**: `wolfmed_intake.py` re-run — polls done extract tasks, queues validate tasks.
**Agent**: EX1 (gemini-3.1-pro-preview), MODE: VALIDATE
**What happens**:
- Checks each question: grounded in source? correct answer in text? distractors similar length? exactly one correct?
- Removes failing questions — no fixes, no regeneration
- Writes validated JSON to `agent-ecosystem/extractor/outputs/<chunk>_validated.json`
- Drops `wm-vl-<id>.json` task files into `agent-ecosystem/tasks/`
- Manifest chunk status updated to `validating`

**Manifest state after**: chunks `validating`, validated_count still 0

---

### Phase 4 — Merge (Python only, no LLM)

**Trigger**: `wolfmed_intake.py` re-run — detects `validated_count == total_chunks`.
**Script**: `merge_utils.py`
**What happens**:
- Loads all `*_validated.json` files from `agent-ecosystem/extractor/outputs/`
- Normalizes schema (ensures `meta.course` + `meta.category`)
- Deduplicates by question text (lowercase normalized)
- Checks UUID uniqueness against SQLite (`extractor/data/wolfmed.db`)
- Migrates legacy `uuid_registry.json` on first run
- Writes final file to `extractor/done/<category>_all_merged.json`
- Updates manifest `merge_status: done`

**Final output**: `C:\Users\wujek\WesaTeam\extractor\done\fizjologia_all_merged.json`

---

## How to Run

### Full run from scratch

```powershell
cd C:\Users\wujek\WesaTeam\agent-ecosystem\scripts

# Step 1: Drop PDF into intake
# (manually copy book.pdf to extractor\intake\fizjologia\)

# Step 2: Chunk + queue generate tasks
python wolfmed_intake.py --category fizjologia
# → chunks created, wm-ex-* tasks dropped, daemon picks up automatically

# Step 3: Wait for generate tasks to complete
# Monitor: check Wolfmed tab in dashboard or AXEL_BRIEFING.md

# Step 4: Queue validate tasks + trigger merge when ready
python wolfmed_intake.py --category fizjologia
# → if all generate done: queues validate tasks
# → if all validate done: fires merge automatically
```

### Resume after interruption

Just re-run the same command — intake is idempotent:
- PDF with existing manifest → skipped (no re-chunking)
- Done tasks → advances pipeline state
- Already validated → triggers merge

```powershell
python wolfmed_intake.py --category fizjologia
```

### Continuous watch mode (use with caution)

```powershell
python wolfmed_intake.py --watch --category fizjologia
```

Note: always use `--category` with `--watch` to avoid scanning all intake folders.

---

## Manifest — Status Flow

```
pending → queued → validating → validated
                                    ↓
                             validated_count == total_chunks
                                    ↓
                              merge fires
                                    ↓
                           merge_status: done
```

The manifest lives at `extractor/data/<book>_manifest.json` and is the single source of truth for pipeline state. Do not delete it mid-run.

---

## Key Rules

| Rule | Reason |
|------|--------|
| One category per run | Never mix categories in one pipeline run |
| Do not delete manifest mid-run | Intake uses it to avoid re-chunking and track progress |
| Do not run `--watch` without `--category` | Will re-scan all intake folders and re-queue existing PDFs |
| Merge output is append-safe | UUID registry prevents duplicate questions across runs |
| Agent outputs go to `agent-ecosystem/extractor/outputs/` | Daemon WORKSPACE_ROOT is `agent-ecosystem/` — all output_file paths are relative to it |

---

## Files Created Per Run

| File | Location | Created by |
|------|----------|------------|
| `<book>_page_N-M.pdf` | `extractor/chunks/<category>/` | pdf_chunker.py |
| `<book>_page_N-M.txt` | `extractor/chunks/<category>/` | pdf_chunker.py |
| `<book>_manifest.json` | `extractor/data/` | pdf_chunker.py |
| `wolfmed.db` | `extractor/data/` | merge_utils.py (first run) |
| `wm-ex-<id>.json` | `agent-ecosystem/tasks/` | wolfmed_intake.py |
| `wm-vl-<id>.json` | `agent-ecosystem/tasks/` | wolfmed_intake.py |
| `<chunk>_raw.json` | `agent-ecosystem/extractor/outputs/` | EX1 via daemon |
| `<chunk>_validated.json` | `agent-ecosystem/extractor/outputs/` | EX1 via daemon |
| `<category>_all_merged.json` | `extractor/done/` | merge_utils.py |
