"""
AES-256-GCM Field-Level Encryption
Member 3 — Execution Engine & Security Lead

Encrypts PII vault values at rest. This is a SEPARATE control from
pii_masker.py's tokenization:

  - Tokenization hides PII from the LLM / orchestrator / logs — they only
    ever see an opaque token like TOKEN_NIC_a1b2c3d4.
  - Encryption protects the vault ITSELF — so even if the in-memory vault
    were dumped, swapped to disk, or leaked in a crash report, the NIC /
    phone / passport values inside it are not readable without the key.

Uses AES-256-GCM (authenticated encryption): confidentiality + integrity
in one primitive, no separate MAC needed. This is the standard modern
choice over CBC mode (which has no built-in integrity check).
"""

from __future__ import annotations

import base64 # Converts encrypted binary data into a storable text format.
import os # Used to generate a random nonce.

# Imports AES-GCM encryption from the cryptography library.
try:
  from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore[reportMissingImports]
except ImportError as exc:
  raise ImportError(
    "The 'cryptography' package is required. Install it with "
    "'pip install cryptography'."
  ) from exc


class FieldEncryptor:
    """AES-256-GCM encryption/decryption for individual PII field values."""

    NONCE_SIZE = 12  # 96 bits — the standard, recommended nonce size for GCM

    def __init__(self, key: bytes | None = None) -> None:
        """
        key: 32 raw bytes (AES-256). If omitted, a fresh random key is
        generated for this process/session. In production this key would
        come from an env var or a secrets manager (e.g. AWS KMS, Vault) —
        never hardcoded and never logged.
        """
        self.key = key if key is not None else AESGCM.generate_key(bit_length=256)
        self._aesgcm = AESGCM(self.key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypts a plaintext string, returns a base64 string safe to store."""
        nonce = os.urandom(self.NONCE_SIZE) # Generates a random nonce for the encryption.
        # Converts the original text into bytes.
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # Store nonce + ciphertext (GCM tag is appended to ciphertext automatically)
        blob = nonce + ciphertext
        return base64.b64encode(blob).decode("utf-8") # Converts the encrypted binary data into Base64 text for storage.

    def decrypt(self, encoded: str) -> str:
        """Reverses encrypt(). Raises InvalidTag if the blob was tampered with."""
        blob = base64.b64decode(encoded) # Converts the Base64 text back into binary data.
        nonce, ciphertext = blob[: self.NONCE_SIZE], blob[self.NONCE_SIZE :]
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None) # Decrypts the data and checks that it was not modified.
        return plaintext.decode("utf-8")# Converts the decrypted bytes back into normal text.