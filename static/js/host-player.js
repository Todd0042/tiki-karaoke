/**
 * Tiki Karaoke Host Display Engine
 * Manages Dual Video Player (Local HTML5 vs YouTube IFrame),
 * Web Audio DSP (Pitch/EQ), Stage Intermissions, Countdown Timer,
 * BGM cross-fader, TTS Voice Announcer, and WebSocket live events.
 */

let audioDSP = null;
let ytPlayer = null;
let ws = null;
let currentTrack = null;
let intermissionTimer = null;
let intermissionRemaining = 15;
let isIntermission = false;
let isSpectatorViewer = false;
let isMutedAudio = false;
let isMutedViewer = false; // Kept for backward compatibility
let configuredAudioSink = null;
let bgmPlaylist = [];
let currentBgmIndex = -1;
let currentNextTrack = null;
let currentQueue = [];
let stagePendingDownloads = [];
let onDeckAlertFiredForQueueId = null;

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

const myDisplayId = localStorage.getItem("tiki_display_id") || ("disp_" + Math.random().toString(36).substring(2, 8));
localStorage.setItem("tiki_display_id", myDisplayId);

function getMyDisplayName() {
    let name = localStorage.getItem("tiki_display_name");
    if (!name) {
        const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
        name = isMobile ? "Mobile Screen" : ("Stage TV (" + window.innerWidth + "x" + window.innerHeight + ")");
        localStorage.setItem("tiki_display_name", name);
    }
    return name;
}
let myDisplayName = getMyDisplayName();

document.addEventListener("DOMContentLoaded", () => {
    initWebSocket();
    initAudio();
    initBGMPlaylist();
    initYouTubeAPI();
    setupEventListeners();
    fetchCurrentState();
    fetchHostNetworkStatus();
    initCrowdParticleEngine();
});

function initAudio() {
    const urlParams = new URLSearchParams(window.location.search);
    isSpectatorViewer = urlParams.has("mute") || urlParams.has("muted") || urlParams.has("view_only") || urlParams.has("handheld");
    isMutedAudio = isSpectatorViewer;
    isMutedViewer = isMutedAudio;

    const videoEl = document.getElementById("localVideo");
    if (isSpectatorViewer) {
        if (videoEl) videoEl.muted = true;
        const bgm = document.getElementById("bgmPlayer");
        if (bgm) bgm.muted = true;

        // Visual indicator on handheld lyrics display
        const hud = document.getElementById("topHUD");
        if (hud && !document.getElementById("handheldBadge")) {
            const badge = document.createElement("div");
            badge.id = "handheldBadge";
            badge.style.cssText = "background: rgba(0,240,255,0.18); border: 1px solid var(--neon-cyan); color: var(--neon-cyan); padding: 4px 10px; border-radius: 12px; font-size: 0.78rem; font-weight: 700; display: flex; align-items: center; gap: 4px; pointer-events: auto;";
            badge.innerHTML = "<span>🔇 Handheld Lyrics (Muted)</span>";
            hud.appendChild(badge);
        }
        const unmuteBtn = document.querySelector("#unmuteOverlay button");
        if (unmuteBtn) {
            unmuteBtn.innerText = "▶️ Tap to Sync Handheld Lyrics";
        }
        const hostBtn = document.querySelector("button[onclick='toggleHostQRModal()']");
        if (hostBtn) {
            hostBtn.style.display = "none";
        }
    } else {
        audioDSP = new AudioDSPPipeline();
        window.audioDSP = audioDSP;
        populateHostMicDevices();
    }

    applyAudioSink();

    document.body.addEventListener("click", () => {
        if (!isMutedAudio && audioDSP && audioDSP.ctx && audioDSP.ctx.state === "suspended") {
            audioDSP.resume();
        }
    });
}

function resumeStagePlayback() {
    const videoEl = document.getElementById("localVideo");
    const fillerVideo = document.getElementById("fillerVideo");
    const ytContainer = document.getElementById("youtubeContainer");
    if (isMutedAudio) {
        if (videoEl) videoEl.muted = true;
        if (fillerVideo) fillerVideo.muted = true;
        const bgm = document.getElementById("bgmPlayer");
        if (bgm) bgm.muted = true;
    } else if (audioDSP) {
        audioDSP.init(videoEl);
        audioDSP.resume();
    }
    const bgm = document.getElementById("bgmPlayer");
    if (bgm && bgm.paused && isIntermission && !isFillerPlaying && !currentTrack && !isMutedAudio) {
        bgm.play().catch(() => {});
    }
    if (videoEl && videoEl.style.display !== "none" && currentTrack) {
        if (currentTrack.elapsed_seconds && videoEl.currentTime < currentTrack.elapsed_seconds - 3) {
            videoEl.currentTime = currentTrack.elapsed_seconds;
        }
        if (isMutedAudio) videoEl.muted = true;
        videoEl.play().then(() => {
            const unmute = document.getElementById("unmuteOverlay");
            if (unmute) unmute.style.display = "none";
        }).catch(e => console.log(e));
    }
    if (fillerVideo && fillerVideo.style.display !== "none" && isFillerPlaying) {
        if (isMutedAudio) fillerVideo.muted = true;
        fillerVideo.play().catch(e => console.log(e));
    }
    if (ytPlayer && ytPlayer.playVideo && ytContainer && ytContainer.style.display !== "none" && (currentTrack || isFillerPlaying)) {
        if (isMutedAudio && ytPlayer.mute) ytPlayer.mute();
        ytPlayer.playVideo();
    }
    const unmute = document.getElementById("unmuteOverlay");
    if (unmute) unmute.style.display = "none";
}

let wsPingInterval = null;

function setStageConnectionBanner(connected) {
    let banner = document.getElementById("stageWsBanner");
    if (!banner) {
        banner = document.createElement("div");
        banner.id = "stageWsBanner";
        banner.style.cssText = "position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:9999;padding:8px 18px;border-radius:999px;background:rgba(48,12,22,0.94);border:1px solid #ff5555;color:#fff;font-size:0.88rem;font-weight:700;display:none;box-shadow:0 0 18px rgba(255,85,85,0.45);";
        banner.innerText = "🔄 Reconnecting to Tiki Server...";
        document.body.appendChild(banner);
    }
    banner.style.display = connected ? "none" : "block";
}

function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    if (wsPingInterval) {
        clearInterval(wsPingInterval);
        wsPingInterval = null;
    }

    try {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            ws.onclose = null;
            ws.onerror = null;
            ws.close();
        }
    } catch (e) { console.debug('[Tiki]', e); }

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        setStageConnectionBanner(true);
        console.log("[Host WS] Connected as display:", myDisplayId, myDisplayName);
        ws.send(JSON.stringify({
            type: "stage_register",
            display_id: myDisplayId,
            name: myDisplayName,
            is_muted: isMutedAudio,
            is_spectator: isSpectatorViewer
        }));

        // Keepalive heartbeat every 10s to prevent router/tunnel idle drops
        wsPingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 10000);

        // Always sync current server state upon connect or reconnect
        fetchCurrentState();
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleSocketMessage(data);
        } catch (e) {
            console.error("[Host WS] Error parsing message:", e);
        }
    };

    ws.onerror = (e) => {
        console.warn("[Host WS] Error on socket:", e);
    };

    ws.onclose = () => {
        setStageConnectionBanner(false);
        console.log("[Host WS] Disconnected, retrying in 2s...");
        if (wsPingInterval) {
            clearInterval(wsPingInterval);
            wsPingInterval = null;
        }
        setTimeout(initWebSocket, 2000);
    };
}

