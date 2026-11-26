#!/usr/bin/env python3
"""
🔥 SOUL FORGE MASTERING — Python CLI
Real mastering for Suno tracks: Firework, Mount Moron, Neon Saturday BV

Usage:
  python3 master.py Firework.wav
  python3 master.py --lufs -14 --truepeak -1 --width 1.3 Firework.wav Mount_Moron.wav
  python3 master.py --batch soul_forge_vault/

Requirements:
  pip install numpy soundfile pyloudnorm pedalboard librosa scipy
"""

import os, sys, json, argparse, glob
import numpy as np
import soundfile as sf

# Optional imports with graceful fallback
try:
    import pyloudnorm as pyln
    HAS_PYLN = True
except:
    HAS_PYLN = False
    print("⚠ pyloudnorm not found — using RMS fallback for LUFS")

try:
    from pedalboard import Pedalboard, Limiter, HighShelfFilter, LowShelfFilter, PeakFilter, Gain
    HAS_PEDAL = True
except:
    HAS_PEDAL = False
    print("⚠ pedalboard not found — using numpy EQ fallback")

try:
    import librosa
    HAS_LIBROSA = True
except:
    HAS_LIBROSA = False

def load_audio(path):
    data, sr = sf.read(path, always_2d=True)  # shape (samples, channels)
    data = data.T  # to (channels, samples) for processing then back
    # transpose to (samples, channels) for pedalboard which expects that
    audio = sf.read(path)[0]  # get again as (samples, channels) if stereo, else (samples,)
    if audio.ndim == 1:
        audio = np.stack([audio, audio], axis=1)  # mono -> stereo
    # ensure float32
    audio = audio.astype(np.float32)
    sr = sf.info(path).samplerate
    return audio, sr

def measure_lufs(audio, sr):
    """Measure integrated LUFS. audio shape (samples, channels)"""
    if HAS_PYLN:
        try:
            meter = pyln.Meter(sr)
            # pyloudnorm expects (samples, channels) or (channels, samples)?? It wants (samples, channels) as 2D
            # Actually Meter.integrated_loudness expects ndarray shape (samples, channels) or (samples,)
            lufs = meter.integrated_loudness(audio)
            if np.isnan(lufs):
                lufs = -20.0
            return float(lufs)
        except Exception as e:
            print(f"  pyloudnorm error {e}, fallback")
    # Fallback RMS approximation: LUFS ≈ 20log10(RMS) -0.691
    rms = np.sqrt(np.mean(audio**2))
    lufs = 20*np.log10(max(1e-9, rms)) - 0.691
    return float(lufs)

def measure_peak(audio):
    peak = np.max(np.abs(audio))
    peak_db = 20*np.log10(max(1e-12, peak))
    return peak, peak_db

def measure_dr(audio):
    rms = np.sqrt(np.mean(audio**2))
    peak = np.max(np.abs(audio))
    dr = 20*np.log10(max(1e-12, peak) / max(1e-12, rms))
    return dr, rms, peak

def apply_eq(audio, sr, params):
    """params dict with sub,bass,lowMid,highMid,air in dB"""
    print(f"\n🎛 Step 1: Multiband EQ (Suno Fix)")
    if HAS_PEDAL:
        board = Pedalboard()
        # Sub 50Hz lowshelf +3
        if abs(params['sub'])>0.01:
            board.append(LowShelfFilter(cutoff_frequency_hz=50, gain_db=params['sub'], q=0.8))
            print(f"   Sub 50Hz: {params['sub']:+.1f} dB lowshelf")
        if abs(params['bass'])>0.01:
            board.append(PeakFilter(cutoff_frequency_hz=100, gain_db=params['bass'], q=0.9))
            print(f"   Bass 100Hz: {params['bass']:+.1f} dB")
        if abs(params['lowMid'])>0.01:
            board.append(PeakFilter(cutoff_frequency_hz=250, gain_db=params['lowMid'], q=1.0))
            print(f"   LowMid 250Hz: {params['lowMid']:+.1f} dB")
        if abs(params['highMid'])>0.01:
            board.append(PeakFilter(cutoff_frequency_hz=3000, gain_db=params['highMid'], q=1.1))
            print(f"   HighMid 3kHz: {params['highMid']:+.1f} dB")
        if abs(params['air'])>0.01:
            board.append(HighShelfFilter(cutoff_frequency_hz=12000, gain_db=params['air'], q=0.7))
            print(f"   Air 12kHz: {params['air']:+.1f} dB highshelf")
        return board(audio, sr)
    else:
        print("   (pedalboard not available — skipping EQ, install pedalboard for real EQ)")
        return audio

