# Agent Ecosystem - Setup Guide
**Date**: 2026-03-21

---

## Prerequisites

- Python 3.11+ installed
- Google Gemini API key (from aistudio.google.com)
- Git

---

## Step 1: Copy Environment File

In the `agent-ecosystem/` folder:
```bash
cp .env.example .env
```

Open `.env` and replace `your_api_key_here` with your actual Gemini API key.

---

## Step 2: Install Dependencies

```bash
cd WesaTeam/agent-ecosystem
pip install -r requirements.txt
```

---

## Step 3: Test the Connection

```bash
python src/test_connection.py
```

Expected output:
```
=== WESA Agent Ecosystem - Connection Test ===

[OK] Gemini API connected successfully.
     Model: gemini-2.0-flash
     Response: WESA Agent Ecosystem is online.

All checks passed. You are ready to build.
```

---

## Step 4: (Optional) Install Ollama for Local Models

Download from: https://ollama.ai

After install, pull a model:
```bash
ollama pull qwen2.5-coder   # for coding tasks
ollama pull llama3.3        # for general reasoning
```

Test it is running:
```bash
curl http://localhost:11434/api/tags
```

---

## What's Next

Once the connection test passes, tell Claude (Team Lead) and we proceed to build Agent Daemon v0.1.

---

## Free Tier Limits (Google Gemini)

With your $300 credit, you effectively have unlimited usage for months.
After credit expires, Gemini 2.0 Flash pricing is very cheap (~$0.075 per 1M input tokens).

For reference: a typical agent task uses ~2,000-5,000 tokens. At that rate, $300 = millions of agent tasks.
