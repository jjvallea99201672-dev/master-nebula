#!/usr/bin/env python3
"""
AI Music Mastering Suite v6.1
Serial/parallel DSP chain with LUFS, true peak, M/S, provenance logging.
Author: Jay Vallea | Self-taught, 3 months
"""

import argparse
import json
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
import numpy as np
from scipy.signal import resample_poly, butter, sosfilt, sosfilt_zi, iirfilter, lfilter

try:
    import soundfile as sf
    import pyloudnorm as pyln
except ImportError as e:
    raise SystemExit("Missing deps: pip install soundfile pyloudnorm numpy scipy")

# --- Core Data Structures ---

@dataclass
class AudioTrack:
    samples: np.ndarray # shape: (channels, num_samples), float32 [-1, 1]
    sample_rate: int
    bit_depth: int = 24
    metadata: Dict[str, Any] = None
    source_hash: str = ""

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if "provenance" not in self.metadata:
            self.metadata["provenance"] = []
        if not self.source_hash:
            self.source_hash = self.compute_hash()
        self.samples = self.samples.astype(np.float32)
        if self.samples.ndim == 1:
            self.samples = self.samples[np.newaxis, :]

    @classmethod
    def from_file(cls, path: Path) -> 'AudioTrack':
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        info = sf.info(str(path))
        data, sr = sf.read(str(path), always_2d=True, dtype='float32')
        samples = data.T
        bits = info.subtype_info.bits if info.subtype_info else 24
        return cls(samples=samples, sample_rate=sr, bit_depth=bits,
                   metadata={"source_path": str(path)})

    def to_file(self, path: Path, dither: bool = False, target_bits: int = 24):
        data = self.samples.T
        if dither and target_bits < 32:
            data = self._tpdf_dither(data, target_bits)
        subtype = f'PCM_{target_bits}' if target_bits in [16, 24, 32] else 'FLOAT'
        sf.write(str(path), data, self.sample_rate, subtype=subtype)

    def _tpdf_dither(self, x: np.ndarray, bits: int) -> np.ndarray:
        lsb = 2 ** (1 - bits)
        dither = (np.random.random(x.shape) - np.random.random(x.shape)) * lsb
        y = x + dither
        quant = np.round(y / lsb) * lsb
        return np.clip(quant, -1.0, 1.0 - lsb)

    def compute_hash(self) -> str:
        return hashlib.sha256(self.samples.tobytes()).hexdigest()

    def lufs(self) -> float:
        meter = pyln.Meter(self.sample_rate)
        return float(meter.integrated_loudness(self.samples.T))

    def true_peak_dbtp(self) -> float:
        up = resample_poly(self.samples, 4, 1, axis=1)
        peak = np.max(np.abs(up))
        return 20 * np.log10(peak + 1e-20)

    def loudness_match(self, target_lufs: float) -> 'AudioTrack':
        current = self.lufs()
        if not np.isfinite(current):
            return self
        gain_db = target_lufs - current
        gain = 10 ** (gain_db / 20)
        return AudioTrack(self.samples * gain, self.sample_rate, self.bit_depth, self.metadata.copy())

    def to_ms(self) -> np.ndarray:
        """Convert L/R to M/S. Returns shape (2, N) = [M, S]"""
        if self.samples.shape[0] < 2:
            return np.vstack([self.samples[0], np.zeros_like(self.samples[0])])
        L, R = self.samples[0], self.samples[1]
        M = (L + R) * 0.5
        S = (L - R) * 0.5
        return np.vstack([M, S])

    @classmethod
    def from_ms(cls, ms: np.ndarray, sr: int, bit_depth: int, metadata: Dict) -> 'AudioTrack':
        """Convert M/S back to L/R"""
        if ms.shape[0] < 2:
            lr = np.vstack([ms[0], ms[0]])
        else:
            M, S = ms[0], ms[1]
            L = M + S
            R = M - S
            lr = np.vstack([L, R])
        return cls(lr, sr, bit_depth, metadata)

# --- AI Model Abstraction ---

