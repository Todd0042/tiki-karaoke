/**
 * Tiki Karaoke Mobile Client (Smartphones)
 * Manages singer identity, debounced tiered search, key selection,
 * live WebSocket queue updates, crowd emoji reactions, and Host DJ controls.
 */

let currentSinger = null;
let isHost = false;
let ws = null;
let searchDebounceTimer = null;
let selectedTrackForQueue = null;
let selectedPitch = 0;

document.addEventListener("DOMContentLoaded", () => {
    checkIdentity();
    initWebSocket();
    setupNavigation();
    setupSearch();
    loadLiveQueue();
});

// Identity & Session
function checkIdentity() {
    let token = localStorage.getItem("tiki_token") || getCookie("tiki_token");
    let name = localStorage.getItem("tiki_name");
    let avatar = localStorage.getItem("tiki_avatar") || "🌺";

    if (!token || token === "undefined" || token === "null") {
        token = null;
        localStorage.removeItem("tiki_token");
        setCookie("tiki_token", "", -1);
    }
    if (!name || name === "undefined" || name === "null") {
        name = null;
        localStorage.removeItem("tiki_name");
    }

    if (token && name) {
        currentSinger = { token, name, avatar };
        updateSingerHeader();
        registerSingerWithServer();
    } else {
        currentSinger = null;
        updateSingerHeader();
        showOnboardingModal();
    }

    // Check if host
    fetch("/api/auth/role")
        .then(res => res.json())
        .then(data => {
            isHost = data.is_host;
            if (isHost) {
                document.getElementById("hostNavTab").style.display = "flex";
                loadHostSettings();
                loadHostDisplays();
                loadHostStorageStatus();
            }
        });
}

function registerSingerWithServer() {
    if (!currentSinger || !currentSinger.token || !currentSinger.name) return;
    fetch("/api/singer/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentSinger)
    })
    .then(async res => {
        if (!res.ok) {
            console.warn("[Identity] Registration conflict or error on boot. Re-opening onboarding.");
            localStorage.removeItem("tiki_token");
            localStorage.removeItem("tiki_name");
            currentSinger = null;
            updateSingerHeader();
            showOnboardingModal();
            return null;
        }
        return res.json();
    })
    .then(data => {
        if (data && data.token && data.name && data.name !== "undefined") {
            currentSinger = data;
            localStorage.setItem("tiki_token", data.token);
            localStorage.setItem("tiki_name", data.name);
            localStorage.setItem("tiki_avatar", data.avatar || "🌺");
            setCookie("tiki_token", data.token, 7);
            updateSingerHeader();
        }
    })
    .catch(err => {
        console.error("[Identity] Failed to register with server:", err);
    });
}

let avatarCheckDebounceTimer = null;

function showOnboardingModal() {
    const modal = document.getElementById("onboardingModal");
    const nameInput = document.getElementById("singerNameInput");
    const errEl = document.getElementById("nameErrorMsg");
    if (errEl) errEl.style.display = "none";

    let initialName = (currentSinger && currentSinger.name && currentSinger.name !== "undefined")
        ? currentSinger.name
        : (localStorage.getItem("tiki_name") && localStorage.getItem("tiki_name") !== "undefined" ? localStorage.getItem("tiki_name") : "");

    if (nameInput) nameInput.value = initialName;

    const currentAv = (currentSinger && currentSinger.avatar) || localStorage.getItem("tiki_avatar") || "🌺";
    document.querySelectorAll(".avatar-option").forEach(opt => {
        if (opt.dataset.avatar === currentAv) {
            opt.classList.add("selected");
        } else {
            opt.classList.remove("selected");
        }
    });

    if (initialName) {
        checkAvailableAvatarsForName(initialName);
    } else {
        document.querySelectorAll(".avatar-option").forEach(opt => opt.classList.remove("disabled-avatar"));
    }

    if (modal) modal.style.display = "flex";
}

