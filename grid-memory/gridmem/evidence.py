"""Evidence types. Each builder RECOMPUTES its check now and returns pass/fail.

Only these can make an entry 'verified':
  hash     - sha256 of a file recomputed by this tool equals the expected value
  output   - a saved test/command output file contains an expected line (file sha256 recorded)
  command  - a command the operator runs from the CLI exits 0 (and optionally prints a line)
  url      - a URL/document reference the operator explicitly confirms
Operator confirmation without a check is a separate status ('operator-confirmed').
Model agreement is never evidence.
"""
from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .store import GridMemError, utc_now

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Union[str, Path]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_check(path: str, expected_sha256: str) -> Dict[str, Any]:
    exp = expected_sha256.strip().lower()
    if not HEX64.match(exp):
        raise GridMemError("expected sha256 must be 64 hex characters")
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise GridMemError(f"file not found: {p}")
    got = sha256_file(p)
    return {"type": "hash", "path": str(p), "size": p.stat().st_size, "expected_sha256": exp,
            "computed_sha256": got, "result": "pass" if got == exp else "fail", "checked_at": utc_now(),
            "reproduce": f"sha256sum {shlex.quote(str(p))}"}


def output_check(path: str, expect: str) -> Dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise GridMemError(f"file not found: {p}")
    if not expect.strip():
        raise GridMemError("--expect text is required for output evidence")
    text = p.read_text(errors="replace")
    hit = next((ln for ln in text.splitlines() if expect in ln), None)
    return {"type": "output", "path": str(p), "file_sha256": sha256_file(p), "expect": expect,
            "matched_line": hit, "result": "pass" if hit is not None else "fail", "checked_at": utc_now(),
            "reproduce": f"grep -F {shlex.quote(expect)} {shlex.quote(str(p))}"}


def command_check(command: Union[str, List[str]], cwd: Optional[str] = None, expect: str = "",
                  timeout: int = 600) -> Dict[str, Any]:
    """Run ONLY from the operator's CLI. Never called by ingest/ask/web."""
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise GridMemError("empty command")
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out, code = (p.stdout or "") + (p.stderr or ""), p.returncode
    except FileNotFoundError:
        out, code = f"command not found: {argv[0]}", 127
    except subprocess.TimeoutExpired:
        out, code = f"timed out after {timeout}s", -1
    ok = code == 0 and (not expect or expect in out)
    tail = out.strip().splitlines()[-15:]
    return {"type": "command", "argv": argv, "cwd": cwd, "exit_code": code, "expect": expect or None,
            "output_sha256": hashlib.sha256(out.encode()).hexdigest(), "output_tail": tail,
            "result": "pass" if ok else "fail", "checked_at": utc_now(),
            "reproduce": " ".join(shlex.quote(a) for a in argv)}


def url_reference(url: str, operator_confirmed: bool, note: str = "") -> Dict[str, Any]:
    if not re.match(r"^(https?://|file://|/)", url):
        raise GridMemError("reference must be an http(s) URL, file:// URL or absolute path")
    if not operator_confirmed:
        raise GridMemError("a URL/document reference only counts after the operator confirms it "
                           "(pass --i-confirm). Without that, add it as a reference instead.")
    return {"type": "url", "url": url, "note": note, "operator_confirmed": True,
            "result": "pass", "checked_at": utc_now()}


def recheck(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Re-run file-based evidence now (files can change after verification)."""
    try:
        if ev["type"] == "hash":
            return hash_check(ev["path"], ev["expected_sha256"])
        if ev["type"] == "output":
            r = output_check(ev["path"], ev["expect"])
            if r["result"] == "pass" and r["file_sha256"] != ev["file_sha256"]:
                r["result"], r["why"] = "changed", "file still matches but its sha256 changed since verification"
            return r
    except GridMemError as e:
        return {"type": ev["type"], "result": "fail", "why": str(e)}
    return {"type": ev["type"], "result": "not-rechecked", "why": "only hash/output evidence is re-run by check"}
