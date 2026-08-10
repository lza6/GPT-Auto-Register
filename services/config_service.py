"""Runtime configuration service — frozen dataclass wrapping config.json.

Provides an immutable, thread-safe view of the application configuration
with a reload mechanism that reads from config.json and validates via
the config_schema module.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from services.config_schema import validate_config as _validate_schema


def _freeze(value: Any) -> Any:
    """Recursively freeze a dict/list tree into immutable equivalents."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Recursively thaw a frozen tree back to mutable dicts/lists."""
    if isinstance(value, MappingProxyType):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _default_config_path() -> Path:
    """Resolve config.json relative to the project root."""
    return Path(__file__).resolve().parent.parent / "config.json"


class ConfigValidationError(ValueError):
    """Raised when config.json fails schema validation."""
    pass


class ConfigNotFoundError(FileNotFoundError):
    """Raised when config.json does not exist."""
    pass


@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable wrapper around the application configuration dict.

    All nested dicts are frozen via MappingProxyType and lists are frozen
    to tuples, providing a thread-safe read-only view.
    """

    _data: Mapping[str, Any] = field(repr=False)
    _source: Path = field(repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False, hash=False)

    # ── public accessors ──────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key, returning default if missing."""
        return _thaw(self._data.get(key, default))

    def as_dict(self) -> dict[str, Any]:
        """Return a mutable deep-copy of the entire config."""
        return _thaw(self._data)

    @property
    def source(self) -> Path:
        """Path to the config file this was loaded from."""
        return self._source

    # ── immutable update ──────────────────────────────────────────────

    def set(self, key: str, value: Any) -> RuntimeConfig:
        """Return a *new* RuntimeConfig with the given key updated.

        The original RuntimeConfig is not mutated (immutable pattern).
        """
        data = self.as_dict()
        data[key] = value
        return RuntimeConfig(
            _data=_freeze(data),
            _source=self._source,
        )

    # ── factory / loader ──────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str | Path = "<injected>") -> RuntimeConfig:
        """Create a RuntimeConfig from a dict, optionally validating."""
        return cls(
            _data=_freeze(data),
            _source=Path(source),
        )

    @classmethod
    def load_config(cls, path: str | Path | None = None) -> RuntimeConfig:
        """Read config.json from disk and return a validated RuntimeConfig.

        Args:
            path: Path to config.json. Defaults to project-root/config.json.

        Returns:
            A frozen RuntimeConfig instance.

        Raises:
            ConfigNotFoundError: if the file does not exist.
            ConfigValidationError: if schema validation fails.
        """
        source = Path(path).expanduser().resolve() if path else _default_config_path()
        if not source.is_file():
            raise ConfigNotFoundError(f"config file not found: {source}")
        try:
            raw = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ConfigValidationError(f"invalid config file {source}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigValidationError("config root must be a JSON object")

        cls.validate_config(raw)
        return cls(
            _data=_freeze(raw),
            _source=source,
        )

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        """Validate config dict against the schema.

        Raises ConfigValidationError on issues; uses config_schema module.
        """
        issues = _validate_schema(config)
        if issues:
            messages = [str(i) for i in issues]
            raise ConfigValidationError("; ".join(messages))

    # ── thread-safe reload ────────────────────────────────────────────

    def reload(self) -> RuntimeConfig:
        """Re-read config.json from disk and return a new RuntimeConfig.

        Uses the same source path as the current instance.
        Thread-safe via internal lock.
        """
        with self._lock:
            return self.load_config(self._source)

    # ── compatibility ─────────────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        """Dict-style access for backward compatibility."""
        return self.get(key)

    def __contains__(self, key: str) -> bool:
        """Support 'key in config' checks."""
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __repr__(self) -> str:
        return f"RuntimeConfig(source={self._source.name}, keys={list(self._data.keys())})"