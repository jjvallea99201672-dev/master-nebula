"""BellNet v2.1 - framed bell-tone protocol with digits (encoder + decoder).

Every payload/control symbol is a DYAD: one LOW tone + one HIGH tone struck together.
All tones sit on a whole-tone grid from 300 Hz, so no tone is within 1 semitone of any
other tone's 1.5f overtone (overtones land on odd semitones, every tone on even ones).

Frame: SYNC(5 single bells 378-600-952-600-378) <VER21> LEN(2 letters) CLEN(1 letter)
       <START> PAYLOAD(a-z, 0-9, <WORD>) <CHECK> CRC(CLEN letters) <END>
Usage:  python3 bellnet21.py encode "Hello lattice 432" out.wav
        python3 bellnet21.py decode in.wav
"""
import sys, json, zlib, string
import numpy as np, soundfile as sf

SR, SLOT, BELL_LEN, LEAD, TAIL = 44100, 0.50, 0.46, 1.0, 1.0
BASE = 300.0
st = lambda s: BASE * 2 ** (s / 12)
LOW = [st(s) for s in (0, 4, 8, 12, 16, 20, 24)]        # 300 .. 1200 Hz (major-third steps)
HIGH = [st(s) for s in (26, 30, 34, 38, 42, 46)]        # 1347 .. 4276 Hz
TONES = LOW + HIGH
SYNC = [1, 3, 5, 3, 1]                                  # LOW indices: 378, 600, 952, 600, 378 Hz

ALPHA = list(string.ascii_lowercase) + list(string.digits) + \
        ["<WORD>", "<VER21>", "<START>", "<CHECK>", "<END>", "<RSV>"]   # 42 codes = 7 x 6
