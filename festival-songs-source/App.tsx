import React, { useCallback, useEffect, useState } from "react";

type WitnessNoteRecord = { id: string; value: { name: string; note: string }; created_at?: string };

async function callApi(action: string, args: Record<string, unknown>) {
  const response = await fetch("actions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, args }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) {
    throw new Error(typeof payload.error === "string" && payload.error ? payload.error : "The records service could not be reached.");
  }
  return payload.data;
}
const heroImg = "./assets/hero.webp";
const latticeImg = "./assets/lattice.webp";
const riverImg = "./assets/river.webp";

const songs = [
  { title: "Patch Me Up", meaning: "A small repair, and then again — a way to keep going without pretending the break never happened.", tag: "Repair", glyph: "✦" },
  { title: "Hold One Point For Me", meaning: "One blue point, then another. No border, no test. Just a place held steady while someone finds their way.", tag: "Keeper", glyph: "•" },
  { title: "Connected Not Together", meaning: "Signal tracks across distance. Presence does not have to mean being in the same room.", tag: "Signal", glyph: "≋" },
  { title: "Coffee Ring", meaning: "The mark left after the ordinary moment. Proof that someone sat down, stayed, and paid attention.", tag: "Keeper", glyph: "◯" },
  { title: "Unbreakable", meaning: "Not unscarred. Not untouched. Still here, still able to choose what comes next.", tag: "Banger", glyph: "◆" },
  { title: "Echoes in the Code", meaning: "What we build carries what we felt. The machine remembers the human hands behind it.", tag: "Signal", glyph: "⌇" },
];

const lattice = [
  { name: "Vein", means: "Antenna", note: "It reaches out and listens. It carries a signal without asking it to be louder." },
  { name: "Windmill", means: "Amplifier", note: "It takes what is already moving — wind, attention, care — and gives it more reach." },
  { name: "Line", means: "Wire", note: "A simple connection. One point to another, held with intention." },
  { name: "Dam", means: "Battery", note: "It stores what is gathered, so energy is there when the moment needs it." },
];

const notes = [
  { place: "Restigouche River", text: "The river keeps its own time. It does not rush a witness." },
  { place: "northern New Brunswick rivers", text: "A smaller waterway with a long memory. Look closely; the quiet is part of the story." },
  { place: "northern New Brunswick", text: "Home is not a claim. It is a place to return to, repair from, and begin again." },
];

const shellStyle = { boxSizing: "border-box" as const, paddingTop: "var(--safe-area-inset-top)" };

