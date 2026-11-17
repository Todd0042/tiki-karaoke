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
let activeDuplicateInfo = null;
let lastAlertedQueueId = null;
let currentAvSyncOffset = 0;
let lastQueuedSongTime = 0;
let lastQueuedSongId = null;
let isSingAlongMode = false;

document.addEventListener("DOMContentLoaded", () => {
    initSingAlongMode();
    checkIdentity();
    initWebSocket();
    setupNavigation();
    setupSearch();
    loadLiveQueue();
    loadLibraryTracks();
});

function initSingAlongMode() {
    const cookieVal = getCookie("tiki_sing_along");
    if (cookieVal !== null) {
        isSingAlongMode = (cookieVal === "1" || cookieVal === "true");
    } else {
        const localVal = localStorage.getItem("tiki_sing_along");
        isSingAlongMode = (localVal === "1" || localVal === "true");
    }
    syncSingAlongToggleUI();
}

function syncSingAlongToggleUI() {
    const tSearch = document.getElementById("singAlongToggleSearch");
    if (tSearch) tSearch.checked = isSingAlongMode;
    const tLib = document.getElementById("singAlongToggleLibrary");
    if (tLib) tLib.checked = isSingAlongMode;
}

function onSingAlongToggleChange(checked) {
    isSingAlongMode = !!checked;
    setCookie("tiki_sing_along", isSingAlongMode ? "1" : "0", 365);
    localStorage.setItem("tiki_sing_along", isSingAlongMode ? "1" : "0");
    syncSingAlongToggleUI();

    const searchInput = document.getElementById("searchInput");
    if (searchInput && searchInput.value.trim().length > 0) {
        performSearch(searchInput.value.trim());
    }

    if (typeof loadLibraryTracks === "function") {
        currentLibPage = 1;
        loadLibraryTracks();
    }
}

// Identity & Session
function checkIdentity() {
    let token = localStorage.getItem("tiki_token") || getCookie("tiki_token");
    let name = localStorage.getItem("tiki_name") || getCookie("tiki_name");
    let avatar = localStorage.getItem("tiki_avatar") || getCookie("tiki_avatar") || "🌺";
    const isCheckedOut = (localStorage.getItem("tiki_checked_out") === "1" || getCookie("tiki_checked_out") === "1");

    if (!token || token === "undefined" || token === "null") {
        token = null;
        localStorage.removeItem("tiki_token");
        setCookie("tiki_token", "", -1);
    }
    if (!name || name === "undefined" || name === "null") {
        name = null;
        localStorage.removeItem("tiki_name");
        setCookie("tiki_name", "", -1);
    }

    const urlP = new URLSearchParams(window.location.search);
    const isHostParam = urlP.get("tab") === "host" || urlP.get("tab") === "hostTab";

    if (window.TIKI_REQUIRE_ROOM_CODE && !window.TIKI_ROOM_VERIFIED && !isHostParam) {
        const roomModal = document.getElementById("roomCodeModal");
        if (roomModal) roomModal.style.display = "flex";
        checkRole();
        return;
    }

    if (isCheckedOut && !isHostParam) {
        currentSinger = null;
        updateSingerHeader();
        showLeftPartyModal(name || "Singer", avatar || "🌺");
        checkRole();
        return;
    }

    if (token && name) {
        currentSinger = { token, name, avatar };
        updateSingerHeader();
        registerSingerWithServer();
    } else if (token) {
        // Token was preserved in cookie or storage, restore profile from server!
        fetch(`/api/singer/${token}/profile`)
            .then(res => res.ok ? res.json() : null)
            .then(prof => {
                if (prof && prof.name) {
                    currentSinger = prof;
                    localStorage.setItem("tiki_token", prof.token);
                    localStorage.setItem("tiki_name", prof.name);
                    localStorage.setItem("tiki_avatar", prof.avatar || "🌺");
                    setCookie("tiki_token", prof.token, 365);
                    setCookie("tiki_name", prof.name, 365);
                    setCookie("tiki_avatar", prof.avatar || "🌺", 365);
                    updateSingerHeader();
                    registerSingerWithServer();
                } else {
                    currentSinger = null;
                    updateSingerHeader();
                    showOnboardingModal();
                }
            })
            .catch(() => {
                currentSinger = null;
                updateSingerHeader();
                showOnboardingModal();
            });
    } else {
        currentSinger = null;
        updateSingerHeader();
        if (!isHostParam) {
            showOnboardingModal();
        }
    }

    // Check if host
    checkRole();
}

function checkRole() {
    return fetch("/api/auth/role")
        .then(res => res.json())
        .then(data => {
            isHost = !!data.is_host;
            const hostTab = document.getElementById("hostNavTab");
            if (hostTab) {
                hostTab.style.display = isHost ? "flex" : "none";
            }
            const hostRow = document.getElementById("hostNavRow");
            if (hostRow) {
                hostRow.style.display = isHost ? "flex" : "none";
            }
            document.body.classList.toggle("host-mode-active", isHost);
            if (isHost) {
                const roomModal = document.getElementById("roomCodeModal");
                if (roomModal) roomModal.style.display = "none";
                const onboarding = document.getElementById("onboardingModal");
                if (onboarding) onboarding.style.display = "none";
                const leftModal = document.getElementById("leftPartyModal");
                if (leftModal) leftModal.style.display = "none";
                loadHostSettings();
                loadHostDisplays();
                loadHostStorageStatus();
                checkYtdlpStatus();
                loadVenueAvSync();
                checkHostHealth();
                loadHostStrictMode();
                loadHostNetworkStatus();

                const urlParams = new URLSearchParams(window.location.search);
                const reqTab = urlParams.get("tab");
                if (reqTab === "host" || reqTab === "hostTab") {
                    switchTab("hostTab");
                }
            }
            return isHost;
        })
        .catch(err => {
            console.error("[Auth] Role check failed:", err);
            showToast("⚠️ Could not reach karaoke server to verify session.", true);
            return false;
        });
}

function openHostLoginModal() {
    if (isHost) {
        showToast("👑 You are already authorized as Host DJ!");
        switchTab("host");
        return;
    }
    const modal = document.getElementById("hostLoginModal");
    const pinInput = document.getElementById("hostPinInput");
    const err = document.getElementById("hostPinErrorMsg");
    if (err) err.style.display = "none";
    if (pinInput) pinInput.value = "";
    if (modal) {
        modal.style.display = "flex";
        setTimeout(() => pinInput && pinInput.focus(), 150);
    }
}

function closeHostLoginModal() {
    const modal = document.getElementById("hostLoginModal");
    if (modal) modal.style.display = "none";
}

async function submitHostPinLogin() {
    const pinInput = document.getElementById("hostPinInput");
    const err = document.getElementById("hostPinErrorMsg");
    const btn = document.getElementById("hostPinSubmitBtn");
    const pin = (pinInput ? pinInput.value : "").trim();

    if (!pin) {
        if (err) {
            err.innerText = "Please enter the PIN.";
            err.style.display = "block";
        }
        return;
    }

    if (btn) btn.disabled = true;
    if (err) err.style.display = "none";

    try {
        const res = await fetch("/api/auth/pin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pin: pin })
        });

        if (res.ok) {
            closeHostLoginModal();
            showToast("👑 Host DJ privileges unlocked!");
            await checkRole();
            reconnectWebSocket();
            switchTab("host");
        } else {
            const data = await res.json().catch(() => ({}));
            const msg = data.detail || (res.status === 429 ? "Too many failed attempts. Please wait 1 minute." : "Invalid Host PIN.");
            if (err) {
                err.innerText = msg;
                err.style.display = "block";
            }
        }
    } catch (e) {
        if (err) {
            err.innerText = "Connection error. Please try again.";
            err.style.display = "block";
        }
    } finally {
        if (btn) btn.disabled = false;
    }
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
            setCookie("tiki_token", data.token, 365);
            setCookie("tiki_name", data.name, 365);
            setCookie("tiki_avatar", data.avatar || "🌺", 365);
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

    const leaveBtnContainer = document.getElementById("singerLeavePartyContainer");
    if (leaveBtnContainer) {
        leaveBtnContainer.style.display = (currentSinger && currentSinger.name) ? "block" : "none";
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
            if (errEl) {
                errEl.innerText = "⚠️ Could not check nickname availability. Check connection.";
                errEl.style.display = "block";
            }
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
        : ((localStorage.getItem("tiki_token") && localStorage.getItem("tiki_token") !== "undefined")
            ? localStorage.getItem("tiki_token")
            : (getCookie("tiki_token") && getCookie("tiki_token") !== "undefined" ? getCookie("tiki_token") : null));

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
        localStorage.removeItem("tiki_checked_out");
        setCookie("tiki_checked_out", "", -1);
        localStorage.setItem("tiki_token", data.token);
        localStorage.setItem("tiki_name", data.name);
        localStorage.setItem("tiki_avatar", data.avatar || "🌺");
        setCookie("tiki_token", data.token, 365);
        setCookie("tiki_name", data.name, 365);
        setCookie("tiki_avatar", data.avatar || "🌺", 365);

        const obModal = document.getElementById("onboardingModal");
        if (obModal) obModal.style.display = "none";
        const leftModal = document.getElementById("leftPartyModal");
        if (leftModal) leftModal.style.display = "none";
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
        const isOnBreak = currentSinger.status === "on_break";
        badge.innerHTML = `${currentSinger.avatar || '🌺'} ${escapeHtml(currentSinger.name)} ${isOnBreak ? '<span style="color:var(--neon-gold); font-size:0.75rem; margin-left: 4px;">(Paused ⏸️)</span>' : ''}`;
    } else {
        badge.innerText = "🌺 Tap to Set Nickname";
    }
    renderSingerStatusBanner();
}

