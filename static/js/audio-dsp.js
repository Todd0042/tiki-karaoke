/**
 * Web Audio DSP Pipeline: 3-Band Parametric EQ, Chris Wilson Delay-Line Pitch Shifter,
 * Peak Limiter, and BGM Cross-fader.
 */

class AudioDSPPipeline {
    constructor() {
        this.ctx = null;
        this.sourceNode = null;
        this.bassFilter = null;
        this.midFilter = null;
        this.trebleFilter = null;
        this.limiter = null;
        this.masterGain = null;
        this.musicDuckingGain = null;
        this.masterSumNode = null;
        this.pitchShifter = null;
        this.currentPitch = 0;
        this.avDelayNode = null;
        this.currentSyncOffsetMs = 0;
        this.initialized = false;

        // Microphone & Live Vocal Bus
        this.micStream = null;
        this.micSourceNode = null;
        this.micSplitter = null;
        this.micHpL = null;
        this.micHpR = null;
        this.autoTuneNodeL = null; // Reserved hook for future WASM Autotalent
        this.autoTuneNodeR = null; // Reserved hook for future WASM Autotalent
        this.micCompL = null;
        this.micCompR = null;
        this.micAnalyserL = null;
        this.micAnalyserR = null;
        this.micCenterSum = null;
        this.micPresenceFilter = null;
        this.micDryGain = null;
        this.micWetGain = null;
        this.micReverbConvolver = null;
        this.micMasterGain = null;
        this.micMuteGain = null;
        this.micMuted = true; // Default OFF as requested
        this.micVolume = 0.8;
        this.micReverbEnabled = true;

        // Between-Song Music Ducking
        this.isBetweenSongs = true;
        this.duckingEnabled = true;
        this.duckingDb = -6.0;
        this.duckingReleaseTime = 0;
        this.duckingInterval = null;
    }

    ensureContext() {
        if (this.ctx) {
            if (this.ctx.state === "suspended") {
                this.ctx.resume().catch(() => {});
            }
            return this.ctx;
        }

        const AudioContext = window.AudioContext || window.webkitAudioContext;
        this.ctx = new AudioContext();

        // 5. Music Master Gain & Between-Song Ducking Node
        this.masterGain = this.ctx.createGain();
        this.masterGain.gain.value = 1.0;

        this.musicDuckingGain = this.ctx.createGain();
        this.musicDuckingGain.gain.value = 1.0;

        // Master Venue Summing Node
        this.masterSumNode = this.ctx.createGain();
        this.masterSumNode.gain.value = 1.0;

        this.masterGain.connect(this.musicDuckingGain);
        this.musicDuckingGain.connect(this.masterSumNode);

        // Music Bass Analyser (for audio-reactive stage visuals)
        this.musicAnalyser = this.ctx.createAnalyser();
        this.musicAnalyser.fftSize = 64;
        this.masterGain.connect(this.musicAnalyser);

        // 7. Venue A/V Sync Delay Node
        this.avDelayNode = this.ctx.createDelay(1.0);
        this.avDelayNode.delayTime.value = Math.max(0, this.currentSyncOffsetMs) / 1000.0;

        this.masterSumNode.connect(this.avDelayNode);
        this.avDelayNode.connect(this.ctx.destination);

        this.startDuckingMonitor();
        return this.ctx;
    }

