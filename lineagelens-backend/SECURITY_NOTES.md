# Security Notes

This file documents security-sensitive design decisions, operational requirements,
and runbooks for the LineageLens backend. It is maintained alongside code changes
— update it whenever a security-relevant decision is made.

---

## Ed25519 Attestation Signing Key (ATTESTATION_SIGNING_KEY)

### Why this matters

The Ed25519 private key loaded from `ATTESTATION_SIGNING_KEY` is the root of trust
for **all** signed artifacts in LineageLens:

- **F1 Indemnity certificates** — signature on the certificate's attestation row
- **F5 License-clean attestations** — signed when a scope passes the license policy
- **F6 Human-review attestations** — signed when a reviewer submits a depth signal
- **Hash-chain integrity** — `prev_hash` in every attestation statement binds it to
  the predecessor, making retrospective forgery detectable

If this key is compromised or rotated incorrectly, **every historical attestation
becomes unverifiable** until the old public key is also retained for validation.

### Production requirements

1. **Never derive from JWT_SECRET_KEY in production.**
   The dev fallback (`hashlib.pbkdf2_hmac` over `JWT_SECRET_KEY`) is deliberately
   rejected by the `Settings` validator when `APP_ENV=production`. Set
   `ATTESTATION_SIGNING_KEY` to a base64-encoded 32-byte seed generated offline:

   ```sh
   python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
   ```

2. **Store in a KMS / HSM, not a plain environment variable.**
   Recommended options (cheapest to most hardened):
   - AWS Secrets Manager with automatic rotation + Lambda hook to update ECS tasks
   - GCP Secret Manager with Secret Accessor IAM binding on the service account
   - HashiCorp Vault with transit secrets engine (sign inside Vault, key never leaves)
   - HSM (FIPS 140-2 Level 3) for regulated environments

3. **Key-ID versioning — required before first rotation.**
   The `public_key_id` column on every `Attestation` row stores the first 16 hex
   chars of `SHA-256(public_key_bytes)`. Before rotating:
   - Deploy code that can verify with **both** the old and new public key by looking
     up `public_key_id` in a registry of known keys.
   - Only decommission the old key once all attestations that reference it have
     expired or been re-attested.
   - The `app.core.attestation.verify_attestation` function currently loads only
     the **current** key. Add multi-key lookup before rotating in production.

4. **Key-compromise runbook.**
   If `ATTESTATION_SIGNING_KEY` is leaked:
   a. Immediately rotate the secret in the KMS and redeploy (all new attestations
      use the new key).
   b. Mark all `Attestation` rows signed with the compromised `public_key_id` as
      untrusted in a new `key_compromised` flag column (migration required).
   c. Notify relying parties (indemnity certificate holders, auditors) that
      attestations signed with the old key ID require manual re-attestation.
   d. Issue new indemnity certificates for any `IndemnityCertificate` rows whose
      linked attestation used the compromised key.
   e. Revoke the compromised key in the KMS and audit who had access.

5. **Do not share the key between environments.**
   Dev / staging / production must each have a distinct key. The test suite derives
   a deterministic key from `JWT_SECRET_KEY` (dev path) — this is intentional and
   safe only because test keys are ephemeral and `APP_ENV=test`.

### Current implementation gaps (do not close in this session)

- `verify_attestation` validates only with the single currently-loaded key. A
  multi-key registry is needed before any rotation can be performed safely.
- There is no `key_id` registry table yet — add one before the first production
  rotation.
- Indemnity certificate validity checks do not verify that the signing key was
  not compromised at the time of issuance — add a `key_valid_at` check once the
  compromise-timestamp tracking is in place.
