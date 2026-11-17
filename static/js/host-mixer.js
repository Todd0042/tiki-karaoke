let currentFillerTrack = null;
let fillerBufferTimer = null;
let isFillerPlaying = false;

function triggerFillerBuffer(durationSeconds) {
    stopCurrentTrack(null);
    stopFillerPlayback();
    isFillerPlaying = false;

    const overlay = document.getElementById("fillerBufferOverlay");
    if (!overlay) return;
    overlay.style.display = "flex";

    const intermission = document.getElementById("intermissionOverlay");
    if (intermission) intermission.style.display = "none";

    let remaining = durationSeconds || 15;
    const numEl = document.getElementById("fillerCountdownNumber");
    if (numEl) numEl.innerText = remaining;

    if (fillerBufferTimer) clearInterval(fillerBufferTimer);
    fillerBufferTimer = setInterval(() => {
        remaining--;
        if (numEl) numEl.innerText = Math.max(0, remaining);
        if (remaining <= 0) {
            clearInterval(fillerBufferTimer);
            fillerBufferTimer = null;
        }
    }, 1000);

    fadeInBGM();
}

function triggerTickerScroll(resetAnimation = true) {
    const tickerBar = document.getElementById("fillerTickerBar");
    const tickerText = document.getElementById("fillerTickerText");
    if (!tickerBar || !tickerText) return;

    tickerBar.style.display = "flex";
    tickerText.style.display = "inline-block";
    tickerText.style.whiteSpace = "nowrap";

    const containerWidth = tickerBar.offsetWidth || window.innerWidth || 1920;
    const textWidth = tickerText.scrollWidth || tickerText.offsetWidth || 800;

    const startPx = containerWidth;
    const endPx = -(Math.ceil(textWidth) + 60);

    tickerText.style.setProperty("--marquee-start", `${startPx}px`);
    tickerText.style.setProperty("--marquee-end", `${endPx}px`);

    const totalDistance = startPx + Math.abs(endPx);
    const speed = configuredMarqueeSpeed || 85;
    const duration = Math.max(12, Math.round(totalDistance / speed));

    if (resetAnimation) {
        tickerText.style.animation = "none";
        tickerText.offsetHeight; // trigger reflow to reset translation off-screen right
        tickerText.style.animation = `tickerMarquee ${duration}s linear infinite`;
    } else {
        tickerText.style.animationDuration = `${duration}s`;
    }
}

function startFillerPlayback(track) {
    if (!track) return;
    currentFillerTrack = track;
    isFillerPlaying = true;
    if (audioDSP) audioDSP.setBetweenSongs(true);

    if (fillerBufferTimer) {
        clearInterval(fillerBufferTimer);
        fillerBufferTimer = null;
    }
    const bufferOverlay = document.getElementById("fillerBufferOverlay");
    if (bufferOverlay) bufferOverlay.style.display = "none";

    stopIntermission();

    const nowBar = document.getElementById("nowPlayingBar");
    if (nowBar) nowBar.style.display = "none";

    fadeOutBGM();

    // Hide standard join QR overlay so it doesn't clash with fillerQRCard
    const joinQR = document.getElementById("joinQROverlay");
    if (joinQR) joinQR.style.display = "none";

    const qrCard = document.getElementById("fillerQRCard");
    if (qrCard) qrCard.style.display = "flex";

    renderStageMarquee(true);

    const fillerVideo = document.getElementById("fillerVideo");
    const localVideo = document.getElementById("localVideo");
    const ytContainer = document.getElementById("youtubeContainer");

    if (track.is_downloaded && track.local_path) {
        if (localVideo) {
            localVideo.style.display = "none";
            localVideo.pause();
        }
        if (ytContainer) ytContainer.style.display = "none";
        if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();

        if (fillerVideo) {
            fillerVideo.style.display = "block";
            fillerVideo.src = track.stream_url || (track.is_sing_along ? `/stream/${track.id}` : `/stream/filler/${track.id}`);
            fillerVideo.volume = 0.85;
            if (isMutedAudio) {
                fillerVideo.muted = true;
            } else {
                fillerVideo.muted = false;
                if (configuredAudioSink && typeof fillerVideo.setSinkId === "function") {
                    fillerVideo.setSinkId(configuredAudioSink).catch(() => {});
                }
            }
            fillerVideo.onended = () => {
                if (!isSpectatorViewer) {
                    fetch("/api/host/filler-tracks/finished", { method: "POST" }).catch(() => {});
                }
            };
            fillerVideo.play().catch(e => {
                if (e.name === "AbortError") return;
                console.log("[Filler] Playback blocked:", e);
                const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
                const unmuteOverlay = document.getElementById("unmuteOverlay");
                if (isMobile && unmuteOverlay && fillerVideo.paused) {
                    unmuteOverlay.style.display = "flex";
                }
            });
        }
    } else {
        if (localVideo) localVideo.style.display = "none";
        if (fillerVideo) fillerVideo.style.display = "none";
        if (ytContainer) ytContainer.style.display = "block";

        if (ytPlayer && ytPlayer.loadVideoById) {
            ytPlayer.loadVideoById({ videoId: track.video_id });
            if (isMutedAudio && ytPlayer.mute) ytPlayer.mute();
            ytPlayer.playVideo();
        }
    }
}

