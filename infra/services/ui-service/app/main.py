"""
Lumio UI Service
Serves the kids' web interface and acts as BFF (Backend for Frontend).
Proxies API calls to internal services so kids' browsers only talk to port 3000.
"""

import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

log = logging.getLogger("lumio.ui")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

STORAGE_API = os.environ.get("STORAGE_API", "http://storage:8767")
RAG_API = os.environ.get("RAG_API", "http://rag:8768")
TUTOR_API = os.environ.get("TUTOR_API", "http://tutor:8769")
TIME_WATCHER_API = os.environ.get("TIME_WATCHER_API", "http://time-watcher:8770")

app = FastAPI(title="Lumio UI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

HTML_PATH = Path(__file__).parent / "index.html"


# ---------------------------------------------------------------------------
# Serve main app
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# BFF proxy routes — kids' browser never knows about internal ports
# ---------------------------------------------------------------------------


@app.get("/api/users")
async def list_users():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{STORAGE_API}/users")
        return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/api/auth")
async def auth(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{STORAGE_API}/auth", json=body)
        return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/api/files/{username}")
async def list_files(username: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{STORAGE_API}/files/{username}")
        return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/api/files/{username}/upload")
async def upload_file(username: str, file: UploadFile = File(...)):
    async with httpx.AsyncClient(timeout=60.0) as client:
        files = {"file": (file.filename, await file.read(), file.content_type)}
        r = await client.post(f"{STORAGE_API}/files/{username}/upload", files=files)
        upload_result = r.json()

        # Auto-trigger ingestion after upload
        if r.status_code == 200 and upload_result.get("trigger_ingestion"):
            try:
                await client.post(
                    f"{RAG_API}/ingest",
                    json={"username": username, "filename": file.filename},
                )
            except Exception as e:
                log.warning(f"Auto-ingest trigger failed: {e}")

        return JSONResponse(content=upload_result, status_code=r.status_code)


@app.delete("/api/files/{username}/{filename}")
async def delete_file(username: str, filename: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.delete(f"{STORAGE_API}/files/{username}/{filename}")
        # Also remove from RAG index
        try:
            await client.delete(f"{RAG_API}/documents/{username}/{filename}")
        except Exception:
            pass
        return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/api/documents/{username}")
async def list_documents(username: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{RAG_API}/documents/{username}")
        return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    username = body.get("username")

    # Start time-watcher session on first message
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{TIME_WATCHER_API}/session/start", json={"username": username})
    except Exception:
        pass

    # Stream from tutor service
    async def stream_tutor():
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", f"{TUTOR_API}/chat", json=body) as resp:
                async for chunk in resp.aiter_text():
                    yield chunk

    return StreamingResponse(stream_tutor(), media_type="text/event-stream")


@app.get("/api/stats/{username}")
async def get_stats(username: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{TIME_WATCHER_API}/stats/{username}")
        return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/api/session/end")
async def end_session(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{TIME_WATCHER_API}/session/end", json=body)
        return JSONResponse(content=r.json(), status_code=r.status_code)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
