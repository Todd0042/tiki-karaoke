function hostSkip() {
    fetch("/api/host/skip", { method: "POST" })
        .then(res => res.json())
        .then(data => {
            if (data && data.skipped_to_filler) {
                showToast(`⏭️ Skipped to filler: ${data.skipped_to_filler}`);
            } else if (data && data.skipped_to_bgm) {
                showToast("⏭️ Skipped to next background track");
            }
        })
        .catch(err => console.error("Skip error:", err));
}

function hostPauseResume() {
    fetch("/api/host/toggle-pause", { method: "POST" })
        .then(r => r.json())
        .then(data => {
            if (data.action === "paused_to_filler") {
                showToast("⏸️ Show paused! DJ Lounge music playing on stage.");
            } else if (data.action === "resumed_to_intermission") {
                showToast("▶️ Resuming show! Intermission countdown started.");
            }
            loadLiveQueue();
        })
        .catch(err => console.error("Pause/resume error:", err));
}

function hostStop() {
    fetch("/api/host/stop", { method: "POST" })
        .then(r => r.json())
        .then(data => {
            if (data.action === "wrapped") {
                showToast("🏆 Showing Tonight's Party Wrapped on Stage!");
            } else {
                showToast("⏹️ Stage stopped and placed on standby");
            }
            loadLiveQueue();
        })
        .catch(err => console.error("Stop error:", err));
}