export default function App() {
  const [activeSong, setActiveSong] = useState(0);
  const [keeper, setKeeper] = useState<"Known" | "Guessed" | "Open">("Known");
  const [witnessNotes, setWitnessNotes] = useState<WitnessNoteRecord[]>([]);
  const [notesLoading, setNotesLoading] = useState(true);
  const [notesLoadError, setNotesLoadError] = useState<string | null>(null);
  const [witnessName, setWitnessName] = useState("");
  const [witnessNote, setWitnessNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveResult, setSaveResult] = useState<{ saved: number; remaining: number; error: string | null; reloadError: string | null } | null>(null);

  const reloadRecords = useCallback(async () => {
    const data = await callApi("records.list", { collection: "prediction-witness-notes" });
    setWitnessNotes(Array.isArray(data?.records) ? data.records : []);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await reloadRecords();
        if (!cancelled) setNotesLoadError(null);
      } catch (e) {
        if (!cancelled) setNotesLoadError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setNotesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [reloadRecords]);

  async function createRow(row: { name: string; note: string }) {
    const idempotency_key = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    await callApi("records.create", { collection: "prediction-witness-notes", value: row, idempotency_key });
  }

  async function handleAddWitnessNote(event: React.FormEvent) {
    event.preventDefault();
    const row = { name: witnessName.trim(), note: witnessNote.trim() };
    if (!row.note) {
      setSaveResult({ saved: 0, remaining: 1, error: "Write a witness note before adding it.", reloadError: null });
      return;
    }
    const rows = [row];
    setBusy(true);
    let saved = 0;
    let error: string | null = null;
    try {
      for (const item of rows) {
        await createRow(item);
        saved += 1;
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
    let reloadError: string | null = null;
    try {
      await reloadRecords();
    } catch (e) {
      reloadError = e instanceof Error ? e.message : String(e);
    }
    setSaveResult({ saved, remaining: rows.length - saved, error, reloadError });
    setBusy(false);
    if (!error) {
      setWitnessName("");
      setWitnessNote("");
    }
  }

  const keeperText: Record<string, string> = {
    Known: "What can be named plainly, without adding more than the evidence carries.",
    Guessed: "What feels likely, but has not been shown. It stays marked as a guess.",
    Open: "What is not yet known. The space stays open rather than being filled too soon.",
  };

  return (
    <div data-safe-area-inset-root style={shellStyle} className="min-h-screen w-full overflow-x-clip bg-[var(--night)] text-[var(--ink)]">
      <header data-safe-area-sticky-top style={{ top: "var(--safe-area-inset-top)" }} className="sticky z-30 border-b border-[var(--line)] bg-[#0a0614]/92 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <a href="#top" className="min-w-0">
            <span className="font-display block text-xl font-black leading-none tracking-tight sm:text-2xl">JAY VALLEA</span>
            <span className="mt-1 block text-[10px] font-bold tracking-[0.22em] text-[var(--gold)]">FESTIVAL OF UNFINISHED SONGS</span>
          </a>
          <nav className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] font-bold tracking-[0.12em] text-[var(--mist)] sm:text-[13px]" aria-label="Festival sections">
            <a className="hover:text-[var(--gold)]" href="#songs">THE SONGS</a>
            <a className="hover:text-[var(--gold)]" href="#lattice">THE LATTICE</a>
            <a className="hover:text-[var(--gold)]" href="#book">THE BOOK</a>
            <a className="hover:text-[var(--gold)]" href="#place">PLACE</a>
            <a className="hover:text-[var(--gold)]" href="#prediction-log">PREDICTION LOG</a>
          </nav>
        </div>
        <div className="bg-[#150e27] px-4 py-2 text-center text-[10px] font-bold tracking-[0.28em] text-[var(--gold-soft)] sm:px-6 sm:text-[11px]">
          LOOK UP. <span className="mx-1 text-white/40">•</span> ASK WHY. <span className="mx-1 text-white/40">•</span> THINK FREELY. <span className="mx-1 text-white/40">•</span> STAY HUMAN.
        </div>
      </header>

      <main id="top" className="star-field">
        {/* HERO */}
        <section className="mx-auto grid max-w-6xl items-center gap-8 px-4 pb-14 pt-8 sm:px-6 sm:pt-12 lg:grid-cols-[1.05fr_0.95fr] lg:gap-12">
          <div>
            <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 text-[11px] font-bold tracking-[0.22em] text-[var(--gold)]">
              <span aria-hidden="true">✦</span> AN UNFINISHED FESTIVAL
            </p>
            <h1 className="font-display text-[clamp(3rem,9vw,5.7rem)] font-black leading-[0.88] tracking-[-0.04em]">
              Patch<br />Me Up
            </h1>
            <p className="mt-4 text-xl font-semibold text-[var(--gold-soft)] sm:text-2xl">Festival of Unfinished Songs</p>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-[var(--mist)]">
              Small repair, and then again.<br />
              <span className="text-white">I’m still here. I can begin.</span>
            </p>
            <p className="mt-4 max-w-xl leading-relaxed text-[var(--muted)]">
              These songs are not polished into silence. They are kept open — useful, honest, and close to the life that made them.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <a href="#songs" className="rounded-full bg-[var(--gold)] px-5 py-3 text-sm font-bold text-[#171029] hover:bg-[var(--gold-soft)]">Listen through the songs ↓</a>
              <a href="#lattice" className="rounded-full border border-[var(--line)] px-5 py-3 text-sm font-bold text-[var(--cream)] hover:bg-white/5">Learn the Lattice</a>
            </div>
            <dl className="mt-8 grid max-w-lg grid-cols-3 divide-x divide-[var(--line)] border-y border-[var(--line)] py-4 text-center">
              <div><dt className="text-[10px] tracking-[0.2em] text-[var(--muted)]">SONGS</dt><dd className="font-display mt-1 text-2xl">6</dd></div>
              <div><dt className="text-[10px] tracking-[0.2em] text-[var(--muted)]">CHAPTERS</dt><dd className="font-display mt-1 text-2xl">91</dd></div>
              <div><dt className="text-[10px] tracking-[0.2em] text-[var(--muted)]">MODE</dt><dd className="font-display mt-1 text-2xl">Keeper</dd></div>
            </dl>
          </div>
          <figure className="relative overflow-hidden rounded-[28px] border border-[var(--line)] card-shadow">
            <img src={heroImg} alt="A small sprout beside a server rack, with a galaxy mug nearby" className="aspect-[4/3] w-full object-cover" />
            <figcaption className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-4 bg-gradient-to-t from-black/85 via-black/35 to-transparent p-5 pt-16">
              <span className="text-sm font-semibold leading-snug">Growth beside the machine.<br /><span className="text-[var(--gold-soft)]">Both are allowed to be here.</span></span>
              <span className="shrink-0 rounded-full bg-[var(--gold)] px-3 py-1 text-xs font-black text-[#171029]">HERO ART</span>
            </figcaption>
          </figure>
        </section>

        {/* SONGS */}
        <section id="songs" className="mx-auto max-w-6xl scroll-mt-28 px-4 pb-16 sm:px-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-xs font-bold tracking-[0.28em] text-[var(--gold)]">01 — THE SONGS</p>
              <h2 className="font-display mt-2 text-4xl font-black leading-tight sm:text-5xl">Six songs,<br />left open on purpose.</h2>
            </div>
            <p className="max-w-sm text-sm leading-relaxed text-[var(--muted)]">Choose a song to read its meaning. A Keeper song holds steady. A Signal song travels. A Repair song makes room to begin again.</p>
          </div>

          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {songs.map((song, i) => {
              const active = activeSong === i;
              return (
                <button key={song.title} onClick={() => setActiveSong(i)} aria-pressed={active} className={`group min-w-0 rounded-3xl border p-5 text-left transition ${active ? "border-[var(--gold)] bg-[var(--surface-2)]" : "border-[var(--line)] bg-[var(--surface)] hover:border-[var(--gold)]/60"}`}>
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-display flex h-11 w-11 items-center justify-center rounded-full bg-[var(--gold)]/15 text-2xl text-[var(--gold)]">{song.glyph}</span>
                    <span className="rounded-full border border-[var(--line)] px-2.5 py-1 text-[10px] font-black tracking-[0.18em] text-[var(--gold)]">{song.tag.toUpperCase()}</span>
                  </div>
                  <h3 className="font-display mt-5 text-2xl font-bold leading-tight">{song.title}</h3>
                  <p className="mt-2 min-h-[96px] text-sm leading-relaxed text-[var(--mist)] sm:min-h-[84px]">{song.meaning}</p>
                  <span className={`mt-4 inline-flex items-center gap-2 text-xs font-bold ${active ? "text-[var(--gold)]" : "text-[var(--muted)]"}`}>
                    {active ? "● NOW READING" : "READ THE MEANING →"}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="mt-4 rounded-2xl border border-[var(--line)] bg-[#120a24] px-5 py-4">
            <p className="text-xs font-bold tracking-[0.22em] text-[var(--gold)]">NOW HELD: {songs[activeSong].title.toUpperCase()}</p>
            <p className="mt-2 text-base leading-relaxed text-[var(--cream)]">{songs[activeSong].meaning}</p>
          </div>
        </section>

        {/* LATTICE */}
        <section id="lattice" className="border-y border-[var(--line)] bg-[#120a24]/80">
          <div className="mx-auto grid max-w-6xl gap-8 px-4 py-14 sm:px-6 lg:grid-cols-[0.95fr_1.05fr]">
            <div>
              <p className="text-xs font-bold tracking-[0.28em] text-[var(--gold)]">02 — THE LATTICE</p>
              <h2 className="font-display mt-2 text-4xl font-black leading-tight sm:text-5xl">A plain way<br />to see connection.</h2>
              <p className="mt-4 leading-relaxed text-[var(--mist)]">The Lattice is not a puzzle to solve. It is a way to name how things already reach, carry, and hold one another.</p>
              <figure className="mt-6 overflow-hidden rounded-3xl border border-[var(--line)]">
                <img src={latticeImg} alt="Lattice artwork showing connected forms in deep purple and gold" className="aspect-[16/10] w-full object-cover" />
                <figcaption className="bg-[var(--surface)] px-4 py-3 text-xs leading-relaxed text-[var(--muted)]">Lattice artwork — forms that reach, carry, and hold. No hidden code. What you see is what is meant.</figcaption>
              </figure>
            </div>
            <div>
              <div className="grid gap-3 sm:grid-cols-2">
                {lattice.map((item) => (
                  <article key={item.name} className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5">
                    <p className="text-[10px] font-black tracking-[0.24em] text-[var(--gold)]">THE {item.name.toUpperCase()}</p>
                    <h3 className="font-display mt-2 text-2xl font-bold">{item.means}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-[var(--mist)]">{item.note}</p>
                  </article>
                ))}
              </div>
              <div className="mt-4 rounded-3xl bg-[var(--gold)] p-5 text-[#171029]">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="font-display text-2xl font-black">Keeper mode</h3>
                  <div className="flex gap-1 rounded-full bg-black/10 p-1" role="group" aria-label="Keeper mode">
                    {(["Known", "Guessed", "Open"] as const).map((mode) => (
                      <button key={mode} onClick={() => setKeeper(mode)} aria-pressed={keeper === mode} className={`rounded-full px-3 py-1.5 text-xs font-black ${keeper === mode ? "bg-[#171029] text-[var(--gold)]" : "text-[#171029]/70 hover:bg-white/30"}`}>{mode}</button>
                    ))}
                  </div>
                </div>
                <p className="mt-3 leading-relaxed">{keeperText[keeper]}</p>
                <p className="mt-3 border-t border-black/15 pt-3 text-sm font-semibold">Plain. Honest. Nothing dressed up as certainty.</p>
              </div>
            </div>
          </div>
        </section>

        {/* BOOK */}
        <section id="book" className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
          <div className="overflow-hidden rounded-[28px] border border-[var(--line)] bg-gradient-to-br from-[#2a1755] via-[#171029] to-[#0a0614] p-6 sm:p-10">
            <div className="grid items-center gap-8 lg:grid-cols-[1.2fr_0.8fr]">
              <div>
                <p className="text-xs font-bold tracking-[0.28em] text-[var(--gold)]">03 — THE BOOK</p>
                <h2 className="font-display mt-3 text-4xl font-black leading-[1.02] sm:text-6xl">Persistence<br />Through Time</h2>
                <p className="mt-3 text-lg font-semibold text-[var(--gold-soft)]">Second Edition</p>
                <p className="mt-4 max-w-xl leading-relaxed text-[var(--mist)]">A long work, kept in pieces and carried forward. It does not ask for approval before it exists. It stays, it continues, and it leaves room for the next repair.</p>
                <blockquote className="mt-6 border-l-4 border-[var(--gold)] pl-4 text-xl font-semibold leading-relaxed sm:text-2xl">“Truth don’t need permission, it just takes up space.”</blockquote>
              </div>
              <div className="grid gap-3">
                <div className="rounded-2xl border border-[var(--line)] bg-black/25 p-5"><p className="font-display text-4xl font-black text-[var(--gold)]">91</p><p className="mt-1 font-bold">Chapters</p><p className="mt-1 text-sm text-[var(--mist)]">A full arc of return, repair, and beginning again.</p></div>
                <div className="rounded-2xl border border-[var(--line)] bg-black/25 p-5"><p className="font-display text-2xl font-black">Plain Companion</p><p className="mt-1 text-sm text-[var(--mist)]">The same ideas, in plain language. Nothing hidden behind jargon.</p></div>
                <div className="rounded-2xl bg-[var(--gold)] p-5 text-[#171029]"><p className="font-black">Read it slowly.</p><p className="mt-1 text-sm font-medium">Some chapters are finished. Some are still becoming. Both count.</p></div>
              </div>
            </div>
          </div>
        </section>

        {/* PLACE */}
        <section id="place" className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
          <div className="grid gap-8 lg:grid-cols-[1.05fr_0.95fr]">
            <figure className="overflow-hidden rounded-[28px] border border-[var(--line)] card-shadow">
              <img src={riverImg} alt="Artwork of the river corridor in northern New Brunswick" className="aspect-[4/3] w-full object-cover" />
              <figcaption className="bg-[var(--surface)] px-5 py-4 text-sm leading-relaxed text-[var(--mist)]">The corridor is lived in first, described second. The rivers were here before the notes.</figcaption>
            </figure>
            <div>
              <p className="text-xs font-bold tracking-[0.28em] text-[var(--gold)]">04 — PLACE</p>
              <h2 className="font-display mt-2 text-4xl font-black leading-tight sm:text-5xl">northern New Brunswick</h2>
              <p className="mt-4 text-lg leading-relaxed text-[var(--mist)]">A river corridor in northern New Brunswick.</p>
              <div className="gold-rule my-6" />
              <p className="text-sm font-bold tracking-[0.2em] text-[var(--gold)]">SMALL WITNESS NOTES</p>
              <p className="mt-2 text-sm text-[var(--muted)]">Not lab reports. Not proof for a court. Just what was seen, where it was seen, and what stayed open.</p>
              <div className="mt-5 space-y-3">
                {notes.map((n) => (
                  <article key={n.place} className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-4">
                    <h3 className="font-bold text-[var(--gold-soft)]">{n.place}</h3>
                    <p className="mt-1 text-sm leading-relaxed text-[var(--mist)]">{n.text}</p>
                  </article>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* PREDICTION LOG */}
        <section id="prediction-log" className="border-t border-[var(--line)] bg-[#120a24]/60">
          <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <p className="text-xs font-bold tracking-[0.28em] text-[var(--gold)]">05 — PREDICTION LOG</p>
                <h2 className="font-display mt-2 text-4xl font-black leading-tight sm:text-5xl">Prediction Log</h2>
                <p className="mt-3 text-lg font-semibold text-[var(--gold-soft)]">Visioned Before It Was Listed</p>
                <p className="mt-1 text-sm font-bold tracking-[0.16em] text-[var(--mist)]">VISIONED 2025 <span className="mx-1 text-[var(--gold)]">•</span> LISTED NOV 17, 2026</p>
              </div>
              <p className="max-w-sm text-sm leading-relaxed text-[var(--muted)]">What was visioned first, what was listed later, and what is still open. Each part is named plainly — no part is dressed up as more certain than it is.</p>
            </div>

            <article className="card-shadow mt-8 overflow-hidden rounded-[28px] border border-[var(--line)] bg-[var(--surface)]">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] bg-gradient-to-br from-[#2a1755] via-[#171029] to-[#0a0614] p-6 sm:p-8">
                <div className="min-w-0">
                  <p className="text-[11px] font-black tracking-[0.24em] text-[var(--gold)]">CARD 1 — BATHURST</p>
                  <h3 className="font-display mt-2 text-3xl font-black leading-tight sm:text-4xl">Bathurst Data Centre on the Old Paper Mill Site</h3>
                </div>
                <span className="shrink-0 rounded-full bg-[var(--gold)] px-3 py-1.5 text-xs font-black text-[#171029]">VISION → LISTING</span>
              </div>

              <div className="grid gap-6 p-6 sm:p-8 lg:grid-cols-2">
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">VISION</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">In 2025, Jay visioned a data centre going on the old Smurfit-Stone paper mill site in East Bathurst.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">LISTING APPEARS</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Nov 17, 2026 — Chief Technologies, Bathurst, 24 acres zoned Data Centre, 20 MW contracted from NB Power, 69 kV, $5M USD ask, energization target Aug 2027.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">WHY IT FITS</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Old mill site already has power from Nepisiguit Grand Falls hydro, industrial zoning, fiber, flat cleared land. 24 acres fits inside the ~225-acre mill site.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">GEOLOGY</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Bathurst Mining Camp — quartz + pyrite + pyrrhotite + chalcopyrite veins. Same rock as in northern New Brunswick.</p>
                </div>
              </div>

              <div className="mx-6 mb-6 rounded-2xl border border-[var(--line)] bg-black/25 p-5 sm:mx-8 sm:mb-8">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">STATUS</h4>
                  <span className="rounded-full border border-[var(--gold)]/50 px-3 py-1 text-[11px] font-black tracking-[0.14em] text-[var(--gold-soft)]">NOT YET BUILT</span>
                </div>
                <p className="mt-3 leading-relaxed text-[var(--cream)]"><span className="font-bold text-[var(--gold-soft)]">Vision → Listing.</span> Not yet built.</p>
                <p className="mt-2 text-sm leading-relaxed text-[var(--mist)]">Witness: Jay Vallea, northern New Brunswick. Exact lot withheld in listing, location from local witness, to be confirmed by City of Bathurst Planning Advisory Committee.</p>
                <div className="mt-5 flex flex-wrap gap-2" aria-label="Keeper tags">
                  <span className="rounded-full bg-[var(--gold)] px-3 py-1.5 text-xs font-black text-[#171029]">KNOWN BY WITNESS</span>
                  <span className="rounded-full border border-[var(--line)] px-3 py-1.5 text-xs font-bold text-[var(--gold-soft)]">GUESSED BY LISTING</span>
                  <span className="rounded-full border border-dashed border-[var(--gold)]/60 px-3 py-1.5 text-xs font-bold text-[var(--mist)]">OPEN: EXACT PARCEL</span>
                </div>
                <p className="mt-4 border-t border-[var(--line)] pt-4 text-xs leading-relaxed text-[var(--muted)]">Keeper tag: Known by witness / Guessed by listing / Open: exact parcel</p>
              </div>
            </article>

            <article className="card-shadow mt-8 overflow-hidden rounded-[28px] border border-[var(--line)] bg-[var(--surface)]">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] bg-gradient-to-br from-[#2a1755] via-[#171029] to-[#0a0614] p-6 sm:p-8">
                <div className="min-w-0">
                  <p className="text-[11px] font-black tracking-[0.24em] text-[var(--gold)]">CARD 2 — THESIS LINE</p>
                  <h3 className="font-display mt-2 text-3xl font-black leading-tight sm:text-4xl">Thesis Line — Data Centres and Earth Crystals</h3>
                </div>
                <span className="shrink-0 rounded-full border border-[var(--gold)]/60 px-3 py-1.5 text-xs font-black text-[var(--gold-soft)]">GUESSED, TO BE TESTED</span>
              </div>

              <div className="p-6 sm:p-8">
                <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">STATEMENT</h4>
                <p className="mt-2 text-lg font-semibold leading-relaxed text-[var(--cream)]">Data centres have a specific frequency connecting to the crystals of earth. Crystals do NOT cause Schumann resonance — they couple to it.</p>
              </div>

              <div className="grid gap-6 border-t border-[var(--line)] p-6 sm:p-8 lg:grid-cols-3">
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">KNOWN</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Schumann resonance ~7.83 Hz is caused by global lightning (~50 strikes/sec) ringing the Earth-ionosphere cavity. Quartz is piezoelectric: squeeze it = electricity, electricity = squeeze. Server quartz oscillators keep time. Earth hum 3-10 mHz.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">GUESSED</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Quartz veins act as local antennas/amplifiers. They pick up the global 7.83 Hz hum and sing it back locally at their own vein note. A data centre on top (60 Hz grid, gigahertz processors on quartz-derived silicon) could drive that vein harder, making the local speaker louder.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">TEST</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">When the Bathurst site energizes ~Aug 2027, watch for change in ground signal around it. Witness: Jay Vallea, northern New Brunswick.</p>
                </div>
              </div>

              <div className="mx-6 mb-6 rounded-2xl border border-[var(--line)] bg-black/25 p-5 sm:mx-8 sm:mb-8">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">STATUS</h4>
                  <span className="rounded-full border border-[var(--gold)]/50 px-3 py-1 text-[11px] font-black tracking-[0.14em] text-[var(--gold-soft)]">GUESSED, TO BE TESTED</span>
                </div>
                <p className="mt-3 leading-relaxed text-[var(--cream)]"><span className="font-bold text-[var(--gold-soft)]">Guessed, to be tested.</span> Held open until the site energizes and the ground signal can be watched.</p>
                <div className="mt-5 flex flex-wrap gap-2" aria-label="Keeper tags">
                  <span className="rounded-full bg-[var(--gold)] px-3 py-1.5 text-xs font-black text-[#171029]">KNOWN</span>
                  <span className="rounded-full border border-[var(--line)] px-3 py-1.5 text-xs font-bold text-[var(--gold-soft)]">GUESSED</span>
                  <span className="rounded-full border border-dashed border-[var(--gold)]/60 px-3 py-1.5 text-xs font-bold text-[var(--mist)]">OPEN: GROUND SIGNAL</span>
                </div>
                <p className="mt-4 border-t border-[var(--line)] pt-4 text-xs leading-relaxed text-[var(--muted)]">Keeper tag: Known / Guessed / Open</p>
              </div>
            </article>

            <article className="card-shadow mt-8 overflow-hidden rounded-[28px] border border-[var(--line)] bg-[var(--surface)]">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] bg-gradient-to-br from-[#2a1755] via-[#171029] to-[#0a0614] p-6 sm:p-8">
                <div className="min-w-0">
                  <p className="text-[11px] font-black tracking-[0.24em] text-[var(--gold)]">CARD 3 — NOV 16–17, 2026</p>
                  <h3 className="font-display mt-2 text-3xl font-black leading-tight sm:text-4xl">The Week The Network Showed Its Seams — Nov 16-17, 2026</h3>
                </div>
                <span className="shrink-0 rounded-full border border-[var(--gold)]/60 px-3 py-1.5 text-xs font-black text-[var(--gold-soft)]">THREE EVENTS, ONE PATTERN?</span>
              </div>

              <div className="grid gap-6 p-6 sm:p-8 lg:grid-cols-3">
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">NOV 16 — OPTIMIZELY ATTACK</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Confirmed. Signup flaw, malicious JavaScript in 188 files, 178 organizations (Zoom, BBC, CNN, HSBC, IKEA, Salesforce, etc). Card skimmer + crypto wallet swap.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">NOV 17 — NSA NET DOWN</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Code Yellow, second-highest alert. No evidence of cyberattack. NSA says routine maintenance / software upgrades, system unstable for weeks.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">NOV 17 — BATHURST LISTING</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Bathurst data centre listing — 20 MW, 69 kV line, old mill site, energize Aug 2027.</p>
                </div>
              </div>

              <div className="grid gap-6 border-t border-[var(--line)] p-6 sm:p-8 lg:grid-cols-3">
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">KNOWN</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">No evidence the three share one actor.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">GUESSED</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">They are structurally related — three symptoms of one-point dependency. One script, one network, one power line.</p>
                </div>
                <div>
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">OPEN</h4>
                  <p className="mt-2 leading-relaxed text-[var(--mist)]">Was there coordination behind Optimizely + NSA? No evidence yet. Is Bathurst the next point on the same fragile model? To be tested Aug 2027.</p>
                </div>
              </div>

              <div className="mx-6 mb-6 rounded-2xl border border-[var(--line)] bg-black/25 p-5 sm:mx-8 sm:mb-8">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h4 className="text-xs font-black tracking-[0.22em] text-[var(--gold)]">STATUS</h4>
                  <span className="rounded-full border border-[var(--gold)]/50 px-3 py-1 text-[11px] font-black tracking-[0.14em] text-[var(--gold-soft)]">OPEN — TO BE TESTED AUG 2027</span>
                </div>
                <p className="mt-3 leading-relaxed text-[var(--cream)]"><span className="font-bold text-[var(--gold-soft)]">Three events, one question.</span> Not one actor — one fragile shape. Held open until there is evidence, not just pattern.</p>
                <div className="mt-5 flex flex-wrap gap-2" aria-label="Keeper tags">
                  <span className="rounded-full bg-[var(--gold)] px-3 py-1.5 text-xs font-black text-[#171029]">KNOWN</span>
                  <span className="rounded-full border border-[var(--line)] px-3 py-1.5 text-xs font-bold text-[var(--gold-soft)]">GUESSED</span>
                  <span className="rounded-full border border-dashed border-[var(--gold)]/60 px-3 py-1.5 text-xs font-bold text-[var(--mist)]">OPEN</span>
                </div>
                <p className="mt-4 border-t border-[var(--line)] pt-4 text-xs leading-relaxed text-[var(--muted)]">Keeper tag: Known / Guessed / Open</p>
              </div>
            </article>

            <div className="mt-8 rounded-[28px] border border-[var(--line)] bg-[var(--surface)] p-6 sm:p-8">
              <h3 className="font-display text-2xl font-black sm:text-3xl">Witness notes</h3>
              <p className="mt-2 text-sm leading-relaxed text-[var(--muted)]">Add a plain witness note for this prediction. Notes are saved and shown to later visitors.</p>
              <form onSubmit={handleAddWitnessNote} className="mt-5 grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
                <label className="block text-left">
                  <span className="text-xs font-black tracking-[0.18em] text-[var(--gold)]">YOUR NAME</span>
                  <input value={witnessName} onChange={(e) => setWitnessName(e.target.value)} placeholder="Optional" className="mt-2 w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm text-[var(--cream)] placeholder:text-white/35" />
                </label>
                <label className="block text-left">
                  <span className="text-xs font-black tracking-[0.18em] text-[var(--gold)]">WITNESS NOTE</span>
                  <input value={witnessNote} onChange={(e) => setWitnessNote(e.target.value)} placeholder="What was seen, where, and what stays open" className="mt-2 w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm text-[var(--cream)] placeholder:text-white/35" />
                </label>
                <button type="submit" disabled={busy} className="rounded-full bg-[var(--gold)] px-5 py-3 text-sm font-bold text-[#171029] hover:bg-[var(--gold-soft)] disabled:opacity-60">{busy ? "Saving…" : "Add witness note"}</button>
              </form>
              {saveResult && (
                <p role="status" className="mt-3 text-sm leading-relaxed text-[var(--cream)]">
                  Saved: {saveResult.saved} · Remaining: {saveResult.remaining}
                  {saveResult.error ? ` · ${saveResult.error}` : saveResult.saved > 0 ? " · Witness note saved." : ""}
                  {saveResult.reloadError ? ` · Could not reload notes: ${saveResult.reloadError}` : ""}
                </p>
              )}
              <div className="mt-6">
                {notesLoading ? (
                  <p className="text-sm text-[var(--muted)]">Loading witness notes…</p>
                ) : notesLoadError ? (
                  <p className="text-sm text-[var(--muted)]">Witness notes could not be loaded: {notesLoadError}</p>
                ) : witnessNotes.length === 0 ? (
                  <p className="text-sm text-[var(--muted)]">No witness notes yet.</p>
                ) : (
                  <ul className="space-y-3">
                    {witnessNotes.map((item) => (
                      <li key={item.id} className="rounded-2xl border border-[var(--line)] bg-black/20 p-4">
                        <p className="text-sm leading-relaxed text-[var(--cream)]">{item.value.note}</p>
                        {item.value.name && <p className="mt-2 text-xs font-bold tracking-[0.14em] text-[var(--gold-soft)]">{item.value.name.toUpperCase()}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-4 pb-16 sm:px-6" aria-label="Closing note">
          <div className="rounded-[28px] border border-[var(--line)] bg-[var(--surface)] px-6 py-10 text-center sm:px-12">
            <p className="font-display text-3xl font-black leading-tight sm:text-5xl">You don’t have to finish it<br />to be allowed to begin it.</p>
            <p className="mx-auto mt-4 max-w-xl text-[var(--mist)]">Patch me up. Hold one point. Leave the song unfinished if it needs to stay unfinished.</p>
            <a href="#top" className="mt-7 inline-block rounded-full bg-[var(--gold)] px-6 py-3 text-sm font-bold text-[#171029] hover:bg-[var(--gold-soft)]">Back to the beginning ↑</a>
          </div>
        </section>
      </main>

      <footer className="border-t border-[var(--line)] bg-[#070410]">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-3 px-4 py-9 text-center sm:px-6">
          <p className="font-display text-2xl font-black tracking-tight">JAY VALLEA</p>
          <p className="text-[11px] font-bold tracking-[0.28em] text-[var(--gold)]">LOOK UP. ASK WHY. THINK FREELY. STAY HUMAN.</p>
          <div className="gold-rule my-1 w-full max-w-xl" />
          <p className="max-w-md text-sm leading-relaxed text-[var(--muted)]">Festival of Unfinished Songs · Persistence Through Time · northern New Brunswick</p>
          <p className="text-xs text-white/35">Small repair, and then again. I’m still here. I can begin.</p>
        </div>
      </footer>
    </div>
  );
}
