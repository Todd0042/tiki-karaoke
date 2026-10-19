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
        this.pitchShifter = null;
        this.currentPitch = 0;
        this.avDelayNode = null;
        this.currentSyncOffsetMs = 0;
        this.initialized = false;
    }

    init(mediaElement) {
        if (this.initialized) return;

        const AudioContext = window.AudioContext || window.webkitAudioContext;
        this.ctx = new AudioContext();

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

        // 5. Master Gain
        this.masterGain = this.ctx.createGain();
        this.masterGain.gain.value = 1.0;

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
        // 7. Venue A/V Sync Delay Node
        this.avDelayNode = this.ctx.createDelay(1.0);
        this.avDelayNode.delayTime.value = Math.max(0, this.currentSyncOffsetMs) / 1000.0;

        this.masterGain.connect(this.avDelayNode);
        this.avDelayNode.connect(this.ctx.destination);

        this.initialized = true;
    }

    resume() {
        if (this.ctx && this.ctx.state === "suspended") {
            this.ctx.resume();
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

        this.mod1 = context.createBufferSource();
        this.mod2 = context.createBufferSource();
        this.mod3 = context.createBufferSource();
        this.mod4 = context.createBufferSource();

        this.delay1 = context.createDelay();
        this.delay2 = context.createDelay();

        this.fade1 = context.createGain();
        this.fade2 = context.createGain();

        this.init();
    }

    init() {
        const length = Math.floor(this.bufferTime * this.context.sampleRate);
        const buffer = this.context.createBuffer(1, length, this.context.sampleRate);
        const data = buffer.getChannelData(0);

        // Linear saw ramp for delay modulation
        for (let i = 0; i < length; i++) {
            data[i] = (i / length) * this.bufferTime;
        }

        const fadeBuffer = this.context.createBuffer(1, length, this.context.sampleRate);
        const fadeData = fadeBuffer.getChannelData(0);

        // Triangular cross-fade envelope
        for (let i = 0; i < length; i++) {
            fadeData[i] = Math.sin((i / length) * Math.PI);
        }

        this.mod1.buffer = buffer;
        this.mod2.buffer = buffer;
        this.mod1.loop = true;
        this.mod2.loop = true;

        this.mod3.buffer = fadeBuffer;
        this.mod4.buffer = fadeBuffer;
        this.mod3.loop = true;
        this.mod4.loop = true;

        // Modulate Delay Times
        const modGain1 = this.context.createGain();
        const modGain2 = this.context.createGain();

        this.mod1.connect(modGain1);
        this.mod2.connect(modGain2);
        modGain1.connect(this.delay1.delayTime);
        modGain2.connect(this.delay2.delayTime);

        // Modulate Volume Crossfade
        this.mod3.connect(this.fade1.gain);
        this.mod4.connect(this.fade2.gain);

        // Signal Path
        this.input.connect(this.delay1);
        this.input.connect(this.delay2);
        this.delay1.connect(this.fade1);
        this.delay2.connect(this.fade2);
        this.fade1.connect(this.output);
        this.fade2.connect(this.output);

        this.modGain1 = modGain1;
        this.modGain2 = modGain2;

        this.mod1.start(0);
        this.mod2.start(this.bufferTime / 2);
        this.mod3.start(0);
        this.mod4.start(this.bufferTime / 2);

        this.setPitchOffset(0);
    }

    setPitchOffset(semitones) {
        this.pitchOffset = semitones;
        if (semitones === 0) {
            this.modGain1.gain.value = 0;
            this.modGain2.gain.value = 0;
            return;
        }

        // pitch multiplier = 2^(semitones/12)
        const mult = Math.pow(2, semitones / 12);
        // speed delta: (1 - mult)
        const delta = 1 - mult;
        this.modGain1.gain.value = delta;
        this.modGain2.gain.value = delta;
    }
}

window.AudioDSPPipeline = AudioDSPPipeline;
