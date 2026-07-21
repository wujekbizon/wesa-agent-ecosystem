"""
WESA Agent Ecosystem - Connection Test

** DEAD AS OF 2026-06-13 (E0.3 audit) ** — Gemini access is gone, this will
always fail with no GOOGLE_API_KEY set. Kept for reference only. Not part of
the pytest suite (requires a live API key, contradicts E0.4 "no live API
calls in CI"). See src/gemini_agent.py for full deprecation note.

Run this first to verify your Gemini API key is working.

Usage:
    python src/test_connection.py
"""

import os

from dotenv import load_dotenv
from google import genai

load_dotenv()


def test_gemini():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key or api_key == "your_api_key_here":
        print("[FAIL] GOOGLE_API_KEY is not set in your .env file.")
        print("       Get your key from: https://aistudio.google.com → Get API Key")
        return False

    try:
        client = genai.Client(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

        response = client.models.generate_content(
            model=model_name,
            contents="Say exactly this: WESA Agent Ecosystem is online.",
        )
        print("[OK] Gemini API connected successfully.")
        print(f"     Model: {model_name}")
        print(f"     Response: {response.text.strip()}")
        return True

    except Exception as e:
        print(f"[FAIL] Gemini API error: {e}")