const STAGE_WS_HANDLERS = {
    pong: () => {},
    set_display_audio: (data) => {
        if (data.display_id === myDisplayId) applyStageAudioMute(data.is_muted);
    },
    play_track: (data) => startTrackPlayback(data.track),
    skip_track: () => skipCurrentTrack(),
    skip_bgm: () => playNextBGM(),
    stop_track: (data) => stopCurrentTrack(data.next_track),
    show_wrapped: (data) => showPartyWrappedStage(data.stats),
    queue_empty: () => {
        skipCurrentTrack();
        stopIntermission();
        stopFillerPlayback();
        triggerIntermission(null, 0);
    },
    reload_display: () => {
        console.log("[Stage] Reload display command received from host.");
        window.location.reload(true);
    },
    resync_state: () => {
        console.log("[Stage] Resync state command received from host.");
        fetchCurrentState(true);
    },
    pause_track: () => pausePlayback(),
    resume_track: () => resumePlayback(),
    pitch_change: (data) => {
        if (audioDSP) audioDSP.setPitch(data.pitch);
        updatePitchBadge(data.pitch);
    },
    eq_change: (data) => { if (audioDSP) audioDSP.setEQ(data.bass, data.mid, data.treble); },
    volume_change: (data) => { if (audioDSP) audioDSP.setMasterVolume(data.volume); },
    host_mic_settings: (data) => handleHostMicSettings(data),
    emoji_reaction: (data) => spawnFloatingEmoji(data.emoji),
    network_updated: (data) => updateStageNetworkUI(data),
    singer_joined: (data) => spawnSingerJoinedPill(data.name, data.avatar),
    marquee_updated: (data) => handleMarqueeUpdate(data),
    queue_updated: (data) => {
        currentQueue = data.queue;
        updateNextTicker(data.queue);
    },
    pending_downloads_updated: (data) => {
        stagePendingDownloads = data.pending_downloads || [];
        updateNextTicker(currentQueue || []);
        renderStageMarquee();
    },
    download_progress: (data) => {
        if (data.pending_downloads) {
            stagePendingDownloads = data.pending_downloads;
        } else if (data.pending_id) {
            const item = stagePendingDownloads.find(p => p.id === data.pending_id);
            if (item) {
                item.progress = data.progress;
                item.status = data.status;
            }
        }
        updateNextTicker(currentQueue || []);
        renderStageMarquee();
    },
    host_alert: (data) => { if (typeof showToast === "function") showToast(`⚠️ ${data.message}`); },
    start_intermission: (data) => triggerIntermission(data.next_track, data.duration || 15),
    start_filler_buffer: (data) => triggerFillerBuffer(data.duration || 15),
    play_filler_track: (data) => startFillerPlayback(data.track),
    stop_filler: () => stopFillerPlayback(),
    filler_transition_fade: (data) => handleFillerTransitionFade(data),
    filler_transition_finish: (data) => handleFillerTransitionFinish(data),
    soundboard_play: (data) => playSoundEffect(data.sound),
    av_sync_updated: (data) => { if (audioDSP) audioDSP.setSyncOffset(data.offset_ms); },
    test_display: (data) => showTestDisplayFlash(data.badge),
    server_shutting_down: () => {
        showSyncToast("🛑 Closing session... Aloha! 🌺");
        setTimeout(() => {
            try {
                if (document.fullscreenElement) {
                    document.exitFullscreen().catch((err) => console.debug("[Tiki]", err));
                }
            } catch (e) { console.debug("[Tiki]", e); }
            document.body.innerHTML = `
                <div style="position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#05080f;color:#00f0ff;font-family:sans-serif;text-align:center;padding:24px;box-sizing:border-box;">
                    <button onclick="window.close()" title="Close Window" style="position:absolute;top:24px;right:24px;background:rgba(255,42,133,0.15);color:#ff2a85;border:1px solid #ff2a85;border-radius:50%;width:50px;height:50px;font-size:1.6rem;font-weight:bold;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all 0.2s;" onmouseover="this.style.background='#ff2a85';this.style.color='#fff';" onmouseout="this.style.background='rgba(255,42,133,0.15)';this.style.color='#ff2a85';">✕</button>
                    <h1 style="font-size:2.8rem;margin-bottom:8px;text-shadow:0 0 20px #00f0ff;">🌴 Session Closed</h1>
                    <p style="color:#aaa;font-size:1.2rem;max-width:550px;line-height:1.6;margin-bottom:32px;">
                        Tiki Karaoke Lounge has been shut down by the host.<br>
                        Aloha and see you next time! 🌺
                    </p>
                    <div style="display:flex;gap:16px;flex-wrap:wrap;justify-content:center;">
                        <button onclick="window.close()" style="background:linear-gradient(135deg,#ff2a85,#ff758c);color:#fff;border:none;border-radius:12px;padding:14px 32px;font-size:1.15rem;font-weight:bold;cursor:pointer;box-shadow:0 0 20px rgba(255,42,133,0.4);transition:transform 0.15s;" onmousedown="this.style.transform='scale(0.96)';" onmouseup="this.style.transform='scale(1)';">
                            ❌ Close Window
                        </button>
                        <button onclick="if(document.fullscreenElement){document.exitFullscreen();}else{window.close();}" style="background:rgba(0,240,255,0.15);color:#00f0ff;border:1px solid #00f0ff;border-radius:12px;padding:14px 28px;font-size:1.15rem;font-weight:bold;cursor:pointer;transition:transform 0.15s;" onmousedown="this.style.transform='scale(0.96)';" onmouseup="this.style.transform='scale(1)';">
                            🗗 Exit Fullscreen
                        </button>
                    </div>
                </div>
            `;
        }, 500);
    },
};

function handleSocketMessage(data) {
    const handler = STAGE_WS_HANDLERS[data.type];
    if (handler) handler(data);
}

function initYouTubeAPI() {
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    const firstScriptTag = document.getElementsByTagName("script")[0];
    firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);

    window.onYouTubeIframeAPIReady = () => {
        ytPlayer = new YT.Player("youtubePlayer", {
            height: "100%",
            width: "100%",
            playerVars: {
                autoplay: 1,
                controls: 0,
                modestbranding: 1,
                rel: 0,
                iv_load_policy: 3
            },
            events: {
                onReady: () => console.log("[YouTube] IFrame API Ready"),
                onStateChange: onYouTubeStateChange,
                onError: (e) => {
                    const ytContainer = document.getElementById("youtubeContainer");
                    if (!ytContainer || ytContainer.style.display === "none") return;
                    console.error("[YouTube] Player error code:", e.data);
                    onPlaybackFailure(`YouTube error code ${e.data}`, currentTrack);
                }
            }
        });
    };
}

function onYouTubeStateChange(event) {
    const ytContainer = document.getElementById("youtubeContainer");
    if (!ytContainer || ytContainer.style.display === "none") return;
    if (event.data === YT.PlayerState.ENDED) {
        if (isFillerPlaying) {
            if (!isSpectatorViewer) {
                fetch("/api/host/filler-tracks/finished", { method: "POST" }).catch(() => {});
            }
        } else {
            onSongFinished();
        }
    }
}

let cdgRenderer = null;
let cdgAnimFrame = null;
let isCdgPlaying = false;

function initCdgRenderer() {
    const canvas = document.getElementById("cdgCanvas");
    if (!canvas) return null;
    if (!cdgRenderer && typeof CDGRenderer === "function") {
        cdgRenderer = new CDGRenderer(canvas);
        const sel = document.getElementById("cdgThemeSelect");
        if (sel) sel.value = cdgRenderer.theme;
        const glow = document.getElementById("cdgGlowToggle");
        if (glow) glow.checked = cdgRenderer.glow;
    }
    return cdgRenderer;
}

function onCdgThemeChange(val) {
    const renderer = initCdgRenderer();
    if (renderer) renderer.setTheme(val);
}

function onCdgGlowChange(checked) {
    const renderer = initCdgRenderer();
    if (renderer) renderer.setGlow(checked);
}

async function startCdgPlayback(track) {
    const canvas = document.getElementById("cdgCanvas");
    if (!canvas) return;

    const renderer = initCdgRenderer();
    if (!renderer) return;

    try {
        canvas.style.display = "block";
        isCdgPlaying = true;
        const res = await fetch(`/stream-cdg/${track.track_id}`);
        if (!res.ok) throw new Error("Failed to load CDG stream");
        const buf = await res.arrayBuffer();
        renderer.load(buf);

        const localVideo = document.getElementById("localVideo");
        if (cdgAnimFrame) cancelAnimationFrame(cdgAnimFrame);

        function renderLoop() {
            if (!isCdgPlaying) return;
            const t = localVideo ? localVideo.currentTime : 0;
            renderer.sync(t);
            cdgAnimFrame = requestAnimationFrame(renderLoop);
        }
        cdgAnimFrame = requestAnimationFrame(renderLoop);
    } catch (err) {
        console.warn("[CDG] Playback error:", err);
        stopCdgPlayback();
    }
}

