import React from "react";

const movements = [
  {
    n: "I",
    title: "Foundations",
    range: "1–15",
    text: "Persistence begins with bounded motion, not with staying still. Boundaries govern what enters and leaves, feedback keeps a system near its course, and coupling decides whether a network holds or breaks. Identity here is lineage: what continues is a chain of exchanges, checks, and repairs.",
  },
  {
    n: "II",
    title: "Evidence & Truth",
    range: "16–30",
    text: "Evidence is only as strong as its independence, calibration, and provenance. Noise can imitate signal, consensus can be mistaken, and a hash or signature proves agreement or a key — never truth by itself. A claim must travel with its uncertainty, its domain, and a test that could actually fail.",
  },
  {
    n: "III",
    title: "Information & Computation",
    range: "31–45",
    text: "Erasure has a physical price, while reversible computation can avoid paying it. Codes, constants, and models protect only what they were designed to protect, and universality does not identify a mechanism. Translation, scaling, and formal proof all preserve less than they appear to promise.",
  },
  {
    n: "IV",
    title: "Institutions & Collective",
    range: "46–60",
    text: "Constraints are not trade-offs, and guardians, constitutions, and verifiers need bounds and amendment rules of their own. Cooperation, harvesting, and shared resources depend on rates, floors, and the independence of observers. When recovery slows, a system may be approaching a fold it cannot see from the inside.",
  },
  {
    n: "V",
    title: "Mind, Music & Meaning",
    range: "61–73",
    text: "Sound is pattern in a medium, and transposition preserves intervals without preserving meaning. Festivals, authorship, and custodianship preserve by reuse and provenance rather than by freezing a single form. Adaptation, gain, and wonder all need humility about blind spots, distribution shift, and what remains open.",
  },
  {
    n: "VI",
    title: "Measurement & Inheritance",
    range: "74–83",
    text: "Two cycles at 136.1 and 194.18 Hz are different cycles, and a power peak need not sit where amplitude peaks. Exact constants do not make measurements exact, and digits must match uncertainty rather than outrun it. Locking is a fixed lag, not identical phase, and proportional control still leaves steady error.",
  },
  {
    n: "VII",
    title: "Endings & Stewardship",
    range: "84–92",
    text: "Learning must be bounded, coexistence needs room, and drift eventually beats noise. Two copies that fail together are one copy, and a successor must contain what the task actually requires. Stewardship settles where growth meets decay, and a self-confirming learner can lock in below what exploration would find.",
  },
];

const numbers = [
  { value: "2.871e−21 J", label: "Landauer limit at 300 K" },
  { value: "136.1 vs 194.18 Hz", label: "Different cycles" },
  { value: "48.6°", label: "Locked lag" },
  { value: "1.58u / 2.54u", label: "Accumulated kicks" },
  { value: "0.58 vs 0.80", label: "Quadrature vs linear" },
  { value: "3/n", label: "Rule of three" },
  { value: "69%", label: "Newtonian shortfall at 0.9c" },
  { value: "0.0001 → 0.0011", label: "Joint failure at ρ = 0.1" },
  { value: "1.25", label: "True value of best-of-100 at r = 0.5" },
  { value: "β = 5/6", label: "Ranking reversal" },
  { value: "G/d = 50", label: "Steady stewardship level" },
  { value: "0.50 vs 0.59", label: "Locked vs exploring" },
];