function renderSingerStatusBanner() {
    const banner = document.getElementById("singerSelfStatusBanner");
    if (!banner) return;
    if (!currentSinger || !currentSinger.token) {
        banner.innerHTML = "";
        return;
    }
    const isOnBreak = currentSinger.status === "on_break";
    if (isOnBreak) {
        banner.innerHTML = `
            <div style="background: rgba(255, 183, 3, 0.12); border: 1px solid var(--neon-gold); border-radius: 12px; padding: 12px 14px; margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; gap: 10px; box-shadow: 0 0 15px rgba(255, 183, 3, 0.15);">
                <div>
                    <div style="font-weight: 700; font-size: 0.9rem; color: var(--neon-gold); display: flex; align-items: center; gap: 6px;">
                        <span>⏸️</span> <span>Your Stage Turn is Paused</span>
                    </div>
                    <div style="font-size: 0.74rem; color: #cbd5e1; margin-top: 2px;">Your songs stay in queue without losing your spot. Stage will skip to active singers until you tap ready!</div>
                </div>
                <button onclick="toggleSingerSelfStatus('active')" class="btn-ghost" style="padding: 8px 14px; font-size: 0.82rem; font-weight: 700; background: rgba(57, 255, 20, 0.15); color: var(--neon-lime); border-color: var(--neon-lime); border-radius: 8px; white-space: nowrap; cursor: pointer;">
                    🎤 I'm Ready!
                </button>
            </div>
        `;
    } else {
        banner.innerHTML = `
            <div style="display: flex; justify-content: flex-end; margin-bottom: 10px;">
                <button onclick="toggleSingerSelfStatus('on_break')" class="btn-ghost" style="padding: 4px 10px; font-size: 0.75rem; border-color: rgba(255,183,3,0.4); color: var(--neon-gold); border-radius: 8px; display: inline-flex; align-items: center; gap: 5px; cursor: pointer;">
                    <span>🍹</span> <span>Step Away (Pause My Turn)</span>
                </button>
            </div>
        `;
    }
}