function selectAvatar(el) {
    if (!el || el.classList.contains("disabled-avatar")) return;
    document.querySelectorAll(".avatar-option").forEach(opt => opt.classList.remove("selected"));
    el.classList.add("selected");
}

function onSingerNameInput(val) {
    clearTimeout(avatarCheckDebounceTimer);
    avatarCheckDebounceTimer = setTimeout(() => {
        checkAvailableAvatarsForName(val);
    }, 250);
}

function checkAvailableAvatarsForName(name) {
    const cleanName = (name || "").trim();
    const token = (currentSinger && currentSinger.token) || localStorage.getItem("tiki_token") || "";
    const errEl = document.getElementById("nameErrorMsg");
    const submitBtn = document.getElementById("onboardingSubmitBtn");

    if (!cleanName) {
        document.querySelectorAll(".avatar-option").forEach(opt => opt.classList.remove("disabled-avatar"));
        if (errEl) errEl.style.display = "none";
        if (submitBtn) submitBtn.disabled = false;
        return;
    }

    fetch(`/api/singer/available-avatars?name=${encodeURIComponent(cleanName)}&token=${encodeURIComponent(token)}`)
        .then(res => res.json())
        .then(data => {
            const taken = Array.isArray(data.taken_avatars) ? data.taken_avatars : [];
            const avatarEls = document.querySelectorAll(".avatar-option");
            let availableCount = 0;

            avatarEls.forEach(el => {
                const av = el.dataset.avatar;
                if (taken.includes(av)) {
                    el.classList.add("disabled-avatar");
                    el.classList.remove("selected");
                } else {
                    el.classList.remove("disabled-avatar");
                    availableCount++;
                }
            });

            // If selected avatar became disabled, select the first available avatar
            const hasSelected = document.querySelector(".avatar-option.selected:not(.disabled-avatar)");
            if (!hasSelected && availableCount > 0) {
                const firstAvailable = document.querySelector(".avatar-option:not(.disabled-avatar)");
                if (firstAvailable) firstAvailable.classList.add("selected");
            }

            if (availableCount === 0) {
                if (errEl) {
                    errEl.innerText = `⚠️ All tropical icons for "${cleanName}" are claimed! Please choose another nickname or add an initial.`;
                    errEl.style.display = "block";
                }
                if (submitBtn) submitBtn.disabled = true;
            } else {
                if (errEl) errEl.style.display = "none";
                if (submitBtn) submitBtn.disabled = false;
            }
        })
        .catch(err => {
            console.error("[AvatarCheck] Error checking avatars:", err);
        });
}

function completeOnboarding() {
    const nameInput = document.getElementById("singerNameInput").value.trim();
    const errEl = document.getElementById("nameErrorMsg");
    if (errEl) errEl.style.display = "none";

    if (!nameInput || nameInput.toLowerCase() === "undefined" || nameInput.toLowerCase() === "null") {
        if (errEl) {
            errEl.innerText = "Please enter a valid nickname!";
            errEl.style.display = "block";
        } else {
            alert("Please enter a valid nickname!");
        }
        return;
    }

    const selectedEl = document.querySelector(".avatar-option.selected:not(.disabled-avatar)");
    if (!selectedEl) {
        if (errEl) {
            errEl.innerText = "Please choose an available avatar icon!";
            errEl.style.display = "block";
        } else {
            alert("Please choose an available avatar icon!");
        }
        return;
    }
    const selectedAvatar = selectedEl.dataset.avatar || "🌺";
    
    let existingToken = (currentSinger && currentSinger.token && currentSinger.token !== "undefined" && currentSinger.token !== "null")
        ? currentSinger.token
        : (localStorage.getItem("tiki_token") && localStorage.getItem("tiki_token") !== "undefined" ? localStorage.getItem("tiki_token") : null);

    if (!existingToken) {
        existingToken = "usr_" + Math.random().toString(36).substring(2, 12);
    }

    const candidate = {
        token: existingToken,
        name: nameInput,
        avatar: selectedAvatar
    };

    fetch("/api/singer/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(candidate)
    })
    .then(async res => {
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "That avatar is already taken for this name! Please choose another.");
        }
        return res.json();
    })
    .then(data => {
        if (!data || !data.token || !data.name || data.name === "undefined") {
            throw new Error("Invalid singer registration received from server.");
        }
        currentSinger = data;
        localStorage.setItem("tiki_token", data.token);
        localStorage.setItem("tiki_name", data.name);
        localStorage.setItem("tiki_avatar", data.avatar);
        setCookie("tiki_token", data.token, 7);

        document.getElementById("onboardingModal").style.display = "none";
        updateSingerHeader();
        showToast(`Welcome to the party, ${data.name}! ${data.avatar}`);
    })
    .catch(err => {
        if (errEl) {
            errEl.innerText = `⚠️ ${err.message}`;
            errEl.style.display = "block";
        } else {
            alert(err.message);
        }
        checkAvailableAvatarsForName(nameInput);
    });
}

