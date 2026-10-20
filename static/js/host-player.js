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
            fetchCurrentState();
            showSyncToast("🔄 Stage Resynced by Host");
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
        case "emoji_reaction":
            spawnFloatingEmoji(data.emoji);
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

function startTrackPlayback(track) {
    if (!track) return;
    currentTrack = track;
    stopIntermission();
    stopFillerPlayback();
    fadeOutBGM();
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
        if (seekTime > 0) {
            localVideo.currentTime = seekTime;
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
    if (!currentTrack) return;
    if (isSpectatorViewer) return;
    fetch(`/api/queue/${currentTrack.queue_id}/complete`, { method: "POST" });
    currentTrack = null;
    fadeInBGM();
    fetchNextTrackForIntermission();
}

function skipCurrentTrack() {
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
        document.getElementById("intermissionSong").innerText = `${nextTrack.artist} - ${nextTrack.title}`;
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
    isIntermission = true;
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

// Floating Emoji Reactions
function spawnFloatingEmoji(emoji) {
    const container = document.getElementById("reactionContainer");
    if (!container) return;

    const el = document.createElement("div");
    el.className = "floating-reaction";
    el.innerText = emoji;

    // Randomize horizontal offset (right side of screen: 70% to 92%)
    const randomRight = 20 + Math.random() * 80;
    el.style.right = `${randomRight}px`;
    el.style.bottom = "80px";

    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function playSoundEffect(sound) {
    const audio = new Audio(`/static/sounds/${sound}.mp3`);
    audio.play().catch(() => {});
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

function fetchCurrentState() {
    fetch("/api/state")
        .then(res => res.json())
        .then(data => {
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
        ticker.innerText = `${nextSinger} ("${pending[0].title}")`;
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
                fetchCurrentState();
                showSyncToast("🔄 Stage Player Synced");
            }
        } else if (e.key === "[") {
            // Pitch down 1 semitone
            if (audioDSP) audioDSP.setPitch(audioDSP.currentPitch - 1);
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
        tickerText.innerText = `🌴 DJ LOUNGE MUSIC: ${track.artist} - ${track.title} • 🌺 OPEN MIC: Scan the QR code to queue your favorite song!`;
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
            fillerVideo.src = `/stream/filler/${track.id}`;
            fillerVideo.volume = 1.0;
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