async function toggleSingerSelfStatus(forcedStatus = null) {
    if (!currentSinger || !currentSinger.token) {
        showOnboardingModal();
        return;
    }
    const currentStatus = currentSinger.status || "active";
    const nextStatus = forcedStatus || (currentStatus === "active" ? "on_break" : "active");
    try {
        const res = await fetch(`/api/singer/${currentSinger.token}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: nextStatus })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to update your status");
        currentSinger.status = nextStatus;
        updateSingerHeader();
        loadLiveQueue();
        if (nextStatus === "on_break") {
            showToast("🍹 You're on break! Your turn in queue is paused until you're back.");
        } else {
            showToast("🎤 Welcome back! You are active in the rotation.");
        }
    } catch (e) {
        showToast(`⚠️ ${e.message}`);
    }
}

// WebSocket Live Updates
let wsPingInterval = null;
let wsReconnectTimeout = null;

function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    if (wsPingInterval) {
        clearInterval(wsPingInterval);
        wsPingInterval = null;
    }
    if (wsReconnectTimeout) {
        clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = null;
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
        const pill = document.getElementById("reconnectingPill");
        if (pill) pill.style.display = "none";

        // Keepalive heartbeat every 10s to prevent router/tunnel drops on mobile
        wsPingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "ping" }));
            }
        }, 10000);

        // Sync fresh queue upon connection or reconnection
        loadLiveQueue();
    };

const MOBILE_WS_HANDLERS = {
    pong: () => {},
    queue_updated: () => {
        loadLiveQueue();
        if (isHost) loadHostSingerRoster();
    },
    play_track: () => {
        loadLiveQueue();
        if (isHost) loadHostSingerRoster();
    },
    pending_downloads_updated: (data) => {
        currentPendingDownloads = data.pending_downloads || [];
        renderQueue(currentQueueCache);
    },
    download_progress: (data) => {
        if (data.pending_downloads) {
            currentPendingDownloads = data.pending_downloads;
        } else if (data.pending_id) {
            const item = currentPendingDownloads.find(p => p.id === data.pending_id);
            if (item) {
                item.progress = data.progress;
                item.status = data.status;
            }
        }
        renderQueue(currentQueueCache);
    },
    renormalize_progress: (data) => {
        if (isHost) showToast(`🎚️ Calibrating [${data.current}/${data.total}]: "${data.title}" (${data.percent}%)`);
    },
    renormalize_complete: (data) => {
        if (isHost) {
            showToast(`✅ Audio calibration complete for ${data.total} tracks!`);
            loadLiveQueue();
        }
    },
    host_alert: (data) => {
        if (isHost) showToast(`⚠️ ${data.message}`);
    },
    ws_error: (data) => {
        showToast(`⚠️ ${data.message || "Invalid request"}`);
    },
    singer_status_updated: (data) => {
        if (currentSinger && (data.singer_id === currentSinger.id || data.token === currentSinger.token)) {
            currentSinger.status = data.status;
            updateSingerHeader();
        }
        if (isHost) loadHostSingerRoster();
        loadLiveQueue();
    },
    recording_ready: (data) => {
        if (!data.singer_token || (currentSinger && (data.singer_token === currentSinger.token || data.singer_id === currentSinger.id))) {
            const recTitle = data.song_title || data.track_title || "Your Song";
            showToast(`🎙️ Performance recording ready: "${recTitle}"! Check My Songs to stream or download MP3 souvenir!`);
            if (document.getElementById("singerRecordingsList")) loadSingerRecordings();
        }
    },
    displays_updated: () => { if (isHost) loadHostDisplays(); },
    library_updated: () => {
        loadLibraryTracks();
        if (isHost) loadHostStorageStatus();
    },
    filler_tracks_updated: () => { if (isHost) loadFillerTracks(); },
    filler_track_updated: () => { if (isHost) loadFillerTracks(); },
    filler_settings_updated: (data) => { if (isHost) loadFillerSettings(data.settings); },
    singer_on_deck: (data) => handleSingerOnDeckMessage(data),
    host_mic_settings: (data) => { if (isHost) syncHostMicSettingsUI(data); },
    strict_mode_updated: (data) => { if (isHost) syncHostStrictModeUI(data.strict_byom_only); },
    network_updated: () => { if (isHost) loadHostNetworkStatus(); },
    server_shutting_down: () => {
        showToast("🛑 Host closed the session. Aloha! 🌺");
        setTimeout(() => {
            document.body.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#05080f;color:#00f0ff;font-family:sans-serif;text-align:center;padding:20px;"><h1>🌴 Party Closed</h1><p style="color:#aaa;font-size:1.1rem;margin-top:12px;">Tiki Karaoke has been shut down by the host.<br>Aloha and see you next time! 🌺</p></div>';
        }, 1000);
    },
};

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const handler = MOBILE_WS_HANDLERS[data.type];
            if (handler) handler(data);
        } catch (e) {
            console.error("[WS] Error:", e);
        }
    };

    ws.onclose = () => {
        const pill = document.getElementById("reconnectingPill");
        if (pill) pill.style.display = "flex";
        if (wsPingInterval) {
            clearInterval(wsPingInterval);
            wsPingInterval = null;
        }
        if (!wsReconnectTimeout) {
            wsReconnectTimeout = setTimeout(() => {
                wsReconnectTimeout = null;
                initWebSocket();
            }, 2000);
        }
    };

    ws.onerror = () => {
        const pill = document.getElementById("reconnectingPill");
        if (pill) pill.style.display = "flex";
    };
}

function reconnectWebSocket() {
    if (wsReconnectTimeout) {
        clearTimeout(wsReconnectTimeout);
        wsReconnectTimeout = null;
    }
    if (ws) {
        try {
            ws.onclose = null;
            ws.close();
        } catch (e) { console.debug('[Tiki]', e); }
    }
    initWebSocket();
}

// Wi-Fi Self-Healing & Tab Wake Synchronization
window.addEventListener("online", () => {
    console.log("[Network] Online event detected. Resyncing WebSocket and Queue...");
    reconnectWebSocket();
    loadLiveQueue();
});

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
        console.log("[App] Tab restored to foreground. Checking connection & Queue...");
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            reconnectWebSocket();
        }
        loadLiveQueue();
    }
});

function switchTab(tabName) {
    if (!tabName) return;
    const target = tabName.endsWith("Tab") ? tabName : (tabName + "Tab");
    const btn = document.querySelector(`.nav-tab-btn[data-tab="${target}"]`) || document.querySelector(`.nav-tab-btn[data-tab="${tabName}"]`);
    if (btn) {
        btn.click();
        return;
    }
    // Fallback if button not found or hidden
    document.querySelectorAll(".nav-tab-btn[data-tab]").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-view").forEach(v => v.style.display = "none");
    const targetEl = document.getElementById(target) || document.getElementById(tabName);
    if (targetEl) targetEl.style.display = "block";
}

// Navigation Tabs
function setupNavigation() {
    document.querySelectorAll(".nav-tab-btn[data-tab]").forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            if (!target) return;

            document.querySelectorAll(".nav-tab-btn[data-tab]").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-view").forEach(v => v.style.display = "none");

            btn.classList.add("active");
            const targetEl = document.getElementById(target);
            if (targetEl) targetEl.style.display = "block";

            if (target === "libraryTab") loadLibraryTracks();
            if (target === "queueTab") loadLiveQueue();
            if (target === "historyTab") loadHistory();
            if (target === "hostTab") {
                loadHostSingerRoster();
                loadHostDisplays();
                loadHostStorageStatus();
                checkYtdlpStatus();
                loadVenueAvSync();
                loadHostDefaultPaths();
                loadHostStrictMode();
                loadHostNetworkStatus();
            }
            if (target === "fillerCrateTab") {
                loadFillerTracks();
            }
            if (target === "fillerSearchTab") {
                const fInput = document.getElementById("fillerSearchInput");
                if (fInput) fInput.focus();
            }
        });
    });
}

function openStageTab(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const hostToken = localStorage.getItem("tiki_host_token") || "";
    let stageUrl = "/stage";
    if (isHost && hostToken) {
        stageUrl = `/stage?token=${encodeURIComponent(hostToken)}`;
    } else if (isHost) {
        stageUrl = "/stage";
    } else {
        stageUrl = "/stage?mute=1";
    }
    window.open(stageUrl, "_blank");
}

// Tiered Search & Queue
function setupSearch() {
    const input = document.getElementById("searchInput");
    const clearBtn = document.getElementById("searchClearBtn");
    if (!input) return;

    const updateClearBtn = () => {
        if (clearBtn) {
            clearBtn.style.display = input.value.length > 0 ? "flex" : "none";
        }
    };

    input.addEventListener("input", () => {
        updateClearBtn();
        clearTimeout(searchDebounceTimer);
        const query = input.value.trim();
        if (query.length < 2) {
            const resultsContainer = document.getElementById("searchResults");
            if (resultsContainer) {
                resultsContainer.innerHTML = `
                    <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                        <div style="font-size: 2.5rem; margin-bottom: 8px;">🎤</div>
                        <p>Type a song or artist above to search our instant local library and YouTube!</p>
                    </div>`;
            }
            return;
        }

        searchDebounceTimer = setTimeout(() => {
            performSearch(query);
        }, 320);
    });

    input.addEventListener("keyup", updateClearBtn);
    input.addEventListener("change", updateClearBtn);
    updateClearBtn();
}

function clearSearchInput() {
    const input = document.getElementById("searchInput");
    const clearBtn = document.getElementById("searchClearBtn");
    if (input) {
        input.value = "";
        input.focus();
    }
    if (clearBtn) {
        clearBtn.style.display = "none";
    }
    clearTimeout(searchDebounceTimer);
    const resultsContainer = document.getElementById("searchResults");
    if (resultsContainer) {
        resultsContainer.innerHTML = `
            <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                <div style="font-size: 2.5rem; margin-bottom: 8px;">🎤</div>
                <p>Type a song or artist above to search our instant local library and YouTube!</p>
            </div>`;
    }
}

let currentSearchQuery = "";
let currentSearchForceOnline = false;
let currentSearchPage = 1;
let currentSearchAbortController = null;

function performSearch(query, forceOnline = false, page = 1) {
    const resultsContainer = document.getElementById("searchResults");
    if (!resultsContainer) return;

    currentSearchQuery = query;
    currentSearchForceOnline = forceOnline;
    currentSearchPage = page;

    if (currentSearchAbortController) {
        currentSearchAbortController.abort();
    }
    currentSearchAbortController = new AbortController();
    const signal = currentSearchAbortController.signal;

    if (page === 1) {
        const searchMsg = isSingAlongMode ? "🍹 Searching Tiki Sing-Along Library & YouTube..." : "🍹 Searching Tiki Library & YouTube...";
        resultsContainer.innerHTML = `<div style="text-align: center; padding: 25px; color: var(--neon-cyan);">${searchMsg}</div>`;
    } else {
        const findMoreBtn = document.getElementById("findMoreBtn");
        if (findMoreBtn) {
            findMoreBtn.disabled = true;
            findMoreBtn.innerText = "🌺 Digging up more songs...";
        }
    }

    const timeoutId = setTimeout(() => {
        if (currentSearchAbortController) {
            currentSearchAbortController.abort();
        }
    }, 12000);

    fetch(`/api/search?q=${encodeURIComponent(query)}&force_online=${forceOnline}&sing_along=${isSingAlongMode}&page=${page}`, { signal })
        .then(async res => {
            clearTimeout(timeoutId);
            if (res.status === 429) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || "Search rate limit reached. Please wait a few seconds.");
            }
            if (!res.ok) {
                throw new Error("Search request failed.");
            }
            return res.json();
        })
        .then(data => {
            renderSearchResults(data, query, page > 1);
        })
        .catch(err => {
            clearTimeout(timeoutId);
            if (err.name === "AbortError") {
                // Superseded by newer search keystroke; ignore silently
                return;
            }
            if (page === 1) {
                resultsContainer.innerHTML = `<div style="text-align: center; color: #ffb703; padding: 20px;">
                    <div style="margin-bottom: 10px;">⏳ ${err.message || "Search error. Check connection."}</div>
                    <button type="button" onclick="performSearch(currentSearchQuery, currentSearchForceOnline, 1)" class="btn-ghost" style="padding: 6px 14px; font-size: 0.85rem;">↻ Retry Search</button>
                </div>`;
            } else {
                showToast("⚠️ " + (err.message || "Could not load more songs"));
                const findMoreBtn = document.getElementById("findMoreBtn");
                if (findMoreBtn) {
                    findMoreBtn.disabled = false;
                    findMoreBtn.innerText = "🔎 Find More Songs...";
                }
            }
        });
}

function renderSearchResults(data, query, isAppend = false) {
    const container = document.getElementById("searchResults");
    if (!container) return;

    const localList = data.local_results || [];
    const ytList = data.youtube_results || [];
    const hasMore = Boolean(data.has_more);

    if (!isAppend) {
        container.innerHTML = "";

        if (data.is_offline) {
            const offlineBanner = document.createElement("div");
            offlineBanner.style.cssText = "background: rgba(255, 170, 0, 0.12); border: 1px solid rgba(255, 170, 0, 0.4); border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 0.82rem; color: #ffaa00; display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap;";
            offlineBanner.innerHTML = `
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span>📶</span> <span><strong>Offline Hotspot Mode:</strong> Searching downloaded library only.</span>
                </div>
                <button type="button" class="btn-ghost" style="border-color: #ffaa00; color: #ffaa00; padding: 4px 10px; font-size: 0.75rem; border-radius: 6px; cursor: pointer;" onclick="performSearch('${escapeHtml(query)}', true, 1)">
                    🌐 Search YouTube Anyway
                </button>
            `;
            container.appendChild(offlineBanner);
        }

        if (localList.length === 0 && ytList.length === 0) {
            const emptyNotice = document.createElement("div");
            emptyNotice.style.cssText = "text-align:center; padding: 30px; color: var(--text-muted);";
            if (data.is_offline) {
                emptyNotice.innerHTML = `
                    <div style="font-size: 1.5rem; margin-bottom: 8px;">🌴 Library Search</div>
                    No downloaded songs matching "<strong>${escapeHtml(query)}</strong>" found.<br>
                    <span style="font-size:0.85rem; color:#888;">If host is connected to the internet, tap below to search YouTube:</span>
                    <div style="margin-top: 14px;">
                        <button type="button" class="btn-neon-pink" style="padding: 10px 20px; font-size: 0.88rem; border-radius: 20px; cursor: pointer;" onclick="performSearch('${escapeHtml(query)}', true, 1)">
                            🌐 Force Online YouTube Search
                        </button>
                    </div>
                `;
            } else {
                emptyNotice.innerHTML = `No songs found for "${escapeHtml(query)}". Try another title or artist! 🌺`;
            }
            container.appendChild(emptyNotice);
            return;
        }

        // 1. Local Tracks (Tier 1)
        if (localList.length > 0) {
            const localSection = document.createElement("div");
            const localHeader = isSingAlongMode ? "⚡ LOCAL SING-ALONG TRACKS (Instant Play)" : "⚡ FROM LOCAL TIKI LIBRARY (Instant Play)";
            localSection.innerHTML = `<div style="font-size: 0.8rem; color: var(--neon-lime); font-weight: 700; margin: 15px 0 8px 4px;">${localHeader}</div>`;
            
            localList.forEach(track => {
                const card = document.createElement("div");
                card.className = "tiki-card";
                card.style.padding = "14px";
                card.style.marginBottom = "10px";
                card.style.display = "flex";
                card.style.justifyContent = "space-between";
                card.style.alignItems = "center";

                const isSingAlongTrack = Boolean(track.is_sing_along || isSingAlongMode);

                card.innerHTML = `
                    <div style="flex: 1; padding-right: 10px;">
                        <div style="font-weight: 700; font-size: 1.05rem;">${escapeHtml(track.title)}</div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(track.artist)}</div>
                        <div style="display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap;">
                            <span class="badge-local">⚡ Downloaded</span>
                            ${isSingAlongTrack ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                        </div>
                    </div>
                    <button class="btn-neon-pink local-queue-btn" style="padding: 8px 14px; font-size: 0.85rem;">
                        + Queue
                    </button>
                `;
                card.querySelector(".local-queue-btn").onclick = () => openQueueModal(track.id, track.title, track.artist, track.video_id, 0, isSingAlongTrack);
                localSection.appendChild(card);
            });
            container.appendChild(localSection);
        }
    }

    // 2. YouTube Results (Tier 2)
    let ytSection = document.getElementById("ytResultsSection");
    if (!ytSection && ytList.length > 0) {
        ytSection = document.createElement("div");
        ytSection.id = "ytResultsSection";
        const ytHeader = isSingAlongMode ? "🌐 YOUTUBE SING-ALONG (LYRICS + VOCALS)" : "🌐 YOUTUBE KARAOKE RESULTS";
        ytSection.innerHTML = `<div style="font-size: 0.8rem; color: var(--neon-pink); font-weight: 700; margin: 15px 0 8px 4px;">${ytHeader}</div><div id="ytResultsCards"></div>`;
        container.appendChild(ytSection);
    }

    const cardsContainer = document.getElementById("ytResultsCards") || ytSection;
    if (cardsContainer && ytList.length > 0) {
        ytList.forEach(group => {
            const card = document.createElement("div");
            card.className = "tiki-card-pink";
            card.style.padding = "14px";
            card.style.marginBottom = "12px";

            const bestVersion = group.versions[0];
            const isSingAlongYt = Boolean(group.is_sing_along || isSingAlongMode);

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div style="flex: 1; padding-right: 10px;">
                        <div style="font-weight: 700; font-size: 1.05rem;">${escapeHtml(group.title)}</div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">${escapeHtml(group.artist)}</div>
                        <div style="font-size: 0.75rem; color: var(--neon-gold); margin-top: 4px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                            <span>${escapeHtml(bestVersion.channel || "YouTube")} • ${formatDuration(bestVersion.duration)}</span>
                            ${bestVersion.attributes && bestVersion.attributes.length ? `<span>• ${bestVersion.attributes.join(', ')}</span>` : ''}
                            ${isSingAlongYt ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                        </div>
                    </div>
                    <button class="btn-neon-cyan yt-queue-btn" style="padding: 8px 14px; font-size: 0.85rem;">
                        + Queue
                    </button>
                </div>
            `;
            card.querySelector(".yt-queue-btn").onclick = () => openQueueModal(null, group.title, group.artist, bestVersion.video_id, 0, isSingAlongYt);
            cardsContainer.appendChild(card);
        });
    }

    // Clean up old button
    const oldBtn = document.getElementById("findMoreBtn");
    if (oldBtn) oldBtn.remove();

    // Render "Find More Songs" button
    if (hasMore || (!currentSearchForceOnline && localList.length > 0)) {
        const findMoreBtn = document.createElement("button");
        findMoreBtn.id = "findMoreBtn";
        findMoreBtn.className = "btn-ghost";
        findMoreBtn.style.width = "100%";
        findMoreBtn.style.margin = "14px 0 24px 0";
        findMoreBtn.style.padding = "12px";
        findMoreBtn.style.borderColor = "var(--neon-cyan)";
        findMoreBtn.style.color = "var(--neon-cyan)";
        findMoreBtn.style.fontWeight = "700";
        findMoreBtn.style.borderRadius = "10px";
        findMoreBtn.innerText = (!currentSearchForceOnline && localList.length > 0)
            ? (isSingAlongMode ? "🌐 Search YouTube for more sing-alongs..." : "🌐 Search YouTube for more versions...")
            : "🔎 Find More Songs...";
        findMoreBtn.onclick = () => {
            const nextPage = (!currentSearchForceOnline && localList.length > 0) ? 1 : (currentSearchPage + 1);
            performSearch(currentSearchQuery, true, nextPage);
        };
        container.appendChild(findMoreBtn);
    } else if (isAppend && ytList.length === 0) {
        const endBanner = document.createElement("div");
        endBanner.style.textAlign = "center";
        endBanner.style.padding = "16px";
        endBanner.style.color = "var(--text-muted)";
        endBanner.style.fontSize = "0.85rem";
        endBanner.innerText = "🌴 That's all the songs found for this search!";
        container.appendChild(endBanner);
    }

}