function hostRenormalizeTrackPrompt(trackId, title) {
    if (!confirm(`Re-normalize audio for "${title}" to target LUFS?`)) return;
    fetch(`/api/host/tracks/${trackId}/renormalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === "ok") {
            showToast(`🎚️ Re-normalized "${title}" to ${res.normalized_lufs} LUFS!`);
            loadLiveQueue();
        } else {
            showToast("Failed to re-normalize track");
        }
    })
    .catch(err => {
        console.error(err);
        showToast("Error re-normalizing track");
    });
}

function openRenormalizeWizardModal() {
    fetch("/api/host/tracks/renormalize-audit")
        .then(r => r.json())
        .then(data => {
            const count = data.count || 0;
            const target = data.target_lufs || -16.0;
            if (count === 0) {
                alert(`✅ All local tracks are already calibrated to target (${target} LUFS)!`);
                return;
            }
            if (confirm(`Found ${count} tracks with audio level differing from target (${target} LUFS).\n\nStart background loudness calibration?`)) {
                runBatchRenormalize(target);
            }
        })
        .catch(err => {
            console.error(err);
            showToast("Error checking track calibration");
        });
}

function runBatchRenormalize(targetLufs) {
    fetch("/api/host/tracks/renormalize-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_lufs: targetLufs })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === "already_running") {
            showToast("⏳ Loudness calibration is already running in background!");
        } else {
            showToast("🚀 Started loudness calibration in background!");
        }
    })
    .catch(err => {
        console.error(err);
        showToast("Error starting batch calibration");
    });
}

function hostStartNow() {
    fetch("/api/host/start-now", { method: "POST" });
}

function hostAdd15s() {
    fetch("/api/host/add-time", { method: "POST" });
}

function hostRefreshStage(hard = false) {
    const url = hard ? "/api/host/stage/refresh?hard=true" : "/api/host/stage/refresh";
    fetch(url, { method: "POST" })
        .then(res => res.json())
        .then(data => {
            showToast(hard ? "🔄 Reloading all stage display screens..." : "🔄 Stage displays refreshed and resynced!");
            loadLiveQueue();
        })
        .catch(err => {
            alert("Error refreshing stage: " + err.message);
        });
}

function hostPlaySound(sound) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "soundboard_play", sound: sound }));
    }
}

function hostToggleTTS(enabled) {
    fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tts_enabled: enabled ? "true" : "false" })
    });
}

// -------------------------------------------------------------
// Mobile Host Remote Microphone Controls
// -------------------------------------------------------------
let hostMicState = {
    device: "default",
    muted: true,
    volume: 80,
    reverb: true,
    echo_cancellation: false,
    ducking_enabled: true,
    ducking_db: -6,
    cdg_theme: "neon",
    cdg_glow: true,
    available: null,   // null = unknown yet; true/false from backend router
    latency_ms: null,  // negotiated buffering latency estimate (native path)
    monitoring_mode: "native"  // hardware_direct | native | browser
};

async function loadHostMicDevices(showFeedback = false) {
    const sel = document.getElementById("mobileHostMicDeviceSelect");
    if (!sel) return;

    try {
        // First fetch live state from backend audio router so we know the saved device
        try {
            const stateRes = await fetch("/api/host/mic-state");
            if (stateRes.ok) {
                const st = await stateRes.json();
                if (st) {
                    hostMicState.muted = Boolean(st.muted);
                    hostMicState.volume = Number(st.volume) || 80;
                    if (st.device && st.device !== "default") hostMicState.device = st.device;
                    if (st.available !== undefined) hostMicState.available = Boolean(st.available);
                    if (st.monitoring_mode) hostMicState.monitoring_mode = st.monitoring_mode;
                    if (st.latency_ms_est !== undefined && st.latency_ms_est !== null) {
                        hostMicState.latency_ms = Number(st.latency_ms_est);
                    }
                    if (st.reverb !== undefined) {
                        hostMicState.reverb = Boolean(st.reverb);
                        const rev = document.getElementById("mobileMicReverbToggle");
                        if (rev) rev.checked = hostMicState.reverb;
                    }
                    if (st.echo_cancellation !== undefined) {
                        hostMicState.echo_cancellation = Boolean(st.echo_cancellation);
                        const aec = document.getElementById("mobileHostMicAecToggle");
                        if (aec) aec.checked = hostMicState.echo_cancellation;
                    }
                    if (st.ducking_enabled !== undefined) {
                        hostMicState.ducking_enabled = Boolean(st.ducking_enabled);
                        const duck = document.getElementById("mobileDuckingToggle");
                        if (duck) duck.checked = hostMicState.ducking_enabled;
                    }
                    if (st.ducking_db !== undefined) {
                        hostMicState.ducking_db = Number(st.ducking_db) || -6;
                        const dbSel = document.getElementById("mobileDuckingDbSelect");
                        if (dbSel) dbSel.value = String(hostMicState.ducking_db);
                    }
                    if (st.cdg_theme) {
                        hostMicState.cdg_theme = st.cdg_theme;
                        const cdgSel = document.getElementById("mobileCdgThemeSelect");
                        if (cdgSel) cdgSel.value = st.cdg_theme;
                    }
                    if (st.cdg_glow !== undefined) {
                        hostMicState.cdg_glow = Boolean(st.cdg_glow);
                        const cdgGlow = document.getElementById("mobileCdgGlowToggle");
                        if (cdgGlow) cdgGlow.checked = hostMicState.cdg_glow;
                    }
                }
            }
        } catch (e) { console.debug('[Tiki]', e); }

        const res = await fetch("/api/setup/audio-inputs");
        if (res.ok) {
            const devices = await res.json();
            sel.innerHTML = "";
            let matched = false;
            devices.forEach(d => {
                const opt = document.createElement("option");
                opt.value = d.id;
                opt.text = d.description || d.name;
                if (d.id === hostMicState.device) {
                    opt.selected = true;
                    matched = true;
                }
                sel.appendChild(opt);
            });
            if (!matched && hostMicState.device && hostMicState.device !== "default") {
                const opt = document.createElement("option");
                opt.value = hostMicState.device;
                opt.text = `Saved Device (${hostMicState.device.slice(0, 18)})`;
                opt.selected = true;
                sel.appendChild(opt);
            }
        }

        renderMobileMonitoringModeGating();
        updateMobileHostMicUI();
        const slider = document.getElementById("mobileHostMicSlider");
        if (slider) slider.value = hostMicState.volume;
        const volText = document.getElementById("mobileHostMicVolText");
        if (volText) volText.innerText = `${hostMicState.volume}%`;
        if (showFeedback && typeof showToast === "function") {
            showToast("🔄 Audio input devices refreshed!");
        }
    } catch (e) {
        console.warn("[Host Remote] Error loading audio inputs:", e);
    }
}

function hostSetMonitoringMode(mode) {
    if (!mode) return;
    hostMicState.monitoring_mode = mode;
    sendHostMicSettings({ monitoring_mode: mode });
    renderMobileMonitoringModeGating();
    updateMobileHostMicUI();
}

function renderMobileMonitoringModeGating() {
    const isHwDirect = hostMicState.monitoring_mode === "hardware_direct";
    const btn = document.getElementById("mobileHostMicToggleBtn");
    const slider = document.getElementById("mobileHostMicSlider");
    const hint = document.getElementById("mobileMicModeHint");
    const modeSel = document.getElementById("mobileMonitoringModeSelect");

    if (modeSel && hostMicState.monitoring_mode && modeSel.value !== hostMicState.monitoring_mode) {
        modeSel.value = hostMicState.monitoring_mode;
    }
    if (btn) {
        btn.disabled = isHwDirect;
        btn.title = isHwDirect ? "Hardware Direct mode: use interface knob" : "";
        btn.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (slider) {
        slider.disabled = isHwDirect;
        slider.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (hint) {
        hint.style.display = isHwDirect ? "block" : "none";
    }
    renderMobileMicAvailability();
}

function hostSetMicDevice(dev) {
    hostMicState.device = dev;
    sendHostMicSettings({ device: dev });
}

function hostToggleMic(muted) {
    if (muted === undefined) muted = !hostMicState.muted;
    hostMicState.muted = muted;
    sendHostMicSettings({ muted: hostMicState.muted });
    updateMobileHostMicUI();
}

function hostSetMicVolume(vol) {
    hostMicState.volume = Number(vol) || 80;
    sendHostMicSettings({ volume: hostMicState.volume });
    const label = document.getElementById("mobileHostMicVolText");
    if (label) label.innerText = `${hostMicState.volume}%`;
    updateMobileHostMicUI();
}

function hostToggleMicReverb(enabled) {
    hostMicState.reverb = !!enabled;
    sendHostMicSettings({ reverb: hostMicState.reverb });
}

function hostToggleMicAec(enabled) {
    hostMicState.echo_cancellation = !!enabled;
    sendHostMicSettings({ echo_cancellation: hostMicState.echo_cancellation });
}

function hostToggleDucking(enabled) {
    hostMicState.ducking_enabled = !!enabled;
    sendHostMicSettings({ ducking_enabled: hostMicState.ducking_enabled });
}

function hostSetDuckingDb(db) {
    hostMicState.ducking_db = Number(db) || -6;
    sendHostMicSettings({ ducking_db: hostMicState.ducking_db });
}

function hostSetCdgTheme(theme) {
    if (!theme) return;
    hostMicState.cdg_theme = theme;
    sendHostMicSettings({ cdg_theme: theme });
}

function hostToggleCdgGlow(enabled) {
    hostMicState.cdg_glow = !!enabled;
    sendHostMicSettings({ cdg_glow: hostMicState.cdg_glow });
}

function sendHostMicSettings(partial) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "host_mic_settings",
            monitoring_mode: hostMicState.monitoring_mode,
            ...partial
        }));
    }
}

function updateMobileHostMicUI() {
    const btn = document.getElementById("mobileHostMicToggleBtn");
    const badge = document.getElementById("mobileHostMicBadge");
    const vuBadge = document.getElementById("mobileVuStatusBadge");
    const barL = document.getElementById("mobileVuMeterBarL");
    const barR = document.getElementById("mobileVuMeterBarR");

    if (btn) {
        if (hostMicState.muted) {
            btn.innerHTML = "🔇 Stage Mic Muted (OFF)";
            btn.style.color = "#ff5555";
            btn.style.borderColor = "rgba(255,85,85,0.4)";
        } else {
            btn.innerHTML = "🎤 Stage Mic LIVE (ON)";
            btn.style.color = "#39ff14";
            btn.style.borderColor = "rgba(57,255,20,0.4)";
        }
    }

    let badgeLabel = "MUTED";
    let badgeColor = "#ff5555";
    let badgeBg = "rgba(255, 85, 85, 0.2)";
    let badgeBorder = "rgba(255, 85, 85, 0.4)";

    if (hostMicState.monitoring_mode === "hardware_direct") {
        badgeLabel = "DIRECT HARDWARE";
        badgeColor = "#39ff14";
        badgeBg = "rgba(57, 255, 20, 0.2)";
        badgeBorder = "rgba(57, 255, 20, 0.4)";
    } else if (hostMicState.monitoring_mode === "browser") {
        if (!hostMicState.muted) {
            badgeLabel = "LIVE (Browser WebRTC)";
            badgeColor = "#00f0ff";
            badgeBg = "rgba(0, 240, 255, 0.2)";
            badgeBorder = "rgba(0, 240, 255, 0.4)";
        }
    } else if (hostMicState.available === false) {
        badgeLabel = "NO NATIVE ROUTING";
        badgeColor = "#ffb703";
        badgeBg = "rgba(255, 183, 3, 0.2)";
        badgeBorder = "rgba(255, 183, 3, 0.4)";
    } else if (!hostMicState.muted) {
        badgeLabel = (hostMicState.latency_ms !== null && hostMicState.latency_ms > 0)
            ? `LIVE (~${Math.round(hostMicState.latency_ms)}ms)`
            : "LIVE";
        badgeColor = "#39ff14";
        badgeBg = "rgba(57, 255, 20, 0.2)";
        badgeBorder = "rgba(57, 255, 20, 0.4)";
    }

    if (badge) {
        badge.innerText = badgeLabel;
        badge.style.background = badgeBg;
        badge.style.color = badgeColor;
        badge.style.borderColor = badgeBorder;
    }
    if (vuBadge) {
        vuBadge.innerText = badgeLabel;
        vuBadge.style.color = badgeColor;
    }
    if (barL && barR) {
        if (hostMicState.muted || hostMicState.monitoring_mode === "hardware_direct" || (hostMicState.monitoring_mode === "native" && hostMicState.available === false)) {
            barL.style.width = "0%";
            barR.style.width = "0%";
        } else {
            const pct = Math.min(100, Math.max(15, Math.round((hostMicState.volume / 150) * 90)));
            barL.style.width = `${pct}%`;
            barR.style.width = `${pct}%`;
        }
    }
}

function renderMobileMicAvailability() {
    const warn = document.getElementById("mobileHostMicNativeWarning");
    if (!warn) return;
    if (hostMicState.available === false && hostMicState.monitoring_mode === "native") {
        const hint = document.getElementById("mobileHostMicNativeWarningHint");
        if (hint) {
            const isWin = /Windows/i.test(navigator.userAgent);
            hint.innerText = isWin
                ? "The tiki-mic-router helper was not found in the app's bin folder — reinstall, or copy tiki-mic-router.exe into bin\\ next to TikiKaraoke.exe."
                : "Install PipeWire or PulseAudio (pactl) on the host system to enable low-latency mic monitoring.";
        }
        warn.style.display = "block";
    } else {
        warn.style.display = "none";
    }
}

function syncHostMicSettingsUI(data) {
    if (data.available !== undefined) {
        hostMicState.available = Boolean(data.available);
        renderMobileMicAvailability();
    }
    if (data.latency_ms !== undefined && data.latency_ms !== null) {
        hostMicState.latency_ms = Number(data.latency_ms);
    }
    if (data.monitoring_mode !== undefined && data.monitoring_mode) {
        hostMicState.monitoring_mode = data.monitoring_mode;
        renderMobileMonitoringModeGating();
        updateMobileHostMicUI();
    }
    if (data.device !== undefined && data.device) {
        hostMicState.device = data.device;
        const sel = document.getElementById("mobileHostMicDeviceSelect");
        if (sel) {
            let opt = Array.from(sel.options).find(o => o.value === data.device);
            if (!opt) {
                opt = document.createElement("option");
                opt.value = data.device;
                opt.text = data.device;
                sel.appendChild(opt);
            }
            sel.value = data.device;
        }
    }
    if (data.muted !== undefined) {
        hostMicState.muted = !!data.muted;
        updateMobileHostMicUI();
    }
    if (data.volume !== undefined) {
        hostMicState.volume = Number(data.volume);
        const slider = document.getElementById("mobileHostMicSlider");
        if (slider) slider.value = hostMicState.volume;
        const volText = document.getElementById("mobileHostMicVolText");
        if (volText) volText.innerText = `${hostMicState.volume}%`;
        updateMobileHostMicUI();
    }
    if (data.reverb !== undefined) {
        hostMicState.reverb = !!data.reverb;
        const rev = document.getElementById("mobileMicReverbToggle");
        if (rev) rev.checked = hostMicState.reverb;
    }
    if (data.echo_cancellation !== undefined) {
        hostMicState.echo_cancellation = !!data.echo_cancellation;
        const aec = document.getElementById("mobileHostMicAecToggle");
        if (aec) aec.checked = hostMicState.echo_cancellation;
    }
    if (data.ducking_enabled !== undefined) {
        hostMicState.ducking_enabled = !!data.ducking_enabled;
        const duck = document.getElementById("mobileDuckingToggle");
        if (duck) duck.checked = hostMicState.ducking_enabled;
    }
    if (data.ducking_db !== undefined) {
        hostMicState.ducking_db = Number(data.ducking_db);
        const sel = document.getElementById("mobileDuckingDbSelect");
        if (sel) sel.value = String(hostMicState.ducking_db);
    }
    if (data.cdg_theme !== undefined && data.cdg_theme) {
        hostMicState.cdg_theme = data.cdg_theme;
        const cdgSel = document.getElementById("mobileCdgThemeSelect");
        if (cdgSel) cdgSel.value = data.cdg_theme;
    }
    if (data.cdg_glow !== undefined) {
        hostMicState.cdg_glow = !!data.cdg_glow;
        const cdgGlow = document.getElementById("mobileCdgGlowToggle");
        if (cdgGlow) cdgGlow.checked = hostMicState.cdg_glow;
    }
}

function loadHostSettings() {
    loadHostMicDevices();
    fetch("/api/settings")
        .then(res => res.json())
        .then(settings => {
            const ttsCheck = document.getElementById("ttsToggle");
            if (ttsCheck) ttsCheck.checked = (settings.tts_enabled === "true");
            const speedSelect = document.getElementById("hostMarqueeSpeedSelect");
            if (speedSelect && settings.marquee_scroll_speed) {
                speedSelect.value = settings.marquee_scroll_speed;
            }
            if (settings.intermission_vibe_mode) {
                updateVibeModeUI(settings.intermission_vibe_mode);
            }
        });
}

// Venue A/V Sync (Audio Latency Offset)
function loadVenueAvSync() {
    const badge = document.getElementById("avSyncBadge");
    if (!badge) return;

    fetch("/api/host/av-sync")
        .then(res => res.json())
        .then(data => {
            currentAvSyncOffset = data.offset_ms || 0;
            updateAvSyncUI();
        })
        .catch(err => {
            console.error("[AVSync] Failed to load sync offset:", err);
        });
}

function updateAvSyncUI() {
    const badge = document.getElementById("avSyncBadge");
    const desc = document.getElementById("avSyncDesc");
    if (!badge) return;

    const sign = currentAvSyncOffset > 0 ? "+" : "";
    badge.innerText = `${sign}${currentAvSyncOffset} ms`;

    if (currentAvSyncOffset === 0) {
        badge.style.color = "var(--neon-gold)";
        badge.style.background = "rgba(255, 183, 3, 0.18)";
        if (desc) desc.innerText = "Direct PA / zero lag. Adjust if TV picture trails behind the speakers.";
    } else if (currentAvSyncOffset > 0) {
        badge.style.color = "var(--neon-cyan)";
        badge.style.background = "rgba(0, 240, 255, 0.18)";
        if (desc) desc.innerText = `Audio delayed by ${currentAvSyncOffset}ms to match HDMI TV / projector video lag.`;
    } else {
        badge.style.color = "#ffaa00";
        badge.style.background = "rgba(255, 170, 0, 0.18)";
        if (desc) desc.innerText = `Audio advanced by ${Math.abs(currentAvSyncOffset)}ms for Bluetooth / wireless PA delay.`;
    }

    // Highlight matching preset button
    document.querySelectorAll(".av-preset-btn").forEach(btn => {
        const val = parseInt(btn.dataset.offset, 10);
        if (val === currentAvSyncOffset) {
            btn.style.borderColor = "var(--neon-cyan)";
            btn.style.color = "var(--neon-cyan)";
            btn.style.background = "rgba(0, 240, 255, 0.15)";
        } else {
            btn.style.borderColor = "rgba(255,255,255,0.15)";
            btn.style.color = "var(--text-main)";
            btn.style.background = "transparent";
        }
    });
}

function setVenueAvSyncPreset(offsetMs) {
    currentAvSyncOffset = parseInt(offsetMs, 10) || 0;
    updateAvSyncUI();

    fetch("/api/host/av-sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offset_ms: currentAvSyncOffset })
    })
    .then(res => res.json())
    .then(data => {
        const sign = data.offset_ms > 0 ? "+" : "";
        showToast(`🎛️ Venue A/V Sync: ${sign}${data.offset_ms} ms`);
    })
    .catch(err => {
        console.error("[AVSync] Failed to set sync offset:", err);
        showToast("⚠️ Failed to update A/V sync");
    });
}

function nudgeVenueAvSync(deltaMs) {
    const newOffset = Math.max(-300, Math.min(500, currentAvSyncOffset + deltaMs));
    setVenueAvSyncPreset(newOffset);
}

// Helpers
function showToast(msg, isError = false) {
    const toast = document.createElement("div");
    toast.style.position = "fixed";
    toast.style.bottom = "80px";
    toast.style.left = "50%";
    toast.style.transform = "translateX(-50%)";
    toast.style.background = isError ? "rgba(48, 12, 22, 0.96)" : "rgba(16, 28, 45, 0.95)";
    toast.style.border = isError ? "1px solid #ff5555" : "1px solid var(--neon-cyan)";
    toast.style.color = "#fff";
    toast.style.padding = "10px 18px";
    toast.style.borderRadius = "20px";
    toast.style.fontSize = "0.9rem";
    toast.style.zIndex = "1000";
    toast.style.boxShadow = isError ? "0 0 18px rgba(255, 85, 85, 0.55)" : "0 0 15px rgba(0, 240, 255, 0.5)";
    toast.innerText = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), isError ? 3500 : 2500);
}

function formatDuration(sec) {
    if (!sec) return "";
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
}

function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function setCookie(name, val, days = 365) {
    const d = new Date();
    d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
    document.cookie = `${name}=${encodeURIComponent(val)};expires=${d.toUTCString()};path=/;SameSite=Lax`;
}

function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
}

function toggleHostInstructionsModal(show) {
    const modal = document.getElementById("hostInstructionsModal");
    if (modal) {
        modal.style.display = show ? "flex" : "none";
    }
}

// Host Singer Roster & Mass Queue Removal
function loadHostSingerRoster() {
    const container = document.getElementById("hostSingerRosterContainer");
    if (!container) return;

    container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 10px;">Loading singer roster...</div>`;

    fetch("/api/host/singers")
        .then(res => {
            if (!res.ok) throw new Error("Unauthorized or server error");
            return res.json();
        })
        .then(singers => {
            container.innerHTML = "";
            if (!singers || singers.length === 0) {
                container.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 10px;">No singers registered yet.</div>`;
                return;
            }

            singers.forEach(s => {
                const pendingSongs = s.songs ? s.songs.filter(song => song.status === 'pending') : [];
                const playingSongs = s.songs ? s.songs.filter(song => song.status === 'playing') : [];
                const totalActive = pendingSongs.length + playingSongs.length;

                const card = document.createElement("div");
                card.style.background = "rgba(8, 14, 24, 0.75)";
                card.style.border = totalActive > 0 ? "1px solid rgba(0, 240, 255, 0.35)" : "1px solid rgba(255, 255, 255, 0.08)";
                card.style.borderRadius = "12px";
                card.style.padding = "12px";

                let songsHtml = "";
                if (s.songs && s.songs.length > 0) {
                    songsHtml = `<div style="margin-top: 8px; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 6px;">`;
                    s.songs.forEach(song => {
                        const statusBadge = song.status === 'playing' 
                            ? `<span style="background: rgba(255,42,133,0.25); color: var(--neon-pink); font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">PLAYING</span>`
                            : `<span style="background: rgba(0,240,255,0.15); color: var(--neon-cyan); font-size: 0.7rem; font-weight: 600; padding: 2px 6px; border-radius: 4px;">Pending</span>`;
                        songsHtml += `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 0.8rem;">
                                <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 75%;">
                                    🎵 <strong>${escapeHtml(song.title)}</strong> <span style="color: var(--text-muted); font-size: 0.75rem;">- ${escapeHtml(song.artist)}</span>
                                </div>
                                <div>${statusBadge}</div>
                            </div>
                        `;
                    });
                    songsHtml += `</div>`;
                } else {
                    songsHtml = `<div style="margin-top: 6px; font-size: 0.75rem; color: var(--text-muted); font-style: italic;">No songs currently in queue</div>`;
                }

                let removeBtnHtml = "";
                if (pendingSongs.length > 0) {
                    removeBtnHtml = `
                        <button onclick="openHostSingerManageModal(${s.singer_id}, '${escapeHtml(s.singer_name)}', '${s.singer_avatar || '🌺'}', ${pendingSongs.length})" 
                                class="btn-ghost" 
                                style="margin-top: 10px; width: 100%; padding: 8px; font-size: 0.8rem; font-weight: 600; color: #ff5555; border-color: rgba(255,85,85,0.35); background: rgba(255,85,85,0.08); border-radius: 8px; cursor: pointer;">
                            🗑️ Manage / Remove ${pendingSongs.length} Queued Song${pendingSongs.length > 1 ? 's' : ''}
                        </button>
                    `;
                } else {
                    removeBtnHtml = `
                        <button onclick="openHostSingerManageModal(${s.singer_id}, '${escapeHtml(s.singer_name)}', '${s.singer_avatar || '🌺'}', 0)" 
                                class="btn-ghost" 
                                style="margin-top: 10px; width: 100%; padding: 6px; font-size: 0.75rem; font-weight: 600; color: var(--text-muted); border-color: rgba(255,255,255,0.15); border-radius: 8px; cursor: pointer;">
                            👋 Check Out Singer (Left Party)
                        </button>
                    `;
                }

                const isBreak = s.singer_status === "on_break";
                const statusBadge = isBreak
                    ? `<span style="background: rgba(255,183,3,0.15); color: var(--neon-gold); border: 1px solid var(--neon-gold); font-size: 0.68rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">⏸️ BREAK</span>`
                    : `<span style="background: rgba(57,255,20,0.15); color: var(--neon-lime); border: 1px solid var(--neon-lime); font-size: 0.68rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">🟢 ACTIVE</span>`;
                
                const toggleStatusBtn = isBreak
                    ? `<button onclick="toggleHostSingerStatus(${s.singer_id}, 'active')" class="btn-ghost" style="padding: 3px 8px; font-size: 0.72rem; border-color: var(--neon-lime); color: var(--neon-lime); border-radius: 6px; cursor: pointer;">▶️ Resume</button>`
                    : `<button onclick="toggleHostSingerStatus(${s.singer_id}, 'on_break')" class="btn-ghost" style="padding: 3px 8px; font-size: 0.72rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px; cursor: pointer;">⏸️ Break</button>`;

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <span style="font-size: 1.3rem;">${s.singer_avatar || '🌺'}</span>
                            <span style="font-weight: 700; font-size: 0.95rem; color: #fff;">${escapeHtml(s.singer_name)}</span>
                            ${statusBadge}
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            ${toggleStatusBtn}
                            <span style="font-size: 0.75rem; color: ${totalActive > 0 ? 'var(--neon-gold)' : 'var(--text-muted)'}; font-weight: 600;">
                                ${totalActive} song${totalActive === 1 ? '' : 's'}
                            </span>
                        </div>
                    </div>
                    ${songsHtml}
                    ${removeBtnHtml}
                `;
                container.appendChild(card);
            });
        })
        .catch(err => {
            container.innerHTML = `<div style="text-align: center; color: #ff5555; font-size: 0.8rem; padding: 10px;">Failed to load roster: ${err.message}</div>`;
        });
}

async function toggleHostSingerStatus(singerId, newStatus) {
    try {
        const res = await fetch(`/api/host/singer/${singerId}/status`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Host-Token": typeof HOST_TOKEN !== "undefined" ? HOST_TOKEN : ""
            },
            body: JSON.stringify({ status: newStatus })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update singer status");
        showToast(newStatus === "on_break" ? "⏸️ Singer placed on break (queue held)" : "🟢 Singer active in rotation!");
        loadHostSingerRoster();
        loadLiveQueue();
    } catch (e) {
        showToast(`⚠️ ${e.message}`);
    }
}

let activeManageSinger = null;

function openHostSingerManageModal(singerId, singerName, singerAvatar, pendingCount) {
    activeManageSinger = { singerId, singerName, singerAvatar, pendingCount };
    const modal = document.getElementById("hostSingerManageModal");
    if (!modal) return;

    const avEl = document.getElementById("manageSingerAvatar");
    const nameEl = document.getElementById("manageSingerName");
    const nameTextEl = document.getElementById("manageSingerNameText");
    const countEl = document.getElementById("manageSingerQueueCount");
    const clearBtn = document.getElementById("manageClearQueueOnlyBtn");

    if (avEl) avEl.innerText = singerAvatar || "🌺";
    if (nameEl) nameEl.innerText = singerName;
    if (nameTextEl) nameTextEl.innerText = singerName;
    if (countEl) {
        countEl.innerText = pendingCount > 0 ? `${pendingCount} song${pendingCount === 1 ? '' : 's'} in queue` : "No songs in queue";
    }
    if (clearBtn) {
        clearBtn.style.display = pendingCount > 0 ? "flex" : "none";
    }

    modal.style.display = "flex";
}

function closeHostSingerManageModal() {
    activeManageSinger = null;
    const modal = document.getElementById("hostSingerManageModal");
    if (modal) modal.style.display = "none";
}

function confirmHostSingerAction(checkout) {
    if (!activeManageSinger) return;
    const { singerId, singerName, pendingCount } = activeManageSinger;
    closeHostSingerManageModal();

    fetch(`/api/host/singer/${singerId}/remove-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkout: !!checkout })
    })
    .then(res => res.json())
    .then(data => {
        if (checkout) {
            showToast(`👋 ${singerName} checked out! Name and icon are now free.`);
        } else {
            showToast(`Cleared ${data.deleted_count || pendingCount} song(s) for ${singerName}. Profile remains active.`);
        }
        loadHostSingerRoster();
        loadLiveQueue();
    })
    .catch(err => {
        alert("Error updating singer: " + err.message);
    });
}

function singerLeaveParty() {
    if (!confirm("Are you leaving the party? This will clear any queued songs and free your nickname and avatar for other guests.")) {
        return;
    }
    const token = (currentSinger && currentSinger.token) || localStorage.getItem("tiki_token") || getCookie("tiki_token") || "";
    const savedName = (currentSinger && currentSinger.name) || localStorage.getItem("tiki_name") || getCookie("tiki_name") || "Singer";
    const savedAvatar = (currentSinger && currentSinger.avatar) || localStorage.getItem("tiki_avatar") || getCookie("tiki_avatar") || "🌺";

    fetch("/api/singer/leave", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token })
    })
    .then(res => res.json())
    .then(() => {
        // Keep tiki_token, tiki_name, and tiki_avatar intact so My Songs & Live Recordings stay linked!
        localStorage.setItem("tiki_checked_out", "1");
        setCookie("tiki_checked_out", "1", 7);
        currentSinger = null;
        const onboarding = document.getElementById("onboardingModal");
        if (onboarding) onboarding.style.display = "none";
        updateSingerHeader();
        showLeftPartyModal(savedName, savedAvatar);
        showToast("You have checked out of the party. Have a great night! 🌺");
        loadLiveQueue();
    })
    .catch(err => {
        alert("Error checking out: " + err.message);
    });
}

