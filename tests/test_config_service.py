"""Tests for RuntimeConfig service."""
from __future__ import annotations

import dataclasses
import json

import pytest

from services.config_service import (
    RuntimeConfig,
    ConfigNotFoundError,
    ConfigValidationError,
)


def _write_config(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


class TestRuntimeConfigLoad:
    def test_load_config(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {
            "auth_key": "test-key",
            "port": 23457,
            "register_concurrency": 1,
        })
        config = RuntimeConfig.load_config(cfg_path)
        assert config.get("auth_key") == "test-key"
        assert config.get("port") == 23457
        assert config.get("register_concurrency") == 1

    def test_load_config_not_found(self):
        with pytest.raises(ConfigNotFoundError):
            RuntimeConfig.load_config("/nonexistent/config.json")

    def test_load_config_invalid_json(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{invalid json}", encoding="utf-8")
        with pytest.raises(ConfigValidationError):
            RuntimeConfig.load_config(cfg_path)

    def test_load_config_not_object(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, ["not", "a", "dict"])
        with pytest.raises(ConfigValidationError, match="JSON object"):
            RuntimeConfig.load_config(cfg_path)


class TestRuntimeConfigGetSet:
    def test_get_setting(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "test-key", "port": 23457})
        config = RuntimeConfig.load_config(cfg_path)
        assert config.get("auth_key") == "test-key"
        assert config.get("port") == 23457

    def test_get_default(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "test-key"})
        config = RuntimeConfig.load_config(cfg_path)
        assert config.get("nonexistent") is None
        assert config.get("nonexistent", "fallback") == "fallback"

    def test_set_returns_new_instance(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "old-key"})
        config = RuntimeConfig.load_config(cfg_path)
        new_config = config.set("auth_key", "new-key")
        # Original should be unchanged
        assert config.get("auth_key") == "old-key"
        # New should have the updated value
        assert new_config.get("auth_key") == "new-key"
        # They should be different objects
        assert config is not new_config

    def test_immutable(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "test-key"})
        config = RuntimeConfig.load_config(cfg_path)
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            # noinspection PyDataclass
            config._data = {}

    def test_contains(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "test-key"})
        config = RuntimeConfig.load_config(cfg_path)
        assert "auth_key" in config
        assert "nonexistent" not in config


class TestRuntimeConfigValidate:
    def test_validate_config(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {
            "auth_key": "test-key",
            "port": 23457,
            "register_concurrency": 1,
        })
        # Should not raise
        config = RuntimeConfig.load_config(cfg_path)
        assert config is not None

    def test_validate_config_fails_on_wrong_type(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {
            "auth_key": "test-key",
            "port": "not-a-port",
            "register_concurrency": "not-a-number",
        })
        with pytest.raises(ConfigValidationError):
            RuntimeConfig.load_config(cfg_path)


class TestRuntimeConfigReload:
    def test_reload(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "first-key"})
        config = RuntimeConfig.load_config(cfg_path)
        assert config.get("auth_key") == "first-key"
        # Update the file
        _write_config(cfg_path, {"auth_key": "second-key"})
        new_config = config.reload()
        assert new_config.get("auth_key") == "second-key"
        assert config is not new_config

    def test_as_dict(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        _write_config(cfg_path, {"auth_key": "test-key", "port": 23457})
        config = RuntimeConfig.load_config(cfg_path)
        d = config.as_dict()
        assert isinstance(d, dict)
        assert d["auth_key"] == "test-key"
        # Mutating the returned dict should not affect the original
        d["auth_key"] = "mutated"
        assert config.get("auth_key") == "test-key"