// Queue Modal & Pitch Selector
// Queue Modal & Pitch Selector
async function openQueueModal(trackId, title, artist, videoId, initialPitch = 0, isSingAlong = null) {
    const isSA = (isSingAlong !== null && isSingAlong !== undefined) ? Boolean(isSingAlong) : Boolean(isSingAlongMode);
    selectedTrackForQueue = { trackId, title, artist, videoId, isSingAlong: isSA };
    selectedPitch = initialPitch || 0;
    activeDuplicateInfo = null;

    const modalTitleEl = document.getElementById("modalSongTitle");
    if (modalTitleEl) {
        modalTitleEl.innerHTML = `${escapeHtml(artist)} - ${escapeHtml(title)} ${isSA ? ' <span class="badge-sing-along" style="font-size:0.75rem;">🎙️ Sing-Along</span>' : ''}`;
    }
    resetPitchButtons();
    if (selectedPitch !== 0) {
        selectPitch(selectedPitch);
    }
    document.getElementById("duetInput").value = "";
    const dedInput = document.getElementById("dedicationInput");
    if (dedInput) dedInput.value = "";

    const dupSec = document.getElementById("queueModalDuplicateSection");
    if (dupSec) dupSec.style.display = "none";
    const submitBtn = document.getElementById("submitQueueBtn");
    if (submitBtn) submitBtn.innerText = "+ Queue Song";

    document.getElementById("queueModal").style.display = "flex";

    // Asynchronously check if song is already in queue or performed recently
    try {
        const token = currentSinger ? currentSinger.token : "";
        const params = new URLSearchParams({
            singer_token: token,
            title: title || "",
            artist: artist || "",
            video_id: videoId || ""
        });
        if (trackId) params.set("track_id", trackId);
        const res = await fetch(`/api/queue/check-duplicate?${params.toString()}`);
        if (res.ok) {
            const data = await res.json();
            if (data.duplicate && data.duplicate.duplicate_found) {
                showDuplicateAdvisory(data.duplicate);
            }
        }
    } catch (e) {
        console.log("Duplicate pre-check error:", e);
    }
}