class AIMasteringModel(ABC):
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.enabled = True
        self.logger = logging.getLogger(f"Model.{name}")

    @abstractmethod
    def process(self, track: AudioTrack) -> AudioTrack:
        pass

    def get_signature(self) -> Dict[str, Any]:
        cfg_str = json.dumps(self.config, sort_keys=True)
        return {
            "name": self.name,
            "config_hash": hashlib.sha256(cfg_str.encode()).hexdigest()[:12],
            "config": self.config
        }

    def safe_process(self, track: AudioTrack) -> AudioTrack:
        if not self.enabled:
            return track
        try:
            self.logger.info(f"Processing with {self.name}")
            result = self.process(track)
            result.metadata["provenance"] = result.metadata.get("provenance", []) + [self.get_signature()]
            return result
        except Exception as e:
            self.logger.error(f"Model {self.name} failed: {e}")
            return track

# --- DSP Utils ---

def db_to_lin(db: float) -> float:
    return 10 ** (db / 20)

def lin_to_db(lin: float) -> float:
    return 20 * np.log10(lin + 1e-20)

def lr_filterbank(x: np.ndarray, sr: int, crossover_hz: List[float], order: int = 4) -> np.ndarray:
    """Returns shape (num_bands, channels, num_samples)"""
    bands = []
    freqs = [0] + crossover_hz + [sr / 2]
    for i in range(len(freqs) - 1):
        if i == 0:
            sos = butter(order, freqs[i+1] / (sr / 2), btype='low', output='sos')
        elif i == len(freqs) - 2:
            sos = butter(order, freqs[i] / (sr / 2), btype='high', output='sos')
        else:
            sos = butter(order, [freqs[i] / (sr / 2), freqs[i+1] / (sr / 2)], btype='band', output='sos')

        ch_bands = []
        for ch in range(x.shape[0]):
            zi = sosfilt_zi(sos) * x[ch, 0]
            y, _ = sosfilt(sos, x[ch], zi=zi)
            ch_bands.append(y)
        bands.append(np.stack(ch_bands))
    return np.stack(bands)

def peak_env(x: np.ndarray, sr: int, attack_ms: float, release_ms: float) -> np.ndarray:
    a_att = np.exp(-1.0 / (sr * attack_ms / 1000))
    a_rel = np.exp(-1.0 / (sr * release_ms / 1000))
    env = np.zeros_like(x)
    env[0] = abs(x[0])
    for i in range(1, x.shape[0]):
        a = a_att if abs(x[i]) > env[i-1] else a_rel
        env[i] = a * env[i-1] + (1 - a) * abs(x[i])
    return env

def rms_env(x: np.ndarray, sr: int, win_ms: float = 5.0) -> np.ndarray:
    win = max(1, int(sr * win_ms / 1000))
    x2 = x ** 2
    kernel = np.ones(win) / win
    rms = np.sqrt(np.convolve(x2, kernel, mode='same'))
    return rms + 1e-20

# --- Models ---

class GainModel(AIMasteringModel):
    def process(self, track: AudioTrack) -> AudioTrack:
        target = self.config.get("target_lufs", -14.0)
        current = track.lufs()
        if not np.isfinite(current):
            self.logger.warning("Non-finite LUFS detected, skipping gain")
            return track
        db_diff = target - current
        gain = db_to_lin(db_diff)
        processed = track.samples * gain
        return AudioTrack(processed, track.sample_rate, track.bit_depth, track.metadata.copy())

class TruePeakLimiter(AIMasteringModel):
    def process(self, track: AudioTrack) -> AudioTrack:
        ceiling_db = self.config.get("ceiling_dbtp", -1.0)
        attack_ms = self.config.get("attack_ms", 1.0)
        ceiling_lin = db_to_lin(ceiling_db)
        up = resample_poly(track.samples, 4, 1, axis=1)

        gr = np.ones_like(up)
        over = np.abs(up) > ceiling_lin
        gr[over] = ceiling_lin / (np.abs(up[over]) + 1e-9)

        sr_up = track.sample_rate * 4
        for ch in range(up.shape[0]):
            gr[ch] = peak_env(gr[ch], sr_up, attack_ms, attack_ms * 10)

        limited_up = up * gr
        limited = resample_poly(limited_up, 1, 4, axis=1)[:, :track.samples.shape[1]]
        return AudioTrack(limited, track.sample_rate, track.bit_depth, track.metadata.copy())