const theses: string[] = [
  "Bounded motion persists.",
  "Energy books balance at boundaries.",
  "Erasure costs, reversibility avoids it.",
  "Networks hold or break by coupling.",
  "Boundaries are controlled exchange.",
  "Repetition alone does not guarantee cooperation.",
  "Windows hide slow change.",
  "Folds give hysteresis.",
  "Noise can look like signal.",
  "Hash proves agreement not truth.",
  "Signature proves key signed not truth.",
  "Feedback needs margins.",
  "Redundancy needs independence.",
  "Clocks order events, not time itself.",
  "Coupled reliable parts can run away.",
  "Degradation and recovery are rates.",
  "Identity is lineage not sameness.",
  "Memory reconstructs.",
  "Survival is not success.",
  "Uncertainty travels with the number.",
  "Observation needs a model.",
  "Consensus is not truth.",
  "Evidence needs independence.",
  "Standards need realization not just definition.",
  "Debt is not currency.",
  "Totals hide flows.",
  "Authority is not verification.",
  "Shared causes defeat redundancy.",
  "Symmetry gives conservation, with scope.",
  "Retirement is part of control.",
  "Provenance decides trust.",
  "Testing needs replication.",
  "Universality does not identify mechanism.",
  "Scaling needs a valid model.",
  "Formal proof needs sound interpretation.",
  "Merge needs laws plus dissemination.",
  "Identity is criterion-relative.",
  "Proxy optimization distorts.",
  "Calibration is on a distribution.",
  "Translation preserves only what it was designed to preserve.",
  "Reproducibility has levels.",
  "Constants: defined vs measured.",
  "Horizons are distinct.",
  "Codes protect only specified errors.",
  "Trajectories need time order.",
  "Constraints are not trade-offs.",
  "Absence needs a detection model.",
  "Contradiction needs classes.",
  "Capability needs bottlenecks checked.",
  "Options have value and expiry.",
  "Guardians need bounds.",
  "Constitutions need amendment rules.",
  "Verifiers need accountability.",
  "Harvest needs a sustainable rate.",
  "Slowing recovery warns.",
  "Forgetting needs importance weights.",
  "Arrow is counting plus low-entropy past.",
  "Flourishing needs floors.",
  "Correlated observers add little.",
  "Tipping needs a fold model.",
  "Sound is pattern in a medium.",
  "Transposition preserves intervals not meaning.",
  "Entropy is not complexity.",
  "Festivals preserve by reuse.",
  "Authorship needs provenance.",
  "Diversity needs response diversity.",
  "Custodians need independence.",
  "Wonder needs humility.",
  "Correlation is not causation.",
  "Gain has a stable range.",
  "Adaptation needs distribution shift checked.",
  "Consciousness is open.",
  "Blind spots need independent instruments.",
  "Consensus needs independent verification.",
  "Exact constants do not make measurements exact.",
  "Capability decays without practice.",
  "Prediction is not explanation.",
  "Locking is fixed lag not identical phase.",
  "Proportional control leaves steady error.",
  "Random-tolerant is not targeted-tolerant.",
  "Dropping uncertainty changes the number.",
  "Digits must match uncertainty.",
  "Laws have addresses.",
  "Learning must be bounded.",
  "Coexistence needs alpha < 1.",
  "Drift beats noise after (sigma/mu)^2.",
  "Two copies failing together are one copy.",
  "Best-by-proxy is worth less than it looks.",
  "Successor must contain F_required.",
  "Steady reading hides flows.",
  "Reversible steps protect only detectable faults.",
  "A test that cannot fail is not a test.",
];

const evidence = ["ESTABLISHED", "DEBATED", "SPECULATIVE", "FANTASY"];