function handleFillerTransitionFade(data) {
    const tickerBar = document.getElementById("fillerTickerBar");
    if (tickerBar) {
        tickerBar.style.background = "linear-gradient(90deg, rgba(255,42,133,0.95) 0%, rgba(255,183,3,0.95) 100%)";
        tickerBar.style.borderTop = "2px solid #fff";
    }
    const tickerText = document.getElementById("fillerTickerText");
    if (tickerText) {
        // Freeze animation and show prominent centered alert banner
        tickerText.style.animation = "none";
        tickerText.style.textAlign = "center";
        tickerText.style.width = "100%";
        tickerText.style.paddingLeft = "0";
        tickerText.style.color = "#fff";
        tickerText.style.fontSize = "1.05rem";
        tickerText.style.fontWeight = "800";
        tickerText.innerHTML = `⚡ <strong>STAGE RE-OPENING:</strong> <span style="color: #fff; text-decoration: underline;">${escapeHtml(data.next_singer)}</span> with "${escapeHtml(data.next_title)}"! <em>(Starting in 3s...)</em>`;
    }

    const fillerVideo = document.getElementById("fillerVideo");
    if (fillerVideo && fillerVideo.style.display !== "none") {
        let vol = fillerVideo.volume;
        const fadeInterval = setInterval(() => {
            vol -= 0.1;
            if (vol <= 0.05) {
                fillerVideo.volume = 0;
                fillerVideo.pause();
                clearInterval(fadeInterval);
            } else {
                fillerVideo.volume = Math.max(0, vol);
            }
        }, 300);
    }
}

function handleFillerTransitionFinish(data) {
    const tickerBar = document.getElementById("fillerTickerBar");
    if (tickerBar) {
        tickerBar.style.background = "linear-gradient(90deg, rgba(8,14,24,0.95) 0%, rgba(255,183,3,0.3) 100%)";
        tickerBar.style.borderTop = "2px solid var(--neon-gold)";
    }
    const tickerText = document.getElementById("fillerTickerText");
    if (tickerText) {
        // Display stationary alert banner for 4s so everyone can read it, then resume marquee from right
        tickerText.style.animation = "none";
        tickerText.style.textAlign = "center";
        tickerText.style.width = "100%";
        tickerText.style.paddingLeft = "0";
        tickerText.style.color = "var(--neon-gold)";
        tickerText.style.fontSize = "1rem";
        tickerText.style.fontWeight = "800";
        tickerText.innerHTML = `🎤 <strong>UP NEXT:</strong> ${escapeHtml(data.next_singer)} with "${escapeHtml(data.next_title)}" <em>(Right after this video!)</em> 🎶`;

        setTimeout(() => {
            if (isFillerPlaying && tickerText) {
                tickerText.style.textAlign = "left";
                tickerText.style.width = "auto";
                tickerText.style.paddingLeft = "0";
                tickerText.innerText = `🎤 UP NEXT: ${data.next_singer} with "${data.next_title}" by ${data.next_artist} right after this video! • 🌺 Scan QR to join the queue!`;
                triggerTickerScroll();
            }
        }, 4000);
    }
}

function stopFillerPlayback() {
    isFillerPlaying = false;
    currentFillerTrack = null;

    if (fillerBufferTimer) {
        clearInterval(fillerBufferTimer);
        fillerBufferTimer = null;
    }

    const bufferOverlay = document.getElementById("fillerBufferOverlay");
    if (bufferOverlay) bufferOverlay.style.display = "none";

    const qrCard = document.getElementById("fillerQRCard");
    if (qrCard) qrCard.style.display = "none";

    // Restore standard join QR overlay for karaoke playback and intermissions
    const joinQR = document.getElementById("joinQROverlay");
    if (joinQR) joinQR.style.display = "flex";

    const tickerBar = document.getElementById("fillerTickerBar");
    if (tickerBar) {
        tickerBar.style.display = "none";
        tickerBar.style.background = "rgba(8, 14, 24, 0.95)";
        tickerBar.style.borderTop = "1px solid rgba(0, 240, 255, 0.3)";
    }

    const tickerText = document.getElementById("fillerTickerText");
    if (tickerText) {
        tickerText.style.animation = "none";
        tickerText.style.textAlign = "left";
        tickerText.style.width = "auto";
        tickerText.style.color = "var(--neon-cyan)";
        tickerText.style.fontSize = "0.95rem";
        tickerText.style.fontWeight = "600";
    }
    activeMarqueeText = "";
    pendingMarqueeText = null;
    updateStageQrPosition();

    const fillerVideo = document.getElementById("fillerVideo");
    if (fillerVideo) {
        fillerVideo.pause();
        fillerVideo.style.display = "none";
        fillerVideo.removeAttribute("src");
        fillerVideo.volume = 1.0;
    }
}

