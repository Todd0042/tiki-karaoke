#!/usr/bin/env python3
"""
Tiki Karaoke - Procedural High-Energy BGM Generator
===================================================
Copyright (c) 2026 Todd / Tiki Karaoke Project
Licensed under the PolyForm Noncommercial License 1.0.0 (or Commercial License where applicable).

LEGAL & PROVENANCE NOTICE:
--------------------------
All 10 BGM tracks in `static/bgm/` are 100% procedurally synthesized from pure
mathematical digital signal processing (DSP), waveform oscillators, and physical modeling.
Harmonically inspired by the legendary "4 Chords of Pop/Dance" (I - V - vi - IV and vi - IV - I - V).

NO external audio samples, sound libraries, or copyrighted recordings were used.
This script serves as reproducible mathematical proof of original authorship
and clean-room provenance for commercial and non-commercial distribution.

To re-synthesize and master all 10 BGM tracks:
    python3 scripts/generate_bgm.py
"""

import os
import sys
import wave
import tempfile
import subprocess
import numpy as np
from scipy import signal
from pathlib import Path

SAMPLE_RATE = 44100
OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "bgm"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Note frequency mapping (12-TET relative to A4 = 440 Hz)
NOTE_OFFSETS = {
    'C': -9, 'C#': -8, 'Db': -8, 'D': -7, 'D#': -6, 'Eb': -6,
    'E': -5, 'F': -4, 'F#': -3, 'Gb': -3, 'G': -2, 'G#': -1,
    'Ab': -1, 'A': 0, 'A#': 1, 'Bb': 1, 'B': 2
}

def note_freq(name: str, octave: int = 4) -> float:
    semitones = NOTE_OFFSETS[name] + (octave - 4) * 12
    return 440.0 * (2.0 ** (semitones / 12.0))


