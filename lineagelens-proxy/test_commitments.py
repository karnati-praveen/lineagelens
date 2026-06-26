"""Tests for proxy client-side commitments (PART 2 #17).

The proxy commits to the inserted code + prompt digests before forwarding to the
backend, so the backend's evidence layer can detect silent disagreement. The
prompt digest construction must match the backend's
integrity_service.compute_prompt_sha256.

Run with:
    cd lineagelens-proxy && pytest test_commitments.py -v
"""
import hashlib
import json
import sys

sys.path.insert(0, ".")
from ingest import _build_commitments, _prompt_commitment, _sha256_text  # noqa: E402


def test_sha256_text_matches_hashlib():
    assert _sha256_text("def f(): pass") == hashlib.sha256(b"def f(): pass").hexdigest()
    assert _sha256_text(None) is None


def test_prompt_commitment_matches_backend_canonical_form():
    messages = [{"role": "user", "content": "add rate limiting"}]
    expected = hashlib.sha256(
        json.dumps(messages, sort_keys=True, default=str).encode()
    ).hexdigest()
    assert _prompt_commitment(messages) == expected
    assert _prompt_commitment(None) is None


def test_build_commitments_shape():
    c = _build_commitments(inserted_text="x = 1", prompt_messages=[{"role": "user", "content": "hi"}])
    assert c["algorithm"] == "sha256"
    assert c["committedBy"] == "lineagelens-proxy"
    assert c["insertedTextSha256"] == hashlib.sha256(b"x = 1").hexdigest()
    assert c["promptSha256"] is not None
    assert c["version"] == "1"