// -------------------------------------------------------------
// Dynamic Stage Marquee & Announcements Engine
// -------------------------------------------------------------
let currentMarqueeState = null;
let activeMarqueeText = "";
let pendingMarqueeText = null;
let configuredMarqueeSpeed = 85;
let lastMarqueeSpeed = 85;

function updateStageQrPosition() {
    const joinQR = document.getElementById("joinQROverlay");
    if (!joinQR) return;
    const tickerBar = document.getElementById("fillerTickerBar");
    const isTickerVisible = tickerBar && tickerBar.style.display !== "none";
    const isSinging = Boolean(currentTrack);

    if (isSinging) {
        joinQR.style.bottom = isTickerVisible ? "136px" : "105px";
    } else {
        joinQR.style.bottom = isTickerVisible ? "46px" : "25px";
    }
}
window.updateStageQrPosition = updateStageQrPosition;

function setupMarqueeIterationListener() {
    const tickerText = document.getElementById("fillerTickerText");
    if (!tickerText || tickerText._hasIterationListener) return;
    tickerText._hasIterationListener = true;
    tickerText.addEventListener("animationiteration", () => {
        if (pendingMarqueeText && pendingMarqueeText !== activeMarqueeText) {
            activeMarqueeText = pendingMarqueeText;
            pendingMarqueeText = null;
            tickerText.innerHTML = activeMarqueeText;
            triggerTickerScroll(false);
        }
    });
}

function handleMarqueeUpdate(data) {
    if (data.speed) {
        configuredMarqueeSpeed = parseInt(data.speed, 10) || configuredMarqueeSpeed;
    }
    currentMarqueeState = {
        ...(currentMarqueeState || {}),
        message: data.message,
        mode: data.mode,
        expires_at: data.expires_at,
        speed: data.speed || configuredMarqueeSpeed
    };
    renderStageMarquee(true);
}

function renderStageMarquee(forceRestart = false) {
    const tickerBar = document.getElementById("fillerTickerBar");
    const tickerText = document.getElementById("fillerTickerText");
    if (!tickerBar || !tickerText) return;

    setupMarqueeIterationListener();

    const m = currentMarqueeState;
    if (m && m.mode === "off") {
        tickerBar.style.display = "none";
        tickerBar.style.bottom = "0px";
        activeMarqueeText = "";
        pendingMarqueeText = null;
        updateStageQrPosition();
        return;
    }

    let textToShow = "";
    if (m && m.message) {
        textToShow = `📢 <strong>HOST ANNOUNCEMENT:</strong> ${escapeHtml(m.message)} • 🌺 `;
    } else if (currentTrack && currentTrack.dedication) {
        textToShow = `💌 <strong>DEDICATED TO:</strong> ${escapeHtml(currentTrack.dedication)} • 🎤 ${escapeHtml(currentTrack.singer_name)} singing "${escapeHtml(currentTrack.title)}" • 🌺 `;
    } else if (m && m.queue_ticker) {
        textToShow = `🌴 ${escapeHtml(m.queue_ticker)} • 🌺 OPEN MIC: Scan QR to Sing! • `;
    } else if (stagePendingDownloads && stagePendingDownloads.length > 0 && (!currentQueue || currentQueue.filter(q => q.status === "pending").length === 0)) {
        const dl = stagePendingDownloads[0];
        const pct = Math.round(dl.progress || 0);
        textToShow = `🌴 Preparing next song for ${escapeHtml(dl.singer_name)} (${escapeHtml(dl.title)}) - Downloading ${pct}%... • 🌺 OPEN MIC: Scan QR to Sing! • `;
    } else if (isFillerPlaying && currentFillerTrack) {
        textToShow = currentFillerTrack.is_sing_along 
            ? `🎤 SING-ALONG INTERMISSION: ${escapeHtml(currentFillerTrack.artist)} - ${escapeHtml(currentFillerTrack.title)} • 🌺 Sing along with the stage or scan to queue next!`
            : `🌴 DJ LOUNGE MUSIC: ${escapeHtml(currentFillerTrack.artist)} - ${escapeHtml(currentFillerTrack.title)} • 🌺 OPEN MIC: Scan the QR code to queue your favorite song!`;
    }

    const isIntermission = document.getElementById("intermissionOverlay") && document.getElementById("intermissionOverlay").style.display !== "none";
    const isSinging = Boolean(currentTrack);
    
    let shouldShow = false;
    if (m && m.mode === "always") {
        shouldShow = true;
    } else if (m && m.message) {
        shouldShow = true;
    } else if (isSinging && currentTrack && currentTrack.dedication) {
        shouldShow = true;
    } else if (isIntermission || isFillerPlaying || (stagePendingDownloads && stagePendingDownloads.length > 0)) {
        shouldShow = true;
    }

    if (!shouldShow || !textToShow) {
        tickerBar.style.display = "none";
        tickerBar.style.bottom = "0px";
        activeMarqueeText = "";
        pendingMarqueeText = null;
        updateStageQrPosition();
        return;
    }

    tickerBar.style.display = "flex";
    tickerBar.style.bottom = isSinging ? "90px" : "0px";
    tickerBar.style.background = isSinging ? "rgba(8, 14, 24, 0.88)" : "rgba(8, 14, 24, 0.95)";
    updateStageQrPosition();

    // If text is already active and displaying, do not restart in-flight marquee!
    if (textToShow === activeMarqueeText && !forceRestart) {
        if (configuredMarqueeSpeed !== lastMarqueeSpeed) {
            lastMarqueeSpeed = configuredMarqueeSpeed;
            triggerTickerScroll(false); // Adjust animation duration in-place without resetting scroll
        }
        return;
    }

    // If in-flight crawl is already running and this is NOT a forced restart (e.g. background queue ticker changed),
    // defer applying the updated text until the current loop iteration completes!
    const isTickerActive = activeMarqueeText && tickerBar.style.display !== "none" && tickerText.style.animation && tickerText.style.animation !== "none";
    if (isTickerActive && !forceRestart) {
        pendingMarqueeText = textToShow;
        if (configuredMarqueeSpeed !== lastMarqueeSpeed) {
            lastMarqueeSpeed = configuredMarqueeSpeed;
            triggerTickerScroll(false);
        }
        return;
    }

    // New text or explicit restart
    pendingMarqueeText = null;
    activeMarqueeText = textToShow;
    lastMarqueeSpeed = configuredMarqueeSpeed;
    tickerText.style.textAlign = "left";
    tickerText.style.width = "auto";
    tickerText.style.color = "var(--neon-cyan)";
    tickerText.style.fontSize = "0.95rem";
    tickerText.style.fontWeight = "600";
    tickerText.innerHTML = textToShow;

    triggerTickerScroll(true);
}

