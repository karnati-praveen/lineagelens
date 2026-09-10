import base64
import os
import json
from typing import Any, Dict, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def generate_key() -> str:
    """Generates a new AES-256-GCM key and returns it as a base64 string."""
    key = AESGCM.generate_key(bit_length=256)
    return base64.b64encode(key).decode("utf-8")

def encrypt_payload(key_b64: str, payload: Any) -> str:
    """Encrypts a python object (converted to JSON) using the provided base64 key."""
    if payload is None:
        return None
    key = base64.b64decode(key_b64)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    
    plaintext = json.dumps(payload).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    
    # Prepend nonce to ciphertext for storage
    encrypted_data = nonce + ciphertext
    return base64.b64encode(encrypted_data).decode("utf-8")

def decrypt_payload(key_b64: str, encrypted_b64: str) -> Any:
    """Decrypts a base64 encoded AES-256-GCM encrypted payload back to a python object."""
    if not encrypted_b64:
        return None
    key = base64.b64decode(key_b64)
    aesgcm = AESGCM(key)
    
    encrypted_data = base64.b64decode(encrypted_b64)
    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]
    
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))