class MusicEngine:
    """Vectorized procedural audio synthesizer for electronic dance music."""
    def __init__(self, duration_sec: float):
        self.dur = duration_sec
        self.n_samples = int(duration_sec * SAMPLE_RATE)
        self.t = np.linspace(0, duration_sec, self.n_samples, endpoint=False)
        self.left = np.zeros(self.n_samples, dtype=np.float32)
        self.right = np.zeros(self.n_samples, dtype=np.float32)
        self.kick_events = []

    def add_kick(self, t_sec: float, amp: float = 0.95):
        self.kick_events.append(t_sec)
        idx = int(t_sec * SAMPLE_RATE)
        k_dur = 0.18
        k_samples = int(k_dur * SAMPLE_RATE)
        if idx + k_samples >= self.n_samples:
            return
        t_k = np.linspace(0, k_dur, k_samples)
        # Fast exponential pitch sweep from 170Hz down to 48Hz
        freq = 48.0 + 122.0 * np.exp(-t_k * 45.0)
        phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
        sub = np.sin(phase) * np.exp(-t_k * 18.0)
        # Transient click at start
        click = np.random.normal(0, 0.4, k_samples) * np.exp(-t_k * 180.0)
        kick_sound = np.tanh(2.5 * (sub + click)) * amp
        self.left[idx:idx + k_samples] += kick_sound
        self.right[idx:idx + k_samples] += kick_sound

    def add_snare(self, t_sec: float, amp: float = 0.85):
        idx = int(t_sec * SAMPLE_RATE)
        s_dur = 0.22
        s_samples = int(s_dur * SAMPLE_RATE)
        if idx + s_samples >= self.n_samples:
            return
        t_s = np.linspace(0, s_dur, s_samples)
        # Snare body tone
        f_tone = 195.0 * np.exp(-t_s * 15.0)
        body = np.sin(np.cumsum(2 * np.pi * f_tone / SAMPLE_RATE)) * np.exp(-t_s * 25.0)
        # Noise snap
        noise = np.random.normal(0, 1.0, s_samples)
        b, a = signal.butter(2, [1400 / (SAMPLE_RATE / 2), 8000 / (SAMPLE_RATE / 2)], btype='band')
        snap = signal.lfilter(b, a, noise) * np.exp(-t_s * 22.0)
        snare_sound = (body * 0.45 + snap * 0.85) * amp
        self.left[idx:idx + s_samples] += snare_sound * 0.95
        self.right[idx:idx + s_samples] += snare_sound * 0.95

    def add_clap(self, t_sec: float, amp: float = 0.75):
        idx = int(t_sec * SAMPLE_RATE)
        c_dur = 0.25
        c_samples = int(c_dur * SAMPLE_RATE)
        if idx + c_samples >= self.n_samples:
            return
        # 3 micro-bursts followed by sustained tail
        clap_out = np.zeros(c_samples)
        b, a = signal.butter(2, [1000 / (SAMPLE_RATE / 2), 4500 / (SAMPLE_RATE / 2)], btype='band')
        for offset_ms in [0, 10, 22]:
            off_idx = int(offset_ms * 0.001 * SAMPLE_RATE)
            rem = c_samples - off_idx
            t_m = np.linspace(0, rem / SAMPLE_RATE, rem)
            burst = signal.lfilter(b, a, np.random.normal(0, 1.0, rem)) * np.exp(-t_m * 45.0)
            clap_out[off_idx:] += burst * 0.55
        self.left[idx:idx + c_samples] += clap_out * amp * 0.9
        self.right[idx:idx + c_samples] += clap_out * amp * 1.0

    def add_hihat(self, t_sec: float, amp: float = 0.35, open_hat: bool = False):
        idx = int(t_sec * SAMPLE_RATE)
        dur = 0.22 if open_hat else 0.045
        h_samples = int(dur * SAMPLE_RATE)
        if idx + h_samples >= self.n_samples:
            return
        t_h = np.linspace(0, dur, h_samples)
        # Metallic cluster + highpass noise
        noise = np.random.normal(0, 1.0, h_samples)
        b, a = signal.butter(2, 6500 / (SAMPLE_RATE / 2), btype='high')
        filtered = signal.lfilter(b, a, noise)
        decay = 14.0 if open_hat else 90.0
        hat = filtered * np.exp(-t_h * decay) * amp
        pan = 0.6 if open_hat else 0.4
        self.left[idx:idx + h_samples] += hat * (1.0 - pan)
        self.right[idx:idx + h_samples] += hat * pan

    def add_supersaw_chord(self, start_sec: float, dur_sec: float, freqs: list, amp: float = 0.18):
        """Rich detuned supersaw synth chord with stereo panning."""
        idx = int(start_sec * SAMPLE_RATE)
        n = int(dur_sec * SAMPLE_RATE)
        if idx + n >= self.n_samples:
            return
        t_ch = np.linspace(0, dur_sec, n)
        # Soft attack, steady sustain, clean release
        att = min(int(0.02 * SAMPLE_RATE), n // 4)
        rel = min(int(0.04 * SAMPLE_RATE), n // 4)
        env = np.ones(n)
        if att > 0:
            env[:att] = np.sin(np.linspace(0, np.pi / 2, att)) ** 2
        if rel > 0:
            env[-rel:] = np.cos(np.linspace(0, np.pi / 2, rel)) ** 2

        chord_l = np.zeros(n)
        chord_r = np.zeros(n)
        for f in freqs:
            for detune, p_offset in [(-0.012, 0.2), (-0.005, 0.4), (0.0, 0.5), (0.006, 0.7), (0.013, 0.9)]:
                f_det = f * (1.0 + detune)
                phase = 2 * np.pi * f_det * t_ch + p_offset
                # Band-limited sawtooth
                saw = 2.0 * (phase / (2 * np.pi) - np.floor(phase / (2 * np.pi) + 0.5))
                chord_l += saw * (1.0 - p_offset)
                chord_r += saw * p_offset

        chord_l = chord_l / (len(freqs) * 3.5) * env * amp
        chord_r = chord_r / (len(freqs) * 3.5) * env * amp
        self.left[idx:idx + n] += chord_l
        self.right[idx:idx + n] += chord_r

    def add_synth_pluck(self, start_sec: float, dur_sec: float, freq: float, amp: float = 0.22, pan: float = 0.5):
        """Bouncy, resonant pluck melody."""
        idx = int(start_sec * SAMPLE_RATE)
        n = int(dur_sec * SAMPLE_RATE)
        if idx + n >= self.n_samples:
            return
        t_p = np.linspace(0, dur_sec, n)
        env = np.exp(-t_p * 18.0) * min(1.0, 1.0)
        phase = 2 * np.pi * freq * t_p
        # Saw + square harmonic mix
        wave_val = 0.65 * (2.0 * (phase / (2 * np.pi) - np.floor(phase / (2 * np.pi) + 0.5))) + 0.35 * np.sign(np.sin(phase))
        # Swept lowpass filter for punchy bite
        b, a = signal.butter(2, [max(100, min(SAMPLE_RATE / 2 - 100, freq * 1.2)) / (SAMPLE_RATE / 2),
                                 max(200, min(SAMPLE_RATE / 2 - 100, freq * 4.5)) / (SAMPLE_RATE / 2)], btype='band')
        filtered = signal.lfilter(b, a, wave_val) * env * amp
        self.left[idx:idx + n] += filtered * (1.0 - pan)
        self.right[idx:idx + n] += filtered * pan

    def add_rolling_bass(self, start_sec: float, dur_sec: float, freq: float, amp: float = 0.38):
        """Deep driving EDM rolling bass with punchy sub."""
        idx = int(start_sec * SAMPLE_RATE)
        n = int(dur_sec * SAMPLE_RATE)
        if idx + n >= self.n_samples:
            return
        t_b = np.linspace(0, dur_sec, n)
        env = np.exp(-t_b * 7.5)
        phase = 2 * np.pi * freq * t_b
        # Clean sub fundamental + 2nd harmonic bite
        bass_sound = (0.75 * np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)) * env * amp
        self.left[idx:idx + n] += bass_sound
        self.right[idx:idx + n] += bass_sound

    def add_wobble_bass(self, start_sec: float, dur_sec: float, freq: float, lfo_hz: float = 4.0, amp: float = 0.42):
        """Dubstep style resonant wobble bass."""
        idx = int(start_sec * SAMPLE_RATE)
        n = int(dur_sec * SAMPLE_RATE)
        if idx + n >= self.n_samples:
            return
        t_w = np.linspace(0, dur_sec, n)
        phase = 2 * np.pi * freq * t_w
        raw_saw = 2.0 * (phase / (2 * np.pi) - np.floor(phase / (2 * np.pi) + 0.5))
        # Wobble LFO modulation
        wobble = 0.5 + 0.5 * np.sin(2 * np.pi * lfo_hz * t_w)
        sub = np.sin(phase) * 0.8
        wob = np.tanh(2.8 * (raw_saw * wobble + sub)) * amp
        self.left[idx:idx + n] += wob * 0.95
        self.right[idx:idx + n] += wob * 0.95

    def apply_sidechain_ducking(self, depth: float = 0.70, recovery_sec: float = 0.18):
        """Authentic club sidechain compression pumping on every kick drum hit."""
        if not self.kick_events:
            return
        sidechain_envelope = np.ones(self.n_samples, dtype=np.float32)
        rec_samples = int(recovery_sec * SAMPLE_RATE)
        curve = 1.0 - depth * np.exp(-np.linspace(0, 4.0, rec_samples))
        for k_t in self.kick_events:
            idx = int(k_t * SAMPLE_RATE)
            end = min(idx + rec_samples, self.n_samples)
            span = end - idx
            sidechain_envelope[idx:end] = np.minimum(sidechain_envelope[idx:end], curve[:span])
        self.left *= sidechain_envelope
        self.right *= sidechain_envelope

    def render_and_export(self, out_mp3_path: Path):
        peak = max(np.max(np.abs(self.left)), np.max(np.abs(self.right)))
        if peak > 0:
            self.left = (self.left / peak) * 0.88
            self.right = (self.right / peak) * 0.88
        audio_stereo = np.vstack([self.left, self.right]).T
        int16 = (audio_stereo * 32767).astype(np.int16)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_wav = tmp.name
            with wave.open(tmp_wav, "w") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(int16.tobytes())

        try:
            cmd = [
                "ffmpeg", "-y", "-i", tmp_wav,
                "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
                "-b:a", "192k", str(out_mp3_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        finally:
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)


# ==============================================================================
# TRACK GENERATOR FUNCTIONS (4 CHORDS POP & ELECTRONIC DANCE LINEUP)
# ==============================================================================

def generate_track_01_neon_highway(dur=40.0) -> MusicEngine:
    """Track 1: Synthwave / French Electro (124 BPM) - vi - IV - I - V (Am - F - C - G)"""
    bpm = 124.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('A', 3), note_freq('C', 4), note_freq('E', 4)], note_freq('A', 2)),
        ([note_freq('F', 3), note_freq('A', 3), note_freq('C', 4)], note_freq('F', 2)),
        ([note_freq('C', 4), note_freq('E', 4), note_freq('G', 4)], note_freq('C', 2)),
        ([note_freq('G', 3), note_freq('B', 3), note_freq('D', 4)], note_freq('G', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        # 4-on-the-floor kick & clap
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b)
            if beat_i in (1, 3):
                engine.add_clap(t_b)
            engine.add_hihat(t_b + beat * 0.5, open_hat=True)
            # 16th-note rolling bass
            for step in range(4):
                engine.add_rolling_bass(t_b + step * (beat / 4.0), beat / 4.5, root_f)

        # Supersaw chords with sidechain pumping
        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.22)

    engine.apply_sidechain_ducking(depth=0.75)
    return engine


