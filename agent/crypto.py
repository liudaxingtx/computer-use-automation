"""Encryption at rest for customer data (design principle #6).

Step input values that a replay must reproduce are AES-256-GCM encrypted with a
per-tenant key before they are written into an artifact; replay decrypts only at
the moment of use. The master key lives in the environment (or a KMS) and is
never committed or stored alongside the artifact. The locator strategy stays
plaintext — it is UI structure, not customer data.
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Encrypted values are tagged with this prefix so replay knows to decrypt.
ENC_PREFIX = "enc:"


def master_key_from_env() -> bytes:
    """Read the master key from ARTIFACT_ENC_KEY (base64, 32 bytes).

    Falls back to deriving from a passphrase so the demo is self-contained; in
    production this MUST come from a real KMS / secret store.
    """
    b64 = os.getenv("ARTIFACT_ENC_KEY", "")
    if b64:
        key = base64.b64decode(b64)
        if len(key) == 32:
            return key
    phrase = os.getenv("ARTIFACT_ENC_PASSPHRASE", "dev-insecure-passphrase")
    return hashlib.sha256(phrase.encode()).digest()


def derive_key(tenant_id: str, master_key: bytes) -> bytes:
    """Derive a per-tenant key from the master key (SHA-256 of key + tenant)."""
    return hashlib.sha256(master_key + tenant_id.encode()).digest()


def get_key(tenant_id: str = "default") -> bytes:
    return derive_key(tenant_id, master_key_from_env())


def encrypt(plaintext: str, key: bytes) -> str:
    """AES-256-GCM encrypt; returns 'enc:<base64(nonce + ciphertext + tag)>'."""
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return ENC_PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt(token: str, key: bytes) -> str:
    """Decrypt a token produced by `encrypt`. Raises on wrong key / tampering."""
    if not token.startswith(ENC_PREFIX):
        raise ValueError("not an encrypted value")
    raw = base64.b64decode(token[len(ENC_PREFIX):])
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)
