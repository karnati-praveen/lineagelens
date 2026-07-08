# LineageLens Vendor-Failure & Perpetual-Unlock Covenant

This covenant exists so that nothing you depend on disappears if LineageLens (the
vendor) does. It is intentionally easy to honor because LineageLens is MIT-licensed
and runs entirely self-hosted with offline license verification.

## 1. Your evidence is verifiable forever

- The hash chain, signed attestations, AI-BOM (dual-signed: HMAC **and** offline
  Ed25519), and exported records can be verified with only the public keys that
  ship inside each export — no LineageLens server, network, or account required.
- No feature charges, throttles, or phones home to *verify* existing evidence.

## 2. Purchased self-hosted features are perpetual

- Paid licenses may be minted as **perpetual** (`perpetual: true`). A perpetual
  license keeps its plan unlocked **forever**, even after `expires` passes.
- For a perpetual license, `expires` marks only the end of **updates and support**
  (the subscription), not the end of the features you paid for. The backend reports
  this as `subscription_lapsed = true` while keeping `licensed = true`.
  (Implemented in `app/core/license.py`; see `verify_license`.)
- Non-perpetual (pure subscription) licenses still degrade to the free Lite tier on
  expiry, by design.

## 3. If the vendor shuts down

On wind-down, LineageLens commits to:
- Publishing a final source + release kit (signed, checksummed) to independent
  mirrors.
- Releasing a perpetual-unlock license (or the vendor public key + a minting note)
  so existing customers retain their purchased plan.
- Transferring the schema, the standalone verifier, and the key registry to a
  neutral steward.
- Charging **no** fee for export or verification, ever.

### What "final release kit" now actually means

As of the Part 5 evidence/continuity build-out, the artifacts a wind-down
would publish are no longer aspirational — they exist and are exercised by
the continuity drill (`POST /continuity-drills`, PART 5 #55):

- **Key registry export** (`lineagelens-release-keys-<ver>.json`, PART 5
  #57/#59) — the exact key-history handoff a neutral steward would receive.
- **Evidence Capsule format** (PART 5 #51) — the versioned, signed bundle
  format every customer's evidence already exports into; a steward inherits
  a known schema, not a bespoke one invented at wind-down time.
- **Standalone offline verifier** (`lineagelens-verifier/`, PART 5 #52) —
  ships in every release and inside every capsule; it has no dependency on
  LineageLens infrastructure, so it keeps working after every other service
  (and the company) is gone.
- **SBOMs + SLSA provenance** (PART 5 #59) — published with every release,
  so a steward (or a customer's own auditor) can verify exactly what was
  shipped.

This does not automate the shutdown *trigger* itself — deciding to wind down,
and executing the legal/operational handoff to mirrors and a steward, stays a
manual process. What changes is that the artifacts that process would publish
already exist and are tested, not something to invent under pressure.

## 4. License keys are offline and customer-verifiable

- A license is an Ed25519-signed statement verified locally against the vendor
  **public** key embedded in the backend (`LICENSE_PUBLIC_KEY_HEX`, overridable via
  `LINEAGELENS_LICENSE_PUBLIC_KEY`).
- The vendor **private** key never ships. Until a real vendor keypair is provisioned
  the public key is a placeholder (`"0"*64`) and **all** licenses are rejected — the
  backend runs free/Lite. This is the safe default: "not configured" never silently
  grants a paid plan.
- Provision a real keypair with `python lineagelens-scripts/mint_license.py keygen`,
  keep the private seed offline, and set the public key hex in the backend.

> This document is the published covenant referenced by Part 3 #20 / Part 5 #60 of
> the improvement plan. It is a statement of intent; it is credible precisely because
> the MIT license and offline verification make it enforceable without trusting the
> vendor to stay alive.