def generate_track_02_sub_bass_lagoon(dur=42.0) -> MusicEngine:
    """Track 2: Melodic Dubstep / Chillstep (140 BPM, Half-Time feel) - Dm - Bb - F - C"""
    bpm = 140.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('D', 4), note_freq('F', 4), note_freq('A', 4)], note_freq('D', 2)),
        ([note_freq('Bb', 3), note_freq('D', 4), note_freq('F', 4)], note_freq('Bb', 1)),
        ([note_freq('F', 3), note_freq('A', 3), note_freq('C', 4)], note_freq('F', 1)),
        ([note_freq('C', 4), note_freq('E', 4), note_freq('G', 4)], note_freq('C', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        # Half-time dubstep drum beat: Kick on 1, Snare on 3
        engine.add_kick(bar_t)
        engine.add_snare(bar_t + 2 * beat, amp=0.95)
        engine.add_hihat(bar_t + beat, amp=0.25)
        engine.add_hihat(bar_t + 3 * beat, amp=0.35, open_hat=True)

        # Dubstep Wobble Bass (4 Hz & 8 Hz triplets)
        lfo = 4.0 if (b % 2 == 0) else 8.0
        engine.add_wobble_bass(bar_t, bar * 0.95, root_f, lfo_hz=lfo, amp=0.45)
        # Lush chord wash
        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.18)

    engine.apply_sidechain_ducking(depth=0.65)
    return engine


def generate_track_03_electric_sunset(dur=39.0) -> MusicEngine:
    """Track 3: Tech House / Club Groove (126 BPM) - Em - C - G - D"""
    bpm = 126.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('E', 4), note_freq('G', 4), note_freq('B', 4)], note_freq('E', 2)),
        ([note_freq('C', 4), note_freq('E', 4), note_freq('G', 4)], note_freq('C', 2)),
        ([note_freq('G', 3), note_freq('B', 3), note_freq('D', 4)], note_freq('G', 2)),
        ([note_freq('D', 4), note_freq('F#', 4), note_freq('A', 4)], note_freq('D', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b)
            if beat_i in (1, 3):
                engine.add_clap(t_b)
            # Driving offbeat open hat
            engine.add_hihat(t_b + beat * 0.5, open_hat=True, amp=0.40)
            engine.add_hihat(t_b + beat * 0.25, open_hat=False, amp=0.18)
            # Tech-house offbeat bass stab
            engine.add_rolling_bass(t_b + beat * 0.5, beat * 0.45, root_f, amp=0.45)

        # Funky short synth stabs
        engine.add_synth_pluck(bar_t + 0.75 * beat, 0.25 * beat, chord_freqs[0] * 1.5, amp=0.25)
        engine.add_synth_pluck(bar_t + 2.75 * beat, 0.25 * beat, chord_freqs[1] * 1.5, amp=0.25)

    engine.apply_sidechain_ducking(depth=0.80)
    return engine


def generate_track_04_liquid_velocity(dur=40.0) -> MusicEngine:
    """Track 4: Liquid Drum & Bass (174 BPM) - F - C - Dm - Bb"""
    bpm = 174.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('F', 4), note_freq('A', 4), note_freq('C', 5)], note_freq('F', 2)),
        ([note_freq('C', 4), note_freq('E', 4), note_freq('G', 4)], note_freq('C', 2)),
        ([note_freq('D', 4), note_freq('F', 4), note_freq('A', 4)], note_freq('D', 2)),
        ([note_freq('Bb', 3), note_freq('D', 4), note_freq('F', 4)], note_freq('Bb', 1)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        # Fast rolling D&B breakbeat: Kick on 1 & 2.75, Snare on 2 & 4
        engine.add_kick(bar_t)
        engine.add_snare(bar_t + beat)
        engine.add_kick(bar_t + beat * 2.5)
        engine.add_snare(bar_t + beat * 3.0)
        # Fast 16th-note rolling hihats
        for step in range(8):
            engine.add_hihat(bar_t + step * (beat * 0.5), amp=0.25)

        # Deep liquid Reese sub-bass
        engine.add_wobble_bass(bar_t, bar * 0.95, root_f, lfo_hz=2.0, amp=0.45)
        # Soaring liquid atmospheric pads
        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.16)

    return engine


