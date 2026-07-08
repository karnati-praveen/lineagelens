from __future__ import annotations

import argparse
import json
import sys

from .verify import VERIFIER_VERSION, STATUS_VALID, STATUS_VALID_WITH_REDACTIONS, verify_capsule

_OK_STATUSES = frozenset({STATUS_VALID, STATUS_VALID_WITH_REDACTIONS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lineagelens-verify",
        description=(
            "Verify a LineageLens Evidence Capsule offline — no backend, network, "
            "database, or license required."
        ),
    )
    parser.add_argument("capsule", help="Path to the capsule .zip file")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument("--version", action="version", version=f"lineagelens-verify {VERIFIER_VERSION}")
    args = parser.parse_args(argv)

    result = verify_capsule(args.capsule)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"status: {result.status}")
        print(f"manifest ok: {result.manifest_ok}")
        print(f"signature ok: {result.signature_ok}")
        print(f"chain ok: {result.chain_ok}")
        print(f"key trust ok: {result.key_trust_ok}")
        if result.details:
            print("details:")
            for line in result.details:
                print(f"  - {line}")

    return 0 if result.status in _OK_STATUSES else 1


if __name__ == "__main__":
    sys.exit(main())
