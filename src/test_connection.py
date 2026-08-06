"""
WESA Agent Ecosystem - Connection Test

** MIGRATED 2026-07-21 ** — Vertex AI (ADC) replaces the Gemini Developer
API key. Run this first after `gcloud auth application-default login` to
verify project/location/credentials are all wired correctly before starting
the daemon. See src/vertex_client.py for auth precedence and
_plans/2026-07-21_wesa_vertex-ai-adc-migration-plan.md for the setup runbook.

Usage:
    python src/test_connection.py
"""

import os
import sys
from pathlib import Path

# Allow `python src/test_connection.py` (repo-root-relative, per README Quick
# Start) to resolve `from src...` imports the same way `python daemon.py`
# does when run from the repo root — the script's own directory (src/) would
# otherwise be sys.path[0] instead of the repo root, and `import src` fails.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.vertex_client import PROJECT_ID, LOCATION, get_client


def test_gemini() -> bool:
    print(f"Project:  {PROJECT_ID}")
    print(f"Location: {LOCATION}")

    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print(
            f"[WARN] GOOGLE_CLOUD_PROJECT is not set in .env — falling back to the "
            f"hardcoded default '{PROJECT_ID}'. Set it explicitly in .env instead of "
            f"relying on the fallback."
        )

    try:
        client = get_client()
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

        response = client.models.generate_content(
            model=model_name,
            contents="Say exactly this: WESA Agent Ecosystem is online.",
        )
        if not response.text:
            print("[FAIL] Vertex AI responded with no text (possible safety block).")
            return False

        print("[OK] Vertex AI connected successfully.")
        print(f"     Model: {model_name}")
        print(f"     Response: {response.text.strip()}")
        return True

    except Exception as e:
        print(f"[FAIL] Vertex AI error: {e}")
        msg = str(e).lower()
        if "could not automatically determine credentials" in msg or "default credentials" in msg:
            print("       Run: gcloud auth application-default login")
        elif "permission" in msg or "403" in msg:
            print("       Check the account has roles/aiplatform.user on the project,")
            print("       and that aiplatform.googleapis.com is enabled:")
            print(f"       gcloud services enable aiplatform.googleapis.com --project={PROJECT_ID}")
        elif "404" in msg or "not found" in msg:
            print(f"       Check GOOGLE_CLOUD_PROJECT ({PROJECT_ID}) is a real, existing project ID.")
        return False


if __name__ == "__main__":
    sys.exit(0 if test_gemini() else 1)