function showLeftPartyModal(name, avatar) {
    const onboarding = document.getElementById("onboardingModal");
    if (onboarding) onboarding.style.display = "none";
    const modal = document.getElementById("leftPartyModal");
    if (!modal) return;
    const nameEl = document.getElementById("leftPartyNameDisplay");
    const avEl = document.getElementById("leftPartyAvatarDisplay");
    if (nameEl) nameEl.innerText = (name && name !== "undefined") ? name : "Singer";
    if (avEl) avEl.innerText = (avatar && avatar !== "undefined") ? avatar : "🌺";
    modal.style.display = "flex";
}

function rejoinParty() {
    localStorage.removeItem("tiki_checked_out");
    setCookie("tiki_checked_out", "", -1);
    const leftModal = document.getElementById("leftPartyModal");
    if (leftModal) leftModal.style.display = "none";

    const token = localStorage.getItem("tiki_token") || getCookie("tiki_token");
    const name = localStorage.getItem("tiki_name") || getCookie("tiki_name");
    const avatar = localStorage.getItem("tiki_avatar") || getCookie("tiki_avatar") || "🌺";

    if (token && name && name !== "undefined") {
        fetch("/api/singer/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token, name, avatar })
        })
        .then(async res => {
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || "Your previous avatar was taken while you were away. Please pick an available avatar!");
            }
            return res.json();
        })
        .then(data => {
            currentSinger = data;
            localStorage.setItem("tiki_token", data.token);
            localStorage.setItem("tiki_name", data.name);
            localStorage.setItem("tiki_avatar", data.avatar || "🌺");
            setCookie("tiki_token", data.token, 365);
            setCookie("tiki_name", data.name, 365);
            setCookie("tiki_avatar", data.avatar || "🌺", 365);
            updateSingerHeader();
            loadLiveQueue();
            showToast(`Welcome back to the party, ${data.name}! ${data.avatar}`);
        })
        .catch(err => {
            showOnboardingModal();
            const errEl = document.getElementById("nameErrorMsg");
            if (errEl) {
                errEl.innerText = `⚠️ ${err.message}`;
                errEl.style.display = "block";
            }
        });
    } else {
        showOnboardingModal();
    }
}