def generate_track_05_tiki_disco_fever(dur=38.0) -> MusicEngine:
    """Track 5: Nu-Disco / Funk Electro (120 BPM) - G - D - Em - C"""
    bpm = 120.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('G', 3), note_freq('B', 3), note_freq('D', 4), note_freq('F#', 4)], note_freq('G', 2)),
        ([note_freq('D', 3), note_freq('F#', 3), note_freq('A', 3), note_freq('C', 4)], note_freq('D', 2)),
        ([note_freq('E', 3), note_freq('G', 3), note_freq('B', 3), note_freq('D', 4)], note_freq('E', 2)),
        ([note_freq('C', 3), note_freq('E', 3), note_freq('G', 3), note_freq('B', 3)], note_freq('C', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b)
            if beat_i in (1, 3):
                engine.add_snare(t_b)
                engine.add_clap(t_b, amp=0.5)
            engine.add_hihat(t_b + beat * 0.5, open_hat=True, amp=0.35)

        # Funky slapped octaves bassline
        for step, oct_mul in [(0.0, 1.0), (0.75, 2.0), (1.5, 1.0), (2.25, 2.0), (3.0, 1.0), (3.5, 2.0)]:
            engine.add_synth_pluck(bar_t + step * beat, beat * 0.45, root_f * oct_mul, amp=0.35)

        # Disco chord stabs on the upbeats
        for step in [0.5, 1.5, 2.5, 3.5]:
            engine.add_supersaw_chord(bar_t + step * beat, beat * 0.4, chord_freqs, amp=0.20)

    engine.apply_sidechain_ducking(depth=0.60)
    return engine


def generate_track_06_cyber_glitch(dur=41.0) -> MusicEngine:
    """Track 6: Midtempo Bass / Cyberpunk (105 BPM) - Cm - Ab - Eb - Bb"""
    bpm = 105.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('C', 3), note_freq('Eb', 3), note_freq('G', 3)], note_freq('C', 2)),
        ([note_freq('Ab', 2), note_freq('C', 3), note_freq('Eb', 3)], note_freq('Ab', 1)),
        ([note_freq('Eb', 3), note_freq('G', 3), note_freq('Bb', 3)], note_freq('Eb', 2)),
        ([note_freq('Bb', 2), note_freq('D', 3), note_freq('F', 3)], note_freq('Bb', 1)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b, amp=1.0)
            if beat_i in (1, 3):
                engine.add_snare(t_b, amp=0.95)
            engine.add_hihat(t_b + beat * 0.5, amp=0.25)

        # Heavy industrial cybernetic bass
        engine.add_wobble_bass(bar_t, bar * 0.95, root_f, lfo_hz=3.0, amp=0.48)
        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.18)

    engine.apply_sidechain_ducking(depth=0.85)
    return engine


