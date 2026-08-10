"""Tests for AccountData model."""
from __future__ import annotations

import pytest

from services.models import AccountData


class TestAccountDataFromDict:
    def test_from_dict_creates_model(self):
        data = {
            "email": "test@example.com",
            "password": "secret123",
            "client_id": "client-abc",
            "refresh_token": "rt_abc123",
            "access_token": "eyJ.abc.xyz",
            "openai_refresh_token": "ort_xyz",
            "id_token": "idtoken123",
            "openai_password": "openai_secret",
            "name": "Test User",
            "birthdate": "1990-01-01",
            "proxy": "http://proxy:8080",
            "status": "success",
            "error": "",
            "failure_type": "",
        }
        model = AccountData.from_dict(data)
        assert model.email == "test@example.com"
        assert model.password == "secret123"
        assert model.client_id == "client-abc"
        assert model.refresh_token == "rt_abc123"
        assert model.access_token == "eyJ.abc.xyz"
        assert model.openai_refresh_token == "ort_xyz"
        assert model.id_token == "idtoken123"
        assert model.openai_password == "openai_secret"
        assert model.name == "Test User"
        assert model.birthdate == "1990-01-01"
        assert model.proxy == "http://proxy:8080"
        assert model.status == "success"
        assert model.error == ""
        assert model.failure_type == ""

    def test_from_dict_defaults(self):
        model = AccountData.from_dict({})
        assert model.email == ""
        assert model.password == ""
        assert model.client_id == ""
        assert model.refresh_token == ""
        assert model.access_token == ""
        assert model.openai_refresh_token == ""
        assert model.id_token == ""
        assert model.openai_password == ""
        assert model.name == ""
        assert model.birthdate == ""
        assert model.proxy == ""
        assert model.status == ""
        assert model.error == ""
        assert model.failure_type == ""

    def test_from_dict_coerces_none(self):
        data = {
            "email": "test@example.com",
            "password": None,
            "status": None,
            "name": None,
        }
        model = AccountData.from_dict(data)
        assert model.email == "test@example.com"
        assert model.password == ""
        assert model.status == ""

    def test_immutable(self):
        model = AccountData.from_dict({"email": "test@example.com"})
        with pytest.raises(AttributeError):
            model.email = "changed@example.com"


class TestAccountDataSafeSnapshot:
    def test_safe_snapshot_redacts_secrets(self):
        model = AccountData.from_dict({
            "email": "test@example.com",
            "password": "my_password",
            "refresh_token": "rt_secret",
            "access_token": "eyJ.abc",
            "client_id": "client-abc",
            "name": "Test User",
            "status": "success",
        })
        snapshot = model.safe_snapshot()
        # Email should be preserved
        assert snapshot["email"] == "test@example.com"
        # Client_id should be preserved
        assert snapshot["client_id"] == "client-abc"
        # Password should be redacted
        assert snapshot["password"] == "[REDACTED]"
        # Refresh token should be redacted
        assert snapshot["refresh_token"] == "[REDACTED]"
        # Access token should be redacted
        assert snapshot["access_token"] == "[REDACTED]"
        # Non-sensitive fields should be preserved
        assert snapshot["name"] == "Test User"
        assert snapshot["status"] == "success"

    def test_safe_snapshot_empty_values(self):
        model = AccountData.from_dict({})
        snapshot = model.safe_snapshot()
        assert snapshot["email"] == ""
        assert snapshot["password"] == ""
        assert snapshot["status"] == ""

    def test_safe_snapshot_returns_dict(self):
        model = AccountData.from_dict({"email": "a@b.com"})
        snapshot = model.safe_snapshot()
        assert isinstance(snapshot, dict)