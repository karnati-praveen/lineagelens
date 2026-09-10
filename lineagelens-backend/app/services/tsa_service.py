import hashlib
import json
import base64
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

def compute_merkle_root(hashes: List[str]) -> Optional[str]:
    """Computes a basic Merkle Root from a list of SHA-256 hashes."""
    if not hashes:
        return None
        
    current_level = hashes[:]
    
    while len(current_level) > 1:
        next_level = []
        for i in range(0, len(current_level), 2):
            left = current_level[i]
            right = current_level[i+1] if (i+1) < len(current_level) else left
            
            combined = left + right
            digest = hashlib.sha256(combined.encode('utf-8')).hexdigest()
            next_level.append(digest)
            
        current_level = next_level
        
    return current_level[0]

async def request_rfc3161_timestamp(merkle_root: str) -> str:
    """
    Request a real RFC 3161 timestamp. There is no mock: without a configured
    TSA the caller must fail closed instead of persisting fake assurance.
    Configure TSA_URL and implement the ASN.1 TimeStampReq POST there.
    """
    logger.error("TSA notarization requested for root %s with no TSA configured", merkle_root)
    raise RuntimeError("No RFC 3161 TSA configured — refusing to fabricate a timestamp.")