function stopCdgPlayback() {
    isCdgPlaying = false;
    if (cdgAnimFrame) {
        cancelAnimationFrame(cdgAnimFrame);
        cdgAnimFrame = null;
    }
    const canvas = document.getElementById("cdgCanvas");
    if (canvas) {
        canvas.style.display = "none";
        const ctx = canvas.getContext("2d");
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    if (cdgRenderer) {
        cdgRenderer.resetState();
    }
}

function startTrackPlayback(track) {
    if (!track) return;
    stopCdgPlayback();
    currentTrack = track;
    stopIntermission();
    stopFillerPlayback();
    fadeOutBGM();
    if (audioDSP) {
        audioDSP.setBetweenSongs(false);
        audioDSP.resume();
    }
    onDeckAlertFiredForQueueId = null;

    // Start live performance audio recording for this singer (safely guarded)
    try {
        if (audioDSP && track.singer_id) {
            audioDSP.startPerformanceRecording(track.queue_id, track.singer_id, track.track_id);
        }
    } catch (recErr) {
        console.warn("[Host Player] Performance recording start error (non-fatal):", recErr);
    }

    document.getElementById("nowPlayingBar").style.display = "flex";
    document.getElementById("intermissionOverlay").style.display = "none";
    hidePartyWrappedStage();
    const fillerBuffer = document.getElementById("fillerBufferOverlay");
    if (fillerBuffer) fillerBuffer.style.display = "none";
    const singerText = track.duet_partner ? `${track.singer_name} & ${track.duet_partner}` : track.singer_name;
    document.getElementById("nowSinger").innerText = singerText;
    document.getElementById("nowTitle").innerText = `${track.artist} - ${track.title}`;
    updatePitchBadge(track.pitch_shift || 0);
    updateLufsBadge(track.normalized_lufs);

    try {
        renderStageMarquee();
    } catch (marqueeErr) {
        console.warn("[Host Player] Stage marquee render error (non-fatal):", marqueeErr);
    }
    updateStageQrPosition();

    const seekTime = (track.elapsed_seconds && track.elapsed_seconds > 0) ? track.elapsed_seconds : 0;
    const localVideo = document.getElementById("localVideo");
    const ytContainer = document.getElementById("youtubeContainer");

    // Check if downloaded locally
    if (track.is_downloaded && track.local_path) {
        ytContainer.style.display = "none";
        if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();

        localVideo.style.display = "block";
        localVideo.src = `/stream/${track.track_id}`;
        if (configuredAudioSink && configuredAudioSink !== "default" && typeof localVideo.setSinkId === "function") {
            localVideo.setSinkId(configuredAudioSink).catch(() => {});
        }

        // Start CDG Canvas layer if track contains CDG subcodes
        if (track.media_type === "cdg" || track.cdg_path) {
            startCdgPlayback(track);
        }
        if (seekTime > 0) {
            if (localVideo.readyState >= 1) {
                localVideo.currentTime = seekTime;
            } else {
                localVideo.addEventListener("loadedmetadata", () => {
                    localVideo.currentTime = seekTime;
                }, { once: true });
            }
        } else {
            localVideo.currentTime = 0;
        }

        if (isMutedAudio) {
            localVideo.muted = true;
        } else if (audioDSP) {
            audioDSP.init(localVideo);
            audioDSP.resume();
            audioDSP.setPitch(track.pitch_shift || 0);
        }

        const unmuteOverlay = document.getElementById("unmuteOverlay");
        localVideo.play()
            .then(() => {
                if (unmuteOverlay) unmuteOverlay.style.display = "none";
            })
            .catch(e => {
                if (e.name === "AbortError") return;
                console.log("Local video autoplay blocked by browser policy:", e);
                if (unmuteOverlay && localVideo.paused) {
                    unmuteOverlay.style.display = "flex";
                }
            });
        localVideo.onended = onSongFinished;
        localVideo.onerror = (e) => {
            console.error("[Host] Local video playback failed to load or decode:", e);
            onPlaybackFailure("Local file missing or unplayable", track);
        };
    } else {
        // Fallback to YouTube IFrame player
        localVideo.style.display = "none";
        localVideo.pause();
        ytContainer.style.display = "block";

        if (ytPlayer && ytPlayer.loadVideoById) {
            ytPlayer.loadVideoById({
                videoId: track.video_id,
                startSeconds: seekTime
            });
            if (isMutedAudio && ytPlayer.mute) ytPlayer.mute();
            ytPlayer.playVideo();
        }
    }
}

function onPlaybackFailure(reason, failedTrack) {
    console.warn(`[Host Playback] Failure: ${reason}. Auto-skipping to avoid freeze...`);
    if (isSpectatorViewer) return;
    fetch("/api/host/skip", { method: "POST" })
        .catch(err => console.error("[Host] Auto-skip call failed:", err));
}

function onSongFinished() {
    stopCdgPlayback();
    if (audioDSP && audioDSP.mediaRecorder && audioDSP.mediaRecorder.state !== "inactive") {
        audioDSP.stopAndUploadPerformanceRecording();
    }
    if (!currentTrack) return;
    if (isSpectatorViewer) return;
    fetch(`/api/queue/${currentTrack.queue_id}/complete`, { method: "POST" });
    currentTrack = null;
    fadeInBGM();
    renderStageMarquee();
    updateStageQrPosition();
}

function skipCurrentTrack() {
    stopCdgPlayback();
    if (audioDSP && audioDSP.mediaRecorder && audioDSP.mediaRecorder.state !== "inactive") {
        audioDSP.stopAndUploadPerformanceRecording();
    }
    const localVideo = document.getElementById("localVideo");
    if (localVideo) {
        localVideo.pause();
        localVideo.style.display = "none";
        localVideo.removeAttribute("src");
        localVideo.load();
    }
    if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();
    const ytContainer = document.getElementById("youtubeContainer");
    if (ytContainer) ytContainer.style.display = "none";

    const nowPlayingBar = document.getElementById("nowPlayingBar");
    if (nowPlayingBar) nowPlayingBar.style.display = "none";

    stopFillerPlayback();
    currentTrack = null;
    fadeInBGM();
    renderStageMarquee();
    updateStageQrPosition();
}

function pausePlayback() {
    const localVideo = document.getElementById("localVideo");
    if (localVideo && localVideo.style.display !== "none") {
        if (localVideo.paused) {
            localVideo.play().catch(() => {});
        } else {
            localVideo.pause();
        }
    }
    if (ytPlayer) {
        if (typeof ytPlayer.getPlayerState === "function") {
            const state = ytPlayer.getPlayerState();
            if (state === YT.PlayerState.PLAYING) {
                ytPlayer.pauseVideo();
            } else if (state === YT.PlayerState.PAUSED) {
                ytPlayer.playVideo();
            }
        } else if (ytPlayer.pauseVideo) {
            ytPlayer.pauseVideo();
        }
    }
}

function resumePlayback() {
    const localVideo = document.getElementById("localVideo");
    if (localVideo && localVideo.style.display !== "none") localVideo.play().catch(() => {});
    if (ytPlayer && ytPlayer.playVideo) ytPlayer.playVideo();
}

function updatePitchBadge(pitch) {
    const badge = document.getElementById("nowPitchBadge");
    if (!badge) return;
    if (pitch === 0) {
        badge.innerText = "Key: Standard";
        badge.style.display = "inline-block";
    } else {
        const sign = pitch > 0 ? "+" : "";
        badge.innerText = `Key: ${sign}${pitch}`;
        badge.style.display = "inline-block";
    }
}

function updateLufsBadge(lufs) {
    const badge = document.getElementById("nowLufsBadge");
    if (!badge) return;
    if (typeof lufs === "number") {
        badge.innerText = `🎚️ ${lufs.toFixed(1)} LUFS`;
        badge.style.display = "inline-block";
    } else {
        badge.style.display = "none";
    }
}

// Intermission & Countdown Timer
function triggerIntermission(nextTrack, duration = 20) {
    isIntermission = true;
    hidePartyWrappedStage();
    if (audioDSP) audioDSP.setBetweenSongs(true);
    // Guarantee full countdown duration if nextTrack is queued
    intermissionRemaining = nextTrack ? (duration > 0 ? duration : 15) : 0;
    stopFillerPlayback();

    const overlay = document.getElementById("intermissionOverlay");
    overlay.style.display = "flex";
    document.getElementById("nowPlayingBar").style.display = "none";
    document.getElementById("localVideo").style.display = "none";
    document.getElementById("youtubeContainer").style.display = "none";

    fadeInBGM();

    const idleQRBox = document.getElementById("idleStageQRBox");
    const countdownCircle = document.getElementById("countdownCircle");
    const subtext = document.getElementById("intermissionSubtext");
    const upNextBadge = document.querySelector("#intermissionOverlay .badge-youtube");
    renderStageMarquee();
    updateStageQrPosition();

    if (nextTrack) {
        if (idleQRBox) idleQRBox.style.display = "none";
        if (countdownCircle) countdownCircle.style.display = "flex";
        if (upNextBadge) {
            upNextBadge.style.display = "inline-block";
            upNextBadge.innerText = "🎤 UP NEXT ON STAGE";
        }
        if (subtext) {
            subtext.innerText = "Get ready! Head up to the microphone! 🌺";
            subtext.style.display = "block";
        }

        const nextSingerText = nextTrack.duet_partner ? `${nextTrack.singer_name} & ${nextTrack.duet_partner}` : nextTrack.singer_name;
        document.getElementById("intermissionSinger").innerText = nextSingerText;
        const saBadge = nextTrack.is_sing_along ? " [🎙️ Sing-Along]" : "";
        document.getElementById("intermissionSong").innerText = `${nextTrack.artist} - ${nextTrack.title}${saBadge}`;
        document.getElementById("intermissionKey").innerText = nextTrack.pitch_shift !== 0 ? `Key: ${nextTrack.pitch_shift > 0 ? '+' : ''}${nextTrack.pitch_shift}` : "Key: Standard";

        const lufsEl = document.getElementById("intermissionLufs");
        if (lufsEl) {
            if (typeof nextTrack.normalized_lufs === "number") {
                lufsEl.innerText = `🎚️ ${nextTrack.normalized_lufs.toFixed(1)} LUFS`;
                lufsEl.style.display = "inline-block";
            } else {
                lufsEl.style.display = "none";
            }
        }

        // Virtual DJ TTS Announcer (if enabled)
        speakAnnouncerIntro(nextSingerText, nextTrack.title);
    } else {
        if (idleQRBox) idleQRBox.style.display = "flex";
        if (countdownCircle) countdownCircle.style.display = "none";
        if (upNextBadge) upNextBadge.style.display = "none";
        if (subtext) subtext.style.display = "none";

        document.getElementById("intermissionSinger").innerText = "Queue is Open!";
        document.getElementById("intermissionSong").innerText = "The stage is yours! Pick your favorite song!";
        document.getElementById("intermissionKey").innerText = "";
        const lufsEl = document.getElementById("intermissionLufs");
        if (lufsEl) lufsEl.style.display = "none";
    }

    updateCountdownDisplay();
    clearInterval(intermissionTimer);
    intermissionTimer = setInterval(() => {
        intermissionRemaining--;
        updateCountdownDisplay();
        if (intermissionRemaining <= 0) {
            clearInterval(intermissionTimer);
            if (nextTrack && !isSpectatorViewer) {
                fetch(`/api/queue/${nextTrack.queue_id}/play`, { method: "POST" });
            }
        }
    }, 1000);
}

function stopCurrentTrack(nextTrack) {
    stopCdgPlayback();
    hidePartyWrappedStage();
    if (audioDSP && audioDSP.mediaRecorder && audioDSP.mediaRecorder.state !== "inactive") {
        audioDSP.stopAndUploadPerformanceRecording();
    }
    isIntermission = true;
    if (audioDSP) audioDSP.setBetweenSongs(true);
    currentTrack = null;
    stopFillerPlayback();
    clearInterval(intermissionTimer);
    intermissionTimer = null;
    renderStageMarquee();
    updateStageQrPosition();

    const overlay = document.getElementById("intermissionOverlay");
    if (overlay) overlay.style.display = "flex";
    const nowPlayingBar = document.getElementById("nowPlayingBar");
    if (nowPlayingBar) nowPlayingBar.style.display = "none";
    const localVideo = document.getElementById("localVideo");
    if (localVideo) {
        localVideo.pause();
        localVideo.style.display = "none";
        localVideo.removeAttribute("src");
        localVideo.load();
    }
    const ytContainer = document.getElementById("youtubeContainer");
    if (ytContainer) ytContainer.style.display = "none";
    if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();

    fadeInBGM();

    const idleQRBox = document.getElementById("idleStageQRBox");
    const countdownCircle = document.getElementById("countdownCircle");
    const subtext = document.getElementById("intermissionSubtext");
    const upNextBadge = document.querySelector("#intermissionOverlay .badge-youtube");
    renderStageMarquee();
    updateStageQrPosition();

    if (countdownCircle) countdownCircle.style.display = "none";

    if (nextTrack) {
        if (idleQRBox) idleQRBox.style.display = "none";
        if (upNextBadge) {
            upNextBadge.style.display = "inline-block";
            upNextBadge.innerText = "⏸️ STAGE ON STANDBY";
        }
        if (subtext) {
            subtext.innerText = "Stage on standby. Awaiting Host to press Play / Start! 🌺";
            subtext.style.display = "block";
        }

        const nextSingerText = nextTrack.duet_partner ? `${nextTrack.singer_name} & ${nextTrack.duet_partner}` : nextTrack.singer_name;
        document.getElementById("intermissionSinger").innerText = nextSingerText;
        document.getElementById("intermissionSong").innerText = `${nextTrack.artist} - ${nextTrack.title}`;
        document.getElementById("intermissionKey").innerText = nextTrack.pitch_shift !== 0 ? `Key: ${nextTrack.pitch_shift > 0 ? '+' : ''}${nextTrack.pitch_shift}` : "Key: Standard";
        const lufsEl = document.getElementById("intermissionLufs");
        if (lufsEl) {
            if (typeof nextTrack.normalized_lufs === "number") {
                lufsEl.innerText = `🎚️ ${nextTrack.normalized_lufs.toFixed(1)} LUFS`;
                lufsEl.style.display = "inline-block";
            } else {
                lufsEl.style.display = "none";
            }
        }
    } else {
        if (idleQRBox) idleQRBox.style.display = "flex";
        if (upNextBadge) upNextBadge.style.display = "none";
        if (subtext) subtext.style.display = "none";

        document.getElementById("intermissionSinger").innerText = "Queue is Open!";
        document.getElementById("intermissionSong").innerText = "The stage is yours! Pick your favorite song!";
        document.getElementById("intermissionKey").innerText = "";
        const lufsEl = document.getElementById("intermissionLufs");
        if (lufsEl) lufsEl.style.display = "none";
    }
}

function showPartyWrappedStage(stats) {
    stopCdgPlayback();
    stopFillerPlayback();
    stopIntermission();
    if (audioDSP && audioDSP.mediaRecorder && audioDSP.mediaRecorder.state !== "inactive") {
        audioDSP.stopAndUploadPerformanceRecording();
    }
    const localVideo = document.getElementById("localVideo");
    if (localVideo) {
        localVideo.pause();
        localVideo.style.display = "none";
        localVideo.removeAttribute("src");
        localVideo.load();
    }
    const ytContainer = document.getElementById("youtubeContainer");
    if (ytContainer) ytContainer.style.display = "none";
    if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();

    const nowPlayingBar = document.getElementById("nowPlayingBar");
    if (nowPlayingBar) nowPlayingBar.style.display = "none";

    fadeInBGM();

    const wrappedOverlay = document.getElementById("wrappedOverlay");
    if (!wrappedOverlay) return;

    const totalEl = document.getElementById("wrappedTotalSongs");
    if (totalEl) totalEl.innerText = (stats && stats.total_songs) ? stats.total_songs : 0;

    const podiumEl = document.getElementById("wrappedPodium");
    if (podiumEl) {
        if (stats && stats.top_singers && stats.top_singers.length > 0) {
            const medals = ["🥇", "🥈", "🥉"];
            podiumEl.innerHTML = stats.top_singers.map((s, idx) => `
                <div style="background: rgba(16,28,45,0.92); border: 2px solid ${idx === 0 ? 'var(--neon-gold)' : (idx === 1 ? 'var(--neon-cyan)' : 'var(--neon-pink)')}; border-radius: 18px; padding: 16px 20px; text-align: center; min-width: 170px; box-shadow: 0 0 25px rgba(0,0,0,0.5);">
                    <div style="font-size: 2.2rem; margin-bottom: 4px;">${medals[idx] || '🎖️'} ${s.avatar || '🎤'}</div>
                    <div style="font-weight: 800; font-size: 1.25rem; color: #fff;">${escapeHtml(s.name)}</div>
                    <div style="color: var(--neon-gold); font-size: 0.95rem; font-weight: 700; margin-top: 4px;">${s.song_count} songs</div>
                </div>
            `).join("");
        } else {
            podiumEl.innerHTML = `<div style="color: var(--text-muted); font-size: 1.1rem;">No songs completed yet tonight!</div>`;
        }
    }

    const songsEl = document.getElementById("wrappedTopSongs");
    if (songsEl) {
        if (stats && stats.top_songs && stats.top_songs.length > 0) {
            songsEl.innerHTML = stats.top_songs.map((song, idx) => `
                <div style="font-size: 1.05rem; color: #fff; margin: 4px 0;">
                    <span style="color: var(--neon-pink); font-weight: 700;">#${idx+1}</span> ${escapeHtml(song.title)} <span style="color: var(--neon-cyan);">(${escapeHtml(song.artist)})</span>
                </div>
            `).join("");
        } else {
            songsEl.innerHTML = `<div style="color: var(--text-muted);">No song history recorded yet.</div>`;
        }
    }

    wrappedOverlay.style.display = "flex";
}

function hidePartyWrappedStage() {
    const wrappedOverlay = document.getElementById("wrappedOverlay");
    if (wrappedOverlay) wrappedOverlay.style.display = "none";
}

function stopIntermission() {
    isIntermission = false;
    clearInterval(intermissionTimer);
    document.getElementById("intermissionOverlay").style.display = "none";
}

function updateCountdownDisplay() {
    const timerEl = document.getElementById("countdownNumber");
    if (timerEl) {
        timerEl.innerText = intermissionRemaining > 0 ? intermissionRemaining : "0";
    }
}

function speakAnnouncerIntro(singerName, songTitle) {
    fetch("/api/settings")
        .then(res => res.json())
        .then(settings => {
            if (settings.tts_enabled === "true" && 'speechSynthesis' in window) {
                const phrase = `Give it up for ${singerName} singing ${songTitle}!`;
                const utterance = new SpeechSynthesisUtterance(phrase);
                utterance.rate = 1.05;
                utterance.pitch = 1.1;
                window.speechSynthesis.speak(utterance);
            }
        }).catch(() => {});
}

// BGM Shuffled Playlist Engine
async function initBGMPlaylist() {
    try {
        const res = await fetch("/api/bgm-tracks");
        const data = await res.json();
        if (data.tracks && data.tracks.length > 0) {
            bgmPlaylist = shuffleArray([...data.tracks]);
            currentBgmIndex = 0;
            const bgm = document.getElementById("bgmPlayer");
            if (bgm) {
                bgm.removeAttribute("loop");
                bgm.src = bgmPlaylist[0];
                bgm.onended = playNextBGM;
            }
            console.log(`[BGM] Loaded ${bgmPlaylist.length} background music tracks (shuffled)`);
        }
    } catch (e) {
        console.warn("[BGM] Failed to fetch BGM tracks:", e);
    }
}

function shuffleArray(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

function playNextBGM() {
    if (!bgmPlaylist || bgmPlaylist.length === 0) return;
    currentBgmIndex = (currentBgmIndex + 1) % bgmPlaylist.length;
    if (currentBgmIndex === 0) {
        bgmPlaylist = shuffleArray(bgmPlaylist);
    }
    const nextSrc = bgmPlaylist[currentBgmIndex];
    const bgm = document.getElementById("bgmPlayer");
    if (bgm) {
        bgm.src = nextSrc;
        if (isIntermission && !isMutedAudio) {
            bgm.volume = 0.25;
            bgm.play().catch(() => {});
        }
    }
}

let bgmFadeInterval = null;

function fadeOutBGM() {
    if (bgmFadeInterval) {
        clearInterval(bgmFadeInterval);
        bgmFadeInterval = null;
    }
    const bgm = document.getElementById("bgmPlayer");
    if (!bgm) return;
    let vol = bgm.volume;
    bgmFadeInterval = setInterval(() => {
        if (vol > 0.05) {
            vol -= 0.05;
            bgm.volume = Math.max(0, vol);
        } else {
            bgm.volume = 0;
            bgm.pause();
            clearInterval(bgmFadeInterval);
            bgmFadeInterval = null;
        }
    }, 100);
}

function fadeInBGM() {
    if (currentTrack || isFillerPlaying) return;
    if (bgmFadeInterval) {
        clearInterval(bgmFadeInterval);
        bgmFadeInterval = null;
    }
    const bgm = document.getElementById("bgmPlayer");
    if (!bgm) return;
    if (isMutedAudio) {
        bgm.muted = true;
        return;
    }
    if (!bgm.src && bgmPlaylist.length > 0) {
        bgm.src = bgmPlaylist[currentBgmIndex >= 0 ? currentBgmIndex : 0];
    }
    bgm.volume = 0;
    bgm.play().catch(() => {});
    let vol = 0;
    bgmFadeInterval = setInterval(() => {
        if (currentTrack || isFillerPlaying) {
            bgm.volume = 0;
            bgm.pause();
            clearInterval(bgmFadeInterval);
            bgmFadeInterval = null;
            return;
        }
        if (vol < 0.25) {
            vol += 0.05;
            bgm.volume = Math.min(0.25, vol);
        } else {
            clearInterval(bgmFadeInterval);
            bgmFadeInterval = null;
        }
    }, 150);
}

// ==============================================================
// 🌴 STAGE CROWD PARTICLE & AUDIO-REACTIVE LIVING TIKI ENGINE
// ==============================================================
let crowdCanvas = null;
let crowdCtx = null;
let crowdEmbers = [];
let crowdParticles = [];
let crowdAnimFrameId = null;
let crowdBassSmooth = 0;
let crowdHypeBannerTimer = null;

function initCrowdParticleEngine() {
    crowdCanvas = document.getElementById("crowdParticleCanvas");
    if (!crowdCanvas) return;
    crowdCtx = crowdCanvas.getContext("2d");
    if (!crowdCtx) return;

    function resizeCanvas() {
        if (!crowdCanvas) return;
        const rect = crowdCanvas.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
            crowdCanvas.width = rect.width;
            crowdCanvas.height = rect.height;
        }
    }
    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();

    // Initialize 24 ambient embers floating gently
    crowdEmbers = [];
    const emberColors = ["#ffb703", "#ff2a85", "#00f0ff", "#ff8800"];
    for (let i = 0; i < 24; i++) {
        crowdEmbers.push({
            x: Math.random() * (crowdCanvas.width || 1920),
            y: Math.random() * (crowdCanvas.height || 1080),
            radius: 1.2 + Math.random() * 2.2,
            vy: -0.35 - Math.random() * 0.65,
            vxPhase: Math.random() * Math.PI * 2,
            vxSpeed: 0.015 + Math.random() * 0.02,
            alpha: 0.2 + Math.random() * 0.45,
            color: emberColors[Math.floor(Math.random() * emberColors.length)]
        });
    }

    if (crowdAnimFrameId) cancelAnimationFrame(crowdAnimFrameId);
    renderCrowdParticlesLoop();
}

function renderCrowdParticlesLoop() {
    if (!crowdCanvas || !crowdCtx) return;
    const w = crowdCanvas.width;
    const h = crowdCanvas.height;

    crowdCtx.clearRect(0, 0, w, h);

    // 1. Bass Energy Reading from AudioDSP
    let targetBass = 0;
    if (window.audioDSP && typeof window.audioDSP.getBassEnergy === "function") {
        targetBass = window.audioDSP.getBassEnergy();
    }
    crowdBassSmooth = crowdBassSmooth * 0.82 + targetBass * 0.18;

    // 2. Render Ambient Embers
    const time = Date.now() * 0.002;
    for (let i = 0; i < crowdEmbers.length; i++) {
        const em = crowdEmbers[i];
        em.y += em.vy;
        em.x += Math.sin(time + em.vxPhase) * 0.45;
        if (em.y < -15) {
            em.y = h + 10;
            em.x = Math.random() * w;
        }
        crowdCtx.save();
        crowdCtx.globalAlpha = em.alpha;
        crowdCtx.shadowBlur = 8;
        crowdCtx.shadowColor = em.color;
        crowdCtx.fillStyle = em.color;
        crowdCtx.beginPath();
        crowdCtx.arc(em.x, em.y, em.radius, 0, Math.PI * 2);
        crowdCtx.fill();
        crowdCtx.restore();
    }

    // 3. Render Audio-Reactive Living Neon Tiki Masks in Top Corners
    if (w > 400 && h > 300) {
        // Left Tiki Mask (Electric Cyan theme)
        drawLivingTikiMask(crowdCtx, 52, 60, 0.72, "#00f0ff", "#ff2a85", crowdBassSmooth, true);
        // Right Tiki Mask (Hot Magenta theme)
        drawLivingTikiMask(crowdCtx, w - 52, 60, 0.72, "#ff2a85", "#00f0ff", crowdBassSmooth, false);
    }

    // 4. Render Active Reaction Particles
    for (let i = crowdParticles.length - 1; i >= 0; i--) {
        const p = crowdParticles[i];
        p.life -= 1;
        p.x += p.vx;
        p.y += p.vy;
        p.vy += p.gravity || 0;
        p.alpha = Math.max(0, p.life / p.maxLife);

        crowdCtx.save();
        crowdCtx.globalAlpha = p.alpha;
        crowdCtx.shadowBlur = p.glow ? 12 : 4;
        crowdCtx.shadowColor = p.color;

        if (p.type === "bubble") {
            crowdCtx.strokeStyle = p.color;
            crowdCtx.lineWidth = 1.5;
            crowdCtx.beginPath();
            crowdCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
            crowdCtx.stroke();
            // Highlight shine
            crowdCtx.fillStyle = "rgba(255, 255, 255, 0.8)";
            crowdCtx.beginPath();
            crowdCtx.arc(p.x - p.size * 0.35, p.y - p.size * 0.35, p.size * 0.25, 0, Math.PI * 2);
            crowdCtx.fill();
        } else if (p.type === "spark") {
            crowdCtx.fillStyle = p.color;
            crowdCtx.beginPath();
            crowdCtx.arc(p.x, p.y, p.size * (p.life / p.maxLife), 0, Math.PI * 2);
            crowdCtx.fill();
        } else if (p.type === "star") {
            drawStarParticle(crowdCtx, p.x, p.y, 4, p.size, p.size * 0.4, p.color);
        } else {
            // Default flame/burst
            crowdCtx.fillStyle = p.color;
            crowdCtx.beginPath();
            crowdCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
            crowdCtx.fill();
        }
        crowdCtx.restore();

        if (p.life <= 0) {
            crowdParticles.splice(i, 1);
        }
    }

    crowdAnimFrameId = requestAnimationFrame(renderCrowdParticlesLoop);
}

function drawStarParticle(ctx, cx, cy, spikes, outerRadius, innerRadius, color) {
    let rot = Math.PI / 2 * 3;
    let step = Math.PI / spikes;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(cx, cy - outerRadius);
    for (let i = 0; i < spikes; i++) {
        let x = cx + Math.cos(rot) * outerRadius;
        let y = cy + Math.sin(rot) * outerRadius;
        ctx.lineTo(x, y);
        rot += step;
        x = cx + Math.cos(rot) * innerRadius;
        y = cy + Math.sin(rot) * innerRadius;
        ctx.lineTo(x, y);
        rot += step;
    }
    ctx.lineTo(cx, cy - outerRadius);
    ctx.closePath();
    ctx.fill();
}

function drawLivingTikiMask(ctx, cx, cy, baseScale, neonColor, eyeColor, bass, isLeft) {
    ctx.save();
    ctx.translate(cx, cy);
    const pulse = 1.0 + bass * 0.22;
    ctx.scale(baseScale * pulse, baseScale * pulse);

    ctx.shadowBlur = 12 + bass * 35;
    ctx.shadowColor = neonColor;
    ctx.strokeStyle = neonColor;
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    // 1. Totem Headdress / Crown slats
    ctx.beginPath();
    ctx.moveTo(-24, -30);
    ctx.lineTo(-32, -48);
    ctx.lineTo(-18, -40);
    ctx.lineTo(-8, -52);
    ctx.lineTo(0, -42);
    ctx.lineTo(8, -52);
    ctx.lineTo(18, -40);
    ctx.lineTo(32, -48);
    ctx.lineTo(24, -30);
    ctx.stroke();

    // 2. Head Outline (Carved Totem geometry)
    ctx.beginPath();
    ctx.moveTo(-24, -30);
    ctx.lineTo(24, -30);
    ctx.lineTo(26, -10);
    ctx.lineTo(21, 14);
    ctx.lineTo(25, 34);
    ctx.lineTo(16, 46);
    ctx.lineTo(0, 50);
    ctx.lineTo(-16, 46);
    ctx.lineTo(-25, 34);
    ctx.lineTo(-21, 14);
    ctx.lineTo(-26, -10);
    ctx.closePath();
    ctx.stroke();

    // 3. Forehead Tribal Chevron
    ctx.beginPath();
    ctx.moveTo(-16, -20);
    ctx.lineTo(0, -14);
    ctx.lineTo(16, -20);
    ctx.stroke();

    // 4. Carved Nose Bridge
    ctx.beginPath();
    ctx.moveTo(0, -10);
    ctx.lineTo(0, 10);
    ctx.lineTo(-7, 16);
    ctx.lineTo(7, 16);
    ctx.lineTo(0, 10);
    ctx.stroke();

    // 5. Living Glowing Tiki Eyes
    const eyeGlowAlpha = Math.min(1.0, 0.45 + bass * 0.55);
    ctx.save();
    ctx.shadowBlur = 15 + bass * 30;
    ctx.shadowColor = eyeColor;
    ctx.fillStyle = eyeColor;
    ctx.globalAlpha = eyeGlowAlpha;

    // Left Eye (Slanted almond)
    ctx.beginPath();
    ctx.moveTo(-18, -4);
    ctx.quadraticCurveTo(-11, -12, -4, -4);
    ctx.quadraticCurveTo(-11, 2, -18, -4);
    ctx.fill();

    // Right Eye (Slanted almond)
    ctx.beginPath();
    ctx.moveTo(4, -4);
    ctx.quadraticCurveTo(11, -12, 18, -4);
    ctx.quadraticCurveTo(11, 2, 4, -4);
    ctx.fill();
    ctx.restore();

    // 6. Grinning Mouth with Tiki Teeth
    ctx.beginPath();
    ctx.rect(-15, 23, 30, 14);
    ctx.stroke();

    // Carved teeth grid inside mouth
    ctx.beginPath();
    ctx.moveTo(-15, 30); ctx.lineTo(15, 30); // Center line
    ctx.moveTo(-7.5, 23); ctx.lineTo(-7.5, 37); // Tooth separator L
    ctx.moveTo(0, 23); ctx.lineTo(0, 37);       // Tooth separator C
    ctx.moveTo(7.5, 23); ctx.lineTo(7.5, 37);   // Tooth separator R
    ctx.stroke();

    ctx.restore();
}

function spawnCrowdParticleBurst(emoji) {
    if (!crowdCanvas) return;
    const w = crowdCanvas.width || 1280;
    const h = crowdCanvas.height || 720;

    // Origin: near bottom right / QR code area if active, else stage lower third
    const { activeQR } = getActiveReactionContainer();
    let ox = w * 0.82;
    let oy = h * 0.78;
    if (activeQR) {
        const rect = activeQR.getBoundingClientRect();
        if (rect.width > 0) {
            ox = rect.left + rect.width / 2;
            oy = rect.top + 20;
        }
    }

    if (emoji.includes("🔥")) {
        // Flame / Firestorm explosion: 22 flame particles
        const flameColors = ["#ff0055", "#ff5500", "#ffb703", "#ffea00", "#ffffff"];
        for (let i = 0; i < 22; i++) {
            crowdParticles.push({
                x: ox + (Math.random() - 0.5) * 40,
                y: oy + (Math.random() - 0.5) * 20,
                vx: (Math.random() - 0.5) * 6,
                vy: -3.5 - Math.random() * 6.5,
                gravity: 0.08,
                size: 4 + Math.random() * 6,
                color: flameColors[Math.floor(Math.random() * flameColors.length)],
                life: 45 + Math.floor(Math.random() * 30),
                maxLife: 75,
                type: "flame",
                glow: true
            });
        }
    } else if (emoji.includes("🍹") || emoji.includes("🍻")) {
        // Cocktail Bubbles & Tropical Splash: 18 neon bubbles
        const bubbleColors = ["#00f0ff", "#39ff14", "#ffffaa", "#00d4ff"];
        for (let i = 0; i < 18; i++) {
            crowdParticles.push({
                x: ox + (Math.random() - 0.5) * 50,
                y: oy + (Math.random() - 0.5) * 20,
                vx: (Math.random() - 0.5) * 4.5,
                vy: -2.2 - Math.random() * 4.8,
                gravity: -0.04, // Float upward gently
                size: 5 + Math.random() * 9,
                color: bubbleColors[Math.floor(Math.random() * bubbleColors.length)],
                life: 55 + Math.floor(Math.random() * 35),
                maxLife: 90,
                type: "bubble",
                glow: true
            });
        }
    } else {
        // Starburst / Flower Blossom sparks (👏, 🌺, etc.): 20 radiant stars
        const starColors = ["#ff2a85", "#ffb703", "#00f0ff", "#d946ef", "#ffffff"];
        for (let i = 0; i < 20; i++) {
            const angle = Math.random() * Math.PI * 2;
            const speed = 2.5 + Math.random() * 6.0;
            crowdParticles.push({
                x: ox,
                y: oy,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed - 1.5,
                gravity: 0.12,
                size: 5 + Math.random() * 6,
                color: starColors[Math.floor(Math.random() * starColors.length)],
                life: 40 + Math.floor(Math.random() * 30),
                maxLife: 70,
                type: "star",
                glow: true
            });
        }
    }
}

function triggerCrowdHypeComboBanner(streak) {
    const banner = document.getElementById("crowdHypeComboBanner");
    if (!banner) return;

    banner.style.display = "block";
    requestAnimationFrame(() => {
        banner.style.opacity = "1";
        banner.style.transform = "scale(1.0)";
    });

    clearTimeout(crowdHypeBannerTimer);
    crowdHypeBannerTimer = setTimeout(() => {
        banner.style.opacity = "0";
        banner.style.transform = "scale(0.7)";
        setTimeout(() => {
            if (banner.style.opacity === "0") {
                banner.style.display = "none";
            }
        }, 350);
    }, 3500);
}

// ==========================================
// 🌴 Crowd Hype System & Floating Reactions
// ==========================================
let reactionTotalCount = 0;
let reactionStreakCount = 0;
let lastReactionTimestamp = 0;
let reactionStreakTimer = null;

function getActiveReactionContainer() {
    const fillerQR = document.getElementById("fillerQRCard");
    const joinQR = document.getElementById("joinQROverlay");
    const activeQR = (fillerQR && fillerQR.style.display !== "none" && fillerQR.offsetParent !== null) ? fillerQR : joinQR;

    let container = document.getElementById("reactionContainer");
    if (!container) {
        container = document.createElement("div");
        container.id = "reactionContainer";
        if (activeQR) activeQR.appendChild(container);
    } else if (activeQR && container.parentElement !== activeQR) {
        activeQR.appendChild(container);
    }
    return { container, activeQR };
}

function spawnFloatingEmoji(emoji) {
    const { container, activeQR } = getActiveReactionContainer();
    if (!container) return;

    const now = Date.now();
    if (now - lastReactionTimestamp < 3500) {
        reactionStreakCount++;
    } else {
        reactionStreakCount = 1;
    }
    lastReactionTimestamp = now;
    reactionTotalCount++;

    clearTimeout(reactionStreakTimer);
    reactionStreakTimer = setTimeout(() => {
        reactionStreakCount = 0;
    }, 3500);

    // Stage Particle Burst & Hyper-Drive Combo Multiplier Banner
    spawnCrowdParticleBurst(emoji);
    if (reactionStreakCount >= 4) {
        triggerCrowdHypeComboBanner(reactionStreakCount);
    }

    // Is this a Milestone Hype event? (Every 5th reaction or when streak is a multiple of 5)
    const isMilestone = (reactionTotalCount % 5 === 0) || (reactionStreakCount >= 5 && reactionStreakCount % 5 === 0);

    const el = document.createElement("div");
    el.className = "floating-reaction";
    el.innerText = emoji;

    // Horizontal range across the width of the QR card
    const cardWidth = activeQR ? Math.max(180, activeQR.offsetWidth) : 240;
    const spawnLeft = Math.floor(10 + Math.random() * (cardWidth - 55));
    // Vertical: 12px to 32px strictly ABOVE the top roof of the card
    const spawnBottom = Math.floor(12 + Math.random() * 20);

    el.style.left = `${spawnLeft}px`;
    el.style.bottom = `${spawnBottom}px`;

    if (isMilestone) {
        // Special Hype Milestone: BAM slam, Electric zap, Mega hype fire, or Starburst
        const milestoneTypes = ["bam", "zap", "fire", "starburst"];
        const pick = milestoneTypes[Math.floor(Math.random() * milestoneTypes.length)];

        if (pick === "bam") {
            el.classList.add("reaction-bam");
            // Spawn neon shockwave ring centered around the emoji
            const ring = document.createElement("div");
            ring.className = "reaction-shockwave";
            ring.style.left = `${spawnLeft - 20}px`;
            ring.style.bottom = `${spawnBottom - 20}px`;
            container.appendChild(ring);
            setTimeout(() => ring.remove(), 900);
        } else if (pick === "zap") {
            el.classList.add("reaction-zap");
        } else if (pick === "fire") {
            el.classList.add("reaction-fire");
            // Add mini Hype streak badge
            const badge = document.createElement("span");
            badge.style.cssText = "position: absolute; bottom: -8px; left: 50%; transform: translateX(-50%); font-size: 0.65rem; font-weight: 800; background: linear-gradient(135deg, #ff007f, #ffb703); color: #fff; padding: 2px 8px; border-radius: 10px; box-shadow: 0 0 12px #ff007f; white-space: nowrap;";
            badge.innerText = `🔥 HYPE x${reactionStreakCount > 1 ? reactionStreakCount : 5}!`;
            el.appendChild(badge);
        } else if (pick === "starburst") {
            el.classList.add("reaction-pop");
            // Burst 4-6 mini sparkles radially
            const sparkles = ["✨", "⭐", "💫", "🎉"];
            for (let i = 0; i < 5; i++) {
                const sp = document.createElement("div");
                sp.className = "reaction-sparkle";
                sp.innerText = sparkles[Math.floor(Math.random() * sparkles.length)];
                sp.style.left = `${spawnLeft + 12}px`;
                sp.style.bottom = `${spawnBottom + 12}px`;
                const angle = (i / 5) * 2 * Math.PI;
                const dist = 50 + Math.random() * 30;
                sp.style.setProperty("--dx", `${Math.cos(angle) * dist}px`);
                sp.style.setProperty("--dy", `${Math.sin(angle) * dist - 25}px`);
                container.appendChild(sp);
                setTimeout(() => sp.remove(), 1250);
            }
        }
    } else {
        // Standard reaction: choose random base float style
        const standardStyles = ["reaction-sway", "reaction-pop", "reaction-glide"];
        const style = standardStyles[Math.floor(Math.random() * standardStyles.length)];
        el.classList.add(style);
        const randomSize = (2.3 + Math.random() * 0.9).toFixed(2);
        el.style.fontSize = `${randomSize}rem`;
    }

    container.appendChild(el);
    setTimeout(() => el.remove(), 3400);
}

// Singer Registration Notification Pill (Decoupled from Hype system)
function spawnSingerJoinedPill(singerName, avatar) {
    const { container, activeQR } = getActiveReactionContainer();
    if (!container) return;

    const el = document.createElement("div");
    el.className = "singer-joined-pill";
    
    const av = avatar || "🌴";
    const name = singerName || "Guest Singer";
    el.innerHTML = `<span style="font-size: 1.25rem;">${av}</span> <span style="color: var(--neon-cyan);">${escapeHtml(name)}</span> <span style="color: var(--text-muted); font-size: 0.85rem; font-weight: 600;">joined!</span>`;

    // Position directly above the card, centered
    el.style.left = "50%";
    el.style.transform = "translateX(-50%)";
    el.style.bottom = "16px";

    container.appendChild(el);
    setTimeout(() => el.remove(), 4300);
}

function playSoundEffect(sound) {
    const audio = new Audio(`/static/sounds/${sound}.mp3`);
    audio.play().catch(() => {});
}

function confirmHostShutdown() {
    if (!confirm("Are you sure you want to shut down Tiki Karaoke and stop all server processes?")) return;
    fetch("/api/host/shutdown", { method: "POST" })
        .then(res => res.json())
        .then(data => {
            alert(data.message || "Tiki Karaoke is shutting down. Aloha! 🌺");
            setTimeout(() => { window.close(); }, 800);
        })
        .catch(() => {
            alert("Shutdown command sent. Window will now close.");
            setTimeout(() => { window.close(); }, 800);
        });
}

function showSyncToast(msg) {
    let toast = document.getElementById("stageSyncToast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "stageSyncToast";
        toast.style.cssText = "position: fixed; top: 80px; right: 30px; z-index: 9999; background: rgba(8,14,24,0.92); border: 1px solid var(--neon-cyan); color: #fff; padding: 10px 18px; border-radius: 12px; font-size: 0.95rem; font-weight: 700; box-shadow: 0 0 20px rgba(0,240,255,0.4); pointer-events: none; transition: opacity 0.3s ease;";
        document.body.appendChild(toast);
    }
    toast.innerText = msg || "🔄 Stage Player Synced";
    toast.style.opacity = "1";
    toast.style.display = "block";
    clearTimeout(window._syncToastTimer);
    window._syncToastTimer = setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => { toast.style.display = "none"; }, 350);
    }, 1800);
}

