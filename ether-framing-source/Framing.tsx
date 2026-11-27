import React, { useCallback, useEffect, useRef, useState } from "react";
import "./index.css";

/* ---------------------------------------------------------------
   Framing (mirrors ether.py)
--------------------------------------------------------------- */
const MAGIC = new TextEncoder().encode("ETHR");
const HEADER = 8;
const MAX_PAYLOAD = 1 << 20;

function encodeFrame(obj: unknown): Uint8Array {
  const payload = new TextEncoder().encode(JSON.stringify(obj));
  if (payload.length > MAX_PAYLOAD) throw new Error("payload too large");
  const buf = new Uint8Array(HEADER + payload.length);
  buf.set(MAGIC, 0);
  new DataView(buf.buffer).setUint32(4, payload.length, false);
  buf.set(payload, HEADER);
  return buf;
}

class FrameReader {
  buf: Uint8Array = new Uint8Array(0);
  push(chunk: Uint8Array) {
    const merged = new Uint8Array(this.buf.length + chunk.length);
    merged.set(this.buf, 0);
    merged.set(chunk, this.buf.length);
    this.buf = merged;
    const frames: unknown[] = [];
    while (this.buf.length >= HEADER) {
      const dv = new DataView(this.buf.buffer, this.buf.byteOffset, HEADER);
      const magic = this.buf.subarray(0, 4);
      if (!(magic[0] === 77 && magic[1] === 84 && magic[2] === 72 && magic[3] === 82))
        throw new Error("bad magic");
      const len = dv.getUint32(4, false);
      if (len > MAX_PAYLOAD) throw new Error("declared length too large");
      if (this.buf.length < HEADER + len) break;
      const payload = this.buf.subarray(HEADER, HEADER + len);
      try {
        frames.push(JSON.parse(new TextDecoder().decode(payload)));
      } catch (e) {
        throw new Error("bad JSON: " + (e instanceof Error ? e.message : String(e)));
      }
      this.buf = this.buf.subarray(HEADER + len);
    }
    return frames;
  }
}

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void };
type StatusKind = "idle" | "connecting" | "connected" | "error";

type Hit = { impression?: string; digest?: string; [k: string]: unknown };
type EtherReply = {
  ok?: boolean;
  hits?: Hit[];
  field_energy?: number;
  nebula_spread?: number;
  records?: number;
  [k: string]: unknown;
};

type SettingsValue = { wsUrl: string; thought: string; noise: string; query: string };

async function callRecords(action: string, args: Record<string, unknown>) {
  const res = await fetch("actions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, args }),
  });
  const json = await res.json().catch(() => null);
  if (!res.ok || !json || json.error) throw new Error(json?.error || `records ${action} failed`);
  return json.data;
}

