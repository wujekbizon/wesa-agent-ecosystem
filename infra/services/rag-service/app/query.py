"""
Lumio RAG — Query
Semantic search against a user's ChromaDB collection.
Returns top-k chunks + metadata.
"""

import logging
import os

import httpx

from .chroma_client import get_or_create_collection

log = logging.getLogger("rag.query")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host-gateway:11434")
EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
DEFAULT_TOP_K = 5


def embed_query(text: str) -> list[float]:
    url = f"{OLLAMA_BASE_URL}/api/embed"
    response = httpx.post(
        url,
        json={"model": EMBEDDING_MODEL, "input": [text]},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


def query_user_collection(username: str, question: str, top_k: int = DEFAULT_TOP_K) -> dict:
    """
    Semantic search in the user's collection.
    Returns top-k relevant chunks with metadata.
    """
    collection = get_or_create_collection(username)

    if collection.count() == 0:
        log.warning(f"[{username}] Collection is empty — no materials ingested yet")
        return {
            "username": username,
            "question": question,
            "results": [],
            "context": "",
            "warning": "No materials found. Please upload some documents first.",
        }

    query_embedding = embed_query(question)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for i, doc in enumerate(results["documents"][0]):
        chunks.append(
            {
                "text": doc,
                "source": results["metadatas"][0][i].get("source", "unknown"),
                "chunk_index": results["metadatas"][0][i].get("chunk_index", 0),
                "relevance_score": round(1 - results["distances"][0][i], 4),  # cosine → similarity
            }
        )

    # Build context string for LLM prompt
    context = "\n\n---\n\n".join(f"[Source: {c['source']}]\n{c['text']}" for c in chunks)

    return {
        "username": username,
        "question": question,
        "results": chunks,
        "context": context,
    }


def get_user_documents(username: str) -> list[str]:
    """List unique document names in the user's collection."""
    collection = get_or_create_collection(username)
    if collection.count() == 0:
        return []
    all_meta = collection.get(include=["metadatas"])
    sources = {m.get("source", "") for m in all_meta["metadatas"] if m.get("source")}
    return sorted(sources)