function resyncPlayhead(targetSeconds, forceResync = false) {
    if (typeof targetSeconds !== "number" || targetSeconds < 0) return;
    const localVideo = document.getElementById("localVideo");
    if (localVideo && localVideo.style.display !== "none" && localVideo.src && !localVideo.paused) {
        const current = localVideo.currentTime;
        const drift = current - targetSeconds; // positive = ahead, negative = behind
        const absDrift = Math.abs(drift);

        if (forceResync) {
            localVideo.currentTime = targetSeconds;
            localVideo.playbackRate = 1.0;
            const dir = drift < 0 ? `${absDrift.toFixed(2)}s behind` : `${absDrift.toFixed(2)}s ahead`;
            showSyncToast(`🔄 Synced: ${absDrift < 0.15 ? 'Locked in sync' : 'Corrected ' + dir}`);
            console.log(`[Stage Sync] Force resynced playhead to ${targetSeconds.toFixed(2)}s (drift was ${drift.toFixed(3)}s)`);
            return;
        }

        // Automatic drift management:
        // Hard-seek if drift is substantial (> 1.2s)
        if (absDrift > 1.2) {
            console.log(`[Stage Sync] Correcting large drift: ${drift.toFixed(2)}s (video: ${current.toFixed(2)}s -> server: ${targetSeconds.toFixed(2)}s)`);
            localVideo.currentTime = targetSeconds;
            localVideo.playbackRate = 1.0;
        } 
        // Smooth dynamic playback rate alignment for mild drift (0.15s - 1.2s)
        else if (absDrift > 0.15) {
            if (drift < 0) {
                // Video is slightly behind server: subtly speed up (1.05x) to catch up seamlessly
                localVideo.playbackRate = 1.05;
            } else {
                // Video is slightly ahead of server: subtly slow down (0.95x)
                localVideo.playbackRate = 0.95;
            }
        } else {
            // Drift is negligible (< 150ms): maintain normal 1.0x playback rate
            if (localVideo.playbackRate !== 1.0) {
                localVideo.playbackRate = 1.0;
            }
        }
    } else if (ytPlayer && typeof ytPlayer.getCurrentTime === "function" && typeof ytPlayer.seekTo === "function") {
        const isYtPaused = typeof ytPlayer.getPlayerState === "function" && ytPlayer.getPlayerState() === (window.YT ? YT.PlayerState.PAUSED : 2);
        if (!isYtPaused || forceResync) {
            const ytTime = ytPlayer.getCurrentTime();
            const drift = Math.abs(ytTime - targetSeconds);
            if (forceResync || drift > 1.5) {
                ytPlayer.seekTo(targetSeconds, true);
                if (forceResync) {
                    showSyncToast(`🔄 Synced YouTube playhead (${drift.toFixed(2)}s corrected)`);
                }
            }
        }
    } else if (forceResync) {
        showSyncToast("🔄 Stage Player Synced");
    }
}

