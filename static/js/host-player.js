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
let intermissionRemaining = 20;
let isIntermission = false;
let isSpectatorViewer = false;
let isMutedAudio = false;
let isMutedViewer = false; // Kept for backward compatibility
let configuredAudioSink = null;
let bgmPlaylist = [];
let currentBgmIndex = -1;
let currentNextTrack = null;
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
        resumeStagePlayback();
    });
}

function resumeStagePlayback() {
    const videoEl = document.getElementById("localVideo");
    if (isMutedAudio) {
        if (videoEl) videoEl.muted = true;
        const bgm = document.getElementById("bgmPlayer");
        if (bgm) bgm.muted = true;
    } else if (audioDSP) {
        audioDSP.init(videoEl);
        audioDSP.resume();
    }
    const bgm = document.getElementById("bgmPlayer");
    if (bgm && bgm.paused && isIntermission && !isMutedAudio) {
        bgm.play().catch(() => {});
    }
    if (videoEl && videoEl.style.display !== "none") {
        if (currentTrack && currentTrack.elapsed_seconds && videoEl.currentTime < currentTrack.elapsed_seconds - 3) {
            videoEl.currentTime = currentTrack.elapsed_seconds;
        }
        if (isMutedAudio) videoEl.muted = true;
        videoEl.play().then(() => {
            const unmute = document.getElementById("unmuteOverlay");
            if (unmute) unmute.style.display = "none";
        }).catch(e => console.log(e));
    }
    if (ytPlayer && ytPlayer.playVideo) {
        if (isMutedAudio && ytPlayer.mute) ytPlayer.mute();
        ytPlayer.playVideo();
    }
    const unmute = document.getElementById("unmuteOverlay");
    if (unmute) unmute.style.display = "none";
}

let wsPingInterval = null;

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
    } catch (e) {}

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
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
        console.log("[Host WS] Disconnected, retrying in 2s...");
        if (wsPingInterval) {
            clearInterval(wsPingInterval);
            wsPingInterval = null;
        }
        setTimeout(initWebSocket, 2000);
    };
}

function handleSocketMessage(data) {
    switch (data.type) {
        case "pong":
            // Heartbeat response received
            break;
        case "set_display_audio":
            if (data.display_id === myDisplayId) {
                applyStageAudioMute(data.is_muted);
            }
            break;
        case "play_track":
            startTrackPlayback(data.track);
            break;
        case "skip_track":
            skipCurrentTrack();
            break;
        case "skip_bgm":
            playNextBGM();
            break;
        case "stop_track":
            stopCurrentTrack(data.next_track);
            break;
        case "queue_empty":
            skipCurrentTrack();
            stopIntermission();
            stopFillerPlayback();
            triggerIntermission(null, 0);
            break;
        case "reload_display":
            console.log("[Stage] Reload display command received from host.");
            window.location.reload(true);
            break;
        case "resync_state":
            console.log("[Stage] Resync state command received from host.");
            fetchCurrentState(true);
            break;
        case "pause_track":
            pausePlayback();
            break;
        case "resume_track":
            resumePlayback();
            break;
        case "pitch_change":
            if (audioDSP) audioDSP.setPitch(data.pitch);
            updatePitchBadge(data.pitch);
            break;
        case "eq_change":
            if (audioDSP) audioDSP.setEQ(data.bass, data.mid, data.treble);
            break;
        case "volume_change":
            if (audioDSP) audioDSP.setMasterVolume(data.volume);
            break;
        case "host_mic_settings":
            handleHostMicSettings(data);
            break;
        case "emoji_reaction":
            spawnFloatingEmoji(data.emoji);
            break;
        case "network_updated":
            updateStageNetworkUI(data);
            break;
        case "singer_joined":
            spawnSingerJoinedPill(data.name, data.avatar);
            break;
        case "queue_updated":
            updateNextTicker(data.queue);
            break;
        case "start_intermission":
            triggerIntermission(data.next_track, data.duration || 20);
            break;
        case "start_filler_buffer":
            triggerFillerBuffer(data.duration || 15);
            break;
        case "play_filler_track":
            startFillerPlayback(data.track);
            break;
        case "stop_filler":
            stopFillerPlayback();
            break;
        case "filler_transition_fade":
            handleFillerTransitionFade(data);
            break;
        case "filler_transition_finish":
            handleFillerTransitionFinish(data);
            break;
        case "soundboard_play":
            playSoundEffect(data.sound);
            break;
        case "av_sync_updated":
            if (audioDSP) audioDSP.setSyncOffset(data.offset_ms);
            break;
        case "test_display":
            showTestDisplayFlash(data.badge);
            break;
        case "server_shutting_down":
            showSyncToast("🛑 Closing session... Aloha! 🌺");
            setTimeout(() => {
                try {
                    if (document.fullscreenElement) {
                        document.exitFullscreen().catch(() => {});
                    }
                } catch (e) {}
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
            break;
    }
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
                    console.error("[YouTube] Player error code:", e.data);
                    onPlaybackFailure(`YouTube error code ${e.data}`, currentTrack);
                }
            }
        });
    };
}

