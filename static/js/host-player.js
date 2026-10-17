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
let isMutedViewer = false;
let configuredAudioSink = null;

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
    initYouTubeAPI();
    setupEventListeners();
    fetchCurrentState();
});

function initAudio() {
    const urlParams = new URLSearchParams(window.location.search);
    isMutedViewer = urlParams.has("mute") || urlParams.has("view_only");

    const videoEl = document.getElementById("localVideo");
    if (isMutedViewer) {
        if (videoEl) videoEl.muted = true;
        const bgm = document.getElementById("bgmPlayer");
        if (bgm) bgm.muted = true;
    } else {
        audioDSP = new AudioDSPPipeline();
    }

    applyAudioSink();

    document.body.addEventListener("click", () => {
        resumeStagePlayback();
    });
}

function resumeStagePlayback() {
    const videoEl = document.getElementById("localVideo");
    if (audioDSP && !isMutedViewer) {
        audioDSP.init(videoEl);
        audioDSP.resume();
    }
    const bgm = document.getElementById("bgmPlayer");
    if (bgm && bgm.paused && isIntermission && !isMutedViewer) {
        bgm.play().catch(() => {});
    }
    if (videoEl && videoEl.style.display !== "none") {
        if (currentTrack && currentTrack.elapsed_seconds && videoEl.currentTime < currentTrack.elapsed_seconds - 3) {
            videoEl.currentTime = currentTrack.elapsed_seconds;
        }
        videoEl.play().then(() => {
            const unmute = document.getElementById("unmuteOverlay");
            if (unmute) unmute.style.display = "none";
        }).catch(e => console.log(e));
    }
    if (ytPlayer && ytPlayer.playVideo) {
        ytPlayer.playVideo();
    }
    const unmute = document.getElementById("unmuteOverlay");
    if (unmute) unmute.style.display = "none";
}

function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("[Host WS] Connected as display:", myDisplayId, myDisplayName);
        ws.send(JSON.stringify({
            type: "stage_register",
            display_id: myDisplayId,
            name: myDisplayName,
            is_muted: isMutedViewer
        }));
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleSocketMessage(data);
        } catch (e) {
            console.error("[Host WS] Error parsing message:", e);
        }
    };

    ws.onclose = () => {
        console.log("[Host WS] Disconnected, retrying in 2s...");
        setTimeout(initWebSocket, 2000);
    };
}

function handleSocketMessage(data) {
    switch (data.type) {
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
        case "soundboard_play":
            playSoundEffect(data.sound);
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
                onStateChange: onYouTubeStateChange
            }
        });
    };
}

function onYouTubeStateChange(event) {
    if (event.data === YT.PlayerState.ENDED) {
        onSongFinished();
    }
}

function startTrackPlayback(track) {
    if (!track) return;
    currentTrack = track;
    stopIntermission();
    fadeOutBGM();

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

        if (isMutedViewer) {
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
                console.log("Local video autoplay blocked by browser policy:", e);
                if (unmuteOverlay) unmuteOverlay.style.display = "flex";
            });
        localVideo.onended = onSongFinished;
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
            if (isMutedViewer && ytPlayer.mute) ytPlayer.mute();
            ytPlayer.playVideo();
        }
    }
}

function onSongFinished() {
    if (!currentTrack) return;
    fetch(`/api/queue/${currentTrack.queue_id}/complete`, { method: "POST" });
    currentTrack = null;
    fadeInBGM();
    fetchNextTrackForIntermission();
}

function skipCurrentTrack() {
    const localVideo = document.getElementById("localVideo");
    localVideo.pause();
    if (ytPlayer && ytPlayer.stopVideo) ytPlayer.stopVideo();
    onSongFinished();
}

function pausePlayback() {
    const localVideo = document.getElementById("localVideo");
    if (localVideo.style.display !== "none") localVideo.pause();
    if (ytPlayer && ytPlayer.pauseVideo) ytPlayer.pauseVideo();
}

function resumePlayback() {
    const localVideo = document.getElementById("localVideo");
    if (localVideo.style.display !== "none") localVideo.play();
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
        if (upNextBadge) upNextBadge.style.display = "inline-block";
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
            if (nextTrack) {
                fetch(`/api/queue/${nextTrack.queue_id}/play`, { method: "POST" });
            }
        }
    }, 1000);
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

// BGM Fading
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

function fetchCurrentState() {
    fetch("/api/state")
        .then(res => res.json())
        .then(data => {
            if (data.playing) {
                startTrackPlayback(data.playing);
            } else if (data.next) {
                triggerIntermission(data.next, data.intermission_seconds || 20);
            } else {
                triggerIntermission(null, 0);
            }
            if (data.queue) updateNextTicker(data.queue);
        });
}

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
        const nextSinger = pending[0].duet_partner ? `${pending[0].singer_name} & ${pending[0].duet_partner}` : pending[0].singer_name;
        ticker.innerText = `${nextSinger} ("${pending[0].title}")`;
    } else {
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
            if (currentTrack) pausePlayback();
        } else if (e.key === "n" || e.key === "N") {
            // N for Next/Skip
            skipCurrentTrack();
        } else if (e.key === "[") {
            // Pitch down 1 semitone
            if (audioDSP) audioDSP.setPitch(audioDSP.currentPitch - 1);
        } else if (e.key === "m" || e.key === "M") {
            // M for Mute/Unmute
            toggleStageAudioMute();
        }
    });
}

function applyStageAudioMute(muted) {
    isMutedViewer = muted;
    const btn = document.getElementById("stageAudioToggleBtn");
    const videoEl = document.getElementById("localVideo");
    const bgm = document.getElementById("bgmPlayer");

    if (isMutedViewer) {
        if (videoEl) videoEl.muted = true;
        if (bgm) bgm.muted = true;
        if (ytPlayer && ytPlayer.mute) ytPlayer.mute();
        if (btn) {
            btn.innerText = "🔇 Muted";
            btn.style.color = "#ff5555";
            btn.style.borderColor = "rgba(255,85,85,0.4)";
        }
    } else {
        if (videoEl) videoEl.muted = false;
        if (bgm) bgm.muted = false;
        if (ytPlayer && ytPlayer.unMute) ytPlayer.unMute();
        if (btn) {
            btn.innerText = "🔊 Audio Active (Master)";
            btn.style.color = "var(--neon-cyan)";
            btn.style.borderColor = "var(--neon-cyan)";
        }
        if (audioDSP) audioDSP.resume();
    }
}

function toggleStageAudioMute() {
    applyStageAudioMute(!isMutedViewer);
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: "stage_audio_state",
            display_id: myDisplayId,
            is_muted: isMutedViewer
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