class EQModel(AIMasteringModel):
    def _biquad(self, sr: int, freq: float, gain_db: float, q: float, ftype: str):
        A = 10 ** (gain_db / 40)
        w0 = 2 * np.pi * freq / sr
        alpha = np.sin(w0) / (2 * q)
        if ftype == "lowshelf":
            b0 = A * ((A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha)
            b1 = 2 * A * ((A - 1) - (A + 1) * np.cos(w0))
            b2 = A * ((A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)
            a0 = (A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha
            a1 = -2 * ((A - 1) + (A + 1) * np.cos(w0))
            a2 = (A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha
        elif ftype == "highshelf":
            b0 = A * ((A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha)
            b1 = -2 * A * ((A - 1) + (A + 1) * np.cos(w0))
            b2 = A * ((A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)
            a0 = (A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha
            a1 = 2 * ((A - 1) - (A + 1) * np.cos(w0))
            a2 = (A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha
        else: # peak
            b0 = 1 + alpha * A
            b1 = -2 * np.cos(w0)
            b2 = 1 - alpha * A
            a0 = 1 + alpha / A
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha / A
        return [b0/a0, b1/a0, b2/a0, 1.0, a1/a0, a2/a0]

    def process(self, track: AudioTrack) -> AudioTrack:
        bands = self.config.get("bands", [])
        mode = self.config.get("mode", "stereo")
        x = track.samples.copy()

        if mode!= "stereo" and x.shape[0] < 2:
            self.logger.warning("M/S mode requested but input is mono. Bypassing EQ.")
            return track

        if mode == "stereo":
            proc = x
        else:
            ms = track.to_ms()
            proc = ms[0:1] if mode == "mid" else ms[1:2]

        for band in bands:
            freq = band.get("freq", 1000)
            gain = band.get("gain_db", 0.0)
            q = band.get("q", 0.707)
            ftype = band.get("type", "peak")
            if abs(gain) < 0.01:
                continue
            coeffs = self._biquad(track.sample_rate, freq, gain, q, ftype)
            b, a = coeffs[:3], coeffs[3:]
            for ch in range(proc.shape[0]):
                proc[ch] = lfilter(b, a, proc[ch])

        if mode == "stereo":
            out = proc
        else:
            ms = track.to_ms()
            if mode == "mid":
                ms[0:1] = proc
            else:
                ms[1:2] = proc
            out = AudioTrack.from_ms(ms, track.sample_rate, track.bit_depth, {}).samples

        return AudioTrack(out, track.sample_rate, track.bit_depth, track.metadata.copy())

class StereoWidener(AIMasteringModel):
    def process(self, track: AudioTrack) -> AudioTrack:
        width = self.config.get("width", 1.2)
        bass_mono_hz = self.config.get("bass_mono_hz", 120)
        if track.samples.shape[0] < 2:
            return track
        L, R = track.samples[0], track.samples[1]
        M = (L + R) * 0.5
        S = (L - R) * 0.5
        if bass_mono_hz > 0:
            sos = butter(2, bass_mono_hz / (track.sample_rate / 2), btype='low', output='sos')
            S_low = sosfilt(sos, S)
            S = S - S_low
        S *= width
        L_out = M + S
        R_out = M - S
        out = np.stack([L_out, R_out])
        return AudioTrack(out, track.sample_rate, track.bit_depth, track.metadata.copy())

class MultibandCompressor(AIMasteringModel):
    def _compress_band(self, x: np.ndarray, sr: int, cfg: Dict[str, Any]) -> np.ndarray:
        thresh_db = cfg.get("threshold_db", -24)
        ratio = cfg.get("ratio", 4.0)
        attack_ms = cfg.get("attack_ms", 5)
        release_ms = cfg.get("release_ms", 100)
        makeup_db = cfg.get("makeup_db", 0.0)
        lookahead_ms = cfg.get("lookahead_ms", 3.0)
        knee_db = cfg.get("knee_db", 6.0)

        look_samples = int(sr * lookahead_ms / 1000)
        x_dl = np.pad(x, (look_samples, 0), mode='edge')[:-look_samples] if look_samples > 0 else x
        env = rms_env(x_dl, sr, win_ms=attack_ms)
        env_db = lin_to_db(env)

        # Fixed soft-knee calculation
        gain_db = np.zeros_like(env_db)
        thresh_low = thresh_db - knee_db / 2
        thresh_high = thresh_db + knee_db / 2

        below = env_db <= thresh_low
        knee = (env_db > thresh_low) & (env_db < thresh_high)
        above = env_db >= thresh_high

        gain_db[below] = 0
        gain_db[knee] = ((env_db[knee] - thresh_low) ** 2) / (2 * knee_db) * (1 - 1/ratio)
        gain_db[above] = (env_db[above] - thresh_db) * (1 - 1/ratio)

        # Smooth gain reduction
        a_att = np.exp(-1.0 / (sr * attack_ms / 1000))
        a_rel = np.exp(-1.0 / (sr * release_ms / 1000))
        gain_db_sm = np.zeros_like(gain_db)
        for i in range(1, len(gain_db)):
            a = a_att if gain_db[i] > gain_db_sm[i-1] else a_rel
            gain_db_sm[i] = a * gain_db_sm[i-1] + (1 - a) * gain_db[i]

        return x * db_to_lin(-gain_db_sm + makeup_db)

    def process(self, track: AudioTrack) -> AudioTrack:
        crossovers = self.config.get("crossovers_hz", [200, 2000])
        bands_cfg = self.config.get("bands", [
            {"threshold_db": -20, "ratio": 2.5, "attack_ms": 20, "release_ms": 200, "makeup_db": 1.5},
            {"threshold_db": -24, "ratio": 3.5, "attack_ms": 5, "release_ms": 100, "makeup_db": 2.5},
            {"threshold_db": -18, "ratio": 3.0, "attack_ms": 1, "release_ms": 60, "makeup_db": 2.0},
        ])
        mode = self.config.get("mode", "stereo")
        x = track.samples.copy()

        if mode!= "stereo":
            if x.shape[0] < 2:
                self.logger.warning("M/S requested but input is mono. Using stereo mode.")
                mode = "stereo"
            else:
                ms = track.to_ms()
                x = ms[0:1] if mode == "mid" else ms[1:2]

        if len(bands_cfg)!= len(crossovers) + 1:
            raise ValueError(f"bands config length {len(bands_cfg)} must match crossovers + 1 = {len(crossovers)+1}")

        banded = lr_filterbank(x, track.sample_rate, crossovers)
        out_bands = []
        for i in range(banded.shape[0]):
            comp_band = np.stack([self._compress_band(banded[i, ch], track.sample_rate, bands_cfg[i])
                                  for ch in range(banded.shape[1])])
            out_bands.append(comp_band)
        out = np.sum(out_bands, axis=0)

        if mode!= "stereo":
            ms = track.to_ms()
            if mode == "mid":
                ms[0:1] = out
            else:
                ms[1:2] = out
            out = AudioTrack.from_ms(ms, track.sample_rate, track.bit_depth, {}).samples

        return AudioTrack(out, track.sample_rate, track.bit_depth, track.metadata.copy())

class DynamicEQ(AIMasteringModel):
    def _dyn_band(self, x: np.ndarray, sr: int, cfg: Dict[str, Any]) -> np.ndarray:
        freq = cfg.get("freq", 3000)
        q = cfg.get("q", 1.0)
        thresh_db = cfg.get("threshold_db", -30)
        ratio = cfg.get("ratio", 3.0)
        attack_ms = cfg.get("attack_ms", 10)
        release_ms = cfg.get("release_ms", 100)
        max_gain_db = cfg.get("max_gain_db", -6.0)
        mode = cfg.get("mode", "compress")

        # Correct bandwidth calculation
        bw = freq / q
        low = (freq - bw/2) / (sr / 2)
        high = (freq + bw/2) / (sr / 2)
        low, high = np.clip([low, high], 0.001, 0.999)

        sos = butter(2, [low, high], btype='band', output='sos')
        band = sosfilt(sos, x)
        env = peak_env(band, sr, attack_ms, release_ms)
        env_db = lin_to_db(env)

        gain_db = np.zeros_like(env_db)
        if mode == "compress":
            over = env_db - thresh_db
            gain_db = np.where(over > 0, -over * (1 - 1/ratio), 0)
            gain_db = np.maximum(gain_db, -abs(max_gain_db))
        else: # expand
            under = thresh_db - env_db
            gain_db = np.where(under > 0, under * (ratio - 1), 0)
            gain_db = np.minimum(gain_db, abs(max_gain_db))

        processed_band = band * db_to_lin(gain_db)
        return x - band + processed_band

    def process(self, track: AudioTrack) -> AudioTrack:
        bands = self.config.get("bands", [])
        mode = self.config.get("mode", "stereo")
        x = track.samples.copy()

        if mode!= "stereo" and x.shape[0] < 2:
            self.logger.warning("M/S requested but input is mono. Using stereo.")
            mode = "stereo"

        if mode!= "stereo":
            ms = track.to_ms()
            x = ms[0:1] if mode == "mid" else ms[1:2]

        for band_cfg in bands:
            for ch in range(x.shape[0]):
                x[ch] = self._dyn_band(x[ch], track.sample_rate, band_cfg)

        if mode!= "stereo":
            ms = track.to_ms()
            if mode == "mid":
                ms[0:1] = x
            else:
                ms[1:2] = x
            x = AudioTrack.from_ms(ms, track.sample_rate, track.bit_depth, {}).samples

        return AudioTrack(x, track.sample_rate, track.bit_depth, track.metadata.copy())

class SaturatorModel(AIMasteringModel):
    def _saturate(self, x: np.ndarray, drive_db: float, curve: str = "tanh") -> np.ndarray:
        drive = db_to_lin(drive_db)
        x_driven = x * drive
        if curve == "tanh":
            y = np.tanh(x_driven)
        elif curve == "atan":
            y = (2 / np.pi) * np.arctan(x_driven * np.pi / 2)
        else: # soft
            y = x_driven / (1 + np.abs(x_driven))
        return y / drive

    def process(self, track: AudioTrack) -> AudioTrack:
        drive_db = self.config.get("drive_db", 6.0)
        mix = self.config.get("mix", 0.3)
        curve = self.config.get("curve", "tanh")
        oversample = self.config.get("oversample", 4)
        up = resample_poly(track.samples, oversample, 1, axis=1)
        wet = self._saturate(up, drive_db, curve)
        wet = resample_poly(wet, 1, oversample, axis=1)[:, :track.samples.shape[1]]
        out = track.samples * (1 - mix) + wet * mix
        out = np.clip(out, -1.0, 1.0)
        return AudioTrack(out, track.sample_rate, track.bit_depth, track.metadata.copy())

MODEL_REGISTRY = {
    "GainModel": GainModel,
    "TruePeakLimiter": TruePeakLimiter,
    "EQModel": EQModel,
    "StereoWidener": StereoWidener,
    "MultibandCompressor": MultibandCompressor,
    "DynamicEQ": DynamicEQ,
    "SaturatorModel": SaturatorModel,
}

# --- Suite Orchestrator ---

class MasteringSuite:
    def __init__(self, mode: Literal["serial", "parallel"] = "serial"):
        self.models: List[AIMasteringModel] = []
        self.mode = mode
        self.processing_log: List[Dict[str, Any]] = []
        self.logger = logging.getLogger("MasteringSuite")

    def add_model(self, model: AIMasteringModel):
        self.models.append(model)

    def _log_step(self, model: AIMasteringModel, in_track: AudioTrack, out_track: AudioTrack):
        self.processing_log.append({
            "model": model.get_signature(),
            "input_hash": in_track.compute_hash()[:12],
            "output_hash": out_track.compute_hash()[:12],
            "input_lufs": round(in_track.lufs(), 2),
            "output_lufs": round(out_track.lufs(), 2),
            "input_true_peak": round(in_track.true_peak_dbtp(), 2),
            "output_true_peak": round(out_track.true_peak_dbtp(), 2)
        })

    def run(self, track: AudioTrack, blend_weights: Optional[List[float]] = None) -> AudioTrack:
        if self.mode == "serial":
            current = track
            for model in self.models:
                prev = current
                current = model.safe_process(current)
                self._log_step(model, prev, current)
            return current
        elif self.mode == "parallel":
            if not self.models:
                return track
            outputs = [m.safe_process(track) for m in self.models if m.enabled]
            if not outputs:
                return track
            if blend_weights is None:
                blend_weights = [1.0 / len(outputs)] * len(outputs)
            if len(blend_weights)!= len(outputs):
                raise ValueError("blend_weights length must match number of active models")
            blended = sum(out.samples * w for out, w in zip(outputs, blend_weights))
            result = AudioTrack(blended, track.sample_rate, track.bit_depth, track.metadata.copy())
            result.metadata["provenance"] = track.metadata.get("provenance", []) + \
                ["parallel_blend"] + [m.get_signature() for m in self.models]
            return result

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> 'MasteringSuite':
        suite = cls(mode=cfg.get("mode", "serial"))
        for m in cfg.get("models", []):
            model_cls = MODEL_REGISTRY.get(m["type"])
            if not model_cls:
                raise ValueError(f"Unknown model type: {m['type']}")
            suite.add_model(model_cls(m.get("name", m["type"]), m.get("config", {})))
        return suite

# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="AI Music Mastering Suite v6.1")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--chain", type=Path, help="JSON config for model chain")
    parser.add_argument("--mode", choices=["serial", "parallel"], default="serial")
    parser.add_argument("--target-lufs", type=float, default=-14.0)
    parser.add_argument("--ceiling", type=float, default=-1.0)
    parser.add_argument("--bits", type=int, default=24, choices=[16, 24, 32])
    parser.add_argument("--dither", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ab-export", type=Path, help="Export loudness-matched original for A/B")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--fail-on-clip", action="store_true", help="Exit 1 if final TP > ceiling")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                       format="%(name)s: %(message)s")

    try:
        track = AudioTrack.from_file(args.input)
        logging.info(f"Loaded: {track.samples.shape}, {track.sample_rate}Hz, {track.bit_depth}-bit")
        logging.info(f"Input: {track.lufs():.2f} LUFS, {track.true_peak_dbtp():.2f} dBTP")

        if args.chain:
            cfg = json.loads(args.chain.read_text())
            suite = MasteringSuite.from_config(cfg)
        else:
            suite = MasteringSuite(mode=args.mode)
            suite.add_model(GainModel("LUFS_Gain", {"target_lufs": args.target_lufs}))
            suite.add_model(TruePeakLimiter("TP_Limiter", {"ceiling_dbtp": args.ceiling}))

        master = suite.run(track)
        final_lufs = master.lufs()
        final_tp = master.true_peak_dbtp()
        logging.info(f"Output: {final_lufs:.2f} LUFS, {final_tp:.2f} dBTP")

        if args.fail_on_clip and final_tp > args.ceiling + 0.1:
            logging.error(f"Final true peak {final_tp:.2f} dBTP exceeds ceiling {args.ceiling:.2f}")
            return 1

        if args.ab_export:
            ab = track.loudness_match(final_lufs)
            ab.to_file(args.ab_export, dither=False, target_bits=args.bits)
            logging.info(f"A/B reference -> {args.ab_export} at {final_lufs:.2f} LUFS")

        if not args.dry_run:
            if not args.output:
                parser.error("--output required unless --dry-run")
            master.to_file(args.output, dither=args.dither, target_bits=args.bits)
            logging.info(f"Mastered -> {args.output}")

        if args.log:
            with open(args.log, 'w') as f:
                json.dump({
                    "input": {"hash": track.source_hash[:12], "lufs": round(track.lufs(), 2),
                             "true_peak": round(track.true_peak_dbtp(), 2), "sr": track.sample_rate},
                    "output": {"hash": master.compute_hash()[:12], "lufs": round(final_lufs, 2),
                              "true_peak": round(final_tp, 2)},
                    "chain": suite.processing_log,
                    "final_provenance": master.metadata["provenance"]
                }, f, indent=2)

    except Exception as e:
        logging.error(f"Processing failed: {e}", exc_info=args.verbose)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())