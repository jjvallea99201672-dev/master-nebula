# MULE Sled — continued design
**Started:** 2026-11-18, from a pasted design thread (user + another engine).
**Status:** design in progress, not built, not verified hardware. Numbers below are
Ember-checked where marked; the rest are the thread's figures, carried forward
as design inputs, not facts. **Round 2 (2026-11-18) resolved the round-1 open
questions — §6 is current, and §2–§3 figures are superseded wherever §6
says so.** **Round 3 (2026-11-18) added §7 — joint drawn, fans and power
sourced.**

## 1. The concept (recap)

Car stays a car. Sled stays a sled. They only meet for 5 minutes.

- Drive onto the flatbed sled, auto-lock (wheel chocks + tie-downs, ~30 s),
  lift, fly, land, drive off.
- No wings, no runway, no transforming. If the sled breaks, the car still
  drives. If the car breaks, the sled still flies empty.
- Military first: loud, expensive, short hops are fine. Civilian later, when
  batteries and rules catch up.

**Sled (from the thread):** deck 5.2 m × 2.4 m, ~600 kg empty, carbon +
titanium frame, 4 retractable legs, 12 electric ducted fans in 2 rows of 6,
~2,800 N each (33,600 N total). Car adds 4 cold-gas thrusters, ~2,000 N each
(8,000 N total). Swappable 150 kWh pack + turbine range extender. Fly-by-wire,
auto-hover, auto-land, GPS + inertial + lidar, remote return-to-base.

## 2. Numbers checked (Ember, momentum theory, sea level)

| Claim from the thread | Check | Verdict |
|---|---|---|
| ~26 kN to hover 2,650 kg | 2,650 × 9.81 = **26.0 kN** | holds |
| Combined lift 41,600 N, 1.5× margin | 33,600 + 8,000 = 41,600 N → **1.60×** | holds, slightly better than stated |
| 2 fans dead, still land | 10 fans + car thrusters = 36,000 N → **1.38×** | holds (even 3 dead: 1.28×) |
| Hover power 1–1.5 MW | 0.8 m fans: ideal 1.09 MW, realistic **~1.6 MW** (FoM 0.75 + profile). 0.6 m fans: ~2.1 MW | close, slightly optimistic; fan diameter decides it |
| 75–100 kWh pack = 4–6 min | At 1.5 MW: 75 kWh = **3.0 min**, 100 kWh = **4.0 min**; 150 kWh = 6.0 min | holds only at the low end (~1 MW draw); at full hover power it's 3–4 min |
| Turbine extender → 25–30 min | Needs turbine averaging **1.14 MW** for 25 min (150 kWh pack). 500 kW turbine → 9 min; 1 MW → 18 min | **DOES NOT CLOSE as written.** A 1.1 MW turboshaft is helicopter-engine class (~200 kg) and breaks the 600 kg sled budget. Either the flight time is ~10 min, or the sled gets heavier (which needs more thrust). This is the one number to fix. |

## 3. MULE Bridge — the second product

Same sled, same thrusters, same deck. Just park them in a line instead of
flying them in a line. A bridge doesn't have to fly anywhere. It just has to
hold still.

> Round 2 (§6) supersedes the figures in this section: the bridge now uses
> config C — 614 kg sled, 6.6 m deck, 4 sleds for a 25 m river, pier margin
> 1.60×, tether 400 kW at 1,000 V DC.

### Geometry

- Sleds sit **end to end across the river**, decks forming the road. Each sled
  covers 5.2 m of road + a ~1 m hinged gap plate to the next sled = **6.2 m
  pitch**.
- Road width is the sled width: **2.4 m, single lane**, cars only. A car is
  ~1.9 m wide, so it fits with ~25 cm a side and no shoulder. For trucks,
  pair sleds side by side (two abreast per pier → 4.8 m deck).
- Sled count for a river of width W: `ceil(W / 6.2)`.