function rejoinPartyWithNewName() {
    localStorage.removeItem("tiki_checked_out");
    setCookie("tiki_checked_out", "", -1);
    const leftModal = document.getElementById("leftPartyModal");
    if (leftModal) leftModal.style.display = "none";
    showOnboardingModal();
}

function hostPurgeAllSingers() {
    if (!confirm("Are you sure you want to purge ALL registered singers and clear the current queue? This cannot be undone.")) {
        return;
    }
    fetch("/api/host/singers/purge-all", { method: "POST" })
        .then(res => {
            if (!res.ok) throw new Error("Unauthorized or server error");
            return res.json();
        })
        .then(data => {
            showToast(`Party reset! Purged ${data.singers_deleted || 0} singers.`);
            loadHostSingerRoster();
            loadLiveQueue();
        })
        .catch(err => {
            alert("Error purging singers: " + err.message);
        });
}

function loadHostDisplays() {
    const container = document.getElementById("hostDisplaysContainer");
    if (!container) return;

    fetch("/api/host/displays")
        .then(res => {
            if (!res.ok) throw new Error("Unauthorized or server error");
            return res.json();
        })
        .then(displays => {
            container.innerHTML = "";
            if (!displays || displays.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; color: var(--text-muted); font-size: 0.8rem; padding: 12px; background: rgba(8, 14, 24, 0.5); border-radius: 8px; border: 1px dashed rgba(255,255,255,0.1);">
                        No stage displays currently connected.<br>
                        <span style="font-size: 0.72rem; color: var(--neon-cyan);">Open <code>/stage</code> on your TV or laptop to link a display!</span>
                    </div>
                `;
                return;
            }

            displays.forEach(disp => {
                const card = document.createElement("div");
                card.style.background = "rgba(8, 14, 24, 0.75)";
                card.style.border = !disp.is_muted ? "1px solid rgba(0, 240, 255, 0.45)" : "1px solid rgba(255, 255, 255, 0.08)";
                card.style.borderRadius = "12px";
                card.style.padding = "10px 12px";

                const audioBadge = !disp.is_muted
                    ? `<span style="background: rgba(0,240,255,0.18); color: var(--neon-cyan); border: 1px solid rgba(0,240,255,0.4); font-size: 0.72rem; font-weight: 700; padding: 3px 8px; border-radius: 6px;">🔊 Master Audio</span>`
                    : `<span style="background: rgba(255,85,85,0.12); color: #ff5555; border: 1px solid rgba(255,85,85,0.3); font-size: 0.72rem; font-weight: 600; padding: 3px 8px; border-radius: 6px;">🔇 Muted</span>`;

                const masterBtn = disp.is_muted ? `
                    <button onclick="hostSetDisplayAudio('${escapeHtml(disp.display_id)}', 'set_master')" 
                            class="btn-neon-cyan" 
                            style="padding: 6px 10px; font-size: 0.78rem; font-weight: 700; border-radius: 6px; cursor: pointer;">
                        ⭐ Set Master Audio
                    </button>
                ` : `
                    <span style="font-size: 0.75rem; color: var(--neon-gold); font-weight: 600; display: flex; align-items: center; gap: 4px;">
                        👑 Primary Output
                    </span>
                `;

                const toggleMuteBtn = disp.is_muted ? `
                    <button onclick="hostSetDisplayAudio('${escapeHtml(disp.display_id)}', 'unmute')" 
                            class="btn-ghost" 
                            style="padding: 6px 10px; font-size: 0.78rem; border-radius: 6px; cursor: pointer;">
                        🔊 Unmute
                    </button>
                ` : `
                    <button onclick="hostSetDisplayAudio('${escapeHtml(disp.display_id)}', 'mute')" 
                            class="btn-ghost" 
                            style="padding: 6px 10px; font-size: 0.78rem; color: #ff5555; border-color: rgba(255,85,85,0.3); border-radius: 6px; cursor: pointer;">
                        🔇 Mute
                    </button>
                `;

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px; overflow: hidden;">
                            <span style="font-size: 1.2rem;">🖥️</span>
                            <div style="overflow: hidden;">
                                <div style="font-weight: 700; font-size: 0.88rem; color: #fff; white-space: nowrap; text-overflow: ellipsis; overflow: hidden;">
                                    ${escapeHtml(disp.name || "Stage Display")}
                                </div>
                                <div style="font-size: 0.68rem; color: var(--text-muted);">
                                    ${disp.ip ? `IP: ${escapeHtml(disp.ip)} • ` : ''}ID: ${escapeHtml(disp.display_id)}
                                </div>
                            </div>
                        </div>
                        <div>${audioBadge}</div>
                    </div>
                    <div style="display: flex; gap: 8px; justify-content: flex-end; align-items: center; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 8px;">
                        ${masterBtn}
                        ${toggleMuteBtn}
                    </div>
                `;
                container.appendChild(card);
            });
        })
        .catch(err => {
            container.innerHTML = `<div style="text-align: center; color: #ff5555; font-size: 0.8rem; padding: 10px;">Failed to load displays: ${err.message}</div>`;
        });
}

