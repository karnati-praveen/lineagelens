# lineagelens-verifier

Standalone offline verifier for a LineageLens **Evidence Capsule**. This is
the one artifact meant to outlive the company: it has no dependency on the
LineageLens backend, no database, no network call, no license check, and no
shared secret. Everything needed to verify a capsule travels *inside* the
capsule — the signed document, its content-addressed manifest, and a
snapshot of the signing-key registry at the time the capsule was built.

## Install

```sh
pip install .
```

Only third-party dependency: `cryptography` (for Ed25519 signature
verification). Everything else is Python stdlib (`json`, `hashlib`,
`zipfile`, `argparse`).

## Usage

```sh
lineagelens-verify path/to/capsule.zip
lineagelens-verify path/to/capsule.zip --json
```

Exit code is `0` for `valid` / `valid_with_redactions`, non-zero otherwise —
safe to use in a CI gate.

## What it checks

1. **Schema version** — refuses to guess at an unfamiliar `capsuleSchemaVersion`
   (`unsupported_schema`) rather than silently attempting a best-effort parse.
2. **Manifest** — recomputes the SHA-256 of every file in the archive and
   compares it to the manifest; flags extra or missing files.
3. **Signature** — verifies the Ed25519 signature over the canonical
   `capsule.json` content.
4. **Key trust** — checks the signing key's status (valid / expired /
   compromised / retired) *at the capsule's own generation time*, using only
   the key-registry snapshot embedded in the capsule. No live lookup needed.
5. **Hash chain** — recomputes the provenance record hash chain from the
   capsule's `recordChain` section; redacted/deleted records are verified
   against their signed lifecycle event's content commitment instead of
   (impossible) content recomputation, mirroring the backend's own
   `/integrity/verify` semantics.

Possible `status` values: `valid`, `valid_with_redactions`,
`valid_but_incomplete`, `unverifiable`, `tampered`, `unsupported_schema`,
`expired_or_compromised_key`.

## Why the Ed25519/hash-chain logic is duplicated here

`app.core.attestation` and `app.services.integrity_service` on the backend
implement the same primitives. This package intentionally does **not**
import them — any dependency on the backend package tree would defeat the
point of a verifier that has to keep working after the backend is gone. The
backend's test suite includes a round-trip test
(`tests/test_capsule_verifier_roundtrip.py`) that builds a real capsule with
the backend and verifies it with this package, so the two independent
implementations are checked against each other on every test run.

## Scoped follow-ups (not built here)

- **WASM / browser build.** A Pyodide or Rust/Go port that runs a static
  verify page in-browser would add convenience, not a new trust guarantee
  over this CLI — the CLI is the primary, fully-built differentiator.
- **Frozen, reproducible single-binary.** Shipped today as a plain Python
  source package (verifiable via `pip install .` + a checksum of the
  sdist). A reproducible PyInstaller/static build is more CI investment than
  a solo maintainer should take on before it's asked for.