// Window resize handler to maintain accurate marquee boundaries across monitor/display changes
let marqueeResizeTimeout = null;
window.addEventListener("resize", () => {
    if (marqueeResizeTimeout) clearTimeout(marqueeResizeTimeout);
    marqueeResizeTimeout = setTimeout(() => {
        const tickerBar = document.getElementById("fillerTickerBar");
        if (tickerBar && tickerBar.style.display !== "none" && activeMarqueeText) {
            triggerTickerScroll(false);
        }
    }, 200);
});

// -------------------------------------------------------------
// Live Host Microphone & Audio Mixing Engine
// -------------------------------------------------------------
let hostMicModalOpen = false;
let hostMicVuAnimFrame = null;

window.onAudioDspDuckingActive = function(factor) {
    const bgm = document.getElementById("bgmPlayer");
    if (bgm && !bgm.paused) {
        if (!bgm.dataset.baseVolume) bgm.dataset.baseVolume = "0.25";
        bgm.volume = Math.max(0, Math.min(1.0, parseFloat(bgm.dataset.baseVolume) * factor));
    }
    const fillerV = document.getElementById("fillerVideo");
    if (fillerV && !fillerV.paused) {
        if (!fillerV.dataset.baseVolume) fillerV.dataset.baseVolume = "1.0";
        fillerV.volume = Math.max(0, Math.min(1.0, parseFloat(fillerV.dataset.baseVolume) * factor));
    }
};

async function populateHostMicDevices() {
    const select = document.getElementById("hostMicDeviceSelect");
    if (!select) return;

    try {
        const savedDevice = localStorage.getItem("tiki_mic_device") || "default";
        let deviceList = [];

        // 1. Fetch system input devices from server (pactl / OS)
        try {
            const res = await fetch("/api/setup/audio-inputs");
            if (res.ok) {
                const sysInputs = await res.json();
                deviceList = [...sysInputs];
            }
        } catch (e) { console.debug('[Tiki]', e); }

        // 2. Merge browser audio inputs.
        // NOTE: the native audio router (pactl / tiki-mic-router) resolves
        // devices by OS name — opaque browser GUIDs are useless to it and
        // actively harmful if they overwrite server-side ids. So we keep
        // server ids authoritative and only fall back to browser devices
        // when server enumeration returned nothing beyond "default".
        const serverHasRealDevices = deviceList.some(d => d.id !== "default");
        if (!serverHasRealDevices && navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            try {
                const devList = await navigator.mediaDevices.enumerateDevices();
                const browserInputs = devList.filter(d => d.kind === "audioinput");
                browserInputs.forEach((dev, idx) => {
                    if (!dev.deviceId) return;
                    const match = deviceList.find(d => d.id === dev.deviceId);
                    if (!match) {
                        deviceList.push({
                            id: dev.deviceId,
                            name: dev.deviceId,
                            description: dev.label || `Microphone ${idx + 1}`
                        });
                    }
                });
            } catch (e) { console.debug('[Tiki]', e); }
        } else if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            // Server list is authoritative; enrich descriptions with browser
            // labels where they clearly refer to the same device.
            try {
                const devList = await navigator.mediaDevices.enumerateDevices();
                const browserInputs = devList.filter(d => d.kind === "audioinput" && d.label);
                browserInputs.forEach(dev => {
                    const match = deviceList.find(d =>
                        d.id !== "default" && d.description &&
                        (d.description.toLowerCase().includes(dev.label.toLowerCase()) ||
                         dev.label.toLowerCase().includes(d.description.toLowerCase())));
                    if (match && (!match.description || match.description === match.id)) {
                        match.description = dev.label;
                    }
                });
            } catch (e) { console.debug('[Tiki]', e); }
        }

        if (deviceList.length === 0) {
            deviceList = [{ id: "default", description: "Default Audio Input" }];
        }

        const currentVal = select.value || savedDevice;
        select.innerHTML = "";
        let matched = false;
        deviceList.forEach(dev => {
            const opt = document.createElement("option");
            opt.value = dev.id;
            opt.text = dev.description || dev.name;
            if (dev.id === currentVal || dev.id === savedDevice) {
                opt.selected = true;
                matched = true;
            }
            select.appendChild(opt);
        });

        // If savedDevice wasn't found in current enumerated list, add it so dropdown never clears
        if (!matched && savedDevice && savedDevice !== "default") {
            const opt = document.createElement("option");
            opt.value = savedDevice;
            opt.text = `Saved Device (${savedDevice.slice(0, 16)}...)`;
            opt.selected = true;
            select.appendChild(opt);
        } else if (!matched && select.options.length > 0) {
            select.selectedIndex = 0;
        }
    } catch (e) {
        console.warn("[Host Mic] Failed to enumerate audio devices:", e);
    }
}