    init(mediaElement) {
        if (this.initialized) return;
        this.ensureContext();

        // 1. Source from HTML5 video/audio
        this.sourceNode = this.ctx.createMediaElementSource(mediaElement);

        // 2. 3-Band Parametric Equalizer
        this.bassFilter = this.ctx.createBiquadFilter();
        this.bassFilter.type = "lowshelf";
        this.bassFilter.frequency.value = 120;
        this.bassFilter.gain.value = 0;

        this.midFilter = this.ctx.createBiquadFilter();
        this.midFilter.type = "peaking";
        this.midFilter.frequency.value = 1500;
        this.midFilter.Q.value = 1.0;
        this.midFilter.gain.value = 0;

        this.trebleFilter = this.ctx.createBiquadFilter();
        this.trebleFilter.type = "highshelf";
        this.trebleFilter.frequency.value = 8000;
        this.trebleFilter.gain.value = 0;

        // 3. Pitch Shifter (Delay-Line cross-fader)
        this.pitchShifter = new WebAudioPitchShifter(this.ctx);

        // 4. Dynamics Compressor (Gentle Safety Limiter only for pitch shift)
        this.limiter = this.ctx.createDynamicsCompressor();
        this.limiter.threshold.value = -0.5;
        this.limiter.knee.value = 6.0;
        this.limiter.ratio.value = 4.0;
        this.limiter.attack.value = 0.005;
        this.limiter.release.value = 0.15;

        // 6. Direct Clean Bypass vs DSP Branch
        this.directGain = this.ctx.createGain();
        this.directGain.gain.value = 1.0;

        this.dspGain = this.ctx.createGain();
        this.dspGain.gain.value = 0.0;

        // Wire Audio Graph:
        // Source -> Bass -> Mid -> Treble
        this.sourceNode.connect(this.bassFilter);
        this.bassFilter.connect(this.midFilter);
        this.midFilter.connect(this.trebleFilter);

        // Branch 1: Bit-perfect Clean Direct Bypass (Pitch = 0, default)
        this.trebleFilter.connect(this.directGain);
        this.directGain.connect(this.masterGain);

        // Branch 2: Pitch Shift + Limiter (Active ONLY when Pitch != 0)
        this.trebleFilter.connect(this.pitchShifter.input);
        this.pitchShifter.output.connect(this.limiter);
        this.limiter.connect(this.dspGain);
        this.dspGain.connect(this.masterGain);

        this.initialized = true;
    }

    resume() {
        if (this.ctx && this.ctx.state === "suspended") {
            this.ctx.resume().catch(() => {});
        }
    }