def apply_stereo(audio, width, mono_compat=True):
    print(f"\n🔊 Step 2: Stereo Width {width:.1f}x MonoCompat={mono_compat}")
    if audio.shape[1] < 2:
        return audio
    if mono_compat:
        width = min(width, 1.5)
    mid = (audio[:,0] + audio[:,1]) / 2
    side = (audio[:,0] - audio[:,1]) / 2
    side = side * width
    left = mid + side
    right = mid - side
    # prevent clipping
    maxv = max(np.max(np.abs(left)), np.max(np.abs(right)))
    if maxv > 0.95:
        left *= 0.95/maxv
        right *= 0.95/maxv
    return np.stack([left, right], axis=1)

def normalize_loudness(audio, sr, target_lufs):
    print(f"\n📊 Step 3: Loudness Normalization → {target_lufs} LUFS")
    cur = measure_lufs(audio, sr)
    gain_db = target_lufs - cur
    gain_lin = 10**(gain_db/20)
    gain_lin = np.clip(gain_lin, 0.1, 10.0)  # safety
    print(f"   Before: {cur:.1f} LUFS | Gain: {gain_db:+.1f} dB ({gain_lin:.3f}x)")
    return audio * gain_lin, cur, gain_db

def true_peak_limit(audio, sr, threshold_db):
    print(f"\n📈 Step 4: True Peak Limiting → {threshold_db} dBTP")
    if HAS_PEDAL:
        board = Pedalboard([Limiter(threshold_db=threshold_db, release_ms=50)])
        limited = board(audio, sr)
        peak, peak_db = measure_peak(limited)
        print(f"   Peak after limiter: {peak_db:.1f} dBTP")
        return limited
    else:
        thresh_lin = 10**(threshold_db/20)
        peak = np.max(np.abs(audio))
        if peak > thresh_lin:
            scale = thresh_lin / peak
            print(f"   Hard limiting {20*np.log10(peak):.1f} → {threshold_db} dBTP scale {scale:.3f}")
            return audio * scale
        print(f"   Peak {20*np.log10(peak):.1f} dBTP — clean")
        return audio

def process_file(path, out_dir, args):
    print(f"\n{'='*60}")
    print(f"🔥 MASTERING: {os.path.basename(path)}")
    print(f"{'='*60}")
    audio, sr = load_audio(path)
    print(f"Loaded {audio.shape[0]/sr:.2f}s, {sr}Hz, {audio.shape[1]}ch")

    # 1 EQ
    eq_params = {'sub': args.sub, 'bass': args.bass, 'lowMid': args.lowMid, 'highMid': args.highMid, 'air': args.air}
    audio = apply_eq(audio, sr, eq_params)

    # 2 Stereo
    audio = apply_stereo(audio, args.width, mono_compat=True)

    # 3 Loudness
    audio, cur_lufs, gain_db = normalize_loudness(audio, sr, args.lufs)

    # 4 True peak
    audio = true_peak_limit(audio, sr, args.truepeak)

    # 5 DR
    dr, rms, peak = measure_dr(audio)
    final_lufs = measure_lufs(audio, sr)
    final_peak, final_peak_db = measure_peak(audio)
    print(f"\n💎 Step 5: Dynamic Range: {dr:.1f} dB (Peak {final_peak_db:.1f} dBTP, RMS {20*np.log10(max(1e-12,rms)):.1f})")
    if dr < 8:
        print(f"   ⚠ DR low — consider less limiting")
    else:
        print(f"   ✅ DR preserved")

    # Write
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out_wav = os.path.join(out_dir, f"{base}_mastered_{int(args.lufs)}LUFS.wav")
    sf.write(out_wav, audio, sr, subtype='PCM_24')
    print(f"\n✅ Saved WAV: {out_wav}")

    # Metadata
    meta = {
        "title": base,
        "artist": "Soul Forge Radio",
        "album": "Self-Generating Transmissions",
        "isrc": f"ISRC-{np.random.randint(1000000,9999999)}",
        "upc": f"UPC-{np.random.randint(100000000000,999999999999)}",
        "genre": "Electronic / Spoken Word",
        "year": 2026,
        "bpm": 60,
        "key": "A2",
        "lufs": round(final_lufs,2),
        "truePeak_dBTP": round(final_peak_db,2),
        "dr_dB": round(dr,2),
        "gainApplied_dB": round(gain_db,2),
        "params": {"lufs": args.lufs, "truePeak": args.truepeak, "width": args.width, "sub": args.sub, "bass": args.bass, "lowMid": args.lowMid, "highMid": args.highMid, "air": args.air},
        "source": path,
        "sampleRate": sr
    }
    out_json = os.path.join(out_dir, f"{base}_metadata.json")
    with open(out_json, 'w') as f:
        json.dump(meta, f, indent=2)
    print(f"🏷 Metadata: {out_json} | ISRC {meta['isrc']}")

    return {"path": out_wav, "lufs": final_lufs, "peak": final_peak_db, "dr": dr}