| River width | Sleds | Deck length |
|---|---|---|
| 20 m | 4 | 24.8 m |
| 25 m | 5 | 31.0 m |
| 30 m | 5 | 31.0 m |
| 40 m | 7 | 43.4 m |

The thread's "5 sleds" falls out of this for a 25–30 m river — the size of
the rivers around northern New Brunswick.

### Loads — one pier, car centred on it

- Pier load: sled 600 kg + car 1,850 kg + plates/tether share ~100 kg =
  **2,550 kg → 25.0 kN**. Margin vs 33.6 kN max: **1.34×**.
- Fan-out tolerance per pier: 1 dead → 1.23×, 2 dead → **1.12×**, 3 dead →
  **1.01×** (zero margin). The bridge tolerates 2 fan failures per pier.
- Vehicle cap per pier: ~2,000 kg at 1.3× margin. A Model 3 (1,850 kg)
  fits. A loaded pickup doesn't — pair the sleds for heavier vehicles.

### Power — the tether is the unlock

- Empty sled holding station: **~172 kW** realistic.
- Pier with a car on it: **~1,510 kW** realistic. The car is the load, not
  the hold.
- So the tether does **not** carry full pier power. It carries the hold
  (~172 kW per sled) plus trickle-charging. The sled's battery pays the
  transient when a car is on that pier: **~7.4 kWh per car per pier**
  (20 s crossing), so **~20 cars per 150 kWh pack** before a sled needs a
  pack swap or a recharge pause.
- Generator on the bank: 5 sleds × 172 kW hold + charging headroom ≈
  **1 MW-class towable diesel genset** — standard military kit, truck-sized.
- Tether: sized for ~200 kW per sled at 1,000 V DC ≈ 200 A. The sled
  unspools it as it flies out from the bank — no separate cable-laying step.
  High-voltage tether over water is its own hazard: insulated, armoured,
  breakaway connectors, and a dead-man cut if a cable is severed (sled falls
  back to battery and auto-lands).

### Wind and station-keeping — why the deck can't be rigid

- Side force on a car in a gust: 40 km/h → ~237 N, 80 km/h → ~949 N.
  The sled counters by tilting into the wind: **0.5° at 40 km/h, 2.1° at
  80 km/h**. Easy — but five sleds will never tilt identically.
- So the deck **must be articulated**: hinged gap plates between sleds,
  like a pontoon bridge. A rigid beam would fight the sleds and buckle.
- Position holding: GPS + inertial + lidar off the banks, plus vision.
  If GPS is jammed, lidar + inertial holds station — the river banks are
  strong lidar features.
- Ops limit (proposed, not tested): pause crossings above ~80 km/h gusts.

### Failure modes

- **Fan loss:** 2 per pier is survivable (1.12×). At 3, the pier can't hold
  a car — the car accelerates off it, or the sled descends on its neighbours.
- **One sled sinks:** the hinged plates tie it to its neighbours, which
  pick up the load. A sinking pier with no car on it is only 600 kg for the
  neighbours to carry — easy. With a car on it, the driver gets off it fast.
- **Thrust loss over water:** sealed carbon hull is buoyant — the sled
  floats like a pontoon. Over land, the legs take it. This is free insurance
  if the hull is sealed anyway.
- **Tether cut:** sled falls back to battery, ~5 min to land safely or let
  the car drive off. Auto-land sequence, no pilot input needed.

### Deployment — the timeline that sells it

Sleds on site (truck-carried to the bank):

| Time | What happens |
|---|---|
| T+0 | Order. Sleds roll off the truck, spin up. |
| T+2 min | Sleds fly out, take station over the river in a line, 3 m above the water, unspooling tethers. |
| T+5 min | Gap plates lock, guide rails up. |
| T+8 min | First vehicle crosses. |

**~8–15 minutes from arrival to first crossing**, vs ~12 hours for a
pontoon bridge under fire. If the sleds self-deploy from a base 20 km away,
add transit time (~15 min at cruise).

