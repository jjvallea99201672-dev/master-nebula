"""Command-line entrypoint: ``lattice-keeper``.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

import structlog
from aiohttp import web

from . import __version__
from .config import ConfigError, Settings
from .crypto import KeyFileError, KeyRing, PQCUnavailableError


def configure_logging(level: str = "INFO") -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _keyring(s: Settings) -> KeyRing:
    try:
        return KeyRing.load_or_create(s.keys_dir, s.key_passphrase, s.require_pqc)
    except (PQCUnavailableError, KeyFileError) as exc:
        print(f"key error: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc


def cmd_serve(_: argparse.Namespace) -> int:
    from .server import create_app

    s = _settings()
    configure_logging(s.log_level)
    try:
        app = create_app(s, keyring=_keyring(s))
    except RuntimeError as exc:  # startup safety checks (e.g. operator key placement)
        print(f"startup error: {exc}", file=sys.stderr)
        return 4
    # run_app installs SIGINT/SIGTERM handlers and shuts down gracefully.
    web.run_app(
        app,
        host=s.api_host,
        port=s.api_port,
        shutdown_timeout=s.shutdown_timeout_seconds,
        access_log=None,
        print=lambda m: structlog.get_logger().info("startup", message=m),
    )
    return 0


def cmd_keygen(a: argparse.Namespace) -> int:
    if getattr(a, "operator", False):
        return cmd_operator_keygen(a)
    s = _settings()
    ring = _keyring(s)
    print(
        json.dumps(
            {
                "keys_dir": s.keys_dir,
                "encrypted_at_rest": ring.encrypted_at_rest,
                "pqc": ring.pqc_available,
                "public": ring.public.to_json(),
            },
            indent=2,
        )
    )
    return 0


# ------------------------------------------------------------ operator / policy
def _operator_passphrase(confirm: bool, allow_empty: bool) -> str | None:
    pw = os.environ.get("OPERATOR_KEY_PASSPHRASE")
    if pw:
        return pw
    if allow_empty:
        return None
    if not sys.stdin.isatty():
        print(
            "operator key passphrase required: set OPERATOR_KEY_PASSPHRASE or run interactively "
            "(or pass --no-passphrase, not recommended)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    pw = getpass.getpass("Operator key passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != pw:
        print("passphrases do not match", file=sys.stderr)
        raise SystemExit(2)
    if not pw:
        print("empty passphrase; use --no-passphrase to store the key unencrypted", file=sys.stderr)
        raise SystemExit(2)
    return pw


def _pqc_for_operator(no_pqc: bool):
    from .crypto import load_pqc_backend

    if no_pqc:
        return None, "disabled by --no-pqc"
    return load_pqc_backend()


def cmd_operator_keygen(a: argparse.Namespace) -> int:
    from .operator import OPERATOR_PUB_FILE, OperatorKey

    out = Path(a.out)
    backend, reason = _pqc_for_operator(a.no_pqc)
    if backend is None and not a.no_pqc:
        print(
            f"warning: liboqs unavailable ({reason}); creating an Ed25519-only operator key. "
            "A server with REQUIRE_PQC=true will refuse it.",
            file=sys.stderr,
        )
    pw = _operator_passphrase(confirm=True, allow_empty=a.no_passphrase)
    try:
        key = OperatorKey.generate(out, pw, backend, force=a.force)
    except KeyFileError as exc:
        print(f"key error: {exc}", file=sys.stderr)
        return 3
    pub = key.public
    print(
        json.dumps(
            {
                "operator_key_dir": str(out),
                "encrypted_at_rest": pw is not None,
                "alg": pub.to_json()["alg"],
                "ed25519_key_id": pub.summary()["ed25519_key_id"],
                "public_key_file": str(out / OPERATOR_PUB_FILE),
            },
            indent=2,
        )
    )
    print(
        "\nKeep this directory OFF the server. Give the server only the public key:\n"
        f"  OPERATOR_PUBKEY_FILE=/path/to/{OPERATOR_PUB_FILE}\n"
        "For the ESP32 firmware (Ed25519 part only):\n"
        f'  #define LK_OPERATOR_ED25519_PUBKEY_B64 "{pub.summary()["ed25519_public_key"]}"',
        file=sys.stderr,
    )
    return 0


def _read_json(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(data, dict):
        print(f"{path} must contain a JSON object", file=sys.stderr)
        raise SystemExit(2)
    return data


def _write_json(path: str | None, obj: dict) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    if path in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(path).write_text(text)


def _policy_of(doc: dict) -> dict:
    """Accept a bare policy, a GET /policy response, or a signed {policy, signature} file."""
    return doc["policy"] if isinstance(doc.get("policy"), dict) else doc


def cmd_policy_next(a: argparse.Namespace) -> int:
    from .policy import next_proposal

    current = _policy_of(_read_json(a.current))
    _write_json(a.out, next_proposal(current, note=a.note or ""))
    if a.out not in (None, "-"):
        print(
            f"Draft v{int(current['version']) + 1} written to {a.out}; edit 'values', then policy-sign.",
            file=sys.stderr,
        )
    return 0


def cmd_policy_sign(a: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from .crypto import canonical_json, sha256_hex
    from .operator import OperatorKey
    from .policy import PolicyDocument

    doc = _policy_of(_read_json(a.policy))
    try:
        PolicyDocument.model_validate(doc)
    except ValidationError as exc:
        print(f"policy is invalid, not signing:\n{exc}", file=sys.stderr)
        return 2
    backend, _ = _pqc_for_operator(a.no_pqc)
    pw = _operator_passphrase(confirm=False, allow_empty=a.no_passphrase)
    try:
        key = OperatorKey.load(a.key_dir, pw, backend)
    except KeyFileError as exc:
        print(f"key error: {exc}", file=sys.stderr)
        return 3
    payload = canonical_json(doc)
    signed = {"policy": doc, "signature": key.sign(payload)}
    _write_json(a.out, signed)
    print(
        f"Signed policy v{doc['version']} sha256={sha256_hex(payload)} alg={signed['signature']['alg']}\n"
        "Submit with: curl -X PUT -H 'Authorization: Bearer $LATTICE_API_KEY' "
        "-H 'Content-Type: application/json' --data @<file> https://<server>/policy",
        file=sys.stderr,
    )
    return 0


def cmd_policy_verify(a: argparse.Namespace) -> int:
    from .crypto import canonical_json, load_pqc_backend
    from .operator import OperatorPublicKey

    try:
        pub = OperatorPublicKey.from_file(a.pubkey)
    except KeyFileError as exc:
        print(f"key error: {exc}", file=sys.stderr)
        return 3
    doc = _read_json(a.signed)
    backend, _ = load_pqc_backend() if pub.hybrid else (None, None)
    result = pub.verify(canonical_json(doc.get("policy") or {}), doc.get("signature") or {}, backend)
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


def cmd_verify_audit(_: argparse.Namespace) -> int:
    from .audit import AuditLog
    from .server import connect_redis

    s = _settings()
    ring = _keyring(s)

    async def run() -> dict:
        client = await connect_redis(s.redis_url, s.redis_connect_retries)
        try:
            return await AuditLog(client, ring).verify()
        finally:
            await client.aclose()

    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


def cmd_rag_build(a: argparse.Namespace) -> int:
    from .rag import build_index

    s = _settings()
    pin = build_index(a.source, a.out, s.ollama_host, s.rag_embed_model)
    print(f"Index written to {a.out}\nSet RAG_INDEX_DIR={a.out}\nSet RAG_INDEX_MANIFEST_SHA256={pin}")
    return 0


def cmd_rag_manifest(a: argparse.Namespace) -> int:
    from .rag import write_manifest

    pin = write_manifest(a.index_dir)
    print(f"Set RAG_INDEX_MANIFEST_SHA256={pin}")
    return 0


def _operator_keygen_args(p: argparse.ArgumentParser, required: bool) -> None:
    p.add_argument(
        "--out",
        required=required,
        default=None if required else "./operator-keys",
        help="operator key directory (keep it off the server)",
    )
    p.add_argument("--no-pqc", action="store_true", help="Ed25519-only even if liboqs is available")
    p.add_argument("--no-passphrase", action="store_true", help="store the key unencrypted (not recommended)")
    p.add_argument("--force", action="store_true", help="overwrite an existing operator key")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lattice-keeper", description="Lattice Keeper service")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the HTTP server").set_defaults(fn=cmd_serve)
    kg = sub.add_parser("keygen", help="create/load server keys and print public keys")
    kg.add_argument("--operator", action="store_true", help="same as operator-keygen")
    _operator_keygen_args(kg, required=False)
    kg.set_defaults(fn=cmd_keygen)
    ok = sub.add_parser("operator-keygen", help="create the OFFLINE operator signing key")
    _operator_keygen_args(ok, required=True)
    ok.set_defaults(fn=cmd_operator_keygen)
    pn = sub.add_parser("policy-next", help="draft policy version N+1 from a saved GET /policy response")
    pn.add_argument("--current", required=True, help="file with the current policy (GET /policy output)")
    pn.add_argument("--out", default="-")
    pn.add_argument("--note", default="")
    pn.set_defaults(fn=cmd_policy_next)
    ps = sub.add_parser("policy-sign", help="sign a policy file with the operator key (offline)")
    ps.add_argument("policy", help="proposed policy JSON")
    ps.add_argument("--key-dir", required=True, help="operator key directory")
    ps.add_argument("--out", default="-", help="signed output (body for PUT /policy)")
    ps.add_argument("--no-passphrase", action="store_true", help="operator key is not encrypted")
    ps.add_argument("--no-pqc", action="store_true", help=argparse.SUPPRESS)
    ps.set_defaults(fn=cmd_policy_sign)
    pv = sub.add_parser("policy-verify", help="verify a signed policy file against operator_pubkey.json")
    pv.add_argument("signed")
    pv.add_argument("--pubkey", required=True)
    pv.set_defaults(fn=cmd_policy_verify)
    sub.add_parser("verify-audit", help="verify the audit hash chain and signatures").set_defaults(
        fn=cmd_verify_audit
    )
    rb = sub.add_parser("rag-build", help="build a FAISS index from .txt/.md files ([rag] extra)")
    rb.add_argument("--source", required=True)
    rb.add_argument("--out", required=True)
    rb.set_defaults(fn=cmd_rag_build)
    rm = sub.add_parser("rag-manifest", help="(re)write manifest.json for an index you trust")
    rm.add_argument("index_dir")
    rm.set_defaults(fn=cmd_rag_manifest)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
