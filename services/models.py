"""Account data model — frozen dataclass for typed account persistence.

Maps to the `accounts` table columns and provides a safe serialization
boundary that redacts secrets via the sanitizer module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.sanitizer import sanitize


def _text(value: Any) -> str:
    """Coerce a value to string, with None -> ''."""
    return "" if value is None else str(value)


@dataclass(frozen=True)
class AccountData:
    """Immutable account record matching the `accounts` table schema.

    Sensitive fields (password, tokens) are excluded from repr to prevent
    accidental leakage in logs or error messages.
    """

    email: str = ""
    password: str = field(default="", repr=False)
    client_id: str = ""
    refresh_token: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)
    openai_refresh_token: str = field(default="", repr=False)
    id_token: str = field(default="", repr=False)
    openai_password: str = field(default="", repr=False)
    name: str = ""
    birthdate: str = ""
    proxy: str = ""
    status: str = ""
    error: str = ""
    failure_type: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> AccountData:
        """Create an AccountData from a dict, coercing None to ''."""
        return cls(
            email=_text(data.get("email")),
            password=_text(data.get("password")),
            client_id=_text(data.get("client_id")),
            refresh_token=_text(data.get("refresh_token")),
            access_token=_text(data.get("access_token")),
            openai_refresh_token=_text(data.get("openai_refresh_token")),
            id_token=_text(data.get("id_token")),
            openai_password=_text(data.get("openai_password")),
            name=_text(data.get("name")),
            birthdate=_text(data.get("birthdate")),
            proxy=_text(data.get("proxy")),
            status=_text(data.get("status")),
            error=_text(data.get("error")),
            failure_type=_text(data.get("failure_type")),
        )

    def safe_snapshot(self) -> dict[str, Any]:
        """Return a sanitized dict with secrets redacted by the sanitizer.

        All fields are included; the sanitizer module handles redaction of
        sensitive keys (password, tokens, etc.) according to the active
        sensitive_policy.json.
        """
        return sanitize({
            "email": self.email,
            "password": self.password,
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "openai_refresh_token": self.openai_refresh_token,
            "id_token": self.id_token,
            "openai_password": self.openai_password,
            "name": self.name,
            "birthdate": self.birthdate,
            "proxy": self.proxy,
            "status": self.status,
            "error": self.error,
            "failure_type": self.failure_type,
        })