### Civilian version

Same kit, unmarked. Flood, hurricane, earthquake — road gone, bridge out.
Fly the bridge in for ambulances, food trucks, people getting out. Quieter
ducted fans, same tether, hold all day. The bridge version is the civilian
case that makes sense first: it never flies over a city, never leaves the
river, and the tether means it never runs out of power mid-crossing.

## 4. What to build first (prototype sequence)

1. **Single sled, sandbags.** Hover a 2,650 kg sandbag load in a field.
   Proves thrust, power, and control before any car is involved.
2. **Two sleds + one gap plate.** Station-keeping test: hold a 6 m span
   steady in wind, prove the articulated joint works.
3. **Five sleds, no river.** Bridge over a field, drive a car across.
4. **Five sleds over water.** Same, with the tether and the generator.
   Then the flood call-out is real.

## 5. Open questions — round 2 resolutions (2026-11-18)

Round 1 left five open questions. All five are resolved in §6; the new
open list is §6.9.

## 6. Design round 2 — closing the open questions

### 6.1 The 600 kg sled doesn't close — split the configs

Itemised mass budget (solid-state pack at 400 Wh/kg, 500 kW turbine
~100 kg, avionics + lidar + legs + locks + tether spool ~100 kg,
frame ~150 kg):

- The thread's sled at 600 kg with a 150 kWh pack (375 kg) + turbine
  (100 kg) + 12 fans (~200 kg) + frame (150 kg) + avionics (100 kg) =
  **~929 kg, not 600.** The 600 kg figure only works with no pack and no
  turbine.
- Fix: the pack is swappable (the thread already says so), so split the
  configs. **Bridge config** carries a 40 kWh buffer pack (100 kg), no
  turbine: sled **554–614 kg** depending on fan config. **Flying config**
  carries the 150 kWh pack + 500 kW turbine: sled **929–989 kg**.
  All numbers in §6.2 use these masses, not 600 kg.

### 6.2 Fan trade study — A / B / C

|  | A | B | C (baseline) |
|---|---|---|---|
| Fans | 12 × 0.8 m | 10 × 1.0 m | 12 × 1.0 m |
| Thrust each / total | 2,800 N / 33,600 N | 3,360 N / 33,600 N | 3,360 N / 40,320 N |
| Disk area | 6.03 m² | 7.85 m² | 9.42 m² |
| Deck length | 5.2 m | 5.6 m | 6.6 m |
| Bridge sled | 554 kg | 570 kg | 614 kg |
| Flying sled | 929 kg | 945 kg | 989 kg |
| Hover power, realistic | 1,717 kW | 1,518 kW | 1,419 kW |
| Flying margin, combined | 1.53× | 1.52× | 1.73× |
| Flying margin, sled alone | 1.23× | 1.23× | 1.45× |
| Flying, 2 fans dead | 1.32× | 1.27× | 1.49× |
| Pier margin (car centred) | 1.37× | 1.36× | 1.60× |
| Pier, 2 fans dead | 1.14× | 1.09× | 1.34× |
| Sleds for a 25 m river | 5 | 4 | 4 |

**Recommendation: C as the baseline, B as the compact fallback, A retired.**
C wins on every margin and on power, needs one fewer sled per river, and
its 6.6 m deck fixes a real fit problem the thread missed: a 5.2 m deck
barely fits a Model 3 (4.72 m) and does **not** fit a light pickup
(Ranger 5.35 m). C fits both. Cost: the biggest, heaviest sled and the most
expensive fans.

### 6.3 Flight time — the 25-minute figure is dropped

With the honest masses and a 500 kW turbine:

| Config | Battery-only | + 500 kW turbine |
|---|---|---|
| A | 5.2 min | 7.4 min |
| B | 5.9 min | 8.8 min |
| C | 6.3 min | 9.8 min |