function updateSingerHeader() {
    const badge = document.getElementById("headerSingerBadge");
    if (!badge) return;
    if (currentSinger && currentSinger.name && currentSinger.name !== "undefined") {
        badge.innerText = `${currentSinger.avatar || '🌺'} ${currentSinger.name}`;
    } else {
        badge.innerText = "🌺 Tap to Set Nickname";
    }
}

// WebSocket Live Updates
function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === "queue_updated" || data.type === "play_track") {
                loadLiveQueue();
                if (isHost) {
                    loadHostSingerRoster();
                }
            } else if (data.type === "displays_updated") {
                if (isHost) {
                    loadHostDisplays();
                }
            } else if (data.type === "library_updated") {
                loadLibraryTracks();
                if (isHost) {
                    loadHostStorageStatus();
                }
            }
        } catch (e) {
            console.error("[WS] Error:", e);
        }
    };

    ws.onclose = () => {
        setTimeout(initWebSocket, 2000);
    };
}

// Navigation Tabs
function setupNavigation() {
    document.querySelectorAll(".nav-tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".nav-tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-view").forEach(v => v.style.display = "none");

            btn.classList.add("active");
            const target = btn.dataset.tab;
            document.getElementById(target).style.display = "block";

            if (target === "libraryTab") loadLibraryTracks();
            if (target === "queueTab") loadLiveQueue();
            if (target === "historyTab") loadHistory();
            if (target === "hostTab") {
                loadHostSingerRoster();
                loadHostDisplays();
                loadHostStorageStatus();
            }
        });
    });
}

// Tiered Search & Queue
function setupSearch() {
    const input = document.getElementById("searchInput");
    input.addEventListener("input", () => {
        clearTimeout(searchDebounceTimer);
        const query = input.value.trim();
        if (query.length < 2) {
            document.getElementById("searchResults").innerHTML = "";
            return;
        }

        searchDebounceTimer = setTimeout(() => {
            performSearch(query);
        }, 320);
    });
}

function performSearch(query, forceOnline = false) {
    const resultsContainer = document.getElementById("searchResults");
    resultsContainer.innerHTML = '<div style="text-align: center; padding: 25px; color: var(--neon-cyan);">🍹 Searching Tiki Library & YouTube...</div>';

    fetch(`/api/search?q=${encodeURIComponent(query)}&force_online=${forceOnline}`)
        .then(res => res.json())
        .then(data => {
            renderSearchResults(data, query);
        })
        .catch(() => {
            resultsContainer.innerHTML = '<div style="text-align: center; color: #ff5555; padding: 20px;">Search error. Check connection.</div>';
        });
}