    async initMicrophone(deviceId = null) {
        this.ensureContext();
        this.resume();

        // Release existing mic stream if open
        if (this.micStream) {
            try {
                this.micStream.getTracks().forEach(t => t.stop());
            } catch (e) {}
            this.micStream = null;
        }

        try {
            const constraints = {
                audio: {
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                    latency: 0
                }
            };
            // Only pass exact deviceId if it looks like a valid browser GUID
            const isBrowserGuid = deviceId && deviceId !== "default" && !deviceId.startsWith("alsa_") && !deviceId.includes(":") && !deviceId.includes(".");
            if (isBrowserGuid) {
                constraints.audio.deviceId = { exact: deviceId };
            }

            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia(constraints);
            } catch (err) {
                if (constraints.audio.deviceId) {
                    console.warn("[AudioDSP] Failed with exact deviceId, retrying with default audio:", err);
                    stream = await navigator.mediaDevices.getUserMedia({
                        audio: {
                            echoCancellation: false,
                            noiseSuppression: false,
                            autoGainControl: false,
                            latency: 0
                        }
                    });
                } else {
                    throw err;
                }
            }

            this.micStream = stream;
            this.setupMicGraph(stream);
            console.log("[AudioDSP] Microphone stream connected successfully:", deviceId || "Default");
            return true;
        } catch (e) {
            console.warn("[AudioDSP] Microphone access error:", e);
            return false;
        }
    }

    setupMicGraph(stream) {
        if (!this.ctx || !stream) return;

        if (this.micSourceNode) {
            try { this.micSourceNode.disconnect(); } catch (e) {}
        }

        this.micSourceNode = this.ctx.createMediaStreamSource(stream);

        // 1. Channel Splitter (Singer 1 on Left, Singer 2 on Right)
        this.micSplitter = this.ctx.createChannelSplitter(2);
        this.micSourceNode.connect(this.micSplitter);

        // 2. High-Pass Filters (90Hz) on each mic channel to eliminate thumps & plosives
        this.micHpL = this.ctx.createBiquadFilter();
        this.micHpL.type = "highpass";
        this.micHpL.frequency.value = 90;
        this.micHpL.Q.value = 0.7;

        this.micHpR = this.ctx.createBiquadFilter();
        this.micHpR.type = "highpass";
        this.micHpR.frequency.value = 90;
        this.micHpR.Q.value = 0.7;

        this.micSplitter.connect(this.micHpL, 0);
        this.micSplitter.connect(this.micHpR, 1);

        // 3. Reserved Auto-Tune insertion hooks (WASM Autotalent slot)
        const afterHpL = this.autoTuneNodeL ? this.autoTuneNodeL : this.micHpL;
        const afterHpR = this.autoTuneNodeR ? this.autoTuneNodeR : this.micHpR;

        // 4. Vocal Dynamics Compressors (Threshold -24dB, Ratio 4:1)
        this.micCompL = this.ctx.createDynamicsCompressor();
        this.micCompL.threshold.value = -24;
        this.micCompL.knee.value = 10;
        this.micCompL.ratio.value = 4.0;
        this.micCompL.attack.value = 0.015;
        this.micCompL.release.value = 0.15;

        this.micCompR = this.ctx.createDynamicsCompressor();
        this.micCompR.threshold.value = -24;
        this.micCompR.knee.value = 10;
        this.micCompR.ratio.value = 4.0;
        this.micCompR.attack.value = 0.015;
        this.micCompR.release.value = 0.15;

        afterHpL.connect(this.micCompL);
        afterHpR.connect(this.micCompR);

        // 5. Independent Analysers for VU metering
        this.micAnalyserL = this.ctx.createAnalyser();
        this.micAnalyserL.fftSize = 256;
        this.micCompL.connect(this.micAnalyserL);

        this.micAnalyserR = this.ctx.createAnalyser();
        this.micAnalyserR.fftSize = 256;
        this.micCompR.connect(this.micAnalyserR);

        // 6. Center-Summing Matrix:
        // Distribute both Singer 1 (Left) and Singer 2 (Right) across all venue speakers
        this.micCenterSum = this.ctx.createGain();
        this.micCenterSum.gain.value = 0.707; // -3dB compensation for summing two channels

        this.micCompL.connect(this.micCenterSum);
        this.micCompR.connect(this.micCenterSum);

        // 7. Vocal Presence EQ (+3dB at 3.5kHz)
        this.micPresenceFilter = this.ctx.createBiquadFilter();
        this.micPresenceFilter.type = "peaking";
        this.micPresenceFilter.frequency.value = 3500;
        this.micPresenceFilter.gain.value = 3.0;
        this.micPresenceFilter.Q.value = 1.2;

        this.micCenterSum.connect(this.micPresenceFilter);

        // 8. Vocal Ambience Reverb (Synthetic Plate)
        this.micDryGain = this.ctx.createGain();
        this.micDryGain.gain.value = 1.0;

        this.micWetGain = this.ctx.createGain();
        this.micWetGain.gain.value = this.micReverbEnabled ? 0.28 : 0.0;

        this.micReverbConvolver = this.ctx.createConvolver();
        this.micReverbConvolver.buffer = this.createReverbImpulse(1.4, 2.2);

        this.micPresenceFilter.connect(this.micDryGain);
        this.micPresenceFilter.connect(this.micReverbConvolver);
        this.micReverbConvolver.connect(this.micWetGain);

        // 9. Mic Master Volume Fader & Mute Node (Default Muted / Off)
        this.micMasterGain = this.ctx.createGain();
        this.micMasterGain.gain.value = this.micVolume;

        this.micMuteGain = this.ctx.createGain();
        this.micMuteGain.gain.value = this.micMuted ? 0.0 : 1.0;

        this.micDryGain.connect(this.micMasterGain);
        this.micWetGain.connect(this.micMasterGain);
        this.micMasterGain.connect(this.micMuteGain);

        // Connect into Master Venue Summing Node
        if (this.masterSumNode) {
            this.micMuteGain.connect(this.masterSumNode);
        }
    }

    createReverbImpulse(duration = 1.4, decay = 2.0) {
        const rate = this.ctx.sampleRate;
        const length = Math.floor(rate * duration);
        const impulse = this.ctx.createBuffer(2, length, rate);
        const l = impulse.getChannelData(0);
        const r = impulse.getChannelData(1);
        for (let i = 0; i < length; i++) {
            const n = i / length;
            const env = Math.pow(1 - n, decay);
            l[i] = (Math.random() * 2 - 1) * env;
            r[i] = (Math.random() * 2 - 1) * env;
        }
        return impulse;
    }

    startDuckingMonitor() {
        if (this.duckingInterval) clearInterval(this.duckingInterval);
        const bufferL = new Float32Array(128);
        const bufferR = new Float32Array(128);

        this.duckingInterval = setInterval(() => {
            if (!this.ctx || !this.musicDuckingGain) return;

            // If mic is muted, or not between songs, or ducking disabled: music is 100% full volume
            if (this.micMuted || !this.isBetweenSongs || !this.duckingEnabled || !this.micAnalyserL) {
                if (this.musicDuckingGain.gain.value < 0.99) {
                    try {
                        this.musicDuckingGain.gain.setTargetAtTime(1.0, this.ctx.currentTime, 0.15);
                    } catch (e) {
                        this.musicDuckingGain.gain.value = 1.0;
                    }
                    if (typeof window.onAudioDspDuckingActive === "function") {
                        window.onAudioDspDuckingActive(1.0);
                    }
                }
                return;
            }

            // Read peak amplitude
            this.micAnalyserL.getFloatTimeDomainData(bufferL);
            let peakL = 0;
            for (let i = 0; i < bufferL.length; i++) {
                const val = Math.abs(bufferL[i]);
                if (val > peakL) peakL = val;
            }

            let peakR = 0;
            if (this.micAnalyserR) {
                this.micAnalyserR.getFloatTimeDomainData(bufferR);
                for (let i = 0; i < bufferR.length; i++) {
                    const val = Math.abs(bufferR[i]);
                    if (val > peakR) peakR = val;
                }
            }

            const maxPeak = Math.max(peakL, peakR);
            const now = Date.now();

            // Speech threshold (-32 dBFS ≈ 0.025 peak amplitude)
            if (maxPeak > 0.025) {
                const targetLinear = Math.pow(10, this.duckingDb / 20.0);
                this.musicDuckingGain.gain.setTargetAtTime(targetLinear, this.ctx.currentTime, 0.08);
                this.duckingReleaseTime = now + 800; // Hold 800ms
                if (typeof window.onAudioDspDuckingActive === "function") {
                    window.onAudioDspDuckingActive(targetLinear);
                }
            } else if (now > this.duckingReleaseTime) {
                this.musicDuckingGain.gain.setTargetAtTime(1.0, this.ctx.currentTime, 0.25);
                if (typeof window.onAudioDspDuckingActive === "function") {
                    window.onAudioDspDuckingActive(1.0);
                }
            }
        }, 50);
    }

    setMusicDucked(isDucked) {
        if (!this.ctx || !this.musicDuckingGain) return;
        const targetLinear = isDucked ? Math.pow(10, (this.duckingDb || -6) / 20.0) : 1.0;
        try {
            this.musicDuckingGain.gain.setTargetAtTime(targetLinear, this.ctx.currentTime, 0.15);
        } catch (e) {
            this.musicDuckingGain.gain.value = targetLinear;
        }
        if (typeof window.onAudioDspDuckingActive === "function") {
            window.onAudioDspDuckingActive(targetLinear);
        }
    }

    setMicVolume(val) {
        this.micVolume = Math.max(0.0, Math.min(2.0, Number(val) || 0.8));
        if (this.micMasterGain && this.ctx) {
            this.micMasterGain.gain.setTargetAtTime(this.micVolume, this.ctx.currentTime, 0.02);
        }
    }

    setMicMuted(muted) {
        this.micMuted = !!muted;
        if (this.micMuteGain && this.ctx) {
            this.micMuteGain.gain.setTargetAtTime(this.micMuted ? 0.0 : 1.0, this.ctx.currentTime, 0.015);
        }
    }

    setMicReverb(enabled) {
        this.micReverbEnabled = !!enabled;
        if (this.micWetGain && this.ctx) {
            this.micWetGain.gain.setTargetAtTime(this.micReverbEnabled ? 0.28 : 0.0, this.ctx.currentTime, 0.05);
        }
    }

    setDuckingEnabled(enabled) {
        this.duckingEnabled = !!enabled;
    }

    setDuckingDb(db) {
        this.duckingDb = Math.min(-1.0, Math.max(-24.0, Number(db) || -6.0));
    }

    setBetweenSongs(isBetween) {
        this.isBetweenSongs = !!isBetween;
    }

    getMicLevels() {
        if (!this.micAnalyserL || this.micMuted) {
            return { peakL: 0, peakR: 0, isLive: !this.micMuted };
        }
        const bL = new Uint8Array(64);
        const bR = new Uint8Array(64);
        this.micAnalyserL.getByteTimeDomainData(bL);
        if (this.micAnalyserR) this.micAnalyserR.getByteTimeDomainData(bR);

        let maxL = 0;
        let maxR = 0;
        for (let i = 0; i < 64; i++) {
            const devL = Math.abs(bL[i] - 128);
            if (devL > maxL) maxL = devL;
            const devR = Math.abs(bR[i] - 128);
            if (devR > maxR) maxR = devR;
        }
        return {
            peakL: Math.min(100, Math.round((maxL / 128) * 100)),
            peakR: Math.min(100, Math.round((maxR / 128) * 100)),
            isLive: !this.micMuted
        };
    }

    getBassEnergy() {
        if (!this.musicAnalyser) return 0;
        try {
            const data = new Uint8Array(this.musicAnalyser.frequencyBinCount);
            this.musicAnalyser.getByteFrequencyData(data);
            const bass = (data[0] + data[1] + data[2]) / 3.0;
            return Math.min(1.0, bass / 255.0);
        } catch (e) {
            return 0;
        }
    }

    setPitch(semitones) {
        this.currentPitch = semitones;
        if (this.pitchShifter) {
            this.pitchShifter.setPitchOffset(semitones);
        }
        if (this.directGain && this.dspGain) {
            if (semitones === 0) {
                // 100% Bit-perfect direct stream without delay lines or compressor pumping
                this.directGain.gain.setValueAtTime(1.0, this.ctx.currentTime);
                this.dspGain.gain.setValueAtTime(0.0, this.ctx.currentTime);
            } else {
                // Enable pitch shift circuit
                this.directGain.gain.setValueAtTime(0.0, this.ctx.currentTime);
                this.dspGain.gain.setValueAtTime(1.0, this.ctx.currentTime);
            }
        }
    }

    setEQ(bass, mid, treble) {
        if (this.bassFilter) this.bassFilter.gain.value = bass;
        if (this.midFilter) this.midFilter.gain.value = mid;
        if (this.trebleFilter) this.trebleFilter.gain.value = treble;
    }

    setMasterVolume(val) {
        if (this.masterGain) {
            this.masterGain.gain.setValueAtTime(val, this.ctx.currentTime);
        }
    }

    setSyncOffset(offsetMs) {
        this.currentSyncOffsetMs = Number(offsetMs) || 0;
        if (this.avDelayNode && this.ctx) {
            const delaySec = Math.max(0, this.currentSyncOffsetMs) / 1000.0;
            try {
                this.avDelayNode.delayTime.setTargetAtTime(delaySec, this.ctx.currentTime, 0.02);
            } catch (e) {
                this.avDelayNode.delayTime.value = delaySec;
            }
        }
    }

    startPerformanceRecording(queueId, singerId, trackId) {
        try {
            this.ensureContext();
            if (!this.ctx || !this.masterSumNode) return false;
            if (!this.recDestination) {
                this.recDestination = this.ctx.createMediaStreamDestination();
                this.masterSumNode.connect(this.recDestination);
            }
            this.recordedChunks = [];
            this.recMetadata = { queueId, singerId, trackId, startTime: Date.now() };

            if (typeof MediaRecorder === "undefined") return false;
            let options = { mimeType: 'audio/webm;codecs=opus' };
            if (!MediaRecorder.isTypeSupported(options.mimeType)) {
                options = { mimeType: 'audio/webm' };
                if (!MediaRecorder.isTypeSupported(options.mimeType)) {
                    options = {};
                }
            }

            this.mediaRecorder = new MediaRecorder(this.recDestination.stream, options);
            this.mediaRecorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) {
                    this.recordedChunks.push(e.data);
                }
            };
            this.mediaRecorder.start(1000);
            console.log("[AudioDSP] Started live performance recording for singer", singerId);
            return true;
        } catch (err) {
            console.warn("[AudioDSP] Could not start MediaRecorder:", err);
            return false;
        }
    }

    async stopAndUploadPerformanceRecording() {
        if (!this.mediaRecorder || this.mediaRecorder.state === "inactive") return null;
        const meta = this.recMetadata || {};
        return new Promise((resolve) => {
            this.mediaRecorder.onstop = async () => {
                try {
                    if (!this.recordedChunks || this.recordedChunks.length === 0) {
                        resolve(null);
                        return;
                    }
                    const blob = new Blob(this.recordedChunks, { type: this.mediaRecorder.mimeType || 'audio/webm' });
                    const durationSec = Math.max(1, (Date.now() - (meta.startTime || Date.now())) / 1000);

                    const formData = new FormData();
                    formData.append("file", blob, `perf_${meta.singerId}_${meta.trackId}.webm`);
                    if (meta.queueId) formData.append("queue_id", meta.queueId);
                    formData.append("singer_id", meta.singerId);
                    formData.append("track_id", meta.trackId);
                    formData.append("duration", durationSec.toFixed(1));

                    const res = await fetch("/api/recordings/upload", {
                        method: "POST",
                        body: formData
                    });
                    const data = await res.json();
                    console.log("[AudioDSP] Performance recording uploaded successfully:", data);
                    resolve(data);
                } catch (err) {
                    console.error("[AudioDSP] Uploading performance recording failed:", err);
                    resolve(null);
                } finally {
                    this.recordedChunks = [];
                    this.recMetadata = null;
                    this.mediaRecorder = null;
                }
            };
            try {
                this.mediaRecorder.stop();
            } catch (e) {
                console.warn("[AudioDSP] Stop recording error:", e);
                resolve(null);
            }
        });
    }
}