function hostSetDisplayAudio(displayId, action) {
    fetch(`/api/host/display/${encodeURIComponent(displayId)}/audio`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action })
    })
    .then(res => {
        if (!res.ok) throw new Error("Unauthorized or server error");
        return res.json();
    })
    .then(() => {
        if (action === "set_master") {
            showToast("Set display as sole Master Audio output! 🔊");
        } else if (action === "mute") {
            showToast("Muted display. 🔇");
        } else {
            showToast("Unmuted display. 🔊");
        }
        loadHostDisplays();
    })
    .catch(err => {
        alert("Error updating display audio: " + err.message);
    });
}

// ==========================================
// 🌴 Local Library Browsing & Smart Sorting
// ==========================================

let currentLibPage = 1;
let currentLibSort = "popularity";
let currentLibArtist = "";
let currentLibSearch = "";
let libSearchDebounceTimer = null;
let cachedLibraryArtists = [];

function formatRelativeTime(timestamp) {
    if (!timestamp) return "Never played";
    const date = new Date(timestamp.replace(" ", "T") + "Z");
    if (isNaN(date.getTime())) return "Recently";
    const diffSec = Math.floor((new Date() - date) / 1000);
    if (diffSec < 60) return "Just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    const days = Math.floor(diffSec / 86400);
    if (days === 1) return "Yesterday";
    if (days < 30) return `${days}d ago`;
    const months = Math.floor(days / 30);
    return `${months}mo ago`;
}

