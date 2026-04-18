"""
Lumio Tutor Service
Conversational AI tutor. Pipeline:
  1. Receives question + username
  2. Calls rag-service to retrieve relevant context from user's materials
  3. Builds prompt: system + context + conversation history + question
  4. Calls Ollama LLM (on Greg's Windows PC via LAN)
  5. Streams response back to UI
"""

import os
import logging
from typing import List, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = logging.getLogger("tutor")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host-gateway:11434")
TUTOR_MODEL = os.environ.get("OLLAMA_TUTOR_MODEL", "qwen2.5:latest")
RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://rag:8768")

SYSTEM_PROMPT = """You are Lumio, a friendly and patient learning tutor.
You help students understand their study materials by answering questions based ONLY on the provided context.
If the answer is not in the context, say so clearly — do not invent information.
Keep answers clear, encouraging, and appropriately detailed for the student.
Always refer back to the source material when relevant."""

app = FastAPI(title="Lumio Tutor Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Message(BaseModel):
    role: str    # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    username: str
    message: str
    history: List[Message] = []   # last N turns for context window
    stream: bool = True


# ---------------------------------------------------------------------------
# RAG context retrieval
# ---------------------------------------------------------------------------
async def get_rag_context(username: str, question: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{RAG_SERVICE_URL}/query",
                json={"username": username, "question": question, "top_k": 5},
            )
            response.raise_for_status()
            data = response.json()
            context = data.get("context", "")
            warning = data.get("warning", "")
            if warning:
                log.warning(f"[{username}] RAG warning: {warning}")
            return context
        except Exception as e:
            log.error(f"[{username}] RAG service error: {e}")
            return ""


# ---------------------------------------------------------------------------
# Ollama chat
# ---------------------------------------------------------------------------
def build_ollama_messages(context: str, history: List[Message], question: str) -> List[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if context:
        messages.append({
            "role": "system",
            "content": f"Here is the relevant context from the student's materials:\n\n{context}"
        })
    else:
        messages.append({
            "role": "system",
            "content": "No relevant materials found. Let the student know they should upload documents first."
        })

    # Add conversation history (last 10 turns max)
    for msg in history[-10:]:
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": question})
    return messages


async def stream_ollama(messages: List[dict]) -> AsyncGenerator[str, None]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": TUTOR_MODEL, "messages": messages, "stream": True},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip():
                    import json
                    try:
                        data = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield f"data: {token}\n\n"
                        if data.get("done"):
                            yield "data: [DONE]\n\n"
                            return
                    except json.JSONDecodeError:
                        continue


async def call_ollama_sync(messages: List[dict]) -> str:
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": TUTOR_MODEL, "messages": messages, "stream": False},
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": TUTOR_MODEL,
        "ollama_url": OLLAMA_BASE_URL,
        "rag_url": RAG_SERVICE_URL,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Main chat endpoint.
    stream=true → Server-Sent Events (text/event-stream)
    stream=false → JSON response
    """
    # 1. Get RAG context
    context = await get_rag_context(req.username, req.message)

    # 2. Build messages
    messages = build_ollama_messages(context, req.history, req.message)

    # 3. Respond
    if req.stream:
        return StreamingResponse(
            stream_ollama(messages),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        answer = await call_ollama_sync(messages)
        return {
            "username": req.username,
            "question": req.message,
            "answer": answer,
            "context_used": bool(context),
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8769)))