let hostMicMuted = true;
let hostMicVolume = 80;
let hostMicReverb = false;
let hostEchoCancellation = false;
let hostDuckingEnabled = true;
let hostDuckingDb = -6;
let hostMicAvailable = null;   // null = unknown yet; true/false from backend router
let hostMicLatencyMs = null;   // negotiated buffering latency estimate (native path)
let hostMonitoringMode = "native"; // hardware_direct | native | browser

// Fetch initial state from backend audio router
fetch("/api/host/mic-state")
    .then(r => r.json())
    .then(st => {
        if (st) {
            hostMicMuted = Boolean(st.muted);
            hostMicVolume = Number(st.volume) || 80;
            hostMicAvailable = Boolean(st.available);
            hostMicLatencyMs = (st.latency_ms_est !== undefined && st.latency_ms_est !== null)
                ? Number(st.latency_ms_est) : null;
            if (st.monitoring_mode) {
                hostMonitoringMode = st.monitoring_mode;
                const modeSel = document.getElementById("hostMonitoringModeSelect");
                if (modeSel) modeSel.value = hostMonitoringMode;
            }
            if (st.reverb !== undefined) {
                hostMicReverb = Boolean(st.reverb);
                const revToggle = document.getElementById("modalMicReverbToggle");
                if (revToggle) revToggle.checked = hostMicReverb;
                if (audioDSP) audioDSP.setMicReverb(hostMicReverb);
            }
            if (st.echo_cancellation !== undefined) {
                hostEchoCancellation = Boolean(st.echo_cancellation);
                if (audioDSP) audioDSP.setEchoCancellation(hostEchoCancellation);
            }
            if (st.ducking_enabled !== undefined) {
                hostDuckingEnabled = Boolean(st.ducking_enabled);
                const duck = document.getElementById("modalDuckingToggle");
                if (duck) duck.checked = hostDuckingEnabled;
            }
            if (st.ducking_db !== undefined) {
                hostDuckingDb = Number(st.ducking_db) || -6;
                if (audioDSP) audioDSP.duckingDb = hostDuckingDb;
                const dbSel = document.getElementById("modalDuckingDbSelect");
                if (dbSel) dbSel.value = String(st.ducking_db);
            }
            if (st.cdg_theme) {
                const cdgSel = document.getElementById("cdgThemeSelect");
                if (cdgSel) cdgSel.value = st.cdg_theme;
                onCdgThemeChange(st.cdg_theme);
            }
            if (st.cdg_glow !== undefined) {
                const cdgGlow = document.getElementById("cdgGlowToggle");
                if (cdgGlow) cdgGlow.checked = Boolean(st.cdg_glow);
                onCdgGlowChange(Boolean(st.cdg_glow));
            }
            renderMicAvailability();
            renderMonitoringModeGating();
            syncBrowserWebRtcState();
            updateHostMicUI(hostMicMuted);
            const slider = document.getElementById("modalMicVolumeSlider");
            if (slider) slider.value = hostMicVolume;
            const txt = document.getElementById("modalMicVolumeText");
            if (txt) txt.innerText = `${hostMicVolume}%`;
            if (st.device && st.device !== "default") {
                localStorage.setItem("tiki_mic_device", st.device);
            }
        }
    })
    .catch(() => {});

