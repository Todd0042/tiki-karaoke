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
        this.dspGain.connect(this.masterGain);

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