/**
 * Real-time Delay-Line Pitch Shifter (Chris Wilson / Jungle architecture)
 */
class WebAudioPitchShifter {
    constructor(context) {
        this.context = context;
        this.input = context.createGain();
        this.output = context.createGain();

        this.pitchOffset = 0; // in semitones
        this.bufferTime = 0.100; // 100ms delay buffer
        this.fadeTime = 0.050; // 50ms crossfade
        this.delayTime = 0.100;

        this.init();
    }

    createFadeBuffer(activeTime, fadeTime) {
        const length1 = Math.floor(activeTime * this.context.sampleRate);
        const length2 = Math.floor((activeTime - 2 * fadeTime) * this.context.sampleRate);
        const length = length1 + Math.max(0, length2);
        const buffer = this.context.createBuffer(1, length, this.context.sampleRate);
        const p = buffer.getChannelData(0);

        const fadeLength = Math.floor(fadeTime * this.context.sampleRate);
        const fadeIndex1 = fadeLength;
        const fadeIndex2 = length1 - fadeLength;

        for (let i = 0; i < length1; ++i) {
            let value;
            if (i < fadeIndex1) {
                value = Math.sqrt(i / fadeLength);
            } else if (i >= fadeIndex2) {
                value = Math.sqrt(1 - (i - fadeIndex2) / fadeLength);
            } else {
                value = 1;
            }
            p[i] = value;
        }

        for (let i = length1; i < length; ++i) {
            p[i] = 0;
        }

        return buffer;
    }