function fetchCurrentState(forceResync = false) {
    const t0 = performance.now();
    fetch("/api/state")
        .then(res => res.json())
        .then(data => {
            const rttSec = (performance.now() - t0) / 1000;
            if (audioDSP && typeof data.av_sync_offset_ms === "number") {
                audioDSP.setSyncOffset(data.av_sync_offset_ms);
            }
            currentNextTrack = data.next || null;
            if (data.playing) {
                // Only start playback if we are not already playing this track
                if (!currentTrack || currentTrack.queue_id !== data.playing.queue_id) {
                    console.log("[Stage Sync] Detected new playing track from server:", data.playing.title);
                    stopFillerPlayback();
                    startTrackPlayback(data.playing);
                } else if (typeof data.playing.elapsed_seconds === "number") {
                    // Account for half RTT transit latency to sync playhead precisely
                    const targetSec = data.playing.elapsed_seconds + (rttSec / 2);
                    resyncPlayhead(targetSec, forceResync);
                }
            } else if (currentTrack) {
                // Server says nothing playing, but we had a track playing -> track was skipped or ended
                console.log("[Stage Sync] Track ended or skipped on server");
                skipCurrentTrack();
                if (data.next) {
                    triggerIntermission(data.next, data.intermission_seconds || 10);
                } else {
                    triggerIntermission(null, 0);
                }
            } else if (data.filler && data.filler.active) {
                if (data.filler.state === "buffer_15s") {
                    const buf = document.getElementById("fillerBufferOverlay");
                    if (!buf || buf.style.display !== "flex") {
                        triggerFillerBuffer(data.filler.buffer_remaining || 15);
                    }
                } else if (data.filler.current_track) {
                    if (!isFillerPlaying || (currentFillerTrack && currentFillerTrack.id !== data.filler.current_track.id)) {
                        startFillerPlayback(data.filler.current_track);
                    }
                }
            } else if (data.next) {
                stopFillerPlayback();
                if (!isIntermission || !intermissionTimer) {
                    triggerIntermission(data.next, data.intermission_seconds || 10);
                }
            } else {
                stopFillerPlayback();
                if (!isIntermission) {
                    triggerIntermission(null, 0);
                }
            }
            if (data.queue) updateNextTicker(data.queue);
            fetch("/api/downloads/pending")
                .then(r => r.json())
                .then(pData => {
                    stagePendingDownloads = pData.pending_downloads || [];
                    if (data.queue) updateNextTicker(data.queue);
                    renderStageMarquee();
                })
                .catch(() => {});
            if (data.marquee) {
                if (data.marquee.speed) {
                    configuredMarqueeSpeed = parseInt(data.marquee.speed, 10) || configuredMarqueeSpeed;
                }
                const newM = data.marquee;
                const oldM = currentMarqueeState;
                const changed = !oldM ||
                    oldM.message !== newM.message ||
                    oldM.mode !== newM.mode ||
                    oldM.expires_at !== newM.expires_at ||
                    oldM.queue_ticker !== newM.queue_ticker ||
                    oldM.current_dedication !== newM.current_dedication ||
                    oldM.speed !== newM.speed;
                currentMarqueeState = newM;
                if (changed) {
                    renderStageMarquee();
                }
            }
        })
        .catch(err => {
            console.warn("[Stage Sync] Notice: state poll error:", err);
        });
}

