# ** BLOCKED AS OF 2026-06-13 (E0.3 audit) **
# Requires GOOGLE_API_KEY (Gemini Files API + gemini-2.5-flash) — Gemini
# access is dead, free tier ended 2026-05-07, key removed from .env.
# This pipeline is part of the Wolfmed pielegniarstwo content build
# (WOLFMED_PIPELINE.md) and is currently blocked until E1's provider
# abstraction lands and a local-model extraction path (Ollama vision model,
# or a re-provisioned key via secure_core.vault) is wired in. Do not run
# as-is — it will exit(1) at GOOGLE_API_KEY check.
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# Gemini's hard limit — 10,800 frames per request.
# At 0.25 fps a 12-hour video = 10,800 frames, so 0.25 fps is safe for any
# lecture recording up to ~12 hours. Slides change every few seconds at most,
# so no information is lost at this rate.
DOWNSAMPLE_FPS = 0.25

# Load .env from agent-ecosystem root (same pattern as daemon)
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv optional — env vars may already be set

try:
    from google import genai
except ImportError:
    print(
        "Error: google-genai not found. Run: pip install google-genai --break-system-packages",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    from fpdf import FPDF
except ImportError:
    print("Install fpdf2: pip install fpdf2 --break-system-packages", file=sys.stderr)
    sys.exit(1)

if not shutil.which("ffmpeg"):
    print(
        "Error: ffmpeg not found in PATH. Install it and make sure it's accessible.",
        file=sys.stderr,
    )
    print("  Windows: https://ffmpeg.org/download.html  (add bin/ folder to PATH)", file=sys.stderr)
    sys.exit(1)

# PATH CONSTANTS
SCRIPT_DIR = Path(__file__).resolve().parent
ECOSYSTEM_DIR = SCRIPT_DIR.parent
WESA_DIR = ECOSYSTEM_DIR.parent
WOLFMED_DIR = WESA_DIR / "wolfmed"
LECTURES_DIR = WOLFMED_DIR / "lectures"
RAG_SOURCES_DIR = WOLFMED_DIR / "rag-sources"
STATE_FILE = LECTURES_DIR / "_extraction_state.json"

EXTRACTION_PROMPT = """This is a recorded Polish university nursing lecture video captured via OBS from a Microsoft Teams meeting.

The video starts with the Microsoft Teams meeting interface — participant tiles, names, chat sidebar,
waiting room, and meeting controls. This Teams UI content must be completely ignored.

Extraction begins ONLY when the lecturer shares their screen and a PowerPoint/presentation slide
fills the majority of the screen. That is the signal that lecture content has started.

Your task:
1. Ignore everything before the presentation starts — Teams UI, participant names, chat messages,
   meeting controls, video tiles, waiting and waiting room screens
2. Once the presentation starts, identify each distinct slide shown
3. For each slide, extract ALL text visible on the slide exactly as written
4. Preserve Polish characters exactly (ą, ę, ó, ś, ź, ż, ć, ń, ł)
5. Output format:

--- SLIDE 1 ---
[all text from slide 1]

--- SLIDE 2 ---
[all text from slide 2]

Rules:
- SKIP: anything that looks like Teams UI (participant names, chat, meeting controls, video thumbnails)
- SKIP: duplicate slides (same content appearing more than once — output only once)
- SKIP: slides containing only the course title, lecturer name, or university logo
- SKIP: any screen that is not a presentation slide (file explorer, desktop, browser, etc.)
- Do not add interpretation or explanation — extract visible slide text only
- If a slide contains only a diagram or image with no readable text, output: [DIAGRAM - no text]
- If unsure whether a slide is a slide or Teams UI, skip it"""


def get_numeric_suffix(filename: str) -> int:
    """Extracts the trailing integer from filename for sorting."""
    match = re.search(r"_(\d+)\.mp4$", filename)
    return int(match.group(1)) if match else 0


def strip_polish_diacritics(text: str) -> str:
    """Replaces Polish characters with ASCII equivalents for PDF compatibility."""
    mapping = {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
        "Ą": "A",
        "Ć": "C",
        "Ę": "E",
        "Ł": "L",
        "Ń": "N",
        "Ó": "O",
        "Ś": "S",
        "Ź": "Z",
        "Ż": "Z",
    }
    for char, replacement in mapping.items():
        text = text.replace(char, replacement)
    return text


class LectureExtractor:
    def __init__(self, force: bool = False):
        self.force = force
        self.state = self._load_state()
        self._ensure_dirs()

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print(
                "Error: GOOGLE_API_KEY not set. Add it to agent-ecosystem/.env or set it in your shell.",
                file=sys.stderr,
            )
            sys.exit(1)
        self.client = genai.Client(api_key=api_key)

    def _ensure_dirs(self):
        """Ensure required directories exist."""
        for d in [LECTURES_DIR, RAG_SOURCES_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Failed to load state file: {e}", file=sys.stderr)
        return {"processed": {}}

    def _save_state(
        self, relative_path: str, status: str, slides_count: int = 0, gemini_file_name: str = ""
    ):
        self.state["processed"][relative_path] = {
            "status": status,
            "slides_extracted": slides_count,
            "processed_at": datetime.now().isoformat(),
            # Store Gemini file name so we can resume polling after a Ctrl+C
            # without re-uploading the video
            "gemini_file_name": gemini_file_name,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _downsample_video(self, video_path: Path, tmp_dir: str) -> Path:
        """
        Re-encode video at DOWNSAMPLE_FPS using ffmpeg.
        Output is written to a temp directory and the path is returned.
        Raises subprocess.CalledProcessError on ffmpeg failure.
        """
        out_path = Path(tmp_dir) / f"{video_path.stem}_low.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={DOWNSAMPLE_FPS}",
            "-c:v",
            "libx264",
            "-crf",
            "28",  # decent quality, small file
            "-an",  # drop audio — not needed for slide extraction
            "-preset",
            "ultrafast",  # speed > compression (it's a temp file)
            str(out_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path

    def _poll_until_active(self, file_obj, max_wait_seconds: int = 600) -> bool:
        """
        Poll Gemini Files API until file is ACTIVE (or FAILED/timeout).
        Prints a dot every 5 s so the terminal doesn't look frozen.
        Returns True on success.
        """
        elapsed = 0
        interval = 5
        while elapsed < max_wait_seconds:
            file_obj = self.client.files.get(name=file_obj.name)
            state = file_obj.state.name
            if state == "ACTIVE":
                return True
            if state == "FAILED":
                print("\n  File processing FAILED on Gemini side.", file=sys.stderr)
                return False
            print(".", end="", flush=True)
            time.sleep(interval)
            elapsed += interval
        return False  # timeout

    def process_subject(self, subject_slug: str):
        subject_dir = LECTURES_DIR / subject_slug
        if not subject_dir.exists():
            print(f"Error: Subject directory {subject_dir} does not exist.", file=sys.stderr)
            return

        # Get all mp4 files, sorted by numeric suffix
        video_files = sorted(
            list(subject_dir.glob("*.mp4")), key=lambda p: get_numeric_suffix(p.name)
        )

        if not video_files:
            print(f"No videos found for subject: {subject_slug}")
            return

        print(f"Processing subject: {subject_slug}")
        all_slides_for_subject = []
        videos_processed_in_run = 0

        for idx, video_path in enumerate(video_files, 1):
            rel_path = f"{subject_slug}/{video_path.name}"
            existing = self.state["processed"].get(rel_path, {})

            # Skip if already done and not forcing
            if not self.force and existing.get("status") == "done":
                continue

            print(f"  Video {idx}/{len(video_files)}: {video_path.name} — ", end="", flush=True)

            try:
                # Stage 1: Size Check (1.9 GB limit)
                if video_path.stat().st_size > 1.9 * 1024 * 1024 * 1024:
                    print("skipped (too large)")
                    self._save_state(rel_path, "skipped_too_large")
                    continue

                # Stage 2: Downsample via ffmpeg → temp file
                with tempfile.TemporaryDirectory() as tmp_dir:
                    print("downsampling...", end="", flush=True)
                    try:
                        upload_path = self._downsample_video(video_path, tmp_dir)
                    except subprocess.CalledProcessError as e:
                        print(f"ffmpeg error — {e}", file=sys.stderr)
                        self._save_state(rel_path, "ffmpeg_error")
                        upload_path = None

                    if upload_path is None:
                        continue

                    # Stage 3: Upload (or resume from a previous interrupted upload)
                    gemini_file_name = (
                        existing.get("gemini_file_name", "") if not self.force else ""
                    )
                    file_obj = None

                    if gemini_file_name and existing.get("status") == "uploading":
                        print(" resuming poll (already uploaded)...", end="", flush=True)
                        try:
                            file_obj = self.client.files.get(name=gemini_file_name)
                        except Exception:
                            file_obj = None

                    if file_obj is None:
                        print(" uploading...", end="", flush=True)
                        file_obj = self.client.files.upload(file=upload_path)
                        self._save_state(rel_path, "uploading", gemini_file_name=file_obj.name)
                        print(" polling", end="", flush=True)

                    # Stage 4: Poll until ACTIVE
                    poll_ok = self._poll_until_active(file_obj)
                    if not poll_ok:
                        print(" upload/processing timeout", file=sys.stderr)
                        self._save_state(rel_path, "upload_timeout", gemini_file_name=file_obj.name)
                # temp dir and downsampled file deleted here

                if not poll_ok:
                    continue

                print(" extracting slides...", end="", flush=True)

                # Stage 4: Extraction
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash", contents=[file_obj, EXTRACTION_PROMPT]
                )
                raw_text = response.text

                # Stage 5: Parse — split on '--- SLIDE X ---' markers
                parts = re.split(r"--- SLIDE \d+ ---", raw_text)
                slides = []
                for part in parts:
                    # Strip leading/trailing blank lines but preserve internal line breaks
                    # so bullet points and headers stay on separate lines (important for RAG)
                    clean_part = part.strip()
                    if not clean_part:
                        continue
                    if clean_part == "[DIAGRAM - no text]":
                        continue
                    # Collapse runs of 3+ blank lines to 2 (keeps structure, removes excess)
                    clean_part = re.sub(r"\n{3,}", "\n\n", clean_part)
                    slides.append(clean_part)

                # Stage 6: Accumulate and save state
                all_slides_for_subject.extend(slides)
                self._save_state(rel_path, "done", len(slides), gemini_file_name=file_obj.name)
                print(f" done ({len(slides)} slides extracted)")
                videos_processed_in_run += 1

                del response

            except KeyboardInterrupt:
                print("\n[interrupted] State saved. Re-run to continue.", file=sys.stderr)
                sys.exit(0)
            except Exception as e:
                print(f"error — {e}", file=sys.stderr)
                self._save_state(rel_path, "error")
                continue

        if all_slides_for_subject:
            self.generate_outputs(subject_slug, all_slides_for_subject)
            print(f"Subject {subject_slug} complete — {len(all_slides_for_subject)} slides total")
        elif videos_processed_in_run == 0:
            print(f"No new videos were processed for {subject_slug}.")

    def generate_outputs(self, subject_slug: str, slides: list[str]):
        output_dir = RAG_SOURCES_DIR / subject_slug
        output_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")

        # 1. Text File (plain UTF-8 — keep Polish chars intact for RAG)
        txt_path = output_dir / f"{subject_slug}_combined.txt"
        header = (
            f"# {subject_slug}\n# Wolfmed RAG Source — Pielegniarstwo\n# Generated: {date_str}\n\n"
        )
        content_body = "\n\n---\n\n".join(slides)

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(header + content_body)
        print(f"  Written: {txt_path.relative_to(WESA_DIR)}")

        # 2. PDF File
        pdf_path = output_dir / f"{subject_slug}_combined.pdf"
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Title Page
        pdf.add_page()
        pdf.set_font("helvetica", "B", 16)
        title_text = f"{subject_slug.capitalize()}\nWolfmed RAG Source\nGenerated: {date_str}"
        pdf.multi_cell(0, 10, txt=strip_polish_diacritics(title_text), align="C")

        # Content Pages
        pdf.add_page()
        pdf.set_font("helvetica", size=10)

        for slide in slides:
            if pdf.get_y() > 270:
                pdf.add_page()
            # Strip diacritics for PDF but keep line breaks (multi_cell handles \n)
            clean_slide = strip_polish_diacritics(slide)
            pdf.multi_cell(0, 6, txt=clean_slide)
            pdf.ln(4)  # spacing between slides

        pdf.output(str(pdf_path))
        print(f"  Written: {pdf_path.relative_to(WESA_DIR)}")


def main():
    parser = argparse.ArgumentParser(description="Extract lecture slides from MP4 using Gemini.")
    parser.add_argument("--subject", type=str, help="Process only this subject folder")
    parser.add_argument("--force", action="store_true", help="Ignore state and reprocess")

    args = parser.parse_args()

    LectureExtractor(force=args.force)
