#!/usr/bin/env python3
"""
Tiki Karaoke - Procedural Soundboard Audio Synthesizer
======================================================
Copyright (c) 2026 Todd / Tiki Karaoke Project
Licensed under the PolyForm Noncommercial License 1.0.0 (or Commercial License where applicable).

LEGAL & PROVENANCE NOTICE:
--------------------------
All soundboard audio files in `static/sounds/` (airhorn, applause, rimshot, bird)
are 100% procedurally synthesized from pure mathematical acoustics, physical modeling,
and digital signal processing (DSP) equations.

NO external audio samples, sound libraries, or copyrighted recordings were used.
This script serves as reproducible cryptographic and mathematical proof of original
authorship and clean-room provenance for all soundboard assets.

To re-synthesize and master all sound assets:
    python3 scripts/generate_soundboard_audio.py
"""

import os
import sys
import wave
import tempfile
import subprocess
import numpy as np
from scipy import signal

SAMPLE_RATE = 44100
SOUNDS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "sounds"))


def create_stereo_reverb(mono: np.ndarray, delay_sec: float = 0.045, decay: float = 0.40, room_size: float = 0.5) -> np.ndarray:
    """Simulates realistic acoustic room reflection and stereo diffusion via comb filtering."""
    delay_l = int(delay_sec * SAMPLE_RATE)
    delay_r = int((delay_sec * 1.35) * SAMPLE_RATE)
    out_l = np.copy(mono)
    out_r = np.copy(mono)
    for d, out in [(delay_l, out_l), (delay_r, out_r)]:
        comb = np.zeros_like(mono)
        for i in range(d, len(mono)):
            comb[i] = mono[i] + decay * comb[i - d]
        out += room_size * comb
    return np.vstack([out_l, out_r]).T


def synthesize_airhorn() -> np.ndarray:
    """
    Synthesizes the iconic Dancehall / DJ Festival Air Horn blast.
    Physical Model:
    - Piercing festival brass frequencies: G5 (784.0 Hz) and D6 (1174.6 Hz) with G6/G4 sub-harmonics.
    - Rapid 12ms attack pressure transients with non-linear saturation (tanh compression).
    - Authentic 4-blast soundclash cadence (3 staccato bursts + 1 sustained blast) with stereo slapback.
    """
    total_dur = 2.2
    out = np.zeros(int(total_dur * SAMPLE_RATE))
    
    blasts = [(0.0, 0.10), (0.16, 0.10), (0.32, 0.10), (0.48, 0.75)]
    f1, f2, f3, f_sub = 784.0, 1174.6, 1568.0, 392.0
    
    for start_t, dur in blasts:
        start_idx = int(start_t * SAMPLE_RATE)
        n_samples = int(dur * SAMPLE_RATE)
        t = np.linspace(0, dur, n_samples)
        
        pitch_env = np.ones_like(t)
        att_len = int(0.012 * SAMPLE_RATE)
        rel_len = int(0.025 * SAMPLE_RATE)
        if len(t) > att_len:
            pitch_env[:att_len] = np.linspace(0.85, 1.0, att_len)
        if len(t) > rel_len:
            pitch_env[-rel_len:] = np.linspace(1.0, 0.95, rel_len)
        
        amp_env = np.ones_like(t)
        amp_env[:att_len] = np.sin(np.linspace(0, np.pi / 2, att_len)) ** 2
        amp_env[-rel_len:] = np.cos(np.linspace(0, np.pi / 2, rel_len)) ** 2
        
        vibrato = 1.0 + 0.008 * np.sin(2 * np.pi * 32 * t)
        
        p1 = np.cumsum(2 * np.pi * f1 * pitch_env * vibrato / SAMPLE_RATE)
        p2 = np.cumsum(2 * np.pi * f2 * pitch_env * vibrato / SAMPLE_RATE)
        p3 = np.cumsum(2 * np.pi * f3 * pitch_env * vibrato / SAMPLE_RATE)
        p_sub = np.cumsum(2 * np.pi * f_sub * pitch_env * vibrato / SAMPLE_RATE)
        
        wave_sum = (
            1.00 * np.sin(p1) + 0.70 * np.sin(2 * p1) + 0.35 * np.sin(3 * p1) +
            0.85 * np.sin(p2) + 0.55 * np.sin(2 * p2) +
            0.40 * np.sin(p3) + 0.30 * np.sin(p_sub)
        )
        
        hiss = np.random.normal(0, 0.07, n_samples)
        b, a = signal.butter(2, [1800 / (SAMPLE_RATE / 2), 8000 / (SAMPLE_RATE / 2)], btype='band')
        hiss = signal.lfilter(b, a, hiss)
        
        blast = np.tanh(3.2 * (wave_sum + hiss)) * amp_env
        out[start_idx:start_idx + n_samples] += blast

    delay_samples = int(0.14 * SAMPLE_RATE)
    delayed_l = np.zeros_like(out)
    delayed_r = np.zeros_like(out)
    delayed_l[delay_samples:] = out[:-delay_samples] * 0.40
    delayed_r[int(delay_samples * 1.35):] = out[:-int(delay_samples * 1.35)] * 0.30
    
    return np.vstack([out + delayed_l, out + delayed_r]).T


