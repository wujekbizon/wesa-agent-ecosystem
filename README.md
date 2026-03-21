# WESA Agent Ecosystem

Internal AI workforce infrastructure for WESA.

## Stack
- **Team Lead**: Claude (via Cowork)
- **Worker agents**: Google Gemini 2.5 Flash + Ollama qwen3.5:35b (local)
- **Runtime**: Python 3.12, file-based task queue

## Quick Start
```bash
cp .env.example .env   # add your API keys
pip install -r requirements.txt
python src/test_connection.py
python daemon.py
```

## How it works
Drop a `.json` task file into `tasks/` → daemon processes it → report appears in `logs/`.

## Task types
- `research` / `write` / `analyze` / `general` → Gemini (fast, cloud)
- `code` → qwen3.5:35b (local, private)