function renderSearchResults(data, query) {
    const container = document.getElementById("searchResults");
    container.innerHTML = "";

    const localList = data.local_results || [];
    const ytList = data.youtube_results || [];

    if (localList.length === 0 && ytList.length === 0) {
        container.innerHTML = `<div style="text-align:center; padding: 30px; color: var(--text-muted);">
            No songs found for "${query}". Try another title or artist! 🌺
        </div>`;
        return;
    }

    // 1. Local Tracks (Tier 1)
    if (localList.length > 0) {
        const localSection = document.createElement("div");
        localSection.innerHTML = '<div style="font-size: 0.8rem; color: var(--neon-lime); font-weight: 700; margin: 15px 0 8px 4px;">⚡ FROM LOCAL TIKI LIBRARY (Instant Play)</div>';
        
        localList.forEach(track => {
            const card = document.createElement("div");
            card.className = "tiki-card";
            card.style.padding = "14px";
            card.style.marginBottom = "10px";
            card.style.display = "flex";
            card.style.justifyContent = "space-between";
            card.style.alignItems = "center";

            card.innerHTML = `
                <div style="flex: 1; padding-right: 10px;">
                    <div style="font-weight: 700; font-size: 1.05rem;">${escapeHtml(track.title)}</div>
                    <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(track.artist)}</div>
                    <span class="badge-local" style="margin-top: 4px;">⚡ Downloaded</span>
                </div>
                <button onclick="openQueueModal(${track.id}, '${escapeHtml(track.title)}', '${escapeHtml(track.artist)}', '${track.video_id}')" class="btn-neon-pink" style="padding: 8px 14px; font-size: 0.85rem;">
                    + Queue
                </button>
            `;
            localSection.appendChild(card);
        });
        container.appendChild(localSection);

        // Optional YouTube button if user wants more versions
        const searchMoreBtn = document.createElement("button");
        searchMoreBtn.className = "btn-ghost";
        searchMoreBtn.style.width = "100%";
        searchMoreBtn.style.margin = "10px 0 20px 0";
        searchMoreBtn.innerText = "🌐 Search YouTube for more versions...";
        searchMoreBtn.onclick = () => performSearch(query, true);
        container.appendChild(searchMoreBtn);
    }

    // 2. YouTube Results (Tier 2)
    if (ytList.length > 0) {
        const ytSection = document.createElement("div");
        ytSection.innerHTML = '<div style="font-size: 0.8rem; color: var(--neon-pink); font-weight: 700; margin: 15px 0 8px 4px;">🌐 YOUTUBE KARAOKE RESULTS</div>';

        ytList.forEach(group => {
            const card = document.createElement("div");
            card.className = "tiki-card-pink";
            card.style.padding = "14px";
            card.style.marginBottom = "12px";

            const bestVersion = group.versions[0];
            const hasMultiple = group.versions.length > 1;

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div style="flex: 1; padding-right: 10px;">
                        <div style="font-weight: 700; font-size: 1.05rem;">${escapeHtml(group.title)}</div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(group.artist)}</div>
                        <div style="font-size: 0.75rem; color: var(--neon-gold); margin-top: 4px;">
                            ${escapeHtml(bestVersion.channel || "YouTube")} • ${formatDuration(bestVersion.duration)}
                            ${bestVersion.attributes.length ? `• ${bestVersion.attributes.join(', ')}` : ''}
                        </div>
                    </div>
                    <button onclick="openQueueModal(null, '${escapeHtml(group.title)}', '${escapeHtml(group.artist)}', '${bestVersion.video_id}')" class="btn-neon-cyan" style="padding: 8px 14px; font-size: 0.85rem;">
                        + Queue
                    </button>
                </div>
            `;
            ytSection.appendChild(card);
        });
        container.appendChild(ytSection);
    }
}

// Queue Modal & Pitch Selector
function openQueueModal(trackId, title, artist, videoId) {
    selectedTrackForQueue = { trackId, title, artist, videoId };
    selectedPitch = 0;

    document.getElementById("modalSongTitle").innerText = `${artist} - ${title}`;
    resetPitchButtons();
    document.getElementById("duetInput").value = "";
    document.getElementById("queueModal").style.display = "flex";
}

function closeQueueModal() {
    document.getElementById("queueModal").style.display = "none";
}

function selectPitch(semitones) {
    selectedPitch = semitones;
    document.querySelectorAll(".pitch-btn").forEach(btn => {
        if (parseInt(btn.dataset.pitch) === semitones) {
            btn.classList.add("active");
            btn.style.borderColor = "var(--neon-pink)";
            btn.style.color = "var(--neon-pink)";
        } else {
            btn.classList.remove("active");
            btn.style.borderColor = "rgba(255,255,255,0.2)";
            btn.style.color = "var(--text-main)";
        }
    });
}

function resetPitchButtons() {
    selectPitch(0);
}

function submitQueue() {
    if (!selectedTrackForQueue) return;
    if (!currentSinger || !currentSinger.token || currentSinger.token === "undefined" || !currentSinger.name || currentSinger.name === "undefined") {
        showToast("⚠️ Please choose your nickname first!");
        showOnboardingModal();
        return;
    }

    const duet = document.getElementById("duetInput").value.trim();
    const payload = {
        track_id: selectedTrackForQueue.trackId,
        video_id: selectedTrackForQueue.videoId,
        title: selectedTrackForQueue.title,
        artist: selectedTrackForQueue.artist,
        singer_token: currentSinger.token,
        duet_partner: duet,
        pitch_shift: selectedPitch
    };

    fetch("/api/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(async res => {
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "Failed to queue song.");
        }
        return res.json();
    })
    .then(data => {
        closeQueueModal();
        showToast("🌺 Song Queued! Check Live Queue for your spot.");
        loadLiveQueue();
        // Switch to Queue Tab
        document.querySelector('[data-tab="queueTab"]').click();
    })
    .catch((err) => {
        alert(err.message || "Failed to queue song.");
    });
}

// Live Queue View
function loadLiveQueue() {
    fetch("/api/queue")
        .then(res => res.json())
        .then(queue => {
            renderQueue(queue);
        });
}

function renderQueue(queue) {
    const container = document.getElementById("queueListContainer");
    container.innerHTML = "";

    if (queue.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                <div style="font-size: 3rem; margin-bottom: 10px;">🌴</div>
                <div class="font-tiki neon-cyan" style="font-size: 1.4rem;">The Stage is Open!</div>
                <p style="margin-top: 8px;">Be the first rockstar to request a song tonight!</p>
            </div>
        `;
        return;
    }

    queue.forEach((item, index) => {
        const isPlaying = item.status === "playing";
        const isMine = currentSinger && item.singer_id === currentSinger.id;

        const card = document.createElement("div");
        card.className = isPlaying ? "tiki-card-pink pulse-glow" : "tiki-card";
        card.style.padding = "16px";
        card.style.marginBottom = "12px";

        const pitchText = item.pitch_shift !== 0 ? `Key: ${item.pitch_shift > 0 ? '+' : ''}${item.pitch_shift}` : "Standard Key";

        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="font-size: 1.8rem;">${item.singer_avatar || "🌺"}</div>
                    <div>
                        <div style="font-size: 0.8rem; font-weight: 700; color: ${isPlaying ? 'var(--neon-pink)' : 'var(--neon-cyan)'};">
                            ${isPlaying ? 'NOW SINGING' : `#${index} IN LINE`}
                        </div>
                        <div style="font-weight: 700; font-size: 1.1rem;">${escapeHtml(item.singer_name)} ${item.duet_partner ? `& ${escapeHtml(item.duet_partner)}` : ''}</div>
                        <div style="color: var(--text-muted); font-size: 0.9rem;">${escapeHtml(item.artist)} - ${escapeHtml(item.title)}</div>
                        <span class="badge-key" style="margin-top: 4px; display: inline-block;">${pitchText}</span>
                    </div>
                </div>

                ${(isMine || isHost) && !isPlaying ? `
                    <button onclick="cancelQueuedSong(${item.queue_id})" class="btn-ghost" style="padding: 6px 10px; font-size: 0.8rem; color: #ff5555;">
                        ✕
                    </button>
                ` : ''}
            </div>
        `;
        container.appendChild(card);
    });
}

function cancelQueuedSong(queueId) {
    if (!confirm("Remove this song from queue?")) return;
    fetch(`/api/queue/${queueId}?token=${currentSinger?.token || ''}`, { method: "DELETE" })
        .then(() => loadLiveQueue());
}

// Crowd Emoji Reactions
function sendReaction(emoji) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "emoji_reaction", emoji: emoji }));
    }
    // Mobile tactile feedback
    showToast(`Sent ${emoji} to stage screen!`);
}

// History & Surprise Me
function loadHistory() {
    if (!currentSinger) return;
    fetch(`/api/singer/${currentSinger.token}/history`)
        .then(res => res.json())
        .then(history => {
            const list = document.getElementById("historyList");
            list.innerHTML = "";
            if (history.length === 0) {
                list.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">No songs sung yet tonight. Step up to the mic! 🎤</div>';
                return;
            }
            history.forEach(item => {
                const card = document.createElement("div");
                card.className = "tiki-card";
                card.style.padding = "12px 16px";
                card.style.marginBottom = "10px";
                card.style.display = "flex";
                card.style.justifyContent = "space-between";
                card.style.alignItems = "center";
                card.innerHTML = `
                    <div>
                        <div style="font-weight: 700;">${escapeHtml(item.title)}</div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(item.artist)}</div>
                    </div>
                    <button onclick="openQueueModal(null, '${escapeHtml(item.title)}', '${escapeHtml(item.artist)}', '${item.video_id}')" class="btn-ghost" style="font-size: 0.8rem;">
                        Sing Again
                    </button>
                `;
                list.appendChild(card);
            });
        });
}

function spinTikiWheel() {
    fetch("/api/surprise")
        .then(res => res.json())
        .then(track => {
            if (track) {
                openQueueModal(track.id, track.title, track.artist, track.video_id);
            } else {
                alert("No tracks in local library yet to spin! Search for a song to build the party catalog!");
            }
        });
}

// Host DJ Controls
function hostSkip() {
    fetch("/api/host/skip", { method: "POST" });
}

function hostPauseResume() {
    fetch("/api/host/toggle-pause", { method: "POST" });
}

function hostStartNow() {
    fetch("/api/host/start-now", { method: "POST" });
}

function hostAdd15s() {
    fetch("/api/host/add-time", { method: "POST" });
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

function hostDuckBGM(duck) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "volume_change", volume: duck ? 0.05 : 1.0 }));
    }
}

function loadHostSettings() {
    fetch("/api/settings")
        .then(res => res.json())
        .then(settings => {
            const ttsCheck = document.getElementById("ttsToggle");
            if (ttsCheck) ttsCheck.checked = (settings.tts_enabled === "true");
        });
}

// Helpers
function showToast(msg) {
    const toast = document.createElement("div");
    toast.style.position = "fixed";
    toast.style.bottom = "80px";
    toast.style.left = "50%";
    toast.style.transform = "translateX(-50%)";
    toast.style.background = "rgba(16, 28, 45, 0.95)";
    toast.style.border = "1px solid var(--neon-cyan)";
    toast.style.color = "#fff";
    toast.style.padding = "10px 18px";
    toast.style.borderRadius = "20px";
    toast.style.fontSize = "0.9rem";
    toast.style.zIndex = "1000";
    toast.style.boxShadow = "0 0 15px rgba(0, 240, 255, 0.5)";
    toast.innerText = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 2500);
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

function setCookie(name, val, days) {
    const d = new Date();
    d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
    document.cookie = `${name}=${val};expires=${d.toUTCString()};path=/`;
}

function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? match[2] : null;
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
                        <button onclick="hostRemoveAllSingerSongs(${s.singer_id}, '${escapeHtml(s.singer_name)}', ${pendingSongs.length})" 
                                class="btn-ghost" 
                                style="margin-top: 10px; width: 100%; padding: 8px; font-size: 0.8rem; font-weight: 600; color: #ff5555; border-color: rgba(255,85,85,0.35); background: rgba(255,85,85,0.08); border-radius: 8px; cursor: pointer;">
                            🗑️ Remove All ${pendingSongs.length} Queued Song${pendingSongs.length > 1 ? 's' : ''} for ${escapeHtml(s.singer_name)}
                        </button>
                    `;
                }

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 1.3rem;">${s.singer_avatar || '🌺'}</span>
                            <span style="font-weight: 700; font-size: 0.95rem; color: #fff;">${escapeHtml(s.singer_name)}</span>
                        </div>
                        <span style="font-size: 0.75rem; color: ${totalActive > 0 ? 'var(--neon-gold)' : 'var(--text-muted)'}; font-weight: 600;">
                            ${totalActive} song${totalActive === 1 ? '' : 's'} in queue
                        </span>
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

function hostRemoveAllSingerSongs(singerId, singerName, count) {
    if (!confirm(`Are you sure you want to remove all ${count} queued song(s) for ${singerName}?`)) {
        return;
    }
    fetch(`/api/host/singer/${singerId}/remove-all`, { method: "POST" })
        .then(res => res.json())
        .then(data => {
            showToast(`Removed all ${data.deleted_count || count} song(s) for ${singerName}`);
            loadHostSingerRoster();
            loadLiveQueue();
        })
        .catch(err => {
            alert("Error removing songs: " + err.message);
        });
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
    clearTimeout(libSearchDebounceTimer);
    libSearchDebounceTimer = setTimeout(() => {
        const input = document.getElementById("libSearchInput");
        currentLibSearch = input ? input.value.trim() : "";
        currentLibPage = 1;
        loadLibraryTracks();
    }, 280);
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

    const url = `/api/library?page=${currentLibPage}&limit=25&sort=${encodeURIComponent(currentLibSort)}&artist=${encodeURIComponent(currentLibArtist)}&q=${encodeURIComponent(currentLibSearch)}`;

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
                container.innerHTML = `
                    <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                        <div style="font-size: 2.2rem; margin-bottom: 8px;">🌴</div>
                        <p style="font-size: 0.95rem; font-weight: 600; color: #fff;">No downloaded songs found</p>
                        <p style="font-size: 0.82rem;">Search YouTube above to download and add new tracks to this library!</p>
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
                            <span style="color: var(--neon-gold);">
                                🕒 ${lastPlayedText}
                            </span>
                            <span style="color: var(--text-muted);">
                                • ${formatDuration(track.duration)}
                            </span>
                        </div>
                    </div>
                    <button onclick="openQueueModal(${track.id}, '${escapeHtml(track.title).replace(/'/g, "\\'")}', '${escapeHtml(track.artist).replace(/'/g, "\\'")}', '${track.video_id}')" 
                            class="btn-neon-pink" style="padding: 9px 14px; font-size: 0.85rem; white-space: nowrap; flex-shrink: 0;">
                        + Queue
                    </button>
                `;
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
let selectedCleanupTrackIds = new Set();
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
        })
        .catch(err => {
            console.error("[Storage] Failed to load status:", err);
        });
}