CODE = {s: i for i, s in enumerate(ALPHA)}
DYAD = {s: (i // 6, i % 6) for s, i in CODE.items()}    # (low index, high index)
FROM_DYAD = {v: k for k, v in DYAD.items()}
LETTERS = set(string.ascii_lowercase)
PAYLOAD_OK = set(string.ascii_lowercase + string.digits)
CRC_LEN_DEFAULT, MAX_LEN = 3, 26 ** 2 - 1


def normalize(text):
    if not isinstance(text, str): raise TypeError("payload must be a string")
    msg = text.strip().lower()
    bad = sorted(set(msg) - PAYLOAD_OK - {" "})
    if bad: raise ValueError(f"unsupported characters: {bad!r} (only a-z, 0-9, space)")
    if not msg: raise ValueError("empty payload")
    if len(msg) > MAX_LEN: raise ValueError(f"payload too long ({len(msg)} > {MAX_LEN})")
    return msg


def to_b26(n, w):
    out = []
    for _ in range(w): out.append(chr(97 + n % 26)); n //= 26
    return out[::-1]


def from_b26(ls):
    n = 0
    for c in ls: n = n * 26 + ord(c) - 97
    return n


def crc_value(text, clen): return zlib.crc32(text.encode()) % 26 ** clen


def frame_symbols(text, clen=CRC_LEN_DEFAULT):
    msg = normalize(text)
    payload = ["<WORD>" if c == " " else c for c in msg]
    return ([("SYNC", i) for i in SYNC] + ["<VER21>"] + to_b26(len(payload), 2) + to_b26(clen, 1)
            + ["<START>"] + payload + ["<CHECK>"] + to_b26(crc_value(msg, clen), clen) + ["<END>"])


def _bell(f, amp):
    t = np.arange(int(BELL_LEN * SR)) / SR
    return np.minimum(1, 30 * t) * np.exp(-4.2 * t) * amp * (
        np.sin(2 * np.pi * f * t) + 0.10 / 0.34 * np.sin(2 * np.pi * 1.5 * f * t))


def synth(symbols):
    y = np.zeros(int((LEAD + (len(symbols) - 1) * SLOT + BELL_LEN + TAIL) * SR))
    for i, s in enumerate(symbols):
        if isinstance(s, tuple): b = _bell(LOW[s[1]], 0.34)                  # sync: single bell
        else:
            lo, hi = DYAD[s]; b = _bell(LOW[lo], 0.24) + _bell(HIGH[hi], 0.24)
        i0 = int(round((LEAD + i * SLOT) * SR)); y[i0:i0 + len(b)] += b
    return y


def encode(text, path=None, clen=CRC_LEN_DEFAULT):
    y = synth(frame_symbols(text, clen))
    if path: sf.write(path, y.astype(np.float32), SR, subtype="PCM_16")
    return y


# ------------------------------------------------------------------ decoder
WIN, HOP, NFFT, TOL, PRESENCE = 0.24, 0.01, 16384, 0.006, 10.0


class Analyzer:
    def __init__(self, x, sr):
        self.sr, self.win, self.hop = sr, int(WIN * sr), int(HOP * sr)
        fr = np.array(TONES); freqs = np.concatenate([fr, fr * 1.5]); self.nt = len(fr)
        bins = np.fft.rfftfreq(NFFT, 1 / sr)
        cols = [np.where(np.abs(bins - f) <= max(f * TOL, sr / NFFT))[0] for f in freqs]
        band = (bins > 150) & (bins < 7500); w = np.hanning(self.win)
        nfr = max(0, (len(x) - self.win) // self.hop + 1)
        self.M = np.zeros((nfr, len(freqs))); self.floor = np.zeros(nfr)
        for a in range(0, nfr, 200):
            idx = np.arange(a, min(nfr, a + 200))
            S = np.abs(np.fft.rfft(np.stack([x[i * self.hop:i * self.hop + self.win] * w for i in idx]), NFFT))
            self.M[idx] = np.stack([S[:, c].max(1) for c in cols], 1)
            self.floor[idx] = np.median(S[:, band], 1)

    def _fr(self, t):
        i = int(round(t * self.sr / self.hop)); return i if 0 <= i < len(self.M) else None

    def tones(self, t, fallback=False):
        """(low idx | None, high idx | None, low ratio) from the rise in energy at slot time t.
        fallback: if one band clearly rose but the other did not (a repeated tone can partly cancel
        against its own reverb tail), read the quiet band from raw energy instead of the rise."""
        ic, ip = self._fr(t + 0.01), self._fr(t - 0.01 - WIN)
        if ic is None: return None, None, 0.0
        cur = self.M[ic]; prev = self.M[ip] if ip is not None else 0 * cur
        nov = np.maximum(0, cur - prev); fund = nov[:self.nt]; sc = fund + 0.3 * nov[self.nt:]
        fl = max(self.floor[ic], 1e-9); out = []
        for lo, hi in ((0, len(LOW)), (len(LOW), self.nt)):
            k = lo + int(np.argmax(sc[lo:hi])); r = fund[k] / fl
            out.append((k - lo, r) if fund[k] > 1e-4 and r >= PRESENCE else (None, r))
        if fallback and (out[0][0] is None) != (out[1][0] is None):
            raw = cur[:self.nt] + 0.3 * cur[self.nt:]
            for b, (lo, hi) in enumerate(((0, len(LOW)), (len(LOW), self.nt))):
                if out[b][0] is None:
                    k = lo + int(np.argmax(raw[lo:hi]))
                    if cur[k] / fl >= PRESENCE: out[b] = (k - lo, cur[k] / fl)
        return out[0][0], out[1][0], out[0][1]

    def symbol(self, t):
        lo, hi, _ = self.tones(t, fallback=True)
        if lo is None and hi is None: return None          # silence / past end of stream
        if lo is None or hi is None: return "<ERR>"        # a bell is there but unreadable
        return FROM_DYAD[(lo, hi)]


def find_sync(an):
    best, bt = 0.0, None
    for i in range(len(an.M)):
        t, sc = i * HOP, 0.0
        for j, want in enumerate(SYNC):
            lo, hi, r = an.tones(t + j * SLOT)
            if lo != want or hi is not None: sc = None; break
            sc += r
        if sc and sc > best: best, bt = sc, t
    return bt


def classify_frame(end_seen, complete, crc):
    if crc == "CRC_VALID": return "valid" if end_seen else "truncated_valid"
    if crc == "CRC_INVALID": return "corrupt"
    if crc == "CRC_INCOMPLETE": return "integrity_unknown" if complete else "truncated_unknown"
    if crc == "CRC_MISSING": return "unverified" if complete else "truncated_unverified"
    return "unknown"


def decode(src, sr=SR):
    x, sr = sf.read(src) if isinstance(src, str) else (src, sr)
    x = np.asarray(x, float); x = x.mean(1) if x.ndim > 1 else x
    an = Analyzer(x, sr); t0 = find_sync(an)
    res = dict(message="", end_seen=False, payload_complete=False, crc_status="CRC_MISSING",
               frame_status="no_sync", reason="", sync_time=t0, symbols=[])
    if t0 is None: res["reason"] = "sync pattern not found"; return res
    k = len(SYNC)
    def nxt():
        nonlocal k
        s = an.symbol(t0 + k * SLOT); k += 1; res["symbols"].append(s); return s
    def header_fail(why, s):
        res.update(frame_status="truncated_unverified" if s is None else "corrupt", reason=why); return res
    v = nxt()
    if v != "<VER21>": return header_fail(f"version symbol missing/wrong: {v}", v)
    hdr = [nxt() for _ in range(3)]
    if any(h not in LETTERS for h in hdr):
        return header_fail(f"header unreadable: {hdr}", None if None in hdr else "x")
    plen, clen = from_b26(hdr[:2]), from_b26(hdr[2:])
    s = nxt()
    if s != "<START>": return header_fail(f"START bell missing/wrong: {s}", s)
    res.update(payload_length=plen, crc_length=clen)
    payload, stop = [], None
    while len(payload) < plen:                       # priority 1: declared length
        s = nxt()
        if s is None: stop = "stream ended inside payload"; break
        if s == "<WORD>": payload.append(" ")
        elif s in PAYLOAD_OK: payload.append(s)
        elif s == "<ERR>": payload.append("?")       # damaged bell: CRC will reject it
        else: stop = f"unexpected {s} inside payload"; k -= 1; res["symbols"].pop(); break
    msg = "".join(payload); res["message"] = msg; res["payload_complete"] = len(payload) == plen
    digits, damaged = [], False
    if stop is None or "unexpected" in stop:
        if nxt() == "<CHECK>":                       # priority 2: CRC of declared length
            while len(digits) < clen:
                s = nxt()
                if s is None: break
                if s not in LETTERS: damaged = True
                digits.append(s)
    if damaged: crc = "CRC_INVALID"                  # bell present but not a letter = damage
    elif not digits: crc = "CRC_MISSING"
    elif len(digits) < clen: crc = "CRC_INCOMPLETE"  # never reported as invalid
    else:
        calc = crc_value(msg, clen)
        res.update(crc_supplied="".join(digits), crc_calculated="".join(to_b26(calc, clen)))
        crc = "CRC_VALID" if from_b26(digits) == calc and res["payload_complete"] and "?" not in msg else "CRC_INVALID"
    res["crc_status"] = crc
    if crc in ("CRC_VALID", "CRC_INVALID"): res["end_seen"] = nxt() == "<END>"   # priority 3
    res["frame_status"] = classify_frame(res["end_seen"], res["payload_complete"], crc)
    res["reason"] = (f"{stop} ({len(payload)}/{plen} payload symbols)" if stop else
                     "unreadable bell in checksum" if damaged else
                     "payload complete, no checksum received" if crc == "CRC_MISSING" else
                     f"checksum clipped ({len(digits)}/{clen} letters)" if crc == "CRC_INCOMPLETE" else
                     "checksum mismatch" if crc == "CRC_INVALID" else
                     "complete frame" if res["end_seen"] else
                     "END bell missing; length + CRC verified (priority 4: stream end)")
    return res


if __name__ == "__main__":
    if sys.argv[1] == "encode":
        y = encode(sys.argv[2], sys.argv[3]); print(f"wrote {sys.argv[3]} ({len(y)/SR:.2f} s)")
    else:
        print(json.dumps(decode(sys.argv[2]), indent=1, default=str))