// Periodic background sync every 2.5s to ensure display never desyncs if socket drops
setInterval(() => {
    fetchCurrentState();
}, 2500);

function fetchNextTrackForIntermission() {
    fetch("/api/queue")
        .then(res => res.json())
        .then(queue => {
            const next = queue.find(q => q.status === "pending");
            triggerIntermission(next, 20);
        });
}

function updateNextTicker(queue) {
    currentQueue = queue || [];
    const ticker = document.getElementById("upNextSinger");
    if (!ticker) return;
    const pending = (queue || []).filter(q => q.status === "pending");
    if (pending.length > 0) {
        currentNextTrack = pending[0];
        const nextSinger = pending[0].duet_partner ? `${pending[0].singer_name} & ${pending[0].duet_partner}` : pending[0].singer_name;
        const saBadge = pending[0].is_sing_along ? " [🎙️ Sing-Along]" : "";
        ticker.innerText = `${nextSinger} ("${pending[0].title}")${saBadge}`;
    } else if (stagePendingDownloads && stagePendingDownloads.length > 0) {
        currentNextTrack = null;
        const dl = stagePendingDownloads[0];
        const pct = Math.round(dl.progress || 0);
        ticker.innerText = `🌴 Preparing next song for ${dl.singer_name} (${dl.title}) - Downloading ${pct}%...`;
    } else {
        currentNextTrack = null;
        ticker.innerText = "Queue is open — scan to sing!";
    }
}

