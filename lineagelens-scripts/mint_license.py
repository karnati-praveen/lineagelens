#!/usr/bin/env python3
"""Vendor-side LineageLens license tool. NOT shipped to customers.

This script holds nothing secret itself — the secret is the private key, which you keep
in a password manager / Polar secret and pass via the LICENSE_SIGNING_KEY env var (or
--key). It reuses the verification logic in app/core/license.py so minting and the
backend can never drift apart.

Usage
-----
  # 1. One-time: create a vendor keypair. Store the PRIVATE key as a secret;
  #    paste the PUBLIC key into LICENSE_PUBLIC_KEY_HEX in app/core/license.py.
  python lineagelens-scripts/mint_license.py keygen

  # 2. Per sale: mint a key (private key from env LICENSE_SIGNING_KEY or --key).
  #    Defaults to a 30-day license; override with --days N, --days 0 (perpetual),
  #    or an explicit --exp YYYY-MM-DD.
  export LICENSE_SIGNING_KEY=<base64-private-seed>
  python lineagelens-scripts/mint_license.py mint --plan max --seats 25 \
      --customer "Acme Corp"                 # 30-day key (default)
  python lineagelens-scripts/mint_license.py mint --plan max --days 365   # 1-year key

  # 3. Sanity-check a key against the embedded/overridden public key.
  python lineagelens-scripts/mint_license.py verify --key <license> \
      --public-key <hex>
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Default validity for a freshly minted license, in days. Override per-mint with
# --days N (or --days 0 for a perpetual license), or change this number to move the
# default for every future `mint` call.
DEFAULT_LICENSE_DAYS = 30

# Make the backend package importable when run from the repo root.
_BACKEND = Path(__file__).resolve().parent.parent / "lineagelens-backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.license import (  # noqa: E402
    issue_license,
    public_key_hex_for,
    verify_license,
)


def _resolve_private_key(arg_key: str | None) -> str:
    key = (arg_key or os.environ.get("LICENSE_SIGNING_KEY") or "").strip()
    if not key:
        sys.exit(
            "No private key. Set LICENSE_SIGNING_KEY=<base64-seed> or pass --key. "
            "Generate one with: mint_license.py keygen"
        )
    return key


def cmd_keygen(_: argparse.Namespace) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PrivateFormat,
        PublicFormat,
        NoEncryption,
    )

    seed = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    print("# LineageLens license keypair — generated", datetime.now(tz=UTC).isoformat())
    print()
    print("PRIVATE KEY (base64 seed) — STORE AS A SECRET, NEVER COMMIT:")
    print(base64.b64encode(seed).decode())
    print()
    print("PUBLIC KEY (hex) — paste into LICENSE_PUBLIC_KEY_HEX in app/core/license.py:")
    print(pub.hex())


def cmd_mint(args: argparse.Namespace) -> None:
    private_key = _resolve_private_key(args.key)

    expires: str | None = None
    if args.exp:
        expires = args.exp
    elif args.days:
        expires = (datetime.now(tz=UTC).date() + timedelta(days=args.days)).isoformat()

    key = issue_license(
        plan=args.plan,
        seats=args.seats,
        customer=args.customer,
        expires=expires,
        private_key_b64=private_key,
    )

    # Echo the verified contents so the operator can confirm before sending.
    ent = verify_license(key, public_key_hex=public_key_hex_for(private_key))
    print("# Minted license — verified OK:", ent.licensed)
    print(f"# plan={ent.plan} seats={ent.seats or 'unlimited'} "
          f"customer={ent.customer or 'n/a'} expires={ent.expires or 'perpetual'}")
    print()
    print(key)


def cmd_verify(args: argparse.Namespace) -> None:
    ent = verify_license(args.key, public_key_hex=args.public_key)
    print(f"licensed = {ent.licensed}")
    print(f"plan     = {ent.plan}")
    print(f"seats    = {ent.seats or 'unlimited'}")
    print(f"customer = {ent.customer or 'n/a'}")
    print(f"expires  = {ent.expires or 'perpetual'}")
    if not ent.licensed:
        print(f"reason   = {ent.reason}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="LineageLens vendor license tool.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("keygen", help="Generate a new vendor Ed25519 keypair.").set_defaults(
        func=cmd_keygen
    )

    mint = sub.add_parser("mint", help="Mint a signed license key.")
    mint.add_argument("--plan", required=True, choices=["lite", "plus", "max"])
    mint.add_argument("--seats", type=int, default=0, help="0 = unlimited (default)")
    mint.add_argument("--customer", default="", help="Customer display name / id")
    mint.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LICENSE_DAYS,
        help=f"Validity in days from today (default: {DEFAULT_LICENSE_DAYS}; use 0 for perpetual)",
    )
    mint.add_argument("--exp", default="", help="Explicit expiry date YYYY-MM-DD (overrides --days)")
    mint.add_argument("--key", default="", help="Private key seed (else LICENSE_SIGNING_KEY)")
    mint.set_defaults(func=cmd_mint)

    verify = sub.add_parser("verify", help="Verify a license key against a public key.")
    verify.add_argument("--key", required=True, help="The license key string")
    verify.add_argument("--public-key", default=None, help="Vendor public key hex (else embedded)")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