    createDelayTimeBuffer(activeTime, fadeTime, shiftUp) {
        const length1 = Math.floor(activeTime * this.context.sampleRate);
        const length2 = Math.floor((activeTime - 2 * fadeTime) * this.context.sampleRate);
        const length = length1 + Math.max(0, length2);
        const buffer = this.context.createBuffer(1, length, this.context.sampleRate);
        const p = buffer.getChannelData(0);

        for (let i = 0; i < length1; ++i) {
            if (shiftUp) {
                p[i] = (length1 - i) / length;
            } else {
                p[i] = i / length1;
            }
        }

        for (let i = length1; i < length; ++i) {
            p[i] = 0;
        }

        return buffer;
    }

    init() {
        const mod1 = this.context.createBufferSource();
        const mod2 = this.context.createBufferSource();
        const mod3 = this.context.createBufferSource();
        const mod4 = this.context.createBufferSource();

        this.shiftDownBuffer = this.createDelayTimeBuffer(this.bufferTime, this.fadeTime, false);
        this.shiftUpBuffer = this.createDelayTimeBuffer(this.bufferTime, this.fadeTime, true);

        mod1.buffer = this.shiftDownBuffer;
        mod2.buffer = this.shiftDownBuffer;
        mod3.buffer = this.shiftUpBuffer;
        mod4.buffer = this.shiftUpBuffer;

        mod1.loop = true;
        mod2.loop = true;
        mod3.loop = true;
        mod4.loop = true;

        this.mod1Gain = this.context.createGain();
        this.mod2Gain = this.context.createGain();
        this.mod3Gain = this.context.createGain();
        this.mod4Gain = this.context.createGain();

        this.mod3Gain.gain.value = 0;
        this.mod4Gain.gain.value = 0;

        mod1.connect(this.mod1Gain);
        mod2.connect(this.mod2Gain);
        mod3.connect(this.mod3Gain);
        mod4.connect(this.mod4Gain);

        this.modGain1 = this.context.createGain();
        this.modGain2 = this.context.createGain();

        this.delay1 = this.context.createDelay();
        this.delay2 = this.context.createDelay();

        this.mod1Gain.connect(this.modGain1);
        this.mod2Gain.connect(this.modGain2);
        this.mod3Gain.connect(this.modGain1);
        this.mod4Gain.connect(this.modGain2);

        this.modGain1.connect(this.delay1.delayTime);
        this.modGain2.connect(this.delay2.delayTime);

        const fade1 = this.context.createBufferSource();
        const fade2 = this.context.createBufferSource();
        const fadeBuffer = this.createFadeBuffer(this.bufferTime, this.fadeTime);

        fade1.buffer = fadeBuffer;
        fade2.buffer = fadeBuffer;
        fade1.loop = true;
        fade2.loop = true;

        const mix1 = this.context.createGain();
        const mix2 = this.context.createGain();
        mix1.gain.value = 0;
        mix2.gain.value = 0;

        fade1.connect(mix1.gain);
        fade2.connect(mix2.gain);

        this.input.connect(this.delay1);
        this.input.connect(this.delay2);
        this.delay1.connect(mix1);
        this.delay2.connect(mix2);
        mix1.connect(this.output);
        mix2.connect(this.output);

        const t = this.context.currentTime + 0.050;
        const t2 = t + this.bufferTime - this.fadeTime;

        mod1.start(t);
        mod2.start(t2);
        mod3.start(t);
        mod4.start(t2);
        fade1.start(t);
        fade2.start(t2);

        this.setPitchOffset(0);
    }