The thread's 25–30 min is gone. A 1 MW turbine would give C ~18 min, but
that's a ~200 kg helicopter engine and it pushes the flying sled past
1,000 kg. **Decision: ~10 min is the design flight time.** It fits the
military use cases in the thread anyway — every hop described is a
5-minute crossing, and the bridge never flies at all.

### 6.4 Car cold-gas thrusters — seconds only, sled must hover alone

Checked: nitrogen cold gas, Isp ~65 s. One 2,000 N thruster burns
**3.14 kg/s**. Four thrusters:

| Assist length | Gas | Tank volume (300 bar) | System mass |
|---|---|---|---|
| 1 s | 12.5 kg | 55 L | ~50 kg |
| 2 s | 25.1 kg | 109 L | ~90 kg |
| 3 s | 37.6 kg | 164 L | ~129 kg |
| 5 s | 62.7 kg | 273 L | ~209 kg |

The thread's "combined 41,600 N" is only true for the first 1–3 seconds —
after the gas is gone it's the sled's thrust alone. **Design rule: the sled
must hover on its own fans; the car thrusters are a takeoff puff, never a
requirement.** The margins in §6.2 already assume the car thrusters
contribute nothing after liftoff (sled-alone margin 1.23–1.45×). A 1-second
puff (~50 kg of tanks) is the sensible fit; 5 seconds costs 209 kg and
isn't worth it.

### 6.5 Tether — 1,000 V DC, 400 kW bridge spec

- **Decision: 1,000 V DC.** It's the EV fast-charge voltage class, so
  connectors, breakers and cable exist off the shelf. 3,000 V would cut the
  cable weight by two-thirds but the shock and insulation burden over water
  isn't worth it for a first build.
- Bridge spec (config C): tether **400 kW per sled**. Hold power is only
  ~143 kW, so ~257 kW goes to recharging the buffer between cars.
- Crossing rate: each car costs a pier ~6.0 kWh (20 s on the pier). The
  40 kWh buffer covers **~7 cars in a burst**, then the tether recharges at
  ~1.4 min per car. Sustained rate: **~1 car every 1.4 min**. A 250 kW
  tether would stretch that to ~1.8 min per car and save cable weight —
  the 400 kW number is the one to build to, the 250 kW is the fallback.
- The tether unspools from the sled as it flies out; breakaway connectors
  and a dead-man cut if a cable is severed (sled falls back to battery and
  auto-lands, §3).

### 6.6 Deck joint — designed, not yet drawn

- Gap plate: **1.2 m long × 2.4 m wide**, steel-reinforced composite,
  hinged to both neighbouring sled decks on **30 mm steel pins**.
  Each pin takes ~half a car in shear (~9 kN) — a 30 mm pin is far inside
  its rating; the pins are sized by fatigue and corrosion, not strength.
