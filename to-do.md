# Tiki Karaoke — To-Do

## Audio / Live Stage Feedback
- [ ] **Reduce live speaker feedback & room bleed in Browser WebRTC & Native modes**
  - Browser `echoCancellation: true` alone is not suppressing acoustic feedback strongly enough in a live room with speakers + mic open.
  - Follow-up ideas to investigate after Phase 3/4:
    1. **Linux PipeWire / PulseAudio `module-echo-cancel` (`aec_method=webrtc`)** in `NativeAudioRouter` so OS-level AEC subtracts the stage playback sink from the mic source at low latency.
    2. **Feedback / Ringing Mitigation in `audio-dsp.js`:** Add an optional vocal noise gate (attenuates mic below threshold when nobody is singing), a gentle low-latency anti-feedback notch/high-pass/low-pass trim, and verify `masterSumNode` isn't feeding back into a system monitor loopback device.
    3. **Input/Output Loop Check:** Warn the host in UI if the selected mic input is a monitor/loopback source or if browser mic monitoring is active at the same time as OS loopback.
