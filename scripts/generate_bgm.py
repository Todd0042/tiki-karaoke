#!/usr/bin/env python3
"""
Generates procedural tropical lounge background music (BGM) tracks for Tiki Karaoke.
Uses pure Python standard library (wave, math, struct, random) and ffmpeg to encode to MP3.
"""

import os
import math
import wave
import struct
import random
import subprocess
from pathlib import Path

SAMPLE_RATE = 44100
OUT_DIR = Path(__file__).resolve().parent.parent / "static" / "bgm"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def note_freq(name, octave=4):
    notes = {'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
             'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8,
             'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11}
    semitones = notes[name] + (octave - 4) * 12 - 9  # relative to A4 (440Hz)
    return 440.0 * (2.0 ** (semitones / 12.0))

class SynthTrack:
    def __init__(self, duration_sec):
        self.num_samples = int(duration_sec * SAMPLE_RATE)
        self.left = [0.0] * self.num_samples
        self.right = [0.0] * self.num_samples

    def add_tone(self, start_sec, dur_sec, freq, amp=0.2, instrument="marimba", pan=0.0):
        start_idx = int(start_sec * SAMPLE_RATE)
        length = int(dur_sec * SAMPLE_RATE)
        end_idx = min(start_idx + length, self.num_samples)
        
        # Pan: -1 (left) to +1 (right)
        left_gain = math.cos((pan + 1.0) * math.pi / 4.0)
        right_gain = math.sin((pan + 1.0) * math.pi / 4.0)

        for i in range(start_idx, end_idx):
            t = (i - start_idx) / SAMPLE_RATE
            progress = t / dur_sec
            
            if instrument == "marimba":
                # Warm woody percussion: fundamental + inharmonic upper bells + quick exponential decay
                env = math.exp(-t * 5.5) * min(1.0, t * 120.0)
                val = (math.sin(2.0 * math.pi * freq * t) * 0.7 +
                       math.sin(2.0 * math.pi * freq * 2.75 * t) * 0.2 +
                       math.sin(2.0 * math.pi * freq * 4.05 * t) * 0.1)
            elif instrument == "pad":
                # Lush warm synth pad: soft swell, detuned chorused sines
                env = math.sin(math.pi * progress) ** 1.5
                detune1 = freq * 1.004
                detune2 = freq * 0.996
                val = (math.sin(2.0 * math.pi * freq * t) * 0.45 +
                       math.sin(2.0 * math.pi * detune1 * t) * 0.28 +
                       math.sin(2.0 * math.pi * detune2 * t) * 0.27)
            elif instrument == "rhodes":
                # Warm bell-like electric piano
                env = math.exp(-t * 2.5) * min(1.0, t * 80.0)
                val = (math.sin(2.0 * math.pi * freq * t) * 0.65 +
                       math.sin(2.0 * math.pi * freq * 2.0 * t) * 0.22 +
                       math.sin(2.0 * math.pi * freq * 3.0 * t) * 0.13)
            elif instrument == "bass":
                # Deep sub-bass with gentle release
                env = math.exp(-t * 1.8) * min(1.0, t * 50.0)
                val = (math.sin(2.0 * math.pi * freq * t) * 0.8 +
                       math.sin(2.0 * math.pi * freq * 2.0 * t) * 0.2)
            else:
                env = math.exp(-t * 4.0)
                val = math.sin(2.0 * math.pi * freq * t)

            sample_val = val * env * amp
            self.left[i] += sample_val * left_gain
            self.right[i] += sample_val * right_gain

    def add_ocean_swell(self, amp=0.04):
        # Pinkish noise modulated by slow gentle sine wave
        last_n = 0.0
        for i in range(self.num_samples):
            t = i / SAMPLE_RATE
            white = random.uniform(-1.0, 1.0)
            last_n = last_n * 0.95 + white * 0.05
            swell = (math.sin(2.0 * math.pi * 0.12 * t) * 0.5 + 0.5) ** 1.8
            val = last_n * swell * amp
            self.left[i] += val * 0.9
            self.right[i] += val * 1.0

    def add_shaker(self, start_sec, amp=0.03, pan=0.3):
        start_idx = int(start_sec * SAMPLE_RATE)
        dur = 0.08
        length = int(dur * SAMPLE_RATE)
        end_idx = min(start_idx + length, self.num_samples)
        left_gain = math.cos((pan + 1.0) * math.pi / 4.0)
        right_gain = math.sin((pan + 1.0) * math.pi / 4.0)
        last_n = 0.0
        for i in range(start_idx, end_idx):
            t = (i - start_idx) / SAMPLE_RATE
            env = math.exp(-t * 35.0)
            white = random.uniform(-1.0, 1.0)
            last_n = white - last_n * 0.6  # High-pass filter
            val = last_n * env * amp
            self.left[i] += val * left_gain
            self.right[i] += val * right_gain

    def export(self, wav_path: Path, mp3_path: Path):
        # Normalize to peak amplitude around 0.88 to avoid clipping
        max_peak = max(max(abs(s) for s in self.left), max(abs(s) for s in self.right), 0.001)
        scale = 0.88 / max_peak if max_peak > 0.88 else 1.0

        with wave.open(str(wav_path), 'wb') as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            frames = bytearray()
            for i in range(self.num_samples):
                l = int(max(-32767, min(32767, self.left[i] * scale * 32767)))
                r = int(max(-32767, min(32767, self.right[i] * scale * 32767)))
                frames.extend(struct.pack('<hh', l, r))
            wf.writeframes(frames)

        # Convert to high quality MP3 via ffmpeg
        subprocess.run([
            "ffmpeg", "-y", "-i", str(wav_path),
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(mp3_path)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if wav_path.exists():
            wav_path.unlink()

def generate_track_1():
    # 01_tiki_breeze: Key of C Major, 76 BPM
    # Chords: Cmaj7 (C-E-G-B), Am7 (A-C-E-G), Fmaj7 (F-A-C-E), G7 (G-B-D-F)
    bpm = 76
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8  # ~25.2 seconds seamless loop
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.04)

    chords = [
        ([('C', 4), ('E', 4), ('G', 4), ('B', 4)], ('C', 2)),
        ([('A', 3), ('C', 4), ('E', 4), ('G', 4)], ('A', 1)),
        ([('F', 3), ('A', 3), ('C', 4), ('E', 4)], ('F', 1)),
        ([('G', 3), ('B', 3), ('D', 4), ('F', 4)], ('G', 1)),
    ] * 2

    # Melodic notes for marimba
    melody_scale = [('C', 5), ('D', 5), ('E', 5), ('G', 5), ('A', 5), ('C', 6)]

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        # Ambient Pad chord
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 1.05, note_freq(note, oct), amp=0.10, instrument="pad", pan=random.uniform(-0.4, 0.4))
        # Bass
        track.add_tone(bar_start, bar_dur * 0.9, note_freq(bass_note[0], bass_note[1]), amp=0.25, instrument="bass", pan=0.0)
        track.add_tone(bar_start + bar_dur * 0.5, bar_dur * 0.45, note_freq(bass_note[0], bass_note[1]), amp=0.18, instrument="bass", pan=0.0)

        # Marimba Arpeggio & Melody
        for b in range(4):
            beat_time = bar_start + b * beat_dur
            track.add_shaker(beat_time, amp=0.035, pan=0.4)
            track.add_shaker(beat_time + beat_dur * 0.5, amp=0.025, pan=-0.4)

            # Play harmonic marimba notes
            m_note, m_oct = chord[b % len(chord)]
            track.add_tone(beat_time, beat_dur * 0.8, note_freq(m_note, m_oct + 1), amp=0.13, instrument="marimba", pan=-0.3)
            if random.random() > 0.4:
                lead_n, lead_o = random.choice(melody_scale)
                track.add_tone(beat_time + beat_dur * 0.5, beat_dur * 0.6, note_freq(lead_n, lead_o), amp=0.11, instrument="marimba", pan=0.3)

    return track

def generate_track_2():
    # 02_sunset_lagoon: Key of F Major, 72 BPM
    # Chords: Fmaj7, Dm7, Gm7, C7
    bpm = 72
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.035)

    chords = [
        ([('F', 3), ('A', 3), ('C', 4), ('E', 4)], ('F', 1)),
        ([('D', 3), ('F', 3), ('A', 3), ('C', 4)], ('D', 1)),
        ([('G', 3), ('Bb', 3), ('D', 4), ('F', 4)], ('G', 1)),
        ([('C', 3), ('E', 3), ('G', 3), ('Bb', 3)], ('C', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        # Warm Rhodes electric piano chords
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 0.9, note_freq(note, oct), amp=0.12, instrument="rhodes", pan=random.uniform(-0.35, 0.35))
            track.add_tone(bar_start + bar_dur * 0.5, bar_dur * 0.45, note_freq(note, oct), amp=0.09, instrument="rhodes", pan=random.uniform(-0.35, 0.35))

        # Bass line
        track.add_tone(bar_start, bar_dur * 0.45, note_freq(bass_note[0], bass_note[1]), amp=0.24, instrument="bass")
        track.add_tone(bar_start + bar_dur * 0.5, bar_dur * 0.45, note_freq(chord[2][0], bass_note[1]), amp=0.20, instrument="bass")

        # Shakers & Chimes
        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.03, pan=0.3)
            track.add_shaker(t + beat_dur * 0.5, amp=0.02, pan=-0.3)
            if b in (1, 3):
                lead = random.choice([('A', 4), ('C', 5), ('E', 5), ('F', 5)])
                track.add_tone(t + beat_dur * 0.25, beat_dur * 0.7, note_freq(lead[0], lead[1]), amp=0.08, instrument="marimba", pan=0.25)

    return track

def generate_track_3():
    # 03_palm_cabana: Key of G Major, 80 BPM
    # Chords: Gmaj7, Em7, Cmaj7, D9
    bpm = 80
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.03)

    chords = [
        ([('G', 3), ('B', 3), ('D', 4), ('F#', 4)], ('G', 1)),
        ([('E', 3), ('G', 3), ('B', 3), ('D', 4)], ('E', 1)),
        ([('C', 3), ('E', 3), ('G', 3), ('B', 3)], ('C', 1)),
        ([('D', 3), ('F#', 3), ('A', 3), ('C', 4)], ('D', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        # Lush background pad
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 1.05, note_freq(note, oct), amp=0.09, instrument="pad", pan=random.uniform(-0.5, 0.5))

        # Walking bassline
        track.add_tone(bar_start, beat_dur * 1.8, note_freq(bass_note[0], bass_note[1]), amp=0.24, instrument="bass")
        track.add_tone(bar_start + beat_dur * 2, beat_dur * 1.8, note_freq(chord[1][0], bass_note[1]), amp=0.22, instrument="bass")

        # Kalimba melody notes
        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.03, pan=0.35)
            track.add_shaker(t + beat_dur * 0.5, amp=0.02, pan=-0.35)
            k_note = random.choice([('G', 5), ('B', 5), ('D', 6), ('E', 6)])
            track.add_tone(t, beat_dur * 0.8, note_freq(k_note[0], k_note[1]), amp=0.12, instrument="marimba", pan=random.uniform(-0.4, 0.4))

    return track

def generate_track_4():
    # 04_mai_tai_lounge: Key of D Major, 74 BPM
    bpm = 74
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.04)

    chords = [
        ([('D', 3), ('F#', 3), ('A', 3), ('C#', 4)], ('D', 1)),
        ([('B', 2), ('D', 3), ('F#', 3), ('A', 3)], ('B', 1)),
        ([('G', 2), ('B', 2), ('D', 3), ('F#', 3)], ('G', 1)),
        ([('A', 2), ('C#', 3), ('E', 3), ('G', 3)], ('A', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 0.9, note_freq(note, oct), amp=0.11, instrument="rhodes", pan=random.uniform(-0.4, 0.4))
        track.add_tone(bar_start, bar_dur * 0.8, note_freq(bass_note[0], bass_note[1]), amp=0.23, instrument="bass")

        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.03)
            track.add_shaker(t + beat_dur * 0.5, amp=0.025)
            if b % 2 == 0:
                v_note = random.choice([('F#', 5), ('A', 5), ('C#', 6), ('D', 6)])
                track.add_tone(t + beat_dur * 0.25, beat_dur * 0.7, note_freq(v_note[0], v_note[1]), amp=0.10, instrument="marimba")

    return track

def generate_track_5():
    # 05_coral_drift: Key of Eb Major, 68 BPM (Slow chillout wave)
    bpm = 68
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.05)

    chords = [
        ([('Eb', 3), ('G', 3), ('Bb', 3), ('D', 4)], ('Eb', 1)),
        ([('C', 3), ('Eb', 3), ('G', 3), ('Bb', 3)], ('C', 1)),
        ([('Ab', 2), ('C', 3), ('Eb', 3), ('G', 3)], ('Ab', 1)),
        ([('Bb', 2), ('D', 3), ('F', 3), ('Ab', 3)], ('Bb', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 1.1, note_freq(note, oct), amp=0.10, instrument="pad", pan=random.uniform(-0.5, 0.5))
        track.add_tone(bar_start, bar_dur * 0.9, note_freq(bass_note[0], bass_note[1]), amp=0.24, instrument="bass")

        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.025)
            c_note = random.choice([('Bb', 4), ('D', 5), ('Eb', 5), ('G', 5)])
            track.add_tone(t, beat_dur * 0.9, note_freq(c_note[0], c_note[1]), amp=0.08, instrument="rhodes", pan=random.uniform(-0.3, 0.3))

    return track

def generate_track_6():
    # 06_neon_cocktail: Key of Bb Major, 78 BPM
    bpm = 78
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.035)

    chords = [
        ([('Bb', 3), ('D', 4), ('F', 4), ('A', 4)], ('Bb', 1)),
        ([('G', 3), ('Bb', 3), ('D', 4), ('F', 4)], ('G', 1)),
        ([('Eb', 3), ('G', 3), ('Bb', 3), ('D', 4)], ('Eb', 1)),
        ([('F', 3), ('A', 3), ('C', 4), ('Eb', 4)], ('F', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 0.95, note_freq(note, oct), amp=0.11, instrument="rhodes", pan=random.uniform(-0.4, 0.4))
        track.add_tone(bar_start, bar_dur * 0.5, note_freq(bass_note[0], bass_note[1]), amp=0.23, instrument="bass")
        track.add_tone(bar_start + bar_dur * 0.5, bar_dur * 0.45, note_freq(bass_note[0], bass_note[1]), amp=0.19, instrument="bass")

        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.03, pan=0.3)
            track.add_shaker(t + beat_dur * 0.5, amp=0.02, pan=-0.3)
            m_note = random.choice([('D', 5), ('F', 5), ('A', 5), ('Bb', 5)])
            track.add_tone(t + beat_dur * 0.25, beat_dur * 0.7, note_freq(m_note[0], m_note[1]), amp=0.10, instrument="marimba", pan=-0.2)

    return track

def generate_track_7():
    # 07_aloha_twilight: Key of A Major, 70 BPM (Gentle island acoustic)
    bpm = 70
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.045)

    chords = [
        ([('A', 3), ('C#', 4), ('E', 4), ('G#', 4)], ('A', 1)),
        ([('F#', 3), ('A', 3), ('C#', 4), ('E', 4)], ('F#', 1)),
        ([('D', 3), ('F#', 3), ('A', 3), ('C#', 4)], ('D', 1)),
        ([('E', 3), ('G#', 3), ('B', 3), ('D', 4)], ('E', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 1.1, note_freq(note, oct), amp=0.09, instrument="pad", pan=random.uniform(-0.4, 0.4))
        track.add_tone(bar_start, bar_dur * 0.85, note_freq(bass_note[0], bass_note[1]), amp=0.24, instrument="bass")

        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.025)
            lead = random.choice([('C#', 5), ('E', 5), ('G#', 5), ('A', 5)])
            track.add_tone(t, beat_dur * 0.8, note_freq(lead[0], lead[1]), amp=0.11, instrument="marimba", pan=0.25)

    return track

def generate_track_8():
    # 08_midnight_tropic: Key of E Minor / G Major, 75 BPM
    bpm = 75
    beat_dur = 60.0 / bpm
    bar_dur = beat_dur * 4.0
    total_dur = bar_dur * 8
    track = SynthTrack(total_dur)
    track.add_ocean_swell(0.04)

    chords = [
        ([('E', 3), ('G', 3), ('B', 3), ('D', 4)], ('E', 1)),
        ([('C', 3), ('E', 3), ('G', 3), ('B', 3)], ('C', 1)),
        ([('A', 2), ('C', 3), ('E', 3), ('G', 3)], ('A', 1)),
        ([('B', 2), ('D#', 3), ('F#', 3), ('A', 3)], ('B', 1)),
    ] * 2

    for bar_idx, (chord, bass_note) in enumerate(chords):
        bar_start = bar_idx * bar_dur
        for note, oct in chord:
            track.add_tone(bar_start, bar_dur * 0.9, note_freq(note, oct), amp=0.11, instrument="rhodes", pan=random.uniform(-0.3, 0.3))
        track.add_tone(bar_start, bar_dur * 0.8, note_freq(bass_note[0], bass_note[1]), amp=0.25, instrument="bass")

        for b in range(4):
            t = bar_start + b * beat_dur
            track.add_shaker(t, amp=0.03, pan=0.3)
            track.add_shaker(t + beat_dur * 0.5, amp=0.02, pan=-0.3)
            if b % 2 == 1:
                lead = random.choice([('E', 5), ('G', 5), ('B', 5), ('D', 6)])
                track.add_tone(t, beat_dur * 0.75, note_freq(lead[0], lead[1]), amp=0.11, instrument="marimba", pan=-0.3)

    return track

def main():
    generators = [
        ("01_tiki_breeze", generate_track_1),
        ("02_sunset_lagoon", generate_track_2),
        ("03_palm_cabana", generate_track_3),
        ("04_mai_tai_lounge", generate_track_4),
        ("05_coral_drift", generate_track_5),
        ("06_neon_cocktail", generate_track_6),
        ("07_aloha_twilight", generate_track_7),
        ("08_midnight_tropic", generate_track_8),
    ]

    print(f"🎵 Generating {len(generators)} tropical background music tracks into {OUT_DIR}...")
    for name, gen_fn in generators:
        print(f"   Synthesizing {name}...")
        synth = gen_fn()
        wav_file = OUT_DIR / f"{name}.wav"
        mp3_file = OUT_DIR / f"{name}.mp3"
        synth.export(wav_file, mp3_file)
        print(f"   ✅ Saved {mp3_file.name}")

    print("🌴 All BGM tracks successfully generated!")

if __name__ == "__main__":
    main()