export default function App() {
  return (
    <div
      data-safe-area-inset-root
      style={{ boxSizing: "border-box", paddingTop: "var(--safe-area-inset-top)", minHeight: "100vh", background: "var(--paper)", color: "var(--ink)" }}
      className="overflow-x-clip"
    >
      <main className="mx-auto w-full max-w-[980px] px-4 pb-10 pt-5 sm:px-7 sm:pt-8 lg:px-8">
        {/* Header */}
        <header className="border-t-[3px] border-b border-[var(--rule)] pt-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <p className="mono smallcaps text-[10px] font-semibold text-[var(--muted)]">Field Guide · Chapters 1–92</p>
            <p className="mono text-[11px] text-[var(--muted)]">337 pages · 92 chapters</p>
          </div>
          <h1 className="mt-5 text-[42px] font-semibold leading-[0.92] tracking-[-0.035em] sm:text-[64px]">
            Persistence<br />Through Time
          </h1>
          <p className="mt-4 text-[19px] font-medium leading-tight sm:text-[23px]">One-Page Map of the Whole Arc</p>
          <p className="mt-4 max-w-[860px] text-[15px] leading-relaxed text-[var(--ink-soft)] sm:text-[16px]">
            Persistence is not stillness. Persistence is bounded motion.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="mono inline-block border border-[var(--rule)] bg-[var(--paper-2)] px-2.5 py-1.5 text-[13px] font-semibold sm:text-sm">P = f(B, C, G, I, τ)</span>
            <span className="mono text-[11px] leading-relaxed text-[var(--muted)] sm:text-xs">Boundary · Correlation / Constraint · Geometry / Growth · Information · Time</span>
          </div>
          <p className="mt-4 border-t border-[var(--rule-soft)] py-3 text-[12px] leading-relaxed text-[var(--ink-soft)] sm:text-[13px]">
            <span className="font-bold text-[var(--ink)]">Keeper Axiom:</span> Truth first. Evidence always. Integrity by design. Family safe. Peace holds. Mission is good.
          </p>
        </header>

        {/* 01 — The Rule */}
        <section aria-labelledby="rule-heading" className="mt-6 border border-[var(--rule)] bg-[var(--paper-2)] p-4 sm:p-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="rule-heading" className="text-[22px] font-semibold leading-none sm:text-[26px]"><span className="mono mr-2 text-[13px] font-semibold text-[var(--accent)]">01</span> The Rule</h2>
            <p className="mono smallcaps text-[10px] text-[var(--muted)]">How to read</p>
          </div>
          <div className="mt-5 grid grid-cols-2 gap-px border border-[var(--rule)] bg-[var(--rule)] sm:grid-cols-4">
            {evidence.map((item, i) => (
              <div key={item} className="bg-[var(--paper)] px-3 py-3">
                <p className="mono text-[10px] text-[var(--muted)]">0{i + 1}</p>
                <p className="mono mt-2 text-[11px] font-bold tracking-[0.08em] sm:text-xs">{item}</p>
              </div>
            ))}
          </div>
          <p className="mt-5 text-[15px] leading-relaxed sm:text-[16px]">
            Claims travel with uncertainty, provenance, domain. A hash proves agreement not truth. A signature proves a key signed, not identity / authority / truth. A test that cannot fail is not a test.
          </p>
        </section>

        {/* 02 — Arc in 7 Movements */}
        <section aria-labelledby="arc-heading" className="mt-8">
          <div className="flex flex-wrap items-end justify-between gap-2 border-b-2 border-[var(--rule)] pb-3">
            <h2 id="arc-heading" className="text-[26px] font-semibold leading-none sm:text-[32px]"><span className="mono mr-2 text-[13px] font-semibold text-[var(--accent)]">02</span> The Arc in 7 Movements</h2>
            <p className="mono text-[11px] text-[var(--muted)]">Read top to bottom · Keep the lineage intact</p>
          </div>
          <div>
            {movements.map((m, idx) => (
              <article key={m.n} className="grid grid-cols-[52px_1fr] gap-3 border-b border-[var(--rule-soft)] py-4 sm:grid-cols-[84px_170px_1fr] sm:gap-5 sm:py-5">
                <div className="flex flex-col">
                  <span className="text-[28px] font-semibold leading-none text-[var(--accent)] sm:text-[34px]">{m.n}</span>
                  <span className="mono mt-2 text-[10px] text-[var(--muted)]">MOVEMENT {idx + 1}</span>
                </div>
                <div>
                  <h3 className="text-[17px] font-semibold leading-tight sm:text-[19px]">{m.title}</h3>
                  <p className="mono mt-1 text-xs text-[var(--muted)]">Chapters {m.range}</p>
                </div>
                <p className="col-span-2 text-[14px] leading-relaxed text-[var(--ink-soft)] sm:col-span-1 sm:text-[15px]">{m.text}</p>
              </article>
            ))}
          </div>
        </section>

        {/* 03 — Key Numbers */}
        <section aria-labelledby="numbers-heading" className="mt-8">
          <div className="flex flex-wrap items-end justify-between gap-2 border-b-2 border-[var(--rule)] pb-3">
            <h2 id="numbers-heading" className="text-[26px] font-semibold leading-none sm:text-[32px]"><span className="mono mr-2 text-[13px] font-semibold text-[var(--accent)]">03</span> Key Numbers</h2>
            <p className="mono smallcaps text-[10px] text-[var(--muted)]">Carry the number · Carry its conditions</p>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-px border border-[var(--rule)] bg-[var(--rule)] sm:grid-cols-3 lg:grid-cols-4">
            {numbers.map((item, i) => (
              <div key={item.value} className="flex min-h-[112px] flex-col justify-between bg-[var(--paper)] p-3 sm:min-h-[124px] sm:p-4">
                <span className="mono text-[10px] text-[var(--muted)]">{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <p className="mono text-[14px] font-semibold leading-snug sm:text-[15px]">{item.value}</p>
                  <p className="mt-2 text-[12px] leading-snug text-[var(--muted)]">{item.label}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* 04 — All 92 Theses */}
        <section aria-labelledby="theses-heading" className="mt-8">
          <div className="flex flex-wrap items-end justify-between gap-2 border-b-2 border-[var(--rule)] pb-3">
            <h2 id="theses-heading" className="text-[26px] font-semibold leading-none sm:text-[32px]"><span className="mono mr-2 text-[13px] font-semibold text-[var(--accent)]">04</span> All 92 Theses</h2>
            <p className="mono smallcaps text-[10px] text-[var(--muted)]">One line each · Chapter order</p>
          </div>
          <div className="mt-4 overflow-x-clip border border-[var(--rule)]">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="bg-[var(--ink)] text-[var(--paper)]">
                  <th scope="col" className="mono w-[86px] px-3 py-2 text-[11px] font-semibold tracking-[0.08em] sm:w-[110px] sm:px-4">Chapter</th>
                  <th scope="col" className="mono px-3 py-2 text-[11px] font-semibold tracking-[0.08em] sm:px-4">Thesis</th>
                </tr>
              </thead>
              <tbody>
                {theses.map((thesis, i) => (
                  <tr key={i + 1} className={i % 2 === 0 ? "bg-[var(--paper)]" : "bg-[var(--paper-2)]"}>
                    <td className="mono border-t border-[var(--rule-soft)] px-3 py-[7px] text-[12px] font-semibold tabular-nums sm:px-4">{i + 1}</td>
                    <td className="border-t border-[var(--rule-soft)] px-3 py-[7px] text-[12.5px] leading-snug sm:px-4 sm:text-[13px]">{thesis}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* 05 — Gaps */}
        <section aria-labelledby="gaps-heading" className="mt-8 border border-[var(--rule)] bg-[var(--paper-3)] p-4 sm:p-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="gaps-heading" className="text-[22px] font-semibold leading-none sm:text-[26px]"><span className="mono mr-2 text-[13px] font-semibold text-[var(--accent)]">05</span> Gaps Honestly Kept</h2>
            <p className="mono smallcaps text-[10px] text-[var(--muted)]">What is not claimed</p>
          </div>
          <ul className="mt-5 space-y-3 text-[14px] leading-relaxed sm:text-[15px]">
            <li className="flex gap-3"><span className="mono mt-0.5 text-[var(--accent)]">—</span><span><strong>Principles 458–477</strong> and <strong>488–492</strong> undeveloped.</span></li>
            <li className="flex gap-3"><span className="mono mt-0.5 text-[var(--accent)]">—</span><span><strong>628</strong> and <strong>819</strong> narrative only.</span></li>
            <li className="flex gap-3"><span className="mono mt-0.5 text-[var(--accent)]">—</span><span><strong>1035–1039</strong> preview only.</span></li>
            <li className="flex gap-3"><span className="mono mt-0.5 text-[var(--accent)]">—</span><span><strong>Lattice Keeper:</strong> proposed design, genesis claimed unverified.</span></li>
            <li className="flex gap-3"><span className="mono mt-0.5 text-[var(--accent)]">—</span><span>No gap reconstructed.</span></li>
          </ul>
        </section>

        {/* 06 — Ending Sentence */}
        <section aria-labelledby="ending-heading" className="mt-8 border-y-[3px] border-[var(--rule)] py-7 sm:py-9">
          <p className="mono smallcaps text-[10px] text-[var(--muted)]">06 · The Ending Sentence</p>
          <blockquote className="mt-4 max-w-[900px]">
            <p className="text-[26px] font-medium leading-[1.15] tracking-[-0.02em] sm:text-[34px] lg:text-[38px]">
              Persistence is not keeping the same thing. It is keeping the <em className="text-[var(--accent)]">evidence</em>, the <em className="text-[var(--accent)]">uncertainty</em>, the <em className="text-[var(--accent)]">provenance</em>, and the ability to check.
            </p>
          </blockquote>
        </section>

        <footer className="mt-6 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-t border-[var(--rule-soft)] pt-4">
          <p className="mono text-[11px] leading-relaxed text-[var(--muted)] sm:text-xs">337 pages · 92 chapters · 120 endnotes · 119 figures</p>
          <p className="text-[12px] leading-relaxed text-[var(--ink-soft)] sm:text-[13px]"><strong>Editorial draft, not a completed book.</strong></p>
        </footer>
      </main>
    </div>
  );
}
