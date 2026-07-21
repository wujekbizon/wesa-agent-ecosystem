"""Embedding client for the memory system.

Embeddings are produced in Python (Ollama HTTP API) and passed to Postgres as
a vector literal — pgvector, unlike Oracle, cannot embed inside SQL. That puts
an Ollama round-trip on the retrieval hot path, so this module is built to fail
*loudly and catchably*: when Ollama is unreachable, embed() raises
EmbeddingUnavailable and the retrieval layer cascades to lexical search rather
than hanging or crashing the task.

The structured row is truth; the embedding is a rebuildable cache. If the model
changes, bump EMBED_MODEL/EMBED_DIM, update VECTOR(n) in schema_level2.sql, and
re-embed from the content/summary columns.
"""
from __future__ import annotations

import os
from typing import Protocol

# httpx is imported lazily inside OllamaEmbedder.embed so this module loads even
# where httpx isn't installed (e.g. test envs that only use a fake embedder).

# nomic-embed-text emits 768-dim vectors. (The plan said 384 — that was the
# Oracle demo's all-MiniLM model. If you swap models, change BOTH this and the
# VECTOR(n) column in schema_level2.sql, then re-embed.)
EMBED_MODEL = os.getenv("WESA_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.getenv("WESA_EMBED_DIM", "768"))

_DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_DEFAULT_TIMEOUT_S = float(os.getenv("WESA_EMBED_TIMEOUT_S", "10"))


def _normalize_host(h: str | None) -> str:
    """Make an Ollama host usable as a client URL.

    WESA's env often carries OLLAMA_HOST=0.0.0.0 (a server *bind* address) with
    no scheme — which is not a valid client target and was behind the
    '0.0.0.0/api/generate: No scheme supplied' failures in the briefing. Coerce
    a missing scheme to http:// and rewrite the bind-all address to loopback.
    """
    h = (h or _DEFAULT_OLLAMA_HOST).strip().rstrip("/")
    if "://" not in h:
        h = "http://" + h
    h = h.replace("://0.0.0.0", "://127.0.0.1")
    # Default the Ollama port if the host part carries none (e.g. bare
    # "0.0.0.0" -> "http://127.0.0.1" would otherwise hit port 80).
    scheme, _, rest = h.partition("://")
    hostport = rest.split("/", 1)[0]
    if ":" not in hostport:
        h = f"{scheme}://{hostport}:11434" + (("/" + rest.split("/", 1)[1]) if "/" in rest else "")
    return h


class EmbeddingUnavailable(RuntimeError):
    """Raised when an embedding cannot be produced (Ollama down, timeout, model
    missing, wrong dimension). Retrieval catches this and cascades to lexical."""


class Embedder(Protocol):
    """Anything that turns text into a vector. Lets tests inject a fake."""

    def embed(self, text: str) -> list[float]: ...

    @property
    def dim(self) -> int: ...


class OllamaEmbedder:
    """Calls Ollama's /api/embeddings. One text in, one vector out."""

    def __init__(
        self,
        host: str | None = None,
        model: str = EMBED_MODEL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        dim: int = EMBED_DIM,
    ):
        self.host = _normalize_host(host)
        self.model = model
        self.timeout_s = timeout_s
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        import httpx

        url = f"{self.host}/api/embeddings"
        try:
            resp = httpx.post(
                url,
                json={"model": self.model, "prompt": text},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # httpx errors, JSON errors, connection refused
            raise EmbeddingUnavailable(
                f"Ollama embed failed at {url} ({self.model}): {e}"
            ) from e

        vec = data.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise EmbeddingUnavailable(
                f"Ollama returned no embedding for model {self.model!r}"
            )
        if len(vec) != self._dim:
            raise EmbeddingUnavailable(
                f"embedding dim {len(vec)} != expected {self._dim} for "
                f"{self.model!r}; fix EMBED_DIM and the VECTOR(n) column"
            )
        return [float(x) for x in vec]


def to_pgvector_literal(vec: list[float]) -> str:
    """Render a vector as the pgvector text literal: '[0.1,0.2,...]'.
    Bound as a normal string param and cast ::vector in SQL."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def default_embedder() -> OllamaEmbedder:
    return OllamaEmbedder()
