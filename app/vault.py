"""Encrypted storage for sensitive personal details (name, date of birth, address).
Values are encrypted with a key from KIKU_VAULT_KEY before they reach Neo4j. They are never added to a model prompt or sent to the
memory service. ID numbers (My Number, card, passport) are never stored at all."""
import os

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc1:"
LABELS = {"full_name": "name", "birth_date": "date of birth", "address": "address"}


def _fernet():
    key = os.environ.get("KIKU_VAULT_KEY", "").strip()
    try:
        return Fernet(key.encode()) if key else None
    except (ValueError, TypeError):
        return None


def available() -> bool:
    return _fernet() is not None


def encrypt(value: str) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError("encrypted storage is not set up (KIKU_VAULT_KEY)")
    return PREFIX + f.encrypt(value.encode("utf-8")).decode()


def decrypt(token: str) -> str:
    f = _fernet()
    if f is None or not token.startswith(PREFIX):
        raise ValueError("cannot decrypt")
    try:
        return f.decrypt(token[len(PREFIX):].encode()).decode("utf-8")
    except InvalidToken:
        raise ValueError("wrong key or damaged value")


def mask(field: str, value: str) -> str:
    """What is safe to show back in a chat to let the user check it."""
    if field == "birth_date":
        return value[:4] + "-**-**"
    return value[:2] + "***"