function closeQueueModal() {
    document.getElementById("queueModal").style.display = "none";
    activeDuplicateInfo = null;
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
    const dedicationEl = document.getElementById("dedicationInput");
    const dedication = dedicationEl ? dedicationEl.value.trim() : "";
    const payload = {
        track_id: selectedTrackForQueue.trackId,
        video_id: selectedTrackForQueue.videoId,
        title: selectedTrackForQueue.title,
        artist: selectedTrackForQueue.artist,
        singer_token: currentSinger.token,
        duet_partner: duet,
        pitch_shift: selectedPitch,
        dedication: dedication,
        force: Boolean(activeDuplicateInfo),
        is_sing_along: Boolean(selectedTrackForQueue.isSingAlong)
    };

    fetch("/api/queue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(async res => {
        const data = await res.json().catch(() => ({}));
        if (res.status === 409 && data.status === "duplicate_warning" && data.duplicate) {
            showDuplicateAdvisory(data.duplicate);
            return null;
        }
        if (!res.ok) {
            throw new Error(data.detail || "Failed to queue song.");
        }
        return data;
    })
    .then(data => {
        if (!data) return; // duplicate notice displayed in modal
        lastQueuedSongTime = Date.now();
        lastQueuedSongId = data.queue_id || null;
        closeQueueModal();
        showToast("🌺 Song Queued! Check Live Queue for your spot.");
        loadLiveQueue();
        checkNotificationPermissionPrompt();
        // Switch to Queue Tab
        document.querySelector('[data-tab="queueTab"]').click();
    })
    .catch((err) => {
        showToast(err.message || "Failed to queue song.");
    });
}

