"""
ChromaDB client for Lumio RAG service.
One collection per username — fully isolated per kid.
"""

import os
import logging
import chromadb
from chromadb.config import Settings

log = logging.getLogger("rag.chroma")

CHROMA_PERSIST_DIR = os.environ.get("CHROMA_PERSIST_DIR", "/data/chroma")

_client = None


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=CHROMA_PERSIST_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
        log.info(f"ChromaDB client initialized at {CHROMA_PERSIST_DIR}")
    return _client


def collection_name(username: str) -> str:
    """Each user gets their own collection: lumio_<username>"""
    return f"lumio_{username}"


def get_or_create_collection(username: str):
    client = get_client()
    name = collection_name(username)
    col = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )
    log.debug(f"Collection ready: {name}")
    return col


def delete_collection(username: str):
    client = get_client()
    name = collection_name(username)
    try:
        client.delete_collection(name)
        log.info(f"Deleted collection: {name}")
    except Exception as e:
        log.warning(f"Could not delete collection {name}: {e}")


def list_collections() -> list[str]:
    client = get_client()
    return [c.name for c in client.list_collections()]