function setupEventListeners() {
    // Keyboard shortcuts for Host DJ
    window.addEventListener("keydown", (e) => {
        const tag = (e.target && e.target.tagName) ? e.target.tagName.toUpperCase() : "";
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

        if (e.key === "h" || e.key === "H") {
            toggleHostQRModal();
        } else if (e.key === " ") {
            // Space to Pause/Resume
            e.preventDefault();
            pausePlayback();
        } else if (e.key === "n" || e.key === "N") {
            // N for Next/Skip
            fetch("/api/host/skip", { method: "POST" });
        } else if (e.key === "r" || e.key === "R") {
            // R for Refresh / Resync
            if (e.shiftKey) {
                window.location.reload(true);
            } else {
                fetchCurrentState(true);
            }
        } else if (e.key === "[") {
            // Pitch down 1 semitone
            if (audioDSP) {
                audioDSP.setPitch(audioDSP.currentPitch - 1);
                updatePitchBadge(audioDSP.currentPitch);
            }
        } else if (e.key === "]") {
            // Pitch up 1 semitone
            if (audioDSP) {
                audioDSP.setPitch(audioDSP.currentPitch + 1);
                updatePitchBadge(audioDSP.currentPitch);
            }
        } else if (e.key === "p" || e.key === "P") {
            // P for Microphone Mute/Live toggle
            toggleStageMic();
        } else if (e.key === "m" || e.key === "M") {
            // M for Mute/Unmute
            toggleStageAudioMute();
        }
    });

    // Auto-dismiss unmute overlay whenever video actually starts playing
    const localV = document.getElementById("localVideo");
    const fillerV = document.getElementById("fillerVideo");
    if (localV) {
        localV.addEventListener("playing", () => {
            const overlay = document.getElementById("unmuteOverlay");
            if (overlay) overlay.style.display = "none";
        });
        localV.addEventListener("timeupdate", () => {
            if (!currentTrack || isSpectatorViewer || !currentNextTrack) return;
            const dur = localV.duration;
            const cur = localV.currentTime;
            if (!dur || isNaN(dur) || dur < 35) return;
            const remaining = dur - cur;
            if (remaining <= 30 && onDeckAlertFiredForQueueId !== currentNextTrack.queue_id) {
                onDeckAlertFiredForQueueId = currentNextTrack.queue_id;
                console.log(`[Stage] ~30s remaining in current song. Notifying next singer on-deck for track ${currentNextTrack.queue_id}`);
                fetch(`/api/stage/notify-on-deck?queue_id=${currentNextTrack.queue_id}`, { method: "POST" }).catch(() => {});
            }
        });
    }
    if (fillerV) {
        fillerV.addEventListener("playing", () => {
            const overlay = document.getElementById("unmuteOverlay");
            if (overlay) overlay.style.display = "none";
        });
    }

    // Auto-hide mouse cursor after 3.5s of inactivity so TV stage display is clean
    let mouseIdleTimer = null;
    window.addEventListener("mousemove", () => {
        document.body.style.cursor = "default";
        if (mouseIdleTimer) clearTimeout(mouseIdleTimer);
        mouseIdleTimer = setTimeout(() => {
            document.body.style.cursor = "none";
        }, 3500);
    });

    // Request screen wake lock so the display never sleeps during a show
    let screenWakeLock = null;
    async function requestScreenWakeLock() {
        if ("wakeLock" in navigator && !screenWakeLock) {
            try {
                screenWakeLock = await navigator.wakeLock.request("screen");
                screenWakeLock.addEventListener("release", () => {
                    screenWakeLock = null;
                });
            } catch (e) { console.debug('[Tiki]', e); }
        }
    }
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") requestScreenWakeLock();
    });
    window.addEventListener("click", () => requestScreenWakeLock(), { once: true });
    requestScreenWakeLock();
}

