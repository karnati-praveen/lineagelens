from __future__ import annotations

"""External witness / split-trust anchoring (PART 5 #53).

A self-hosted hash chain is only "tamper-evident", never "immutable" — a
privileged DBA who can rewrite rows can also recompute the chain (PART 1 #8).
Periodically publishing the chain's current root hash to an external,
customer-selected domain closes that gap: a DBA would also have to rewrite
that external record, which they typically don't control.

Four backends, in increasing order of external dependency:
  - RFC3161TSABackend      — real, credential-free (a public timestamp
                             authority over plain HTTP).
  - GitTagWitnessBackend   — real, credential-free (uses the `git` binary
                             already required elsewhere in this stack).
  - SigstoreRekorBackend   — config-gated; a minimal, generic integration
                             (not a full sigstore-python client) — reports
                             `not_configured` rather than pretending to run
                             when REKOR_URL is unset.
  - CustomerObjectStoreBackend — config-gated; generic HTTP PUT to a
                             customer-supplied URL (e.g. an S3 presigned URL
                             with retention lock already applied server-side).

No blockchain, per the doc's own guidance. A second-organization witness
(the doc's "optional second org") is inherently customer-arranged, not
buildable in advance — documented as a follow-up, not implemented.
"""

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib import error as url_error
from urllib import request as url_request

logger = logging.getLogger(__name__)

STATUS_WITNESSED = "witnessed"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class WitnessReceipt:
    backend: str
    status: str
    external_ref: str | None = None
    timestamp: str = ""
    details: str | None = None

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "status": self.status,
            "externalRef": self.external_ref,
            "timestamp": self.timestamp,
            "details": self.details,
        }


class WitnessBackend(Protocol):
    name: str

    def is_configured(self, settings) -> bool: ...

    async def publish(self, root_hash: str, *, settings) -> WitnessReceipt: ...


def compute_periodic_root(record_hashes: list[str]) -> str:
    """Simple binary Merkle root over a list of chain-head hashes (stdlib only).

    Order matters and must be stable across calls for the same input set —
    callers pass already-sorted hashes.
    """
    if not record_hashes:
        return hashlib.sha256(b"").hexdigest()

    level = [bytes.fromhex(h) for h in record_hashes]
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            next_level.append(hashlib.sha256(left + right).digest())
        level = next_level
    return level[0].hex()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# ── Backend 1: RFC 3161 Time-Stamp Authority (real, credential-free) ────────
#
# Hand-rolled minimal DER/ASN.1 encoding for a TimeStampReq — small enough to
# implement correctly without a third-party ASN.1 dependency. We do not parse
# the TimeStampResp's DER structure; a 200 response with a non-empty body is
# treated as a successful timestamp token, stored as the external_ref
# (base64) for later independent verification against the TSA's certificate.

def _der_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    encoded = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def _der_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _der_length(len(value)) + value


def _der_integer(value: int) -> bytes:
    body = value.to_bytes((value.bit_length() + 8) // 8 or 1, "big")
    return _der_tlv(0x02, body)


def _der_oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    first = parts[0] * 40 + parts[1]
    encoded = bytearray([first])
    for p in parts[2:]:
        if p == 0:
            encoded.append(0)
            continue
        chunk = []
        while p > 0:
            chunk.insert(0, p & 0x7F)
            p >>= 7
        for i in range(len(chunk) - 1):
            chunk[i] |= 0x80
        encoded.extend(chunk)
    return _der_tlv(0x06, bytes(encoded))


def _der_octet_string(data: bytes) -> bytes:
    return _der_tlv(0x04, data)


def _der_sequence(*parts: bytes) -> bytes:
    return _der_tlv(0x30, b"".join(parts))


def _der_boolean(value: bool) -> bytes:
    return _der_tlv(0x01, b"\xff" if value else b"\x00")


_SHA256_OID = "2.16.840.1.101.3.4.2.1"


def build_timestamp_request(root_hash_hex: str) -> bytes:
    """Build a minimal RFC 3161 TimeStampReq DER blob for a SHA-256 digest."""
    message_imprint = _der_sequence(
        _der_sequence(_der_oid(_SHA256_OID)),
        _der_octet_string(bytes.fromhex(root_hash_hex)),
    )
    # version=1, messageImprint, certReq=true (ask the TSA to include its cert)
    return _der_sequence(
        _der_integer(1),
        message_imprint,
        _der_boolean(True),
    )


class RFC3161TSABackend:
    name = "rfc3161_tsa"

    def is_configured(self, settings) -> bool:
        return bool((getattr(settings, "tsa_url", None) or "").strip())

    async def publish(self, root_hash: str, *, settings) -> WitnessReceipt:
        import asyncio

        tsa_url = (getattr(settings, "tsa_url", None) or "").strip()
        if not tsa_url:
            return WitnessReceipt(self.name, STATUS_NOT_CONFIGURED, timestamp=_now_iso())

        return await asyncio.to_thread(self._publish_sync, root_hash, tsa_url)

    def _publish_sync(self, root_hash: str, tsa_url: str) -> WitnessReceipt:
        try:
            body = build_timestamp_request(root_hash)
            req = url_request.Request(
                tsa_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/timestamp-query"},
            )
            with url_request.urlopen(req, timeout=10) as resp:
                token = resp.read()
            if not token:
                return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details="empty TSA response")
            import base64

            return WitnessReceipt(
                self.name, STATUS_WITNESSED, external_ref=base64.b64encode(token).decode(), timestamp=_now_iso()
            )
        except (url_error.URLError, TimeoutError, OSError) as exc:
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc))
        except Exception as exc:  # never let a witness failure propagate
            logger.exception("RFC3161TSABackend.publish failed")
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc))