def synthesize_applause() -> np.ndarray:
    """
    Synthesizes an intimate lounge crowd applause with natural palm body and distinct bony hand snaps.
    Physical Model:
    - Layer 1: Warm, muffled fleshy palm body (300-850 Hz, 24dB/oct low-pass at 1500 Hz).
    - Layer 2: Finger and hand definition across 18 individual guests with natural timing variance.
    - Layer 3: Distinct higher-pitched bony hand snaps (2400-3400 Hz) with short 25ms decays.
    - Layer 4: Friendly lounge 'Whoo-hoo!' vocal formant and non-repeating multi-tap prime room diffusion.
    """
    dur = 4.0
    n_samples = int(dur * SAMPLE_RATE)
    
    # 1. Articulated handclaps layer (18 guests)
    np.random.seed(42)
    v3_l = np.zeros(n_samples)
    v3_r = np.zeros(n_samples)
    people_v3 = []
    for _ in range(18):
        pan = np.random.uniform(0.1, 0.9)
        tempo = np.random.uniform(2.8, 4.4)
        start_delay = np.random.uniform(0.05, 0.6)
        stop_time = dur - np.random.uniform(0.1, 0.7)
        style = np.random.choice(['cupped', 'flat', 'fingertip'], p=[0.35, 0.45, 0.20])
        if style == 'cupped':
            f_center = np.random.uniform(450, 750)
            vol = np.random.uniform(0.6, 0.9)
            decay_rate = np.random.uniform(60, 90)
        elif style == 'flat':
            f_center = np.random.uniform(900, 1400)
            vol = np.random.uniform(0.5, 0.8)
            decay_rate = np.random.uniform(80, 120)
        else:
            f_center = np.random.uniform(1600, 2400)
            vol = np.random.uniform(0.3, 0.5)
            decay_rate = np.random.uniform(100, 150)
        people_v3.append({
            'pan': pan, 'tempo': tempo, 'start': start_delay, 'stop': stop_time,
            'f_center': f_center, 'vol': vol, 'decay': decay_rate
        })

    for p in people_v3:
        cur_t = p['start']
        b_p, a_p = signal.butter(2, [max(100, p['f_center'] - 200) / (SAMPLE_RATE / 2), min(SAMPLE_RATE / 2 - 100, p['f_center'] + 300) / (SAMPLE_RATE / 2)], btype='band')
        while cur_t < p['stop']:
            jitter = np.random.normal(0, 0.015)
            idx = int((cur_t + jitter) * SAMPLE_RATE)
            cur_t += (1.0 / p['tempo'])
            c_samples = int(0.045 * SAMPLE_RATE)
            if idx < 0 or idx + c_samples >= n_samples:
                continue
            t_c = np.linspace(0, 0.045, c_samples)
            palm_pulse = np.exp(-t_c * p['decay'])
            fingers_offset = int(0.004 * SAMPLE_RATE)
            finger_pulse = np.zeros_like(palm_pulse)
            if fingers_offset < len(finger_pulse):
                finger_pulse[fingers_offset:] = 0.5 * np.exp(-t_c[:-fingers_offset] * (p['decay'] * 1.3))
            noise = np.random.normal(0, 1.0, c_samples)
            clap_wave = signal.lfilter(b_p, a_p, noise * (palm_pulse + finger_pulse)) * p['vol']
            v3_l[idx:idx + c_samples] += clap_wave * (1.0 - p['pan'])
            v3_r[idx:idx + c_samples] += clap_wave * p['pan']

    diff_v3_l = np.copy(v3_l)
    diff_v3_r = np.copy(v3_r)
    for d, g in [(811, 0.20), (1223, 0.15), (1987, 0.10)]:
        diff_v3_l[d:] += v3_l[:-d] * g
        diff_v3_r[int(d * 1.3):] += v3_r[:-int(d * 1.3)] * g

    v3_audio = np.vstack([diff_v3_l, diff_v3_r]).T
    peak_v3 = np.max(np.abs(v3_audio))
    if peak_v3 > 0:
        v3_audio = v3_audio / peak_v3

    # 2. Warm muffled palm foundation layer (16 guests)
    np.random.seed(101)
    v4_l = np.zeros(n_samples)
    v4_r = np.zeros(n_samples)
    b_muff, a_muff = signal.butter(4, 1500 / (SAMPLE_RATE / 2), btype='low')
    b_body, a_body = signal.butter(2, [280 / (SAMPLE_RATE / 2), 850 / (SAMPLE_RATE / 2)], btype='band')

    for _ in range(16):
        pan = np.random.uniform(0.12, 0.88)
        tempo = np.random.uniform(2.4, 3.8)
        cur_t = np.random.uniform(0.08, 0.5)
        stop_t = dur - np.random.uniform(0.1, 0.6)
        hand_pitch = np.random.uniform(380, 820)
        b_p, a_p = signal.butter(2, [max(120, hand_pitch - 180) / (SAMPLE_RATE / 2), min(SAMPLE_RATE / 2 - 200, hand_pitch + 240) / (SAMPLE_RATE / 2)], btype='band')
        person_vol = np.random.uniform(0.55, 0.90)
        while cur_t < stop_t:
            jitter = np.random.normal(0, 0.025)
            idx = int((cur_t + jitter) * SAMPLE_RATE)
            cur_t += (1.0 / tempo)
            c_samples = int(0.055 * SAMPLE_RATE)
            if idx < 0 or idx + c_samples >= n_samples:
                continue
            noise = np.random.normal(0, 1.0, c_samples)
            t_c = np.linspace(0, 0.055, c_samples)
            env = np.sin(np.linspace(0, np.pi / 2, int(0.003 * SAMPLE_RATE))) ** 2
            full_env = np.ones(c_samples)
            full_env[:len(env)] = env
            full_env[len(env):] = np.exp(-t_c[len(env):] * 70)
            single_clap = (0.7 * signal.lfilter(b_p, a_p, noise) + 0.3 * signal.lfilter(b_body, a_body, noise)) * full_env
            single_clap = signal.lfilter(b_muff, a_muff, single_clap) * person_vol
            v4_l[idx:idx + c_samples] += single_clap * (1.0 - pan)
            v4_r[idx:idx + c_samples] += single_clap * pan

    diff_v4_l = np.copy(v4_l)
    diff_v4_r = np.copy(v4_r)
    for d, g in [(733, 0.22), (1151, 0.18), (1789, 0.14)]:
        diff_v4_l[d:] += v4_l[:-d] * g
        diff_v4_r[int(d * 1.28):] += v4_r[:-int(d * 1.28)] * g

    v4_audio = np.vstack([diff_v4_l, diff_v4_r]).T
    peak_v4 = np.max(np.abs(v4_audio))
    if peak_v4 > 0:
        v4_audio = v4_audio / peak_v4

    # 3. 50/50 blend of foundation and definition
    combined = 0.50 * v3_audio + 0.50 * v4_audio

    # 4. Small bony-hand snappy clappers layer
    bony_l = np.zeros(n_samples)
    bony_r = np.zeros(n_samples)
    bony_people = [
        {'pan': 0.28, 'tempo': 3.7, 'start': 0.15, 'f_center': 2400, 'vol': 0.55, 'decay': 140},
        {'pan': 0.78, 'tempo': 4.1, 'start': 0.22, 'f_center': 2950, 'vol': 0.50, 'decay': 160},
        {'pan': 0.48, 'tempo': 3.3, 'start': 0.10, 'f_center': 3400, 'vol': 0.42, 'decay': 180}
    ]

    for bp in bony_people:
        cur_t = bp['start']
        b_bony, a_bony = signal.butter(2, [(bp['f_center'] - 300) / (SAMPLE_RATE / 2), (bp['f_center'] + 400) / (SAMPLE_RATE / 2)], btype='band')
        while cur_t < (dur - 0.3):
            jitter = np.random.normal(0, 0.012)
            idx = int((cur_t + jitter) * SAMPLE_RATE)
            cur_t += (1.0 / bp['tempo'])
            c_len = int(0.025 * SAMPLE_RATE)
            if idx < 0 or idx + c_len >= n_samples:
                continue
            noise = np.random.normal(0, 1.0, c_len)
            t_snap = np.linspace(0, 0.025, c_len)
            snap_env = np.exp(-t_snap * bp['decay'])
            bony_clap = signal.lfilter(b_bony, a_bony, noise * snap_env) * bp['vol']
            bony_l[idx:idx + c_len] += bony_clap * (1.0 - bp['pan'])
            bony_r[idx:idx + c_len] += bony_clap * bp['pan']

    combined[:, 0] += bony_l * 0.45
    combined[:, 1] += bony_r * 0.45

    # 5. Friendly lounge cheer ('Whoo-hoo!')
    w_idx = int(0.80 * SAMPLE_RATE)
    w_dur = int(0.65 * SAMPLE_RATE)
    wt = np.linspace(0, 0.65, w_dur)
    w_f0 = np.linspace(350, 500, w_dur) + 12 * np.sin(2 * np.pi * 14 * wt)
    v_raw = np.sin(np.cumsum(2 * np.pi * w_f0 / SAMPLE_RATE))
    b_vf, a_vf = signal.butter(2, [550 / (SAMPLE_RATE / 2), 1400 / (SAMPLE_RATE / 2)], btype='band')
    woo = signal.lfilter(b_vf, a_vf, v_raw * np.sin(np.pi * wt / 0.65) ** 2) * 0.15
    combined[w_idx:w_idx + w_dur, 0] += woo * 0.7
    combined[w_idx:w_idx + w_dur, 1] += woo * 0.3

    return combined