function renderMicAvailability() {
    const warn = document.getElementById("micNativeWarning");
    if (!warn) return;
    if (hostMicAvailable === false && hostMonitoringMode === "native") {
        const hint = document.getElementById("micNativeWarningHint");
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

function renderMonitoringModeGating() {
    // In hardware_direct mode: the interface's own monitor knob is doing the work.
    // Disable mute toggle and volume slider — we can't control hardware directly.
    const isHwDirect = hostMonitoringMode === "hardware_direct";
    const toggleBtn = document.getElementById("modalMicToggleBtn");
    const volSlider = document.getElementById("modalMicVolumeSlider");
    const stageMicBtn = document.getElementById("stageMicToggleBtn");
    const hint = document.getElementById("micModeHint");

    if (toggleBtn) {
        toggleBtn.disabled = isHwDirect;
        toggleBtn.title = isHwDirect ? "Hardware Direct mode: use the interface's monitor knob" : "";
        toggleBtn.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (stageMicBtn) {
        stageMicBtn.disabled = isHwDirect;
        stageMicBtn.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (volSlider) {
        volSlider.disabled = isHwDirect;
        volSlider.style.opacity = isHwDirect ? "0.45" : "1";
    }
    if (hint) {
        hint.style.display = isHwDirect ? "block" : "none";
    }
    renderMicAvailability();
}

async function syncBrowserWebRtcState() {
    if (hostMonitoringMode === "browser") {
        if (!audioDSP) {
            audioDSP = new AudioDSPPipeline();
            window.audioDSP = audioDSP;
        }
        audioDSP.ensureContext();
        audioDSP.echoCancellationEnabled = !!hostEchoCancellation;
        if (!audioDSP.micStream) {
            const devId = localStorage.getItem("tiki_mic_device") || "default";
            await audioDSP.connectMicrophone(devId);
        }
        if (hostMonitoringMode !== "browser") {
            audioDSP.setMicMuted(true);
            return;
        }
        audioDSP.setMicMuted(hostMicMuted);
        audioDSP.setMicVolume(hostMicVolume / 100.0);
        audioDSP.setMicReverb(hostMicReverb);
    } else {
        // If not in browser WebRTC mode, mute the Web Audio mic so it does not echo with native route
        if (audioDSP) {
            audioDSP.setMicMuted(true);
        }
    }
}

function onHostMonitoringModeChange(newMode) {
    if (!newMode) return;
    hostMonitoringMode = newMode;
    renderMonitoringModeGating();
    syncBrowserWebRtcState();
    broadcastHostMicState();
}

function requestHostMicPermission() {
    if (hostMonitoringMode === "browser") {
        syncBrowserWebRtcState();
    } else {
        toggleStageMic();
    }
}

function onHostMicDeviceChange(deviceId) {
    localStorage.setItem("tiki_mic_device", deviceId);
    if (hostMonitoringMode === "browser" && audioDSP) {
        audioDSP.connectMicrophone(deviceId);
    }
    broadcastHostMicState();
}

function toggleStageMic() {
    hostMicMuted = !hostMicMuted;
    updateHostMicUI(hostMicMuted);
    syncBrowserWebRtcState();
    broadcastHostMicState();
}

function updateHostMicUI(muted) {
    const topBtn = document.getElementById("stageMicToggleBtn");
    if (topBtn) {
        if (muted) {
            topBtn.innerHTML = "🔇 Mic Off";
            topBtn.style.color = "#ff5555";
            topBtn.style.borderColor = "rgba(255,85,85,0.4)";
        } else {
            topBtn.innerHTML = "🎤 Mic LIVE";
            topBtn.style.color = "#39ff14";
            topBtn.style.borderColor = "rgba(57,255,20,0.4)";
        }
    }

    const modalBtn = document.getElementById("modalMicToggleBtn");
    if (modalBtn) {
        if (muted) {
            modalBtn.innerHTML = "🔇 Mic Muted (OFF)";
            modalBtn.style.color = "#ff5555";
            modalBtn.style.borderColor = "rgba(255,85,85,0.4)";
        } else {
            modalBtn.innerHTML = "🎤 Mic LIVE (ON)";
            modalBtn.style.color = "#39ff14";
            modalBtn.style.borderColor = "rgba(57,255,20,0.4)";
        }
    }

    const badge = document.getElementById("micLiveStatusBadge");
    if (badge) {
        if (hostMonitoringMode === "hardware_direct") {
            badge.innerText = "DIRECT HARDWARE (Interface Knob)";
            badge.style.color = "#39ff14";
        } else if (hostMonitoringMode === "browser") {
            if (muted) {
                badge.innerText = "MUTED";
                badge.style.color = "#ff5555";
            } else {
                badge.innerText = "LIVE (Browser WebRTC)";
                badge.style.color = "#00f0ff";
            }
        } else if (hostMicAvailable === false) {
            badge.innerText = "NO NATIVE ROUTING";
            badge.style.color = "#ffb703";
        } else if (muted) {
            badge.innerText = "MUTED";
            badge.style.color = "#ff5555";
        } else {
            badge.innerText = (hostMicLatencyMs !== null && hostMicLatencyMs > 0)
                ? `LIVE (~${Math.round(hostMicLatencyMs)}ms Direct)`
                : "LIVE (Direct)";
            badge.style.color = "#39ff14";
        }
    }

    const barL = document.getElementById("vuMeterBarL");
    const barR = document.getElementById("vuMeterBarR");
    if (barL && barR) {
        if (muted || hostMonitoringMode === "hardware_direct") {
            barL.style.width = "0%";
            barR.style.width = "0%";
        } else if (hostMonitoringMode === "native") {
            barL.style.width = "65%";
            barR.style.width = "65%";
        }
    }
}

function onHostMicVolumeChange(val) {
    const num = Number(val) || 80;
    hostMicVolume = num;
    const txt = document.getElementById("modalMicVolumeText");
    if (txt) txt.innerText = `${num}%`;
    syncBrowserWebRtcState();
    broadcastHostMicState();
}

function onHostMicReverbChange(checked) {
    hostMicReverb = !!checked;
    if (audioDSP) audioDSP.setMicReverb(hostMicReverb);
    broadcastHostMicState();
}

function onHostDuckingToggleChange(checked) {
    hostDuckingEnabled = !!checked;
    broadcastHostMicState();
}

function onHostDuckingDbChange(val) {
    hostDuckingDb = Number(val) || -6;
    if (audioDSP) audioDSP.duckingDb = hostDuckingDb;
    broadcastHostMicState();
}

function broadcastHostMicState() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "host_mic_settings",
            device: localStorage.getItem("tiki_mic_device") || "default",
            muted: hostMicMuted,
            volume: hostMicVolume,
            reverb: hostMicReverb,
            monitoring_mode: hostMonitoringMode,
            ducking_enabled: hostDuckingEnabled,
            ducking_db: hostDuckingDb
        }));
    }
}