# ── Backend 2: Git tag (real, credential-free — git already required) ──────

class GitTagWitnessBackend:
    name = "git_tag"

    def is_configured(self, settings) -> bool:
        return bool((getattr(settings, "witness_git_repo_path", None) or "").strip())

    async def publish(self, root_hash: str, *, settings) -> WitnessReceipt:
        import asyncio

        repo_path = (getattr(settings, "witness_git_repo_path", None) or "").strip()
        if not repo_path:
            return WitnessReceipt(self.name, STATUS_NOT_CONFIGURED, timestamp=_now_iso())

        return await asyncio.to_thread(self._publish_sync, root_hash, repo_path)

    def _publish_sync(self, root_hash: str, repo_path: str) -> WitnessReceipt:
        tag_name = f"lineagelens-witness-{root_hash[:16]}-{int(datetime.now(tz=UTC).timestamp())}"
        try:
            subprocess.run(
                ["git", "tag", "-a", tag_name, "-m", f"lineagelens-witness-root:{root_hash}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            return WitnessReceipt(self.name, STATUS_WITNESSED, external_ref=tag_name, timestamp=_now_iso())
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            details = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(details))


# ── Backend 3: Sigstore/private Rekor (config-gated, generic minimal client) ─

class SigstoreRekorBackend:
    name = "sigstore_rekor"

    def is_configured(self, settings) -> bool:
        return bool((getattr(settings, "rekor_url", None) or "").strip())

    async def publish(self, root_hash: str, *, settings) -> WitnessReceipt:
        import asyncio

        rekor_url = (getattr(settings, "rekor_url", None) or "").strip()
        if not rekor_url:
            return WitnessReceipt(self.name, STATUS_NOT_CONFIGURED, timestamp=_now_iso())
        return await asyncio.to_thread(self._publish_sync, root_hash, rekor_url)

    def _publish_sync(self, root_hash: str, rekor_url: str) -> WitnessReceipt:
        # Generic minimal integration: posts the root hash as a note to the
        # configured endpoint. This is NOT a spec-compliant hashedrekord/DSSE
        # Rekor entry (that requires an accompanying signature over the entry
        # in Rekor's exact envelope format) — a real integration is a scoped
        # follow-up once a design partner needs Rekor specifically. Any
        # non-2xx or network error is reported as `failed`, never faked.
        try:
            body = json.dumps({"rootHash": root_hash, "algorithm": "sha256"}).encode()
            req = url_request.Request(
                rekor_url.rstrip("/") + "/api/v1/log/entries",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with url_request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 201):
                    return WitnessReceipt(
                        self.name, STATUS_FAILED, timestamp=_now_iso(), details=f"HTTP {resp.status}"
                    )
                response_body = resp.read().decode(errors="replace")
            return WitnessReceipt(
                self.name, STATUS_WITNESSED, external_ref=response_body[:256], timestamp=_now_iso()
            )
        except (url_error.URLError, TimeoutError, OSError) as exc:
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc))
        except Exception as exc:
            logger.exception("SigstoreRekorBackend.publish failed")
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc))


# ── Backend 4: Customer object store (config-gated, generic PUT) ───────────

class CustomerObjectStoreBackend:
    name = "customer_object_store"

    def is_configured(self, settings) -> bool:
        return bool((getattr(settings, "witness_object_store_url", None) or "").strip())

    async def publish(self, root_hash: str, *, settings) -> WitnessReceipt:
        import asyncio

        url = (getattr(settings, "witness_object_store_url", None) or "").strip()
        if not url:
            return WitnessReceipt(self.name, STATUS_NOT_CONFIGURED, timestamp=_now_iso())
        return await asyncio.to_thread(self._publish_sync, root_hash, url)

    def _publish_sync(self, root_hash: str, url: str) -> WitnessReceipt:
        # Generic S3-compatible PUT (e.g. a customer-supplied presigned URL
        # with retention-lock already configured server-side). Retention-lock
        # support varies by provider and is the customer's responsibility.
        try:
            body = json.dumps(
                {"rootHash": root_hash, "algorithm": "sha256", "timestamp": _now_iso()}
            ).encode()
            req = url_request.Request(url, data=body, method="PUT", headers={"Content-Type": "application/json"})
            with url_request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 201, 204):
                    return WitnessReceipt(
                        self.name, STATUS_FAILED, timestamp=_now_iso(), details=f"HTTP {resp.status}"
                    )
            return WitnessReceipt(self.name, STATUS_WITNESSED, external_ref=url, timestamp=_now_iso())
        except (url_error.URLError, TimeoutError, OSError) as exc:
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc))
        except Exception as exc:
            logger.exception("CustomerObjectStoreBackend.publish failed")
            return WitnessReceipt(self.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc))


_BACKENDS: list[WitnessBackend] = [
    RFC3161TSABackend(),
    GitTagWitnessBackend(),
    SigstoreRekorBackend(),
    CustomerObjectStoreBackend(),
]


async def witness_root(root_hash: str, *, settings) -> list[WitnessReceipt]:
    """Publish *root_hash* to every backend, collecting all receipts.

    Never short-circuits on one backend failing or being unconfigured — every
    backend's receipt (including `not_configured` ones) is returned so a
    caller can see exactly which anchors are and aren't active. No silent
    omission (PART 5 #58).
    """
    receipts: list[WitnessReceipt] = []
    for backend in _BACKENDS:
        try:
            receipts.append(await backend.publish(root_hash, settings=settings))
        except Exception as exc:  # a backend must never crash the whole round
            logger.exception("Witness backend %s raised unexpectedly", backend.name)
            receipts.append(WitnessReceipt(backend.name, STATUS_FAILED, timestamp=_now_iso(), details=str(exc)))
    return receipts