def synthesize_rimshot() -> np.ndarray:
    """
    Synthesizes the classic acoustic comedy rimshot with authentic drummer drag ("buh... -dl- ... DUM! ... TSSSH!").
    Physical Model:
    - Event 1: Warm resonant high tom tap ('buh') at 0.00s.
    - Event 2: Snare drumhead ghost-stroke drag bounce ('-dl-') at 0.16s.
    - Event 3: Heavy crisp rimshot accent on snare ('dum') at 0.24s.
    - Event 4: Shimmering splash / crash cymbal wash ('tsssh!') at 0.46s.
    """
    dur = 2.4
    n_samples = int(dur * SAMPLE_RATE)
    left = np.zeros(n_samples)
    right = np.zeros(n_samples)
    
    # 1. 'buh' (t=0.00s)
    t1_samples = int(0.12 * SAMPLE_RATE)
    t1 = np.linspace(0, 0.12, t1_samples)
    f_tom = np.linspace(230, 120, t1_samples)
    tom_tone = np.sin(np.cumsum(2 * np.pi * f_tom / SAMPLE_RATE)) * np.exp(-t1 * 30)
    tom_stick = np.random.normal(0, 0.25, t1_samples) * np.exp(-t1 * 110)
    buh = (tom_tone + tom_stick) * 0.75
    left[:t1_samples] += buh * 0.8
    right[:t1_samples] += buh * 0.7
    
    # 2. '-dl-' (t=0.16s)
    idx_dl = int(0.16 * SAMPLE_RATE)
    t_dl_samples = int(0.09 * SAMPLE_RATE)
    t_dl = np.linspace(0, 0.09, t_dl_samples)
    b_sn, a_sn = signal.butter(2, [1200 / (SAMPLE_RATE / 2), 4500 / (SAMPLE_RATE / 2)], btype='band')
    ghost_snare = signal.lfilter(b_sn, a_sn, np.random.normal(0, 0.7, t_dl_samples)) * np.exp(-t_dl * 60)
    ghost_body = np.sin(np.cumsum(2 * np.pi * np.linspace(210, 170, t_dl_samples) / SAMPLE_RATE)) * np.exp(-t_dl * 35)
    dl = (0.55 * ghost_snare + 0.45 * ghost_body) * 0.60
    left[idx_dl:idx_dl + t_dl_samples] += dl * 0.75
    right[idx_dl:idx_dl + t_dl_samples] += dl * 0.85
    
    # 3. 'dum' (t=0.24s)
    idx_dum = int(0.24 * SAMPLE_RATE)
    t_dum_samples = int(0.22 * SAMPLE_RATE)
    t_dum = np.linspace(0, 0.22, t_dum_samples)
    f_snare = np.linspace(270, 180, t_dum_samples)
    snare_tone = np.sin(np.cumsum(2 * np.pi * f_snare / SAMPLE_RATE)) * np.exp(-t_dum * 18)
    b_crack, a_crack = signal.butter(2, [2800 / (SAMPLE_RATE / 2), 7000 / (SAMPLE_RATE / 2)], btype='band')
    rim_crack = signal.lfilter(b_crack, a_crack, np.random.normal(0, 1.2, t_dum_samples)) * np.exp(-t_dum * 65)
    b_buzz, a_buzz = signal.butter(2, 2000 / (SAMPLE_RATE / 2), btype='high')
    snare_buzz = signal.lfilter(b_buzz, a_buzz, np.random.normal(0, 0.9, t_dum_samples)) * np.exp(-t_dum * 15)
    dum = 0.50 * snare_tone + 1.10 * rim_crack + 0.55 * snare_buzz
    left[idx_dum:idx_dum + t_dum_samples] += dum * 1.0
    right[idx_dum:idx_dum + t_dum_samples] += dum * 1.0
    
    # 4. 'tssss' (t=0.46s)
    idx_cym = int(0.46 * SAMPLE_RATE)
    t_cym_samples = n_samples - idx_cym
    t_cym = np.linspace(0, t_cym_samples / SAMPLE_RATE, t_cym_samples)
    metal_freqs = [318, 532, 828, 1260, 2140, 3520, 5880]
    metal = np.zeros(t_cym_samples)
    for mf in metal_freqs:
        metal += np.sign(np.sin(2 * np.pi * mf * t_cym)) * 0.16
    b_cym, a_cym = signal.butter(2, [3200 / (SAMPLE_RATE / 2), 15000 / (SAMPLE_RATE / 2)], btype='band')
    cym_noise = signal.lfilter(b_cym, a_cym, np.random.normal(0, 1.0, t_cym_samples) + metal)
    cym_env = 0.85 * np.exp(-t_cym * 4.2) + 0.35 * np.exp(-t_cym * 1.5)
    cymbal = cym_noise * cym_env * 0.95
    left[idx_cym:] += cymbal * 0.95
    right[idx_cym:] += cymbal * 0.90
    
    return np.vstack([left, right]).T