function applyStageAudioMute(muted) {
    isMutedAudio = muted;
    isMutedViewer = muted;
    window.isMutedAudio = muted;
    const btn = document.getElementById("stageAudioToggleBtn");
    const videoEl = document.getElementById("localVideo");
    const bgm = document.getElementById("bgmPlayer");
    const fillerVideo = document.getElementById("fillerVideo");

    if (isMutedAudio) {
        if (videoEl) videoEl.muted = true;
        if (bgm) bgm.muted = true;
        if (fillerVideo) fillerVideo.muted = true;
        if (ytPlayer && ytPlayer.mute) ytPlayer.mute();
        if (audioDSP) audioDSP.setMasterVolume(0.0);
        if (btn) {
            btn.innerText = "🔇 Muted";
            btn.style.color = "#ff5555";
            btn.style.borderColor = "rgba(255,85,85,0.4)";
        }
    } else {
        if (videoEl) videoEl.muted = false;
        if (bgm) bgm.muted = false;
        if (fillerVideo) fillerVideo.muted = false;
        if (ytPlayer && ytPlayer.unMute) ytPlayer.unMute();
        if (btn) {
            btn.innerText = "🔊 Audio Active (Master)";
            btn.style.color = "var(--neon-cyan)";
            btn.style.borderColor = "var(--neon-cyan)";
        }
        if (audioDSP) {
            audioDSP.setMasterVolume(1.0);
            audioDSP.resume();
        }
    }
}
window.applyStageAudioMute = applyStageAudioMute;

function toggleStageAudioMute() {
    applyStageAudioMute(!isMutedAudio);
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "stage_audio_state",
            display_id: myDisplayId,
            is_muted: isMutedAudio
        }));
    }
}

function toggleHostQRModal() {
    const modal = document.getElementById("hostQRModal");
    modal.style.display = modal.style.display === "flex" ? "none" : "flex";
}

async function applyAudioSink() {
    try {
        const res = await fetch("/api/settings");
        const settings = await res.json();
        configuredAudioSink = settings.audio_output_device;
        if (configuredAudioSink && configuredAudioSink !== "default") {
            const videoEl = document.getElementById("localVideo");
            const bgm = document.getElementById("bgmPlayer");
            if (videoEl && typeof videoEl.setSinkId === "function") {
                await videoEl.setSinkId(configuredAudioSink);
            }
            if (bgm && typeof bgm.setSinkId === "function") {
                await bgm.setSinkId(configuredAudioSink);
            }
            console.log("[AudioSink] Applied audio sink device:", configuredAudioSink);
        }
    } catch (e) {
        console.log("[AudioSink] Notice:", e);
    }
}

function showTestDisplayFlash(badge) {
    let overlay = document.getElementById("testDisplayFlashOverlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "testDisplayFlashOverlay";
        overlay.style.position = "fixed";
        overlay.style.top = "0";
        overlay.style.left = "0";
        overlay.style.width = "100vw";
        overlay.style.height = "100vh";
        overlay.style.zIndex = "999999";
        overlay.style.background = "radial-gradient(circle at center, rgba(10, 20, 35, 0.95) 0%, rgba(5, 8, 15, 0.98) 100%)";
        overlay.style.display = "flex";
        overlay.style.flexDirection = "column";
        overlay.style.alignItems = "center";
        overlay.style.justifyContent = "center";
        overlay.style.border = "4px solid #00f0ff";
        overlay.style.boxShadow = "inset 0 0 60px rgba(0, 240, 255, 0.6)";
        overlay.style.transition = "opacity 0.4s ease";
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
        <div style="font-size: 80px; margin-bottom: 20px;">📺</div>
        <div style="font-size: 38px; font-weight: 900; color: #00f0ff; text-shadow: 0 0 25px rgba(0, 240, 255, 0.8); text-align: center; padding: 0 20px;">
            ${badge || "STAGE DISPLAY"}
        </div>
        <div style="margin-top: 15px; font-size: 20px; color: #ff007f; font-weight: bold; letter-spacing: 2px;">
            IDENTIFICATION CONFIRMED 🌺
        </div>
    `;
    overlay.style.opacity = "1";
    overlay.style.display = "flex";

    if (window._testDisplayTimer) clearTimeout(window._testDisplayTimer);
    window._testDisplayTimer = setTimeout(() => {
        overlay.style.opacity = "0";
        setTimeout(() => { overlay.style.display = "none"; }, 400);
    }, 4000);
}

// =============================================================================
// BGM FILLER & MUSIC VIDEOS ENGINE
// =============================================================================