function onYouTubeStateChange(event) {
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
    if (audioDSP) audioDSP.setBetweenSongs(false);
    onDeckAlertFiredForQueueId = null;

    document.getElementById("nowPlayingBar").style.display = "flex";
    const joinQR = document.getElementById("joinQROverlay");
    if (joinQR) joinQR.style.bottom = "105px";
    document.getElementById("intermissionOverlay").style.display = "none";
    const singerText = track.duet_partner ? `${track.singer_name} & ${track.duet_partner}` : track.singer_name;
    document.getElementById("nowSinger").innerText = singerText;
    document.getElementById("nowTitle").innerText = `${track.artist} - ${track.title}`;
    updatePitchBadge(track.pitch_shift || 0);

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
                const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
                if (isMobile && unmuteOverlay && localVideo.paused) {
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
    if (!currentTrack) return;
    if (isSpectatorViewer) return;
    fetch(`/api/queue/${currentTrack.queue_id}/complete`, { method: "POST" });
    currentTrack = null;
    fadeInBGM();
    fetchNextTrackForIntermission();
}

function skipCurrentTrack() {
    stopCdgPlayback();
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
    if (pitch === 0) {
        badge.innerText = "Key: Standard";
        badge.style.display = "inline-block";
    } else {
        const sign = pitch > 0 ? "+" : "";
        badge.innerText = `Key: ${sign}${pitch}`;
        badge.style.display = "inline-block";
    }
}

// Intermission & Countdown Timer
function triggerIntermission(nextTrack, duration = 20) {
    isIntermission = true;
    if (audioDSP) audioDSP.setBetweenSongs(true);
    intermissionRemaining = duration;
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
    const joinQR = document.getElementById("joinQROverlay");
    if (joinQR) joinQR.style.bottom = "25px";

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
    isIntermission = true;
    if (audioDSP) audioDSP.setBetweenSongs(true);
    currentTrack = null;
    stopFillerPlayback();
    clearInterval(intermissionTimer);
    intermissionTimer = null;

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
    const joinQR = document.getElementById("joinQROverlay");
    if (joinQR) joinQR.style.bottom = "25px";

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
    } else {
        if (idleQRBox) idleQRBox.style.display = "flex";
        if (upNextBadge) upNextBadge.style.display = "none";
        if (subtext) subtext.style.display = "none";

        document.getElementById("intermissionSinger").innerText = "Queue is Open!";
        document.getElementById("intermissionSong").innerText = "The stage is yours! Pick your favorite song!";
        document.getElementById("intermissionKey").innerText = "";
    }
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

function fadeOutBGM() {
    const bgm = document.getElementById("bgmPlayer");
    if (!bgm) return;
    let vol = bgm.volume;
    const interval = setInterval(() => {
        if (vol > 0.05) {
            vol -= 0.05;
            bgm.volume = Math.max(0, vol);
        } else {
            bgm.volume = 0;
            bgm.pause();
            clearInterval(interval);
        }
    }, 100);
}

function fadeInBGM() {
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
    const interval = setInterval(() => {
        if (vol < 0.25) {
            vol += 0.05;
            bgm.volume = Math.min(0.25, vol);
        } else {
            clearInterval(interval);
        }
    }, 150);
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
        const ytTime = ytPlayer.getCurrentTime();
        const drift = Math.abs(ytTime - targetSeconds);
        if (forceResync || drift > 1.5) {
            ytPlayer.seekTo(targetSeconds, true);
            if (forceResync) {
                showSyncToast(`🔄 Synced YouTube playhead (${drift.toFixed(2)}s corrected)`);
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
    const ticker = document.getElementById("upNextSinger");
    const pending = queue.filter(q => q.status === "pending");
    if (pending.length > 0) {
        currentNextTrack = pending[0];
        const nextSinger = pending[0].duet_partner ? `${pending[0].singer_name} & ${pending[0].duet_partner}` : pending[0].singer_name;
        const saBadge = pending[0].is_sing_along ? " [🎙️ Sing-Along]" : "";
        ticker.innerText = `${nextSinger} ("${pending[0].title}")${saBadge}`;
    } else {
        currentNextTrack = null;
        ticker.innerText = "Queue is open — scan to sing!";
    }
}

function setupEventListeners() {
    // Keyboard shortcuts for Host DJ
    window.addEventListener("keydown", (e) => {
        if (e.key === "h" || e.key === "H") {
            toggleHostQRModal();
        } else if (e.key === " ") {
            // Space to Pause/Resume
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
            if (audioDSP) audioDSP.setPitch(audioDSP.currentPitch - 1);
        } else if (e.key === "]") {
            // Pitch up 1 semitone
            if (audioDSP) audioDSP.setPitch(audioDSP.currentPitch + 1);
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
            } catch (e) {}
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

    const intermission = document.getElementById("intermissionOverlay");
    if (intermission) intermission.style.display = "none";

    const nowBar = document.getElementById("nowPlayingBar");
    if (nowBar) nowBar.style.display = "none";

    fadeOutBGM();

    // Hide standard join QR overlay so it doesn't clash with fillerQRCard
    const joinQR = document.getElementById("joinQROverlay");
    if (joinQR) joinQR.style.display = "none";

    const qrCard = document.getElementById("fillerQRCard");
    if (qrCard) qrCard.style.display = "flex";

    const tickerBar = document.getElementById("fillerTickerBar");
    if (tickerBar) {
        tickerBar.style.display = "flex";
        tickerBar.style.background = "rgba(8, 14, 24, 0.95)";
        tickerBar.style.borderTop = "1px solid rgba(0, 240, 255, 0.3)";
    }

    const tickerText = document.getElementById("fillerTickerText");
    if (tickerText) {
        tickerText.style.animation = "none";
        tickerText.style.textAlign = "left";
        tickerText.style.width = "auto";
        tickerText.style.paddingLeft = "20px";
        tickerText.style.color = "var(--neon-cyan)";
        tickerText.style.fontSize = "0.95rem";
        tickerText.style.fontWeight = "600";
        if (track.is_sing_along) {
            tickerText.innerText = `🎤 SING-ALONG INTERMISSION: ${track.artist} - ${track.title} • 🌺 Sing along with the stage or scan to queue next!`;
        } else {
            tickerText.innerText = `🌴 DJ LOUNGE MUSIC: ${track.artist} - ${track.title} • 🌺 OPEN MIC: Scan the QR code to queue your favorite song!`;
        }
        tickerText.offsetHeight; /* trigger reflow to reset animation from right */
        tickerText.style.animation = "tickerMarquee 25s linear infinite";
    }

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
                tickerText.style.paddingLeft = "20px";
                tickerText.innerText = `🎤 UP NEXT: ${data.next_singer} with "${data.next_title}" by ${data.next_artist} right after this video! • 🌺 Scan QR to join the queue!`;
                tickerText.style.animation = "none";
                tickerText.offsetHeight; /* trigger reflow */
                tickerText.style.animation = "tickerMarquee 25s linear infinite";
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

    const fillerVideo = document.getElementById("fillerVideo");
    if (fillerVideo) {
        fillerVideo.pause();
        fillerVideo.style.display = "none";
        fillerVideo.removeAttribute("src");
        fillerVideo.volume = 1.0;
    }
}

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
        } catch (e) {}

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
            } catch (e) {}
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
            } catch (e) {}
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
        if (!audioDSP.micStream) {
            const devId = localStorage.getItem("tiki_mic_device") || "default";
            await audioDSP.connectMicrophone(devId);
        }
        audioDSP.setMicMuted(hostMicMuted);
        audioDSP.setMicVolume(hostMicVolume / 100.0);
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
    if (wifiQrImg) wifiQrImg.src = `/qr/wifi?t=${t}`;
    if (publicQrImg) publicQrImg.src = `/qr/public?t=${t}`;
    if (fillerQrImg) fillerQrImg.src = `/qr/public?t=${t}`;
    if (joinQrImg) joinQrImg.src = `/qr/public?t=${t}`;

    if (fillerDisplayIp && data.join_url) {
        fillerDisplayIp.innerText = data.join_url;
    }
    if (joinDisplayIp && data.join_url) {
        joinDisplayIp.innerText = data.join_url;
    }
}