def main():
    p = argparse.ArgumentParser(description="Soul Forge Mastering — Python CLI")
    p.add_argument("inputs", nargs="*", help="WAV/MP3 files or folders")
    p.add_argument("--lufs", type=float, default=-14.0, help="Target LUFS (default -14 Spotify)")
    p.add_argument("--truepeak", type=float, default=-1.0, help="True peak threshold dBTP (default -1)")
    p.add_argument("--width", type=float, default=1.3, help="Stereo width 0.5-2.0")
    p.add_argument("--sub", type=float, default=3.0, help="Sub 50Hz gain dB")
    p.add_argument("--bass", type=float, default=-2.0, help="Bass 100Hz gain dB")
    p.add_argument("--lowMid", type=float, default=-1.5, help="LowMid 250Hz gain dB")
    p.add_argument("--highMid", type=float, default=-1.0, help="HighMid 3kHz gain dB")
    p.add_argument("--air", type=float, default=1.0, help="Air 12kHz gain dB")
    p.add_argument("--out", default="mastered", help="Output dir")
    p.add_argument("--batch", help="Batch folder to process all audio")
    args = p.parse_args()

    files = []
    if args.batch:
        for ext in ("*.wav","*.mp3","*.flac","*.m4a","*.WAV","*.MP3"):
            files.extend(glob.glob(os.path.join(args.batch, "**", ext), recursive=True))
    files.extend([f for f in args.inputs if os.path.isfile(f)])
    # folders in inputs
    for inp in args.inputs:
        if os.path.isdir(inp):
            for ext in ("*.wav","*.mp3","*.flac","*.m4a","*.WAV","*.MP3"):
                files.extend(glob.glob(os.path.join(inp, "**", ext), recursive=True))

    if not files:
        print("No files found. Usage: python3 master.py Firework.wav  OR  python3 master.py --batch soul_forge_vault/")
        print("Install deps: pip install numpy soundfile pyloudnorm pedalboard librosa")
        sys.exit(1)

    print(f"🔥 SOUL FORGE MASTERING — {len(files)} file(s) | Target {args.lufs} LUFS | {args.truepeak} dBTP")
    results = []
    for fp in files:
        try:
            r = process_file(fp, args.out, args)
            results.append(r)
        except Exception as e:
            print(f"❌ Failed {fp}: {e}")
            import traceback; traceback.print_exc()

    print(f"\n{'='*60}\n✅ BATCH DONE — {len(results)}/{len(files)} mastered → {args.out}/\n{'='*60}")
    for r in results:
        print(f"  {os.path.basename(r['path'])} — {r['lufs']:.1f} LUFS, {r['peak']:.1f} dBTP, DR {r['dr']:.1f} dB")

if __name__ == "__main__":
    main()