def generate_track_07_midnight_bounce(dur=40.0) -> MusicEngine:
    """Track 7: Future Bounce / Festival EDM (128 BPM) - F#m - D - A - E"""
    bpm = 128.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('F#', 4), note_freq('A', 4), note_freq('C#', 5)], note_freq('F#', 2)),
        ([note_freq('D', 4), note_freq('F#', 4), note_freq('A', 4)], note_freq('D', 2)),
        ([note_freq('A', 3), note_freq('C#', 4), note_freq('E', 4)], note_freq('A', 2)),
        ([note_freq('E', 4), note_freq('G#', 4), note_freq('B', 4)], note_freq('E', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b)
            if beat_i in (1, 3):
                engine.add_clap(t_b)
            engine.add_hihat(t_b + beat * 0.5, open_hat=True, amp=0.35)

        # Bouncy metallic pluck bassline
        for step in range(8):
            t_s = bar_t + step * (beat * 0.5)
            pitch = root_f * (2.0 if (step % 2 == 1) else 1.0)
            engine.add_synth_pluck(t_s, beat * 0.35, pitch, amp=0.38)

        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.22)

    engine.apply_sidechain_ducking(depth=0.80)
    return engine


def generate_track_08_hyper_tropic(dur=40.0) -> MusicEngine:
    """Track 8: Upbeat Tropical Dance (122 BPM) - D - A - Bm - G"""
    bpm = 122.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('D', 4), note_freq('F#', 4), note_freq('A', 4)], note_freq('D', 2)),
        ([note_freq('A', 3), note_freq('C#', 4), note_freq('E', 4)], note_freq('A', 2)),
        ([note_freq('B', 3), note_freq('D', 4), note_freq('F#', 4)], note_freq('B', 1)),
        ([note_freq('G', 3), note_freq('B', 3), note_freq('D', 4)], note_freq('G', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b)
            if beat_i in (1, 3):
                engine.add_clap(t_b)
            engine.add_hihat(t_b + beat * 0.5, amp=0.30)

        # Tropical marimba melodic hook
        lead_notes = [chord_freqs[0] * 2.0, chord_freqs[1] * 2.0, chord_freqs[2] * 2.0, chord_freqs[0] * 2.5]
        for idx_l, step in enumerate([0.0, 0.75, 1.5, 2.25, 3.0]):
            engine.add_synth_pluck(bar_t + step * beat, beat * 0.5, lead_notes[idx_l % len(lead_notes)], amp=0.28)

        engine.add_rolling_bass(bar_t, bar * 0.95, root_f, amp=0.38)
        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.18)

    engine.apply_sidechain_ducking(depth=0.65)
    return engine


