"""
WESA Agent Ecosystem - Connection Test
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
        print(f"[OK] Gemini API connected successfully.")
        print(f"     Model: {model_name}")
        print(f"     Response: {response.text.strip()}")
        return True

    except Exception as e:
        print(f"[FAIL] Gemini API error: {e}")
        return False


if __name__ == "__main__":
    print("=== WESA Agent Ecosystem - Connection Test ===\n")
    gemini_ok = test_gemini()
    print()
    if gemini_ok:
        print("All checks passed. You are ready to build.")
    else:
        print("Fix the errors above before proceeding.")