function openCleanupModal() {
    const modal = document.getElementById("cleanupModal");
    if (modal) modal.style.display = "flex";
    selectedCleanupTrackIds.clear();
    loadCleanupCandidates();
}

function closeCleanupModal() {
    const modal = document.getElementById("cleanupModal");
    if (modal) modal.style.display = "none";
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

    fetch(`/api/storage/cleanup-candidates?sort_by=${encodeURIComponent(currentCleanupSort)}&limit=60`)
        .then(res => res.json())
        .then(data => {
            cleanupCandidatesList = Array.isArray(data.candidates) ? data.candidates : [];
            list.innerHTML = "";

            if (cleanupCandidatesList.length === 0) {
                list.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">No downloaded video files found to prune.</div>`;
                updateCleanupStats();
                return;
            }

            cleanupCandidatesList.forEach(item => {
                const row = document.createElement("label");
                row.style.display = "flex";
                row.style.alignItems = "center";
                row.style.gap = "10px";
                row.style.padding = "10px 12px";
                row.style.background = "rgba(8, 14, 24, 0.8)";
                row.style.border = "1px solid rgba(255,255,255,0.1)";
                row.style.borderRadius = "10px";
                row.style.cursor = "pointer";

                const isChecked = selectedCleanupTrackIds.has(item.id);

                row.innerHTML = `
                    <input type="checkbox" value="${item.id}" ${isChecked ? 'checked' : ''} onchange="toggleCleanupTrack(${item.id}, this.checked)" style="transform: scale(1.3); cursor: pointer;">
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #fff;">
                            ${escapeHtml(item.title)}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.78rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.artist)}
                        </div>
                    </div>
                    <div style="text-align: right; flex-shrink: 0; font-size: 0.75rem;">
                        <div style="color: var(--neon-gold); font-weight: 700;">${item.size_mb} MB</div>
                        <div style="color: var(--text-muted);">🎤 ${item.play_count} • ${formatRelativeTime(item.last_played)}</div>
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

function toggleCleanupTrack(trackId, isChecked) {
    if (isChecked) {
        selectedCleanupTrackIds.add(trackId);
    } else {
        selectedCleanupTrackIds.delete(trackId);
    }
    updateCleanupStats();
}

function selectTopNCleanup(n) {
    cleanupCandidatesList.slice(0, n).forEach(item => {
        selectedCleanupTrackIds.add(item.id);
    });
    // Check checkboxes
    document.querySelectorAll('#cleanupCandidateList input[type="checkbox"]').forEach(cb => {
        if (selectedCleanupTrackIds.has(parseInt(cb.value))) {
            cb.checked = true;
        }
    });
    updateCleanupStats();
}

function toggleAllCleanup() {
    if (selectedCleanupTrackIds.size === cleanupCandidatesList.length) {
        selectedCleanupTrackIds.clear();
    } else {
        cleanupCandidatesList.forEach(item => selectedCleanupTrackIds.add(item.id));
    }
    document.querySelectorAll('#cleanupCandidateList input[type="checkbox"]').forEach(cb => {
        cb.checked = selectedCleanupTrackIds.has(parseInt(cb.value));
    });
    updateCleanupStats();
}

function updateCleanupStats() {
    const statsEl = document.getElementById("cleanupSelectedStats");
    const btn = document.getElementById("pruneExecuteBtn");

    let totalMb = 0;
    cleanupCandidatesList.forEach(item => {
        if (selectedCleanupTrackIds.has(item.id)) {
            totalMb += item.size_mb || 0;
        }
    });
    totalMb = Math.round(totalMb * 10) / 10;

    const count = selectedCleanupTrackIds.size;
    if (statsEl) {
        statsEl.innerText = `Selected: ${count} songs (${totalMb} MB)`;
    }

    if (btn) {
        btn.disabled = count === 0;
        btn.innerText = count > 0 ? `🗑️ Prune ${count} Files (${totalMb} MB)` : `🗑️ Prune Selected Files`;
    }
}

function executePrune() {
    if (selectedCleanupTrackIds.size === 0) return;
    const count = selectedCleanupTrackIds.size;
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
        body: JSON.stringify({ track_ids: Array.from(selectedCleanupTrackIds) })
    })
    .then(res => {
        if (!res.ok) throw new Error("Unauthorized or server error");
        return res.json();
    })
    .then(data => {
        showToast(`✅ Successfully pruned ${data.pruned_count} video files!`);
        selectedCleanupTrackIds.clear();
        closeCleanupModal();
        loadHostStorageStatus();
        loadLibraryTracks();
    })
    .catch(err => {
        alert("Error pruning files: " + err.message);
        if (btn) {
            btn.disabled = false;
            updateCleanupStats();
        }
    });
}


