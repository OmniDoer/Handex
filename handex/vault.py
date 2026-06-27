from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import settings
from . import db


class VaultError(Exception):
    pass


def vault_enabled() -> bool:
    return bool(settings.vault_key)


def _fernet() -> Fernet:
    if not settings.vault_key:
        raise VaultError("HANDEX_VAULT_KEY is not configured")
    try:
        return Fernet(settings.vault_key.encode("utf-8"))
    except Exception as exc:
        raise VaultError(f"Invalid HANDEX_VAULT_KEY: {type(exc).__name__}") from exc


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise VaultError("Vault secret could not be decrypted with the configured key") from exc


def create_item(label: str, kind: str, username: str, secret: str, metadata: dict[str, Any] | None = None) -> int:
    if not label.strip():
        raise VaultError("Vault item label is required")
    if secret == "":
        raise VaultError("Vault item secret is required")
    return db.create_vault_item(
        {
            "label": label.strip(),
            "kind": kind.strip(),
            "username": username.strip(),
            "secret_encrypted": encrypt_secret(secret),
            "metadata": metadata or {},
        }
    )


def list_items() -> list[dict[str, Any]]:
    return db.list_vault_items()


def get_item(item_id: int) -> dict[str, Any]:
    item = db.get_vault_item(item_id)
    if not item:
        raise VaultError(f"Vault item not found: {item_id}")
    return item


def delete_item(item_id: int) -> None:
    db.delete_vault_item(item_id)


def metadata_for_tools() -> list[dict[str, Any]]:
    result = []
    for item in list_items():
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        result.append(
            {
                "vault": "handex",
                "credential_id": f"handex:{item['id']}",
                "label": item.get("label", ""),
                "kind": item.get("kind", ""),
                "username": item.get("username", ""),
                "host": metadata.get("host", ""),
                "allowed_origins": metadata.get("allowed_origins", []),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
            }
        )
    return result


def decrypt_item_secret(item_id: int) -> tuple[dict[str, Any], str]:
    item = get_item(item_id)
    return item, decrypt_secret(item["secret_encrypted"])