function handleHostMicSettings(data) {
    if (data.available !== undefined) {
        hostMicAvailable = Boolean(data.available);
        renderMicAvailability();
    }
    if (data.latency_ms !== undefined && data.latency_ms !== null) {
        hostMicLatencyMs = Number(data.latency_ms);
    }
    if (data.monitoring_mode !== undefined && data.monitoring_mode) {
        hostMonitoringMode = data.monitoring_mode;
        const modeSel = document.getElementById("hostMonitoringModeSelect");
        if (modeSel && modeSel.value !== hostMonitoringMode) {
            modeSel.value = hostMonitoringMode;
        }
        renderMonitoringModeGating();
        syncBrowserWebRtcState();
    }
    if (data.device !== undefined && data.device) {
        localStorage.setItem("tiki_mic_device", data.device);
        const select = document.getElementById("hostMicDeviceSelect");
        if (select) {
            let opt = Array.from(select.options).find(o => o.value === data.device);
            if (!opt) {
                opt = document.createElement("option");
                opt.value = data.device;
                opt.text = data.device_name || data.device;
                select.appendChild(opt);
            }
            select.value = data.device;
        }
    }
    if (data.muted !== undefined) {
        hostMicMuted = Boolean(data.muted);
        updateHostMicUI(hostMicMuted);
        syncBrowserWebRtcState();

        // Intelligent Between-Song Intermission Ducking on stage player
        if (audioDSP) {
            if (!hostMicMuted && isIntermission && hostDuckingEnabled) {
                audioDSP.setMusicDucked(true);
            } else {
                audioDSP.setMusicDucked(false);
            }
        }
    }
    if (data.volume !== undefined) {
        hostMicVolume = Number(data.volume);
        const slider = document.getElementById("modalMicVolumeSlider");
        if (slider) slider.value = hostMicVolume;
        const txt = document.getElementById("modalMicVolumeText");
        if (txt) txt.innerText = `${hostMicVolume}%`;
        syncBrowserWebRtcState();
    }
    if (data.reverb !== undefined) {
        hostMicReverb = Boolean(data.reverb);
        const revToggle = document.getElementById("modalMicReverbToggle");
        if (revToggle) revToggle.checked = hostMicReverb;
        if (audioDSP) audioDSP.setMicReverb(hostMicReverb);
    }
    if (data.echo_cancellation !== undefined) {
        hostEchoCancellation = Boolean(data.echo_cancellation);
        if (audioDSP) audioDSP.setEchoCancellation(hostEchoCancellation);
    }
    if (data.ducking_enabled !== undefined) {
        hostDuckingEnabled = Boolean(data.ducking_enabled);
        const duck = document.getElementById("modalDuckingToggle");
        if (duck) duck.checked = hostDuckingEnabled;
    }
    if (data.ducking_db !== undefined) {
        hostDuckingDb = Number(data.ducking_db);
        if (audioDSP) audioDSP.duckingDb = hostDuckingDb;
        const dbSel = document.getElementById("modalDuckingDbSelect");
        if (dbSel) dbSel.value = String(data.ducking_db);
    }
    if (data.cdg_theme !== undefined && data.cdg_theme) {
        const cdgSel = document.getElementById("cdgThemeSelect");
        if (cdgSel) cdgSel.value = data.cdg_theme;
        onCdgThemeChange(data.cdg_theme);
    }
    if (data.cdg_glow !== undefined) {
        const cdgGlow = document.getElementById("cdgGlowToggle");
        if (cdgGlow) cdgGlow.checked = Boolean(data.cdg_glow);
        onCdgGlowChange(Boolean(data.cdg_glow));
    }
}

function toggleHostMicModal() {
    const modal = document.getElementById("hostMicModal");
    if (!modal) return;

    hostMicModalOpen = (modal.style.display !== "flex");
    modal.style.display = hostMicModalOpen ? "flex" : "none";

    if (hostMicModalOpen) {
        if (!audioDSP) {
            audioDSP = new AudioDSPPipeline();
            window.audioDSP = audioDSP;
        }
        audioDSP.ensureContext();
        populateHostMicDevices();
        startHostMicVULoop();
    } else {
        stopHostMicVULoop();
    }
}