function setLibrarySort(sort) {
    currentLibSort = sort;
    currentLibPage = 1;
    document.querySelectorAll(".lib-sort-btn").forEach(btn => {
        if (btn.dataset.sort === sort) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    loadLibraryTracks();
}

function onLibrarySearchInput() {
    const input = document.getElementById("libSearchInput");
    const clearBtn = document.getElementById("libSearchClearBtn");
    if (clearBtn && input) {
        clearBtn.style.display = input.value.length > 0 ? "flex" : "none";
    }
    clearTimeout(libSearchDebounceTimer);
    libSearchDebounceTimer = setTimeout(() => {
        currentLibSearch = input ? input.value.trim() : "";
        currentLibPage = 1;
        loadLibraryTracks();
    }, 280);
}

function clearLibrarySearchInput() {
    const input = document.getElementById("libSearchInput");
    const clearBtn = document.getElementById("libSearchClearBtn");
    if (input) {
        input.value = "";
        input.focus();
    }
    if (clearBtn) {
        clearBtn.style.display = "none";
    }
    clearTimeout(libSearchDebounceTimer);
    currentLibSearch = "";
    currentLibPage = 1;
    loadLibraryTracks();
}

function onLibraryArtistChange() {
    const sel = document.getElementById("libArtistFilter");
    currentLibArtist = sel ? sel.value : "";
    currentLibPage = 1;
    loadLibraryTracks();
}

function changeLibraryPage(delta) {
    currentLibPage += delta;
    if (currentLibPage < 1) currentLibPage = 1;
    loadLibraryTracks();
}

function loadLibraryTracks() {
    const container = document.getElementById("libraryListContainer");
    if (!container) return;

    const url = `/api/library?page=${currentLibPage}&limit=25&sort=${encodeURIComponent(currentLibSort)}&artist=${encodeURIComponent(currentLibArtist)}&q=${encodeURIComponent(currentLibSearch)}&sing_along=${isSingAlongMode}`;

    fetch(url)
        .then(res => res.json())
        .then(data => {
            const tracks = Array.isArray(data.tracks) ? data.tracks : [];
            const total = data.total || 0;
            const totalPages = data.total_pages || 1;
            currentLibPage = data.page || 1;

            // Update badge
            const badge = document.getElementById("libraryTotalBadge");
            if (badge) badge.innerText = `${total} songs`;

            // Populate artist dropdown if changed
            if (Array.isArray(data.artists) && JSON.stringify(data.artists) !== JSON.stringify(cachedLibraryArtists)) {
                cachedLibraryArtists = data.artists;
                const sel = document.getElementById("libArtistFilter");
                if (sel) {
                    const currentVal = sel.value;
                    sel.innerHTML = '<option value="">All Artists</option>';
                    data.artists.forEach(a => {
                        const opt = document.createElement("option");
                        opt.value = a;
                        opt.innerText = a;
                        if (a === currentVal) opt.selected = true;
                        sel.appendChild(opt);
                    });
                }
            }

            container.innerHTML = "";

            if (tracks.length === 0) {
                const isFiltered = !!(currentLibSearch || currentLibArtist);
                container.innerHTML = `
                    <div class="tiki-card-cyan" style="text-align: center; padding: 32px 20px; margin-top: 10px; border-radius: 16px;">
                        <div style="font-size: 2.8rem; margin-bottom: 10px;">🌴🌺🍹</div>
                        <h3 class="font-tiki neon-cyan" style="font-size: 1.25rem; margin-bottom: 6px;">
                            ${isFiltered ? "No Matching Songs Found" : "Your Songbook is Ready!"}
                        </h3>
                        <p style="font-size: 0.85rem; color: #cbd5e1; max-width: 440px; margin: 0 auto 16px; line-height: 1.45;">
                            ${isFiltered 
                                ? "Try clearing your artist filter or searching with a different keyword." 
                                : "Tap below to search millions of karaoke tracks online, or request songs directly to get the party started!"}
                        </p>
                        <button type="button" onclick="switchTab('searchTab'); setTimeout(() => { const inp = document.getElementById('searchInput'); if(inp) inp.focus(); }, 150);" class="btn-neon-cyan" style="padding: 12px 28px; font-weight: 700; border-radius: 24px; font-size: 0.92rem; cursor: pointer;">
                            🔍 Search Songs Online
                        </button>
                    </div>
                `;
                const pag = document.getElementById("libraryPaginationBar");
                if (pag) pag.style.display = "none";
                return;
            }

            tracks.forEach(track => {
                const card = document.createElement("div");
                card.className = "tiki-card";
                card.style.padding = "14px";
                card.style.marginBottom = "10px";
                card.style.display = "flex";
                card.style.justifyContent = "space-between";
                card.style.alignItems = "center";
                card.style.gap = "10px";

                const plays = track.play_count || 0;
                let playBadgeText = plays === 1 ? "🎤 1 play" : `🎤 ${plays} plays`;
                let playBadgeColor = "rgba(0, 240, 255, 0.15)";
                let playTextColor = "var(--neon-cyan)";

                if (plays >= 5) {
                    playBadgeText = `🔥 ${plays} plays`;
                    playBadgeColor = "rgba(255, 42, 133, 0.2)";
                    playTextColor = "var(--neon-pink)";
                } else if (plays === 0) {
                    playBadgeText = "🆕 Unplayed";
                    playBadgeColor = "rgba(255, 255, 255, 0.08)";
                    playTextColor = "var(--text-muted)";
                }

                const lastPlayedText = formatRelativeTime(track.last_played);

                card.innerHTML = `
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(track.title)}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px;">
                            ${escapeHtml(track.artist)}
                        </div>
                        <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; font-size: 0.75rem;">
                            <span style="background: ${playBadgeColor}; color: ${playTextColor}; padding: 2px 7px; border-radius: 6px; font-weight: 700;">
                                ${playBadgeText}
                            </span>
                            ${track.is_sing_along ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                            <span style="color: var(--neon-gold);">
                                🕒 ${lastPlayedText}
                            </span>
                            <span style="color: var(--text-muted);">
                                • ${formatDuration(track.duration)}
                            </span>
                        </div>
                    </div>
                    <button class="btn-neon-pink lib-queue-btn" style="padding: 9px 14px; font-size: 0.85rem; white-space: nowrap; flex-shrink: 0;">
                        + Queue
                    </button>
                `;
                card.querySelector(".lib-queue-btn").onclick = () => openQueueModal(track.id, track.title, track.artist, track.video_id, 0, Boolean(track.is_sing_along || isSingAlongMode));
                container.appendChild(card);
            });

            // Pagination Controls
            const pag = document.getElementById("libraryPaginationBar");
            const prevBtn = document.getElementById("libPrevBtn");
            const nextBtn = document.getElementById("libNextBtn");
            const pageInfo = document.getElementById("libPageInfo");

            if (pag) {
                if (totalPages > 1) {
                    pag.style.display = "flex";
                    if (pageInfo) pageInfo.innerText = `Page ${currentLibPage} of ${totalPages} (${total} total)`;
                    if (prevBtn) prevBtn.disabled = currentLibPage <= 1;
                    if (nextBtn) nextBtn.disabled = currentLibPage >= totalPages;
                } else {
                    pag.style.display = "none";
                }
            }
        })
        .catch(err => {
            console.error("[Library] Error loading tracks:", err);
            container.innerHTML = `<div style="text-align: center; color: #ff5555; padding: 20px;">Failed to load library: ${err.message}</div>`;
        });
}

// ==========================================
// 💾 Host Storage Monitoring & Pruning
// ==========================================

let currentCleanupSort = "oldest_played";
let currentCleanupTab = "all";
let selectedCleanupTrackIds = new Set();
let selectedCleanupFillerIds = new Set();
let cleanupCandidatesList = [];

function loadHostStorageStatus() {
    const banner = document.getElementById("hostStorageBanner");
    const badge = document.getElementById("storageBadge");
    const pBar = document.getElementById("storageProgressBar");
    const details = document.getElementById("storageDetails");
    if (!banner || !badge) return;

    fetch("/api/storage/status")
        .then(res => res.json())
        .then(data => {
            const usedPct = data.used_percent || 0;
            const freeGb = data.free_gb || 0;
            const totalGb = data.total_gb || 0;
            const libGb = data.library_gb || 0;
            const tracks = data.downloaded_track_count || 0;

            // Thresholds: green default, orange at >= 75%, red at >= 90%
            let colorHex = "var(--neon-lime)";
            let badgeBg = "rgba(0, 255, 100, 0.15)";
            let badgeText = `${usedPct}% Full`;

            if (usedPct >= 90.0) {
                colorHex = "#ff3366";
                badgeBg = "rgba(255, 51, 102, 0.25)";
                badgeText = `🚨 ${usedPct}% Full (Critical)`;
            } else if (usedPct >= 75.0) {
                colorHex = "#ff9f1c";
                badgeBg = "rgba(255, 159, 28, 0.22)";
                badgeText = `⚠️ ${usedPct}% Full`;
            }

            badge.innerText = badgeText;
            badge.style.color = colorHex;
            badge.style.background = badgeBg;

            if (pBar) {
                pBar.style.width = `${Math.min(100, usedPct)}%`;
                pBar.style.background = colorHex;
            }

            banner.style.borderLeftColor = colorHex;

            if (details) {
                details.innerText = `${freeGb} GB Free of ${totalGb} GB (${libGb} GB Karaoke Library • ${tracks} songs)`;
            }
            loadHostRecordingsStorageStatus();
        })
        .catch(err => {
            console.error("[Storage] Failed to load status:", err);
        });
}

function loadHostRecordingsStorageStatus() {
    const card = document.getElementById("hostRecordingsStorageCard");
    const badge = document.getElementById("recordingsStorageBadge");
    const pathText = document.getElementById("hostRecordingsPathText");
    const stats = document.getElementById("hostRecordingsStats");
    if (!card || !pathText) return;

    fetch("/api/host/storage/recordings")
        .then(res => res.json())
        .then(data => {
            if (data && data.status === "ok") {
                pathText.innerText = data.recordings_dir || "Default";
                if (badge) {
                    badge.innerText = data.is_custom ? "Custom Folder" : "Default Folder";
                    badge.style.color = data.is_custom ? "var(--neon-cyan)" : "var(--neon-pink)";
                    badge.style.background = data.is_custom ? "rgba(0, 240, 255, 0.15)" : "rgba(255, 42, 133, 0.15)";
                }
                if (stats) {
                    const mb = data.mb || 0;
                    const count = data.count || 0;
                    const singers = data.singers_count || 0;
                    stats.innerText = `${count} recordings (${mb} MB) across ${singers} singers`;
                }
            }
        })
        .catch(err => {
            console.error("[Recordings] Failed to load recordings storage:", err);
        });
}

function openRecordingsFolder() {
    fetch("/api/host/storage/recordings/open-folder", { method: "POST" })
        .then(res => res.json())
        .then(data => {
            if (data && data.status === "ok") {
                showToast("📂 Opened recordings folder on host PC!");
            } else {
                showToast("⚠️ Could not open folder: " + (data.detail || "Error"));
            }
        })
        .catch(err => {
            showToast("⚠️ Failed to open folder on host");
        });
}

function checkYtdlpStatus() {
    const badge = document.getElementById("ytdlpBadge");
    const text = document.getElementById("ytdlpStatusText");
    const btn = document.getElementById("updateYtdlpBtn");
    if (!badge || !text) return;

    fetch("/api/host/system/ytdlp-status")
        .then(res => res.json())
        .then(data => {
            if (data.update_available) {
                badge.innerText = `Update Available (v${data.latest_version})`;
                badge.style.color = "var(--neon-gold)";
                badge.style.background = "rgba(255, 183, 3, 0.18)";
                text.innerText = `Installed: v${data.current_version} • Latest: v${data.latest_version}`;
                if (btn) {
                    btn.innerText = "⚡ Update Now";
                    btn.style.color = "var(--neon-gold)";
                    btn.style.borderColor = "var(--neon-gold)";
                }
            } else {
                badge.innerText = `v${data.current_version} (Up to date)`;
                badge.style.color = "var(--neon-lime)";
                badge.style.background = "rgba(0, 255, 100, 0.15)";
                text.innerText = `yt-dlp v${data.current_version} is current and operational.`;
                if (btn) {
                    btn.innerText = "🔄 Re-check / Update";
                    btn.style.color = "var(--neon-cyan)";
                    btn.style.borderColor = "rgba(0, 240, 255, 0.4)";
                }
            }
        })
        .catch(err => {
            console.error("[System] Failed to check yt-dlp status:", err);
            badge.innerText = "Status Unknown";
            badge.style.color = "var(--text-muted)";
        });
}

function checkHostHealth() {
    const ffmpegBanner = document.getElementById("hostFfmpegBanner");
    fetch("/api/host/health")
        .then(res => res.json())
        .then(data => {
            if (ffmpegBanner) {
                ffmpegBanner.style.display = data.ffmpeg_installed ? "none" : "block";
            }
        })
        .catch(err => {
            console.error("[Host Health] Health check error:", err);
        });
}

let forceShowQuickstart = false;

function dismissQuickstartCard() {
    forceShowQuickstart = false;
    const card = document.getElementById("hostQuickstartCard");
    if (card) card.style.display = "none";
    try {
        localStorage.setItem("tiki_hide_quickstart", "true");
    } catch (e) { console.debug('[Tiki]', e); }
}

function showQuickstartCard() {
    const card = document.getElementById("hostQuickstartCard");
    if (!card) return;
    const isHidden = window.getComputedStyle(card).display === "none";
    if (isHidden) {
        forceShowQuickstart = true;
        card.style.display = "block";
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
        try {
            localStorage.removeItem("tiki_hide_quickstart");
        } catch (e) { console.debug('[Tiki]', e); }
    } else {
        forceShowQuickstart = false;
        card.style.display = "none";
    }
}

function openHelpGuideModal() {
    const m = document.getElementById("helpGuideModal");
    if (m) m.style.display = "flex";
}

function closeHelpGuideModal() {
    const m = document.getElementById("helpGuideModal");
    if (m) m.style.display = "none";
}

async function updateYtdlpEngine() {
    const btn = document.getElementById("updateYtdlpBtn");
    const badge = document.getElementById("ytdlpBadge");
    const text = document.getElementById("ytdlpStatusText");

    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Updating...";
    }
    if (badge) {
        badge.innerText = "Installing...";
        badge.style.color = "var(--neon-cyan)";
    }
    if (text) {
        text.innerText = "Upgrading yt-dlp package from PyPI, please wait...";
    }
    showToast("⚙️ Upgrading yt-dlp Video Engine in background...");

    try {
        const res = await fetch("/api/host/system/update-ytdlp", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.status === "success") {
            showToast(`🎉 yt-dlp updated: v${data.old_version} ➔ v${data.new_version}`);
            checkYtdlpStatus();
        } else {
            showToast(`⚠️ Update error: ${data.detail || data.output || "Failed"}`);
            checkYtdlpStatus();
        }
    } catch (err) {
        console.error("[System] yt-dlp update request failed:", err);
        showToast("❌ Network error while updating yt-dlp engine.");
        checkYtdlpStatus();
    } finally {
        if (btn) btn.disabled = false;
    }
}

function openCleanupModal() {
    const modal = document.getElementById("cleanupModal");
    if (modal) modal.style.display = "flex";
    selectedCleanupTrackIds.clear();
    selectedCleanupFillerIds.clear();
    loadCleanupCandidates();
}

function closeCleanupModal() {
    const modal = document.getElementById("cleanupModal");
    if (modal) modal.style.display = "none";
}
function setCleanupTab(tab) {
    currentCleanupTab = tab || "all";
    selectedCleanupTrackIds.clear();
    selectedCleanupFillerIds.clear();
    loadCleanupCandidates();
}

function setCleanupSort(sort) {
    currentCleanupSort = sort;
    const btnOldest = document.getElementById("cleanSortOldest");
    const btnLeast = document.getElementById("cleanSortLeast");

    if (sort === "oldest_played") {
        if (btnOldest) {
            btnOldest.style.borderColor = "var(--neon-cyan)";
            btnOldest.style.color = "var(--neon-cyan)";
        }
        if (btnLeast) {
            btnLeast.style.borderColor = "rgba(255,255,255,0.2)";
            btnLeast.style.color = "#fff";
        }
    } else {
        if (btnLeast) {
            btnLeast.style.borderColor = "var(--neon-cyan)";
            btnLeast.style.color = "var(--neon-cyan)";
        }
        if (btnOldest) {
            btnOldest.style.borderColor = "rgba(255,255,255,0.2)";
            btnOldest.style.color = "#fff";
        }
    }
    loadCleanupCandidates();
}

function loadCleanupCandidates() {
    const list = document.getElementById("cleanupCandidateList");
    if (!list) return;
    list.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">Scanning library files...</div>`;

    fetch(`/api/storage/cleanup-candidates?sort_by=${encodeURIComponent(currentCleanupSort)}&limit=60&tab=${encodeURIComponent(currentCleanupTab)}`)
        .then(res => res.json())
        .then(data => {
            cleanupCandidatesList = Array.isArray(data.candidates) ? data.candidates : [];
            list.innerHTML = "";

            if (cleanupCandidatesList.length === 0) {
                list.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">No downloaded video files found in this category.</div>`;
                updateCleanupStats();
                return;
            }

            cleanupCandidatesList.forEach(item => {
                const row = document.createElement("div");
                row.style.display = "flex";
                row.style.alignItems = "center";
                row.style.gap = "8px";
                row.style.padding = "8px 10px";
                row.style.background = "rgba(8, 14, 24, 0.85)";
                row.style.border = "1px solid rgba(255,255,255,0.1)";
                row.style.borderRadius = "10px";
                row.style.marginBottom = "4px";

                const isChecked = item.is_filler ? selectedCleanupFillerIds.has(item.id) : selectedCleanupTrackIds.has(item.id);

                const starterBadge = item.is_starter_pack 
                    ? `<span style="font-size:0.65rem; padding:1px 4px; border-radius:4px; background:rgba(255,42,133,0.18); color:var(--neon-pink); font-weight:600;" title="${escapeHtml(item.starter_pack_name || 'Starter Pack')}">📦 Starter</span>`
                    : "";

                row.innerHTML = `
                    <input type="checkbox" value="${item.id}" ${isChecked ? 'checked' : ''} onchange="toggleCleanupItem(${item.id}, ${item.is_filler ? 'true' : 'false'}, this.checked)" style="transform: scale(1.2); cursor: pointer; flex-shrink: 0;">
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; gap: 4px; margin-bottom: 2px;">
                            <div style="font-weight: 700; font-size: 0.84rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #fff;">
                                ${escapeHtml(item.title)}
                            </div>
                            ${starterBadge}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.74rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.artist)} • <span style="color: var(--neon-gold); font-weight: 600;">${item.size_mb} MB</span>
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 5px; flex-shrink: 0;">
                        <!-- Failsafe Type Switcher -->
                        <select onchange="convertCleanupTrackType(${item.id}, this.value, '${item.song_type}', ${item.is_filler ? 'true' : 'false'})" class="btn-ghost" style="padding: 2px 4px; font-size: 0.68rem; border-radius: 6px; background: rgba(0,0,0,0.6); color: #fff; border-color: rgba(255,255,255,0.25); cursor: pointer;">
                            <option value="karaoke" ${item.song_type === 'karaoke' ? 'selected' : ''}>🎤 Karaoke</option>
                            <option value="sing_along" ${item.song_type === 'sing_along' ? 'selected' : ''}>🎙️ Sing-Along</option>
                            <option value="music_video" ${item.song_type === 'music_video' ? 'selected' : ''}>🎬 Music Video</option>
                        </select>
                        <!-- Audit Preview Button -->
                        <button type="button" onclick="previewCleanupTrack(${item.id}, '${escapeHtml(item.title).replace(/'/g, "\\'")}', '${escapeHtml(item.artist).replace(/'/g, "\\'")}', '${item.video_id}', ${item.is_filler ? 'true' : 'false'})" class="btn-ghost" style="padding: 2px 6px; font-size: 0.75rem; border-color: var(--neon-cyan); color: var(--neon-cyan); border-radius: 6px;" title="Audit Video & Audio">
                            👁️
                        </button>
                        <!-- Edit Metadata Button -->
                        <button type="button" onclick="openEditTrackModal(${item.id}, '${escapeHtml(item.title).replace(/'/g, "\\'")}', '${escapeHtml(item.artist).replace(/'/g, "\\'")}', ${item.is_filler ? 'true' : 'false'})" class="btn-ghost" style="padding: 2px 6px; font-size: 0.75rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px;" title="Edit Title & Artist">
                            ✏️
                        </button>
                    </div>
                `;
                list.appendChild(row);
            });

            updateCleanupStats();
        })
        .catch(err => {
            list.innerHTML = `<div style="color: #ff5555; text-align: center; padding: 20px;">Failed to scan files: ${err.message}</div>`;
        });
}

function toggleCleanupItem(id, isFiller, isChecked) {
    if (isFiller) {
        if (isChecked) selectedCleanupFillerIds.add(id);
        else selectedCleanupFillerIds.delete(id);
    } else {
        if (isChecked) selectedCleanupTrackIds.add(id);
        else selectedCleanupTrackIds.delete(id);
    }
    updateCleanupStats();
}

function selectTopNCleanup(n) {
    cleanupCandidatesList.slice(0, n).forEach(item => {
        if (item.is_filler) selectedCleanupFillerIds.add(item.id);
        else selectedCleanupTrackIds.add(item.id);
    });
    // Check checkboxes
    document.querySelectorAll('#cleanupCandidateList input[type="checkbox"]').forEach((cb, idx) => {
        const item = cleanupCandidatesList[idx];
        if (item) {
            cb.checked = item.is_filler ? selectedCleanupFillerIds.has(item.id) : selectedCleanupTrackIds.has(item.id);
        }
    });
    updateCleanupStats();
}

function toggleAllCleanup() {
    const totalSelected = selectedCleanupTrackIds.size + selectedCleanupFillerIds.size;
    if (totalSelected === cleanupCandidatesList.length) {
        selectedCleanupTrackIds.clear();
        selectedCleanupFillerIds.clear();
    } else {
        cleanupCandidatesList.forEach(item => {
            if (item.is_filler) selectedCleanupFillerIds.add(item.id);
            else selectedCleanupTrackIds.add(item.id);
        });
    }
    document.querySelectorAll('#cleanupCandidateList input[type="checkbox"]').forEach((cb, idx) => {
        const item = cleanupCandidatesList[idx];
        if (item) {
            cb.checked = item.is_filler ? selectedCleanupFillerIds.has(item.id) : selectedCleanupTrackIds.has(item.id);
        }
    });
    updateCleanupStats();
}

function updateCleanupStats() {
    const statsEl = document.getElementById("cleanupSelectedStats");
    const btn = document.getElementById("pruneExecuteBtn");

    let totalMb = 0;
    cleanupCandidatesList.forEach(item => {
        const isSel = item.is_filler ? selectedCleanupFillerIds.has(item.id) : selectedCleanupTrackIds.has(item.id);
        if (isSel) {
            totalMb += item.size_mb || 0;
        }
    });
    totalMb = Math.round(totalMb * 10) / 10;

    const count = selectedCleanupTrackIds.size + selectedCleanupFillerIds.size;
    if (statsEl) {
        statsEl.innerText = `Selected: ${count} songs (${totalMb} MB)`;
    }

    if (btn) {
        btn.disabled = count === 0;
        btn.innerText = count > 0 ? `🗑️ Prune ${count} Files (${totalMb} MB)` : `🗑️ Prune Selected Files`;
    }
}

function executePrune() {
    const count = selectedCleanupTrackIds.size + selectedCleanupFillerIds.size;
    if (count === 0) return;
    if (!confirm(`Are you sure you want to delete ${count} video files from disk? History and songs can be re-downloaded anytime.`)) {
        return;
    }

    const btn = document.getElementById("pruneExecuteBtn");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "⏳ Deleting video files...";
    }

    fetch("/api/storage/prune", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            track_ids: Array.from(selectedCleanupTrackIds),
            filler_ids: Array.from(selectedCleanupFillerIds)
        })
    })
    .then(res => {
        if (!res.ok) throw new Error("Unauthorized or server error");
        return res.json();
    })
    .then(data => {
        showToast(`✅ Successfully pruned ${data.pruned_count} video files!`);
        selectedCleanupTrackIds.clear();
        selectedCleanupFillerIds.clear();
        closeCleanupModal();
        loadHostStorageStatus();
        loadLibraryTracks();
        if (typeof loadFillerTracks === 'function') loadFillerTracks();
    })
    .catch(err => {
        alert("Error pruning files: " + err.message);
        if (btn) {
            btn.disabled = false;
            updateCleanupStats();
        }
    });
}

