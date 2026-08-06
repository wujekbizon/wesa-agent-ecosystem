"""
WESA Agent Ecosystem - Vertex AI client factory

** MIGRATED 2026-07-21 ** — Gemini Developer API (GOOGLE_API_KEY) replaced by
Vertex AI (OAuth2/ADC). Vertex AI has no API-key auth path, so every caller
that used to do `genai.Client(api_key=...)` now goes through get_client()
here instead, which resolves auth the same way every time.

Auth precedence (see get_credentials()):
  1. GOOGLE_SERVICE_ACCOUNT_KEY env var (base64 or raw JSON service-account
     key) — for any future headless/serverless context without a local
     gcloud session. Not used today: the daemon runs on Greg's own Windows
     machine, same as Ollama. Wired now for parity with the Wolfmed pattern
     and so we don't have to revisit this file if that changes.
  2. Application Default Credentials (ADC) — `gcloud auth
     application-default login`, run once on Greg's machine. This is the
     path actually in use. When GOOGLE_SERVICE_ACCOUNT_KEY is unset,
     `credentials=None` is passed to genai.Client() and the SDK falls back
     to ADC discovery automatically (confirmed against google-genai's
     Client._get_api_client — see docstring there).

GCP project: shared with Wolfmed — "My First Project"
(project-9d10f80c-d5df-459f-8d8, org greg-wesa-org, has $300 free credit).
Originally planned as a dedicated, isolated WESA project (matching the
"WESA = commercial, Wolfmed = personal legacy" separation used elsewhere);
Greg opted to share the existing project instead 2026-07-21. WESA and
Wolfmed usage/quota/billing now mix on this one project — worth revisiting
if that becomes a problem (noisy-neighbor quota, cost attribution). Region:
europe-west3 (Frankfurt — closest to Poland, and RAG/embedding
region-pinning doesn't apply here since agent-ecosystem doesn't use Vertex
RAG Engine).

Setup runbook (project creation, API enable, ADC login): see
`_plans/2026-07-21_wesa_vertex-ai-adc-migration-plan.md`.

Reference this was ported from: vertexaisetupguide.md (Wolfmed's TypeScript
`@google/genai` implementation) — same auth precedence, same env var names
where the concepts carry over, adapted to the Python SDK's `credentials=`
kwarg (the Python client takes a `google.auth.credentials.Credentials`
object directly; there's no `googleAuthOptions` dict like the JS client).
"""
import base64
import json
import os

from google import genai
from google.auth.credentials import Credentials
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

# Empty-string env vars (e.g. a blank line in .env) must fall back to the
# default too, so use `or`, not a bare os.getenv default — a blank
# GOOGLE_CLOUD_PROJECT would otherwise silently become "" and fail auth with
# an opaque error instead of a clear one.
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT") or "project-9d10f80c-d5df-459f-8d8"
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION") or "europe-west3"

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Sentinel so "resolved to None (use ADC)" is distinguishable from "not
# resolved yet" — a plain `None` cache would re-parse the env var on every
# call when no service-account key is configured (the common case).
_UNRESOLVED = object()
_cached_credentials: Credentials | None | object = _UNRESOLVED


def _load_service_account_credentials() -> Credentials | None:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY", "").strip()
    if not raw:
        return None
    try:
        raw_json = raw if raw.startswith("{") else base64.b64decode(raw).decode("utf-8")
        info = json.loads(raw_json)
        if "client_email" not in info or "private_key" not in info:
            raise ValueError("missing client_email or private_key")
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    except Exception as e:
        raise RuntimeError(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_KEY: {e}") from e


def get_credentials() -> Credentials | None:
    """Explicit service-account credentials if GOOGLE_SERVICE_ACCOUNT_KEY is
    set, else None — which tells genai.Client() to fall back to ADC."""
    global _cached_credentials
    if _cached_credentials is _UNRESOLVED:
        _cached_credentials = _load_service_account_credentials()
    return _cached_credentials


def get_client() -> genai.Client:
    """Shared Vertex-AI-mode Gemini client. Every agent/script should get its
    client from here rather than constructing genai.Client() directly, so
    auth resolution (and any future change to it) lives in one place."""
    return genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        credentials=get_credentials(),
    )