def generate_track_09_acid_beat_express(dur=40.0) -> MusicEngine:
    """Track 9: 90s Big Beat / Chemical Breaks (132 BPM) - Em - G - C - D"""
    bpm = 132.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('E', 3), note_freq('G', 3), note_freq('B', 3)], note_freq('E', 2)),
        ([note_freq('G', 3), note_freq('B', 3), note_freq('D', 4)], note_freq('G', 2)),
        ([note_freq('C', 3), note_freq('E', 3), note_freq('G', 3)], note_freq('C', 2)),
        ([note_freq('D', 3), note_freq('F#', 3), note_freq('A', 3)], note_freq('D', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        _, root_f = chords[b % 4]
        # Funky syncopated big beat break
        engine.add_kick(bar_t)
        engine.add_snare(bar_t + beat)
        engine.add_kick(bar_t + beat * 2.25)
        engine.add_snare(bar_t + beat * 3.0)
        for s in range(8):
            engine.add_hihat(bar_t + s * (beat * 0.5), amp=0.22)

        # Squelchy 303 acid bassline pattern
        for step in range(8):
            pitch = root_f * (1.5 if step in (3, 6) else 1.0)
            engine.add_synth_pluck(bar_t + step * (beat * 0.5), beat * 0.45, pitch, amp=0.42)

    return engine


def generate_track_10_starlight_euphoria(dur=42.0) -> MusicEngine:
    """Track 10: Progressive Trance Anthem (136 BPM) - Bb - F - C - Dm (IV - I - V - vi)"""
    bpm = 136.0
    beat = 60.0 / bpm
    bar = beat * 4.0
    engine = MusicEngine(dur)
    n_bars = int(dur / bar)

    chords = [
        ([note_freq('Bb', 3), note_freq('D', 4), note_freq('F', 4)], note_freq('Bb', 1)),
        ([note_freq('F', 3), note_freq('A', 3), note_freq('C', 4)], note_freq('F', 1)),
        ([note_freq('C', 4), note_freq('E', 4), note_freq('G', 4)], note_freq('C', 2)),
        ([note_freq('D', 4), note_freq('F', 4), note_freq('A', 4)], note_freq('D', 2)),
    ]

    for b in range(n_bars):
        bar_t = b * bar
        chord_freqs, root_f = chords[b % 4]
        for beat_i in range(4):
            t_b = bar_t + beat_i * beat
            engine.add_kick(t_b)
            if beat_i in (1, 3):
                engine.add_clap(t_b)
            engine.add_hihat(t_b + beat * 0.5, open_hat=True, amp=0.35)
            # Driving 16th-note rolling trance bass
            for step in range(4):
                engine.add_rolling_bass(t_b + step * (beat / 4.0), beat / 4.2, root_f, amp=0.40)

        # Euphoric soaring supersaw progression
        engine.add_supersaw_chord(bar_t, bar * 0.95, chord_freqs, amp=0.25)

    engine.apply_sidechain_ducking(depth=0.85)
    return engine


def main():
    print("🌴 Synthesizing 10 High-Energy Electronic & Pop 4-Chord BGM Tracks...")
    generators = [
        ("01_neon_highway", generate_track_01_neon_highway),
        ("02_sub_bass_lagoon", generate_track_02_sub_bass_lagoon),
        ("03_electric_sunset", generate_track_03_electric_sunset),
        ("04_liquid_velocity", generate_track_04_liquid_velocity),
        ("05_tiki_disco_fever", generate_track_05_tiki_disco_fever),
        ("06_cyber_glitch", generate_track_06_cyber_glitch),
        ("07_midnight_bounce", generate_track_07_midnight_bounce),
        ("08_hyper_tropic", generate_track_08_hyper_tropic),
        ("09_acid_beat_express", generate_track_09_acid_beat_express),
        ("10_starlight_euphoria", generate_track_10_starlight_euphoria),
    ]

    for filename, gen_fn in generators:
        mp3_path = OUT_DIR / f"{filename}.mp3"
        print(f"  🎶 Generating {filename}...")
        engine = gen_fn()
        engine.render_and_export(mp3_path)
        size_kb = os.path.getsize(mp3_path) / 1024
        print(f"  ✓ Mastered: {mp3_path.name} ({size_kb:.1f} KB, -16 LUFS 192kbps stereo)")

    print("\n✨ All 10 High-Energy BGM tracks successfully generated into static/bgm/!")


if __name__ == "__main__":
    main()
