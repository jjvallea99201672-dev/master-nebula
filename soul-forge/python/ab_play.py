#!/usr/bin/env python3
"""
🔥 SOUL FORGE A/B PLAYER — Play Original vs Mastered in terminal
Requires: ffplay OR python3 with sounddevice

Usage:
  python3 ab_play.py Firework.wav
  python3 ab_play.py Firework.wav mastered/Firework_mastered_-14LUFS.wav
  python3 ab_play.py --loop --crossfade 0.5 Firework.wav

Controls while playing:
  [o] Original, [m] Mastered, [SPACE] Play/Pause, [l] Loop toggle, [q] Quit
  If ffplay is available, it uses ffplay. Otherwise uses sounddevice.
"""

import os, sys, argparse, subprocess, shutil, time, threading

def find_mastered(orig):
    base = os.path.splitext(os.path.basename(orig))[0]
    candidates = [
        f"mastered/{base}_mastered_-14LUFS.wav",
        f"mastered/{base}_mastered_-14LUFS.WAV",
        f"{base}_mastered_-14LUFS.wav",
        f"mastered/{base}_mastered_-13LUFS.wav",
        f"mastered/{base}_mastered_-16LUFS.wav",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # search mastered folder
    if os.path.isdir("mastered"):
        for f in os.listdir("mastered"):
            if base.lower() in f.lower() and f.endswith(".wav"):
                return os.path.join("mastered", f)
    return None

def play_ffplay(path):
    if not shutil.which("ffplay"):
        return False
    # -nodisp -autoexit, -af volume
    cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
    subprocess.run(cmd)
    return True

def play_with_sounddevice(path):
    try:
        import soundfile as sf
        import sounddevice as sd
        data, sr = sf.read(path, always_2d=False)
        print(f"▶ Playing {os.path.basename(path)} [{len(data)/sr:.1f}s @ {sr}Hz] — Ctrl+C to stop")
        sd.play(data, sr)
        sd.wait()
        return True
    except ImportError:
        print("⚠ Need: pip install sounddevice soundfile  OR  sudo apt install ffmpeg (for ffplay)")
        return False
    except Exception as e:
        print(f"Playback error: {e}")
        return False

def ab_loop(orig, mast, loop=False):
    print(f"""
╔══════════════════════════════════════════════════╗
║  🔥 SOUL FORGE A/B PLAYER                      ║
║  Original: {os.path.basename(orig):<30} ║
║  Mastered: {os.path.basename(mast):<30} ║
║  [o]=Original [m]=Mastered [a]=A/B Loop [q]=Quit║
╚══════════════════════════════════════════════════╝
""")
    current = 'o'
    while True:
        path = orig if current=='o' else mast
        label = "ORIGINAL" if current=='o' else "MASTERED -14 LUFS"
        print(f"\n▶ Now: {label} — {os.path.basename(path)}")
        if not play_ffplay(path):
            if not play_with_sounddevice(path):
                # fallback: try aplay / paplay
                if shutil.which("aplay"):
                    subprocess.run(["aplay", "-q", path])
                else:
                    print(f"Can't play {path} — install ffmpeg: sudo apt install ffmpeg")
                    break
        if not loop:
            # prompt
            try:
                cmd = input("\n[o]rig [m]astered [a]uto A/B loop [r]epeat [q]uit > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if cmd=='q' or cmd=='quit': break
            if cmd=='o': current='o'
            elif cmd=='m': current='m'
            elif cmd=='a': 
                print("🔁 A/B Loop: Original → 1s gap → Mastered → 1s gap → repeat (Ctrl+C to stop)")
                try:
                    while True:
                        for c, p in [('o',orig),('m',mast)]:
                            print(f"▶ {c.upper()} {os.path.basename(p)}")
                            play_ffplay(p) or play_with_sounddevice(p)
                            time.sleep(0.8)
                except KeyboardInterrupt:
                    print("\nStopped A/B loop")
            elif cmd=='r' or cmd=='':
                continue
        else:
            # auto loop
            try:
                while True:
                    for c, p in [('o',orig),('m',mast)]:
                        print(f"\n▶ AUTO {c.upper()}: {os.path.basename(p)}")
                        play_ffplay(p) or play_with_sounddevice(p)
                        time.sleep(1)
            except KeyboardInterrupt:
                break
            break

def main():
    parser = argparse.ArgumentParser(description="Soul Forge A/B Player")
    parser.add_argument("original", help="Original WAV/MP3")
    parser.add_argument("mastered", nargs="?", help="Mastered WAV (auto-find if not given)")
    parser.add_argument("--loop", action="store_true", help="Auto loop Original→Mastered")
    args = parser.parse_args()

    orig = args.original
    if not os.path.exists(orig):
        print(f"File not found: {orig}")
        sys.exit(1)
    mast = args.mastered or find_mastered(orig)
    if not mast or not os.path.exists(mast):
        print(f"Mastered file not found for {orig}. Run: python3 master.py {orig}")
        # still play original
        play_ffplay(orig) or play_with_sounddevice(orig)
        sys.exit(0)

    ab_loop(orig, mast, loop=args.loop)

if __name__ == "__main__":
    main()