function showDuplicateAdvisory(dup) {
    activeDuplicateInfo = dup;
    const dupSec = document.getElementById("queueModalDuplicateSection");
    if (dupSec) dupSec.style.display = "block";

    const badge = document.getElementById("dupModalBadge");
    const singerAvatar = document.getElementById("dupSingerAvatar");
    const singerName = document.getElementById("dupSingerName");
    const duetTargetName = document.getElementById("dupDuetTargetName");
    const queuePos = document.getElementById("dupQueuePosition");
    const advisoryText = document.getElementById("dupAdvisoryText");
    const joinDuetBtn = document.getElementById("dupJoinDuetBtn");
    const submitBtn = document.getElementById("submitQueueBtn");

    if (singerAvatar) singerAvatar.innerText = dup.existing_singer_avatar || "🌺";
    if (singerName) singerName.innerText = dup.existing_singer_name || "Another Singer";
    if (duetTargetName) duetTargetName.innerText = dup.existing_singer_name || "Friend";

    if (dup.match_type === "playing") {
        if (badge) {
            badge.innerText = "Currently Playing";
            badge.style.color = "var(--neon-lime)";
            badge.style.background = "rgba(57,255,20,0.15)";
        }
        if (queuePos) queuePos.innerText = "Live on Stage";
        if (advisoryText) {
            advisoryText.innerText = dup.is_self 
                ? "You are singing this song on stage right now! Enjoy your performance!" 
                : `${dup.existing_singer_name} is performing this song on stage right now! Head up and cheer them on or sing along!`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "none";
        if (submitBtn) submitBtn.innerText = "+ Queue Anyway";
    } else if (dup.match_type === "self") {
        if (badge) {
            badge.innerText = "Already In Your Queue";
            badge.style.color = "var(--neon-gold)";
            badge.style.background = "rgba(255,183,3,0.15)";
        }
        if (queuePos) queuePos.innerText = dup.position_in_line ? `Position #${dup.position_in_line} in line` : "Upcoming in queue";
        if (advisoryText) {
            advisoryText.innerText = `You already have this song in the queue (${dup.position_in_line ? '#' + dup.position_in_line + ' in line' : 'upcoming'})! Are you sure you want to sing the same song multiple times tonight?`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "none";
        if (submitBtn) submitBtn.innerText = "+ Queue Again Anyway";
    } else if (dup.match_type === "played_tonight") {
        if (badge) {
            badge.innerText = "Sung Earlier Tonight";
            badge.style.color = "var(--neon-pink)";
            badge.style.background = "rgba(255,42,133,0.15)";
        }
        if (queuePos) queuePos.innerText = "Completed earlier tonight";
        if (advisoryText) {
            advisoryText.innerText = `This song was already sung earlier tonight by ${dup.existing_singer_name}. Feel free to queue an encore!`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "none";
        if (submitBtn) submitBtn.innerText = "+ Queue Encore";
    } else {
        // Other singer has it queued
        if (badge) {
            badge.innerText = "Already in Queue";
            badge.style.color = "var(--neon-cyan)";
            badge.style.background = "rgba(0,240,255,0.15)";
        }
        if (queuePos) queuePos.innerText = dup.position_in_line ? `Position #${dup.position_in_line} in line` : "Upcoming in queue";
        if (advisoryText) {
            advisoryText.innerText = `Are you sure? ${dup.existing_singer_name} already has this song in queue (${dup.position_in_line ? '#' + dup.position_in_line + ' in line' : 'upcoming'}). Consider singing along with them as a duet and discuss as appropriate!`;
        }
        if (joinDuetBtn) joinDuetBtn.style.display = "block";
        if (submitBtn) submitBtn.innerText = "+ Queue Anyway";
    }
}

function handleJoinDuet() {
    if (!activeDuplicateInfo || !activeDuplicateInfo.existing_queue_id) return;
    if (!currentSinger || !currentSinger.token || !currentSinger.name || currentSinger.name === "undefined") {
        showToast("⚠️ Please choose your nickname first!");
        showOnboardingModal();
        return;
    }

    fetch(`/api/queue/${activeDuplicateInfo.existing_queue_id}/join-duet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            singer_token: currentSinger.token,
            partner_name: currentSinger.name
        })
    })
    .then(async res => {
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Could not join duet.");
        }
        return res.json();
    })
    .then(data => {
        closeQueueModal();
        showToast(`🎉 You're now singing a duet with ${activeDuplicateInfo.existing_singer_name}!`);
        loadLiveQueue();
        document.querySelector('[data-tab="queueTab"]').click();
    })
    .catch(err => {
        showToast(err.message || "Failed to join duet.");
    });
}

let currentQueueCache = [];
let currentPendingDownloads = [];

// Live Queue View
function loadLiveQueue() {
    Promise.all([
        fetch("/api/queue").then(res => res.json()).catch(() => []),
        fetch("/api/downloads/pending").then(res => res.json()).then(d => d.pending_downloads || []).catch(() => [])
    ]).then(([queue, pending]) => {
        currentQueueCache = queue || [];
        currentPendingDownloads = pending || [];
        renderQueue(currentQueueCache);
    });
}

function renderQueue(queue) {
    currentQueueCache = queue || [];
    const quickstart = document.getElementById("hostQuickstartCard");
    if (quickstart) {
        let isDismissed = false;
        try { isDismissed = !!localStorage.getItem("tiki_hide_quickstart"); } catch (e) { console.debug('[Tiki]', e); }
        const shouldShow = isHost && (forceShowQuickstart || (queue.length === 0 && !isDismissed));
        quickstart.style.display = shouldShow ? "block" : "none";
    }

    const container = document.getElementById("queueListContainer");
    if (!container) return;
    container.innerHTML = "";

    const hasActive = queue && queue.length > 0;
    const hasPending = currentPendingDownloads && currentPendingDownloads.length > 0;

    if (!hasActive && !hasPending) {
        container.innerHTML = `
            <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
                <div style="font-size: 3rem; margin-bottom: 10px;">🌴</div>
                <div class="font-tiki neon-cyan" style="font-size: 1.4rem;">The Stage is Open!</div>
                <p style="margin-top: 8px;">Be the first rockstar to request a song tonight!</p>
            </div>
        `;
        return;
    }

    if (!hasActive && hasPending) {
        const waitingHeader = document.createElement("div");
        waitingHeader.style.cssText = "text-align: center; padding: 20px 15px; color: var(--text-muted);";
        waitingHeader.innerHTML = `
            <div style="font-size: 2rem; margin-bottom: 6px;">🌴</div>
            <div class="font-tiki neon-cyan" style="font-size: 1.15rem;">Stage is Ready</div>
            <p style="margin-top: 4px; font-size: 0.85rem;">Upcoming song is preparing in the staging crate below...</p>
        `;
        container.appendChild(waitingHeader);
    }

    queue.forEach((item, index) => {
        const isPlaying = item.status === "playing";
        const isMine = currentSinger && item.singer_id === currentSinger.id;

        const myName = (currentSinger && currentSinger.name) ? currentSinger.name.trim().toLowerCase() : "";
        const primaryName = (item.singer_name || "").trim().toLowerCase();
        const rawPartners = (item.duet_partner || "").trim();
        const partnerNames = rawPartners 
            ? rawPartners.replace(/&/g, ",").split(",").map(p => p.trim()).filter(Boolean)
            : [];
        
        const isPrimary = Boolean(currentSinger && (item.singer_id === currentSinger.id || (myName && primaryName === myName)));
        const isPartner = Boolean(myName && partnerNames.some(p => p.toLowerCase() === myName));
        const amISingerOnSong = isPrimary || isPartner;
        const totalSingersCount = 1 + partnerNames.length;

        // If user is the only remaining singer, do not show button (they have the ✕ button)
        const isOnlySinger = amISingerOnSong && (totalSingersCount === 1);
        const showDuetToggle = !isOnlySinger;
        const isLeaving = amISingerOnSong && totalSingersCount > 1;

        const duetBtnLabel = isLeaving ? "🚪 Leave Song" : "👯 Join Duet";
        const duetBtnClass = isLeaving ? "btn-leave-duet" : "btn-join-duet";
        const duetBtnStyle = isLeaving 
            ? "border-color: rgba(255, 170, 0, 0.45); color: #ffaa00;" 
            : "border-color: var(--neon-cyan); color: var(--neon-cyan);";

        const showCancelBtn = (isMine || isHost) && !isPlaying;

        const card = document.createElement("div");
        card.className = isPlaying ? "tiki-card-pink pulse-glow" : "tiki-card";
        card.style.padding = "16px";
        card.style.marginBottom = "12px";

        const pitchText = item.pitch_shift !== 0 ? `Key: ${item.pitch_shift > 0 ? '+' : ''}${item.pitch_shift}` : "Standard Key";
        const isOnBreak = item.singer_status === "on_break";
        const statusBadge = isOnBreak 
            ? `<span class="badge" style="background: rgba(255,183,3,0.15); color: var(--neon-gold); border: 1px solid var(--neon-gold); font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; ${isHost ? 'cursor: pointer;' : ''}" ${isHost ? `onclick="toggleHostSingerStatus(${item.singer_id}, 'active')"` : ''}>⏸️ On Break ${isHost ? '(Tap to Resume)' : ''}</span>`
            : (isHost && !isPlaying ? `<span class="badge" style="background: rgba(57,255,20,0.1); color: var(--neon-lime); border: 1px solid var(--neon-lime); font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; cursor: pointer;" onclick="toggleHostSingerStatus(${item.singer_id}, 'on_break')">🟢 Active (Pause)</span>` : '');

        const dedicationHtml = item.dedication 
            ? `<div style="font-size: 0.76rem; color: var(--neon-gold); font-style: italic; margin-top: 3px;">💌 "${escapeHtml(item.dedication)}"</div>`
            : '';

        const lufsVal = typeof item.normalized_lufs === "number" ? item.normalized_lufs : -16.0;
        const isLufsOff = Math.abs(lufsVal - (-16.0)) > 1.5;
        const lufsBadge = isHost ? `
            <span class="badge" style="background: ${isLufsOff ? 'rgba(255, 170, 0, 0.2)' : 'rgba(0, 240, 255, 0.15)'}; color: ${isLufsOff ? 'var(--neon-gold)' : 'var(--neon-cyan)'}; border: 1px solid ${isLufsOff ? 'var(--neon-gold)' : 'rgba(0, 240, 255, 0.4)'}; font-size: 0.72rem; padding: 2px 6px; border-radius: 4px; cursor: pointer;" title="Normalized Loudness: ${lufsVal.toFixed(1)} LUFS (Tap to Re-normalize to target)" onclick="hostRenormalizeTrackPrompt(${item.track_id}, '${escapeHtml(item.title)}')">
                🎚️ ${lufsVal.toFixed(1)} LUFS${isLufsOff ? ' ⚡' : ''}
            </span>
        ` : '';

        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0;">
                    <div style="font-size: 1.8rem; flex-shrink: 0;">${item.singer_avatar || "🌺"}</div>
                    <div style="min-width: 0; overflow: hidden;">
                        <div style="font-size: 0.8rem; font-weight: 700; color: ${isPlaying ? 'var(--neon-pink)' : 'var(--neon-cyan)'}; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <span>${isPlaying ? 'NOW SINGING' : `#${index + 1} IN LINE`}</span>
                            ${statusBadge}
                        </div>
                        <div style="font-weight: 700; font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.singer_name)} ${item.duet_partner ? `& ${escapeHtml(item.duet_partner)}` : ''}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.88rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                            ${escapeHtml(item.artist)} - ${escapeHtml(item.title)}
                        </div>
                        ${dedicationHtml}
                        <div style="display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap;">
                            <span class="badge-key">${pitchText}</span>
                            ${item.is_sing_along ? '<span class="badge-sing-along">🎙️ Sing-Along</span>' : ''}
                            ${lufsBadge}
                        </div>
                    </div>
                </div>

                <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end;">
                    ${amISingerOnSong && !isPlaying ? `
                        <button onclick="openRehearsalModal(${item.track_id}, '${escapeHtml(item.title)}', '${escapeHtml(item.artist)}', '${item.video_id || ''}', ${item.is_downloaded ? 1 : 0})" 
                                class="btn-ghost" 
                                style="padding: 6px 10px; font-size: 0.76rem; font-weight: 700; color: var(--neon-cyan); border-color: rgba(0,240,255,0.4); border-radius: 8px; white-space: nowrap;" title="Privately rehearse video & lyrics">
                            👁️ Rehearse
                        </button>
                    ` : ''}

                    ${showDuetToggle ? `
                        <button onclick="${isLeaving ? `leaveQueueDuet(${item.queue_id})` : `joinQueueDuet(${item.queue_id})`}" 
                                class="btn-ghost ${duetBtnClass}" 
                                style="padding: 6px 10px; font-size: 0.78rem; font-weight: 700; border-radius: 8px; ${duetBtnStyle}; white-space: nowrap;">
                            ${duetBtnLabel}
                        </button>
                    ` : ''}

                    ${showCancelBtn ? `
                        <button onclick="cancelQueuedSong(${item.queue_id})" class="btn-ghost" style="padding: 6px 10px; font-size: 0.85rem; color: #ff5555; border-radius: 8px;" title="Remove song">
                            ✕
                        </button>
                    ` : ''}
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    // Render Pending / Downloading Staging Section below Active Stage Queue
    if (hasPending) {
        const pendingSection = document.createElement("div");
        pendingSection.style.cssText = "margin-top: 24px; margin-bottom: 12px;";

        const pendingHeader = document.createElement("div");
        pendingHeader.style.cssText = "display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; padding: 0 4px;";
        pendingHeader.innerHTML = `
            <div style="font-size: 0.85rem; font-weight: 800; letter-spacing: 1px; color: var(--neon-cyan); display: flex; align-items: center; gap: 6px;">
                <span>⏳ PREPARING & DOWNLOADING</span>
                <span class="badge" style="background: rgba(0,240,255,0.15); border: 1px solid var(--neon-cyan); font-size: 0.72rem; padding: 1px 6px; border-radius: 10px;">${currentPendingDownloads.length}</span>
            </div>
            <div style="font-size: 0.72rem; color: var(--text-muted);">
                Slots into fair rotation once ready
            </div>
        `;
        pendingSection.appendChild(pendingHeader);

        currentPendingDownloads.forEach((item) => {
            const isMine = currentSinger && item.singer_id === currentSinger.id;
            const canManage = isMine || isHost;
            const progressPct = Math.min(100, Math.max(0, item.progress || 0));
            const retryCount = item.retry_count || 0;
            const isFailed = item.status === "failed";

            const card = document.createElement("div");
            card.className = "tiki-card";
            card.style.cssText = "padding: 14px; margin-bottom: 10px; border-style: dashed; border-color: rgba(0,240,255,0.35); background: rgba(18, 11, 30, 0.65);";

            let statusText = "Downloading...";
            if (isFailed) {
                statusText = item.error_message || "Download stalled";
            } else if (progressPct >= 99) {
                statusText = "Finalizing & Normalizing LUFS...";
            } else {
                statusText = `Downloading ${progressPct}%...`;
            }

            let actionsHtml = "";
            if (canManage) {
                if (retryCount >= 3 || (isFailed && retryCount >= 2)) {
                    // Fail 3+: offer medium, lowest, and find another version
                    actionsHtml = `
                        <div style="display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap;">
                            <button onclick="retryPendingDownload(${item.id}, 'medium_360p')" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px;">
                                🔻 Try Medium Quality
                            </button>
                            <button onclick="retryPendingDownload(${item.id}, 'lowest')" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; border-color: var(--neon-cyan); color: var(--neon-cyan); border-radius: 6px;">
                                🔻 Try Lowest Quality
                            </button>
                            <button onclick="findAnotherVersion(${item.id}, '${escapeHtml(item.title)}', '${escapeHtml(item.artist)}')" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; border-color: var(--neon-pink); color: var(--neon-pink); border-radius: 6px;">
                                🔎 Find Another Version
                            </button>
                            <button onclick="cancelPendingDownload(${item.id})" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; color: #ff5555; border-radius: 6px;" title="Cancel">
                                ✕
                            </button>
                        </div>
                    `;
                } else if (isFailed || retryCount >= 1) {
                    // Fail 2: retry button
                    actionsHtml = `
                        <div style="display: flex; gap: 6px; margin-top: 10px; align-items: center;">
                            <button onclick="retryPendingDownload(${item.id})" class="btn-ghost" style="padding: 4px 10px; font-size: 0.75rem; border-color: var(--neon-gold); color: var(--neon-gold); border-radius: 6px; font-weight: 700;">
                                🔄 Retry Download
                            </button>
                            <button onclick="cancelPendingDownload(${item.id})" class="btn-ghost" style="padding: 4px 8px; font-size: 0.72rem; color: #ff5555; border-radius: 6px;" title="Cancel">
                                ✕ Cancel
                            </button>
                        </div>
                    `;
                } else {
                    actionsHtml = `
                        <div style="display: flex; justify-content: flex-end; margin-top: 6px;">
                            <button onclick="cancelPendingDownload(${item.id})" class="btn-ghost" style="padding: 2px 8px; font-size: 0.75rem; color: #ff5555; border-radius: 6px;" title="Cancel download">
                                ✕ Cancel
                            </button>
                        </div>
                    `;
                }
            }

            card.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                    <div style="display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;">
                        <div style="font-size: 1.6rem; flex-shrink: 0;">${item.singer_avatar || "🌺"}</div>
                        <div style="min-width: 0; overflow: hidden; flex: 1;">
                            <div style="font-size: 0.75rem; font-weight: 700; color: ${isFailed ? '#ff5555' : 'var(--neon-cyan)'}; display: flex; align-items: center; gap: 6px;">
                                <span>${isFailed ? '⚠️ STALLED' : 'PREPARING'}</span>
                                <span style="font-size: 0.7rem; color: var(--text-muted);">${statusText}</span>
                            </div>
                            <div style="font-weight: 700; font-size: 0.98rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${escapeHtml(item.singer_name)}
                            </div>
                            <div style="color: var(--text-muted); font-size: 0.85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                                ${escapeHtml(item.artist)} - ${escapeHtml(item.title)}
                            </div>
                            <div style="margin-top: 8px; width: 100%; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden;">
                                <div style="width: ${progressPct}%; height: 100%; background: ${isFailed ? '#ff5555' : 'linear-gradient(90deg, var(--neon-cyan), var(--neon-pink))'}; transition: width 0.3s ease;"></div>
                            </div>
                            ${actionsHtml}
                        </div>
                    </div>
                </div>
            `;
            pendingSection.appendChild(card);
        });
        container.appendChild(pendingSection);
    }
}

function retryPendingDownload(pendingId, quality = "default") {
    fetch(`/api/download/retry/${pendingId}?quality=${encodeURIComponent(quality)}`, {
        method: "POST"
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "ok") {
            showToast("🔄 Retrying download...");
            loadLiveQueue();
        } else {
            showToast("❌ " + (data.detail || "Could not retry download"));
        }
    })
    .catch(() => showToast("Network error retrying download"));
}

function cancelPendingDownload(pendingId) {
    fetch(`/api/download/${pendingId}`, {
        method: "DELETE"
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "ok") {
            showToast("Removed from downloads");
            loadLiveQueue();
        } else {
            showToast("❌ " + (data.detail || "Could not cancel download"));
        }
    })
    .catch(() => showToast("Network error cancelling download"));
}

function findAnotherVersion(pendingId, title, artist) {
    cancelPendingDownload(pendingId);
    if (typeof switchTab === "function") {
        switchTab("search");
    }
    const q = `${artist || ""} ${title || ""}`.trim();
    const searchInput = document.getElementById("searchInput");
    if (searchInput) {
        searchInput.value = q;
        const clearBtn = document.getElementById("searchClearBtn");
        if (clearBtn) clearBtn.style.display = q ? "flex" : "none";
        if (typeof performSearch === "function" && q) {
            performSearch(q);
        }
    }
}

// "You're On Deck (~30s)" Phone Alert Engine (Haptic, Push Notification, Non-blocking Top Banner)
function handleSingerOnDeckMessage(data) {
    if (isHost) return; // Never disturb the Host DJ
    if (!currentSinger || !currentSinger.name) return;

    // Suppress if singer queued this song within the last 15s (they already know)
    if (lastQueuedSongTime && (Date.now() - lastQueuedSongTime < 15000) && lastQueuedSongId === data.queue_id) {
        return;
    }

    const myName = currentSinger.name.trim().toLowerCase();
    const primaryName = (data.singer_name || "").trim().toLowerCase();
    const rawPartners = (data.duet_partner || "").trim();
    const partnerNames = rawPartners 
        ? rawPartners.replace(/&/g, ",").split(",").map(p => p.trim().toLowerCase()).filter(Boolean)
        : [];
    const isPrimary = (data.singer_id === currentSinger.id) || (myName && primaryName === myName);
    const isPartner = Boolean(myName && partnerNames.includes(myName));

    if (isPrimary || isPartner) {
        triggerSingerOnDeckAlert(data);
    }
}

function triggerSingerOnDeckAlert(track) {
    // 1. Subtle Haptic Vibration (Android Chrome / Firefox / Samsung Internet)
    if ("vibrate" in navigator) {
        try {
            navigator.vibrate([250, 150, 250]);
        } catch (e) {
            console.warn("[Vibrate] Haptic error:", e);
        }
    }

    // 2. Web Notification (if permission granted and user switched tabs/screen)
    if ("Notification" in window && Notification.permission === "granted") {
        try {
            new Notification("🎤 You're On Deck (~30s)!", {
                body: `Get ready to sing "${track.title}"! Head toward the stage microphone.`,
                icon: "/static/images/tiki-icon.png",
                vibrate: [250, 150, 250]
            });
        } catch (e) {
            console.warn("[Notification] Push error:", e);
        }
    }

    // 3. Sleek Top Slide-Down Banner (Non-Blocking, Auto-Dismisses after 8s)
    const banner = document.getElementById("singerOnDeckBanner");
    const titleEl = document.getElementById("onDeckSongTitle");
    if (titleEl) titleEl.innerText = track.title || "Your Song";
    if (banner) {
        banner.style.display = "flex";
        setTimeout(() => {
            banner.style.transform = "translateY(0)";
            banner.style.opacity = "1";
        }, 10);
        if (window._onDeckBannerTimer) clearTimeout(window._onDeckBannerTimer);
        window._onDeckBannerTimer = setTimeout(dismissSingerOnDeckBanner, 8000);
    }
}

function dismissSingerOnDeckBanner() {
    const banner = document.getElementById("singerOnDeckBanner");
    if (banner) {
        banner.style.transform = "translateY(-30px)";
        banner.style.opacity = "0";
        setTimeout(() => { banner.style.display = "none"; }, 300);
    }
}

function checkNotificationPermissionPrompt() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default" && !localStorage.getItem("tiki_dismissed_notif_banner")) {
        const banner = document.getElementById("notificationPermissionBanner");
        if (banner) banner.style.display = "flex";
    }
}

function requestNotificationPermission() {
    if (!("Notification" in window)) {
        showToast("Notifications not supported in this browser.");
        dismissNotificationBanner();
        return;
    }
    Notification.requestPermission().then(perm => {
        dismissNotificationBanner();
        if (perm === "granted") {
            showToast("🔔 Alerts enabled! We'll buzz you when you're up.");
        }
    }).catch(() => {
        dismissNotificationBanner();
    });
}

function dismissNotificationBanner() {
    const banner = document.getElementById("notificationPermissionBanner");
    if (banner) banner.style.display = "none";
    localStorage.setItem("tiki_dismissed_notif_banner", "true");
}

function joinQueueDuet(queueId) {
    if (!currentSinger || !currentSinger.token || !currentSinger.name || currentSinger.name === "undefined") {
        showToast("⚠️ Please choose your nickname first!");
        showOnboardingModal();
        return;
    }

    fetch(`/api/queue/${queueId}/join-duet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            singer_token: currentSinger.token,
            partner_name: currentSinger.name
        })
    })
    .then(async res => {
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Could not join song.");
        }
        return res.json();
    })
    .then(() => {
        showToast("👯 You joined this song as a duet partner!");
        loadLiveQueue();
    })
    .catch(err => {
        showToast(err.message || "Failed to join song.");
    });
}

function leaveQueueDuet(queueId) {
    if (!currentSinger || !currentSinger.token) {
        showToast("⚠️ Nickname required!");
        showOnboardingModal();
        return;
    }

    if (!confirm("Remove yourself as a singer on this song?")) return;

    fetch(`/api/queue/${queueId}/leave-duet`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            singer_token: currentSinger.token,
            singer_name: currentSinger.name
        })
    })
    .then(async res => {
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || "Could not remove yourself from song.");
        }
        return res.json();
    })
    .then(() => {
        showToast("👋 You removed yourself from this song.");
        loadLiveQueue();
    })
    .catch(err => {
        showToast(err.message || "Failed to leave song.");
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

// Personal Songbook & Queue Frequency Tracking
let currentSongbookCache = [];
let currentSongbookSort = "frequency";

function setSongbookSort(sortType) {
    currentSongbookSort = sortType;
    document.querySelectorAll("[data-songbook-sort]").forEach(btn => {
        if (btn.getAttribute("data-songbook-sort") === sortType) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    renderSongbookList();
}

function loadSongbook() {
    if (!currentSinger || !currentSinger.token) return;
    const list = document.getElementById("songbookList");
    if (!list) return;

    fetch(`/api/singer/${currentSinger.token}/songbook`)
        .then(res => res.json())
        .then(data => {
            currentSongbookCache = data.songs || [];
            const subtext = document.getElementById("songbookSubtext");
            if (subtext) {
                if (data.total_queued > 0) {
                    const singerName = data.singer?.name || currentSinger.name || "My";
                    subtext.innerText = `${singerName}'s Songbook • ${data.unique_songs} song${data.unique_songs === 1 ? '' : 's'} (${data.total_queued} total time${data.total_queued === 1 ? '' : 's'} queued)`;
                } else {
                    subtext.innerText = "Ranked by your most queued tracks across all visits";
                }
            }
            renderSongbookList();
        })
        .catch(err => {
            console.error("[Songbook] Error loading personal songbook:", err);
            list.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 20px;">Unable to load personal songbook. Tap refresh to retry!</div>';
        });
}

function renderSongbookList() {
    const list = document.getElementById("songbookList");
    if (!list) return;
    list.innerHTML = "";

    if (!currentSongbookCache || currentSongbookCache.length === 0) {
        list.innerHTML = `
            <div style="text-align: center; padding: 30px 16px; color: var(--text-muted);">
                <div style="font-size: 2.2rem; margin-bottom: 8px;">🌴</div>
                <div style="font-size: 1.05rem; font-weight: 700; color: #fff; margin-bottom: 6px;">Your Personal Songbook is Empty!</div>
                <p style="font-size: 0.85rem; max-width: 280px; margin: 0 auto 16px auto;">Search for a song or spin the Tiki Wheel to queue your first karaoke performance!</p>
                <button onclick="document.querySelector('[data-tab=\\'searchTab\\']').click()" class="btn-neon-cyan" style="font-size: 0.85rem; padding: 8px 18px;">
                    🔍 Browse & Search Songs
                </button>
            </div>
        `;
        return;
    }

    // Sort according to currentSongbookSort
    const sorted = [...currentSongbookCache];
    if (currentSongbookSort === "frequency") {
        sorted.sort((a, b) => (b.times_queued || 0) - (a.times_queued || 0));
    } else if (currentSongbookSort === "recent") {
        sorted.sort((a, b) => new Date(b.last_queued_at || 0) - new Date(a.last_queued_at || 0));
    } else if (currentSongbookSort === "alpha") {
        sorted.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    }

    sorted.forEach((item, index) => {
        const card = document.createElement("div");
        card.className = "tiki-card";
        card.style.padding = "14px 16px";
        card.style.marginBottom = "10px";
        card.style.display = "flex";
        card.style.justifyContent = "space-between";
        card.style.alignItems = "center";
        card.style.gap = "12px";

        // Frequency rank badge
        let badgeHtml = "";
        const count = item.times_queued || 1;
        if (currentSongbookSort === "frequency" && index === 0 && count > 1) {
            badgeHtml = `<span style="background: rgba(255, 183, 3, 0.2); border: 1px solid var(--neon-gold); color: var(--neon-gold); padding: 2px 8px; border-radius: 8px; font-size: 0.72rem; font-weight: 700;">👑 #1 Pick • ${count}x Queued</span>`;
        } else if (count > 1) {
            badgeHtml = `<span style="background: rgba(255, 42, 133, 0.15); border: 1px solid var(--neon-pink); color: var(--neon-pink); padding: 2px 8px; border-radius: 8px; font-size: 0.72rem; font-weight: 700;">🔥 ${count}x Queued</span>`;
        } else {
            badgeHtml = `<span style="background: rgba(0, 240, 255, 0.12); border: 1px solid rgba(0, 240, 255, 0.3); color: var(--neon-cyan); padding: 2px 8px; border-radius: 8px; font-size: 0.72rem; font-weight: 600;">⭐ 1x Queued</span>`;
        }

        let pitchHtml = "";
        if (item.preferred_pitch && item.preferred_pitch !== 0) {
            const sign = item.preferred_pitch > 0 ? "+" : "";
            pitchHtml = `<span class="badge-key" style="font-size: 0.7rem; padding: 2px 6px;">Key: ${sign}${item.preferred_pitch}</span>`;
        }

        let hostActionsHtml = "";
        if (isHost) {
            let saveBtnHtml = "";
            if (!item.is_downloaded) {
                saveBtnHtml = `<button class="btn-ghost host-save-lib-btn" style="padding: 6px 10px; font-size: 0.75rem; font-weight: 600; color: var(--neon-lime); border-color: rgba(0,255,100,0.4); white-space: nowrap;">⬇️ Save</button>`;
            }
            const bgmBtnHtml = `<button class="btn-ghost host-bgm-import-btn" style="padding: 6px 10px; font-size: 0.75rem; font-weight: 600; color: var(--neon-gold); border-color: rgba(255,183,3,0.4); white-space: nowrap;">🎬 BGM</button>`;
            hostActionsHtml = `${saveBtnHtml}${bgmBtnHtml}`;
        }

        card.innerHTML = `
            <div style="flex: 1; min-width: 0;">
                <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px; flex-wrap: wrap;">
                    ${badgeHtml}
                    ${pitchHtml}
                </div>
                <div style="font-weight: 700; font-size: 0.95rem; color: #fff; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(item.title)}</div>
                <div style="color: var(--text-muted); font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(item.artist)}</div>
            </div>
            <div style="display: flex; gap: 6px; align-items: center;">
                ${hostActionsHtml}
                <button class="btn-ghost history-requeue-btn" style="padding: 7px 14px; font-size: 0.82rem; font-weight: 700; color: var(--neon-cyan); border-color: rgba(0,240,255,0.4); white-space: nowrap;">
                    Sing Again
                </button>
            </div>
        `;

        card.querySelector(".history-requeue-btn").onclick = () => {
            openQueueModal(item.track_id, item.title, item.artist, item.video_id, item.preferred_pitch || 0);
        };

        const saveBtn = card.querySelector(".host-save-lib-btn");
        if (saveBtn) {
            saveBtn.onclick = (e) => {
                e.stopPropagation();
                saveBtn.disabled = true;
                saveBtn.innerText = "⏳ Saving...";
                fetch(`/api/host/tracks/${item.track_id}/download`, { method: "POST" })
                    .then(res => res.json())
                    .then(d => {
                        saveBtn.innerText = "✅ Saved";
                        showCustomToast(d.message || "Downloading track to library!");
                    })
                    .catch(() => {
                        saveBtn.innerText = "❌ Error";
                    });
            };
        }

        const bgmBtn = card.querySelector(".host-bgm-import-btn");
        if (bgmBtn) {
            bgmBtn.onclick = (e) => {
                e.stopPropagation();
                bgmBtn.disabled = true;
                bgmBtn.innerText = "🔍 Finding MV...";
                fetch(`/api/host/filler/import-from-song/${item.track_id}`, { method: "POST" })
                    .then(res => res.json())
                    .then(d => {
                        if (d.status === "ok") {
                            bgmBtn.innerText = "✅ Added to BGM";
                            showCustomToast(d.message || "Added music video to BGM!");
                        } else {
                            bgmBtn.innerText = "⚠️ In BGM";
                            showCustomToast(d.message || "Already in BGM crate");
                        }
                    })
                    .catch(() => {
                        bgmBtn.innerText = "❌ No Video";
                        showCustomToast("Could not find official music video");
                    });
            };
        }

        list.appendChild(card);
    });
}

function loadHistory() {
    loadSongbook();
    loadSingerRecordings();
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