    setDelay(delayAmount) {
        try {
            this.modGain1.gain.setTargetAtTime(0.5 * delayAmount, this.context.currentTime, 0.010);
            this.modGain2.gain.setTargetAtTime(0.5 * delayAmount, this.context.currentTime, 0.010);
        } catch (e) {
            this.modGain1.gain.value = 0.5 * delayAmount;
            this.modGain2.gain.value = 0.5 * delayAmount;
        }
    }

    setPitchOffset(semitones) {
        this.pitchOffset = semitones;
        if (semitones === 0) {
            this.modGain1.gain.value = 0;
            this.modGain2.gain.value = 0;
            return;
        }

        const pitchRatio = Math.pow(2, semitones / 12);
        const mult = pitchRatio - 1;

        if (mult > 0) {
            this.mod1Gain.gain.value = 0;
            this.mod2Gain.gain.value = 0;
            this.mod3Gain.gain.value = 1;
            this.mod4Gain.gain.value = 1;
        } else {
            this.mod1Gain.gain.value = 1;
            this.mod2Gain.gain.value = 1;
            this.mod3Gain.gain.value = 0;
            this.mod4Gain.gain.value = 0;
        }

        this.setDelay(this.delayTime * Math.abs(mult));
    }
}

window.AudioDSPPipeline = AudioDSPPipeline;
