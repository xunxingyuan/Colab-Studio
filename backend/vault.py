"""OS Keyring credential vault & secret redactor."""

from __future__ import annotations

import logging
import re
from typing import Optional

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)
SERVICE_NAME = "colab-studio-vault"

_TOKEN_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{16,}"),
    re.compile(r"ya29\.[A-Za-z0-9\-_]{20,}"),
    re.compile(r"1//[A-Za-z0-9\-_]{20,}"),
]


class SecretVault:
    def __init__(self, *, use_memory_only: bool = False) -> None:
        self._memory_only = use_memory_only
        self._mem: dict[str, str] = {}

    def _get(self, key: str) -> Optional[str]:
        if self._memory_only:
            return self._mem.get(key)
        try:
            return keyring.get_password(SERVICE_NAME, key)
        except KeyringError as exc:
            logger.warning("Keyring read failed (%s); falling back to memory store.", exc)
            return self._mem.get(key)

    def _set(self, key: str, value: str) -> None:
        self._mem[key] = value
        if self._memory_only:
            return
        try:
            keyring.set_password(SERVICE_NAME, key, value)
        except KeyringError as exc:
            logger.warning("Keyring write failed (%s); stored in memory only.", exc)

    def _delete(self, key: str) -> None:
        self._mem.pop(key, None)
        if self._memory_only:
            return
        try:
            keyring.delete_password(SERVICE_NAME, key)
        except Exception:
            pass

    def get_account_token(self, account_id: str) -> Optional[str]:
        return self._get(f"oauth:{account_id}")

    def set_account_token(self, account_id: str, token_json: str) -> None:
        self._set(f"oauth:{account_id}", token_json)

    def delete_account_token(self, account_id: str) -> None:
        self._delete(f"oauth:{account_id}")

    def get_secret(self, name: str) -> Optional[str]:
        return self._get(f"secret:{name}")

    def set_secret(self, name: str, value: str) -> None:
        self._set(f"secret:{name}", value)

    def delete_secret(self, name: str) -> None:
        self._delete(f"secret:{name}")

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for val in list(self._mem.values()):
            if val and len(val) >= 8 and val in out:
                out = out.replace(val, "***")
        for pat in _TOKEN_PATTERNS:
            out = pat.sub("***", out)
        return out