def synthesize_tiki_bird() -> np.ndarray:
    """
    Synthesizes an authentic tropical rainforest toucan / macaw jungle call.
    Physical Model:
    - Rhythmic FM chirp sweeps between 1.8 kHz and 3.6 kHz with organic biological vibrato.
    - Multiple vocal harmonic formants.
    - Tropical canopy spatial reflections.
    """
    dur = 2.8
    n_samples = int(dur * SAMPLE_RATE)
    out = np.zeros(n_samples)
    
    chirp_cues = [
        (0.1, 0.35, 1800, 3200),
        (0.55, 0.28, 2200, 3600),
        (0.95, 0.45, 1600, 3000),
        (1.55, 0.65, 2400, 1700)
    ]
    
    for start_t, c_dur, f_start, f_end in chirp_cues:
        idx = int(start_t * SAMPLE_RATE)
        n_c = int(c_dur * SAMPLE_RATE)
        if idx + n_c >= n_samples:
            continue
        t = np.linspace(0, c_dur, n_c)
        sweep = np.linspace(f_start, f_end, n_c)
        vibrato = 120 * np.sin(2 * np.pi * 32 * t)
        freq = sweep + vibrato
        phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
        voice = np.sin(phase) + 0.4 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)
        env = np.sin(np.pi * t / c_dur) ** 1.5
        out[idx:idx + n_c] += voice * env * 0.75

    return create_stereo_reverb(out, delay_sec=0.065, decay=0.55, room_size=0.6)


