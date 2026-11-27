import hashlib
import hmac
import json
import logging
import os
from typing import Dict, List, Optional
from aiohttp import web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NebulaSession Consensus v2.1.1")

GENESIS_HASH = "fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2"
# 2026-11-27: hardcoded placeholder secret removed. Read NEBULA_NODE_SECRET; no default.
_secret = os.environ.get("NEBULA_NODE_SECRET")
if not _secret:
    raise RuntimeError(
        "NEBULA_NODE_SECRET is not set. Set this environment variable to the node secret; there is no default."
    )
NODE_SECRET = _secret.encode()

# Re-use your v2.1 decoder
def v21_checksum(text: str) -> int:
    return sum(ord(c) for c in text) % 10_000

def v21_decode(tones: List[int]):
    # Paste your verified v2.1 decode() here — must validate:
    # SYNC, VERSION, LENGTH, START_BELL, payload len, CHECK_BELL, 4-digit checksum, END_BELL, canonical strip
    # Raise ValueError on any failure
    pass

class NebulaSessionConsensus:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.genesis_hash = GENESIS_HASH
        self.verified_tokens: Dict[str, str] = {}  # clip_id -> token
        self.verified_streams: List[Dict] = []
        self.state_lattice: Dict[str, str] = {}
        self.state_versions: Dict[str, int] = {}

    def register_acoustic_stream(self, clip_id: str, title: str, checksum: int, tone_array: List[int]) -> Optional[str]:
        # 1. Full BELLNET validation, not just number check
        try:
            frame = v21_decode(tone_array)
        except ValueError as e:
            logger.error(f"BELLNET decode failed for {clip_id}: {e}")
            return None
        
        if frame.checksum != checksum or checksum != 699:
            logger.error(f"Checksum mismatch for {clip_id}: frame {frame.checksum} vs supplied {checksum}")
            return None
        
        if frame.payload != "JJVALLEA99":
            logger.error(f"Unexpected payload {frame.payload} for keeper anchor")
            return None

        # 2. HMAC bound to genesis, not truncated public hash
        msg = f"{self.genesis_hash}:{clip_id}:{frame.payload}:{frame.checksum}:{tone_array}".encode()
        full_sig = hmac.new(NODE_SECRET, msg, hashlib.sha256).hexdigest()
        session_token = f"nebula_v2.1_{full_sig}"  # full 64 hex chars, no truncation

        self.verified_tokens[clip_id] = session_token
        self.verified_streams.append({
            "clip_id": clip_id,
            "title": title,
            "checksum": checksum,
            "payload": frame.payload,
            "session_token": session_token,
            "anchored_genesis": self.genesis_hash
        })
        logger.info(f"Bound acoustic stream '{title}' [{clip_id}]")
        return session_token

    async def handle_state_sync(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            incoming_token = data.get("session_token")
            
            # 1. Check against set of valid tokens, not single active token
            if incoming_token not in self.verified_tokens.values():
                return web.json_response({"status": "rejected", "reason": "Invalid or unverified session token."}, status=403)
            
            node_state = data.get("state_mutation", {})
            state_key = node_state.get("key")
            state_val = node_state.get("value")
            prev_version = node_state.get("prev_version", -1)

            if not state_key or not isinstance(state_key, str):
                return web.json_response({"status": "rejected", "reason": "Missing key"}, status=400)

            # 4. Versioned, append-only protection (basic replay protection)
            current_ver = self.state_versions.get(state_key, -1)
            if prev_version != current_ver:
                return web.json_response({
                    "status": "conflict",
                    "reason": "Version mismatch",
                    "current_version": current_ver
                }, status=409)
                
            if state_key:
                self.state_lattice[state_key] = state_val
                self.state_versions[state_key] = current_ver + 1
                
            return web.json_response({
                "status": "synchronized",
                "node_id": self.node_id,
                "genesis_anchor": self.genesis_hash,
                "lattice_state": self.state_lattice,
                "versions": self.state_versions
            }, status=200)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=400)