function startHostMicVULoop() {
    if (hostMicVuAnimFrame) cancelAnimationFrame(hostMicVuAnimFrame);
    function updateLoop() {
        if (!hostMicModalOpen) return;
        const barL = document.getElementById("vuMeterBarL");
        const barR = document.getElementById("vuMeterBarR");
        if (barL && barR) {
            if (hostMicMuted || hostMonitoringMode === "hardware_direct") {
                barL.style.width = "0%";
                barR.style.width = "0%";
            } else if (hostMonitoringMode === "browser" && audioDSP) {
                const levels = audioDSP.getMicLevels();
                barL.style.width = levels.isLive ? `${levels.peakL}%` : "0%";
                barR.style.width = levels.isLive ? `${levels.peakR}%` : "0%";
            } else {
                // Native loopback direct routing is active
                barL.style.width = "70%";
                barR.style.width = "70%";
            }
        }
        hostMicVuAnimFrame = requestAnimationFrame(updateLoop);
    }
    hostMicVuAnimFrame = requestAnimationFrame(updateLoop);
}

function stopHostMicVULoop() {
    if (hostMicVuAnimFrame) {
        cancelAnimationFrame(hostMicVuAnimFrame);
        hostMicVuAnimFrame = null;
    }
    const barL = document.getElementById("vuMeterBarL");
    const barR = document.getElementById("vuMeterBarR");
    if (barL) barL.style.width = "0%";
    if (barR) barR.style.width = "0%";
}

// -------------------------------------------------------------
// Stage TV Network Status & Hotspot Engine
// -------------------------------------------------------------
async function fetchHostNetworkStatus() {
    try {
        const res = await fetch("/api/host/network");
        if (res.ok) {
            const data = await res.json();
            updateStageNetworkUI(data);
        }
    } catch (e) {
        console.error("[Stage] Error loading host network status:", e);
    }
}

function updateStageNetworkUI(data) {
    if (!data) return;

    const showWifi = !!(data.show_wifi_card || (data.remote_mode === "local" && data.hotspot_enabled && data.hotspot_ssid));
    const wifiCard = document.getElementById("idleWifiQRCard");
    const appTitle = document.getElementById("idleAppQRTitle");
    if (wifiCard) {
        wifiCard.style.display = showWifi ? "block" : "none";
    }
    if (appTitle) {
        appTitle.innerText = showWifi ? "📱 Step 2: Request Songs" : "📱 Scan to Request Songs";
    }

    const wifiSsid = document.getElementById("stageWifiSsid");
    const wifiPass = document.getElementById("stageWifiPass");
    const joinUrlText = document.getElementById("stageJoinUrlText");
    const wifiQrImg = document.getElementById("stageWifiQrImg");
    const publicQrImg = document.getElementById("stagePublicQrImg");
    const fillerQrImg = document.getElementById("stageFillerQrImg");
    const fillerDisplayIp = document.getElementById("stageFillerDisplayIp");
    const joinQrImg = document.getElementById("stageJoinQrImg");
    const joinDisplayIp = document.getElementById("stageJoinDisplayIp");

    if (wifiSsid && data.hotspot_ssid) {
        wifiSsid.innerText = data.hotspot_ssid;
    }
    if (wifiPass && data.hotspot_password) {
        wifiPass.innerText = data.hotspot_password;
    }
    if (joinUrlText && data.join_url) {
        joinUrlText.innerText = data.join_url;
    }

    const t = Date.now();
    if (wifiQrImg && showWifi) wifiQrImg.src = `/qr/wifi?t=${t}`;
    if (publicQrImg) publicQrImg.src = `/qr/public?t=${t}`;
    if (fillerQrImg) fillerQrImg.src = `/qr/public?t=${t}`;
    if (joinQrImg) joinQrImg.src = `/qr/public?t=${t}`;

    if (fillerDisplayIp && data.join_url) {
        fillerDisplayIp.innerText = data.join_url;
    }
    if (joinDisplayIp && data.join_url) {
        joinDisplayIp.innerText = data.join_url;
    }

    fetch("/api/stage/room-info")
        .then(r => r.ok ? r.json() : null)
        .then(info => {
            if (!info) return;
            const showRoom = Boolean(info.require_room_code && info.room_code);
            const badges = [
                ["stageIdleRoomCodeBadge", "stageIdleRoomCodeVal"],
                ["stageFillerRoomCodeBadge", "stageFillerRoomCodeVal"],
                ["stageJoinRoomCodeBadge", "stageJoinRoomCodeVal"],
            ];
            badges.forEach(([badgeId, valId]) => {
                const badge = document.getElementById(badgeId);
                const val = document.getElementById(valId);
                if (badge) badge.style.display = showRoom ? (badgeId === "stageJoinRoomCodeBadge" ? "block" : "inline-block") : "none";
                if (val && info.room_code) val.innerText = info.room_code;
            });
        })
        .catch(() => {});
}