- Tapered lead-in edges (15°) on both ends so tires never catch the seam.
- The plate rotates freely in pitch (that's the point — §3) but is locked
  against sideways shift by guide lugs.
- Folding guide rails on the plate edges, raised once the bridge is in
  station; they also act as the visual lane edge at night (reflective).
- Still to draw: the pin housing and the rail hinge. Text dimensions above
  are the spec to draw from.

### 6.7 Bridge geometry — updated for the C deck

Pitch is deck + 1 m gap plate = **7.6 m** (C). Sleds per river:

| River width | Sleds (C) | Deck length |
|---|---|---|
| 20 m | 3 | 22.8 m |
| 25 m | 4 | 30.4 m |
| 30 m | 4 | 30.4 m |
| 40 m | 6 | 45.6 m |

(For A/B decks, see §6.2 — A needs 5 sleds for 25 m, B needs 4.)

### 6.8 Prototype step 1 — test plan

1. **Thrust stand.** One 1.0 m fan unit on a stand: verify 3,360 N at rated
   power, measure power vs thrust curve, thermal soak 10 min.
   Pass: within 5% of the design curve, no over-temp.
2. **Single sled, sandbags.** C-config sled, 2,839 kg sandbag load,
   tethered hover in a netted area, 60 s. Measure power draw, hold
   position within ±0.5 m, vibration at the deck.
   Pass: power within 10% of the 1,419 kW prediction, position held.
3. **Fan-out.** Same hover, cut 2 fans by throttle command, verify
   controlled descent and landing, no tip-over.
   Pass: lands level, deck stays within 5° of horizontal throughout.

### 6.9 Open questions (new list) — resolved in §7 (round 3), except the
control sim, which is round 4

- Deck joint drawing — **done**, see §7.1.
- Real fan units — **researched**, see §7.2.
- Tether connector design — **specced**, see §7.4.
- Generator truck spec — **specced**, see §7.3.
- Fly-by-wire control software — **outlined**, see §7.5; the sim is round 4.
- Noise measurement — **estimated + planned**, see §7.6.

## 7. Design round 3 — joint drawn, fans and power sourced (2026-11-18)

### 7.1 Deck joint — drawn

Drawing: [deck_joint_drawing.html](deck_joint_drawing.html) — Fig 1
longitudinal section, Fig 2 plan view, parts list, notes.

The drawing caught a real design error: a plate pinned rigidly at both ends
would bind. Each sled holds station independently, so in wind the gap
breathes by millimetres and the decks pitch a degree or two. The fix is in
the drawing — **pinned at one end only (Ø30 mm transverse pin, 3-lug
clevis); the far end rides in a slotted bearing (±50 mm slide)** and bears
on a pad. Station-keeping tolerance is ±50 mm; beyond that, stop crossings
and re-station. If a plate ever disengages it stays pinned at the fixed end
and hangs — it doesn't fall in the river. Plate top is flush with the decks
(70 mm recess), 15° lead-in tapers both ends, folding guide rails, anti-shift
lugs. Pin load is ~9 kN in shear per side — the pins are wear items, not
strength items; check for elongation and corrosion after every deployment.

### 7.2 Fans — no off-the-shelf 1.0 m / 3,360 N set exists

Researched (web, 2026-11-18):

- Largest credible off-the-shelf aerospace EDF: **Schübeler eP05-21** —
  ~0.5 m diameter, **680 N** static thrust, 21 kW, 8.3 kg, 61 dBA at ~400 ft
  flyby. That's one-fifth of the 3,360 N per station the C config needs.
- Custom-class data point: a builder's 570 mm fan claims 1,650 N at 100 kW
  (29 kg) — physically plausible, unproven.
- Manned eVTOL lift fans are 1.6 m class and custom (a published 1.8 t
  eVTOL design uses four 1.6 m tilting ducted fans). Nothing to buy there.
- The design's FoM 0.75 assumption is supported: an MDPI test-stand study
  of a 390 mm EDF measured figure of merit 0.7–0.75 at medium speeds.

Options:

- **(a) Cluster (buyable now):** 5 × eP05-21 per station = 3,400 N,
  105 kW, 41.5 kg per station, 60 fans total. Graceful degradation — lose
  one fan of 60 and you lose 1.7% of thrust, not 8.3%. Cost: ~234 kg more
  fan mass than the 12-fan layout, and 60 ESCs/motors to wire.
- **(b) Custom 1.0 m fan:** a development program, lighter, but nothing to
  buy — it has to be designed, built and tested.

**Recommendation: prototype on clusters (a), production on a custom 1.0 m
fan (b).** The cluster also makes the prototype's fan-out test trivially
survivable. The cluster's mass penalty has to be re-run through the mass
budget when the prototype is specced — the 554–614 kg bridge sled figures
in §6.2 assume the 12-fan layout.

### 7.3 Generator truck — spec from research

- 1 MW towable diesel, real units: Toros TPP 1000 — 9,690 kg, 9.0 × 2.1 ×
  2.65 m, 4 axles, 195 L/h at full load; Cummins KTA38-G5 1000 kVA —
  8,120 kg open / 10,270 kg soundproof, 209 L/h; big rental unit (Herc
  Cummins C1000D6RG) — 18.7 t, 242 L/h. Working spec: **~7–10 t trailer,
  ~9 m long, ~200 L/h at full load**.
- Sizing: a 4-sled bridge at max crossing rate draws ~400 kW average per
  sled → 1.6 MW. So **one 1 MW genset per 2 sleds at max rate; a 4-sled
  bridge takes 2 gensets, one per bank**. At a reduced rate (~1 car per
  2.5 min per pier) one genset covers 4 sleds.
- Fuel: at ~60% average load, ~120 L/h per genset → 24 h of operations
  needs ~2,900 L per genset. A fuel bowser on site, not jerry cans.

### 7.4 Tether connector — spec

- 400 A at 1,000 V DC per tether. Connector class: parallel high-current
  DC quick-disconnects (Anderson SB-series class, 350 A each, two in
  parallel per pole) or a single 500 A unit — spec the class now, the exact
  part number once the cable is sized.
- **Breakaway:** the connector separates at ~500 N pull. A snag disconnects;
  it doesn't drag the sled down.
- **Dead-man:** a pilot loop runs in the tether. If continuity breaks,
  contactors open at both ends (generator side and sled side) within 50 ms;
  the sled falls back to battery and auto-lands (§3).
- Cable: 400 A needs ~120 mm² copper per conductor; the pair + sheath runs
  ~2.5–3 kg/m, so 30 m is ~75–90 kg — mostly ground-supported (catenary
  from the bank), roughly half of it airborne.

### 7.5 Control architecture — outline

- **Sensors:** RTK GPS (cm-level), IMU, lidar altimeter, per-fan RPM and
  current telemetry, tether tension.
- **Estimator:** GPS/INS fusion; position hold ±0.5 m in gusts (the
  prototype pass criterion from §6.8).
- **Loops:** outer position loop → attitude loop → control allocation
  (12 fans → 4 axes: thrust, roll, pitch, yaw). Allocation is a
  least-squares solve re-run every cycle; on a fan failure the dead fan is
  removed from the allocation and the rest re-solve — that is what makes
  fan-out survivable rather than lucky.
- **Bridge mode:** one leader sled, the rest followers holding *relative*
  position to the leader, not absolute GPS. The whole bridge drifts together
  in a gust instead of the sleds fighting each other.
- **Modes:** takeoff, transit, station-keep (bridge), landing, emergency
  (battery auto-land on tether loss; controlled descent on fan-out).
- **Next step (round 4):** build the allocation + hover sim in Python —
  show the sled holding position and surviving a 2-fan failure in sim
  before any hardware is bought.

### 7.6 Noise — estimate + measurement plan

- Reference points: Schübeler eP05-21 — 61 dBA at ~400 ft flyby. An MDPI
  test-stand measurement on a 390 mm EDF found SPL near 120 dB above
  6,000 RPM — close-in, on the stand, **not** an environmental figure; do
  not quote it as one.
- Rough scaling for the 1.0 m fan (tip speed ~180 m/s, Mach 0.52,
  ~3,400 RPM): inverse-square from the Schübeler figure suggests
  **~85–93 dBA at 7 m**. Rough — label it as an estimate, not a measurement.
- Plan: measure at 7 m and at 120 m in prototype testing. The civilian case
  depends on the measurement, not the estimate.

### 7.7 Open questions (new list)

- Fan supplier outreach: eP05-21 pricing and lead time for 60 units
  (prototype cluster).
- Control sim (round 4): allocation + hover + fan-out in Python.
- Tether cable: exact conductor size and weight once the bank-to-sled route
  is fixed.
- Joint drawing review (his call), then build one joint and load-test the
  pin.
- Noise: nothing to do until the prototype fan runs.
