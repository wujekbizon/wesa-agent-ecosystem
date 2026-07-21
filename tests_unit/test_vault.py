"""Tests for secure_core.vault — Fernet-backed vault + redaction."""

import pytest

from secure_core.vault import Vault, VaultError, redact


@pytest.fixture
def vault(tmp_path, monkeypatch):
    # Force the portable Fernet backend regardless of host OS, isolated to tmp_path.
    monkeypatch.setenv("WESA_VAULT_BACKEND", "fernet")
    return Vault(vault_dir=tmp_path / "vault")


def test_set_and_get_round_trip(vault):
    vault.set("TEST_SECRET", "supersecretvalue", owner="axel", status="active")
    assert vault.get("TEST_SECRET") == "supersecretvalue"


def test_get_missing_raises(vault):
    with pytest.raises(VaultError):
        vault.get("DOES_NOT_EXIST")


def test_list_names_does_not_expose_values(vault):
    vault.set("A", "secretvalueA")
    vault.set("B", "secretvalueB")
    names = vault.list_names()
    assert names == ["A", "B"]


def test_status_report_has_no_values(vault):
    vault.set("A", "secretvalueA", owner="greg", status="dead")
    report = vault.status_report()
    assert report[0]["name"] == "A"
    assert report[0]["owner"] == "greg"
    assert report[0]["status"] == "dead"
    assert "value" not in report[0]


def test_delete_removes_secret(vault):
    vault.set("A", "secretvalueA")
    vault.delete("A")
    with pytest.raises(VaultError):
        vault.get("A")


def test_values_for_redaction(vault):
    vault.set("A", "topsecret123")
    assert "topsecret123" in vault.values_for_redaction()


# ----------------------------------------------------------------------
# redact()
# ----------------------------------------------------------------------

def test_redact_google_api_key():
    text = "key is AIza" + "x" * 35
    assert "AIza" not in redact(text)
    assert "[REDACTED:GOOGLE_API_KEY]" in redact(text)


def test_redact_github_token():
    text = "token=ghp_" + "a" * 36
    assert "[REDACTED:GITHUB_TOKEN]" in redact(text)


def test_redact_database_url():
    user = "user"
    password = "secretpw123"
    host = "dbhost.example.com"
    text = f"DATABASE_URL=postgresql://{user}:{password}{chr(64)}{host}/db"
    out = redact(text)
    assert "secretpw123" not in out
    assert "[REDACTED:DATABASE_URL]" in out


def test_redact_extra_values():
    text = "the secret value abc123secretvalue was logged"
    out = redact(text, extra_values=["abc123secretvalue"])
    assert "abc123secretvalue" not in out
    assert "[REDACTED:VAULT_SECRET]" in out


def test_redact_empty_string():
    assert redact("") == ""


def test_redact_leaves_normal_text_alone():
    text = "Sprint E0 is repo hygiene work, no secrets here."
    assert redact(text) == text
