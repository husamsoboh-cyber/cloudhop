"""Unit tests for cloudhop.settings module."""

import os
from unittest.mock import patch

import pytest

from cloudhop.settings import (
    _default_settings,
    load_settings,
    load_settings_with_secrets,
    save_settings,
)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path):
    """Redirect _SETTINGS_FILE to a temp directory for every test."""
    fake_file = str(tmp_path / "settings.json")
    with patch("cloudhop.settings._SETTINGS_FILE", fake_file):
        yield fake_file


def test_default_settings_returns_dict():
    defaults = _default_settings()
    assert isinstance(defaults, dict)
    expected_keys = {
        "email_enabled",
        "email_smtp_host",
        "email_smtp_port",
        "email_smtp_tls",
        "email_from",
        "email_to",
        "email_username",
        "email_password",
        "email_on_complete",
        "email_on_failure",
    }
    assert set(defaults.keys()) == expected_keys


def test_load_settings_empty_file():
    """load_settings() with no file on disk returns defaults."""
    settings = load_settings()
    assert settings["email_smtp_port"] == 587
    assert settings["email_enabled"] is False


def test_save_and_load_roundtrip(_isolate_settings):
    save_settings({"email_smtp_host": "smtp.example.com", "email_smtp_port": 465})
    settings = load_settings()
    assert settings["email_smtp_host"] == "smtp.example.com"
    assert settings["email_smtp_port"] == 465


def test_password_hidden_in_load(_isolate_settings):
    save_settings({"email_password": "supersecret"})
    settings = load_settings()
    assert settings["email_password"] == ""


def test_password_present_in_load_with_secrets(_isolate_settings):
    save_settings({"email_password": "supersecret"})
    settings = load_settings_with_secrets()
    assert settings["email_password"] == "supersecret"


def test_password_preserved_on_empty_save(_isolate_settings):
    save_settings({"email_password": "original"})
    # Save again with empty password - should preserve existing
    save_settings({"email_password": ""})
    settings = load_settings_with_secrets()
    assert settings["email_password"] == "original"


def test_save_invalid_port_zero():
    result = save_settings({"email_smtp_port": 0})
    assert result["ok"] is False


def test_save_invalid_port_high():
    result = save_settings({"email_smtp_port": 70000})
    assert result["ok"] is False


def test_save_invalid_email_no_at():
    result = save_settings({"email_from": "bademail"})
    assert result["ok"] is False


def test_save_valid_settings():
    result = save_settings(
        {
            "email_smtp_host": "smtp.example.com",
            "email_smtp_port": 587,
            "email_from": "a@b.com",
            "email_to": "c@d.com",
        }
    )
    assert result["ok"] is True


def test_atomic_write_uses_tmp(_isolate_settings):
    """Verify that save uses os.replace for atomic writes."""
    real_replace = os.replace
    with patch("cloudhop.settings.os.replace", side_effect=real_replace) as mock_replace:
        save_settings({"email_smtp_host": "test"})
        assert mock_replace.called
        args = mock_replace.call_args[0]
        assert args[0].endswith(".tmp")
