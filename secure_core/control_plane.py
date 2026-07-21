"""
secure_core.control_plane — S0 emergency lockdown for ntfy + dashboard.

This is the *interim* control-plane fix (security spec §4.7, T6/T7).
It is intentionally simple: a shared-secret prefix on inbound ntfy
messages, and a bearer token on dashboard mutations. Full HMAC-signed
envelopes with nonce/timestamp replay protection are S2 work, landing
with task envelope v2 in E1/E3.

Today's problem this closes: NTFY_TOPIC and the dashboard port are public/
open with zero auth — anyone who knows the topic or reaches the port can
pause/stop the daemon, queue arbitrary tasks (which trigger headless Axel
sessions), or mutate task/staging state.

Secrets used:
    NTFY_SHARED_SECRET   — required prefix on inbound ntfy commands
    DASHBOARD_TOKEN      — required bearer token for mutating dashboard routes

Both are generated on first use if missing and stored in the vault — never
printed in full to logs. `bootstrap()` prints them ONCE for Greg to copy
into his phone's ntfy shortcut / browser, with a reminder that secure_core's
redact() will scrub them from logs going forward.
"""

from __future__ import annotations

import secrets as _secrets

from .vault import Vault, redact

NTFY_SECRET_NAME = "NTFY_SHARED_SECRET"
DASHBOARD_TOKEN_NAME = "DASHBOARD_TOKEN"


def _get_or_create(vault: Vault, name: str, owner: str = "axel") -> tuple[str, bool]:
    """Return (value, created_now)."""
    try:
        return vault.get(name), False
    except Exception:
        value = _secrets.token_urlsafe(24)
        vault.set(name, value, owner=owner, status="active")
        return value, True


def bootstrap(vault: Vault | None = None) -> dict[str, str]:
    """Ensure NTFY_SHARED_SECRET and DASHBOARD_TOKEN exist. Returns a dict
    with both values and a `created` flag string for each — call this once
    interactively (not from the daemon loop) so the values can be shown to
    Greg for his ntfy shortcut and browser bookmark.
    """
    vault = vault or Vault()
    ntfy_secret, ntfy_created = _get_or_create(vault, NTFY_SECRET_NAME)
    dash_token, dash_created = _get_or_create(vault, DASHBOARD_TOKEN_NAME)
    return {
        "NTFY_SHARED_SECRET": ntfy_secret,
        "NTFY_SHARED_SECRET_created": str(ntfy_created),
        "DASHBOARD_TOKEN": dash_token,
        "DASHBOARD_TOKEN_created": str(dash_created),
    }


# ----------------------------------------------------------------------
# ntfy inbound authorization
# ----------------------------------------------------------------------


def authorize_ntfy(raw: str, vault: Vault | None = None) -> tuple[bool, str]:
    """Check an inbound ntfy message for the shared-secret prefix.

    Expected format: "<secret> <command...>"  e.g. "xK3p... status"

    Returns (authorized, remaining_message). If not authorized,
    remaining_message is the original raw text (for audit logging via
    redact() — never logged verbatim if it happens to contain the real
    secret as a wrong guess... callers should still redact).
    """
    vault = vault or Vault()
    try:
        expected = vault.get(NTFY_SECRET_NAME)
    except Exception:
        # No secret provisioned yet — fail closed, nothing is authorized.
        return False, raw

    raw = raw.strip()
    if not raw:
        return False, raw

    parts = raw.split(None, 1)
    if len(parts) == 1:
        token, rest = parts[0], ""
    else:
        token, rest = parts[0], parts[1]

    if _secrets.compare_digest(token, expected):
        return True, rest.strip()
    return False, raw


# ----------------------------------------------------------------------
# Dashboard bearer token (FastAPI dependency)
# ----------------------------------------------------------------------


def verify_dashboard_token(authorization: str | None, vault: Vault | None = None) -> bool:
    """Check an `Authorization: Bearer <token>` header value against the
    vault-stored DASHBOARD_TOKEN. Returns True/False — raising HTTPException
    is the caller's job (keeps this module FastAPI-agnostic)."""
    vault = vault or Vault()
    try:
        expected = vault.get(DASHBOARD_TOKEN_NAME)
    except Exception:
        # No token provisioned — fail closed.
        return False

    if not authorization or not authorization.startswith("Bearer "):
        return False

    token = authorization[len("Bearer ") :].strip()
    return _secrets.compare_digest(token, expected)


if __name__ == "__main__":
    # One-shot bootstrap: `python -m secure_core.control_plane`
    creds = bootstrap()
    print("Control-plane secrets provisioned (stored in vault, redacted below):")
    print(
        f"  NTFY_SHARED_SECRET:  {redact(creds['NTFY_SHARED_SECRET'])} (created={creds['NTFY_SHARED_SECRET_created']})"
    )
    print(
        f"  DASHBOARD_TOKEN:     {redact(creds['DASHBOARD_TOKEN'])} (created={creds['DASHBOARD_TOKEN_created']})"
    )
    print()
    print("These are redacted above by design. Run this script INTERACTIVELY")
    print("on Greg's machine and read secrets.enc / vault.get() directly to")
    print("copy the real values into:")
    print("  - the ntfy phone shortcut (prefix every command with the shared secret)")
    print("  - the dashboard browser bookmark (Authorization: Bearer <token>)")