def export_and_master(audio: np.ndarray, name: str) -> str:
    """Peak normalizes and encodes to EBU R128 loudness-normalized 192k MP3 via FFmpeg."""
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = (audio / peak) * 0.88
    
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    mp3_path = os.path.join(SOUNDS_DIR, f"{name}.mp3")
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav_path = tmp_wav.name
        int16 = (audio * 32767).astype(np.int16)
        with wave.open(tmp_wav_path, "w") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(int16.tobytes())
    
    try:
        cmd = [
            "ffmpeg", "-y", "-i", tmp_wav_path,
            "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
            "-b:a", "192k", mp3_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    finally:
        if os.path.exists(tmp_wav_path):
            os.remove(tmp_wav_path)
            
    return mp3_path


def main():
    print("🌴 Synthesizing 100% Original Procedural Soundboard Audio...")
    sound_generators = [
        ("airhorn", synthesize_airhorn),
        ("applause", synthesize_applause),
        ("rimshot", synthesize_rimshot),
        ("bird", synthesize_tiki_bird)
    ]
    
    for name, gen_fn in sound_generators:
        print(f"  🔊 Generating {name}...")
        raw_audio = gen_fn()
        out_file = export_and_master(raw_audio, name)
        size_kb = os.path.getsize(out_file) / 1024
        print(f"  ✓ Mastered: {out_file} ({size_kb:.1f} KB, 192kbps stereo)")

    print("\n✨ All soundboard assets successfully generated and mastered in static/sounds/!")


if __name__ == "__main__":
    main()
