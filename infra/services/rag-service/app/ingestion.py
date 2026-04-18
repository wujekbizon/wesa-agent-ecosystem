"""
Lumio RAG — Ingestion Pipeline
PDF → text → chunks → Ollama embeddings → ChromaDB
"""

import os
import logging
import hashlib
from pathlib import Path
from typing import List

import httpx
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .chroma_client import get_or_create_collection

log = logging.getLogger("rag.ingestion")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host-gateway:11434")
EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


# ---------------------------------------------------------------------------
# Ollama embedding
# ---------------------------------------------------------------------------
def embed_texts(texts: List[str]) -> List[List[float]]:
    """Call Ollama /api/embed to get embeddings for a batch of texts."""
    url = f"{OLLAMA_BASE_URL}/api/embed"
    response = httpx.post(
        url,
        json={"model": EMBEDDING_MODEL, "input": texts},
        timeout=120.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["embeddings"]


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------
def extract_text_from_pdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def chunk_text(text: str) -> List[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(text)


# ---------------------------------------------------------------------------
# Document ID helper — stable, dedup-safe
# ---------------------------------------------------------------------------
def doc_id(username: str, filename: str, chunk_index: int) -> str:
    base = f"{username}::{filename}::{chunk_index}"
    return hashlib.md5(base.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main ingestion entry point
# ---------------------------------------------------------------------------
def ingest_pdf(username: str, pdf_path: Path) -> dict:
    """
    Full pipeline: PDF → text → chunks → embeddings → ChromaDB.
    Returns a summary dict.
    """
    filename = pdf_path.name
    log.info(f"[{username}] Ingesting: {filename}")

    # 1. Extract text
    text = extract_text_from_pdf(pdf_path)
    if not text.strip():
        log.warning(f"[{username}] No text extracted from {filename}")
        return {"status": "empty", "filename": filename, "chunks": 0}

    # 2. Chunk
    chunks = chunk_text(text)
    log.info(f"[{username}] {filename} → {len(chunks)} chunks")

    # 3. Embed (batch in groups of 32 to avoid Ollama timeouts)
    all_embeddings = []
    batch_size = 32
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        embeddings = embed_texts(batch)
        all_embeddings.extend(embeddings)
        log.debug(f"[{username}] Embedded batch {i//batch_size + 1}")

    # 4. Store in ChromaDB
    collection = get_or_create_collection(username)
    ids = [doc_id(username, filename, i) for i in range(len(chunks))]
    metadatas = [{"username": username, "source": filename, "chunk_index": i} for i in range(len(chunks))]

    collection.upsert(
        ids=ids,
        embeddings=all_embeddings,
        documents=chunks,
        metadatas=metadatas,
    )

    log.info(f"[{username}] Ingestion complete: {filename} ({len(chunks)} chunks stored)")
    return {
        "status": "ok",
        "username": username,
        "filename": filename,
        "chunks": len(chunks),
        "collection": collection.name,
    }


def delete_document(username: str, filename: str):
    """Remove all chunks for a given document from the user's collection."""
    collection = get_or_create_collection(username)
    results = collection.get(where={"source": filename})
    if results["ids"]:
        collection.delete(ids=results["ids"])
        log.info(f"[{username}] Deleted {len(results['ids'])} chunks for {filename}")
    return {"deleted_chunks": len(results["ids"]) if results["ids"] else 0}
