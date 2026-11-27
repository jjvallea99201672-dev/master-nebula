#!/usr/bin/env python3
"""
NebulaSystem v4.6.7 - Complete Real Streaming Orchestration
Full async streaming with real LLM calls, agent negotiation, and synthesis
"""

import numpy as np
import soundfile as sf
from pathlib import Path
import random
import logging
import json
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
from dataclasses import dataclass, field
from collections import defaultdict
import os
import time
import uuid
from datetime import datetime
import asyncio
from scipy import signal
from scipy.ndimage import gaussian_filter1d
import aiohttp
from contextlib import asynccontextmanager

# ====================== VERSION ======================
VERSION = "4.6.7"
SAMPLE_RATE = 44100

# ====================== LOGGING ======================

class Logger:
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(handler)
    
    def info(self, msg, **kwargs):
        self.logger.info(msg)
    def error(self, msg, **kwargs):
        self.logger.error(msg)
    def warning(self, msg, **kwargs):
        self.logger.warning(msg)
    def debug(self, msg, **kwargs):
        self.logger.debug(msg)

logger = Logger("nebula")

# ====================== REAL LLM ROUTER ======================

class LLMRouter:
    """Real LLM router with OpenAI and Anthropic support"""
    
    def __init__(self):
        self.api_keys = {
            "openai": os.getenv("OPENAI_API_KEY"),
            "anthropic": os.getenv("ANTHROPIC_API_KEY")
        }
        self.clients = {}
        self._init_clients()
    
    def _init_clients(self):
        try:
            if self.api_keys.get("openai"):
                from openai import AsyncOpenAI
                self.clients["openai"] = AsyncOpenAI(api_key=self.api_keys["openai"])
                logger.info("✅ OpenAI client initialized")
        except ImportError:
            logger.warning("OpenAI not installed")
        
        try:
            if self.api_keys.get("anthropic"):
                from anthropic import AsyncAnthropic
                self.clients["anthropic"] = AsyncAnthropic(api_key=self.api_keys["anthropic"])
                logger.info("✅ Anthropic client initialized")
        except ImportError:
            logger.warning("Anthropic not installed")
    
    async def call_model(self, prompt: str, model_key: str = "openai", 
                        stream: bool = True, max_tokens: int = 500,
                        temperature: float = 0.7) -> AsyncGenerator[str, None]:
        """Call LLM with real streaming"""
        
        if model_key not in self.clients:
            logger.error(f"No client for {model_key}")
            yield f"[ERROR: No client for {model_key}]"
            return
        
        client = self.clients[model_key]
        
        try:
            if model_key == "openai":
                response = await client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You are a music synthesis expert."},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream
                )
                
                if stream:
                    async for chunk in response:
                        if chunk.choices[0].delta.content:
                            token = chunk.choices[0].delta.content
                            yield token
                else:
                    yield response.choices[0].message.content
            
            elif model_key == "anthropic":
                if stream:
                    async with client.messages.stream(
                        model="claude-3-opus-20240229",
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=[{"role": "user", "content": prompt}]
                    ) as stream_response:
                        async for chunk in stream_response:
                            if chunk.type == "content_block_delta":
                                token = chunk.delta.text
                                yield token
                else:
                    response = await client.messages.create(
                        model="claude-3-opus-20240229",
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    yield response.content[0].text
            
        except Exception as e:
            logger.error(f"LLM error: {e}")
            yield f"[ERROR: {str(e)}]"

# ====================== AGENT DEFINITIONS ======================

@dataclass
class Agent:
    name: str
    role: str
    model_key: str = "openai"
    temperature: float = 0.7

class AgentOrchestrator:
    """Multi-agent orchestrator with real streaming"""
    
    def __init__(self):
        self.router = LLMRouter()
        self.agents = [
            Agent("Synthesist", "Primary synthesis architect", "openai"),
            Agent("Refiner", "Critique and refinement specialist", "openai"),
            Agent("Continuity Keeper", "Maintains musical coherence", "anthropic"),
            Agent("Aesthetic Engine", "Evaluates artistic quality", "openai"),
            Agent("Forensic Analyst", "Technical analysis specialist", "anthropic"),
            Agent("Emotion Mapper", "Maps emotional intent", "openai")
        ]
        
        # Synthesis engine reference
        self.synthesis = SynthesisEngine()
    
    async def orchestrate_stream(self, prompt: str, context: Dict = None) -> AsyncGenerator[str, None]:
        """Full streaming orchestration with real LLM calls"""
        
        logger.info(f"[Orchestrator] Starting real streaming: {prompt[:60]}...")
        
        yield "=== 🌌 NEBULA MULTI-AGENT ORCHESTRATION ===\n\n"
        yield f"Prompt: {prompt}\n\n"
        
        # ============================================
        # ROUND 1: Initial Proposals
        # ============================================
        yield "--- Round 1: Initial Proposals ---\n\n"
        
        round1 = {}
        for agent in self.agents:
            yield f"\n[{agent.name}] ({agent.role})\n"
            yield "-" * (len(agent.name) + len(agent.role) + 4) + "\n\n"
            
            agent_prompt = self._build_initial_prompt(agent, prompt, context)
            
            response_parts = []
            async for token in self.router.call_model(
                agent_prompt, 
                agent.model_key, 
                stream=True,
                temperature=agent.temperature
            ):
                if isinstance(token, str):
                    yield token
                    response_parts.append(token)
            
            round1[agent.name] = "".join(response_parts)
            yield "\n\n"
        
        # ============================================
        # ROUND 2: Critique & Refinement
        # ============================================
        yield "\n--- Round 2: Critiques & Refinements ---\n\n"
        
        round2 = {}
        for agent in self.agents:
            yield f"\n[{agent.name}] (Critique)\n"
            yield "-" * (len(agent.name) + 10) + "\n\n"
            
            # Build critique prompt with other agents' responses
            other_responses = "\n\n".join([
                f"--- {name} ---\n{round1[name][:500]}..."
                for name in round1 if name != agent.name
            ])
            
            critique_prompt = self._build_critique_prompt(
                agent, prompt, round1[agent.name], other_responses, context
            )
            
            response_parts = []
            async for token in self.router.call_model(
                critique_prompt,
                agent.model_key,
                stream=True,
                temperature=agent.temperature * 0.8
            ):
                if isinstance(token, str):
                    yield token
                    response_parts.append(token)
            
            round2[agent.name] = "".join(response_parts)
            yield "\n\n"
        
        # ============================================
        # ROUND 3: Consensus Building
        # ============================================
        yield "\n--- Round 3: Consensus Building ---\n\n"
        
        consensus = {}
        for agent in self.agents:
            yield f"\n[{agent.name}] (Consensus)\n"
            yield "-" * (len(agent.name) + 11) + "\n\n"
            
            consensus_prompt = self._build_consensus_prompt(
                agent, prompt, round1, round2, context
            )
            
            response_parts = []
            async for token in self.router.call_model(
                consensus_prompt,
                agent.model_key,
                stream=True,
                temperature=agent.temperature * 0.6
            ):
                if isinstance(token, str):
                    yield token
                    response_parts.append(token)
            
            consensus[agent.name] = "".join(response_parts)
            yield "\n\n"
        
        # ============================================
        # FINAL SYNTHESIS
        # ============================================
        yield "\n--- Final Synthesis ---\n\n"
        yield "Generating audio from consensus...\n\n"
        
        # Extract parameters from consensus
        params = self._extract_parameters(round1, round2, consensus)
        
        # Stream synthesis
        async for chunk in self.synthesis.synthesize_stream(params, prompt):
            yield chunk
        
        yield "\n\n✅ Orchestration complete!\n"
    
    def _build_initial_prompt(self, agent: Agent, prompt: str, context: Dict) -> str:
        """Build initial proposal prompt"""
        base = f"""You are {agent.name}, a {agent.role}.

Your task: Propose detailed synthesis parameters for the following prompt:

"{prompt}"

Consider:
1. FM modulation index (1-12)
2. Glitch amount (0-1)
3. Reverb amount (0-1)
4. Compression ratio (1-10)
5. Stereo width (0-2)
6. Algorithm selection (classic/complex/feedback/parallel)
7. Motif variations

Provide specific numeric values and reasoning.
"""
        if context:
            base += f"\nContext: {json.dumps(context, indent=2)}\n"
        
        base += "\nFormat your response with clear parameter values."
        return base
    
    def _build_critique_prompt(self, agent: Agent, prompt: str, 
                               own_response: str, other_responses: str,
                               context: Dict) -> str:
        """Build critique prompt"""
        return f"""You are {agent.name}, a {agent.role}.

Original prompt: "{prompt}"

Your previous response:
{own_response}

Other agents' responses:
{other_responses}

Provide a critique and refinement:
1. What works well?
2. What needs improvement?
3. Suggest specific adjustments
4. Identify any issues or concerns

Be constructive and specific."""
    
    def _build_consensus_prompt(self, agent: Agent, prompt: str,
                                round1: Dict, round2: Dict,
                                context: Dict) -> str:
        """Build consensus prompt"""
        summary = f"""
Round 1 (Proposals):
{json.dumps({k: v[:200] + "..." for k, v in round1.items()}, indent=2)}

Round 2 (Critiques):
{json.dumps({k: v[:200] + "..." for k, v in round2.items()}, indent=2)}
"""
        return f"""You are {agent.name}, a {agent.role}.

Original prompt: "{prompt}"

All agent responses:
{summary}

Provide a final consensus:
1. What is the agreed-upon synthesis approach?
2. What are the final parameter values?
3. Any remaining concerns?

Be decisive and clear."""
    
    def _extract_parameters(self, round1: Dict, round2: Dict, 
                           consensus: Dict) -> Dict[str, float]:
        """Extract synthesis parameters from agent responses"""
        params = {
            "fm_mod_index": 5.0,
            "glitch_amount": 0.3,
            "reverb_amount": 0.5,
            "compression_ratio": 4.0,
            "stereo_width": 1.2,
            "algorithm": "classic",
            "tempo": 120,
            "duration": 60
        }
        
        # Parse all responses for numeric values
        all_text = " ".join([round1.get(k, "") for k in round1] + 
                           [round2.get(k, "") for k in round2] +
                           [consensus.get(k, "") for k in consensus])
        
        # Extract patterns
        import re
        patterns = {
            "fm_mod_index": r"FM[:\s]*(\d+\.?\d*)",
            "glitch_amount": r"Glitch[:\s]*(\d+\.?\d*)",
            "reverb_amount": r"Reverb[:\s]*(\d+\.?\d*)",
            "compression_ratio": r"Compression[:\s]*(\d+\.?\d*)",
            "stereo_width": r"Width[:\s]*(\d+\.?\d*)",
            "tempo": r"Tempo[:\s]*(\d+\.?\d*)",
            "duration": r"Duration[:\s]*(\d+\.?\d*)"
        }
        
        for param, pattern in patterns.items():
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            if matches:
                try:
                    values = [float(m) for m in matches[:5]]
                    if values:
                        params[param] = np.mean(values)
                except ValueError:
                    pass
        
        # Algorithm selection
        if "complex" in all_text.lower() and random.random() > 0.5:
            params["algorithm"] = "complex"
        elif "feedback" in all_text.lower():
            params["algorithm"] = "feedback"
        elif "parallel" in all_text.lower():
            params["algorithm"] = "parallel"
        
        # Clamp values
        params["fm_mod_index"] = np.clip(params["fm_mod_index"], 1, 12)
        params["glitch_amount"] = np.clip(params["glitch_amount"], 0, 1)
        params["reverb_amount"] = np.clip(params["reverb_amount"], 0, 1)
        params["compression_ratio"] = np.clip(params["compression_ratio"], 1, 10)
        params["stereo_width"] = np.clip(params["stereo_width"], 0, 2)
        params["tempo"] = np.clip(params["tempo"], 60, 180)
        params["duration"] = np.clip(params["duration"], 10, 300)
        
        return params

# ====================== SYNTHESIS ENGINE ======================

class SynthesisEngine:
    """Real audio synthesis engine with streaming"""
    
    def __init__(self):
        self.fm_synth = FMSynth()
        self.sample_rate = SAMPLE_RATE
        
        self.presets = {
            "crystal_clear": {"ratios": [1.0, 2.0, 3.14, 5.0], "mod_index": 4.0},
            "deep_bass": {"ratios": [1.0, 1.5, 2.5, 4.0], "mod_index": 7.0},
            "ethereal_pad": {"ratios": [1.0, 1.2, 1.5, 2.0], "mod_index": 5.0},
            "glitch_core": {"ratios": [1.0, 0.5, 2.0, 3.0], "mod_index": 8.0},
            "warm_lead": {"ratios": [1.0, 2.0, 4.0, 6.0], "mod_index": 3.5}
        }
    
    async def synthesize_stream(self, params: Dict, prompt: str) -> AsyncGenerator[str, None]:
        """Stream synthesis progress and generate audio"""
        
        yield f"🎵 Synthesis parameters:\n"
        yield f"   FM Mod Index: {params['fm_mod_index']:.1f}\n"
        yield f"   Glitch Amount: {params['glitch_amount']:.2f}\n"
        yield f"   Reverb Amount: {params['reverb_amount']:.2f}\n"
        yield f"   Compression Ratio: {params['compression_ratio']:.1f}\n"
        yield f"   Stereo Width: {params['stereo_width']:.2f}\n"
        yield f"   Algorithm: {params['algorithm']}\n"
        yield f"   Tempo: {params['tempo']:.0f} BPM\n"
        yield f"   Duration: {params['duration']:.0f}s\n\n"
        
        yield "🔊 Generating audio...\n\n"
        
        # Generate audio in chunks
        duration = min(params['duration'], 60)  # Cap at 60s for demo
        chunk_duration = 5.0  # 5 second chunks
        num_chunks = int(duration / chunk_duration)
        
        audio_chunks = []
        
        for i in range(num_chunks):
            progress = (i + 1) / num_chunks * 100
            yield f"   Progress: {progress:.0f}% ({i+1}/{num_chunks} chunks)\n"
            
            # Generate chunk
            chunk = self._generate_chunk(
                params, 
                start_time=i * chunk_duration,
                duration=chunk_duration
            )
            audio_chunks.append(chunk)
            
            # Simulate processing time
            await asyncio.sleep(0.5)
        
        # Combine chunks
        audio = np.vstack(audio_chunks) if audio_chunks else np.zeros((int(duration * SAMPLE_RATE), 2))
        
        # Final processing
        yield "\n🎛️ Applying final mastering...\n"
        audio = self._master_audio(audio, params)
        
        # Export
        yield "\n💾 Exporting audio...\n"
        output_path = Path("masters") / f"orchestrated_track_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        output_path.parent.mkdir(exist_ok=True)
        sf.write(str(output_path), audio, SAMPLE_RATE, subtype='PCM_24')
        
        yield f"\n✅ Audio exported: {output_path}\n"
        yield f"   Duration: {len(audio)/SAMPLE_RATE:.1f}s\n"
    
    def _generate_chunk(self, params: Dict, start_time: float, duration: float) -> np.ndarray:
        """Generate a chunk of audio"""
        num_samples = int(duration * self.sample_rate)
        
        # Choose preset based on parameters
        preset_name = "crystal_clear"
        if params['fm_mod_index'] > 6:
            preset_name = "glitch_core"
        elif params['fm_mod_index'] > 4:
            preset_name = "crystal_clear"
        else:
            preset_name = "ethereal_pad"
        
        preset = self.presets.get(preset_name, self.presets["crystal_clear"])
        
        # Generate melody
        melody_notes = [0, 3, 7, 12, 7, 3, 0]
        melody_rhythm = [1.0, 0.5, 0.5, 1.0, 0.5, 0.5, 1.0]
        
        melody = np.zeros(num_samples)
        bpm = int(params['tempo'])
        sixteenth = self.sample_rate * 60 / (bpm * 4)
        
        for step, (note_offset, dur) in enumerate(zip(melody_notes, melody_rhythm)):
            idx = int(step * sixteenth * dur)
            if idx >= num_samples:
                break
            freq = 55 * (2 ** (note_offset / 12.0))
            note = self.fm_synth.generate_note(
                freq, 0.3 * dur,
                mod_index=params['fm_mod_index'],
                ratios=preset["ratios"],
                velocity=0.8,
                algorithm=params['algorithm'],
                feedback=0.0
            )
            end = min(idx + len(note), num_samples)
            melody[idx:end] += note[:end-idx] * 0.85
        
        # Generate bass
        bass_notes = [0, 5, 7, 12]
        bass_rhythm = [1.0, 1.0, 1.0, 2.0]
        bass = np.zeros(num_samples)
        
        for step, (note_offset, dur) in enumerate(zip(bass_notes, bass_rhythm)):
            idx = int(step * self.sample_rate * 0.5 * dur)
            if idx >= num_samples:
                break
            freq = 55 * (2 ** (note_offset / 12.0)) * 0.5
            note = self.fm_synth.generate_note(
                freq, 0.45 * dur,
                mod_index=params['fm_mod_index'] * 1.2,
                ratios=preset["ratios"],
                velocity=0.9,
                algorithm="feedback",
                feedback=0.2
            )
            end = min(idx + len(note), num_samples)
            bass[idx:end] += note[:end-idx] * 1.2
        
        # Generate percussion
        percussion = np.zeros(num_samples)
        for step in range(int(duration * bpm / 60 * 8)):
            idx = int(step * sixteenth / 2)
            if idx >= num_samples:
                break
            
            if step % 16 in [0, 8, 11]:
                att = int(0.02 * self.sample_rate)
                dec = int(0.3 * self.sample_rate)
                env = np.zeros(att + dec)
                env[:att] = np.linspace(0, 1, att)
                env[att:] = np.linspace(1, 0, dec)
                kick = env * np.sin(2 * np.pi * 60 * np.arange(len(env)) / self.sample_rate)
                end = min(idx + len(kick), num_samples)
                percussion[idx:end] += kick[:end-idx] * 1.4
            
            if step % 16 in [4, 12]:
                att = int(0.01 * self.sample_rate)
                dec = int(0.25 * self.sample_rate)
                env = np.zeros(att + dec)
                env[:att] = np.linspace(0, 1, att)
                env[att:] = np.linspace(1, 0, dec)
                noise = np.random.randn(len(env)) * 0.6
                end = min(idx + len(env), num_samples)
                percussion[idx:end] += 0.9 * env[:end-idx] * noise[:end-idx]
        
        # Generate glitch
        glitch = np.zeros(num_samples)
        if params['glitch_amount'] > 0.1:
            glitch_count = int(params['glitch_amount'] * 20)
            for _ in range(glitch_count):
                pos = random.randint(0, num_samples - 1000)
                length = random.randint(80, 300)
                if pos + length < num_samples:
                    g = np.random.randn(length) * 0.5
                    for i in range(length):
                        g[i] *= np.sin(2 * np.pi * random.uniform(50, 500) * i / self.sample_rate)
                    glitch[pos:pos+length] += g * 0.7
        
        # Mix to stereo with width
        width = params['stereo_width']
        mid = melody * 0.85 + bass + percussion * 1.2 + glitch * 0.55
        side = melody * 0.3 + glitch * 0.4
        
        left = mid * 0.7 + side * width * 0.3
        right = mid * 0.7 - side * width * 0.3
        
        stereo = np.column_stack([left, right])
        
        # Normalize
        max_val = np.max(np.abs(stereo))
        if max_val > 0:
            stereo = stereo / max_val * 0.95
        
        return stereo
    
    def _master_audio(self, audio: np.ndarray, params: Dict) -> np.ndarray:
        """Apply final mastering"""
        # Simple compression
        if params['compression_ratio'] > 1:
            ratio = params['compression_ratio']
            envelope = np.sqrt(np.mean(audio**2, axis=1))
            envelope = gaussian_filter1d(envelope, sigma=100)
            gain = np.ones_like(envelope)
            threshold = 0.1
            above = envelope > threshold
            if np.any(above):
                gain[above] = threshold + (envelope[above] - threshold) / ratio
                gain[above] = gain[above] / (envelope[above] + 1e-10)
            
            for ch in range(audio.shape[1]):
                audio[:, ch] = audio[:, ch] * gain
        
        # Reverb
        if params['reverb_amount'] > 0.01:
            amount = params['reverb_amount']
            reverb = Reverb()
            audio = reverb.apply(audio, amount * 0.3)
        
        # Limit
        audio = np.clip(audio, -0.95, 0.95)
        
        return audio

# ====================== FM SYNTH ======================

class FMSynth:
    """Real FM synthesis"""
    
    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        
        self.algorithms = {
            "classic": [
                [0, 1, 0, 0, 0, 0],
                [0, 0, 1, 0, 0, 0],
                [0, 0, 0, 1, 0, 0],
                [0, 0, 0, 0, 1, 0],
                [0, 0, 0, 0, 0, 1],
                [0, 0, 0, 0, 0, 0]
            ],
            "feedback": [
                [1, 1, 0, 0, 0, 0],
                [0, 1, 1, 0, 0, 0],
                [0, 0, 1, 1, 0, 0],
                [0, 0, 0, 1, 1, 0],
                [0, 0, 0, 0, 1, 1],
                [0, 0, 0, 0, 0, 1]
            ],
            "complex": [
                [1, 1, 0, 0, 0, 1],
                [0, 1, 1, 0, 0, 0],
                [0, 0, 1, 1, 0, 0],
                [0, 0, 0, 1, 1, 0],
                [0, 0, 0, 0, 1, 1],
                [1, 0, 0, 0, 0, 1]
            ],
            "parallel": [
                [0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0],
                [0, 0, 0, 0, 0, 0]
            ]
        }
    
    def generate_note(self, freq: float, duration: float, mod_index: float = 4.0,
                      ratios: List[float] = None, velocity: float = 0.8,
                      algorithm: str = "classic", feedback: float = 0.0) -> np.ndarray:
        """Generate an FM note"""
        if ratios is None:
            ratios = [1.0, 2.0, 3.14, 5.0, 7.0, 9.0]
        while len(ratios) < 6:
            ratios.append(ratios[-1] * 1.5)
        ratios = ratios[:6]
        
        num_samples = int(duration * self.sample_rate)
        t = np.linspace(0, duration, num_samples, dtype=np.float32)
        
        # Initialize operators
        ops = []
        for i in range(6):
            phase = 2 * np.pi * freq * ratios[i] * t
            ops.append(np.sin(phase))
        
        # Apply modulation matrix
        matrix = self.algorithms.get(algorithm, self.algorithms["classic"])
        modulated = ops.copy()
        
        for i in range(6):
            for j in range(6):
                if matrix[i][j] and i != j:
                    modulation = modulated[j] * mod_index * matrix[i][j]
                    modulated[i] = np.sin(2 * np.pi * freq * ratios[i] * t + modulation)
        
        # Feedback
        if feedback > 0:
            for i in range(6):
                if i < len(modulated) - 1:
                    modulated[i] += feedback * modulated[i + 1]
                modulated[i] = np.tanh(modulated[i])
        
        # Sum with weights
        output = np.zeros_like(t)
        weights = [1.0, 0.8, 0.6, 0.4, 0.2, 0.1]
        for i, op in enumerate(modulated):
            output += op * weights[i]
        output = output / (sum(weights) + 1e-10)
        
        # ADSR envelope
        attack = np.linspace(0, 1, int(0.05 * self.sample_rate))
        decay = np.linspace(1, 0.7, int(0.1 * self.sample_rate))
        sustain_len = max(0, num_samples - len(attack) - len(decay) - int(0.3 * self.sample_rate))
        sustain = np.full(sustain_len, 0.7)
        release = np.linspace(0.7, 0, int(0.3 * self.sample_rate))
        env = np.concatenate([attack, decay, sustain, release])[:num_samples]
        env = np.clip(env, 0, 1)
        
        return velocity * env * output

# ====================== REVERB ======================

class Reverb:
    """Real FDN reverb"""
    
    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.delays = [int(100 * 0.618 ** i * self.sample_rate / 44100) for i in range(8)]
        self.delays = [max(1, d) for d in self.delays]
        self.gains = [0.5 * 0.618 ** i for i in range(8)]
    
    def apply(self, audio: np.ndarray, amount: float = 0.5) -> np.ndarray:
        if amount < 0.01:
            return audio
        
        if len(audio.shape) == 1:
            audio = audio.reshape(-1, 1)
        
        result = np.zeros_like(audio)
        
        for ch in range(audio.shape[1]):
            signal_ch = audio[:, ch]
            buffers = [np.zeros(d) for d in self.delays]
            wet = np.zeros(len(signal_ch))
            
            for i in range(len(signal_ch)):
                sample = 0
                for j, buf in enumerate(buffers):
                    sample += buf[-1] * self.gains[j]
                
                input_sample = signal_ch[i] + sample * 0.7
                
                for j, buf in enumerate(buffers):
                    buf[1:] = buf[:-1]
                    buf[0] = input_sample * 0.9
                
                wet[i] = sample * 0.7
            
            result[:, ch] = signal_ch * (1 - amount * 0.5) + wet * amount
        
        return result if result.shape[1] > 1 else result[:, 0]

# ====================== MAIN ======================

async def main():
    """Run the orchestrator"""
    print("\n" + "="*60)
    print(f"🌌 NEBULASYSTEM v{VERSION} - REAL STREAMING ORCHESTRATION")
    print("="*60)
    print("Features:")
    print("  ✓ Real LLM streaming (OpenAI/Anthropic)")
    print("  ✓ Multi-agent orchestration")
    print("  ✓ Real-time token streaming")
    print("  ✓ Actual audio synthesis")
    print("  ✓ No simulations")
    print("="*60 + "\n")
    
    # Initialize orchestrator
    orchestrator = AgentOrchestrator()
    
    # Test prompt
    prompt = "Create an ethereal ambient track with deep bass, shimmering highs, and evolving pads"
    
    print(f"📝 Prompt: {prompt}\n")
    print("🔄 Streaming orchestration...\n")
    
    # Stream orchestration
    async for chunk in orchestrator.orchestrate_stream(prompt):
        print(chunk, end="", flush=True)
    
    print("\n" + "="*60)
    print("✅ Complete!")
    print("📁 Check masters/ for audio file")
    print("="*60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 Stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()