export default function App() {
  const [wsUrl, setWsUrl] = useState("ws://127.0.0.1:8765");
  const [thought, setThought] = useState("Sabaoth");
  const [noise, setNoise] = useState("0");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusKind>("idle");
  const [statusText, setStatusText] = useState("disconnected");
  const [hits, setHits] = useState<Hit[]>([]);
  const [hasQueried, setHasQueried] = useState(false);
  const [lastReply, setLastReply] = useState<unknown>("—");
  const [meters, setMeters] = useState<{ records?: number; energy?: number; spread?: number }>({});
  const [busy, setBusy] = useState<"tx" | "rq" | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const readerRef = useRef(new FrameReader());
  const pendingRef = useRef<Pending[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const particlesRef = useRef<{ x: number; y: number; vx: number; vy: number }[]>([]);
  const settingsIdRef = useRef<string | null>(null);
  const settingsLoadedRef = useRef(false);
  const saveTimerRef = useRef<number | null>(null);
  const valuesRef = useRef<SettingsValue>({ wsUrl, thought, noise, query });
  valuesRef.current = { wsUrl, thought, noise, query };

  const show = useCallback((obj: unknown) => setLastReply(obj), []);

  const updateMeters = useCallback((patch: { records?: number; energy?: number; spread?: number }) => {
    setMeters((prev) => ({ ...prev, ...patch }));
  }, []);

  const persistSettings = useCallback(async () => {
    if (!settingsLoadedRef.current) return;
    const value = { ...valuesRef.current };
    try {
      if (settingsIdRef.current) {
        await callRecords("records.replace", { collection: "ether-settings", id: settingsIdRef.current, value });
      } else {
        const created = await callRecords("records.create", {
          collection: "ether-settings",
          value,
          idempotency_key: "ether-settings-v1",
        });
        if (created?.id) settingsIdRef.current = created.id;
      }
    } catch {
      // Records API unavailable in preview / offline — keep the live ether session working.
    }
  }, []);

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => { void persistSettings(); }, 700);
  }, [persistSettings]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await callRecords("records.list", { collection: "ether-settings" });
        const first = data?.records?.[0];
        if (!cancelled && first?.value) {
          const v = first.value as Partial<SettingsValue>;
          settingsIdRef.current = first.id;
          if (typeof v.wsUrl === "string" && v.wsUrl) setWsUrl(v.wsUrl);
          if (typeof v.thought === "string") setThought(v.thought);
          if (typeof v.noise === "string") setNoise(v.noise);
          if (typeof v.query === "string") setQuery(v.query);
        }
      } catch {
        // No saved settings yet, or API unavailable — use defaults.
      } finally {
        if (!cancelled) settingsLoadedRef.current = true;
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => () => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    if (wsRef.current) wsRef.current.close();
  }, []);

  /* Canvas: nebula */
  const pulse = useCallback((energy?: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const r = canvas.getBoundingClientRect();
    const cx = r.width / 2;
    const cy = r.height / 2;
    const strength = Math.min(8, 1 + (energy || 0) * 0.4);
    for (const p of particlesRef.current) {
      const dx = p.x - cx;
      const dy = p.y - cy;
      const d = Math.hypot(dx, dy) || 1;
      p.vx += (dx / d) * strength * (0.5 + Math.random() * 0.5);
      p.vy += (dy / d) * strength * (0.5 + Math.random() * 0.5);
    }
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let dpr = 1;

    const resize = () => {
      dpr = Math.max(1, window.devicePixelRatio || 1);
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(r.width * dpr));
      canvas.height = Math.max(1, Math.floor(r.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!particlesRef.current.length) {
        particlesRef.current = Array.from({ length: 200 }, () => ({
          x: Math.random() * r.width,
          y: Math.random() * r.height,
          vx: 0,
          vy: 0,
        }));
      }
    };

    const step = () => {
      const r = canvas.getBoundingClientRect();
      const W = r.width;
      const H = r.height;
      for (const p of particlesRef.current) {
        p.vx *= 0.98;
        p.vy *= 0.98;
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x += W; if (p.x > W) p.x -= W;
        if (p.y < 0) p.y += H; if (p.y > H) p.y -= H;
      }
      ctx.clearRect(0, 0, W, H);
      ctx.strokeStyle = "rgba(143,180,255,0.06)";
      ctx.lineWidth = 1;
      const arr = particlesRef.current;
      for (let i = 0; i < arr.length; i++) {
        for (let j = i + 1; j < arr.length; j++) {
          const a = arr[i];
          const b = arr[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy;
          if (d2 < 90 * 90) {
            const t = 1 - Math.sqrt(d2) / 90;
            ctx.globalAlpha = t * 0.35;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }
      ctx.globalAlpha = 1;
      for (const p of arr) {
        const sp = Math.hypot(p.vx, p.vy);
        const g = Math.min(1, sp / 4);
        ctx.fillStyle = `rgba(${143 + 80 * g},${180 + 40 * g},255,${0.35 + 0.55 * g})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 1.2 + g * 1.6, 0, Math.PI * 2);
        ctx.fill();
      }
      raf = requestAnimationFrame(step);
    };

    resize();
    raf = requestAnimationFrame(step);
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  const request = useCallback((obj: unknown): Promise<unknown> => {
    return new Promise((resolve, reject) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error("not connected"));
        return;
      }
      pendingRef.current.push({ resolve, reject });
      try {
        ws.send(encodeFrame(obj));
      } catch (e) {
        pendingRef.current.pop();
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    });
  }, []);

  const connect = useCallback(() => {
    const url = valuesRef.current.wsUrl.trim();
    if (!url) return;
    setStatus("connecting");
    setStatusText("connecting…");
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      setStatus("error");
      setStatusText("bad url");
      return;
    }
    wsRef.current = ws;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      setStatus("connected");
      setStatusText("connected");
      readerRef.current = new FrameReader();
      const iv = window.setInterval(async () => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          window.clearInterval(iv);
          try {
            const s = (await request({ op: "state" })) as EtherReply | null;
            if (s && s.ok) {
              show(s);
              updateMeters({ records: s.records, energy: s.field_energy, spread: s.nebula_spread });
            }
          } catch { /* state is best-effort */ }
        }
      }, 200);
    };
    ws.onclose = () => {
      setStatus("error");
      setStatusText("disconnected");
      wsRef.current = null;
      pendingRef.current.splice(0).forEach((p) => p.reject(new Error("closed")));
    };
    ws.onerror = () => {
      setStatus("error");
      setStatusText("error");
    };
    ws.onmessage = (ev) => {
      let frames: unknown[];
      try {
        frames = readerRef.current.push(new Uint8Array(ev.data as ArrayBuffer));
      } catch (e) {
        setStatus("error");
        setStatusText(e instanceof Error ? e.message : String(e));
        return;
      }
      for (const f of frames) {
        const p = pendingRef.current.shift();
        if (p) p.resolve(f);
        else show({ unsolicited: f });
      }
    };
  }, [request, show, updateMeters]);

  const toggleConnect = () => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.close();
      return;
    }
    connect();
  };

  const onTransmit = async () => {
    if (!thought) return;
    setBusy("tx");
    try {
      const r = (await request({ op: "transmit", thought, noise: parseFloat(noise) || 0 })) as EtherReply | null;
      show(r);
      if (r && r.ok) {
        pulse(r.field_energy);
        updateMeters({ energy: r.field_energy, spread: r.nebula_spread });
      }
      void persistSettings();
    } catch (e) {
      show({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const onResonate = async () => {
    if (!query) return;
    setBusy("rq");
    try {
      const r = (await request({ op: "resonate", query })) as EtherReply | null;
      show(r);
      setHits((r && r.hits) || []);
      setHasQueried(true);
      void persistSettings();
    } catch (e) {
      show({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const recordsPct = meters.records !== undefined ? Math.min(100, (meters.records / 1000) * 100) : 0;
  const energyPct = meters.energy !== undefined ? Math.min(100, (meters.energy / 50) * 100) : 0;
  const spreadPct = meters.spread !== undefined ? Math.min(100, (meters.spread / 2) * 100) : 0;
  const connected = status === "connected";

  const shellStyle: React.CSSProperties = {
    boxSizing: "border-box",
    paddingTop: "var(--safe-area-inset-top)",
  };

  return (
    <div
      data-safe-area-inset-root
      style={shellStyle}
      className="min-h-screen w-full overflow-x-clip bg-[var(--bg)] text-[var(--fg)]"
    >
      <div className="mx-auto flex min-h-screen w-full max-w-[1600px] flex-col lg:h-screen lg:overflow-hidden">
        <header
          data-safe-area-sticky-top
          style={{ top: "var(--safe-area-inset-top)" }}
          className="sticky z-10 flex flex-wrap items-center gap-3 border-b border-[var(--line)] bg-[var(--bg)] px-4 py-3 sm:px-[18px] sm:py-[14px]"
        >
          <h1 className="m-0 text-[15px] font-semibold tracking-[0.02em]">ether</h1>
          <span className="text-xs text-[var(--dim)]">four faces · one medium</span>
          <span
            role="status"
            className={
              connected
                ? "ml-auto rounded-full border border-[#23402f] bg-[#0e1a14] px-[9px] py-[3px] text-xs text-[var(--ok)]"
                : status === "error"
                  ? "ml-auto rounded-full border border-[#402323] bg-[#1a0e0e] px-[9px] py-[3px] text-xs text-[var(--err)]"
                  : "ml-auto rounded-full border border-[var(--line)] px-[9px] py-[3px] text-xs text-[var(--dim)]"
            }
          >
            {statusText}
          </span>
        </header>

        <div className="flex min-h-0 flex-1 flex-col lg:grid lg:grid-cols-[minmax(0,1fr)_360px]">
          <main className="relative min-h-[340px] min-w-0 flex-1 lg:min-h-0">
            <canvas ref={canvasRef} className="block h-full min-h-[340px] w-full lg:min-h-0" aria-label="Nebula visualization of the ether medium" />
            <p className="pointer-events-none absolute left-4 top-3 m-0 text-[11px] tracking-wide text-[var(--dim)]">
              the medium · 200 particles
            </p>
          </main>

          <aside className="flex min-w-0 flex-col gap-[14px] overflow-y-auto border-t border-[var(--line)] p-4 sm:p-[16px] lg:border-l lg:border-t-0">
            <section className="flex flex-col gap-[6px]">
              <h2 className="m-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--dim)]">bridge</h2>
              <div className="flex gap-[6px]">
                <label className="sr-only" htmlFor="wsurl">Bridge WebSocket URL</label>
                <input
                  id="wsurl"
                  value={wsUrl}
                  onChange={(e) => { setWsUrl(e.target.value); scheduleSave(); }}
                  onBlur={() => { void persistSettings(); }}
                  spellCheck={false}
                  inputMode="url"
                />
                <button id="conn" type="button" onClick={toggleConnect} className="shrink-0">
                  {connected ? "disconnect" : "connect"}
                </button>
              </div>
            </section>

            <section className="flex flex-col gap-[6px]">
              <h2 className="m-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--dim)]">transmit</h2>
              <label className="sr-only" htmlFor="thought">Thought to transmit</label>
              <textarea
                id="thought"
                value={thought}
                placeholder="Sabaoth"
                onChange={(e) => { setThought(e.target.value); scheduleSave(); }}
                onBlur={() => { void persistSettings(); }}
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                    e.preventDefault();
                    void onTransmit();
                  }
                }}
              />
              <div className="flex gap-[6px]">
                <label className="flex flex-[2] flex-col gap-1">
                  <span className="text-[11px] text-[var(--dim)]">noise</span>
                  <input
                    id="noise"
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={noise}
                    onChange={(e) => { setNoise(e.target.value); scheduleSave(); }}
                    onBlur={() => { void persistSettings(); }}
                  />
                </label>
                <button id="tx" type="button" className="flex-1 self-end" onClick={() => void onTransmit()} disabled={!thought || busy === "tx"}>
                  {busy === "tx" ? "sending…" : "transmit"}
                </button>
              </div>
              <p className="m-0 text-[11px] text-[var(--dim)]">Ctrl / Cmd + Enter sends.</p>
            </section>

            <section className="flex flex-col gap-[6px]">
              <h2 className="m-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--dim)]">resonate</h2>
              <div className="flex gap-[6px]">
                <label className="sr-only" htmlFor="query">Query</label>
                <input
                  id="query"
                  value={query}
                  placeholder="hosts"
                  spellCheck={false}
                  onChange={(e) => { setQuery(e.target.value); scheduleSave(); }}
                  onBlur={() => { void persistSettings(); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void onResonate(); } }}
                />
                <button id="rq" type="button" className="shrink-0" onClick={() => void onResonate()} disabled={!query || busy === "rq"}>
                  {busy === "rq" ? "querying…" : "query"}
                </button>
              </div>
              <ul className="m-0 flex list-none flex-col gap-1 p-0" aria-live="polite">
                {!hasQueried && hits.length === 0 && (
                  <li className="rounded-md border border-[var(--line)] px-2 py-[6px] text-center text-xs text-[var(--dim)]">
                    no query yet
                  </li>
                )}
                {hasQueried && hits.length === 0 && (
                  <li className="rounded-md border border-[var(--line)] px-2 py-[6px] text-center text-xs text-[var(--dim)]">no matches</li>
                )}
                {hits.map((h, i) => (
                  <li key={`${h.digest ?? i}-${i}`} className="flex justify-between gap-2 rounded-md border border-[var(--line)] px-2 py-[6px] text-xs">
                    <span className="min-w-0 break-words">{String(h.impression ?? "")}</span>
                    <span className="shrink-0 text-[var(--dim)]">{String(h.digest ?? "")}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="flex flex-col gap-[6px]">
              <h2 className="m-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--dim)]">state</h2>
              <div className="grid grid-cols-[auto_1fr] items-center gap-x-[10px] gap-y-[2px] text-xs">
                <b className="font-medium text-[var(--dim)]">records</b>
                <span className="h-[6px] overflow-hidden rounded-[3px] border border-[var(--line)] bg-[var(--surface-2)]">
                  <i className="block h-full bg-[var(--accent)] transition-[width] duration-300" style={{ width: `${recordsPct}%` }} />
                </span>
                <b className="font-medium text-[var(--dim)]">energy</b>
                <span className="h-[6px] overflow-hidden rounded-[3px] border border-[var(--line)] bg-[var(--surface-2)]">
                  <i className="block h-full bg-[var(--accent)] transition-[width] duration-300" style={{ width: `${energyPct}%` }} />
                </span>
                <b className="font-medium text-[var(--dim)]">spread</b>
                <span className="h-[6px] overflow-hidden rounded-[3px] border border-[var(--line)] bg-[var(--surface-2)]">
                  <i className="block h-full bg-[var(--accent)] transition-[width] duration-300" style={{ width: `${spreadPct}%` }} />
                </span>
              </div>
              <p className="m-0 text-[11px] text-[var(--dim)]">
                {meters.records !== undefined ? `${meters.records} records · ` : ""}
                {meters.energy !== undefined ? `energy ${meters.energy} · ` : ""}
                {meters.spread !== undefined ? `spread ${meters.spread}` : "awaiting state"}
              </p>
            </section>

            <section className="flex flex-col gap-[6px]">
              <h2 className="m-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--dim)]">last reply</h2>
              <pre className="m-0 max-h-[260px] min-h-[80px] overflow-auto rounded-lg border border-[var(--line)] bg-[var(--surface)] p-[10px] text-xs">
                {typeof lastReply === "string" ? lastReply : JSON.stringify(lastReply, null, 2)}
              </pre>
            </section>
          </aside>
        </div>

        <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-[var(--line)] px-4 py-2 text-[11px] text-[var(--dim)] sm:px-[18px]">
          <span>wire: <code className="rounded bg-[var(--surface-2)] px-[5px] py-[1px] text-xs">ETHR | u32 len | JSON</code></span>
          <span>run: <code className="rounded bg-[var(--surface-2)] px-[5px] py-[1px] text-xs">python ether.py serve</code></span>
          <span>bridge: <code className="rounded bg-[var(--surface-2)] px-[5px] py-[1px] text-xs">python bridge.py</code></span>
        </footer>
      </div>
    </div>
  );
}
