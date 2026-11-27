"""gridmem command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import VERSION
from . import evidence as ev
from .ask import ask_models, load_pending, render, resolve_pending
from .briefing import DEFAULT_MAX_CHARS, build_briefing
from .ingest import apply, describe, parse_reply
from .providers import ProviderError, get_adapter
from .store import KINDS, STATUSES, GridMemError, Store


def _fmt(e, long=False):
    s = f"{e['id']}  {e['status']:<18} {e['kind']:<10} {e['text'][:110 if not long else None]}"
    if long:
        s = json.dumps({k: v for k, v in e.items() if k != "entry_hash"}, indent=2)
    return s


def cmd_init(st, a):
    print(f"Grid Memory home: {st.home}\nsigning: {st.identity['algorithm']} key {st.identity['key_id']}")
    seq, head = st.head()
    print(f"events: {seq}  head: {head}")


def cmd_add(st, a):
    r = st.add(a.kind, a.text, a.tags, a.source)
    print(f"added {r['id']} (claimed)")


def cmd_correct(st, a):
    r = st.correct(a.id, a.text, a.source)
    print(f"added {r['id']} correcting {a.id}; {a.id} is now 'superseded' and stays visible")


def cmd_retract(st, a):
    r = st.retract(a.id, a.reason, a.source)
    print(f"added {r['id']} retracting {a.id}; {a.id} stays visible as 'retracted'")


def cmd_verify(st, a):
    if a.hash:
        e = ev.hash_check(a.hash[0], a.hash[1])
    elif a.output:
        e = ev.output_check(a.output, a.expect or "")
    elif a.command:
        print(f"running (operator-initiated): {a.command}", file=sys.stderr)
        e = ev.command_check(a.command, cwd=a.cwd, expect=a.expect or "", timeout=a.timeout)
    elif a.url:
        e = ev.url_reference(a.url, a.i_confirm, a.note or "")
    elif a.operator_confirm:
        st.operator_confirm(a.id, a.note or "")
        print(f"{a.id}: operator-confirmed (recorded as Jay's word, not a machine check)")
        return
    elif a.agreed_by:
        st.record_agreement(a.id, a.agreed_by, a.note or "")
        print(f"{a.id}: recorded that {a.agreed_by} agreed. Status unchanged - agreement is not evidence.")
        return
    else:
        raise GridMemError("give one of --hash FILE SHA256, --output FILE --expect TEXT, --command CMD, "
                           "--url URL --i-confirm, --operator-confirm, --agreed-by MODEL")
    st.attach_evidence(a.id, e)
    status = st.entries()[a.id]["status"]
    print(f"{a.id}: {e['type']} check {e['result'].upper()} -> status {status}")
    if e["type"] == "hash":
        print(f"  expected {e['expected_sha256']}\n  computed {e['computed_sha256']}")
    if e["result"] != "pass":
        sys.exit(1)


def cmd_list(st, a):
    rows = st.search(a.query or "", a.status, a.kind, a.tag)
    for e in rows:
        print(_fmt(e))
    print(f"({len(rows)} entries)", file=sys.stderr)


def cmd_show(st, a):
    e = st.entries().get(a.id)
    if not e:
        raise GridMemError(f"no memory entry {a.id}")
    print(_fmt(e, long=True))
    if e["agreed_by"]:
        print("NOTE: agreed_by is context only; it is not evidence and did not change the status.")


def cmd_history(st, a):
    for r in st.history(a.id):
        b = r["body"]
        if r["op"] == "entry":
            line = f"{b['kind']}: {b['text'][:100]}" + (f" (supersedes {b['supersedes']})" if b.get("supersedes") else "")
        elif r["op"] == "evidence":
            line = f"evidence on {r['target']}: {b['evidence']['type']} {b['result']}"
        elif r["op"] == "confirm":
            line = f"operator-confirmed {r['target']} by {b['by']}"
        else:
            line = f"{b['model']} agreed with {r['target']} (not evidence)"
        print(f"{r['id']}  {r['ts']}  {line}")


def cmd_briefing(st, a):
    out = build_briefing(st, topic=a.topic, max_chars=a.max_chars, model=a.model)
    if a.out:
        Path(a.out).write_text(out + "\n")
        print(f"wrote {a.out} ({len(out)} chars)", file=sys.stderr)
    else:
        print(out)


def _read_input(path):
    if path and path != "-":
        return Path(path).read_text(errors="replace")
    return sys.stdin.read()


def cmd_ingest(st, a):
    text = _read_input(a.file)
    source = a.source + (f" ({a.session})" if a.session else "")
    parsed = parse_reply(text, known_ids=set(st.entries()))
    for n in parsed["notes"]:
        print(f"note: {n}")
    print(f"source: {source}  (everything below would be saved as 'claimed')")
    for i, p in enumerate(parsed["proposals"], 1):
        print(f"  {i}. {describe(p)}")
    for ln, why in parsed["rejected"]:
        print(f"  REJECTED: {ln}  -- {why}")
    if not parsed["proposals"]:
        print("nothing to save")
        return
    if not a.yes:
        print("\nNot saved. Re-run with --yes to append these (add --only 1,3 to pick some).")
        return
    props = parsed["proposals"]
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        props = [p for i, p in enumerate(props, 1) if i in keep]
    saved = apply(st, props, source)
    print("saved: " + ", ".join(r["id"] for r in saved))


def cmd_export(st, a):
    data = {"format": f"gridmem/{VERSION}", "identity": {k: st.identity[k] for k in ("algorithm", "public_key", "key_id")},
            "check": st.check(), "events": st.events()}
    if a.format == "md":
        lines = [f"# Grid Memory export\n\nhead `{data['check'].get('head')}`\n"]
        for e in st.entries().values():
            lines.append(f"- **{e['id']}** [{e['status']}] ({e['kind']}; {e['source']}) {e['text']}")
        out = "\n".join(lines) + "\n"
    else:
        out = json.dumps(data, indent=2)
    if a.out:
        Path(a.out).write_text(out)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(out)


def cmd_check(st, a):
    r = st.check()
    print(json.dumps(r, indent=2))
    bad = not r["ok"]
    if a.evidence:
        for e in st.entries().values():
            for x in e["evidence"]:
                rr = ev.recheck(x)
                print(f"{e['id']} {x['type']}: now {rr['result']}" + (f" ({rr.get('why')})" if rr.get("why") else ""))
                bad |= rr["result"] in ("fail", "changed")
    sys.exit(1 if bad else 0)


def cmd_ask(st, a):
    names = [n for n in a.models.split(",") if n.strip()]
    adapters = []
    for n in names:
        try:
            adapters.append(get_adapter(n))
        except ProviderError as e:
            print(e, file=sys.stderr)
    res = ask_models(st, adapters, a.question, topic=a.topic, max_chars=a.max_chars, save=not a.no_save)
    print(render(res))


def cmd_pending(st, a):
    if a.action == "list":
        items = load_pending(st)
        for i in items:
            print(f"{i['pid']}  from {i['source']}  ({i['queued_at']}): {describe(i['proposal'])}")
        print(f"({len(items)} pending)", file=sys.stderr)
        return
    if not a.pids:
        raise GridMemError("name proposal ids (or 'all')")
    if a.action == "accept" and not a.yes:
        for i in load_pending(st):
            if i["pid"] in a.pids or "all" in a.pids:
                print(f"would save as claimed: {describe(i['proposal'])}  [{i['source']}]")
        print("Not saved. Re-run with --yes.")
        return
    written = resolve_pending(st, a.pids, accept=a.action == "accept")
    print(("saved: " + ", ".join(w["id"] for w in written)) if written else "dropped")


def cmd_seed(st, a):
    from .seed import seed
    for line in seed(st, run_keeper_tests=a.run_keeper_tests):
        print(line)


def cmd_serve(st, a):
    from .web import serve
    st.close()
    serve(st.home, a.port)


def build_parser():
    p = argparse.ArgumentParser(prog="gridmem", description="Grid Memory: a shared notebook you hand to AI models.")
    p.add_argument("--home", help="data directory (default $GRIDMEM_HOME or ./data)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the store and signing key").set_defaults(f=cmd_init)

    s = sub.add_parser("add", help="add an entry (always 'claimed')")
    s.add_argument("kind", choices=[k for k in KINDS if k not in ("correction", "retraction")])
    s.add_argument("text"); s.add_argument("--tags", default=""); s.add_argument("--source", default="Jay")
    s.set_defaults(f=cmd_add)

    s = sub.add_parser("correct", help="supersede an entry with corrected text")
    s.add_argument("id"); s.add_argument("text"); s.add_argument("--source", default="Jay"); s.set_defaults(f=cmd_correct)
    s = sub.add_parser("retract", help="retract an entry (stays visible)")
    s.add_argument("id"); s.add_argument("reason"); s.add_argument("--source", default="Jay"); s.set_defaults(f=cmd_retract)

    s = sub.add_parser("verify", help="attach evidence (recomputed now) or record confirmation/agreement")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--hash", nargs=2, metavar=("FILE", "SHA256"), help="recompute sha256 of FILE")
    g.add_argument("--output", metavar="FILE", help="saved test/command output; needs --expect")
    g.add_argument("--command", help="run a command now; passes if exit 0 (and --expect found)")
    g.add_argument("--url", help="URL/document reference; needs --i-confirm")
    g.add_argument("--operator-confirm", action="store_true", help="Jay's word -> 'operator-confirmed'")
    g.add_argument("--agreed-by", metavar="MODEL", help="record agreement (never changes status)")
    s.add_argument("--expect"); s.add_argument("--cwd"); s.add_argument("--timeout", type=int, default=600)
    s.add_argument("--i-confirm", action="store_true"); s.add_argument("--note")
    s.set_defaults(f=cmd_verify)

    for name in ("list", "search"):
        s = sub.add_parser(name, help="list/search entries")
        s.add_argument("query", nargs="?"); s.add_argument("--status", choices=STATUSES)
        s.add_argument("--kind", choices=KINDS); s.add_argument("--tag"); s.set_defaults(f=cmd_list)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(f=cmd_show)
    s = sub.add_parser("history"); s.add_argument("id"); s.set_defaults(f=cmd_history)

    s = sub.add_parser("briefing", help="paste-ready block for a chat model")
    s.add_argument("--topic"); s.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    s.add_argument("--model"); s.add_argument("--out"); s.set_defaults(f=cmd_briefing)

    s = sub.add_parser("ingest", help="read a pasted model reply; only its gridmem block is used")
    s.add_argument("file", nargs="?", default="-"); s.add_argument("--source", required=True)
    s.add_argument("--session"); s.add_argument("--yes", action="store_true"); s.add_argument("--only")
    s.set_defaults(f=cmd_ingest)

    s = sub.add_parser("export"); s.add_argument("--format", choices=["json", "md"], default="json")
    s.add_argument("--out"); s.set_defaults(f=cmd_export)
    s = sub.add_parser("check", help="verify chain integrity and signatures")
    s.add_argument("--evidence", action="store_true", help="also re-run file-based evidence"); s.set_defaults(f=cmd_check)

    s = sub.add_parser("ask", help="Phase 2: send briefing + question to models via their APIs")
    s.add_argument("question"); s.add_argument("--models", default="grok,gemini,claude,chatgpt")
    s.add_argument("--topic"); s.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    s.add_argument("--no-save", action="store_true"); s.set_defaults(f=cmd_ask)

    s = sub.add_parser("pending", help="review proposals queued by `ask`")
    s.add_argument("action", choices=["list", "accept", "reject"]); s.add_argument("pids", nargs="*")
    s.add_argument("--yes", action="store_true"); s.set_defaults(f=cmd_pending)

    s = sub.add_parser("seed", help="add the starter entries (once)")
    s.add_argument("--run-keeper-tests", action="store_true"); s.set_defaults(f=cmd_seed)
    s = sub.add_parser("serve", help="local web UI on 127.0.0.1")
    s.add_argument("--port", type=int, default=8792); s.set_defaults(f=cmd_serve)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        st = Store(a.home) if a.home else Store()
    except GridMemError as e:
        print(f"error: {e}", file=sys.stderr); return 2
    try:
        a.f(st, a)
    except (GridMemError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        try:
            st.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
