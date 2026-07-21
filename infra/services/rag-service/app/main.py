"""
Lumio RAG Service
FastAPI wrapper around ingestion + query modules.
Called by tutor-service and ui-service.
"""

import logging
import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .chroma_client import delete_collection, list_collections
from .ingestion import delete_document, ingest_pdf
from .query import get_user_documents, query_user_collection

log = logging.getLogger("rag.api")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

STORAGE_BASE = Path(os.environ.get("STORAGE_BASE_PATH", "/home"))
MATERIALS_SUBDIR = "materials"

app = FastAPI(title="Lumio RAG Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class IngestRequest(BaseModel):
    username: str
    filename: str  # must exist at /home/{username}/materials/{filename}


class QueryRequest(BaseModel):
    username: str
    question: str
    top_k: int = 5


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "collections": list_collections(),
    }


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
@app.post("/ingest")
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    """
    Trigger ingestion for a file already uploaded via storage-service.
    Runs in background — returns immediately.
    """
    pdf_path = STORAGE_BASE / req.username / MATERIALS_SUBDIR / req.filename
    if not pdf_path.exists():
        raise HTTPException(
            status_code=404, detail=f"File not found: {pdf_path}. Upload via storage-service first."
        )

    def run_ingestion():
        try:
            result = ingest_pdf(req.username, pdf_path)
            log.info(f"Ingestion complete: {result}")
        except Exception as e:
            log.error(f"Ingestion failed for {req.filename}: {e}")

    background_tasks.add_task(run_ingestion)
    return {
        "status": "ingestion_started",
        "username": req.username,
        "filename": req.filename,
    }


@app.post("/ingest/sync")
def ingest_sync(req: IngestRequest):
    """Synchronous ingest — waits for completion. Use for small files or testing."""
    pdf_path = STORAGE_BASE / req.username / MATERIALS_SUBDIR / req.filename
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {pdf_path}")
    result = ingest_pdf(req.username, pdf_path)
    return result


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
@app.post("/query")
def query(req: QueryRequest):
    """Semantic search in user's collection. Returns top-k chunks + context string."""
    return query_user_collection(req.username, req.question, req.top_k)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@app.get("/documents/{username}")
def list_documents(username: str):
    """List all ingested document names for a user."""
    docs = get_user_documents(username)
    return {"username": username, "documents": docs, "count": len(docs)}


@app.delete("/documents/{username}/{filename}")
def remove_document(username: str, filename: str):
    """Remove all chunks for a document from the user's collection."""
    result = delete_document(username, filename)
    return {"status": "deleted", "username": username, "filename": filename, **result}


@app.delete("/collections/{username}")
def remove_collection(username: str):
    """Wipe entire collection for a user (e.g. account deletion)."""
    delete_collection(username)
    return {"status": "deleted", "username": username}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8768)))
